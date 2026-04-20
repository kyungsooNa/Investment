# tests/unit_test/scheduler/test_time_dispatcher.py
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock
from scheduler.ticket_queue.message_broker import MessageBroker
from scheduler.dispatcher.time_dispatcher import TimeDispatcher


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "dispatcher_state.db")


def _make_dispatcher(is_operating: bool, latest_date: str | None, db_path: str):
    broker = MessageBroker()
    market_clock = MagicMock()
    market_clock.is_market_operating_hours.return_value = is_operating
    mcs = MagicMock()
    mcs.get_latest_trading_date = AsyncMock(return_value=latest_date)
    dispatcher = TimeDispatcher(broker=broker, market_clock=market_clock, mcs=mcs, db_path=db_path)
    return dispatcher, broker


async def _drain(dispatcher) -> None:
    """pending_publish_tasks가 모두 완료될 때까지 대기한다."""
    pending = list(dispatcher._pending_publish_tasks)
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


# ── 기존 동작 TC ────────────────────────────────────────────────────────────

async def test_no_ticket_during_market_hours(db_path):
    dispatcher, broker = _make_dispatcher(is_operating=True, latest_date="20250417", db_path=db_path)
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


# ── SQLite 영속화 TC ─────────────────────────────────────────────────────────

async def test_state_updated_in_memory_after_dispatch(db_path):
    """발행 후 _task_dispatched_dates가 즉시 갱신된다."""
    dispatcher, _ = _make_dispatcher(is_operating=False, latest_date="20250417", db_path=db_path)
    dispatcher.register_task("RANKING_UPDATE", priority=100)

    assert dispatcher._task_dispatched_dates.get("RANKING_UPDATE") is None
    await dispatcher._maybe_dispatch()
    assert dispatcher._task_dispatched_dates.get("RANKING_UPDATE") == "20250417"


async def test_last_dispatched_date_persisted_to_db(db_path):
    """발행 후 거래일이 태스크별로 SQLite에 저장되어 새 인스턴스에서 복원된다."""
    dispatcher, broker = _make_dispatcher(is_operating=False, latest_date="20250417", db_path=db_path)
    dispatcher.register_task("RANKING_UPDATE", priority=100)
    await dispatcher._maybe_dispatch()
    await _drain(dispatcher)
    assert broker.qsize == 1

    # 새 인스턴스에서 같은 DB를 열면 날짜가 복원됨
    dispatcher2, _ = _make_dispatcher(is_operating=False, latest_date="20250417", db_path=db_path)
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


async def test_initial_load_empty_when_db_is_empty(db_path):
    """DB가 비어 있으면 _task_dispatched_dates는 빈 딕셔너리로 초기화된다."""
    dispatcher, _ = _make_dispatcher(is_operating=False, latest_date="20250417", db_path=db_path)
    assert dispatcher._task_dispatched_dates == {}


# ── 태스크별 개별 발행 이력 TC ───────────────────────────────────────────────

async def test_partial_restart_dispatches_only_pending_tasks(db_path):
    """재시작 전 일부 태스크만 발행된 경우, 미발행 태스크만 재시작 후 발행된다."""
    # TASK_A는 이미 발행 성공 → DB에 저장됨, TASK_B는 미발행
    dispatcher_prep, _ = _make_dispatcher(is_operating=False, latest_date="20250417", db_path=db_path)
    dispatcher_prep._save_state("TASK_A", "20250417")

    # 재시작 시뮬레이션
    dispatcher2, broker2 = _make_dispatcher(is_operating=False, latest_date="20250417", db_path=db_path)
    dispatcher2.register_task("TASK_A", priority=100)
    dispatcher2.register_task("TASK_B", priority=100)

    await dispatcher2._maybe_dispatch()
    await _drain(dispatcher2)

    assert broker2.qsize == 1
    ticket = await broker2.consume()
    broker2.task_done()
    assert ticket.task_name == "TASK_B"


async def test_each_task_tracked_independently(db_path):
    """태스크별 발행 이력이 독립적으로 관리된다."""
    dispatcher, broker = _make_dispatcher(is_operating=False, latest_date="20250417", db_path=db_path)
    dispatcher.register_task("TASK_A", priority=100)
    dispatcher.register_task("TASK_B", priority=90)

    await dispatcher._maybe_dispatch()
    await _drain(dispatcher)

    assert broker.qsize == 2
    assert dispatcher._task_dispatched_dates.get("TASK_A") == "20250417"
    assert dispatcher._task_dispatched_dates.get("TASK_B") == "20250417"


async def test_new_task_registered_after_dispatch_gets_ticket_on_next_date(db_path):
    """기존 태스크 발행 후, 새로 등록된 태스크는 다음 거래일에 정상 발행된다."""
    dispatcher, _ = _make_dispatcher(is_operating=False, latest_date="20250416", db_path=db_path)
    dispatcher.register_task("TASK_A", priority=100)
    await dispatcher._maybe_dispatch()
    await _drain(dispatcher)

    # 재시작 후 새 거래일 + 새 태스크 추가
    dispatcher2, broker2 = _make_dispatcher(is_operating=False, latest_date="20250417", db_path=db_path)
    dispatcher2.register_task("TASK_A", priority=100)
    dispatcher2.register_task("TASK_B", priority=90)  # 새로 추가된 태스크

    await dispatcher2._maybe_dispatch()
    await _drain(dispatcher2)

    assert broker2.qsize == 2
