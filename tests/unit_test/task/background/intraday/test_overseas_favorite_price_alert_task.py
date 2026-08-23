import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from interfaces.schedulable_task import TaskPriority, TaskState

from common.types import ErrorCode, ResCommonResponse
from repositories.favorite_repository import MARKET_OVERSEAS_US
from task.background.intraday.overseas_favorite_price_alert_task import (
    OverseasFavoritePriceAlertTask,
)


def _price_response(price, change_rate):
    return ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value,
        msg1="정상",
        data=MagicMock(price=price, change_rate=change_rate),
    )


def _build_task(*, symbols, broker, market_open=True, trading_day=True, meta=None):
    repo = MagicMock()
    repo.get_all = AsyncMock(return_value=symbols)
    alert_service = MagicMock()
    alert_service.handle_price_tick = AsyncMock(return_value=True)
    market_clock = MagicMock()
    market_clock.is_market_operating_hours.return_value = market_open
    market_clock.get_current_kst_date_str.return_value = "20260803"
    calendar = MagicMock()
    calendar.is_trading_day.return_value = trading_day
    overseas_codes = MagicMock()
    overseas_codes.get_meta.return_value = meta

    task = OverseasFavoritePriceAlertTask(
        favorite_repository=repo,
        broker=broker,
        alert_service=alert_service,
        market_clock=market_clock,
        overseas_stock_code_repository=overseas_codes,
        us_market_calendar_service=calendar,
    )
    return task, repo, alert_service


@pytest.mark.asyncio
async def test_polls_overseas_favorites_and_forwards_price_to_alert_service():
    broker = MagicMock()
    broker.get_overseas_price = AsyncMock(return_value=_price_response(189.5, 5.3))
    task, repo, alert_service = _build_task(symbols=["AAPL"], broker=broker)

    await task._tick()

    repo.get_all.assert_awaited_once_with(market=MARKET_OVERSEAS_US)
    broker.get_overseas_price.assert_awaited_once_with("AAPL", exchange="NASD")
    alert_service.handle_price_tick.assert_awaited_once_with(
        "AAPL", price=189.5, rate=5.3
    )


@pytest.mark.asyncio
async def test_uses_exchange_from_overseas_stock_code_repository():
    broker = MagicMock()
    broker.get_overseas_price = AsyncMock(return_value=_price_response(42.0, 5.1))
    task, _, _ = _build_task(
        symbols=["F"], broker=broker, meta={"exchange": "NYSE"}
    )

    await task._tick()

    broker.get_overseas_price.assert_awaited_once_with("F", exchange="NYSE")


@pytest.mark.asyncio
async def test_normalizes_saved_symbol_case_before_fetching_and_alerting():
    broker = MagicMock()
    broker.get_overseas_price = AsyncMock(return_value=_price_response(189.5, 5.3))
    task, _, alert_service = _build_task(symbols=["aapl"], broker=broker)

    await task._tick()

    broker.get_overseas_price.assert_awaited_once_with("AAPL", exchange="NASD")
    alert_service.handle_price_tick.assert_awaited_once_with(
        "AAPL", price=189.5, rate=5.3
    )


@pytest.mark.asyncio
async def test_skips_polling_outside_us_market_hours():
    broker = MagicMock()
    broker.get_overseas_price = AsyncMock()
    task, repo, alert_service = _build_task(
        symbols=["AAPL"], broker=broker, market_open=False
    )

    await task._tick()

    broker.get_overseas_price.assert_not_awaited()
    alert_service.handle_price_tick.assert_not_awaited()


@pytest.mark.asyncio
async def test_skips_polling_on_us_market_holiday():
    broker = MagicMock()
    broker.get_overseas_price = AsyncMock()
    task, _, alert_service = _build_task(
        symbols=["AAPL"], broker=broker, trading_day=False
    )

    await task._tick()

    broker.get_overseas_price.assert_not_awaited()
    alert_service.handle_price_tick.assert_not_awaited()


@pytest.mark.asyncio
async def test_continues_remaining_symbols_when_one_lookup_fails():
    broker = MagicMock()
    broker.get_overseas_price = AsyncMock(
        side_effect=[
            RuntimeError("timeout"),
            ResCommonResponse(rt_cd=ErrorCode.API_ERROR.value, msg1="조회 실패", data=None),
            _price_response(300.0, -10.4),
        ]
    )
    task, _, alert_service = _build_task(
        symbols=["AAPL", "MSFT", "TSLA"], broker=broker
    )

    await task._tick()

    alert_service.handle_price_tick.assert_awaited_once_with(
        "TSLA", price=300.0, rate=-10.4
    )


