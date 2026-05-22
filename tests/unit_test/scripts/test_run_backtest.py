from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from config.config_loader import OrderPolicyConfig, PositionSizingConfig, RiskGateConfig
from core.account_snapshot import AccountSnapshot
from scripts.run_backtest import (
    ACTIVE_BACKTEST_STRATEGIES,
    _BacktestLedgerAccountSnapshotCache,
    _BacktestStrategyRiskProvider,
    _build_replay_bar_providers,
    _build_dates,
    _build_risk_sizing_services,
    _build_backtest_strategy,
    _format_console,
    _format_walk_forward_console,
    _format_walk_forward_json,
    _get_program_provider,
    _parse_args,
    _run_profitability_gate_for_result,
    _run_profitability_gate_for_walk_forward,
    _run_monte_carlo_for_result,
    _run_monte_carlo_for_walk_forward,
)
from services.backtest_execution_simulator import BacktestPortfolioLedger, PortfolioPosition
from services.backtest_replay_adapter import (
    StockQueryDailyMtmBarProvider,
    StockQueryIntradayReplayBarProvider,
)
from services.position_sizing_service import PositionSizingService
from services.risk_gate_service import RiskGateService


def test_build_dates_accepts_comma_separated_dates():
    args = SimpleNamespace(dates="20260501,20260503", start_date=None, end_date=None)

    assert _build_dates(args) == ["20260501", "20260503"]


def test_build_dates_accepts_inclusive_start_end_range():
    args = SimpleNamespace(dates=None, start_date="20260501", end_date="20260503")

    assert _build_dates(args) == ["20260501", "20260502", "20260503"]


def test_parse_args_accepts_use_risk_sizing(monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        ["run_backtest", "--dates", "20260501", "--use-risk-sizing"],
    )

    args = _parse_args()

    assert args.use_risk_sizing is True


def test_parse_args_accepts_walk_forward_options(monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        [
            "run_backtest",
            "--dates",
            "20260501,20260502,20260503",
            "--walk-forward",
            "--wf-train-days",
            "20",
            "--wf-tune-days",
            "5",
            "--wf-test-days",
            "3",
            "--wf-step-days",
            "2",
            "--wf-embargo-days",
            "1",
        ],
    )

    args = _parse_args()

    assert args.walk_forward is True
    assert args.wf_train_days == 20
    assert args.wf_tune_days == 5
    assert args.wf_test_days == 3
    assert args.wf_step_days == 2
    assert args.wf_embargo_days == 1


def test_parse_args_accepts_monte_carlo_options(monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        [
            "run_backtest",
            "--dates",
            "20260501",
            "--monte-carlo",
            "--mc-runs",
            "50",
            "--mc-seed",
            "123",
            "--mc-ruin-drawdown-pct",
            "15",
        ],
    )

    args = _parse_args()

    assert args.monte_carlo is True
    assert args.mc_runs == 50
    assert args.mc_seed == 123
    assert args.mc_ruin_drawdown_pct == 15.0


def test_parse_args_accepts_profitability_gate(monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        [
            "run_backtest",
            "--dates",
            "20260501",
            "--profitability-gate",
        ],
    )

    args = _parse_args()

    assert args.profitability_gate is True


def test_parse_args_accepts_execution_bar_policy(monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        [
            "run_backtest",
            "--dates",
            "20260501",
            "--execution-bar-policy",
            "next_bar",
        ],
    )

    args = _parse_args()

    assert args.execution_bar_policy == "next_bar"


def test_parse_args_accepts_backtest_time(monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        [
            "run_backtest",
            "--dates",
            "20260501",
            "--backtest-time",
            "09:30:00",
        ],
    )

    args = _parse_args()

    assert args.backtest_time == "09:30:00"


@pytest.mark.parametrize("strategy_key", ACTIVE_BACKTEST_STRATEGIES)
def test_parse_args_accepts_active_backtest_strategies(monkeypatch, strategy_key):
    monkeypatch.setattr(
        "sys.argv",
        [
            "run_backtest",
            "--strategy",
            strategy_key,
            "--dates",
            "20260501",
        ],
    )

    args = _parse_args()

    assert args.strategy == strategy_key


