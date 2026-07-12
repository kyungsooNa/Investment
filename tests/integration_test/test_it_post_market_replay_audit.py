import json
import logging
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from common.types import TradeSignal
from core.market_clock import MarketClock
from interfaces.schedulable_task import TaskPriority
from repositories.backtest_journal_repository import BacktestJournalRepository
from services.post_market_replay_audit_service import PostMarketReplayAuditService
from strategies.debug.strategy_debug_runner import StrategyDebugRunner
from services.strategy_log_report_service import StrategyLogReportService
from task.background.after_market.post_market_replay_audit_task import PostMarketReplayAuditTask
from view.web.bootstrap.scheduler_bootstrap import SchedulerBootstrap
from view.web.bootstrap.runtime_mode import RuntimeMode


def _write_strategy_log(log_dir: str, strategy: str = "OneilPocketPivot") -> None:
    os.makedirs(log_dir, exist_ok=True)
    path = os.path.join(log_dir, f"20260505_090300_{strategy}.log.json")
    rows = [
        {
            "timestamp": "2026-05-05 09:03:00,000",
            "level": "INFO",
            "data": {"event": "scan_with_watchlist", "count": 2},
        },
        {
            "timestamp": "2026-05-05 09:03:10,000",
            "level": "DEBUG",
            "data": {"event": "entry_rejected", "code": "005930", "name": "삼성전자", "reason": "near_signal"},
        },
        {
            "timestamp": "2026-05-05 09:03:11,000",
            "level": "DEBUG",
            "data": {"event": "entry_rejected", "code": "000660", "name": "SK하이닉스", "reason": "near_signal"},
        },
    ]
    with open(path, "w", encoding="utf-8") as fp:
        for row in rows:
            fp.write(json.dumps(row, ensure_ascii=False) + "\n")


def _strategy_factory(**_kwargs):
    universe = MagicMock()
    universe.get_watchlist = AsyncMock(return_value={"005930": object()})

    class FakeStrategy:
        name = "OneilPocketPivot"
        _universe = universe

        async def scan(self):
            await self._universe.get_watchlist()
            return [
                TradeSignal(
                    code="005930",
                    name="삼성전자",
                    action="BUY",
                    price=70_000,
                    qty=1,
                    reason="replay_signal",
                    strategy_name="OneilPocketPivot",
                )
            ]

    return FakeStrategy()


@pytest.mark.asyncio
async def test_it_post_market_replay_audit_task_saves_backtest_journal_run(tmp_path):
    log_dir = str(tmp_path / "strategies")
    _write_strategy_log(log_dir)
    repo = BacktestJournalRepository(tmp_path / "backtest_journals")
    store = MagicMock()
    store.load_signal_history_for_date.return_value = []
    service = PostMarketReplayAuditService(
        stock_query_service=AsyncMock(),
        universe_service=MagicMock(),
        indicator_service=MagicMock(),
        market_clock=MarketClock(),
        backtest_journal_repository=repo,
        scheduler_store=store,
        log_dir=log_dir,
        strategy_factory=_strategy_factory,
        debug_runner_factory=StrategyDebugRunner,
        env=SimpleNamespace(is_paper_trading=False),
        logger=logging.getLogger("test_it_post_market_replay_audit"),
    )
    task = PostMarketReplayAuditTask(
        audit_service=service,
        mcs=None,
        market_clock=MarketClock(),
        logger=logging.getLogger("test_it_post_market_replay_audit.task"),
    )

    await task.execute({"date": "20260505"})

    records = repo.load_records_for_date("20260505")
    by_code = {record["code"]: record for record in records}
    assert by_code["005930"]["metadata"]["audit_status"] == "missed_by_scheduler"
    assert by_code["000660"]["metadata"]["audit_status"] == "missing_from_universe"
    runs = repo.list_runs(limit=None)
    assert runs[0]["run_id"] == "audit_OneilPocketPivot_20260505"
    assert runs[0]["metadata"]["audit_type"] == "missed_signal"


@pytest.mark.asyncio
async def test_it_strategy_report_reads_replay_audit_journal(tmp_path):
    log_dir = str(tmp_path / "strategies")
    _write_strategy_log(log_dir)
    repo = BacktestJournalRepository(tmp_path / "backtest_journals")
    repo.save_run(
        [
            {
                "source": "backtest",
                "strategy": "OneilPocketPivot",
                "code": "005930",
                "signal_time": "2026-05-05 09:03:00",
                "status": "SIGNAL",
                "metadata": {"audit_status": "missed_by_scheduler", "live_signal_time": ""},
            }
        ],
        run_id="audit_OneilPocketPivot_20260505",
        strategy="OneilPocketPivot",
        target_date="20260505",
        metadata={"audit_type": "missed_signal"},
    )
    virtual_trade_service = MagicMock()
    virtual_trade_service.get_all_trades.return_value = []
    virtual_trade_service.compare_with_backtest_journal.return_value = {
        "summary": {"matched_count": 0, "unmatched_backtest_count": 1, "unmatched_live_count": 0},
        "matches": [],
    }
    service = StrategyLogReportService(
        log_dir=log_dir,
        virtual_trade_service=virtual_trade_service,
        backtest_journal_provider=repo.load_records_for_date,
    )

    report = await service.generate_report("20260505")

    assert "백테스트-실거래 괴리" in report
    assert "Replay audit: missed 1건" in report
    assert "OneilPocketPivot/005930: missed_by_scheduler" in report


def test_it_scheduler_bootstrap_registers_replay_audit_batch_task_with_delay():
    ctx = MagicMock()
    ctx.runtime_mode = RuntimeMode.BATCH
    ctx.logger = MagicMock()
    ctx.pm = MagicMock()
    ctx.worker_pool = MagicMock()
    ctx.time_dispatcher = MagicMock()
    ctx.post_market_replay_audit_task = SimpleNamespace(task_name="post_market_replay_audit")

    for name in (
        "ranking_task",
        "minervini_update_task",
        "daily_price_collector_task",
        "ohlcv_update_task",
        "premium_watchlist_generator_task",
        "newhigh_task",
        "log_cleanup_task",
        "strategy_log_report_task",
        "after_market_reconcile_task",
        "theme_classification_task",
    ):
        setattr(ctx, name, None)

    with patch("view.web.bootstrap.scheduler_bootstrap.BackgroundScheduler") as MockBackground, \
         patch("view.web.bootstrap.scheduler_bootstrap.ForegroundScheduler"), \
         patch(
             "view.web.bootstrap.scheduler_bootstrap.load_after_market_delays",
             return_value={"post_market_replay_audit": 30},
         ):
        ctx.background_scheduler = MockBackground.return_value
        SchedulerBootstrap(ctx).run()

    ctx.time_dispatcher.register_task.assert_called_once_with(
        "post_market_replay_audit",
        TaskPriority.LOW,
        delay_sec=30,
    )
    MockBackground.return_value.register.assert_called_once_with(ctx.post_market_replay_audit_task)
