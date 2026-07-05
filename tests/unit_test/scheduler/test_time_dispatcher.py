# tests/unit_test/scheduler/test_time_dispatcher.py
import asyncio
from datetime import datetime
import pytest
from unittest.mock import AsyncMock, MagicMock
from scheduler.ticket_queue.message_broker import MessageBroker
from scheduler.dispatcher.time_dispatcher import TimeDispatcher


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "dispatcher_state.db")


def _make_clock(*, is_operating: bool, is_after_close: bool, weekday: int = 1, date_str: str = "20250417"):
    """MarketClock mock 생성.

    is_after_close=True  → 장 마감 후 (정상 발행 시간)
    is_after_close=False → 장 전 오전 (발행 차단)
    weekday: 0=월 ~ 4=금, 5=토, 6=일
    date_str: get_current_kst_date_str() 반환값 (mcs=None fallback 거래일 식별자)
    """
    clock = MagicMock()
    clock.is_market_operating_hours.return_value = is_operating
    clock.get_current_kst_date_str.return_value = date_str

    now_mock = MagicMock(spec=datetime)
    now_mock.weekday.return_value = weekday
    clock.get_current_kst_time.return_value = now_mock

    # get_seconds_until_market_close: 음수 → 마감 후, 양수 → 마감 전
    clock.get_seconds_until_market_close.return_value = -60 if is_after_close else 3600
    return clock


def _make_dispatcher(
    is_operating: bool,
    latest_date: str | None,
    db_path: str,
    *,
    is_after_close: bool = True,
    weekday: int = 1,
    date_str: str = "20250417",
):
    broker = MessageBroker()
    market_clock = _make_clock(
        is_operating=is_operating, is_after_close=is_after_close,
        weekday=weekday, date_str=date_str,
    )
    mcs = MagicMock()
    mcs.get_latest_trading_date = AsyncMock(return_value=latest_date)
    logger = MagicMock()
    dispatcher = TimeDispatcher(
        broker=broker, market_clock=market_clock, mcs=mcs, logger=logger, db_path=db_path
    )
    return dispatcher, broker


async def _drain(dispatcher) -> None:
    """pending_publish_tasks가 모두 완료될 때까지 대기한다."""
    pending = list(dispatcher._pending_publish_tasks)
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


# ── 기본 동작 TC ─────────────────────────────────────────────────────────────

async def test_no_ticket_during_market_hours(db_path):
    dispatcher, broker = _make_dispatcher(is_operating=True, latest_date="20250417", db_path=db_path)
    dispatcher.register_task("RANKING_UPDATE", priority=100)

    await dispatcher._maybe_dispatch()
    assert broker.empty is True


async def test_no_ticket_before_market_open(db_path):
    """장 전 오전(is_after_close=False)에는 티켓을 발행하지 않는다."""
    dispatcher, broker = _make_dispatcher(
        is_operating=False, latest_date="20250417", db_path=db_path,
        is_after_close=False, weekday=1,  # 화요일 오전
    )
    dispatcher.register_task("RANKING_UPDATE", priority=100)

    await dispatcher._maybe_dispatch()
    assert broker.empty is True


async def test_ticket_published_after_market_close(db_path):
    dispatcher, broker = _make_dispatcher(is_operating=False, latest_date="20250417", db_path=db_path)
    dispatcher.register_task("RANKING_UPDATE", priority=100)

    await dispatcher._maybe_dispatch()
    await _drain(dispatcher)

    assert broker.qsize == 1
    ticket = await broker.consume()
    broker.task_done()
    assert ticket.task_name == "RANKING_UPDATE"
    assert ticket.payload["date"] == "20250417"


async def test_no_duplicate_ticket_same_date(db_path):
    dispatcher, broker = _make_dispatcher(is_operating=False, latest_date="20250417", db_path=db_path)
    dispatcher.register_task("RANKING_UPDATE", priority=100)

    await dispatcher._maybe_dispatch()
    await _drain(dispatcher)
    await dispatcher._maybe_dispatch()  # 같은 날짜 — 중복 발행 안 됨
    await _drain(dispatcher)

    assert broker.qsize == 1


