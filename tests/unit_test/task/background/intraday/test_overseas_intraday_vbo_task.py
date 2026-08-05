"""해외 장중 VBO 폴링 태스크 테스트.

미국 정규장에서만 동작하며(휴장/장외 no-op), 개장 후 준비 지연이 지나면 세션을
준비하고 감시 종목을 폴링한다. 마감 임박에는 진입 대신 EOD 청산만 수행한다.
"""
import pytest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytz

from task.background.intraday.overseas_intraday_vbo_task import OverseasIntradayVBOTask
from common.types import ErrorCode, ResCommonResponse

_NY = pytz.timezone("America/New_York")


def _ny(h, m, day=12):
    return _NY.localize(datetime(2026, 5, day, h, m))


def _price(value):
    return ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="ok",
        data=SimpleNamespace(price=value),
    )


def _task(*, now=None, operating=True, trading_day=True, early_close=False, watch=("AAA",),
          journal=None):
    clock = MagicMock()
    clock.get_current_kst_time = MagicMock(return_value=now or _ny(10, 0))
    clock.get_current_kst_date_str = MagicMock(return_value="20260512")
    clock.is_market_operating_hours = MagicMock(return_value=operating)
    clock.get_market_open_time = MagicMock(return_value=_ny(9, 30))

    us_mcs = MagicMock()
    us_mcs.is_trading_day = MagicMock(return_value=trading_day)
    us_mcs.get_close_time_str = MagicMock(return_value="13:00" if early_close else "16:00")

    vbo = MagicMock()
    vbo.prepare_session = AsyncMock(return_value=len(watch))
    vbo.watch_codes = MagicMock(return_value=list(watch))
    vbo.on_price = AsyncMock(return_value=None)
    vbo.close_all = AsyncMock(return_value=[])

    broker = MagicMock()
    broker.get_overseas_price = AsyncMock(return_value=_price(106.0))

    task = OverseasIntradayVBOTask(
        vbo_service=vbo,
        broker=broker,
        market_clock=clock,
        us_market_calendar_service=us_mcs,
        shadow_journal=journal,
        logger=MagicMock(),
        session_prepare_delay_min=5,
        eod_exit_before_min=10,
    )
    return SimpleNamespace(task=task, vbo=vbo, broker=broker, clock=clock, us_mcs=us_mcs,
                           journal=journal)


@pytest.mark.asyncio
async def test_tick_noop_on_us_holiday():
    t = _task(trading_day=False)

    await t.task._tick()

    t.vbo.prepare_session.assert_not_awaited()
    t.broker.get_overseas_price.assert_not_awaited()


@pytest.mark.asyncio
async def test_tick_noop_outside_market_hours():
    t = _task(operating=False)

    await t.task._tick()

    t.vbo.prepare_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_tick_waits_for_session_prepare_delay():
    """개장 직후엔 당일 봉(시가)이 아직 없을 수 있어 준비를 미룬다."""
    t = _task(now=_ny(9, 32))

    await t.task._tick()

    t.vbo.prepare_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_tick_prepares_session_and_polls_watch_codes():
    t = _task(now=_ny(10, 0))

    await t.task._tick()

    t.vbo.prepare_session.assert_awaited_once()
    assert t.vbo.prepare_session.await_args.args[0] == "20260512"
    t.broker.get_overseas_price.assert_awaited_once()
    t.vbo.on_price.assert_awaited_once_with("AAA", 106.0)


@pytest.mark.asyncio
async def test_tick_closes_positions_near_close_and_stops_polling():
    t = _task(now=_ny(15, 55))

    await t.task._tick()

    t.vbo.close_all.assert_awaited_once()
    assert t.vbo.close_all.await_args.kwargs["reason"] == "eod"
    t.vbo.on_price.assert_not_awaited()


@pytest.mark.asyncio
async def test_tick_flushes_journal_with_session_date_at_eod():
    """paper 기록은 공유 버퍼에 쌓인다 — 세션 종료 시 자기 거래일로 직접 flush 해야
    다른 태스크의 flush 타이밍/파일명에 의존하지 않는다."""
    journal = MagicMock()
    t = _task(now=_ny(15, 55), journal=journal)

    await t.task._tick()

    journal.flush_to_file.assert_called_once_with("20260512")


@pytest.mark.asyncio
async def test_tick_flushes_journal_only_once_per_day():
    journal = MagicMock()
    t = _task(now=_ny(15, 55), journal=journal)

    await t.task._tick()
    await t.task._tick()

    assert journal.flush_to_file.call_count == 1


@pytest.mark.asyncio
async def test_tick_survives_journal_flush_failure():
    journal = MagicMock()
    journal.flush_to_file = MagicMock(side_effect=RuntimeError("disk full"))
    t = _task(now=_ny(15, 55), journal=journal)

    await t.task._tick()  # 청산은 이미 끝났으므로 flush 실패가 루프를 죽이지 않는다

    t.vbo.close_all.assert_awaited_once()


@pytest.mark.asyncio
async def test_tick_uses_early_close_time_for_eod_exit():
    """조기폐장일(13:00 ET)에는 12:50 부터 EOD 청산 구간이다."""
    t = _task(now=_ny(12, 55), early_close=True)

    await t.task._tick()

    t.vbo.close_all.assert_awaited_once()


@pytest.mark.asyncio
async def test_tick_skips_symbol_when_price_fetch_fails():
    t = _task()
    t.broker.get_overseas_price = AsyncMock(side_effect=RuntimeError("boom"))

    await t.task._tick()  # 예외가 루프로 전파되지 않는다

    t.vbo.on_price.assert_not_awaited()


@pytest.mark.asyncio
async def test_task_name_and_progress():
    t = _task()

    assert t.task.task_name == "overseas_intraday_vbo"
    assert t.task.get_progress()["running"] is False
