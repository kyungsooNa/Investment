"""당일 전략 로그 파일을 분석하여 매수 완료/실패 요약 HTML 리포트를 생성한다."""
from __future__ import annotations

from collections import Counter
import glob
import gzip
import html
import os
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple


def _esc(value: Any) -> str:
    """HTML 본문 삽입용 텍스트 이스케이프. reason/metric 등에 포함된 '<', '>', '&'가
    Telegram HTML 파서에서 unsupported tag 로 인식되는 것을 방지한다."""
    return html.escape(str(value), quote=False) if value is not None else ""

try:
    import orjson

    def _loads(line: bytes) -> dict:
        return orjson.loads(line)
except ImportError:
    import json

    def _loads(line: bytes) -> dict:
        return json.loads(line)


_STRATEGY_NAME_RE = re.compile(r'^\d{8}_(?:\d{6}_)?(.+?)(?:_\d+)?\.log\.json.*$')

# 매수 근접 추적 상수 ─────────────────────────────────────────────────

_NEAR_MISS_EVENTS = frozenset({
    "htf_pattern_detected",
    "breakout_rejected", "pp_rejected", "entry_rejected",
})

# 높을수록 매수에 가까운 단계에서 탈락 (최대 8)
_GATE_PRIORITY: Dict[Tuple[str, str], int] = {
    ("htf_pattern_detected",  ""):                           8,
    ("breakout_rejected",     "smart_money_filter_failed"):  7,
    ("breakout_rejected",     "low_execution_strength"):     6,
    ("entry_rejected",        "low_execution_strength"):     6,
    ("breakout_rejected",     "insufficient_projected_volume"): 5,
    ("pp_rejected",           "insufficient_volume"):        5,
    ("entry_rejected",        "no_bullish_reversal"):        5,
    ("breakout_rejected",     "poor_candle_quality"):        4,
    ("pp_rejected",           "no_ma_proximity"):            2,
}

# 전략 로직이 아닌 데이터 수신/파싱 이슈로 거절된 경우 (매수 실패 통계에서 분리)
_DATA_ERROR_REASON_PATTERNS = (
    "시가/현재가 0",
    "invalid price data",
    "open or current is zero",
)


def _is_data_error_reason(reason: str) -> bool:
    if not reason:
        return False
    low = reason.casefold()
    return any(p.casefold() in low for p in _DATA_ERROR_REASON_PATTERNS)


_REASON_KR: Dict[str, str] = {
    # ── 공통 ────────────────────────────────────────────────────
    "smart_money_filter_failed":     "수급 미달",
    "low_execution_strength":        "체결강도 미달",
    "insufficient_projected_volume": "거래량 미달",
    "insufficient_volume":           "거래량 미달",
    "insufficient_volume_data":      "거래량 데이터 부족",
    "poor_candle_quality":           "캔들 위치 미달",
    "low_pg_metrics":                "프로그램 수급 미달",
    "low_pg_ratio":                  "프로그램 비중 미달",
    # ── OneilPocketPivot / FirstPullback ────────────────────────
    "no_ma_proximity":               "MA 거리 초과",
    "no_bullish_reversal":           "반등 미확인",
    # ── FirstPullback ────────────────────────────────────────────
    "pullback_out_of_range":         "눌림폭 범위 초과",
    "no_surge_history":              "급등 이력 없음",
    "ma_not_uptrending":             "이동평균선 역배열/하락",
    "volume_not_dry":                "거래량 미고갈",
    # ── OneilSqueezeBreakout ─────────────────────────────────────
    "over_extended":                 "과확장(추격 포기)",
    # ── TraditionalVolumeBreakout / free-form English reasons ───
    "not_near_high":                 "신고가 근접 미달",
    "not_in_uptrend":                "이동평균선 역배열/하락",
}

# 각 섹션에서 보여줄 최대 종목 수
_MAX_REJECTED_SHOWN = 5
_MAX_NEAR_MISS_SHOWN = 3
_CONFLUENCE_MIN_STRATEGIES = 2
_REJECT_REASON_SUMMARY_THRESHOLD = 10
_FORCE_CLOSE_REASON = "reconciled_force_close"
_MAX_SOLD_DETAILS_SHOWN = 5
_MA_PROXIMITY_LOWER_PCT = -2.0
_MA_PROXIMITY_UPPER_PCT = 4.0
_MA_NEAR_MISS_MAX_EXCESS_PCT = 4.0
_MAX_EXECUTION_QUALITY_ROWS = 5


def _to_float(value: Any) -> Optional[float]:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _first_number(data: dict, *keys: str) -> Optional[float]:
    for key in keys:
        if key in data:
            value = _to_float(data.get(key))
            if value is not None:
                return value
    return None


def _strategy_name_from_source(source: Any) -> str:
    raw = str(source or "").strip()
    if not raw:
        return "미분류"
    if raw.startswith("strategy:"):
        value = raw.split(":", 1)[1].strip()
        return value or "미분류"
    if raw.startswith("manual:"):
        value = raw.split(":", 1)[1].strip()
        return value or "수동매매"
    return raw


