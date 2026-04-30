from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from common.types import ErrorCode, ResCommonResponse
from task.background.intraday.pre_market_health_check_task import PreMarketHealthCheckTask


@pytest.mark.asyncio
async def test_pre_market_health_check_reports_ok():
    env = SimpleNamespace(
        access_token="token",
        active_config={
            "api_key": "key",
            "api_secret_key": "secret",
            "stock_account_number": "12345678-01",
            "base_url": "https://example.com",
            "websocket_url": "wss://example.com",
        },
    )
    broker = MagicMock()
    broker.get_current_price = AsyncMock(
        return_value=ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data={"output": {}})
    )
    broker.get_account_balance = AsyncMock(
        return_value=ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data={"output1": []})
    )
    dq = MagicMock()
    dq.config.enabled = True
    task = PreMarketHealthCheckTask(
        broker=broker,
        env=env,
        streaming_stock_repo=MagicMock(),
        data_quality_service=dq,
        notification_service=AsyncMock(),
        logger=MagicMock(),
    )

    result = await task.run_once()

    assert result == {"ok": True, "issues": []}
    broker.get_current_price.assert_awaited_once_with("005930")
    broker.get_account_balance.assert_awaited_once()


@pytest.mark.asyncio
async def test_pre_market_health_check_uses_token_api_when_cached_token_missing():
    env = SimpleNamespace(
        active_config={
            "api_key": "key",
            "api_secret_key": "secret",
            "stock_account_number": "12345678-01",
            "base_url": "https://example.com",
            "websocket_url": "wss://example.com",
        },
        get_access_token=AsyncMock(return_value="fresh-token"),
    )
    broker = MagicMock()
    broker.get_current_price = AsyncMock(
        return_value=ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data={"output": {}})
    )
    broker.get_account_balance = AsyncMock(
        return_value=ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data={"output1": []})
    )
    dq = MagicMock()
    dq.config.enabled = True
    task = PreMarketHealthCheckTask(
        broker=broker,
        env=env,
        streaming_stock_repo=MagicMock(),
        data_quality_service=dq,
        notification_service=AsyncMock(),
        logger=MagicMock(),
    )

    result = await task.run_once()

    assert result == {"ok": True, "issues": []}
    env.get_access_token.assert_awaited_once()


@pytest.mark.asyncio
async def test_pre_market_health_check_reports_api_probe_failures():
    ns = AsyncMock()
    env = SimpleNamespace(
        access_token=None,
        active_config={
            "api_key": "key",
            "api_secret_key": "secret",
            "stock_account_number": "12345678-01",
            "base_url": "https://example.com",
            "websocket_url": "wss://example.com",
        },
        get_access_token=AsyncMock(return_value=None),
    )
    broker = MagicMock()
    broker.get_current_price = AsyncMock(
        return_value=ResCommonResponse(rt_cd=ErrorCode.API_ERROR.value, msg1="fail", data=None)
    )
    broker.get_account_balance = AsyncMock(side_effect=RuntimeError("account denied"))
    dq = MagicMock()
    dq.config.enabled = True
    task = PreMarketHealthCheckTask(
        broker=broker,
        env=env,
        streaming_stock_repo=MagicMock(),
        data_quality_service=dq,
        notification_service=ns,
        logger=MagicMock(),
    )

    result = await task.run_once()

    assert result["ok"] is False
    assert "token_check_failed" in result["issues"]
    assert "quotation_api_failed" in result["issues"]
    assert "account_api_failed:exception" in result["issues"]
    ns.emit.assert_awaited_once()


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
