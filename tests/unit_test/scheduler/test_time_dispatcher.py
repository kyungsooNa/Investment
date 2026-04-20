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


def _make_clock(*, is_operating: bool, is_after_close: bool, weekday: int = 1):
    """MarketClock mock 생성.

    is_after_close=True  → 장 마감 후 (정상 발행 시간)
    is_after_close=False → 장 전 오전 (발행 차단)
    weekday: 0=월 ~ 4=금, 5=토, 6=일
    """
    clock = MagicMock()
    clock.is_market_operating_hours.return_value = is_operating

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
):
    broker = MessageBroker()
    market_clock = _make_clock(
        is_operating=is_operating, is_after_close=is_after_close, weekday=weekday
    )
    mcs = MagicMock()
    mcs.get_latest_trading_date = AsyncMock(return_value=latest_date)
    dispatcher = TimeDispatcher(
        broker=broker, market_clock=market_clock, mcs=mcs, db_path=db_path
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

async def test_ticket_allowed_on_weekend(db_path):
    """주말에는 장 마감 시각 체크 없이 직전 거래일 티켓 발행이 허용된다."""
    dispatcher, broker = _make_dispatcher(
        is_operating=False, latest_date="20250418", db_path=db_path,
        is_after_close=False, weekday=5,  # 토요일
    )
    dispatcher.register_task("RANKING_UPDATE", priority=100)

    await dispatcher._maybe_dispatch()
    await _drain(dispatcher)

    assert broker.qsize == 1


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
