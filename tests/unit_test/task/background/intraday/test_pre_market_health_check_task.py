import asyncio
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from common.types import ErrorCode, ResCommonResponse
from interfaces.schedulable_task import TaskState
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


@pytest.mark.asyncio
async def test_pre_market_task_lifecycle_progress_suspend_resume_stop():
    task = PreMarketHealthCheckTask(logger=MagicMock())

    try:
        await task.start()
        assert task.state == TaskState.IDLE
        assert task.get_progress()["running"] is False

        await task.start()
        assert len(task._tasks) == 1

        await task.suspend()
        assert task.state == TaskState.IDLE
        await task.resume()
        assert task.state == TaskState.IDLE
    finally:
        await task.stop()

    assert task.state == TaskState.STOPPED
    assert task._tasks == []


@pytest.mark.asyncio
async def test_pre_market_should_not_run_without_dependencies_or_on_closed_day():
    task = PreMarketHealthCheckTask()
    assert await task._should_run_now() is False

    clock = MagicMock()
    clock.get_current_kst_time.return_value = datetime(2026, 4, 30, 8, 45)
    mcs = AsyncMock()
    mcs.is_business_day.return_value = False
    task = PreMarketHealthCheckTask(market_calendar_service=mcs, market_clock=clock)

    assert await task._should_run_now() is False


@pytest.mark.asyncio
async def test_pre_market_should_not_run_when_calendar_check_raises():
    clock = MagicMock()
    clock.get_current_kst_time.return_value = datetime(2026, 4, 30, 8, 45)
    mcs = AsyncMock()
    mcs.is_business_day.side_effect = RuntimeError("calendar down")
    task = PreMarketHealthCheckTask(market_calendar_service=mcs, market_clock=clock)

    assert await task._should_run_now() is False


@pytest.mark.asyncio
async def test_pre_market_health_check_reports_disabled_and_missing_methods():
    ns = AsyncMock()
    env = SimpleNamespace(
        active_config={
            "api_key": "",
            "api_secret_key": "secret",
            "stock_account_number": "",
            "base_url": "https://example.com",
            "websocket_url": "",
        }
    )
    dq = MagicMock()
    dq.config.enabled = False
    task = PreMarketHealthCheckTask(
        broker=SimpleNamespace(),
        env=env,
        streaming_stock_repo=SimpleNamespace(get_desired=lambda: (_ for _ in ()).throw(RuntimeError("repo down"))),
        data_quality_service=dq,
        notification_service=ns,
        logger=MagicMock(),
    )

    result = await task.run_once()

    assert result["ok"] is False
    assert "config_missing:api_key" in result["issues"]
    assert "config_missing:stock_account_number" in result["issues"]
    assert "config_missing:websocket_url" in result["issues"]
    assert "token_check_failed" in result["issues"]
    assert "quotation_api_failed:method_missing" in result["issues"]
    assert "account_api_failed:method_missing" in result["issues"]
    assert "data_quality_disabled" in result["issues"]
    assert "streaming_desired_check_failed" in result["issues"]
    ns.emit.assert_awaited_once()


@pytest.mark.asyncio
async def test_pre_market_token_check_handles_sync_token_missing_getter_and_exception():
    logger = MagicMock()
    task = PreMarketHealthCheckTask(env=SimpleNamespace(get_access_token=lambda: "sync-token"), logger=logger)
    assert await task._check_token_access() is True

    task = PreMarketHealthCheckTask(env=SimpleNamespace(), logger=logger)
    assert await task._check_token_access() is False

    task = PreMarketHealthCheckTask(
        env=SimpleNamespace(get_access_token=lambda: (_ for _ in ()).throw(RuntimeError("token down"))),
        logger=logger,
    )
    assert await task._check_token_access() is False
    logger.warning.assert_called()


@pytest.mark.asyncio
async def test_pre_market_check_broker_api_accepts_sync_response_and_plain_rt_cd():
    broker = SimpleNamespace(get_account_balance=lambda: SimpleNamespace(rt_cd=ErrorCode.SUCCESS.value))
    task = PreMarketHealthCheckTask(broker=broker, logger=MagicMock())
    issues = []

    await task._check_broker_api("get_account_balance", issues, "account_api_failed")

    assert issues == []
    assert task._is_success_response(SimpleNamespace(rt_cd=ErrorCode.SUCCESS.value)) is True
    assert task._is_success_response(SimpleNamespace(rt_cd=ErrorCode.API_ERROR.value)) is False


@pytest.mark.asyncio
async def test_pre_market_loop_runs_once_and_logs_errors():
    task = PreMarketHealthCheckTask(logger=MagicMock())
    task._state = TaskState.IDLE
    task._should_run_now = AsyncMock(side_effect=[True, RuntimeError("loop boom"), asyncio.CancelledError()])
    task.run_once = AsyncMock()

    with patch("task.background.intraday.pre_market_health_check_task.asyncio.sleep", new_callable=AsyncMock):
        await task._loop()

    task.run_once.assert_awaited_once()
    task._logger.error.assert_called_once()


@pytest.mark.asyncio
async def test_pre_market_loop_marks_running_only_while_checking():
    task = PreMarketHealthCheckTask(logger=MagicMock())
    task._state = TaskState.IDLE
    task._should_run_now = AsyncMock(side_effect=[True, asyncio.CancelledError()])
    seen_states = []

    async def _run_once():
        seen_states.append(task.state)
        seen_states.append(task.get_progress()["running"])

    task.run_once = AsyncMock(side_effect=_run_once)

    with patch("task.background.intraday.pre_market_health_check_task.asyncio.sleep", new_callable=AsyncMock):
        await task._loop()

    assert seen_states == [TaskState.RUNNING, True]
    assert task.state == TaskState.IDLE
    assert task.get_progress()["running"] is False
