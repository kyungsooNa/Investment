from __future__ import annotations

from services.strategy_live_expansion_gate_service import (
    StrategyLiveExpansionGateService,
    _apply_real_overrides,
)
from services.strategy_profitability_gate_service import StrategyProfitabilityGateConfig


def _sold(strategy: str, pnl: float, ret: float, signal_time: str) -> dict:
    return {
        "status": "SOLD",
        "strategy": strategy,
        "net_pnl": pnl,
        "net_return": ret,
        "signal_time": signal_time,
    }


def test_live_expansion_gate_allows_paper_without_records():
    service = StrategyLiveExpansionGateService(
        journal_records_provider=lambda: [],
        is_paper_trading_fn=lambda: True,
    )

    decision = service.check_strategy("S1")

    assert decision.allowed is True
    assert decision.reason == "paper_mode"


def test_live_expansion_gate_blocks_real_when_strategy_result_missing():
    service = StrategyLiveExpansionGateService(
        journal_records_provider=lambda: [],
        is_paper_trading_fn=lambda: False,
        profitability_gate_config=StrategyProfitabilityGateConfig(min_trades=1),
    )

    decision = service.check_strategy("S1")

    assert decision.allowed is False
    assert decision.reason == "profitability_gate_missing"


def test_live_expansion_gate_allows_real_strategy_that_passes_thresholds():
    service = StrategyLiveExpansionGateService(
        journal_records_provider=lambda: [
            _sold("S1", 100, 1.0, "2026-05-01"),
            _sold("S1", -20, -0.2, "2026-05-02"),
        ],
        is_paper_trading_fn=lambda: False,
        profitability_gate_config=StrategyProfitabilityGateConfig(
            min_trades=2,
            min_profit_factor=1.0,
            min_payoff_ratio=1.0,
            min_win_rate=0.5,
            min_avg_net_return=0.0,
            require_positive_total_net_pnl=True,
            max_mdd_pct=10.0,
            capital_base_won=1_000,
            max_monte_carlo_ruin_probability=None,
            max_monte_carlo_worst_mdd_pct=None,
            require_non_negative_regime_pnl=False,
        ),
    )

    decision = service.check_strategy("S1")

    assert decision.allowed is True
    assert decision.reason == "profitability_gate_pass"


def test_live_expansion_gate_blocks_real_strategy_that_fails_thresholds():
    service = StrategyLiveExpansionGateService(
        journal_records_provider=lambda: [
            _sold("S1", -100, -1.0, "2026-05-01"),
            _sold("S1", -50, -0.5, "2026-05-02"),
        ],
        is_paper_trading_fn=lambda: False,
        profitability_gate_config=StrategyProfitabilityGateConfig(
            min_trades=2,
            min_profit_factor=1.0,
            min_payoff_ratio=1.0,
            min_win_rate=0.5,
            require_positive_total_net_pnl=True,
            max_monte_carlo_ruin_probability=None,
            max_monte_carlo_worst_mdd_pct=None,
        ),
    )

    decision = service.check_strategy("S1")

    assert decision.allowed is False
    assert decision.reason == "profitability_gate_fail"
    assert "total_net_pnl_not_positive" in decision.details["blocking_reasons"]


class _RealOverrides:
    """Pydantic 의존 없이 overlay 객체를 시뮬레이트한다."""

    def __init__(self, **fields):
        for k, v in fields.items():
            setattr(self, k, v)


class _PydConfigLike:
    """real_mode_overrides 속성만 추가로 노출하는 어댑터."""

    def __init__(self, base: StrategyProfitabilityGateConfig, overrides: _RealOverrides):
        for f in StrategyProfitabilityGateConfig.__dataclass_fields__:
            setattr(self, f, getattr(base, f))
        self.real_mode_overrides = overrides


def test_apply_real_overrides_returns_base_when_no_overrides():
    base = StrategyProfitabilityGateConfig(min_trades=30, min_profit_factor=1.2)
    assert _apply_real_overrides(base, None) is base


