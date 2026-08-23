import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from common.types import ErrorCode, ResCommonResponse
from interfaces.schedulable_task import TaskPriority, TaskState
from task.background.intraday.market_index_threshold_alert_task import MarketIndexThresholdAlertTask

_UNSET = object()


def _build_task(*, broker, alert_service=None, market_open=True, market_calendar=_UNSET):
    if alert_service is None:
        alert_service = MagicMock()
        alert_service.on_index_change = AsyncMock()
    market_clock = MagicMock()
    market_clock.get_current_kst_time.return_value = datetime(2026, 8, 3, 10, 0)
    market_clock.is_market_operating_hours.return_value = market_open
    if market_calendar is _UNSET:
        market_calendar = MagicMock()
        market_calendar.is_business_day = AsyncMock(return_value=True)
    return MarketIndexThresholdAlertTask(
        broker=broker,
        market_status_alert_service=alert_service,
        market_clock=market_clock,
        market_calendar_service=market_calendar,
        logger=MagicMock(),
    )


@pytest.mark.asyncio
async def test_tick_forwards_signed_kospi_and_kosdaq_change_rates():
    broker = MagicMock()
    broker.inquire_time_indexchartprice = AsyncMock(side_effect=[
        ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value,
            msg1="정상",
            data={"summary": {"bstp_nmix_prdy_ctrt": "5.1", "prdy_vrss_sign": "2"}},
        ),
        ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value,
            msg1="정상",
            data={"summary": {"bstp_nmix_prdy_ctrt": "8.2", "prdy_vrss_sign": "5"}},
        ),
    ])
    alert_service = MagicMock()
    alert_service.on_index_change = AsyncMock()
    market_clock = MagicMock()
    market_clock.get_current_kst_time.return_value = datetime(2026, 8, 3, 10, 0)
    market_clock.is_market_operating_hours.return_value = True
    market_calendar = MagicMock()
    market_calendar.is_business_day = AsyncMock(return_value=True)
    task = MarketIndexThresholdAlertTask(
        broker=broker,
        market_status_alert_service=alert_service,
        market_clock=market_clock,
        market_calendar_service=market_calendar,
        logger=MagicMock(),
    )

    await task._tick()

    assert broker.inquire_time_indexchartprice.await_args_list[0].args == ("0001", 60)
    assert broker.inquire_time_indexchartprice.await_args_list[1].args == ("1001", 60)
    assert alert_service.on_index_change.await_args_list[0].args == ("0001", "코스피", 5.1)
    assert alert_service.on_index_change.await_args_list[1].args == ("1001", "코스닥", -8.2)


@pytest.mark.asyncio
async def test_tick_skips_index_when_quote_response_fails():
    broker = MagicMock()
    broker.inquire_time_indexchartprice = AsyncMock(side_effect=[
        ResCommonResponse(rt_cd=ErrorCode.API_ERROR.value, msg1="조회 실패", data=None),
        ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value,
            msg1="정상",
            data={"summary": {"bstp_nmix_prdy_ctrt": "1.2", "prdy_vrss_sign": "2"}},
        ),
    ])
    alert_service = MagicMock()
    alert_service.on_index_change = AsyncMock()
    task = _build_task(broker=broker, alert_service=alert_service)

    await task._tick()

    assert alert_service.on_index_change.await_count == 1
    assert alert_service.on_index_change.await_args.args == ("1001", "코스닥", 1.2)
    task._logger.warning.assert_called()


@pytest.mark.asyncio
async def test_tick_skips_index_when_change_rate_is_missing():
    broker = MagicMock()
    broker.inquire_time_indexchartprice = AsyncMock(return_value=ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value,
        msg1="정상",
        data={"summary": {"bstp_nmix_prdy_ctrt": ""}},
    ))
    alert_service = MagicMock()
    alert_service.on_index_change = AsyncMock()
    task = _build_task(broker=broker, alert_service=alert_service)

    await task._tick()

    alert_service.on_index_change.assert_not_awaited()


