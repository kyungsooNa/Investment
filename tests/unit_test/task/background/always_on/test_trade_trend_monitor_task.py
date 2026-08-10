from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from interfaces.schedulable_task import TaskState
from services.trade_trend_service import TradeStatItem
from task.background.always_on.trade_trend_monitor_task import TradeTrendMonitorTask


class DummyClock:
    def __init__(self, now: datetime):
        self._now = now

    def get_current_kst_time(self):
        return self._now


@pytest.mark.asyncio
async def test_tick_sends_jeju_semiconductor_report_once(tmp_path):
    current = TradeStatItem("2026.05", "집적회로 반도체", "8542", 46585000, 0, 0)
    previous_month = TradeStatItem("2026.04", "집적회로 반도체", "8542", 30000000, 0, 0)
    previous_year = TradeStatItem("2025.05", "집적회로 반도체", "8542", 10000000, 0, 0)
    total = TradeStatItem("2026.05", "제주 전체", "-", 63590000, 0, 0)
    async def fetch_sido_item_month(yyyymm, item_code):
        return {
            "202605": [current],
            "202604": [previous_month],
            "202505": [previous_year],
        }[yyyymm]

    client = SimpleNamespace(
        fetch_sido_item_month=AsyncMock(side_effect=fetch_sido_item_month),
        fetch_sido_total_month=AsyncMock(return_value=[total]),
    )
    reporter = SimpleNamespace(send_jeju_semiconductor_trade_report=AsyncMock(return_value=True))
    task = TradeTrendMonitorTask(
        customs_client=client,
        repository_path=str(tmp_path / "trade_trend_state.json"),
        telegram_reporter=reporter,
        config=SimpleNamespace(item_code="8542"),
        market_clock=DummyClock(datetime(2026, 6, 16, 9, 30)),
        logger=MagicMock(),
    )

    await task._tick()
    await task._tick()

    reporter.send_jeju_semiconductor_trade_report.assert_awaited_once()
    assert task.get_progress()["sent_count"] == 1


@pytest.mark.asyncio
async def test_start_stop_cancels_background_loop(tmp_path):
    client = SimpleNamespace(
        fetch_sido_item_month=AsyncMock(return_value=[]),
        fetch_sido_total_month=AsyncMock(return_value=[]),
    )
    task = TradeTrendMonitorTask(
        customs_client=client,
        repository_path=str(tmp_path / "trade_trend_state.json"),
        telegram_reporter=None,
        config=SimpleNamespace(poll_interval_sec=3600),
        market_clock=DummyClock(datetime(2026, 6, 16, 9, 30)),
        logger=MagicMock(),
    )

    await task.start()
    assert task.state == TaskState.RUNNING
    await task.stop()

    assert task.state == TaskState.STOPPED