async def test_no_ticket_when_mcs_returns_none(db_path):
    dispatcher, broker = _make_dispatcher(is_operating=False, latest_date=None, db_path=db_path)
    dispatcher.register_task("RANKING_UPDATE", priority=100)

    await dispatcher._maybe_dispatch()
    assert broker.empty is True


async def test_stop_exits_run_loop(db_path):
    """stop()이 호출되면 POLL_INTERVAL 대기 중에도 즉시 루프를 빠져나온다."""
    dispatcher, _ = _make_dispatcher(is_operating=True, latest_date="20250417", db_path=db_path)
    dispatcher.POLL_INTERVAL = 3600  # 긴 sleep — stop()이 즉시 깨워야 함

    run_task = asyncio.create_task(dispatcher.run())
    await asyncio.sleep(0)  # 루프가 시작되도록 한 번 yield
    dispatcher.stop()
    await asyncio.wait_for(run_task, timeout=1.0)
    assert True  # hang 없이 종료됨


# ── 장전 오전 실행 방지 TC ───────────────────────────────────────────────────

async def test_no_ticket_on_non_trading_day_even_if_previous_trading_date_missing(db_path):
    """mcs 주입 시 주말/휴장일에는 직전 거래일 티켓을 새로 발행하지 않는다."""
    dispatcher, broker = _make_dispatcher(
        is_operating=False, latest_date="20250418", db_path=db_path,
        is_after_close=False, weekday=5, date_str="20250419",  # 토요일
    )
    dispatcher.register_task("RANKING_UPDATE", priority=100)

    await dispatcher._maybe_dispatch()
    await _drain(dispatcher)

    assert broker.empty is True


async def test_us_holiday_20260703_skips_ticket_and_logs_clear_reason(db_path):
    """2026-07-03 미국 독립기념일 관측휴장에는 7/2 기준 dry-run을 발행하지 않는다."""
    dispatcher, broker = _make_dispatcher(
        is_operating=False,
        latest_date="20260702",
        db_path=db_path,
        is_after_close=True,
        weekday=4,  # 금요일
        date_str="20260703",
    )
    dispatcher.register_task("overseas_vbo_dryrun", priority=100)

    await dispatcher._maybe_dispatch()
    await _drain(dispatcher)

    assert broker.empty is True
    dispatcher._logger.info.assert_any_call(
        "[TimeDispatcher] 오늘(20260703)은 휴장일/비거래일입니다 — "
        "티켓 발행 스킵 (최근 거래일=20260702)"
    )


async def test_us_resume_20260706_dispatches_new_market_date(db_path):
    """2026-07-06 미국장 재개일에는 새 거래일로 dry-run 티켓을 발행한다."""
    dispatcher, broker = _make_dispatcher(
        is_operating=False,
        latest_date="20260706",
        db_path=db_path,
        is_after_close=True,
        weekday=0,  # 월요일
        date_str="20260706",
    )
    dispatcher.register_task("overseas_vbo_dryrun", priority=100)

    await dispatcher._maybe_dispatch()
    await _drain(dispatcher)

    assert broker.qsize == 1
    ticket = await broker.consume()
    broker.task_done()
    assert ticket.task_name == "overseas_vbo_dryrun"
    assert ticket.payload["date"] == "20260706"


# ── SQLite 영속화 TC ─────────────────────────────────────────────────────────

async def test_state_updated_in_memory_after_dispatch(db_path):
    """발행 후 _task_dispatched_dates가 task별로 갱신된다."""
    dispatcher, _ = _make_dispatcher(is_operating=False, latest_date="20250417", db_path=db_path)
    dispatcher.register_task("RANKING_UPDATE", priority=100)

    assert dispatcher._task_dispatched_dates.get("RANKING_UPDATE") is None
    await dispatcher._maybe_dispatch()
    assert dispatcher._task_dispatched_dates.get("RANKING_UPDATE") == "20250417"


