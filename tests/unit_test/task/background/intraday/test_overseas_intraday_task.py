"""해외 장중 VBO 폴링 태스크 테스트.

미국 정규장에서만 동작하며(휴장/장외 no-op), 개장 후 준비 지연이 지나면 세션을
준비하고 감시 종목을 폴링한다. 마감 임박에는 진입 대신 EOD 청산만 수행한다.
"""
import asyncio

import pytest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytz

from interfaces.schedulable_task import TaskPriority, TaskState
from task.background.intraday.overseas_intraday_task import OverseasIntradayTask
from common.types import ErrorCode, ResCommonResponse
from services.overseas_intraday_vbo_service import OverseasIntradayVBOService

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

    task = OverseasIntradayTask(
        strategy_services=[vbo],
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
    t.vbo.on_price.assert_awaited_once_with("AAA", 106.0, volume=None)


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
async def test_tick_flushes_journal_after_each_polling_pass():
    """paper 기록을 EOD 까지 메모리에 들고 있으면 재시작 한 번에 하루치가 사라진다.

    실측(2026-08-05): 진입 2건이 EOD flush 전 프로세스 종료로 통째로 유실됐다.
    폴링 패스마다 파일로 내려 재시작에 견디게 한다(버퍼가 비면 no-op).
    """
    journal = MagicMock()
    t = _task(now=_ny(10, 0), journal=journal)

    await t.task._tick()

    journal.flush_to_file.assert_called_once_with("20260512")


@pytest.mark.asyncio
async def test_tick_polling_flush_survives_journal_failure():
    journal = MagicMock()
    journal.flush_to_file = MagicMock(side_effect=RuntimeError("disk full"))
    t = _task(now=_ny(10, 0), journal=journal)

    await t.task._tick()  # flush 실패가 폴링 루프를 죽이지 않는다

    t.vbo.on_price.assert_awaited_once()


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

    assert t.task.task_name == "overseas_intraday"
    assert t.task.get_progress()["running"] is False


@pytest.mark.asyncio
async def test_tick_runs_without_us_calendar_service():
    t = _task()
    t.task._us_mcs = None

    await t.task._tick()

    t.vbo.prepare_session.assert_awaited_once_with("20260512")
    assert t.task._close_minute("20260512") == 16 * 60


@pytest.mark.parametrize("close_str", ["", None, "이른마감", "13:xx"])
def test_close_minute_falls_back_to_default_for_unusable_close_time(close_str):
    t = _task()
    t.us_mcs.get_close_time_str = MagicMock(return_value=close_str)

    assert t.task._close_minute("20260512") == 16 * 60


@pytest.mark.asyncio
async def test_fetch_tick_returns_none_when_broker_raises():
    t = _task()
    t.broker.get_overseas_price = AsyncMock(side_effect=RuntimeError("timeout"))

    assert await t.task._fetch_tick("AAA") is None
    t.task._logger.warning.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        ResCommonResponse(rt_cd=ErrorCode.API_ERROR.value, msg1="fail", data=None),
        ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="ok", data=SimpleNamespace(price=None)),
        ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="ok", data=SimpleNamespace(price="없음")),
        ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="ok", data=SimpleNamespace(price=0)),
        ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="ok", data=SimpleNamespace(price=-1.0)),
    ],
)
async def test_fetch_tick_rejects_unusable_responses(response):
    t = _task()
    t.broker.get_overseas_price = AsyncMock(return_value=response)

    assert await t.task._fetch_tick("AAA") is None


@pytest.mark.asyncio
async def test_loop_runs_tick_logs_error_and_exits_on_cancel():
    t = _task()
    t.task._tick = AsyncMock(side_effect=[None, RuntimeError("boom"), asyncio.CancelledError()])

    with patch(
        "task.background.intraday.overseas_intraday_task.asyncio.sleep",
        new_callable=AsyncMock,
    ):
        await t.task._loop()

    assert t.task._tick.await_count == 3
    t.task._logger.error.assert_called_once()


@pytest.mark.asyncio
async def test_loop_skips_tick_while_suspended():
    t = _task()
    t.task._tick = AsyncMock()
    t.task._state = TaskState.SUSPENDED
    sleep_mock = AsyncMock(side_effect=[None, asyncio.CancelledError()])

    with patch("task.background.intraday.overseas_intraday_task.asyncio.sleep", sleep_mock):
        with pytest.raises(asyncio.CancelledError):
            await t.task._loop()

    t.task._tick.assert_not_awaited()
    assert t.task.state == TaskState.SUSPENDED


@pytest.mark.asyncio
async def test_start_is_idempotent_and_stop_clears_background_task():
    t = _task()

    await t.task.start()
    first = t.task._task
    await t.task.start()
    assert t.task._task is first

    await t.task.stop()

    assert t.task._task is None
    assert t.task.state == TaskState.STOPPED


@pytest.mark.asyncio
async def test_stop_without_start_only_marks_stopped():
    t = _task()

    await t.task.stop()

    assert t.task._task is None
    assert t.task.state == TaskState.STOPPED


@pytest.mark.asyncio
async def test_suspend_and_resume_toggle_state():
    t = _task()

    await t.task.suspend()
    assert t.task.state == TaskState.SUSPENDED

    await t.task.resume()
    assert t.task.state == TaskState.IDLE

    await t.task.resume()
    assert t.task.state == TaskState.IDLE
    assert t.task.priority == TaskPriority.NORMAL


# ── 다중 전략 fan-out ────────────────────────────────────────────────────

