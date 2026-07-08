from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


@dataclass(frozen=True)
class MicrostructureQualityThresholds:
    min_intraday_coverage_pct: float = 80.0
    min_program_overlay_coverage_pct: float = 80.0
    min_program_db_coverage_pct: float = 50.0
    # 무틱 ~55%(P2 2-4) + PRICE 미구독 후보를 감안한 보수 임계 — 배선 전손 감지용
    min_execution_strength_db_coverage_pct: float = 30.0
    max_stale_rows: int = 0


def summarize_capture_quality(
    payload: dict[str, Any],
    *,
    fallback_codes: Optional[list[str]] = None,
    thresholds: Optional[MicrostructureQualityThresholds] = None,
) -> dict[str, Any]:
    thresholds = thresholds or MicrostructureQualityThresholds()
    metadata = payload.get("metadata") or {}
    codes = [
        str(code)
        for code in (metadata.get("codes") or fallback_codes or [])
    ]
    code_set = set(codes)
    code_count = len(codes)
    intraday = payload.get("intraday_minutes") or {}
    execution_strength = payload.get("execution_strength") or {}
    program_trades = payload.get("program_trades") or {}
    quality = metadata.get("quality") or {}
    program_fallback_codes = [
        str(code) for code in (metadata.get("program_fallback_codes") or [])
        if str(code) in code_set
    ]
    stale_by_code = quality.get("stale_minute_rows_dropped") or {}
    stale_rows = sum(_to_int(value) for value in stale_by_code.values())
    stale_by_code = {
        str(code): _to_int(value)
        for code, value in stale_by_code.items()
        if _to_int(value) > 0
    }

    intraday_available = sum(1 for code in codes if bool(intraday.get(code)))
    execution_strength_available = sum(
        1 for code in codes if execution_strength.get(code) is not None
    )
    program_overlay_available = sum(
        1 for code in codes if program_trades.get(code) is not None
    )
    program_source = str(metadata.get("program_source") or "")
    program_db_available: Optional[int] = None
    program_db_coverage_pct: Optional[float] = None
    if program_source == "program_db":
        program_db_available = max(0, program_overlay_available - len(program_fallback_codes))
        program_db_coverage_pct = coverage_pct(program_db_available, code_count)

    execution_strength_source = str(metadata.get("execution_strength_source") or "")
    execution_strength_intraday = payload.get("execution_strength_intraday") or {}
    execution_strength_db_available: Optional[int] = None
    execution_strength_db_coverage_pct: Optional[float] = None
    if execution_strength_source == "es_db":
        execution_strength_db_available = sum(
            1 for code in codes if execution_strength_intraday.get(code)
        )
        execution_strength_db_coverage_pct = coverage_pct(
            execution_strength_db_available, code_count
        )

    intraday_coverage_pct = coverage_pct(intraday_available, code_count)
    execution_strength_coverage_pct = coverage_pct(
        execution_strength_available, code_count
    )
    program_overlay_coverage_pct = coverage_pct(program_overlay_available, code_count)

    issues: list[str] = []
    if code_count == 0:
        issues.append("no_codes")
    if intraday_coverage_pct < thresholds.min_intraday_coverage_pct:
        issues.append("intraday_coverage_below_threshold")
    if program_overlay_coverage_pct < thresholds.min_program_overlay_coverage_pct:
        issues.append("program_overlay_coverage_below_threshold")
    if (
        program_db_coverage_pct is not None
        and program_db_coverage_pct < thresholds.min_program_db_coverage_pct
    ):
        issues.append("program_db_coverage_below_threshold")
    if (
        execution_strength_db_coverage_pct is not None
        and execution_strength_db_coverage_pct
        < thresholds.min_execution_strength_db_coverage_pct
    ):
        issues.append("execution_strength_db_coverage_below_threshold")
    return {
        "trade_date": str(metadata.get("trade_date") or ""),
        "codes": code_count,
        "intraday_available": intraday_available,
        "intraday_coverage_pct": intraday_coverage_pct,
        "execution_strength_available": execution_strength_available,
        "execution_strength_coverage_pct": execution_strength_coverage_pct,
        "program_overlay_available": program_overlay_available,
        "program_overlay_coverage_pct": program_overlay_coverage_pct,
        "program_source": program_source,
        "program_fallback_codes": program_fallback_codes,
        "program_fallback_pct": coverage_pct(len(program_fallback_codes), code_count),
        "program_db_available": program_db_available,
        "program_db_coverage_pct": program_db_coverage_pct,
        "execution_strength_source": execution_strength_source,
        "execution_strength_db_available": execution_strength_db_available,
        "execution_strength_db_coverage_pct": execution_strength_db_coverage_pct,
        "empty_minute_codes": quality.get("empty_minute_codes") or [],
        "stale_minute_rows_dropped": stale_rows,
        "stale_minute_rows_dropped_by_code": stale_by_code,
        "issues": issues,
        "quality_gate_passed": not issues,
    }


def coverage_pct(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return numerator / denominator * 100.0


def format_optional_pct(value: Any) -> str:
    if isinstance(value, (int, float)):
        return f"{value:.1f}%"
    return "-"


def _to_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