async def test_last_dispatched_date_persisted_to_db(db_path):
    """발행 후 거래일이 task별로 SQLite에 저장되어 새 인스턴스에서 복원된다."""
    dispatcher, broker = _make_dispatcher(is_operating=False, latest_date="20250417", db_path=db_path)
    dispatcher.register_task("RANKING_UPDATE", priority=100)
    await dispatcher._maybe_dispatch()
    await _drain(dispatcher)
    assert broker.qsize == 1

    # 새 인스턴스에서 같은 DB를 열고 task 등록 → DB에서 날짜 복원
    dispatcher2, _ = _make_dispatcher(is_operating=False, latest_date="20250417", db_path=db_path)
    dispatcher2.register_task("RANKING_UPDATE", priority=100)
    assert dispatcher2._task_dispatched_dates.get("RANKING_UPDATE") == "20250417"


async def test_no_duplicate_ticket_after_restart(db_path):
    """재시작 후에도 같은 거래일에는 티켓을 중복 발행하지 않는다."""
    dispatcher, _ = _make_dispatcher(is_operating=False, latest_date="20250417", db_path=db_path)
    dispatcher.register_task("RANKING_UPDATE", priority=100)
    await dispatcher._maybe_dispatch()
    await _drain(dispatcher)

    # 재시작 시뮬레이션: 같은 DB로 새 인스턴스 생성
    dispatcher2, broker2 = _make_dispatcher(is_operating=False, latest_date="20250417", db_path=db_path)
    dispatcher2.register_task("RANKING_UPDATE", priority=100)
    await dispatcher2._maybe_dispatch()
    await _drain(dispatcher2)

    assert broker2.qsize == 0  # 재시작 후 같은 날 중복 발행 없음


async def test_new_date_dispatched_after_restart(db_path):
    """이전 거래일이 DB에 있어도, 새 거래일에는 정상 발행된다."""
    dispatcher, _ = _make_dispatcher(is_operating=False, latest_date="20250416", db_path=db_path)
    dispatcher.register_task("RANKING_UPDATE", priority=100)
    await dispatcher._maybe_dispatch()
    await _drain(dispatcher)

    # 재시작 후 새 거래일
    dispatcher2, broker2 = _make_dispatcher(is_operating=False, latest_date="20250417", db_path=db_path)
    dispatcher2.register_task("RANKING_UPDATE", priority=100)
    await dispatcher2._maybe_dispatch()
    await _drain(dispatcher2)

    assert broker2.qsize == 1
    ticket = await broker2.consume()
    broker2.task_done()
    assert ticket.payload["date"] == "20250417"


async def test_initial_load_none_when_db_is_empty(db_path):
    """DB가 비어 있으면 task의 _task_dispatched_dates는 None으로 초기화된다."""
    dispatcher, _ = _make_dispatcher(is_operating=False, latest_date="20250417", db_path=db_path)
    dispatcher.register_task("RANKING_UPDATE", priority=100)
    assert dispatcher._task_dispatched_dates.get("RANKING_UPDATE") is None


# ── task별 독립 티켓 승계 TC ─────────────────────────────────────────────────

async def test_only_undispatched_tasks_get_ticket_after_restart(db_path):
    """task A는 발행 완료, task B는 미발행 상태에서 재시작 시 B만 티켓을 받는다."""
    dispatcher, _ = _make_dispatcher(is_operating=False, latest_date="20250417", db_path=db_path)
    dispatcher.register_task("TASK_A", priority=100)
    await dispatcher._maybe_dispatch()  # A 발행 → DB 저장
    await _drain(dispatcher)

    # 재시작: A는 DB에 있음, B는 DB에 없음
    dispatcher2, broker2 = _make_dispatcher(is_operating=False, latest_date="20250417", db_path=db_path)
    dispatcher2.register_task("TASK_A", priority=100)  # DB에서 복원 → already dispatched
    dispatcher2.register_task("TASK_B", priority=100)  # DB에 없음 → 미발행

    await dispatcher2._maybe_dispatch()
    await _drain(dispatcher2)

    assert broker2.qsize == 1
    ticket = await broker2.consume()
    broker2.task_done()
    assert ticket.task_name == "TASK_B"