@pytest.mark.asyncio
async def test_tick_logs_warning_when_change_rate_reaches_threshold_zone():
    broker = MagicMock()
    broker.inquire_time_indexchartprice = AsyncMock(return_value=ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value,
        msg1="정상",
        data={"summary": {"bstp_nmix_prdy_ctrt": "4.6", "prdy_vrss_sign": "2"}},
    ))
    alert_service = MagicMock()
    alert_service.on_index_change = AsyncMock()
    task = _build_task(broker=broker, alert_service=alert_service)

    await task._tick()

    assert task._logger.warning.call_count == 2
    assert alert_service.on_index_change.await_count == 2


@pytest.mark.asyncio
async def test_tick_does_nothing_outside_market_operating_hours():
    broker = MagicMock()
    broker.inquire_time_indexchartprice = AsyncMock()
    task = _build_task(broker=broker, market_open=False)

    await task._tick()

    broker.inquire_time_indexchartprice.assert_not_awaited()


@pytest.mark.asyncio
async def test_market_open_check_treats_missing_calendar_as_business_day():
    task = _build_task(broker=MagicMock(), market_calendar=None)

    assert await task._is_market_open_now() is True


@pytest.mark.asyncio
async def test_market_open_check_returns_false_on_holiday():
    calendar = MagicMock()
    calendar.is_business_day = AsyncMock(return_value=False)
    task = _build_task(broker=MagicMock(), market_calendar=calendar)

    assert await task._is_market_open_now() is False


@pytest.mark.parametrize(
    "summary, expected",
    [
        ({"bstp_nmix_prdy_ctrt": "1,234.5", "prdy_vrss_sign": "2"}, 1234.5),
        ({"bstp_nmix_prdy_ctrt": "3.3", "prdy_vrss_sign": "down"}, -3.3),
        ({"bstp_nmix_prdy_ctrt": "-2.0", "prdy_vrss_sign": "2"}, -2.0),
        ({"bstp_nmix_prdy_ctrt": "abc"}, None),
        ({}, None),
    ],
)
def test_signed_change_rate_parsing(summary, expected):
    assert MarketIndexThresholdAlertTask._signed_change_rate(summary) == expected


@pytest.mark.asyncio
async def test_loop_runs_tick_logs_error_and_exits_on_cancel():
    task = _build_task(broker=MagicMock())
    task._tick = AsyncMock(side_effect=[None, RuntimeError("boom"), asyncio.CancelledError()])

    with patch(
        "task.background.intraday.market_index_threshold_alert_task.asyncio.sleep",
        new_callable=AsyncMock,
    ):
        await task._loop()

    assert task._tick.await_count == 3
    assert task.state == TaskState.RUNNING
    task._logger.error.assert_called_once()


@pytest.mark.asyncio
async def test_loop_skips_tick_while_suspended():
    task = _build_task(broker=MagicMock())
    task._tick = AsyncMock()
    task._state = TaskState.SUSPENDED
    sleep_mock = AsyncMock(side_effect=[None, asyncio.CancelledError()])

    with patch(
        "task.background.intraday.market_index_threshold_alert_task.asyncio.sleep",
        sleep_mock,
    ):
        with pytest.raises(asyncio.CancelledError):
            await task._loop()

    task._tick.assert_not_awaited()
    assert task.state == TaskState.SUSPENDED


@pytest.mark.asyncio
async def test_start_is_idempotent_and_stop_cancels_running_loop():
    task = _build_task(broker=MagicMock())
    task._tick = AsyncMock()

    await task.start()
    first_task = task._task
    await task.start()
    assert task._task is first_task

    await task.stop()

    assert task._task is None
    assert task.state == TaskState.STOPPED


@pytest.mark.asyncio
async def test_stop_without_start_only_marks_stopped():
    task = _build_task(broker=MagicMock())

    await task.stop()

    assert task._task is None
    assert task.state == TaskState.STOPPED


@pytest.mark.asyncio
async def test_suspend_and_resume_toggle_state_and_progress():
    task = _build_task(broker=MagicMock())

    await task.suspend()
    assert task.state == TaskState.SUSPENDED
    assert task.get_progress() == {"running": False}

    await task.resume()
    assert task.state == TaskState.IDLE

    await task.resume()
    assert task.state == TaskState.IDLE


def test_task_identity_exposes_name_and_priority():
    task = _build_task(broker=MagicMock())

    assert task.task_name == "market_index_threshold_alert"
    assert task.priority == TaskPriority.HIGH
