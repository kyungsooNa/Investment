"""Runtime gate for allowing live strategy expansion.

The profitability evaluator is pure and report-oriented. This adapter turns its
result into a small runtime decision that can be used by schedulers before they
open new real-money positions.
"""
from __future__ import annotations

from dataclasses import dataclass, field, fields, replace
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
        base_config = _coerce_config(profitability_gate_config)
        real_overrides = getattr(profitability_gate_config, "real_mode_overrides", None)
        # real 모드는 paper 모드 early-return 이후에만 도달하므로 effective config 를
        # real overlay 적용본으로 한 번만 계산해 둔다.
        self._config = _apply_real_overrides(base_config, real_overrides)
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


def _apply_real_overrides(
    base: StrategyProfitabilityGateConfig,
    overrides: Any | None,
) -> StrategyProfitabilityGateConfig:
    """real 모드 전용 overlay 를 base config 위에 덮어쓴다.

    overrides 가 None 이면 base 를 그대로 반환한다. paper 모드는 check_strategy 의
    early-return 으로 처리되므로 호출자가 모드 분기를 신경쓰지 않아도 된다.
    """
    if overrides is None:
        return base
    overlay: dict[str, Any] = {}
    for f in fields(StrategyProfitabilityGateConfig):
        if hasattr(overrides, f.name):
            overlay[f.name] = getattr(overrides, f.name)
    if not overlay:
        return base
    return replace(base, **overlay)