async def test_all_tasks_dispatched_independently(db_path):
    """여러 task가 모두 미발행이면 전부 티켓을 받는다."""
    dispatcher, broker = _make_dispatcher(is_operating=False, latest_date="20250417", db_path=db_path)
    dispatcher.register_task("TASK_A", priority=100)
    dispatcher.register_task("TASK_B", priority=50)

    await dispatcher._maybe_dispatch()
    await _drain(dispatcher)

    assert broker.qsize == 2
    names = set()
    for _ in range(2):
        t = await broker.consume()
        broker.task_done()
        names.add(t.task_name)
    assert names == {"TASK_A", "TASK_B"}


async def test_partial_dispatch_then_second_task_added(db_path):
    """첫 실행에서 task A만 등록·발행 후, 재시작 시 task B가 추가되면 B만 티켓을 받는다."""
    # 첫 실행: TASK_A만 등록
    dispatcher, _ = _make_dispatcher(is_operating=False, latest_date="20250417", db_path=db_path)
    dispatcher.register_task("TASK_A", priority=100)
    await dispatcher._maybe_dispatch()
    await _drain(dispatcher)

    # 재시작: TASK_A + TASK_B 등록 (TASK_B는 DB에 없음)
    dispatcher2, broker2 = _make_dispatcher(is_operating=False, latest_date="20250417", db_path=db_path)
    dispatcher2.register_task("TASK_A", priority=100)
    dispatcher2.register_task("TASK_B", priority=100)

    await dispatcher2._maybe_dispatch()
    await _drain(dispatcher2)

    assert broker2.qsize == 1
    ticket = await broker2.consume()
    broker2.task_done()
    assert ticket.task_name == "TASK_B"


async def test_unregister_task_removes_registered_state(db_path):
    """unregister_task()는 등록 정보와 발행일 상태를 모두 제거한다."""
    dispatcher, _ = _make_dispatcher(is_operating=False, latest_date="20250417", db_path=db_path)
    dispatcher.register_task("TASK_A", priority=100, delay_sec=15)

    dispatcher.unregister_task("TASK_A")

    assert "TASK_A" not in dispatcher._task_schedule
    assert "TASK_A" not in dispatcher._task_delays
    assert "TASK_A" not in dispatcher._task_dispatched_dates


async def test_publish_after_delay_warns_when_broker_queue_is_full(db_path):
    """broker.publish()가 False면 큐 포화 경고를 남긴다."""
    broker = MagicMock()
    broker.publish = AsyncMock(return_value=False)
    dispatcher = TimeDispatcher(
        broker=broker,
        market_clock=_make_clock(is_operating=False, is_after_close=True),
        mcs=MagicMock(),
        logger=MagicMock(),
        db_path=db_path,
    )

    await dispatcher._publish_after_delay("TASK_A", priority=100, date="20250417", delay_sec=0)

    broker.publish.assert_awaited_once()
    dispatcher._logger.warning.assert_called_once()


async def test_publish_after_delay_waits_before_publishing(db_path, monkeypatch):
    """delay_sec가 있으면 지정한 시간만큼 대기 후 발행한다."""
    broker = MagicMock()
    broker.publish = AsyncMock(return_value=True)
    dispatcher = TimeDispatcher(
        broker=broker,
        market_clock=_make_clock(is_operating=False, is_after_close=True),
        mcs=MagicMock(),
        logger=MagicMock(),
        db_path=db_path,
    )
    sleep_calls = []

    async def fake_sleep(delay):
        sleep_calls.append(delay)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    await dispatcher._publish_after_delay("TASK_A", priority=50, date="20250417", delay_sec=3)

    assert sleep_calls == [3]
    broker.publish.assert_awaited_once()