@pytest.mark.parametrize("strategy_key", ACTIVE_BACKTEST_STRATEGIES)
def test_build_backtest_strategy_injects_replay_context_for_active_strategies(
    tmp_path,
    strategy_key,
):
    replay_sqs = MagicMock(name="replay_sqs")
    universe_service = MagicMock(name="universe_service")
    indicator_service = MagicMock(name="indicator_service")
    backtest_clock = MagicMock(name="backtest_clock")

    strategy = _build_backtest_strategy(
        strategy_key=strategy_key,
        replay_sqs=replay_sqs,
        universe_service=universe_service,
        indicator_service=indicator_service,
        backtest_clock=backtest_clock,
        state_dir=str(tmp_path),
        logger=logging.getLogger("test.backtest_strategy_factory"),
    )

    assert strategy._sqs is replay_sqs
    assert strategy._tm is backtest_clock
    assert getattr(strategy, "_universe", universe_service) is universe_service
    if hasattr(strategy, "_indicator"):
        assert strategy._indicator is indicator_service
    if hasattr(strategy, "STATE_FILE"):
        assert str(tmp_path) in strategy.STATE_FILE
        assert strategy_key in strategy.STATE_FILE


def test_build_backtest_strategy_requires_indicator_service_for_indicator_strategies(
    tmp_path,
):
    with pytest.raises(ValueError, match="indicator_service"):
        _build_backtest_strategy(
            strategy_key="rsi2_pullback",
            replay_sqs=MagicMock(),
            universe_service=MagicMock(),
            indicator_service=None,
            backtest_clock=MagicMock(),
            state_dir=str(tmp_path),
        )


def test_format_console_summarizes_execution_and_portfolio():
    result = SimpleNamespace(
        strategy_name="오닐PP/BGU",
        dates=["20260501", "20260502"],
        execution_reports=[
            SimpleNamespace(order=SimpleNamespace(side=SimpleNamespace(value="BUY")), filled_qty=2),
            SimpleNamespace(order=SimpleNamespace(side=SimpleNamespace(value="SELL")), filled_qty=1),
        ],
        journal_records=[{"status": "REJECTED"}],
        saved_journal_run={"run_id": "period_20260501_20260502"},
        portfolio={
            "cash": 1_100_000,
            "available_cash": 1_090_000,
            "realized_net_pnl": 90_000,
            "positions": {"005930": {"qty": 1}},
        },
        execution_bar_policy="current_bar",
    )

    text = _format_console(result)

    assert "오닐PP/BGU" in text
    assert "BUY 체결: 1" in text
    assert "SELL 체결: 1" in text
    assert "거부 기록: 1" in text
    assert "실현손익(순): 90,000" in text
    assert "체결 봉 정책: current_bar" in text
    assert "journal run: period_20260501_20260502" in text


def test_format_console_includes_monte_carlo_summary_when_present():
    result = SimpleNamespace(
        strategy_name="오닐PP/BGU",
        dates=["20260501"],
        execution_reports=[],
        journal_records=[],
        saved_journal_run={},
        portfolio={"cash": 1_000_000, "available_cash": 1_000_000, "positions": {}},
        monte_carlo={
            "trade_count": 3,
            "runs": 100,
            "worst_max_drawdown_pct": 12.345,
            "worst_losing_streak": 2,
            "ruin_probability": 0.25,
        },
    )

    text = _format_console(result)

    assert "Monte Carlo 거래 수: 3" in text
    assert "Monte Carlo runs: 100" in text
    assert "최악 MDD: 12.35%" in text
    assert "ruin probability: 25.00%" in text


def test_format_console_includes_profitability_gate_when_present():
    result = SimpleNamespace(
        strategy_name="오닐PP/BGU",
        dates=["20260501"],
        execution_reports=[],
        journal_records=[],
        saved_journal_run={},
        portfolio={"cash": 1_000_000, "available_cash": 1_000_000, "positions": {}},
        profitability_gate={
            "summary": {"pass_count": 1, "fail_count": 0, "insufficient_sample_count": 0},
            "strategies": {
                "오닐PP/BGU": {
                    "status": "pass",
                    "blocking_reasons": [],
                    "warnings": [],
                }
            },
        },
    )

    text = _format_console(result)

    assert "[PROFITABILITY GATE]" in text
    assert "오닐PP/BGU: pass" in text


def test_format_console_includes_profitability_gate_warnings():
    result = SimpleNamespace(
        strategy_name="오닐PP/BGU",
        dates=["20260501"],
        execution_reports=[],
        journal_records=[],
        saved_journal_run={},
        portfolio={"cash": 1_000_000, "available_cash": 1_000_000, "positions": {}},
        profitability_gate={
            "summary": {"pass_count": 1, "fail_count": 0, "insufficient_sample_count": 0},
            "strategies": {
                "오닐PP/BGU": {
                    "status": "pass",
                    "blocking_reasons": [],
                    "warnings": ["regime_balance_incomplete"],
                }
            },
        },
    )

    text = _format_console(result)

    assert "오닐PP/BGU: pass [warn: regime_balance_incomplete]" in text


