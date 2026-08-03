from unittest.mock import AsyncMock, MagicMock

import pytest

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