def test_apply_real_overrides_only_overlays_declared_fields():
    base = StrategyProfitabilityGateConfig(
        min_trades=30,
        min_profit_factor=1.2,
        min_payoff_ratio=1.0,
        min_win_rate=0.35,
        max_mdd_pct=20.0,
        capital_base_won=10_000,
        require_parameter_stability=False,
        require_monte_carlo=False,
        require_regime_balance=False,
    )
    overrides = _RealOverrides(
        min_trades=100,
        min_profit_factor=1.3,
        require_monte_carlo=True,
    )

    effective = _apply_real_overrides(base, overrides)

    assert effective.min_trades == 100
    assert effective.min_profit_factor == 1.3
    assert effective.require_monte_carlo is True
    # 선언되지 않은 필드는 base 값 유지
    assert effective.min_payoff_ratio == 1.0
    assert effective.capital_base_won == 10_000
    assert effective.require_parameter_stability is False
    assert effective.require_regime_balance is False


def test_live_expansion_gate_applies_real_overrides_to_escalate_block():
    """paper 기준에서는 통과하지만 real overlay 가 missing evidence 를 block 으로 승격."""
    base = StrategyProfitabilityGateConfig(
        min_trades=2,
        min_profit_factor=1.0,
        min_payoff_ratio=1.0,
        min_win_rate=0.5,
        require_positive_total_net_pnl=True,
        max_mdd_pct=None,
        capital_base_won=1_000,
        max_monte_carlo_ruin_probability=0.05,
        max_monte_carlo_worst_mdd_pct=30.0,
        require_monte_carlo=False,
        require_non_negative_regime_pnl=False,
        regime_balance_required_buckets=(),
    )
    overrides = _RealOverrides(require_monte_carlo=True)
    config = _PydConfigLike(base, overrides)

    service = StrategyLiveExpansionGateService(
        journal_records_provider=lambda: [
            _sold("S1", 100, 1.0, "2026-05-01"),
            _sold("S1", 50, 0.5, "2026-05-02"),
        ],
        is_paper_trading_fn=lambda: False,
        profitability_gate_config=config,
    )

    decision = service.check_strategy("S1")

    assert decision.allowed is False
    assert decision.reason == "profitability_gate_fail"
    assert "monte_carlo_unavailable" in decision.details["blocking_reasons"]


def test_live_expansion_gate_paper_mode_bypasses_real_overrides():
    """paper 모드는 evaluate 자체를 우회하므로 overlay 가 동작하지 않는다."""
    base = StrategyProfitabilityGateConfig(min_trades=2)
    overrides = _RealOverrides(min_trades=100, require_monte_carlo=True)
    config = _PydConfigLike(base, overrides)

    service = StrategyLiveExpansionGateService(
        journal_records_provider=lambda: [],
        is_paper_trading_fn=lambda: True,
        profitability_gate_config=config,
    )

    decision = service.check_strategy("S1")

    assert decision.allowed is True
    assert decision.reason == "paper_mode"


def test_live_expansion_gate_real_overrides_tighten_min_trades():
    """canary min_trades=100 overlay 가 paper 기본값(30) 보다 엄격하게 동작."""
    base = StrategyProfitabilityGateConfig(
        min_trades=2,
        min_profit_factor=1.0,
        min_payoff_ratio=1.0,
        min_win_rate=0.5,
        require_positive_total_net_pnl=True,
        max_mdd_pct=None,
        capital_base_won=1_000,
        max_monte_carlo_ruin_probability=None,
        max_monte_carlo_worst_mdd_pct=None,
        require_non_negative_regime_pnl=False,
        regime_balance_required_buckets=(),
    )
    overrides = _RealOverrides(min_trades=100)
    config = _PydConfigLike(base, overrides)

    service = StrategyLiveExpansionGateService(
        journal_records_provider=lambda: [
            _sold("S1", 100, 1.0, "2026-05-01"),
            _sold("S1", 50, 0.5, "2026-05-02"),
        ],
        is_paper_trading_fn=lambda: False,
        profitability_gate_config=config,
    )

    decision = service.check_strategy("S1")

    assert decision.allowed is False
    assert decision.reason == "profitability_gate_insufficient_sample"
    assert "insufficient_trades" in decision.details["blocking_reasons"]
