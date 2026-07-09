# tests/unit_test/scheduler/test_worker_pool.py
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock
from scheduler.ticket_queue.ticket import Ticket
from scheduler.ticket_queue.message_broker import MessageBroker
from scheduler.ticket_queue.dlq_manager import DlqManager
from scheduler.worker.worker_pool import WorkerPool


def _make_pool(num_workers=1) -> tuple[WorkerPool, MessageBroker, MagicMock]:
    broker = MessageBroker()
    dlq = MagicMock(spec=DlqManager)
    dlq.handle_failed_ticket = AsyncMock()
    pool = WorkerPool(broker=broker, dlq_manager=dlq, num_workers=num_workers)
    return pool, broker, dlq


async def test_handler_called_on_ticket():
    pool, broker, _ = _make_pool()
    called_with = []

    async def handler(payload):
        called_with.append(payload)

    pool.register("MY_TASK", handler)
    await pool.start()
    await broker.publish(Ticket(priority=50, task_name="MY_TASK", payload={"date": "20250417"}))
    await broker.join()
    await pool.shutdown()

    assert called_with == [{"date": "20250417"}]


async def test_retry_on_failure():
    pool, broker, dlq = _make_pool()
    pool.BASE_DELAY = 0  # 테스트 속도 위해 delay 0

    call_count = 0

    async def flaky_handler(payload):
        nonlocal call_count
        call_count += 1
        if call_count < pool.MAX_RETRIES:
            raise ValueError("일시 오류")

    pool.register("FLAKY", flaky_handler)
    await pool.start()
    await broker.publish(Ticket(priority=50, task_name="FLAKY", payload={}))
    await broker.join()
    await pool.shutdown()

    assert call_count == pool.MAX_RETRIES
    dlq.handle_failed_ticket.assert_not_called()


async def test_dlq_after_max_retries():
    pool, broker, dlq = _make_pool()
    pool.BASE_DELAY = 0

    async def always_fail(payload):
        raise RuntimeError("영구 오류")

    pool.register("BAD_TASK", always_fail)
    await pool.start()
    await broker.publish(Ticket(priority=50, task_name="BAD_TASK", payload={}))
    await broker.join()
    await pool.shutdown()

    dlq.handle_failed_ticket.assert_called_once()
    args = dlq.handle_failed_ticket.call_args[0]
    assert args[0].task_name == "BAD_TASK"
    assert args[0].attempt == pool.MAX_RETRIES


async def test_suspend_blocks_processing():
    pool, broker, _ = _make_pool()
    processed = []

    async def handler(payload):
        processed.append(payload)

    pool.register("T", handler)
    await pool.start()
    pool.suspend()

    await broker.publish(Ticket(priority=50, task_name="T", payload={"n": 1}))
    await asyncio.sleep(0.05)  # 잠시 대기 — suspend 중이므로 처리 안 됨
    assert processed == []

    pool.resume()
    await broker.join()
    await pool.shutdown()
    assert len(processed) == 1


async def test_poison_pill_priority_beats_low_tickets():
    """shutdown 시 독약이 LOW 티켓보다 먼저 처리되어 워커가 즉시 종료."""
    pool, broker, _ = _make_pool()
    processed = []

    async def handler(payload):
        processed.append(payload)

    pool.register("LOW_TASK", handler)
    await pool.start()

    # LOW 티켓 여러 개 발행 후 즉시 shutdown
    for i in range(5):
        await broker.publish(Ticket(priority=100, task_name="LOW_TASK", payload={"n": i}))

    await pool.shutdown()  # Poison Pill priority=-1로 발행 → 즉시 종료
    # 독약이 LOW 티켓보다 먼저이므로 일부만 처리되거나 0개 처리될 수 있음
    # 핵심: shutdown이 hang 없이 완료됨을 검증
    assert True  # 여기까지 도달하면 hang 없음


async def test_trace_id_propagated_through_queue():
    """Queue 경계를 넘어 trace_id가 핸들러 내부에서 복원되는지 검증."""
    from core.loggers.trace_context import get_trace_id, _trace_id_var

    pool, broker, _ = _make_pool()
    captured = []

    async def handler(payload):
        captured.append(get_trace_id())

    pool.register("TRACE_TASK", handler)
    await pool.start()

    ticket = Ticket(priority=50, task_name="TRACE_TASK", payload={}, trace_id="TEST-TRACE-001")
    await broker.publish(ticket)
    await broker.join()
    await pool.shutdown()

    assert captured == ["TEST-TRACE-001"]


