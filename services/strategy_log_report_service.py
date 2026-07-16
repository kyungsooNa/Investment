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
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from common.strategy_identity import STRATEGY_IDENTITY_RESOLVER, StrategyStatus
from common.trade_journal_comparison import compare_trade_journals
from services.multiple_testing_bias_service import compute_multiple_testing_bias_summary
from services.strategy_correlation_service import compute_strategy_correlation_summary
from services.overnight_exposure_service import compute_overnight_exposure_summary
from services.regime_performance_service import (
    BUCKET_KEYS,
    compute_strategy_regime_decomposition,
)
from services.strategy_performance_degradation_service import (
    StrategyPerformanceDegradationConfig,
    analyze_strategy_performance_degradation,
    compute_strategy_window_metrics,
)
from services.strategy_profitability_gate_service import (
    StrategyProfitabilityGateConfig,
    evaluate_strategy_profitability_gate,
)


def _esc(value: Any) -> str:
    """HTML 본문 삽입용 텍스트 이스케이프. reason/metric 등에 포함된 '<', '>', '&'가
    Telegram HTML 파서에서 unsupported tag 로 인식되는 것을 방지한다."""
    return html.escape(str(value), quote=False) if value is not None else ""


_REGIME_BUCKET_LABELS = {
    "KOSPI_BULL": "KOSPI 상승",
    "KOSDAQ_BULL": "KOSDAQ 상승",
    "SIDEWAYS": "횡보",
    "BEAR": "하락",
    "TRADING_VALUE_SURGE": "거래대금급증",
}


# P1 1-6: profitability gate 결과(status/blocking_reasons)를 일일 리포트 한글로 노출.
_PROFITABILITY_GATE_STATUS_LABELS = {
    "pass": "통과",
    "fail": "차단",
    "insufficient_sample": "표본 부족",
}

_PROFITABILITY_GATE_REASON_LABELS = {
    "insufficient_trades": "거래 수 부족",
    "profit_factor_below": "수익팩터 미달",
    "payoff_ratio_below": "손익비 미달",
    "win_rate_below": "승률 미달",
    "avg_net_return_below": "평균 순수익률 미달",
    "total_net_pnl_not_positive": "순손익 음수",
    "mdd_pct_above": "MDD 초과",
    "monte_carlo_ruin_probability_above": "몬테카를로 파산확률 초과",
    "monte_carlo_worst_mdd_pct_above": "몬테카를로 최악 MDD 초과",
    "monte_carlo_unavailable": "몬테카를로 미산출",
    "regime_balance_incomplete": "레짐 균형 미충족",
    "parameter_stability_unavailable": "파라미터 안정성 미산출",
    "multiple_testing_bias_warning": "다중검정 편향 경고",
}


def _profitability_gate_reason_label(reason: Any) -> str:
    """blocking_reason 코드를 한글 라벨로 변환한다. 동적 사유(regime_*_negative_pnl 등)는
    매핑이 없으면 원본 코드를 그대로 노출한다."""
    return _PROFITABILITY_GATE_REASON_LABELS.get(str(reason), str(reason))


def _numeric_values(values: List[Any]) -> List[float]:
    result: List[float] = []
    for value in values:
        try:
            if value in (None, ""):
                continue
            result.append(float(value))
        except (TypeError, ValueError):
            continue
    return result


def _avg_numeric(values: List[Any]) -> Optional[float]:
    numeric = _numeric_values(values)
    if not numeric:
        return None
    return round(sum(numeric) / len(numeric), 4)


def _sum_numeric(values: List[Any]) -> Optional[float]:
    numeric = _numeric_values(values)
    if not numeric:
        return None
    return round(sum(numeric), 4)

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
_HTF_EARLY_GUARD_NOTE = "장 초반 진입 제한, 이후 스캔 계속"

_STRATEGY_ALIAS_TO_CANONICAL = {
    "오닐스퀴즈돌파": "OneilSqueezeBreakout",
    "오닐PP/BGU": "OneilPocketPivot",
    "하이타이트플래그": "HighTightFlag",
    "첫눌림목": "FirstPullback",
    "래리윌리엄스VBO": "LarryWilliamsVBO",
    "RSI2눌림목": "RSI2Pullback",
    "래리윌리엄스CB": "LarryWilliamsCB",
    "거래량돌파": "VolumeBreakoutLive",
    "프로그램매수추종": "ProgramBuyFollow",
    "거래량돌파(전통)": "TraditionalVolumeBreakout",
}

_STRATEGY_CANONICAL_TO_ID = {
    "OneilSqueezeBreakout": "oneil_squeeze_breakout",
    "OneilPocketPivot": "oneil_pocket_pivot",
    "HighTightFlag": "high_tight_flag",
    "FirstPullback": "first_pullback",
    "LarryWilliamsVBO": "larry_williams_vbo",
    "RSI2Pullback": "rsi2_pullback",
    "LarryWilliamsCB": "larry_williams_cb",
    "ProgramBuyFollow": "program_buy_follow",
    "TraditionalVolumeBreakout": "traditional_volume_breakout",
    "VolumeBreakoutLive": "volume_breakout_live",
}


def _strategy_alias_key(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "").strip()).casefold()


_STRATEGY_CANONICAL_BY_KEY: Dict[str, str] = {}
for _alias, _canonical in _STRATEGY_ALIAS_TO_CANONICAL.items():
    _STRATEGY_CANONICAL_BY_KEY[_strategy_alias_key(_alias)] = _canonical
    _STRATEGY_CANONICAL_BY_KEY[_strategy_alias_key(_canonical)] = _canonical

_NON_STRATEGY_JOURNAL_KEYS = {
    _strategy_alias_key("BUY실패"),
    _strategy_alias_key("SELL실패"),
    _strategy_alias_key("수동매매"),
}