def test_get_status_handles_market_clock_exception(db_path):
    """get_status()는 MarketClock 조회 예외가 발생해도 상태 반환을 유지한다."""
    dispatcher, _ = _make_dispatcher(is_operating=False, latest_date="20250417", db_path=db_path)
    dispatcher.register_task("TASK_A", priority=100, delay_sec=7)
    dispatcher._task_dispatched_dates["TASK_A"] = "20250416"
    dispatcher._market_clock.is_market_operating_hours.side_effect = RuntimeError("clock error")

    status = dispatcher.get_status()

    assert status["market_is_open"] is None
    assert status["registered_tasks"] == [
        {
            "name": "TASK_A",
            "priority": 100,
            "delay_sec": 7,
            "last_dispatched_date": "20250416",
        }
    ]


# ── mcs=None (해외장 등 거래 캘린더 미주입) TC ──────────────────────────────

def _make_dispatcher_no_mcs(
    is_operating: bool,
    db_path: str,
    *,
    is_after_close: bool = True,
    weekday: int = 1,
    date_str: str = "20250417",
):
    """mcs=None 인 dispatcher (미국장 등). 거래일 식별자는 clock 날짜로 대체된다."""
    broker = MessageBroker()
    market_clock = _make_clock(
        is_operating=is_operating, is_after_close=is_after_close,
        weekday=weekday, date_str=date_str,
    )
    dispatcher = TimeDispatcher(
        broker=broker, market_clock=market_clock, mcs=None, db_path=db_path
    )
    return dispatcher, broker


async def test_no_mcs_publishes_after_close_using_clock_date(db_path):
    """mcs=None이면 clock 날짜를 거래일 식별자로 사용해 마감 후 발행한다."""
    dispatcher, broker = _make_dispatcher_no_mcs(
        is_operating=False, db_path=db_path, date_str="20250417"
    )
    dispatcher.register_task("OVERSEAS_DRYRUN", priority=100)

    await dispatcher._maybe_dispatch()
    await _drain(dispatcher)

    assert broker.qsize == 1
    ticket = await broker.consume()
    broker.task_done()
    assert ticket.task_name == "OVERSEAS_DRYRUN"
    assert ticket.payload["date"] == "20250417"


async def test_no_mcs_no_ticket_during_market_hours(db_path):
    """mcs=None이어도 장중에는 발행하지 않는다."""
    dispatcher, broker = _make_dispatcher_no_mcs(is_operating=True, db_path=db_path)
    dispatcher.register_task("OVERSEAS_DRYRUN", priority=100)

    await dispatcher._maybe_dispatch()
    assert broker.empty is True


async def test_no_mcs_no_ticket_before_close(db_path):
    """mcs=None이어도 평일 장 마감 전에는 발행하지 않는다."""
    dispatcher, broker = _make_dispatcher_no_mcs(
        is_operating=False, db_path=db_path, is_after_close=False, weekday=1
    )
    dispatcher.register_task("OVERSEAS_DRYRUN", priority=100)

    await dispatcher._maybe_dispatch()
    assert broker.empty is True


async def test_no_mcs_no_duplicate_same_date(db_path):
    """mcs=None이어도 같은 clock 날짜에는 중복 발행하지 않는다."""
    dispatcher, broker = _make_dispatcher_no_mcs(
        is_operating=False, db_path=db_path, date_str="20250417"
    )
    dispatcher.register_task("OVERSEAS_DRYRUN", priority=100)

    await dispatcher._maybe_dispatch()
    await _drain(dispatcher)
    await dispatcher._maybe_dispatch()
    await _drain(dispatcher)

    assert broker.qsize == 1


async def test_stop_cancels_pending_publish_tasks(db_path):
    """stop()은 대기 중인 발행 태스크를 취소한다."""
    dispatcher, _ = _make_dispatcher(is_operating=False, latest_date="20250417", db_path=db_path)
    dispatcher.register_task("TASK_A", priority=100, delay_sec=10)

    await dispatcher._maybe_dispatch()
    pending = list(dispatcher._pending_publish_tasks)
    assert len(pending) == 1

    dispatcher.stop()
    results = await asyncio.gather(*pending, return_exceptions=True)

    assert any(isinstance(result, asyncio.CancelledError) for result in results)
