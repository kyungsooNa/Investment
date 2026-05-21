from __future__ import annotations

from services.strategy_live_expansion_gate_service import StrategyLiveExpansionGateService
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