def _normalize_reason(reason: str) -> str:
    normalized = re.sub(r'[\s_-]+', ' ', (reason or "").strip()).casefold()
    freeform_map = {
        "not near high": "not_near_high",
        "not in uptrend": "not_in_uptrend",
    }
    return freeform_map.get(normalized, reason)


def _reason_to_korean(reason: str) -> str:
    reason_key = _normalize_reason(reason)
    return _REASON_KR.get(reason_key, reason)


def _ma_proximity_excess_pct(pct: Optional[float]) -> Optional[float]:
    """MA 허용 범위에서 벗어난 정도를 percentage point 단위로 반환한다."""
    if pct is None:
        return None
    if pct < _MA_PROXIMITY_LOWER_PCT:
        return _MA_PROXIMITY_LOWER_PCT - pct
    if pct > _MA_PROXIMITY_UPPER_PCT:
        return pct - _MA_PROXIMITY_UPPER_PCT
    return 0.0


def _near_miss_sort_metric(event: str, reason: str, data: dict) -> Optional[float]:
    if reason == "no_ma_proximity":
        excess = _ma_proximity_excess_pct(data.get('closest_ma_pct'))
        if excess is None or excess > _MA_NEAR_MISS_MAX_EXCESS_PCT:
            return None
        return excess
    return 0.0


def _build_metric_str(event: str, reason: str, data: dict) -> str:
    """rejection/near-miss 데이터에서 핵심 수치 문자열을 반환한다."""
    reason_key = _normalize_reason(reason)

    if event == "htf_pattern_detected":
        return f"폭등 {data.get('surge_ratio', 0):.1f}x, 깃발 {data.get('flag_days', 0)}일"
    if reason_key == "low_execution_strength":
        cgld = data.get('cgld', 0)
        thr = data.get('threshold', 0)
        return f"강도 {cgld:.1f}%/기준 {thr:.0f}%" if thr else f"강도 {cgld:.1f}%"
    if reason_key == "poor_candle_quality":
        return f"위치 {data.get('pos', 0):.2f}"
    if reason_key == "no_ma_proximity":
        pct = data.get('closest_ma_pct')
        return f"MA 거리 {pct:+.2f}%" if pct is not None else ""
    if reason_key in ("insufficient_volume", "insufficient_projected_volume"):
        pv = data.get('proj_vol', 0)
        thr = data.get('threshold', 0)
        return f"예상거래 {int(pv):,}/기준 {int(thr):,}" if thr else ""
    if reason_key == "pullback_out_of_range":
        pct = data.get('pullback_pct')
        allowed = data.get('allowed_range', '')
        if pct is not None:
            return f"{pct:+.1f}% / 기준 {_esc(allowed)}" if allowed else f"{pct:+.1f}%"
        return ""
    if reason_key == "over_extended":
        current = data.get('current', 0)
        max_entry = data.get('max_entry', 0)
        if current and max_entry:
            over_pct = (current - max_entry) / max_entry * 100
            return f"초과 +{over_pct:.1f}%"
        return ""
    if reason_key == "not_near_high":
        distance_pct = _first_number(
            data, 'distance_pct', 'near_high_pct', 'pct_from_high', 'off_high_pct', 'high_gap_pct'
        )
        threshold = _first_number(
            data, 'threshold', 'near_high_threshold', 'near_high_threshold_pct', 'max_distance_pct'
        )
        if distance_pct is not None and threshold is not None:
            return f"{distance_pct:.1f}% > {threshold:.1f}%"
        return ""
    if reason_key == "not_in_uptrend":
        close = _first_number(data, 'close', 'current', 'stck_clpr')
        ma20 = _first_number(data, 'ma20', 'ma_20', 'ma20_value')
        if close is not None and ma20 is not None:
            return f"종가 {int(close):,} <= MA20 {int(ma20):,}"
        detail = data.get('detail')
        return _esc(detail) if isinstance(detail, str) else ""
    return ""


def _extract_strategy_name(filepath: str) -> Optional[str]:
    """파일명에서 전략 이름을 추출한다.

    예: '20260418_093000_OneilSqueezeBreakout.log.json' → 'OneilSqueezeBreakout'
    """
    m = _STRATEGY_NAME_RE.match(os.path.basename(filepath))
    return m.group(1) if m else None


def _fmt_date(date_str: str) -> str:
    if len(date_str) == 8:
        return f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
    return date_str