def _strategy(name, watch):
    svc = MagicMock()
    svc.STRATEGY_NAME = name
    svc.prepare_session = AsyncMock(return_value=len(watch))
    svc.watch_codes = MagicMock(return_value=list(watch))
    svc.on_price = AsyncMock(return_value=None)
    svc.close_all = AsyncMock(return_value=[])
    return svc


def _multi_task(services, *, now=None, price=106.0, volume=900_000.0):
    clock = MagicMock()
    clock.get_current_kst_time = MagicMock(return_value=now or _ny(10, 0))
    clock.get_current_kst_date_str = MagicMock(return_value="20260512")
    clock.is_market_operating_hours = MagicMock(return_value=True)
    clock.get_market_open_time = MagicMock(return_value=_ny(9, 30))
    us_mcs = MagicMock()
    us_mcs.is_trading_day = MagicMock(return_value=True)
    us_mcs.get_close_time_str = MagicMock(return_value="16:00")
    broker = MagicMock()
    broker.get_overseas_price = AsyncMock(return_value=ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="ok",
        data=SimpleNamespace(price=price, volume=volume)))
    task = OverseasIntradayTask(
        strategy_services=services, broker=broker, market_clock=clock,
        us_market_calendar_service=us_mcs, logger=MagicMock(),
        session_prepare_delay_min=5, eod_exit_before_min=10,
    )
    return SimpleNamespace(task=task, broker=broker)


@pytest.mark.asyncio
async def test_overlapping_symbol_is_fetched_once_and_fanned_out():
    """전략마다 조회하면 API 예산을 전략 수만큼 태운다 — 심볼당 1회만 조회한다."""
    a = _strategy("A", ["AAA", "BBB"])
    b = _strategy("B", ["BBB", "CCC"])
    t = _multi_task([a, b])

    await t.task._tick()

    fetched = sorted(c.args[0] for c in t.broker.get_overseas_price.call_args_list)
    assert fetched == ["AAA", "BBB", "CCC"]          # BBB 를 두 번 조회하지 않는다
    assert t.broker.get_overseas_price.await_count == 3

    a_codes = sorted(c.args[0] for c in a.on_price.call_args_list)
    b_codes = sorted(c.args[0] for c in b.on_price.call_args_list)
    assert a_codes == ["AAA", "BBB"]                 # 자기가 보는 심볼만 받는다
    assert b_codes == ["BBB", "CCC"]


@pytest.mark.asyncio
async def test_tick_passes_volume_to_strategies():
    a = _strategy("A", ["AAA"])
    t = _multi_task([a], volume=1_234_000.0)

    await t.task._tick()

    assert a.on_price.await_args.kwargs["volume"] == pytest.approx(1_234_000.0)


@pytest.mark.asyncio
async def test_tick_can_drive_real_vbo_strategy_with_volume_payload():
    """실제 VBO 서비스도 공용 폴링 태스크의 volume 인자 계약을 받아야 한다."""
    vbo = OverseasIntradayVBOService(
        candidate_service=MagicMock(),
        stock_query_service=MagicMock(),
        order_execution_service=MagicMock(),
        logger=MagicMock(),
    )
    vbo.prepare_session = AsyncMock(return_value=1)
    vbo.watch_codes = MagicMock(return_value=["AAA"])
    vbo._watch = {"AAA": {"target": 105.0, "prev_range": 10.0, "exchange": "NASD"}}
    vbo._session_date = "20260512"
    vbo._orders.place_entry = AsyncMock(
        return_value=ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="ok", data={})
    )
    t = _multi_task([vbo], price=106.0, volume=1_234_000.0)

    await t.task._tick()

    assert vbo._orders.place_entry.await_count == 1


@pytest.mark.asyncio
async def test_missing_volume_is_passed_as_none():
    """거래량이 없으면 None 으로 넘겨 전략이 fail-closed 판정하게 한다."""
    a = _strategy("A", ["AAA"])
    t = _multi_task([a], volume=0)

    await t.task._tick()

    assert a.on_price.await_args.kwargs["volume"] is None


@pytest.mark.asyncio
async def test_one_failing_strategy_does_not_stop_the_others():
    a = _strategy("A", ["AAA"])
    a.on_price = AsyncMock(side_effect=RuntimeError("전략 폭발"))
    b = _strategy("B", ["AAA"])
    t = _multi_task([a, b])

    await t.task._tick()

    b.on_price.assert_awaited_once()
    t.task._logger.warning.assert_called()


@pytest.mark.asyncio
async def test_failing_prepare_session_does_not_stop_the_others():
    a = _strategy("A", ["AAA"])
    a.prepare_session = AsyncMock(side_effect=RuntimeError("세션 준비 실패"))
    a.watch_codes = MagicMock(return_value=[])
    b = _strategy("B", ["BBB"])
    t = _multi_task([a, b])

    await t.task._tick()

    b.on_price.assert_awaited_once()


@pytest.mark.asyncio
async def test_eod_closes_every_strategy():
    a = _strategy("A", ["AAA"])
    b = _strategy("B", ["BBB"])
    t = _multi_task([a, b], now=_ny(15, 55))

    await t.task._tick()

    a.close_all.assert_awaited_once()
    b.close_all.assert_awaited_once()
    a.on_price.assert_not_awaited()


def test_progress_reports_wired_strategies():
    t = _multi_task([_strategy("A", ["AAA"]), _strategy("B", ["BBB"])])
    assert t.task.get_progress()["strategies"] == ["A", "B"]


def test_task_name_is_strategy_agnostic():
    t = _multi_task([_strategy("A", ["AAA"])])
    assert t.task.task_name == "overseas_intraday"