def test_format_console_includes_profitability_gate_top_level_warnings():
    result = SimpleNamespace(
        strategy_name="오닐PP/BGU",
        dates=["20260501"],
        execution_reports=[],
        journal_records=[],
        saved_journal_run={},
        portfolio={"cash": 1_000_000, "available_cash": 1_000_000, "positions": {}},
        profitability_gate={
            "summary": {"pass_count": 2, "fail_count": 0, "insufficient_sample_count": 0},
            "warnings": ["multiple_testing_bias_warning"],
            "multiple_testing_bias": {
                "bias_warning": True,
                "trial_count": 5,
                "best_strategy": "S1",
                "top_to_median_ratio": 12.5,
            },
            "strategies": {
                "S1": {"status": "pass", "blocking_reasons": [], "warnings": []},
                "S2": {"status": "pass", "blocking_reasons": [], "warnings": []},
            },
        },
    )

    text = _format_console(result)

    assert "warnings: multiple_testing_bias_warning" in text
    assert "multiple-testing trials=5 best=S1 top/median=12.50" in text


def test_format_walk_forward_console_summarizes_test_windows():
    result = SimpleNamespace(
        summary={
            "segment_count": 2,
            "embargo_days": 1,
            "train_days": 40,
            "tune_days": 10,
            "test_days": 6,
            "test_realized_net_pnl": 120_000,
            "test_execution_count": 7,
            "test_rejected_count": 3,
        }
    )

    text = _format_walk_forward_console(result)

    assert "[WALK-FORWARD BACKTEST RESULT]" in text
    assert "구간 수: 2" in text
    assert "embargo 일수: 1" in text
    assert "검증 실현손익(순): 120,000" in text
    assert "검증 체결 수: 7" in text
    assert "검증 거부 기록: 3" in text


def test_format_walk_forward_console_includes_monte_carlo_summary_when_present():
    result = SimpleNamespace(
        summary={
            "segment_count": 1,
            "train_days": 20,
            "tune_days": 5,
            "test_days": 5,
            "test_realized_net_pnl": 0,
            "test_execution_count": 0,
            "test_rejected_count": 0,
        },
        monte_carlo={
            "trade_count": 2,
            "runs": 10,
            "worst_max_drawdown_pct": 3.21,
            "worst_losing_streak": 1,
            "ruin_probability": 0.1,
        },
    )

    text = _format_walk_forward_console(result)

    assert "Monte Carlo 거래 수: 2" in text
    assert "ruin probability: 10.00%" in text


def test_format_walk_forward_console_includes_profitability_gate_when_present():
    result = SimpleNamespace(
        summary={
            "segment_count": 1,
            "train_days": 20,
            "tune_days": 5,
            "test_days": 5,
            "test_realized_net_pnl": 0,
            "test_execution_count": 0,
            "test_rejected_count": 0,
        },
        profitability_gate={
            "summary": {"pass_count": 0, "fail_count": 1, "insufficient_sample_count": 0},
            "strategies": {
                "오닐PP/BGU": {
                    "status": "fail",
                    "blocking_reasons": ["profit_factor_below"],
                    "warnings": [],
                }
            },
        },
    )

    text = _format_walk_forward_console(result)

    assert "[PROFITABILITY GATE]" in text
    assert "오닐PP/BGU: fail" in text
    assert "profit_factor_below" in text


def test_format_walk_forward_json_includes_summary_and_segment_phase_runs():
    phase_result = SimpleNamespace(
        strategy_name="오닐PP/BGU",
        dates=["20260501"],
        execution_reports=[],
        journal_records=[],
        portfolio={"realized_net_pnl": 1_000},
        saved_journal_run={"run_id": "wf_0_test"},
    )
    result = SimpleNamespace(
        summary={"segment_count": 1},
        segments=[
            SimpleNamespace(
                index=0,
                train_dates=["20260501"],
                tune_dates=["20260502"],
                test_dates=["20260503"],
                train_result=phase_result,
                tune_result=phase_result,
                test_result=phase_result,
            )
        ],
    )

    text = _format_walk_forward_json(result)

    assert '"summary": {' in text
    assert '"segment_count": 1' in text
    assert '"run_id": "wf_0_test"' in text


