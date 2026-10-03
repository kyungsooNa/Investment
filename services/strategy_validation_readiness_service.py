"""전략 실전 확장 판단에 필요한 표본·설정·캡처 준비도를 한 번에 집계한다."""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping

from services.strategy_profitability_gate_service import (
    StrategyProfitabilityGateConfig,
    evaluate_strategy_profitability_gate,
    sold_sample_exclusion,
)


_QUALITY_FILE_PATTERN = re.compile(r"replay_quality_(\d{8})\.json$")
_MISSING_CONFIG_HASH = "<missing>"


def build_strategy_validation_readiness(
    records: Iterable[Mapping[str, Any]],
    config: StrategyProfitabilityGateConfig | None = None,
    *,
    replay_dir: str | Path = "data/backtest_microstructure",
) -> dict[str, Any]:
    """표준 journal과 replay 품질 파일을 운영자용 준비도 응답으로 변환한다."""
    cfg = config or StrategyProfitabilityGateConfig()
    journal = [dict(record) for record in records]
    gate = evaluate_strategy_profitability_gate(journal, cfg)
    gate_by_strategy = gate.get("strategies", {})

    strategy_names = sorted(
        {
            str(record.get("strategy") or "").strip()
            for record in journal
            if str(record.get("strategy") or "").strip()
        }
    )
    strategies = [
        _strategy_readiness(strategy, journal, gate_by_strategy.get(strategy), cfg)
        for strategy in strategy_names
    ]
    capture = _capture_readiness(Path(replay_dir))
    statuses = [item["status"] for item in strategies]

    return {
        "summary": {
            "strategy_count": len(strategies),
            "pass_count": statuses.count("pass"),
            "fail_count": statuses.count("fail"),
            "insufficient_sample_count": statuses.count("insufficient_sample"),
            **capture,
        },
        "warnings": list(gate.get("warnings") or []),
        "strategies": strategies,
    }


def _strategy_readiness(
    strategy: str,
    journal: list[Mapping[str, Any]],
    gate_result: Mapping[str, Any] | None,
    config: StrategyProfitabilityGateConfig,
) -> dict[str, Any]:
    rows = [record for record in journal if str(record.get("strategy") or "").strip() == strategy]
    sold_rows = [record for record in rows if str(record.get("status") or "").upper() == "SOLD"]
    excluded_sold = {"force_closed": 0, "data_quality_flag": 0}
    for record in sold_rows:
        exclusion = sold_sample_exclusion(record)
        if exclusion is not None:
            excluded_sold[exclusion] += 1
    sold_count = len(sold_rows) - sum(excluded_sold.values())
    min_trades = max(int(config.min_trades or 0), 0)

    cohort_counts: Counter[str] = Counter()
    cohort_sold_counts: Counter[str] = Counter()
    for record in rows:
        config_hash = str(record.get("config_hash") or "").strip() or _MISSING_CONFIG_HASH
        cohort_counts[config_hash] += 1
        if str(record.get("status") or "").upper() == "SOLD":
            cohort_sold_counts[config_hash] += 1

    cohorts = [
        {
            "config_hash": config_hash,
            "record_count": cohort_counts[config_hash],
            "sold_count": cohort_sold_counts[config_hash],
        }
        for config_hash in sorted(cohort_counts)
    ]
    decision = dict(gate_result or {})
    status = str(decision.get("status") or "insufficient_sample")
    blocking_reasons = list(decision.get("blocking_reasons") or [])
    if not gate_result and "insufficient_trades" not in blocking_reasons:
        blocking_reasons.append("insufficient_trades")

    progress_pct = 100.0 if min_trades == 0 else min(sold_count / min_trades * 100.0, 100.0)
    return {
        "strategy": strategy,
        "status": status,
        "record_count": len(rows),
        "sold_trades": sold_count,
        "excluded_sold": excluded_sold,
        "min_trades": min_trades,
        "progress_pct": round(progress_pct, 1),
        "blocking_reasons": blocking_reasons,
        "warnings": list(decision.get("warnings") or []),
        "metrics": dict(decision.get("metrics") or {}),
        "config_cohorts": cohorts,
        "mixed_config": len(cohorts) > 1,
    }


def _capture_readiness(replay_dir: Path) -> dict[str, Any]:
    valid_dates: list[str] = []
    all_dates: list[str] = []
    error_count = 0
    if replay_dir.is_dir():
        for path in replay_dir.glob("replay_quality_*.json"):
            match = _QUALITY_FILE_PATTERN.match(path.name)
            if not match:
                continue
            date = match.group(1)
            all_dates.append(date)
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                error_count += 1
                continue
            if payload.get("valid_for_backtest") is True:
                valid_dates.append(date)

    return {
        "valid_capture_days": len(set(valid_dates)),
        "latest_capture_date": max(all_dates, default=None),
        "latest_valid_capture_date": max(valid_dates, default=None),
        "quality_file_error_count": error_count,
    }
