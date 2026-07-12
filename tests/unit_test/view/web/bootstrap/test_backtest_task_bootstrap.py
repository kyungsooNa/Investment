from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from view.web.bootstrap.backtest_task_bootstrap import BacktestTaskBootstrap


def test_backtest_task_bootstrap_builds_both_batch_tasks():
    ctx = SimpleNamespace(
        stock_repository=MagicMock(),
        stock_query_service=MagicMock(),
        oneil_universe_service=MagicMock(),
        indicator_service=MagicMock(),
        market_clock=MagicMock(),
        backtest_journal_repository=MagicMock(),
        virtual_trade_service=MagicMock(),
        broker=MagicMock(),
        env=MagicMock(),
        logger=MagicMock(log_dir="logs"),
        _mcs=MagicMock(),
        worker_pool=MagicMock(),
    )

    with patch("view.web.bootstrap.backtest_task_bootstrap.PostMarketReplayAuditTask") as audit_task, \
         patch("view.web.bootstrap.backtest_task_bootstrap.NewHighStrategyCoverageBacktestTask") as coverage_task:
        BacktestTaskBootstrap(ctx).run()

    assert ctx.post_market_replay_audit_task is audit_task.return_value
    assert ctx.newhigh_strategy_coverage_backtest_task is coverage_task.return_value