def test_run_monte_carlo_for_result_uses_journal_net_pnl():
    result = SimpleNamespace(
        journal_records=[
            {"status": "SOLD", "net_pnl": 100},
            {"status": "SOLD", "net_pnl": -50},
        ],
        monte_carlo=None,
    )
    args = SimpleNamespace(
        mc_runs=5,
        mc_seed=1,
        initial_cash=1_000,
        mc_ruin_drawdown_pct=10.0,
    )

    _run_monte_carlo_for_result(result, args)

    assert result.monte_carlo["trade_count"] == 2
    assert result.monte_carlo["runs"] == 5


def test_run_monte_carlo_for_walk_forward_uses_test_phase_journals_only():
    result = SimpleNamespace(
        segments=[
            SimpleNamespace(
                train_result=SimpleNamespace(journal_records=[{"status": "SOLD", "net_pnl": 999}]),
                tune_result=SimpleNamespace(journal_records=[{"status": "SOLD", "net_pnl": 999}]),
                test_result=SimpleNamespace(journal_records=[{"status": "SOLD", "net_pnl": -10}]),
            ),
            SimpleNamespace(
                train_result=SimpleNamespace(journal_records=[]),
                tune_result=SimpleNamespace(journal_records=[]),
                test_result=SimpleNamespace(journal_records=[{"status": "SOLD", "net_pnl": 20}]),
            ),
        ],
        monte_carlo=None,
    )
    args = SimpleNamespace(
        mc_runs=5,
        mc_seed=1,
        initial_cash=1_000,
        mc_ruin_drawdown_pct=10.0,
    )

    _run_monte_carlo_for_walk_forward(result, args)

    assert result.monte_carlo["trade_count"] == 2
    assert result.monte_carlo["runs"] == 5


def test_run_profitability_gate_for_result_uses_result_journal_records():
    result = SimpleNamespace(
        journal_records=[
            {"status": "SOLD", "strategy": "S1", "net_pnl": 100, "net_return": 1.0},
            {"status": "SOLD", "strategy": "S1", "net_pnl": -10, "net_return": -0.1},
        ],
        monte_carlo={"ruin_probability": 0.0, "worst_max_drawdown_pct": 1.0},
        profitability_gate=None,
    )
    config = SimpleNamespace(
        strategy_profitability_gate=SimpleNamespace(
            min_trades=2,
            min_profit_factor=1.0,
            min_payoff_ratio=1.0,
            min_win_rate=0.5,
            min_avg_net_return=0.0,
            max_mdd_pct=10.0,
            capital_base_won=1_000,
            max_monte_carlo_ruin_probability=0.1,
            max_monte_carlo_worst_mdd_pct=10.0,
        )
    )

    _run_profitability_gate_for_result(result, config, initial_cash=1_000)

    assert result.profitability_gate["strategies"]["S1"]["status"] == "pass"


def test_run_profitability_gate_for_result_uses_attached_parameter_stability():
    result = SimpleNamespace(
        journal_records=[
            {"status": "SOLD", "strategy": "S1", "net_pnl": 100, "net_return": 1.0},
            {"status": "SOLD", "strategy": "S1", "net_pnl": -10, "net_return": -0.1},
        ],
        monte_carlo=None,
        profitability_gate=None,
        parameter_stability={
            "summary": {
                "dimensions": {
                    "pp_ma_proximity_upper_pct": {"stability": {"flag": "cliff"}}
                }
            }
        },
    )
    config = SimpleNamespace(
        strategy_profitability_gate=SimpleNamespace(
            min_trades=2,
            min_profit_factor=1.0,
            min_payoff_ratio=1.0,
            min_win_rate=0.5,
            min_avg_net_return=0.0,
            max_mdd_pct=10.0,
            capital_base_won=1_000,
            max_monte_carlo_ruin_probability=None,
            max_monte_carlo_worst_mdd_pct=None,
            require_non_negative_regime_pnl=False,
            block_parameter_stability_flags=["spike", "cliff"],
        )
    )

    _run_profitability_gate_for_result(result, config, initial_cash=1_000)

    s1 = result.profitability_gate["strategies"]["S1"]
    assert s1["status"] == "fail"
    assert "parameter_stability_cliff:pp_ma_proximity_upper_pct" in s1["blocking_reasons"]