@pytest.mark.asyncio
async def test_skips_symbol_when_change_rate_is_missing():
    broker = MagicMock()
    broker.get_overseas_price = AsyncMock(return_value=_price_response(189.5, None))
    task, _, alert_service = _build_task(symbols=["AAPL"], broker=broker)

    await task._tick()

    alert_service.handle_price_tick.assert_not_awaited()


@pytest.mark.asyncio
async def test_skips_blank_symbols_without_calling_broker():
    broker = MagicMock()
    broker.get_overseas_price = AsyncMock(return_value=_price_response(189.5, 5.3))
    task, _, alert_service = _build_task(symbols=["", "   ", None, "AAPL"], broker=broker)

    await task._tick()

    broker.get_overseas_price.assert_awaited_once_with("AAPL", exchange="NASD")
    alert_service.handle_price_tick.assert_awaited_once()


def test_exchange_falls_back_to_default_without_code_repository():
    task = OverseasFavoritePriceAlertTask(
        favorite_repository=MagicMock(),
        broker=MagicMock(),
        alert_service=MagicMock(),
        market_clock=MagicMock(),
        logger=MagicMock(),
    )

    assert task._exchange_of("AAPL") == OverseasFavoritePriceAlertTask.DEFAULT_EXCHANGE


def test_market_open_check_treats_missing_calendar_as_trading_day():
    market_clock = MagicMock()
    market_clock.is_market_operating_hours.return_value = True
    task = OverseasFavoritePriceAlertTask(
        favorite_repository=MagicMock(),
        broker=MagicMock(),
        alert_service=MagicMock(),
        market_clock=market_clock,
        logger=MagicMock(),
    )

    assert task._is_market_open_now() is True


@pytest.mark.asyncio
async def test_loop_runs_tick_logs_error_and_exits_on_cancel():
    task, _, _ = _build_task(symbols=[], broker=MagicMock())
    task._logger = MagicMock()
    task._tick = AsyncMock(side_effect=[None, RuntimeError("boom"), asyncio.CancelledError()])

    with patch(
        "task.background.intraday.overseas_favorite_price_alert_task.asyncio.sleep",
        new_callable=AsyncMock,
    ):
        await task._loop()

    assert task._tick.await_count == 3
    task._logger.error.assert_called_once()


@pytest.mark.asyncio
async def test_loop_skips_tick_while_suspended():
    task, _, _ = _build_task(symbols=[], broker=MagicMock())
    task._tick = AsyncMock()
    task._state = TaskState.SUSPENDED
    sleep_mock = AsyncMock(side_effect=[None, asyncio.CancelledError()])

    with patch(
        "task.background.intraday.overseas_favorite_price_alert_task.asyncio.sleep",
        sleep_mock,
    ):
        with pytest.raises(asyncio.CancelledError):
            await task._loop()

    task._tick.assert_not_awaited()
    assert task.state == TaskState.SUSPENDED


@pytest.mark.asyncio
async def test_start_is_idempotent_and_stop_clears_background_task():
    task, _, _ = _build_task(symbols=[], broker=MagicMock())

    await task.start()
    first_task = task._task
    await task.start()
    assert task._task is first_task

    await task.stop()

    assert task._task is None
    assert task.state == TaskState.STOPPED


@pytest.mark.asyncio
async def test_stop_without_start_only_marks_stopped():
    task, _, _ = _build_task(symbols=[], broker=MagicMock())

    await task.stop()

    assert task._task is None
    assert task.state == TaskState.STOPPED


@pytest.mark.asyncio
async def test_suspend_and_resume_toggle_state_and_progress():
    task, _, _ = _build_task(symbols=[], broker=MagicMock())

    await task.suspend()
    assert task.state == TaskState.SUSPENDED
    assert task.get_progress() == {"running": False}

    await task.resume()
    assert task.state == TaskState.IDLE

    await task.resume()
    assert task.state == TaskState.IDLE


def test_task_identity_exposes_name_and_priority():
    task, _, _ = _build_task(symbols=[], broker=MagicMock())

    assert task.task_name == "overseas_favorite_price_alert"
    assert task.priority == TaskPriority.HIGH
