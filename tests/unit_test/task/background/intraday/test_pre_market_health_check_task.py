from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from task.background.intraday.pre_market_health_check_task import PreMarketHealthCheckTask


@pytest.mark.asyncio
async def test_pre_market_health_check_reports_ok():
    env = MagicMock()
    env.access_token = "token"
    dq = MagicMock()
    dq.config.enabled = True
    task = PreMarketHealthCheckTask(
        broker=MagicMock(),
        env=env,
        streaming_stock_repo=MagicMock(),
        data_quality_service=dq,
        notification_service=AsyncMock(),
        logger=MagicMock(),
    )

    result = await task.run_once()

    assert result == {"ok": True, "issues": []}


@pytest.mark.asyncio
async def test_pre_market_health_check_notifies_issues():
    ns = AsyncMock()
    task = PreMarketHealthCheckTask(
        broker=None,
        env=None,
        streaming_stock_repo=None,
        data_quality_service=None,
        notification_service=ns,
        logger=MagicMock(),
    )

    result = await task.run_once()

    assert result["ok"] is False
    assert "broker_missing" in result["issues"]
    ns.emit.assert_awaited_once()


@pytest.mark.asyncio
async def test_pre_market_should_run_once_per_day():
    clock = MagicMock()
    now = datetime(2026, 4, 30, 8, 45)
    clock.get_current_kst_time.return_value = now
    clock.get_market_open_time.return_value = datetime(2026, 4, 30, 9, 0)
    mcs = AsyncMock()
    mcs.is_business_day.return_value = True
    task = PreMarketHealthCheckTask(market_calendar_service=mcs, market_clock=clock)

    assert await task._should_run_now() is True
    task._last_checked_date = "20260430"
    assert await task._should_run_now() is False