def test_run_profitability_gate_for_walk_forward_uses_test_phase_journals_only():
    result = SimpleNamespace(
        segments=[
            SimpleNamespace(
                train_result=SimpleNamespace(
                    journal_records=[{"status": "SOLD", "strategy": "S1", "net_pnl": -999, "net_return": -9.0}]
                ),
                tune_result=SimpleNamespace(journal_records=[]),
                test_result=SimpleNamespace(
                    journal_records=[{"status": "SOLD", "strategy": "S1", "net_pnl": 100, "net_return": 1.0}]
                ),
            ),
            SimpleNamespace(
                train_result=SimpleNamespace(journal_records=[]),
                tune_result=SimpleNamespace(journal_records=[]),
                test_result=SimpleNamespace(
                    journal_records=[{"status": "SOLD", "strategy": "S1", "net_pnl": -10, "net_return": -0.1}]
                ),
            ),
        ],
        monte_carlo={"ruin_probability": 0.0, "worst_max_drawdown_pct": 1.0},
        profitability_gate=None,
    )
    config = SimpleNamespace(
        strategy_profitability_gate=SimpleNamespace(
            min_trades=2,
            min_profit_factor=1.0,
            min_payoff_ratio=1.0,
            min_win_rate=0.5,
            min_avg_net_return=0.0,
            max_mdd_pct=10.0,
            capital_base_won=1_000,
        )
    )

    _run_profitability_gate_for_walk_forward(result, config, initial_cash=1_000)

    assert result.profitability_gate["strategies"]["S1"]["status"] == "pass"


def test_get_program_provider_uses_market_data_broker_when_available():
    broker = object()
    sqs = SimpleNamespace(market_data_service=SimpleNamespace(_broker_api_wrapper=broker))

    assert _get_program_provider(sqs) is broker


def test_build_replay_bar_providers_wires_intraday_and_daily_mtm_providers():
    replay_sqs = MagicMock(name="replay_sqs")

    bar_provider, mtm_bar_provider = _build_replay_bar_providers(replay_sqs)

    assert isinstance(bar_provider, StockQueryIntradayReplayBarProvider)
    assert isinstance(mtm_bar_provider, StockQueryDailyMtmBarProvider)


@pytest.mark.asyncio
async def test_backtest_ledger_account_snapshot_reflects_portfolio_ledger():
    ledger = BacktestPortfolioLedger(initial_cash=1_000_000)
    ledger.cash = 860_000
    ledger.positions["005930"] = PortfolioPosition(
        code="005930",
        qty=2,
        avg_price=70_000,
        strategy="OneilPocketPivot",
        total_cost=140_000,
    )
    cache = _BacktestLedgerAccountSnapshotCache(ledger)

    snapshot = await cache.get()

    assert isinstance(snapshot, AccountSnapshot)
    assert snapshot.available_cash == 860_000
    assert snapshot.positions == {"005930": 140_000}
    assert snapshot.total_equity == 1_000_000


def test_backtest_strategy_risk_provider_reads_ledger_positions():
    ledger = BacktestPortfolioLedger(initial_cash=1_000_000)
    ledger.positions["005930"] = PortfolioPosition(
        code="005930",
        qty=2,
        avg_price=70_000,
        strategy="OneilPocketPivot",
        total_cost=140_000,
    )
    provider = _BacktestStrategyRiskProvider(ledger)

    assert provider.is_holding("OneilPocketPivot", "005930") is True
    assert provider.is_holding("OtherStrategy", "005930") is False
    assert provider.get_holds_by_strategy("OneilPocketPivot") == [
        {"code": "005930", "qty": 2, "avg_price": 70_000, "evlu_amt": 140_000}
    ]
    assert provider.get_strategy_return_history("OneilPocketPivot") == []


def test_build_risk_sizing_services_disabled_returns_empty_contracts():
    services = _build_risk_sizing_services(
        use_risk_sizing=False,
        config=SimpleNamespace(),
        ledger=BacktestPortfolioLedger(initial_cash=1_000_000),
        indicator_service=object(),
        logger=logging.getLogger("test"),
    )

    assert services.position_sizing_service is None
    assert services.risk_gate_service is None


def test_build_risk_sizing_services_uses_operating_configs():
    config = SimpleNamespace(
        position_sizing=PositionSizingConfig(per_trade_risk_pct=2.0),
        risk_gate=RiskGateConfig(max_order_amount_won=123_456),
        order_policy=OrderPolicyConfig(order_book_checks_enabled=False),
    )

    services = _build_risk_sizing_services(
        use_risk_sizing=True,
        config=config,
        ledger=BacktestPortfolioLedger(initial_cash=1_000_000),
        indicator_service=object(),
        logger=logging.getLogger("test"),
    )

    assert isinstance(services.position_sizing_service, PositionSizingService)
    assert isinstance(services.risk_gate_service, RiskGateService)
    assert services.position_sizing_service._cfg.per_trade_risk_pct == 2.0
    assert services.risk_gate_service._cfg.max_order_amount_won == 123_456