def _strategy_report_key(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    return (
        _STRATEGY_CANONICAL_BY_KEY.get(_strategy_alias_key(raw))
        or _STRATEGY_CANONICAL_BY_KEY.get(
            _strategy_alias_key(STRATEGY_IDENTITY_RESOLVER.to_display(raw))
        )
        or raw
    )


def _strategy_metric_key(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    alias_key = _strategy_alias_key(raw)
    if alias_key in _NON_STRATEGY_JOURNAL_KEYS:
        return ""

    canonical = _STRATEGY_CANONICAL_BY_KEY.get(alias_key)
    strategy_id = _STRATEGY_CANONICAL_TO_ID.get(canonical or "")
    if not strategy_id:
        strategy_id = STRATEGY_IDENTITY_RESOLVER.to_id(raw)
    if STRATEGY_IDENTITY_RESOLVER.is_known_id(strategy_id):
        if STRATEGY_IDENTITY_RESOLVER.get_status(strategy_id) != StrategyStatus.ACTIVE:
            return ""
        return strategy_id
    return raw


def _normalize_strategy_metric_records(records: List[Mapping[str, Any]]) -> List[dict]:
    normalized: List[dict] = []
    for record in records:
        strategy = _strategy_metric_key(record.get("strategy"))
        if not strategy:
            continue
        item = dict(record)
        item["strategy"] = strategy
        normalized.append(item)
    return normalized


def _journal_source(record: Mapping[str, Any]) -> str:
    source = str(record.get("source") or "").strip()
    if source:
        return source
    metadata = record.get("metadata")
    if isinstance(metadata, Mapping):
        signal_source = str(metadata.get("signal_source") or "").strip()
        if signal_source:
            return signal_source
    return "unknown"


def _strategy_display_label(value: Any) -> str:
    return STRATEGY_IDENTITY_RESOLVER.to_display(str(value or ""))


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
    # ── 유동성 / 시장 타이밍 / RiskGate / 포트폴리오 ────────────
    "insufficient_trading_value":    "거래대금 부족",
    "rs_rating_low":                 "RS Rating 부족",
    "market_timing_off":             "시장 타이밍 OFF",
    "risk_gate_blocked":             "RiskGate 차단",
    "insufficient_cash":             "현금 부족",
    "duplicate_entry_blocked":       "동일 종목 재진입 차단",
    "stage_blocked":                 "StageGuard 탈락",
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
    # ── 사람이 보는 리포트용 raw reason 정리 ──────────────────────
    "pattern_not_detected":          "패턴 미감지",
    "out_of_entry_band":             "진입 밴드 이탈",
    "below_breakout_buffer":         "돌파 버퍼 미달",
    "below_target":                  "목표가 미달",
    "program_buy_unavailable":       "프로그램 매수 데이터 없음",
    "range_unavailable":             "가격 범위 데이터 없음",
    "rs_rating_below_min":           "RS Rating 기준 미달",
    "adx_below_threshold":           "ADX 기준 미달",
    "not_stage2":                    "Stage2 아님",
    "rsi_above_threshold":           "RSI 기준 초과",
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


def _to_int(value: Any, default: int = 0) -> int:
    numeric = _to_float(value)
    if numeric is None:
        return default
    return int(numeric)


def _format_krw(value: Any, *, force_integer: bool = False) -> str:
    amount = _to_float(value)
    if amount is None or amount <= 0:
        return ""
    if force_integer or amount.is_integer():
        return f"₩{int(round(amount)):,}"
    return f"₩{amount:,.2f}"


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
        backtest_journal_provider: Optional[Callable[[str], List[dict]]] = None,
        enabled_strategy_provider: Optional[Callable[[], Optional[List[str]]]] = None,
        strategy_degradation_config: Optional[Any] = None,
        profitability_gate_config: Optional[Any] = None,
    ):
        self._log_dir = log_dir
        self._stock_code_repo = stock_code_repo
        self._virtual_trade_service = virtual_trade_service
        self._execution_quality_config = execution_quality_config
        self._backtest_journal_provider = backtest_journal_provider
        self._enabled_strategy_provider = enabled_strategy_provider
        self._strategy_degradation_config = strategy_degradation_config
        self._profitability_gate_config = profitability_gate_config
        self._last_execution_quality_candidates: List[dict] = []
        self._last_strategy_degradation_candidates: List[dict] = []
        self._last_operational_decision_report: str = ""

    def get_last_execution_quality_candidates(self) -> List[dict]:
        """최근 generate_report 실행에서 산출된 체결 품질 비활성화 후보 목록."""
        return list(self._last_execution_quality_candidates)

    def get_last_strategy_degradation_candidates(self) -> List[dict]:
        """최근 generate_report 실행에서 산출된 전략 성과 저하 후보 목록."""
        return list(self._last_strategy_degradation_candidates)

    def get_last_operational_decision_report(self) -> str:
        """최근 generate_report 실행에서 산출된 운영 의사결정 요약."""
        return self._last_operational_decision_report

    def save_diagnostic_report(
        self,
        report_date: str,
        report_html: str,
        report_dir: Optional[str] = None,
    ) -> str:
        """기존 상세 전략 리포트를 운영품질진단 산출물로 파일에 축적한다."""
        if report_dir is None:
            base_dir = os.path.dirname(os.path.normpath(self._log_dir)) or "."
            report_dir = os.path.join(base_dir, "reports", "strategy_diagnostics")
        from repositories.strategy_diagnostic_report_repository import (
            StrategyDiagnosticReportRepository,
        )

        repository = StrategyDiagnosticReportRepository(report_dir)
        saved = repository.save(report_date, report_html)
        return os.path.join(report_dir, saved["id"])

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
            parts = [f"{_esc(reason)}({count}종목)" for reason, count in counts.most_common()]
        else:
            top2 = counts.most_common(2)
            other_count = sum(counts.values()) - sum(count for _, count in top2)
            parts = [f"{_esc(reason)}({count}종목)" for reason, count in top2]
            if other_count > 0:
                parts.append(f"기타({other_count}종목)")
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

        for trade in all_trades:
            if not str(trade.get('buy_date', '')).startswith(date_prefix):
                continue
            if trade.get('status') not in {"HOLD", "SOLD"}:
                continue

            strategy = str(trade.get('strategy') or '').strip()
            code = str(trade.get('code') or '').strip()
            if not strategy or not code:
                continue

            price = _to_float(trade.get('buy_price')) or 0.0
            qty = max(_to_int(trade.get('qty'), default=1), 0)
            total_amount = round(price * qty) if price > 0 and qty > 0 else 0

            result.setdefault(_strategy_report_key(strategy), {})[code] = {
                'name': str(trade.get('name') or code),
                'price': price,
                'qty': qty,
                'total_amount': total_amount,
                'reason': str(trade.get('reason') or '체결 원장 기록'),
                'time': str(trade.get('buy_date', ''))[11:16],
                'volatility_20d_annualized': _to_float(trade.get('volatility_20d_annualized')),
                # P1 1-6: 신호 metadata (진입사유/confidence/기대보유) — journal → 리포트 분석 연결.
                'entry_reason': (str(trade.get('entry_reason')) if trade.get('entry_reason') else None),
                'confidence': _to_float(trade.get('confidence')),
                'expected_holding_period_days': _to_float(trade.get('expected_holding_period_days')),
            }

        # 원장을 읽을 수 있으면 buy_signal_generated 로그보다 원장을 신뢰한다.
        # 전략 태그가 없는 당일 거래가 있어도 테스트/드라이런 로그를 실매수로 오인하지 않는다.
        return True, result

    @staticmethod
    def _format_buy_execution_detail(info: Mapping[str, Any]) -> str:
        avg_price = _format_krw(info.get('price'))
        if not avg_price:
            return ""

        qty = _to_int(info.get('qty'), default=0)
        if qty <= 1:
            return f" @ {avg_price}"

        total_amount = info.get('total_amount')
        total_text = _format_krw(total_amount, force_integer=True)
        if not total_text:
            return f" — 평균체결가 {avg_price}"
        return f" — 평균체결가 {avg_price} / 총체결금액 {total_text}"

    def _get_enabled_strategy_keys(self) -> Optional[set[str]]:
        if not self._enabled_strategy_provider:
            return None
        try:
            names = self._enabled_strategy_provider()
        except Exception:
            return None
        if names is None:
            return None
        return {
            key for key in (_strategy_report_key(name) for name in names)
            if key
        }

    @staticmethod
    def _is_strategy_enabled_for_report(name: str, enabled_strategy_keys: Optional[set[str]]) -> bool:
        if enabled_strategy_keys is None:
            return True
        return _strategy_report_key(name) in enabled_strategy_keys

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
            uses_net_return = any(t.get('net_return') not in (None, "") for t in normal_solds)
            avg_return = sum(self._trade_return_pct(t) for t in normal_solds) / len(normal_solds)
            return_label = "순수익률" if uses_net_return else "수익률"
            lines.append(f"• 당일 청산: {len(normal_solds)}건 (평균 {return_label} {avg_return:+.2f}%)")
            for t in normal_solds[:_MAX_SOLD_DETAILS_SHOWN]:
                code = str(t.get('code', '')).strip()
                name = self._db_resolve(code, str(t.get('name') or code))
                rr = self._trade_return_pct(t)
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

    @staticmethod
    def _trade_return_pct(trade: dict) -> float:
        value = trade.get('net_return')
        if value in (None, ""):
            value = trade.get('return_rate')
        try:
            return float(value or 0.0)
        except (TypeError, ValueError):
            return 0.0

    def _build_backtest_live_divergence_section(self, target_date: str) -> Optional[str]:
        if not self._virtual_trade_service or not self._backtest_journal_provider:
            return None

        try:
            backtest_records = self._backtest_journal_provider(target_date) or []
        except Exception:
            return None
        if not backtest_records:
            return None

        try:
            if hasattr(self._virtual_trade_service, "compare_with_backtest_journal"):
                report = self._virtual_trade_service.compare_with_backtest_journal(backtest_records)
            else:
                live_records = self._virtual_trade_service.get_standard_journal_records()
                report = compare_trade_journals(backtest_records, live_records)
        except Exception:
            return None

        summary = report.get("summary") or {}
        matched = int(summary.get("matched_count") or 0)
        unmatched_backtest = int(summary.get("unmatched_backtest_count") or 0)
        unmatched_live = int(summary.get("unmatched_live_count") or 0)
        if matched == 0 and unmatched_backtest == 0 and unmatched_live == 0:
            return None

        lines = [
            "<b>📊 백테스트-실거래 괴리</b>",
            f"• 매칭: {matched}건, 백테스트만 {unmatched_backtest}건, 실거래만 {unmatched_live}건",
        ]

        audit_lines = self._build_replay_audit_lines(backtest_records)
        if audit_lines:
            lines.extend(audit_lines)

        avg_diff = summary.get("avg_net_return_diff")
        avg_abs_diff = summary.get("avg_abs_net_return_diff")
        if avg_diff is not None:
            abs_part = f", 절대 {float(avg_abs_diff):.2f}%p" if avg_abs_diff is not None else ""
            lines.append(f"• 평균 순수익률 괴리: {float(avg_diff):+.2f}%p{abs_part}")

        fill_diff = summary.get("avg_fill_price_diff_pct")
        if fill_diff is not None:
            lines.append(f"• 평균 체결가 괴리: {float(fill_diff):+.4f}%")

        pnl_diff = summary.get("total_net_pnl_diff")
        if pnl_diff is not None:
            lines.append(f"• 총 순손익 괴리: {int(round(float(pnl_diff))):+,}원")

        matches = report.get("matches") or []
        ranked = sorted(
            [row for row in matches if row.get("net_return_diff") is not None],
            key=lambda row: abs(float(row.get("net_return_diff") or 0.0)),
            reverse=True,
        )
        for row in ranked[:3]:
            strategy = _esc(row.get("strategy") or "")
            code = _esc(row.get("code") or "")
            net_part = f"순수익률 {float(row.get('net_return_diff') or 0.0):+.2f}%p"
            fill_part = ""
            if row.get("fill_price_diff_pct") is not None:
                fill_part = f", 체결가 {float(row.get('fill_price_diff_pct')):+.4f}%"
            pnl_part = ""
            if row.get("net_pnl_diff") is not None:
                pnl_part = f", 순손익 {int(round(float(row.get('net_pnl_diff')))):+,}원"
            lines.append(f"  - {strategy}/{code}: {net_part}{fill_part}{pnl_part}")

        return "\n".join(lines)

    def _build_volatility_section(self, strategy_summaries: List[dict]) -> Optional[str]:
        """전략별 매수 종목의 20일 연환산 변동성 분포 (평균/중앙값/표본수) 요약."""
        rows: List[Tuple[str, int, float, float]] = []
        for summary in strategy_summaries:
            samples: List[float] = []
            for info in summary.get('bought', {}).values():
                vol = info.get('volatility_20d_annualized')
                if isinstance(vol, (int, float)) and vol == vol:  # NaN guard
                    samples.append(float(vol))
            if not samples:
                continue
            samples_sorted = sorted(samples)
            n = len(samples_sorted)
            avg = sum(samples_sorted) / n
            median = (
                samples_sorted[n // 2]
                if n % 2 == 1
                else (samples_sorted[n // 2 - 1] + samples_sorted[n // 2]) / 2
            )
            rows.append((summary['name'], n, avg, median))

        if not rows:
            return None

        rows.sort(key=lambda r: -r[2])  # 평균 변동성 내림차순
        lines = ["<b>📈 매수 종목 변동성 (20일 연환산)</b>"]
        for name, n, avg, median in rows:
            lines.append(
                f"• {_esc(name)} — {n}건 | 평균 {avg*100:.1f}% | 중앙값 {median*100:.1f}%"
            )
        return "\n".join(lines)

    def _build_signal_metadata_section(self, strategy_summaries: List[dict]) -> Optional[str]:
        """전략별 매수 신호 metadata 요약 (P1 1-6).

        진입사유(entry_reason) 분포 / 평균 confidence / 평균 기대 보유기간을 원장(bought)
        기준으로 집계한다. trailing_rule / required_data 는 journal metadata 로 보존되며
        일일 headline 에는 surfacing 하지 않는다.
        """
        rows: List[Tuple[str, int, str]] = []
        for summary in strategy_summaries:
            infos = list(summary.get('bought', {}).values())
            reasons = [str(info['entry_reason']) for info in infos if info.get('entry_reason')]
            confs = [
                float(info['confidence']) for info in infos
                if isinstance(info.get('confidence'), (int, float)) and info['confidence'] == info['confidence']
            ]
            holds = [
                float(info['expected_holding_period_days']) for info in infos
                if isinstance(info.get('expected_holding_period_days'), (int, float))
                and info['expected_holding_period_days'] == info['expected_holding_period_days']
            ]
            if not reasons and not confs and not holds:
                continue

            parts: List[str] = []
            if reasons:
                dist = Counter(reasons).most_common()
                parts.append("진입 " + ", ".join(f"{_esc(reason)}×{count}" for reason, count in dist))
            if confs:
                parts.append(f"평균 conf {sum(confs) / len(confs):.2f}")
            if holds:
                parts.append(f"기대보유 {sum(holds) / len(holds):.1f}일")

            n = max(len(reasons), len(confs), len(holds))
            rows.append((summary['name'], n, " | ".join(parts)))

        if not rows:
            return None

        lines = ["<b>🏷 매수 신호 메타데이터</b>"]
        for name, n, body in rows:
            lines.append(f"• {_esc(name)} — {n}건 | {body}")
        return "\n".join(lines)

    def _strategy_degradation_cfg(self) -> StrategyPerformanceDegradationConfig:
        cfg = self._strategy_degradation_config
        if isinstance(cfg, StrategyPerformanceDegradationConfig):
            return cfg
        values = {}
        if cfg is not None:
            for field in StrategyPerformanceDegradationConfig.__dataclass_fields__:
                if hasattr(cfg, field):
                    values[field] = getattr(cfg, field)
        return StrategyPerformanceDegradationConfig(**values)

    def _build_strategy_degradation_section(self, target_date: str) -> Optional[str]:
        self._last_strategy_degradation_candidates = []
        if not self._virtual_trade_service or not self._backtest_journal_provider:
            return None

        try:
            live_records = self._virtual_trade_service.get_standard_journal_records() or []
            backtest_records = self._backtest_journal_provider(target_date) or []
        except Exception:
            return None
        live_records = _normalize_strategy_metric_records(live_records)
        backtest_records = _normalize_strategy_metric_records(backtest_records)
        if not live_records and not backtest_records:
            return None

        divergence_by_strategy: Dict[str, dict] = {}
        try:
            divergence_by_strategy = self._backtest_live_divergence_by_strategy(
                backtest_records,
                live_records,
            )
        except Exception:
            divergence_by_strategy = {}

        try:
            result = analyze_strategy_performance_degradation(
                live_records,
                backtest_records,
                self._strategy_degradation_cfg(),
            )
        except Exception:
            return None

        candidates = [
            self._candidate_with_backtest_live_divergence(item, divergence_by_strategy)
            for item in (result.get("candidates") or [])
        ]
        self._last_strategy_degradation_candidates = [dict(item) for item in candidates]
        if not candidates:
            return None

        lines = ["<b>📉 전략별 성과 저하 후보</b>"]
        for item in candidates[:5]:
            strategy = _esc(_strategy_display_label(item.get("strategy")))
            status = _esc(item.get("status") or "")
            live = item.get("live_metrics") or {}
            reasons = ", ".join(str(reason) for reason in item.get("reasons") or [])
            actions = ", ".join(str(action) for action in item.get("recommended_actions") or [])
            pf = live.get("profit_factor")
            pf_text = f", PF {float(pf):.2f}" if pf is not None else ""
            lines.append(
                f"• {strategy}: {status} — 최근 {int(live.get('trade_count') or 0)}거래, "
                f"승률 {float(live.get('win_rate') or 0) * 100:.1f}%, "
                f"평균 {float(live.get('avg_net_return') or 0):+.2f}%{pf_text}, "
                f"MDD {int(round(float(live.get('mdd_amount') or 0))):,}원, "
                f"연속손실 {int(live.get('max_consecutive_losses') or 0)}"
            )
            if reasons:
                lines.append(f"  - 사유: {_esc(reasons)}")
            if actions:
                lines.append(f"  - 권고: {_esc(actions)}")
            divergence = item.get("backtest_live_divergence") or {}
            if divergence:
                lines.append(
                    "  - 백테스트 괴리: "
                    f"매칭 {int(divergence.get('matched_count') or 0)}건, "
                    f"백테스트만 {int(divergence.get('unmatched_backtest_count') or 0)}건, "
                    f"실거래만 {int(divergence.get('unmatched_live_count') or 0)}건"
                )
        rest_count = len(candidates) - 5
        if rest_count > 0:
            lines.append(f"  …외 {rest_count}개 전략")
        return "\n".join(lines)

    def _profitability_gate_cfg(self) -> StrategyProfitabilityGateConfig:
        cfg = self._profitability_gate_config
        if isinstance(cfg, StrategyProfitabilityGateConfig):
            return cfg
        values = {}
        if cfg is not None:
            for field in StrategyProfitabilityGateConfig.__dataclass_fields__:
                if hasattr(cfg, field):
                    values[field] = getattr(cfg, field)
        return StrategyProfitabilityGateConfig(**values)

    def _build_profitability_gate_section(self, target_date: str) -> Optional[str]:
        """라이브 표준 journal 에 profitability gate 를 돌려 전략별 통과/차단 근거를
        일일 리포트에 노출한다 (P1 1-6). 운영자가 "이 전략이 실전 확대 기준을
        통과하는가 / 어떤 사유로 막히는가"를 일일 리포트에서 확인하기 위함이다."""
        if not self._virtual_trade_service:
            return None
        try:
            live_records = self._virtual_trade_service.get_standard_journal_records() or []
        except Exception:
            return None
        if not live_records:
            return None
        live_records = _normalize_strategy_metric_records(live_records)
        if not live_records:
            return None

        cfg = self._profitability_gate_cfg()
        try:
            result = evaluate_strategy_profitability_gate(live_records, cfg)
        except Exception:
            return None
        strategies = result.get("strategies") or {}
        if not strategies:
            return None

        lines = ["<b>💹 전략별 수익성 게이트</b>"]
        for name in sorted(strategies):
            decision = strategies[name] or {}
            metrics = decision.get("metrics") or {}
            status = str(decision.get("status") or "")
            status_label = _esc(_PROFITABILITY_GATE_STATUS_LABELS.get(status, status))
            strategy = _esc(_strategy_display_label(name))
            pf = metrics.get("profit_factor")
            payoff = metrics.get("payoff_ratio")
            pf_text = f"{float(pf):.2f}" if pf is not None else "—"
            payoff_text = f"{float(payoff):.2f}" if payoff is not None else "—"
            lines.append(
                f"• {strategy}: {status_label} — "
                f"거래 {int(metrics.get('trade_count') or 0)}/{int(cfg.min_trades)}, "
                f"승률 {float(metrics.get('win_rate') or 0) * 100:.1f}%, "
                f"PF {pf_text}, payoff {payoff_text}, "
                f"순손익 {int(round(float(metrics.get('total_net_pnl') or 0))):,}원"
            )
            reasons = decision.get("blocking_reasons") or []
            if reasons:
                reason_text = ", ".join(_profitability_gate_reason_label(r) for r in reasons)
                lines.append(f"  - 차단 사유: {_esc(reason_text)}")
        return "\n".join(lines)

    def _build_standard_journal_accumulation_section(self, target_date: str) -> Optional[str]:
        """shadow/paper/canary journal 축적량을 일일 리포트에 노출한다 (P1 1-6).

        profitability gate 는 표본이 부족하면 차단 사유만 보여주므로, 이 섹션은
        운영자가 소스별/전략별 표본 축적 속도를 별도로 확인하기 위한 요약이다.
        """
        if not self._virtual_trade_service:
            return None
        try:
            records = self._virtual_trade_service.get_standard_journal_records() or []
        except Exception:
            return None
        if not records:
            return None

        normalized_records = _normalize_strategy_metric_records(records)
        status_counts = Counter(
            str(record.get("status") or "UNKNOWN").upper()
            for record in records
        )
        source_counts = Counter(_journal_source(record) for record in records)
        sold_by_strategy = Counter(
            str(record.get("strategy") or "")
            for record in normalized_records
            if str(record.get("status") or "").upper() == "SOLD"
        )
        total_by_strategy = Counter(
            str(record.get("strategy") or "")
            for record in normalized_records
        )

        sold_count = status_counts.get("SOLD", 0)
        open_count = sum(
            count for status, count in status_counts.items()
            if status != "SOLD"
        )
        source_text = ", ".join(
            f"{_esc(source)} {count}건"
            for source, count in sorted(
                source_counts.items(),
                key=lambda item: (-item[1], item[0]),
            )
        )
        cfg = self._profitability_gate_cfg()
        lines = [
            "<b>📒 표준 journal 축적 현황</b>",
            f"• 전체 {len(records)}건 / SOLD {sold_count}건 / 진행중 {open_count}건",
        ]
        if source_text:
            lines.append(f"• source: {source_text}")

        strategy_names = sorted(
            total_by_strategy,
            key=lambda name: (-sold_by_strategy.get(name, 0), name),
        )[:5]
        for name in strategy_names:
            strategy = _esc(_strategy_display_label(name))
            sold = sold_by_strategy.get(name, 0)
            total = total_by_strategy.get(name, 0)
            lines.append(
                f"• {strategy}: SOLD {sold}/{int(cfg.min_trades)}건 "
                f"(전체 {total}건)"
            )
        remaining = len(total_by_strategy) - len(strategy_names)
        if remaining > 0:
            lines.append(f"  …외 {remaining}개 전략")
        return "\n".join(lines)

    def _build_multiple_testing_section(self, target_date: str) -> Optional[str]:
        """다중검정 편향 / Deflated Sharpe 요약 (P1 1-7).

        live journal 의 전략별 window metric(sharpe_ratio/total_net_pnl/trade_count)을
        compute_multiple_testing_bias_summary 로 집계해 formal Deflated Sharpe, proxy,
        편향 경고를 노출한다. DSR/proxy 어느 것도 산출되지 않고 경고도 없으면 생략한다.
        """
        if not self._virtual_trade_service:
            return None
        try:
            live_records = self._virtual_trade_service.get_standard_journal_records() or []
        except Exception:
            return None
        if not live_records:
            return None

        try:
            cfg = self._strategy_degradation_cfg()
            metric_records = _normalize_strategy_metric_records(live_records)
            if not metric_records:
                return None
            metrics_by_strategy = compute_strategy_window_metrics(
                metric_records,
                window_size=max(int(cfg.window_size or 20), 1),
                capital_base_won=cfg.capital_base_won,
            )
            summary = compute_multiple_testing_bias_summary(metrics_by_strategy)
        except Exception:
            return None

        if int(summary.get("trial_count") or 0) < 2:
            return None

        dsr = summary.get("deflated_sharpe") or {}
        proxy = summary.get("deflated_sharpe_proxy") or {}
        pbo = summary.get("pbo_proxy") or {}

        detail_lines: List[str] = []
        if dsr.get("available"):
            detail_lines.append(
                f"• Deflated Sharpe(확률) {float(dsr.get('deflated_sharpe_ratio') or 0):.3f} "
                f"— best SR {float(dsr.get('best_sharpe') or 0):.2f}, "
                f"기대최대 {float(dsr.get('expected_max_sharpe') or 0):.2f}, "
                f"best 전략 거래 표본 {int(dsr.get('sample_size') or 0)}건"
            )
        if proxy.get("available"):
            detail_lines.append(
                f"• adjusted Sharpe(proxy) {float(proxy.get('adjusted_sharpe') or 0):.2f} "
                f"(haircut {float(proxy.get('selection_haircut') or 0):.2f})"
            )
        if pbo.get("available"):
            detail_lines.append(
                f"• PBO(proxy) {float(pbo.get('pbo_probability') or 0):.2f}"
            )

        warn_line = None
        if summary.get("bias_warning"):
            reasons = ", ".join(str(reason) for reason in summary.get("warning_reasons") or [])
            warn_line = f"  - ⚠️ 편향 경고: {_esc(reasons)}"

        if not detail_lines and not warn_line:
            return None

        best = summary.get("best_strategy")
        best_label = _strategy_display_label(best) if best else ""
        header = [
            "<b>🧪 다중검정 / Deflated Sharpe</b>",
            f"• 성과 표본 전략 {int(summary.get('trial_count') or 0)}개"
            + (f", 최고 {_esc(best_label)}" if best_label else ""),
        ]
        return "\n".join(header + detail_lines + ([warn_line] if warn_line else []))

    def _build_strategy_correlation_section(self, target_date: str) -> Optional[str]:
        """전략 간 일별 net_return 상관 요약 (R-2).

        live journal 로 compute_strategy_correlation_summary 를 돌려 최고 상관쌍과
        고상관(≥threshold) 클러스터를 노출한다. "7전략 분산" 착시(전 전략 long 모멘텀
        동시 손실)를 운영 중 조기에 드러내기 위함. 비교 가능한 쌍이 없으면 생략한다.
        """
        if not self._virtual_trade_service:
            return None
        try:
            live_records = self._virtual_trade_service.get_standard_journal_records() or []
        except Exception:
            return None
        if not live_records:
            return None
        live_records = _normalize_strategy_metric_records(live_records)
        if not live_records:
            return None

        try:
            summary = compute_strategy_correlation_summary(live_records)
        except Exception:
            return None

        if int(summary.get("pair_count") or 0) < 1:
            return None

        lines = [
            "<b>🔗 전략 상관 (일별 net_return)</b>",
            f"• 전략 {int(summary.get('strategy_count') or 0)}개, 비교쌍 {int(summary.get('pair_count') or 0)}개",
        ]
        mx = summary.get("max_positive_pair") or {}
        if mx:
            lines.append(
                f"• 최고 상관: {_esc(_strategy_display_label(mx.get('left')))}"
                f"↔{_esc(_strategy_display_label(mx.get('right')))} "
                f"{float(mx.get('correlation') or 0):+.2f} (overlap {int(mx.get('overlap') or 0)})"
            )

        high = summary.get("high_correlation_pairs") or []
        if high:
            threshold = float(summary.get("warning_threshold") or 0.8)
            lines.append(f"  - ⚠️ 고상관(≥{threshold:.2f}) {len(high)}쌍:")
            for pair in high[:3]:
                lines.append(
                    f"    · {_esc(_strategy_display_label(pair.get('left')))}"
                    f"↔{_esc(_strategy_display_label(pair.get('right')))} "
                    f"{float(pair.get('correlation') or 0):+.2f}"
                )
            if len(high) > 3:
                lines.append(f"    · …외 {len(high) - 3}쌍")

        return "\n".join(lines)

    def _build_overnight_exposure_section(self, target_date: str) -> Optional[str]:
        """전략별 오버나이트(멀티세션 보유) 노출 요약 (R-4 후속).

        장 마감 후 남은 HOLD 포지션(=익일 갭 노출)과 실현된 멀티세션 보유의 다운사이드
        분포를 노출한다. 대부분 전략이 force_exit_on_close=False 로 오버나이트를 허용하므로,
        익일 갭에 노출되는 규모를 운영 중 가시화한다. 실제 익일 시가 갭의 정량 측정은
        종목별 OHLCV 가 필요해 범위 밖이며, 여기서는 노출 규모와 사후 downside proxy 만 본다.
        노출이 전혀 없으면 생략한다.
        """
        if not self._virtual_trade_service:
            return None
        try:
            live_records = self._virtual_trade_service.get_standard_journal_records() or []
        except Exception:
            return None
        if not live_records:
            return None
        live_records = _normalize_strategy_metric_records(live_records)
        if not live_records:
            return None

        try:
            summary = compute_overnight_exposure_summary(live_records, today=target_date)
        except Exception:
            return None

        open_holds = summary.get("open_holds") or {}
        realized = summary.get("realized_overnight") or {}
        open_total = int(open_holds.get("total") or 0)
        realized_total = int(realized.get("total") or 0)
        if open_total == 0 and realized_total == 0:
            return None

        lines = ["<b>🌙 오버나이트 노출 (익일 갭 리스크)</b>"]
        if open_total > 0:
            lines.append(f"• 현재 보유(익일 갭 노출): {open_total}종목")
            for row in (open_holds.get("by_strategy") or [])[:5]:
                lines.append(
                    f"  • {_esc(_strategy_display_label(row.get('strategy')))}: "
                    f"{int(row.get('count') or 0)}종목 "
                    f"(최장 {int(row.get('max_holding_days') or 0)}일, "
                    f"평균 {float(row.get('avg_holding_days') or 0):.1f}일)"
                )
        if realized_total > 0:
            lines.append(f"• 실현 멀티세션 보유: {realized_total}건")
            for row in (realized.get("by_strategy") or [])[:5]:
                lines.append(
                    f"  • {_esc(_strategy_display_label(row.get('strategy')))}: "
                    f"{int(row.get('count') or 0)}건 "
                    f"(평균보유 {float(row.get('avg_holding_days') or 0):.1f}일, "
                    f"평균순익 {float(row.get('avg_net_return') or 0):+.2f}%, "
                    f"최저 {float(row.get('worst_net_return') or 0):+.2f}%)"
                )

        return "\n".join(lines)

    def _build_regime_decomposition_section(self, target_date: str) -> Optional[str]:
        """전략별 시장 국면(regime) 성과 분해 + 집중도 (R-2 후속).

        live journal 로 compute_strategy_regime_decomposition 를 돌려 전략별 dominant
        regime 과 버킷별 평균순익을 노출하고, 전 전략이 같은 regime 에 몰려 있는지
        (=단일 regime 베팅) concentration 으로 드러낸다. "7전략 분산"이 사실은 단일
        상승/추세 regime 베팅이면 강세장 동시 수익·약세장 동시 손실 위험이 있다.
        비교 가능한 전략(regime 채워진 SOLD)이 2개 미만이면 생략한다.
        """
        if not self._virtual_trade_service:
            return None
        try:
            live_records = self._virtual_trade_service.get_standard_journal_records() or []
        except Exception:
            return None
        if not live_records:
            return None
        live_records = _normalize_strategy_metric_records(live_records)
        if not live_records:
            return None

        try:
            summary = compute_strategy_regime_decomposition(live_records)
        except Exception:
            return None

        strategies = summary.get("strategies") or []
        if len(strategies) < 2:
            return None

        lines = ["<b>🧭 전략별 시장국면(regime) 분해</b>"]

        concentration = summary.get("concentration") or {}
        top_bucket = concentration.get("top_bucket")
        if top_bucket:
            pct = float(concentration.get("concentration_pct") or 0.0) * 100
            top_n = int(concentration.get("top_bucket_strategy_count") or 0)
            total_n = int(summary.get("strategy_count") or 0)
            line = (
                f"• 집중도: {top_n}/{total_n} 전략 주력 국면이 "
                f"'{_REGIME_BUCKET_LABELS.get(top_bucket, top_bucket)}' ({pct:.0f}%)"
            )
            if pct >= 70.0:
                line += " ⚠️ 단일 regime 집중"
            lines.append(line)

        for row in strategies[:6]:
            dominant = row.get("dominant_bucket")
            dom_label = _REGIME_BUCKET_LABELS.get(dominant, dominant) if dominant else "—"
            lines.append(
                f"• {_esc(_strategy_display_label(row.get('strategy')))}: 주력 {dom_label} "
                f"(SOLD {int(row.get('trade_count') or 0)}건)"
            )
            by_bucket = row.get("by_bucket") or {}
            for bucket_name in BUCKET_KEYS:
                metrics = by_bucket.get(bucket_name)
                if not metrics:
                    continue
                lines.append(
                    f"  - {_REGIME_BUCKET_LABELS.get(bucket_name, bucket_name)}: "
                    f"{int(metrics.get('trade_count') or 0)}건, "
                    f"승률 {float(metrics.get('win_rate') or 0) * 100:.0f}%, "
                    f"평균순익 {float(metrics.get('avg_net_return') or 0):+.2f}%"
                )

        return "\n".join(lines)

    @staticmethod
    def _extract_gate_pass_strategies(profitability_gate_section: Optional[str]) -> List[str]:
        if not profitability_gate_section:
            return []
        names: List[str] = []
        for line in profitability_gate_section.splitlines():
            match = re.match(r"•\s+(.+?):\s+통과\b", re.sub(r"<[^>]+>", "", line).strip())
            if match:
                names.append(match.group(1).strip())
        return names

    @staticmethod
    def _multiple_testing_is_weak(multiple_testing_section: Optional[str]) -> bool:
        if not multiple_testing_section:
            return False
        text = re.sub(r"<[^>]+>", "", multiple_testing_section)
        if "편향 경고" in text or "adjusted Sharpe(proxy) -" in text:
            return True
        match = re.search(r"Deflated Sharpe\(확률\)\s+([0-9.]+)", text)
        if match:
            try:
                return float(match.group(1)) < 0.55
            except ValueError:
                return False
        return False

    @staticmethod
    def _extract_broker_reconciled_count(overnight_exposure_section: Optional[str]) -> Optional[int]:
        if not overnight_exposure_section or "broker_reconciled" not in overnight_exposure_section:
            return None
        match = re.search(r"broker_reconciled:\s+(\d+)종목", overnight_exposure_section)
        if not match:
            return None
        return int(match.group(1))

    @staticmethod
    def _extract_open_hold_count(overnight_exposure_section: Optional[str]) -> Optional[int]:
        if not overnight_exposure_section:
            return None
        match = re.search(r"현재 보유\(익일 갭 노출\):\s+(\d+)종목", overnight_exposure_section)
        if not match:
            return None
        return int(match.group(1))

    @staticmethod
    def _extract_regime_concentration_label(regime_decomposition_section: Optional[str]) -> Optional[str]:
        if not regime_decomposition_section or "단일 regime 집중" not in regime_decomposition_section:
            return None
        match = re.search(r"'([^']+)'", regime_decomposition_section)
        return match.group(1) if match else "단일"

    def _build_operational_decision_report(
        self,
        target_date: str,
        *,
        buy_count: int,
        buy_entries: List[dict],
        profitability_gate_section: Optional[str],
        multiple_testing_section: Optional[str],
        overnight_exposure_section: Optional[str],
        regime_decomposition_section: Optional[str],
        degradation_section: Optional[str],
        execution_quality_section: Optional[str],
        warnings_section: Optional[str],
    ) -> str:
        lines = [f"<b>🧭 [{_fmt_date(target_date)}] 운영 의사결정 요약</b>"]

        if buy_count > 0:
            lines.append(f"• 신규 진입 {buy_count}건 — 체결/포지션 관리 확인")
            for entry in buy_entries:
                strategy = _esc(entry.get("strategy") or "전략 미상")
                code = str(entry.get("code") or "").strip()
                name = _esc(entry.get("name") or code or "종목 미상")
                instrument = f"{name}({_esc(code)})" if code else name
                details = [strategy, instrument]

                qty = _to_int(entry.get("qty"), default=0)
                if qty > 0:
                    details.append(f"{qty}주")
                price = _format_krw(entry.get("price"))
                if price:
                    details.append(price)
                if entry.get("time"):
                    details.append(_esc(entry["time"]))
                lines.append(f"  - {' · '.join(details)}")
        else:
            lines.append("• 신규 진입 없음 — 주요 후보는 진입 조건 미달/시그널 없음")

        pass_strategies = self._extract_gate_pass_strategies(profitability_gate_section)
        multiple_weak = self._multiple_testing_is_weak(multiple_testing_section)
        if pass_strategies:
            shown = ", ".join(_esc(name) for name in pass_strategies[:3])
            extra = f" 외 {len(pass_strategies) - 3}개" if len(pass_strategies) > 3 else ""
            caveat = " (단 Deflated Sharpe 약함)" if multiple_weak else ""
            lines.append(f"• 전략 확대 후보: {shown}{extra}{caveat}")
        else:
            lines.append("• 전략 확대 후보: 없음 — 표본/수익성 게이트 기준 미충족")
            if multiple_weak:
                lines.append("• 검증 리스크: Deflated Sharpe 약함")

        broker_count = self._extract_broker_reconciled_count(overnight_exposure_section)
        if broker_count is not None:
            lines.append(
                f"• 즉시 점검: broker_reconciled 보유 {broker_count}종목의 전략 귀속/청산 책임"
            )
        else:
            open_hold_count = self._extract_open_hold_count(overnight_exposure_section)
            if open_hold_count:
                lines.append(f"• 보유 리스크: 익일 갭 노출 {open_hold_count}종목")

        regime_label = self._extract_regime_concentration_label(regime_decomposition_section)
        if regime_label:
            lines.append(f"• 리스크: {_esc(regime_label)} regime 집중")

        if degradation_section:
            lines.append("• 성과 저하 후보: 별도 경고 확인")
        if execution_quality_section:
            lines.append("• 체결 품질 후보: 별도 경고 확인")
        if warnings_section:
            lines.append("• 시스템 경고: 데이터 수신/파싱 상태 확인")

        if pass_strategies and not (broker_count or regime_label or multiple_weak):
            lines.append("• 다음 행동: 통과 전략만 소액 확대 검토")
        else:
            lines.append("• 다음 행동: 신규매수 기준 유지, 점검 항목 해결 전 전략 확대 보류")
        return "\n".join(lines)

    def _build_replay_audit_lines(self, backtest_records: List[dict]) -> List[str]:
        counts = Counter()
        examples: List[dict] = []
        for record in backtest_records:
            metadata = record.get("metadata") if isinstance(record, dict) else None
            if not isinstance(metadata, dict):
                continue
            status = str(metadata.get("audit_status") or "")
            if not status:
                continue
            counts[status] += 1
            if status in {"missed_by_scheduler", "late_signal"} and len(examples) < 3:
                examples.append(record)
        if not counts:
            return []

        labels = [
            ("missed_by_scheduler", "missed"),
            ("late_signal", "late"),
            ("missing_from_universe", "universe 누락"),
            ("replayed_rejected", "replay 거절"),
            ("data_unavailable", "데이터 부족"),
        ]
        parts = [
            f"{label} {counts[key]}건"
            for key, label in labels
            if counts.get(key)
        ]
        lines = [f"• Replay audit: {', '.join(parts)}"]
        for record in examples:
            metadata = record.get("metadata") or {}
            strategy = _esc(record.get("strategy") or "")
            code = _esc(record.get("code") or "")
            status = _esc(metadata.get("audit_status") or "")
            live_time = metadata.get("live_signal_time")
            suffix = f" (live {live_time[11:16]})" if isinstance(live_time, str) and len(live_time) >= 16 else ""
            lines.append(f"  - {strategy}/{code}: {status}{suffix}")
        return lines

    def _candidate_with_backtest_live_divergence(
        self,
        candidate: dict,
        divergence_by_strategy: Dict[str, dict],
    ) -> dict:
        strategy = str(candidate.get("strategy") or "")
        divergence = divergence_by_strategy.get(strategy)
        if not divergence:
            return candidate

        enriched = dict(candidate)
        reasons = list(enriched.get("reasons") or [])
        if "backtest_live_divergence" not in reasons:
            reasons.append("backtest_live_divergence")
        enriched["reasons"] = reasons
        enriched["backtest_live_divergence"] = divergence
        return enriched

    def _backtest_live_divergence_by_strategy(
        self,
        backtest_records: List[dict],
        live_records: List[dict],
    ) -> Dict[str, dict]:
        report = compare_trade_journals(backtest_records, live_records)
        grouped: Dict[str, dict] = {}

        for row in report.get("matches") or []:
            strategy = str(row.get("strategy") or "")
            if not strategy:
                continue
            item = grouped.setdefault(strategy, self._empty_strategy_divergence())
            item["matched_count"] += 1
            item["_net_return_diffs"].append(row.get("net_return_diff"))
            item["_fill_price_diffs"].append(row.get("fill_price_diff_pct"))
            item["_net_pnl_diffs"].append(row.get("net_pnl_diff"))
            item["top_matches"].append({
                "code": row.get("code"),
                "trade_date": row.get("trade_date"),
                "net_return_diff": row.get("net_return_diff"),
                "fill_price_diff_pct": row.get("fill_price_diff_pct"),
                "net_pnl_diff": row.get("net_pnl_diff"),
            })

        for key, field in (
            ("unmatched_backtest", "unmatched_backtest_count"),
            ("unmatched_live", "unmatched_live_count"),
        ):
            for record in report.get(key) or []:
                strategy = str(record.get("strategy") or "")
                if not strategy:
                    continue
                grouped.setdefault(strategy, self._empty_strategy_divergence())[field] += 1

        return {
            strategy: self._finalize_strategy_divergence(item)
            for strategy, item in grouped.items()
        }

    @staticmethod
    def _empty_strategy_divergence() -> dict:
        return {
            "matched_count": 0,
            "unmatched_backtest_count": 0,
            "unmatched_live_count": 0,
            "top_matches": [],
            "_net_return_diffs": [],
            "_fill_price_diffs": [],
            "_net_pnl_diffs": [],
        }

    @staticmethod
    def _finalize_strategy_divergence(item: dict) -> dict:
        top_matches = sorted(
            item.get("top_matches") or [],
            key=lambda row: abs(float(row.get("net_return_diff") or 0.0)),
            reverse=True,
        )[:3]
        return {
            "matched_count": item.get("matched_count", 0),
            "unmatched_backtest_count": item.get("unmatched_backtest_count", 0),
            "unmatched_live_count": item.get("unmatched_live_count", 0),
            "avg_net_return_diff": _avg_numeric(item.get("_net_return_diffs") or []),
            "avg_abs_net_return_diff": _avg_numeric(
                [abs(value) for value in _numeric_values(item.get("_net_return_diffs") or [])]
            ),
            "avg_fill_price_diff_pct": _avg_numeric(item.get("_fill_price_diffs") or []),
            "total_net_pnl_diff": _sum_numeric(item.get("_net_pnl_diffs") or []),
            "top_matches": top_matches,
        }

    def _build_execution_quality_section(self, records: List[dict]) -> Optional[str]:
        if not records:
            self._last_execution_quality_candidates = []
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
            spread_values = [
                item["spread_pct"]
                for item in items
                if item.get("spread_pct") is not None
            ]
            order_type_counts = Counter(
                str(item.get("order_type") or "unknown")
                for item in items
            )
            strategy_rows.append({
                "strategy": strategy,
                "count": len(items),
                "period": self._execution_quality_period_for_items(items),
                "avg_slip": avg_slip,
                "p95_slip": self._percentile(slip_values, 95),
                "max_slip": max_slip,
                "avg_latency": avg_latency,
                "incomplete_fill_ratio": incomplete_count / len(items) * 100 if items else 0.0,
                "avg_unfilled_ratio": sum(unfilled_values) / len(unfilled_values) if unfilled_values else None,
                "avg_order_age": sum(age_values) / len(age_values) if age_values else None,
                "avg_spread": sum(spread_values) / len(spread_values) if spread_values else None,
                "order_type_counts": dict(order_type_counts),
            })

        strategy_rows.sort(key=lambda row: (
            row["avg_slip"] is None,
            -(row["avg_slip"] or 0),
            -(row["avg_unfilled_ratio"] or 0),
            row["strategy"],
        ))
        candidate_rows = []
        self._last_execution_quality_candidates = []
        for row in strategy_rows:
            row["quality_label"] = self._execution_quality_label(row)
            if "비활성화" in row["quality_label"]:
                candidate_rows.append(row)
                self._last_execution_quality_candidates.append({
                    "strategy": row["strategy"],
                    "period": row.get("period", ""),
                    "count": row["count"],
                    "reason": row["quality_label"].split(":", 1)[-1].strip(),
                    "avg_slip": row.get("avg_slip"),
                    "p95_slip": row.get("p95_slip"),
                    "avg_latency": row.get("avg_latency"),
                    "incomplete_fill_ratio": row.get("incomplete_fill_ratio"),
                    "avg_unfilled_ratio": row.get("avg_unfilled_ratio"),
                    "avg_order_age": row.get("avg_order_age"),
                })

        if candidate_rows:
            parts = []
            for row in candidate_rows[:3]:
                period = f"{row['period']} " if row.get("period") else ""
                reason = row["quality_label"].split(":", 1)[-1].strip()
                parts.append(f"{_esc(row['strategy'])}({period}{_esc(reason)})")
            extra = f" 외 {len(candidate_rows) - 3}개" if len(candidate_rows) > 3 else ""
            lines.append(f"• ⚠️ 비활성화 후보 {len(candidate_rows)}개: {', '.join(parts)}{extra}")

        for row in strategy_rows[:_MAX_EXECUTION_QUALITY_ROWS]:
            slip_str = f"{row['avg_slip']:.3f}%" if row["avg_slip"] is not None else "N/A"
            p95_str = f"{row['p95_slip']:.3f}%" if row["p95_slip"] is not None else "N/A"
            max_str = f"{row['max_slip']:.3f}%" if row["max_slip"] is not None else "N/A"
            latency_str = f"{row['avg_latency']:.1f}s" if row["avg_latency"] is not None else "N/A"
            incomplete_str = f"{row['incomplete_fill_ratio']:.1f}%"
            unfilled_str = f"{row['avg_unfilled_ratio']:.1f}%" if row["avg_unfilled_ratio"] is not None else "N/A"
            age_str = f"{row['avg_order_age']:.1f}s" if row["avg_order_age"] is not None else "N/A"
            spread_str = f"{row['avg_spread']:.3f}%" if row["avg_spread"] is not None else "N/A"
            order_type_str = self._format_order_type_counts(row.get("order_type_counts", {}))
            period_str = f"[{row['period']}] " if row.get("period") else ""
            quality_label = row["quality_label"]
            quality_str = f" {quality_label}" if quality_label else ""
            lines.append(
                f"• {period_str}{_esc(row['strategy'])}: {row['count']}건, 평균 슬리피지 {slip_str}, "
                f"P95 {p95_str}, 최대 {max_str}, 평균 지연 {latency_str}, "
                f"불완전 체결 {incomplete_str}, 평균 잔량 {unfilled_str}, 평균 지속 {age_str}, "
                f"평균 스프레드 {spread_str}, 주문유형 {order_type_str}{quality_str}"
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

    def _execution_quality_period_for_items(self, items: List[dict]) -> str:
        periods = {self._execution_quality_period_label(item.get("timestamp", "")) for item in items}
        periods.discard("")
        if not periods:
            return ""
        if len(periods) == 1:
            return next(iter(periods))
        return "4-2 전후 혼합"

    def _execution_quality_period_label(self, timestamp: str) -> str:
        cfg = self._execution_quality_config
        effective = str(getattr(cfg, "liquidity_control_effective_date", "") or "").strip()
        effective = re.sub(r"\D", "", effective)
        if len(effective) != 8:
            return ""
        ts_date = re.sub(r"\D", "", str(timestamp)[:10])
        if len(ts_date) != 8:
            return ""
        return "4-2 적용 후" if ts_date >= effective else "4-2 적용 전"

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
    def _format_order_type_counts(counts: dict) -> str:
        if not counts:
            return "N/A"
        labels = {
            "market": "시장가",
            "limit": "지정가",
            "unknown": "미상",
        }
        order = ("market", "limit", "unknown")
        parts = []
        for key in order:
            count = int(counts.get(key) or 0)
            if count:
                parts.append(f"{labels.get(key, key)} {count}")
        for key, count in sorted(counts.items()):
            if key in order or not count:
                continue
            parts.append(f"{labels.get(key, key)} {int(count)}")
        return "/".join(parts) if parts else "N/A"

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
        self._last_execution_quality_candidates = []
        self._last_strategy_degradation_candidates = []
        strategy_files = self._find_strategy_files()

        if not strategy_files:
            self._last_operational_decision_report = self._build_operational_decision_report(
                target_date,
                buy_count=0,
                buy_entries=[],
                profitability_gate_section=None,
                multiple_testing_section=None,
                overnight_exposure_section=None,
                regime_decomposition_section=None,
                degradation_section=None,
                execution_quality_section=None,
                warnings_section=None,
            )
            return (
                f"<b>📊 [{_fmt_date(target_date)}] 전략 실행 요약</b>\n\n"
                "당일 전략 로그가 없습니다."
            )

        strategy_summaries: List[dict] = []
        inactive_names: List[str] = []
        market_timing: Dict[str, Tuple[str, bool]] = {}
        has_executed_buy_source, executed_buys_by_strategy = self._executed_buys_by_strategy(target_date)
        enabled_strategy_keys = self._get_enabled_strategy_keys()

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
                            'volatility_20d_annualized': _to_float(metrics.get('volatility_20d_annualized')),
                            # P1 1-6: 신호 metadata fallback (전략이 metrics 에 실어 보낼 때만 채워짐).
                            'entry_reason': (str(metrics.get('entry_reason')) if metrics.get('entry_reason') else None),
                            'confidence': _to_float(metrics.get('confidence')),
                            'expected_holding_period_days': _to_float(metrics.get('expected_holding_period_days')),
                        }
                        rejected.pop(code, None)
                        near_miss.pop(code, None)

                    elif event == 'execution_quality' and code:
                        name_value = name_map.get(code) or data.get('name') or code
                        strategy_name = _strategy_name_from_source(data.get("source") or data.get("strategy_name"))
                        if not self._is_strategy_enabled_for_report(strategy_name, enabled_strategy_keys):
                            continue
                        execution_quality_records.append({
                            "timestamp": ts,
                            "order_key": data.get("order_key") or f"{ts}:{code}:{len(execution_quality_records)}",
                            "strategy": strategy_name,
                            "code": str(code).strip(),
                            "name": self._db_resolve(str(code).strip(), str(name_value)),
                            "side": data.get("side", ""),
                            "state": data.get("state", ""),
                            "order_type": str(data.get("order_type") or "unknown"),
                            "spread_pct": _to_float(data.get("spread_pct")),
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
                            near_miss[code]['note'] = _HTF_EARLY_GUARD_NOTE

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
                                'note': _HTF_EARLY_GUARD_NOTE if code in early_guard_skipped else "",
                            }

            # StockCodeRepository로 미해결 종목명 보완
            if has_executed_buy_source:
                bought = executed_buys_by_strategy.get(_strategy_report_key(name), {})

            if self._stock_code_repo:
                for code, info in bought.items():
                    info['name'] = self._db_resolve(code, info['name'])
                for code, info in rejected.items():
                    info['name'] = self._db_resolve(code, info['name'])
                for code, info in near_miss.items():
                    info['name'] = self._db_resolve(code, info['name'])

            if not self._is_strategy_enabled_for_report(name, enabled_strategy_keys):
                continue

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
        operational_buy_entries: List[dict] = []
        for summary in strategy_summaries:
            for code, info in summary['bought'].items():
                fallback_buys.append({'code': code, 'name': info['name']})
                operational_buy_entries.append({
                    'strategy': summary['name'],
                    'code': code,
                    'name': info['name'],
                    'qty': info.get('qty'),
                    'price': info.get('price'),
                    'time': info.get('time'),
                })
                entry = confluence_map.setdefault(code, {'name': info['name'], 'count': 0, 'strategies': []})
                entry['name'] = info['name']
                entry['count'] += 1
                entry['strategies'].append(summary['name'])

        active_sections: List[str] = []
        for idx, summary in enumerate(strategy_summaries, start=1):
            if summary['scan_only']:
                active_sections.append(
                    f"<b>{idx}. {_esc(summary['name'])}</b> — "
                    f"최근 관찰 후보 {summary['scan_count']}종목 (시그널 없음)"
                )
                continue

            scan_str = f" — 최근 관찰 후보 {summary['scan_count']}종목" if summary['scan_count'] else ""
            lines = [f"<b>{idx}. {_esc(summary['name'])}</b>{scan_str}"]

            if summary['bought']:
                lines.append(f"\n✅ 매수 완료 ({len(summary['bought'])}건)")
                for code, info in summary['bought'].items():
                    price_str = self._format_buy_execution_detail(info)
                    time_str = f" ({info['time']})" if info.get('time') else ""
                    confluence = confluence_map.get(code, {})
                    confluence_str = ""
                    if confluence.get('count', 0) >= _CONFLUENCE_MIN_STRATEGIES:
                        confluence_str = f" [🔥 다중 전략 포착: {_esc(', '.join(confluence['strategies']))}]"
                    lines.append(f"• {_esc(info['name'])}({code}){confluence_str}: {_esc(info['reason'])}{price_str}{time_str}")
            else:
                lines.append("\n✅ 매수 완료: 없음")

            if summary['rejected']:
                lines.append(f"\n❌ 매수 실패 종목 ({len(summary['rejected'])}종목)")
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
                    lines.append(f"  …외 {rest_count}종목")
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
        divergence_section = self._build_backtest_live_divergence_section(target_date)
        degradation_section = self._build_strategy_degradation_section(target_date)
        journal_accumulation_section = self._build_standard_journal_accumulation_section(target_date)
        profitability_gate_section = self._build_profitability_gate_section(target_date)
        volatility_section = self._build_volatility_section(strategy_summaries)
        signal_metadata_section = self._build_signal_metadata_section(strategy_summaries)
        multiple_testing_section = self._build_multiple_testing_section(target_date)
        strategy_correlation_section = self._build_strategy_correlation_section(target_date)
        overnight_exposure_section = self._build_overnight_exposure_section(target_date)
        regime_decomposition_section = self._build_regime_decomposition_section(target_date)
        self._last_operational_decision_report = self._build_operational_decision_report(
            target_date,
            buy_count=len(fallback_buys),
            buy_entries=operational_buy_entries,
            profitability_gate_section=profitability_gate_section,
            multiple_testing_section=multiple_testing_section,
            overnight_exposure_section=overnight_exposure_section,
            regime_decomposition_section=regime_decomposition_section,
            degradation_section=degradation_section,
            execution_quality_section=execution_quality_section,
            warnings_section=warnings_section,
        )

        if not active_sections:
            portfolio_summary = self._build_portfolio_summary(target_date, fallback_buys)
            extra_sections = [
                section
                for section in (
                    warnings_section,
                    portfolio_summary,
                    divergence_section,
                    degradation_section,
                    journal_accumulation_section,
                    profitability_gate_section,
                    execution_quality_section,
                    volatility_section,
                    signal_metadata_section,
                    multiple_testing_section,
                    strategy_correlation_section,
                    overnight_exposure_section,
                    regime_decomposition_section,
                )
                if section
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
        if divergence_section:
            body += f"\n\n{divergence_section}"
        if degradation_section:
            body += f"\n\n{degradation_section}"
        if journal_accumulation_section:
            body += f"\n\n{journal_accumulation_section}"
        if profitability_gate_section:
            body += f"\n\n{profitability_gate_section}"
        if execution_quality_section:
            body += f"\n\n{execution_quality_section}"
        if volatility_section:
            body += f"\n\n{volatility_section}"
        if signal_metadata_section:
            body += f"\n\n{signal_metadata_section}"
        if multiple_testing_section:
            body += f"\n\n{multiple_testing_section}"
        if strategy_correlation_section:
            body += f"\n\n{strategy_correlation_section}"
        if overnight_exposure_section:
            body += f"\n\n{overnight_exposure_section}"
        if regime_decomposition_section:
            body += f"\n\n{regime_decomposition_section}"

        if inactive_names:
            inactive_summary = f"\n\n💤 <i>활동 없음: {_esc(', '.join(inactive_names[:3]))}"
            if len(inactive_names) > 3:
                inactive_summary += f" 외 {len(inactive_names) - 3}개"
            inactive_summary += "</i>"
            body += inactive_summary

        return header + "\n\n" + body + footer
