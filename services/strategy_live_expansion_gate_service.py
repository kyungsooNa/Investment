"""Runtime gate for allowing live strategy expansion.

The profitability evaluator is pure and report-oriented. This adapter turns its
result into a small runtime decision that can be used by schedulers before they
open new real-money positions.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping

from services.strategy_profitability_gate_service import (
    StrategyProfitabilityGateConfig,
    evaluate_strategy_profitability_gate,
)


@dataclass(frozen=True)
class StrategyLiveExpansionDecision:
    allowed: bool
    reason: str
    details: dict[str, Any] = field(default_factory=dict)


class StrategyLiveExpansionGateService:
    """Decide whether a strategy may open new positions in real trading."""

    def __init__(
        self,
        *,
        journal_records_provider: Callable[[], Iterable[Mapping[str, Any]]] | None,
        is_paper_trading_fn: Callable[[], bool],
        profitability_gate_config: StrategyProfitabilityGateConfig | Any | None = None,
        enabled: bool = True,
        logger=None,
    ) -> None:
        self._journal_records_provider = journal_records_provider
        self._is_paper_trading_fn = is_paper_trading_fn
        self._config = _coerce_config(profitability_gate_config)
        self._enabled = enabled
        self._logger = logger

    def check_strategy(self, strategy_name: str) -> StrategyLiveExpansionDecision:
        strategy_name = str(strategy_name or "").strip()
        if not self._enabled:
            return StrategyLiveExpansionDecision(True, "gate_disabled")
        if self._is_paper_trading():
            return StrategyLiveExpansionDecision(True, "paper_mode")
        if self._journal_records_provider is None:
            return StrategyLiveExpansionDecision(False, "profitability_gate_unavailable")

        try:
            records = list(self._journal_records_provider() or [])
            result = evaluate_strategy_profitability_gate(records, self._config)
        except Exception as exc:
            if self._logger:
                self._logger.warning(
                    "[StrategyLiveExpansionGate] profitability gate evaluation failed: %s",
                    exc,
                    exc_info=True,
                )
            return StrategyLiveExpansionDecision(
                False,
                "profitability_gate_error",
                {"error": str(exc)},
            )

        strategy_result = result.get("strategies", {}).get(strategy_name)
        if strategy_result is None:
            return StrategyLiveExpansionDecision(
                False,
                "profitability_gate_missing",
                {"summary": result.get("summary", {})},
            )
        if bool(strategy_result.get("passed")):
            return StrategyLiveExpansionDecision(
                True,
                "profitability_gate_pass",
                {"status": strategy_result.get("status")},
            )
        return StrategyLiveExpansionDecision(
            False,
            f"profitability_gate_{strategy_result.get('status') or 'fail'}",
            {
                "status": strategy_result.get("status"),
                "blocking_reasons": list(strategy_result.get("blocking_reasons") or []),
                "warnings": list(strategy_result.get("warnings") or []),
            },
        )

    def _is_paper_trading(self) -> bool:
        try:
            return bool(self._is_paper_trading_fn())
        except Exception:
            return False


def _coerce_config(config: StrategyProfitabilityGateConfig | Any | None) -> StrategyProfitabilityGateConfig:
    if config is None:
        return StrategyProfitabilityGateConfig()
    if isinstance(config, StrategyProfitabilityGateConfig):
        return config

    values: dict[str, Any] = {}
    for field_name in StrategyProfitabilityGateConfig.__dataclass_fields__:
        if hasattr(config, field_name):
            values[field_name] = getattr(config, field_name)
    return StrategyProfitabilityGateConfig(**values)
