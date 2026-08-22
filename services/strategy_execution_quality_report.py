"""체결 품질 · 후보 유동성 리포트 섹션 빌더.

`StrategyLogReportService` 에서 분리했다 — 두 섹션과 그 헬퍼(최신 레코드 선별,
기간 라벨, 임계 판정)는 서로만 참조하고 리포트의 나머지 부분과 상태를 공유하지
않는다. 비활성화 후보 목록(`get_last_candidates`)만 리포트 본문 바깥으로 나가며,
서비스가 이를 그대로 노출한다(`get_last_execution_quality_candidates`).
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any, Dict, List, Mapping, Optional, Tuple

from services.strategy_report_format import (
    MAX_CANDIDATE_LIQUIDITY_ROWS,
    MAX_EXECUTION_QUALITY_ROWS,
    _esc,
    _first_number,
    _format_eok_won,
)


class ExecutionQualityReportBuilder:
    def __init__(self, execution_quality_config) -> None:
        self._execution_quality_config = execution_quality_config
        self._last_candidates: List[dict] = []

    def get_last_candidates(self) -> List[dict]:
        """직전 리포트에서 비활성화 후보로 분류된 전략 목록."""
        return list(self._last_candidates)

    def reset_candidates(self) -> None:
        self._last_candidates = []

    def build_execution_quality_section(self, records: List[dict]) -> Optional[str]:
        if not records:
            self._last_candidates = []
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
        self._last_candidates = []
        for row in strategy_rows:
            row["quality_label"] = self._execution_quality_label(row)
            if "비활성화" in row["quality_label"]:
                candidate_rows.append(row)
                self._last_candidates.append({
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

        for row in strategy_rows[:MAX_EXECUTION_QUALITY_ROWS]:
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

    def build_candidate_liquidity_section(self, records: List[dict]) -> Optional[str]:
        """후보군 유동성/capacity 관찰 섹션.

        전략 로그가 후보별 `avg_trading_value_5d`와 주문금액을 제공할 때만 노출한다.
        값이 없는 기존 전략 로그는 조용히 생략해 리포트 노이즈를 만들지 않는다.
        """
        records = self._latest_candidate_liquidity_records(records)
        if not records:
            return None

        by_strategy: Dict[str, List[dict]] = {}
        for record in records:
            by_strategy.setdefault(record["strategy"], []).append(record)

        rows = []
        for strategy, items in by_strategy.items():
            avg_tv_values = [
                item["avg_trading_value_5d"]
                for item in items
                if item.get("avg_trading_value_5d") is not None
            ]
            participation_values = [
                item["order_to_avg_trading_value_pct"]
                for item in items
                if item.get("order_to_avg_trading_value_pct") is not None
            ]
            if not avg_tv_values:
                continue
            rows.append({
                "strategy": strategy,
                "count": len(avg_tv_values),
                "avg_tv": sum(avg_tv_values) / len(avg_tv_values),
                "p25_tv": self._percentile(avg_tv_values, 25),
                "min_tv": min(avg_tv_values),
                "avg_participation": (
                    sum(participation_values) / len(participation_values)
                    if participation_values else None
                ),
                "max_participation": max(participation_values) if participation_values else None,
            })

        if not rows:
            return None
        rows.sort(key=lambda row: (row["avg_tv"], row["strategy"]))

        lines = ["<b>📏 후보 유동성/capacity 관찰</b>"]
        for row in rows[:MAX_CANDIDATE_LIQUIDITY_ROWS]:
            participation = ""
            if row["avg_participation"] is not None and row["max_participation"] is not None:
                participation = (
                    f", 주문/5일대금 평균 {row['avg_participation']:.2f}%, "
                    f"최대 {row['max_participation']:.2f}%"
                )
            lines.append(
                f"• {_esc(row['strategy'])}: {row['count']}종목, "
                f"5일평균대금 평균 {_format_eok_won(row['avg_tv'])}, "
                f"P25 {_format_eok_won(row['p25_tv'])}, "
                f"최소 {_format_eok_won(row['min_tv'])}{participation}"
            )
        return "\n".join(lines)

    @staticmethod
    def _latest_candidate_liquidity_records(records: List[dict]) -> List[dict]:
        latest: Dict[Tuple[str, str], dict] = {}
        anonymous: List[dict] = []
        for idx, record in enumerate(records):
            code = str(record.get("code") or "").strip()
            if not code:
                anonymous.append({**record, "_idx": idx})
                continue
            key = (str(record.get("strategy") or ""), code)
            prev = latest.get(key)
            if prev is None or str(record.get("timestamp", "")) >= str(prev.get("timestamp", "")):
                latest[key] = record
        return [*latest.values(), *anonymous]

    def extract_candidate_liquidity_records(
        self,
        strategy: str,
        timestamp: str,
        data: Mapping[str, Any],
        name_map: Mapping[str, str],
    ) -> List[dict]:
        payloads: List[Mapping[str, Any]] = []
        candidates = data.get("candidates")
        if isinstance(candidates, list):
            payloads.extend(item for item in candidates if isinstance(item, Mapping))
        else:
            payloads.append(data)

        records: List[dict] = []
        for payload in payloads:
            merged = self._merge_candidate_payload(payload)
            avg_tv = _first_number(
                merged,
                "avg_trading_value_5d",
                "avg_5d_tv",
                "trading_value_5d",
                "avg_trading_value",
            )
            if avg_tv is None or avg_tv <= 0:
                continue
            code = str(merged.get("code") or data.get("code") or "").strip()
            order_amount = self._candidate_order_amount_won(merged)
            participation = (
                order_amount / avg_tv * 100
                if order_amount is not None and order_amount > 0
                else None
            )
            records.append({
                "timestamp": timestamp,
                "strategy": strategy,
                "code": code,
                "name": name_map.get(code) or merged.get("name") or data.get("name") or code,
                "avg_trading_value_5d": avg_tv,
                "order_amount_won": order_amount,
                "order_to_avg_trading_value_pct": participation,
            })
        return records

    @staticmethod
    def _merge_candidate_payload(payload: Mapping[str, Any]) -> dict:
        merged = dict(payload)
        for nested_key in ("metrics", "watchlist_item"):
            nested = payload.get(nested_key)
            if isinstance(nested, Mapping):
                merged.update({k: v for k, v in nested.items() if k not in merged})
        return merged

    @staticmethod
    def _candidate_order_amount_won(payload: Mapping[str, Any]) -> Optional[float]:
        amount = _first_number(
            dict(payload),
            "planned_order_amount_won",
            "order_amount_won",
            "max_order_amount_won",
            "signal_amount_won",
            "amount_won",
        )
        if amount is not None:
            return amount
        qty = _first_number(dict(payload), "qty", "order_qty", "planned_qty")
        price = _first_number(dict(payload), "price", "current_price", "entry_price")
        if qty is None or price is None or qty <= 0 or price <= 0:
            return None
        return qty * price

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