class StrategyLogReportService:
    """당일 전략 로그 파일을 분석하여 매수 완료/실패 요약 HTML 리포트를 생성한다.

    logs/strategies/ 디렉토리에서 최근 48시간 내 수정된 *.log.json* 파일을
    전략별로 그룹화하고, target_date에 해당하는 이벤트만 추려 리포트를 생성한다.
    """

    REJECTED_EVENTS = frozenset({
        "breakout_rejected", "pp_rejected", "htf_rejected", "fp_rejected",
        "entry_rejected",
    })

    def __init__(
        self,
        log_dir: str = "logs/strategies",
        stock_code_repo: Optional[Any] = None,
        virtual_trade_service: Optional[Any] = None,
        execution_quality_config: Optional[Any] = None,
    ):
        self._log_dir = log_dir
        self._stock_code_repo = stock_code_repo
        self._virtual_trade_service = virtual_trade_service
        self._execution_quality_config = execution_quality_config

    # ── 파일 탐색 ────────────────────────────────────────────────

    def _find_strategy_files(self) -> Dict[str, List[str]]:
        """최근 48시간 내 수정된 전략 로그 파일을 전략 이름별로 그룹화한다.

        하위 디렉토리(예: logs/strategies/oneil/)도 재귀 탐색한다.
        """
        cutoff = time.time() - 48 * 3600
        pattern = os.path.join(self._log_dir, "**", "*.log.json*")
        result: Dict[str, List[str]] = {}
        for fpath in glob.glob(pattern, recursive=True):
            if os.path.getmtime(fpath) < cutoff:
                continue
            name = _extract_strategy_name(fpath)
            if name:
                result.setdefault(name, []).append(fpath)
        return result

    # ── 로그 파싱 ────────────────────────────────────────────────

    def _iter_events(self, path: str, date_prefix: str):
        """로그 파일을 읽어 date_prefix로 시작하는 타임스탬프의 이벤트를 yield한다."""
        open_fn = gzip.open if path.endswith('.gz') else open
        try:
            with open_fn(path, 'rb') as f:
                for raw in f:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        entry = _loads(raw)
                    except Exception:
                        continue
                    ts = entry.get('timestamp', '')
                    if not ts.startswith(date_prefix):
                        continue
                    data = entry.get('data')
                    if isinstance(data, dict):
                        yield entry.get('level', 'INFO'), ts, data
        except Exception:
            pass

    def _db_resolve(self, code: str, current_name: str) -> str:
        """종목명이 코드와 같으면 StockCodeRepository에서 이름을 조회한다."""
        if current_name != code or not self._stock_code_repo:
            return current_name
        db_name = self._stock_code_repo.get_name_by_code(code)
        return db_name if db_name else code

    def _format_buy_preview(self, trades: List[dict]) -> str:
        if not trades:
            return "• 신규 매수: 없음"

        first_code = str(trades[0].get('code', '')).strip()
        current_name = str(trades[0].get('name') or first_code or "미상")
        first_name = self._db_resolve(first_code, current_name) if first_code else current_name
        count = len(trades)
        if count == 1:
            return f"• 신규 매수: 1건 ({_esc(first_name)})"
        return f"• 신규 매수: {count}건 ({_esc(first_name)} 외 {count - 1}건)"

    def _build_rejected_reason_summary(self, rejected: Dict[str, dict]) -> Optional[str]:
        if len(rejected) < _REJECT_REASON_SUMMARY_THRESHOLD:
            return None

        counts = Counter(_reason_to_korean(info['reason']) for info in rejected.values())
        if not counts:
            return None

        if len(counts) <= 2:
            parts = [f"{_esc(reason)}({count}건)" for reason, count in counts.most_common()]
        else:
            top2 = counts.most_common(2)
            other_count = sum(counts.values()) - sum(count for _, count in top2)
            parts = [f"{_esc(reason)}({count}건)" for reason, count in top2]
            if other_count > 0:
                parts.append(f"기타({other_count}건)")
        return f"• 주요 탈락 사유: {', '.join(parts)}"

    def _executed_buys_by_strategy(self, target_date: str) -> Tuple[bool, Dict[str, Dict[str, dict]]]:
        """가상매매 원장 기준 당일 실제 매수 기록을 전략별로 반환한다."""
        if not self._virtual_trade_service:
            return False, {}

        date_prefix = _fmt_date(target_date)
        result: Dict[str, Dict[str, dict]] = {}
        try:
            all_trades = self._virtual_trade_service.get_all_trades()
        except Exception:
            return False, result

        saw_target_date_trade = False
        saw_strategy_tag = False
        for trade in all_trades:
            if not str(trade.get('buy_date', '')).startswith(date_prefix):
                continue
            saw_target_date_trade = True
            if trade.get('status') not in {"HOLD", "SOLD"}:
                continue

            strategy = str(trade.get('strategy') or '').strip()
            code = str(trade.get('code') or '').strip()
            if not strategy or not code:
                continue
            saw_strategy_tag = True

            try:
                price = int(float(trade.get('buy_price') or 0))
            except (TypeError, ValueError):
                price = 0

            result.setdefault(strategy, {})[code] = {
                'name': str(trade.get('name') or code),
                'price': price,
                'reason': str(trade.get('reason') or '체결 원장 기록'),
                'time': str(trade.get('buy_date', ''))[11:16],
            }

        return (not saw_target_date_trade) or saw_strategy_tag, result

    def _build_portfolio_summary(
        self,
        target_date: str,
        fallback_buys: List[dict],
    ) -> Optional[str]:
        if not self._virtual_trade_service:
            if not fallback_buys:
                return None
            lines = ["<b>💰 오늘의 포트폴리오 요약</b>", self._format_buy_preview(fallback_buys)]
            return "\n".join(lines)

        date_prefix = _fmt_date(target_date)
        all_trades = self._virtual_trade_service.get_all_trades()
        solds = self._virtual_trade_service.get_solds()
        holds = self._virtual_trade_service.get_holds()

        today_buys = [
            trade for trade in all_trades
            if str(trade.get('buy_date', '')).startswith(date_prefix) and trade.get('status') in {"HOLD", "SOLD"}
        ]
        today_solds = [
            trade for trade in solds
            if str(trade.get('sell_date', '')).startswith(date_prefix)
        ]
        hold_codes = {str(trade.get('code', '')).strip() for trade in holds if trade.get('code')}

        lines = ["<b>💰 오늘의 포트폴리오 요약</b>", self._format_buy_preview(today_buys or fallback_buys)]

        # 정상 매도와 강제 종결(브로커 잔고 미일치)을 분리 — 통계 왜곡 방지
        normal_solds, force_closed = [], []
        for trade in today_solds:
            reason = str(trade.get('reason') or '').strip()
            if reason == _FORCE_CLOSE_REASON:
                force_closed.append(trade)
            else:
                normal_solds.append(trade)

        if normal_solds:
            avg_return = sum(float(t.get('return_rate') or 0.0) for t in normal_solds) / len(normal_solds)
            lines.append(f"• 당일 청산: {len(normal_solds)}건 (평균 수익률 {avg_return:+.2f}%)")
            for t in normal_solds[:_MAX_SOLD_DETAILS_SHOWN]:
                code = str(t.get('code', '')).strip()
                name = self._db_resolve(code, str(t.get('name') or code))
                rr = float(t.get('return_rate') or 0.0)
                try:
                    sp = int(float(t.get('sell_price') or 0))
                    sp_str = f" @ ₩{sp:,}" if sp else ""
                except (TypeError, ValueError):
                    sp_str = ""
                strategy = t.get('strategy') or ''
                strategy_str = f" [{_esc(strategy)}]" if strategy else ""
                lines.append(f"  - {_esc(name)}({code}){sp_str} {rr:+.2f}%{strategy_str}")
            rest = len(normal_solds) - _MAX_SOLD_DETAILS_SHOWN
            if rest > 0:
                lines.append(f"  …외 {rest}건")
        elif not force_closed:
            lines.append("• 당일 청산: 없음")

        if force_closed:
            names = []
            for t in force_closed[:3]:
                code = str(t.get('code', '')).strip()
                names.append(self._db_resolve(code, str(t.get('name') or code)))
            extra = f" 외 {len(force_closed) - 3}건" if len(force_closed) > 3 else ""
            lines.append(
                f"• ⚠️ 강제 종결: {len(force_closed)}건 — 브로커 잔고 미일치 ({_esc(', '.join(names))}{extra}). "
                "외부 매도 또는 정합성 점검 필요."
            )

        if hold_codes:
            lines.append(f"• 현재 보유: {len(hold_codes)}종목")
        else:
            lines.append("• 현재 보유: 없음")

        return "\n".join(lines)

    def _build_execution_quality_section(self, records: List[dict]) -> Optional[str]:
        if not records:
            return None

        records = self._latest_execution_quality_records(records)
        by_strategy: Dict[str, List[dict]] = {}
        by_symbol: Dict[Tuple[str, str], List[dict]] = {}
        for record in records:
            strategy = record["strategy"]
            by_strategy.setdefault(strategy, []).append(record)
            by_symbol.setdefault((record["code"], record["name"]), []).append(record)

        lines = ["<b>📈 체결 품질 요약</b>"]
        strategy_rows = []
        for strategy, items in by_strategy.items():
            slip_values = [
                abs(item["slippage_pct"])
                for item in items
                if item.get("slippage_pct") is not None
            ]
            latency_values = [
                item["first_fill_latency_sec"]
                for item in items
                if item.get("first_fill_latency_sec") is not None
            ]
            avg_slip = sum(slip_values) / len(slip_values) if slip_values else None
            max_slip = max(slip_values) if slip_values else None
            avg_latency = sum(latency_values) / len(latency_values) if latency_values else None
            incomplete_count = sum(
                1
                for item in items
                if item.get("order_qty", 0) > 0 and item.get("remaining_qty", 0) > 0
            )
            unfilled_values = [
                item["unfilled_ratio_pct"]
                for item in items
                if item.get("unfilled_ratio_pct") is not None
            ]
            age_values = [
                item["order_age_sec"]
                for item in items
                if item.get("order_age_sec") is not None
            ]
            strategy_rows.append({
                "strategy": strategy,
                "count": len(items),
                "avg_slip": avg_slip,
                "p95_slip": self._percentile(slip_values, 95),
                "max_slip": max_slip,
                "avg_latency": avg_latency,
                "incomplete_fill_ratio": incomplete_count / len(items) * 100 if items else 0.0,
                "avg_unfilled_ratio": sum(unfilled_values) / len(unfilled_values) if unfilled_values else None,
                "avg_order_age": sum(age_values) / len(age_values) if age_values else None,
            })

        strategy_rows.sort(key=lambda row: (
            row["avg_slip"] is None,
            -(row["avg_slip"] or 0),
            -(row["avg_unfilled_ratio"] or 0),
            row["strategy"],
        ))
        for row in strategy_rows[:_MAX_EXECUTION_QUALITY_ROWS]:
            slip_str = f"{row['avg_slip']:.3f}%" if row["avg_slip"] is not None else "N/A"
            p95_str = f"{row['p95_slip']:.3f}%" if row["p95_slip"] is not None else "N/A"
            max_str = f"{row['max_slip']:.3f}%" if row["max_slip"] is not None else "N/A"
            latency_str = f"{row['avg_latency']:.1f}s" if row["avg_latency"] is not None else "N/A"
            incomplete_str = f"{row['incomplete_fill_ratio']:.1f}%"
            unfilled_str = f"{row['avg_unfilled_ratio']:.1f}%" if row["avg_unfilled_ratio"] is not None else "N/A"
            age_str = f"{row['avg_order_age']:.1f}s" if row["avg_order_age"] is not None else "N/A"
            quality_label = self._execution_quality_label(row)
            quality_str = f" {quality_label}" if quality_label else ""
            lines.append(
                f"• {_esc(row['strategy'])}: {row['count']}건, 평균 슬리피지 {slip_str}, "
                f"P95 {p95_str}, 최대 {max_str}, 평균 지연 {latency_str}, "
                f"불완전 체결 {incomplete_str}, 평균 잔량 {unfilled_str}, 평균 지속 {age_str}{quality_str}"
            )

        symbol_rows = []
        for (code, name), items in by_symbol.items():
            slip_values = [
                abs(item["slippage_pct"])
                for item in items
                if item.get("slippage_pct") is not None
            ]
            if not slip_values:
                continue
            symbol_rows.append({
                "code": code,
                "name": name,
                "count": len(items),
                "avg_slip": sum(slip_values) / len(slip_values),
            })
        symbol_rows.sort(key=lambda row: (-row["avg_slip"], row["name"], row["code"]))
        if symbol_rows:
            parts = [
                f"{_esc(row['name'])}({row['code']}) {row['avg_slip']:.3f}%/{row['count']}건"
                for row in symbol_rows[:3]
            ]
            lines.append("• 종목별 슬리피지 상위: " + ", ".join(parts))

        return "\n".join(lines)

    @staticmethod
    def _latest_execution_quality_records(records: List[dict]) -> List[dict]:
        latest: Dict[str, dict] = {}
        for idx, record in enumerate(records):
            order_key = str(record.get("order_key") or f"missing:{idx}")
            prev = latest.get(order_key)
            if prev is None or str(record.get("timestamp", "")) >= str(prev.get("timestamp", "")):
                latest[order_key] = record
        return list(latest.values())

    @staticmethod
    def _percentile(values: List[float], percentile: int) -> Optional[float]:
        if not values:
            return None
        ordered = sorted(values)
        if len(ordered) == 1:
            return ordered[0]
        rank = (len(ordered) - 1) * percentile / 100
        lower = int(rank)
        upper = min(lower + 1, len(ordered) - 1)
        weight = rank - lower
        return ordered[lower] * (1 - weight) + ordered[upper] * weight

    def _execution_quality_label(self, row: dict) -> str:
        cfg = self._execution_quality_config
        if cfg is None or not bool(getattr(cfg, "enabled", True)):
            return ""
        if row.get("count", 0) < int(getattr(cfg, "min_sample_count", 3) or 0):
            return ""

        avg_slip = row.get("avg_slip")
        p95_slip = row.get("p95_slip")
        avg_latency = row.get("avg_latency")
        incomplete_fill_ratio = row.get("incomplete_fill_ratio")
        avg_unfilled_ratio = row.get("avg_unfilled_ratio")
        avg_order_age = row.get("avg_order_age")

        candidate_reasons = self._quality_threshold_reasons(
            avg_slip=avg_slip,
            p95_slip=p95_slip,
            avg_latency=avg_latency,
            incomplete_fill_ratio=incomplete_fill_ratio,
            avg_unfilled_ratio=avg_unfilled_ratio,
            avg_order_age=avg_order_age,
            avg_slip_threshold=getattr(cfg, "candidate_avg_slippage_pct", None),
            p95_slip_threshold=getattr(cfg, "candidate_p95_slippage_pct", None),
            avg_latency_threshold=getattr(cfg, "candidate_avg_first_fill_latency_sec", None),
            incomplete_fill_ratio_threshold=getattr(cfg, "candidate_incomplete_fill_ratio_pct", None),
            avg_unfilled_ratio_threshold=getattr(cfg, "candidate_avg_unfilled_ratio_pct", None),
            avg_order_age_threshold=getattr(cfg, "candidate_avg_order_age_sec", None),
        )
        if candidate_reasons:
            suffix = "자동 OFF" if bool(getattr(cfg, "auto_disable_enabled", False)) else "후보"
            return f"⚠️ 비활성화 {suffix}: {', '.join(candidate_reasons)}"

        warn_reasons = self._quality_threshold_reasons(
            avg_slip=avg_slip,
            p95_slip=p95_slip,
            avg_latency=avg_latency,
            incomplete_fill_ratio=incomplete_fill_ratio,
            avg_unfilled_ratio=avg_unfilled_ratio,
            avg_order_age=avg_order_age,
            avg_slip_threshold=getattr(cfg, "warn_avg_slippage_pct", None),
            p95_slip_threshold=getattr(cfg, "warn_p95_slippage_pct", None),
            avg_latency_threshold=getattr(cfg, "warn_avg_first_fill_latency_sec", None),
            incomplete_fill_ratio_threshold=getattr(cfg, "warn_incomplete_fill_ratio_pct", None),
            avg_unfilled_ratio_threshold=getattr(cfg, "warn_avg_unfilled_ratio_pct", None),
            avg_order_age_threshold=getattr(cfg, "warn_avg_order_age_sec", None),
        )
        if warn_reasons:
            return f"⚠️ 경고: {', '.join(warn_reasons)}"
        return ""

    @staticmethod
    def _quality_threshold_reasons(
        *,
        avg_slip: Optional[float],
        p95_slip: Optional[float],
        avg_latency: Optional[float],
        incomplete_fill_ratio: Optional[float],
        avg_unfilled_ratio: Optional[float],
        avg_order_age: Optional[float],
        avg_slip_threshold: Optional[float],
        p95_slip_threshold: Optional[float],
        avg_latency_threshold: Optional[float],
        incomplete_fill_ratio_threshold: Optional[float],
        avg_unfilled_ratio_threshold: Optional[float],
        avg_order_age_threshold: Optional[float],
    ) -> List[str]:
        reasons = []
        if avg_slip is not None and avg_slip_threshold is not None and avg_slip > avg_slip_threshold:
            reasons.append(f"평균 슬리피지 {avg_slip:.3f}%")
        if p95_slip is not None and p95_slip_threshold is not None and p95_slip > p95_slip_threshold:
            reasons.append(f"P95 슬리피지 {p95_slip:.3f}%")
        if avg_latency is not None and avg_latency_threshold is not None and avg_latency > avg_latency_threshold:
            reasons.append(f"평균 지연 {avg_latency:.1f}s")
        if (
            incomplete_fill_ratio is not None
            and incomplete_fill_ratio_threshold is not None
            and incomplete_fill_ratio > incomplete_fill_ratio_threshold
        ):
            reasons.append(f"불완전 체결 {incomplete_fill_ratio:.1f}%")
        if (
            avg_unfilled_ratio is not None
            and avg_unfilled_ratio_threshold is not None
            and avg_unfilled_ratio > avg_unfilled_ratio_threshold
        ):
            reasons.append(f"평균 잔량 {avg_unfilled_ratio:.1f}%")
        if avg_order_age is not None and avg_order_age_threshold is not None and avg_order_age > avg_order_age_threshold:
            reasons.append(f"평균 지속 {avg_order_age:.1f}s")
        return reasons

    # ── 리포트 생성 ──────────────────────────────────────────────

    async def generate_report(self, target_date: str) -> str:
        """
        target_date: "YYYYMMDD" 형식
        전략별 매수 완료/실패를 분석한 HTML 리포트 문자열을 반환한다.
        """
        date_prefix = f"{target_date[:4]}-{target_date[4:6]}-{target_date[6:8]}"
        strategy_files = self._find_strategy_files()

        if not strategy_files:
            return (
                f"<b>📊 [{_fmt_date(target_date)}] 전략 실행 요약</b>\n\n"
                "당일 전략 로그가 없습니다."
            )

        strategy_summaries: List[dict] = []
        inactive_names: List[str] = []
        market_timing: Dict[str, Tuple[str, bool]] = {}
        has_executed_buy_source, executed_buys_by_strategy = self._executed_buys_by_strategy(target_date)

        # 전략 로직과 무관한 데이터 수신/파싱 오류를 따로 집계
        data_errors_by_strategy: Dict[str, int] = {}
        execution_quality_records: List[dict] = []

        for name, files in sorted(strategy_files.items()):
            bought: Dict[str, dict] = {}
            rejected: Dict[str, dict] = {}
            scan_count: int = 0
            near_miss: Dict[str, dict] = {}
            name_map: Dict[str, str] = {}
            early_guard_skipped: set[str] = set()
            data_error_count: int = 0

            for fpath in sorted(files):
                for _level, ts, data in self._iter_events(fpath, date_prefix):
                    event = data.get('event', '')
                    code = data.get('code', '')

                    if code and data.get('name'):
                        name_map[code] = data['name']

                    if event == 'scan_with_watchlist':
                        scan_count = max(scan_count, data.get('count', 0))

                    elif event == 'market_timing_updated':
                        mkt = data.get('market', '')
                        if mkt and ts >= market_timing.get(mkt, ('', False))[0]:
                            market_timing[mkt] = (ts, data.get('ok', False))

                    elif event == 'buy_signal_generated' and code:
                        metrics = data.get('metrics', {})
                        price = metrics.get('price', data.get('price', 0))
                        bought[code] = {
                            'name': name_map.get(code, data.get('name', code)),
                            'price': price,
                            'reason': data.get('reason', ''),
                            'time': ts[11:16] if len(ts) >= 16 else '',
                        }
                        rejected.pop(code, None)
                        near_miss.pop(code, None)

                    elif event == 'execution_quality' and code:
                        name_value = name_map.get(code) or data.get('name') or code
                        execution_quality_records.append({
                            "timestamp": ts,
                            "order_key": data.get("order_key") or f"{ts}:{code}:{len(execution_quality_records)}",
                            "strategy": _strategy_name_from_source(data.get("source") or data.get("strategy_name")),
                            "code": str(code).strip(),
                            "name": self._db_resolve(str(code).strip(), str(name_value)),
                            "side": data.get("side", ""),
                            "state": data.get("state", ""),
                            "order_qty": int(_to_float(data.get("order_qty")) or 0),
                            "filled_qty": int(_to_float(data.get("filled_qty")) or 0),
                            "remaining_qty": int(_to_float(data.get("remaining_qty")) or 0),
                            "fill_ratio_pct": _to_float(data.get("fill_ratio_pct")),
                            "unfilled_ratio_pct": _to_float(data.get("unfilled_ratio_pct")),
                            "order_age_sec": _to_float(data.get("order_age_sec")),
                            "slippage_amount_won": _to_float(data.get("slippage_amount_won")),
                            "slippage_pct": _to_float(data.get("slippage_pct")),
                            "first_fill_latency_sec": _to_float(data.get("first_fill_latency_sec")),
                        })

                    elif code and (event in self.REJECTED_EVENTS or event.endswith('_rejected')):
                        if code not in bought:
                            raw_reason = data.get('reason', '')
                            if _is_data_error_reason(raw_reason):
                                # 데이터 오류는 전략 통계에서 제외하고 시스템 경고로 집계
                                data_error_count += 1
                            else:
                                prev = rejected.get(code, {'name': '', 'reason': '', 'event': '', 'data': {}, 'count': 0})
                                cand_name = name_map.get(code) or data.get('name', '') or prev['name'] or code
                                rejected[code] = {
                                    'name': cand_name,
                                    'reason': _normalize_reason(data.get('reason', prev['reason'])),
                                    'event': event,
                                    'data': data,
                                    'count': prev['count'] + 1,
                                }

                    if event == 'breakout_skipped' and data.get('reason') == 'early_morning_guard' and code:
                        early_guard_skipped.add(code)
                        if code in near_miss:
                            near_miss[code]['note'] = "장 초반 진입 제한으로 스킵"

                    if event in _NEAR_MISS_EVENTS and code and code not in bought:
                        reason = data.get('reason', '')
                        gate = _GATE_PRIORITY.get((event, reason), 0)
                        sort_metric = _near_miss_sort_metric(event, reason, data)
                        prev = near_miss.get(code)
                        should_replace = (
                            gate > 0 and sort_metric is not None and (
                                not prev
                                or gate > prev.get('gate', -1)
                                or (gate == prev.get('gate') and sort_metric < prev.get('sort_metric', float('inf')))
                            )
                        )
                        if should_replace:
                            near_miss[code] = {
                                'name': name_map.get(code, data.get('name', code)),
                                'gate': gate,
                                'sort_metric': sort_metric,
                                'reason_kr': _reason_to_korean("HTF 패턴 감지" if event == "htf_pattern_detected" else reason),
                                'metric_str': _build_metric_str(event, reason, data),
                                'time': ts[11:16] if len(ts) >= 16 else '',
                                'note': "장 초반 진입 제한으로 스킵" if code in early_guard_skipped else "",
                            }

            # StockCodeRepository로 미해결 종목명 보완
            if has_executed_buy_source:
                bought = executed_buys_by_strategy.get(name, {})

            if self._stock_code_repo:
                for code, info in bought.items():
                    info['name'] = self._db_resolve(code, info['name'])
                for code, info in rejected.items():
                    info['name'] = self._db_resolve(code, info['name'])
                for code, info in near_miss.items():
                    info['name'] = self._db_resolve(code, info['name'])

            if data_error_count > 0:
                data_errors_by_strategy[name] = data_error_count

            if not bought and not rejected and not near_miss:
                if scan_count:
                    strategy_summaries.append({
                        'name': name,
                        'scan_count': scan_count,
                        'bought': bought,
                        'rejected': rejected,
                        'near_miss': near_miss,
                        'scan_only': True,
                    })
                    continue
                inactive_names.append(name)
                continue

            strategy_summaries.append({
                'name': name,
                'scan_count': scan_count,
                'bought': bought,
                'rejected': rejected,
                'near_miss': near_miss,
                'scan_only': False,
            })

        confluence_map: Dict[str, dict] = {}
        fallback_buys: List[dict] = []
        for summary in strategy_summaries:
            for code, info in summary['bought'].items():
                fallback_buys.append({'code': code, 'name': info['name']})
                entry = confluence_map.setdefault(code, {'name': info['name'], 'count': 0, 'strategies': []})
                entry['name'] = info['name']
                entry['count'] += 1
                entry['strategies'].append(summary['name'])

        active_sections: List[str] = []
        for idx, summary in enumerate(strategy_summaries, start=1):
            if summary['scan_only']:
                active_sections.append(f"<b>{idx}. {_esc(summary['name'])}</b> — {summary['scan_count']}종목 스캔 (시그널 없음)")
                continue

            scan_str = f" — {summary['scan_count']}종목 스캔" if summary['scan_count'] else ""
            lines = [f"<b>{idx}. {_esc(summary['name'])}</b>{scan_str}"]

            if summary['bought']:
                lines.append(f"\n✅ 매수 완료 ({len(summary['bought'])}건)")
                for code, info in summary['bought'].items():
                    price_str = f" @ ₩{info['price']:,}" if info['price'] else ""
                    time_str = f" ({info['time']})" if info.get('time') else ""
                    confluence = confluence_map.get(code, {})
                    confluence_str = ""
                    if confluence.get('count', 0) >= _CONFLUENCE_MIN_STRATEGIES:
                        confluence_str = f" [🔥 다중 전략 포착: {_esc(', '.join(confluence['strategies']))}]"
                    lines.append(f"• {_esc(info['name'])}({code}){confluence_str}: {_esc(info['reason'])}{price_str}{time_str}")
            else:
                lines.append("\n✅ 매수 완료: 없음")

            if summary['rejected']:
                lines.append(f"\n❌ 매수 실패 ({len(summary['rejected'])}건)")
                reason_summary = self._build_rejected_reason_summary(summary['rejected'])
                if reason_summary:
                    lines.append(reason_summary)
                sorted_rejected = sorted(summary['rejected'].items(), key=lambda x: -x[1].get('count', 0))
                shown = sorted_rejected[:_MAX_REJECTED_SHOWN]
                rest_count = len(sorted_rejected) - _MAX_REJECTED_SHOWN
                for code, info in shown:
                    reason_kr = _reason_to_korean(info['reason'])
                    metric = _build_metric_str(info.get('event', ''), info['reason'], info.get('data', {}))
                    metric_str = f" ({metric})" if metric else ""
                    count = info.get('count', 1)
                    count_str = f" {count}회 탈락" if count > 1 else ""
                    lines.append(f"• {_esc(info['name'])}({code}): {_esc(reason_kr)}{metric_str}{count_str}")
                if rest_count > 0:
                    lines.append(f"  …외 {rest_count}건")
            else:
                lines.append("\n❌ 매수 실패: 없음")

            top3 = sorted(
                summary['near_miss'].values(),
                key=lambda x: (-x['gate'], x.get('sort_metric', float('inf')))
            )[:_MAX_NEAR_MISS_SHOWN]
            if top3:
                lines.append("\n🎯 매수 근접")
                for c in top3:
                    metric = f" ({c['metric_str']})" if c['metric_str'] else ""
                    time_str = f" ({c['time']})" if c.get('time') else ""
                    note_str = f" - {_esc(c['note'])}" if c.get('note') else ""
                    lines.append(f"• {_esc(c['name'])}: {_esc(c['reason_kr'])}{metric}{time_str}{note_str}")

            active_sections.append("\n".join(lines))

        now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        if market_timing:
            mt_parts = [
                f"{mkt} {'🟢' if market_timing[mkt][1] else '🔴'}"
                for mkt in ["KOSPI", "KOSDAQ"] if mkt in market_timing
            ]
            header = (
                f"<b>📊 [{_fmt_date(target_date)}] 전략 실행 요약</b>\n"
                f"<i>시장: {' | '.join(mt_parts)} (MA20 추세)</i>"
            )
        else:
            header = f"<b>📊 [{_fmt_date(target_date)}] 전략 실행 요약</b>"

        footer = f"\n\n<i>생성: {now_str}</i>"

        system_warnings: List[str] = []
        if data_errors_by_strategy:
            details = ", ".join(
                f"{_esc(strategy)} {count}건"
                for strategy, count in sorted(data_errors_by_strategy.items(), key=lambda x: -x[1])
            )
            total = sum(data_errors_by_strategy.values())
            system_warnings.append(
                f"• 시가/현재가 0 수신 오류 — 총 {total}건 ({details})"
            )

        warnings_section = ""
        if system_warnings:
            warnings_section = "<b>⚠️ 시스템 경고</b>\n" + "\n".join(system_warnings)
        execution_quality_section = self._build_execution_quality_section(execution_quality_records)

        if not active_sections:
            extra_sections = [
                section for section in (warnings_section, execution_quality_section) if section
            ]
            extra_body = "\n\n".join(extra_sections)
            extra = f"\n\n{extra_body}" if extra_body else ""
            return header + extra + "\n\n당일 활동한 전략이 없습니다." + footer

        sections: List[str] = []
        if warnings_section:
            sections.append(warnings_section)
        sections.extend(active_sections)
        body = "\n\n".join(sections)
        portfolio_summary = self._build_portfolio_summary(target_date, fallback_buys)
        if portfolio_summary:
            body += f"\n\n{portfolio_summary}"
        if execution_quality_section:
            body += f"\n\n{execution_quality_section}"

        if inactive_names:
            inactive_summary = f"\n\n💤 <i>활동 없음: {_esc(', '.join(inactive_names[:3]))}"
            if len(inactive_names) > 3:
                inactive_summary += f" 외 {len(inactive_names) - 3}개"
            inactive_summary += "</i>"
            body += inactive_summary

        return header + "\n\n" + body + footer