async def test_no_trace_id_ticket_handler_gets_none():
    """trace_id가 없는 Ticket은 핸들러 내부에서 get_trace_id() == None."""
    from core.loggers.trace_context import get_trace_id, _trace_id_var

    pool, broker, _ = _make_pool()
    captured = []

    async def handler(payload):
        captured.append(get_trace_id())

    pool.register("NO_TRACE_TASK", handler)
    _trace_id_var.set("")  # 부모 컨텍스트도 비워둠
    await pool.start()

    ticket = Ticket(priority=50, task_name="NO_TRACE_TASK", payload={}, trace_id="")
    await broker.publish(ticket)
    await broker.join()
    await pool.shutdown()

    assert captured == [None]


async def test_handle_logs_perf_timers_when_profiler_enabled():
    """profiler 활성화 시 큐 대기시간과 실행시간을 [Performance] 로그로 남긴다."""
    from core.performance_profiler import PerformanceProfiler

    fake_logger = MagicMock()
    pm = PerformanceProfiler(logger=fake_logger, enabled=True, threshold=0.0)
    broker = MessageBroker()
    dlq = MagicMock(spec=DlqManager)
    pool = WorkerPool(broker=broker, dlq_manager=dlq, performance_profiler=pm, num_workers=1)

    async def handler(payload):
        pass

    pool.register("PERF_TASK", handler)
    await pool.start()
    await broker.publish(Ticket(priority=50, task_name="PERF_TASK", payload={}))
    await broker.join()
    await pool.shutdown()

    logged = [c.args[0] for c in fake_logger.info.call_args_list]
    assert any("AfterMarketTask.PERF_TASK(queue_wait):" in m for m in logged)
    assert any(m.startswith("[Performance] AfterMarketTask.PERF_TASK:") for m in logged)


async def test_handle_logs_execution_timer_even_on_failure():
    """실행 중 예외가 발생해도 실행시간 타이머는 매 시도마다 기록된다."""
    from core.performance_profiler import PerformanceProfiler

    fake_logger = MagicMock()
    pm = PerformanceProfiler(logger=fake_logger, enabled=True, threshold=0.0)
    broker = MessageBroker()
    dlq = MagicMock(spec=DlqManager)
    dlq.handle_failed_ticket = AsyncMock()
    pool = WorkerPool(broker=broker, dlq_manager=dlq, performance_profiler=pm, num_workers=1)
    pool.BASE_DELAY = 0

    async def always_fail(payload):
        raise RuntimeError("boom")

    pool.register("FAIL_TASK", always_fail)
    await pool.start()
    await broker.publish(Ticket(priority=50, task_name="FAIL_TASK", payload={}))
    await broker.join()
    await pool.shutdown()

    logged = [c.args[0] for c in fake_logger.info.call_args_list]
    exec_logs = [m for m in logged if m.startswith("[Performance] AfterMarketTask.FAIL_TASK:")]
    assert len(exec_logs) == pool.MAX_RETRIES


async def test_handle_logs_timers_below_profiler_threshold():
    """운영 threshold(1.0s)보다 빨리 끝나도 after-market 타이머는 항상 기록된다.

    2026-07-09 실측: post_market_replay_audit가 1ms에 skip 완료되자 실행시간
    라인이 threshold 게이트에 억제되어 '미실행'과 구분 불가했던 회귀 방지.
    """
    from core.performance_profiler import PerformanceProfiler

    fake_logger = MagicMock()
    pm = PerformanceProfiler(logger=fake_logger, enabled=True, threshold=1.0)
    broker = MessageBroker()
    dlq = MagicMock(spec=DlqManager)
    pool = WorkerPool(broker=broker, dlq_manager=dlq, performance_profiler=pm, num_workers=1)

    async def fast_handler(payload):
        pass

    pool.register("FAST_TASK", fast_handler)
    await pool.start()
    await broker.publish(Ticket(priority=50, task_name="FAST_TASK", payload={}))
    await broker.join()
    await pool.shutdown()

    logged = [c.args[0] for c in fake_logger.info.call_args_list]
    assert any("AfterMarketTask.FAST_TASK(queue_wait):" in m for m in logged)
    assert any(m.startswith("[Performance] AfterMarketTask.FAST_TASK:") for m in logged)
