# tests/unit_test/test_api_request_queue.py

import asyncio
from contextlib import asynccontextmanager
import pytest
from unittest.mock import MagicMock, AsyncMock

from common.types import ResCommonResponse, ErrorCode
from core.retry_queue.api_request_queue import ApiRequestQueue, QueuedRequest


def success_resp() -> ResCommonResponse:
    return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="정상", data={})


def network_fail_resp() -> ResCommonResponse:
    """재시도 가능한 실패 (RETRY)"""
    return ResCommonResponse(rt_cd=ErrorCode.NETWORK_ERROR.value, msg1="네트워크 오류", data=None)


def business_fail_resp() -> ResCommonResponse:
    """재시도 불가능한 실패 (FAIL)"""
    return ResCommonResponse(rt_cd=ErrorCode.MARKET_CLOSED.value, msg1="장 마감", data=None)


@pytest.fixture
def logger():
    return MagicMock()


@pytest.fixture
def queue(logger):
    return ApiRequestQueue(logger=logger)


class FakeBudgetLimiter:
    @asynccontextmanager
    async def acquire(self, category, **kwargs):
        yield


async def test_execute_emits_budget_wait_timer(logger):
    """[S3분해] budget 경유 실행 시 RQBudget.{category} 타이머를 기록한다."""
    mock_pm = MagicMock()
    queue = ApiRequestQueue(logger=logger, budget_limiter=FakeBudgetLimiter(),
                            performance_profiler=mock_pm)
    fn = AsyncMock(return_value=success_resp())
    future = await queue.submit(fn, "005930", request_id="ohlcv",
                                request_category="quotation_ohlcv")
    await future
    names = [c.args[0] for c in mock_pm.log_timer.call_args_list]
    assert "RQBudget.quotation_ohlcv" in names


async def test_delay_and_execute_emits_retry_delay_timer(logger):
    """[S3분해] 재시도 백오프 대기 시 RQRetryDelay.{id} 타이머를 기록한다."""
    mock_pm = MagicMock()
    queue = ApiRequestQueue(logger=logger, performance_profiler=mock_pm)
    fut = asyncio.get_event_loop().create_future()
    req = QueuedRequest(fn=AsyncMock(return_value=success_resp()), args=(), kwargs={},
                        future=fut, request_id="get_price")
    await queue._delay_and_execute(req, 0.0)
    names = [c.args[0] for c in mock_pm.log_timer.call_args_list]
    assert "RQRetryDelay.get_price" in names


class TestSubmitSuccess:
    async def test_immediate_success_resolves_future(self, queue):
        fn = AsyncMock(return_value=success_resp())
        future = await queue.submit(fn, "005930", request_id="get_price")
        result = await future

        assert result.rt_cd == ErrorCode.SUCCESS.value
        fn.assert_called_once_with("005930")

    async def test_success_added_to_done_queue(self, queue):
        fn = AsyncMock(return_value=success_resp())
        future = await queue.submit(fn, request_id="get_price")
        await future

        assert queue.done_queue.qsize() == 1
        assert queue.fail_queue.empty()
        req, result = await queue.done_queue.get()
        assert req.request_id == "get_price"
        assert result.rt_cd == ErrorCode.SUCCESS.value

    async def test_pending_count_zero_after_completion(self, queue):
        fn = AsyncMock(return_value=success_resp())
        future = await queue.submit(fn, request_id="test")
        tasks = list(queue._pending_tasks)
        await future
        # task 자체가 완료될 때까지 대기 (done_callback이 pending_tasks에서 제거)
        await asyncio.gather(*tasks, return_exceptions=True)

        assert queue.pending_count == 0


class TestSubmitNonRetriableFail:
    async def test_non_retriable_fail_resolves_immediately(self, queue):
        fn = AsyncMock(return_value=business_fail_resp())
        future = await queue.submit(fn, request_id="test_fail")
        result = await future

        assert result.rt_cd == ErrorCode.MARKET_CLOSED.value

    async def test_non_retriable_fail_no_retry(self, queue):
        """비즈니스 오류는 재시도 없이 1회만 호출"""
        fn = AsyncMock(return_value=business_fail_resp())
        future = await queue.submit(fn, request_id="test_fail")
        await future

        fn.assert_called_once()

    async def test_non_retriable_fail_added_to_fail_queue(self, queue):
        fn = AsyncMock(return_value=business_fail_resp())
        future = await queue.submit(fn, request_id="test_fail")
        await future

        assert queue.fail_queue.qsize() == 1
        assert queue.done_queue.empty()

    async def test_non_retriable_fail_logs_error(self, queue, logger):
        fn = AsyncMock(return_value=business_fail_resp())
        future = await queue.submit(fn, request_id="test_fail")
        await future

        logger.error.assert_called()


class TestSubmitRetry:
    async def test_retry_once_then_success(self, queue):
        """실패 후 1회 재시도에 성공"""
        fn = AsyncMock(side_effect=[network_fail_resp(), success_resp()])
        future = await queue.submit(fn, request_id="test_retry")
        result = await future

        assert result.rt_cd == ErrorCode.SUCCESS.value
        assert fn.call_count == 2
        assert queue.done_queue.qsize() == 1

    async def test_retry_multiple_times_then_success(self, queue):
        """여러 번 실패 후 마지막에 성공"""
        fn = AsyncMock(side_effect=[
            network_fail_resp(), network_fail_resp(), network_fail_resp(), success_resp()
        ])
        future = await queue.submit(fn, request_id="test_retry_multi")
        result = await future

        assert result.rt_cd == ErrorCode.SUCCESS.value
        assert fn.call_count == 4

    async def test_retry_exhausted_returns_last_fail_result(self, queue):
        """MAX_RETRIES 소진 시 마지막 실패 결과를 Future에 설정"""
        fn = AsyncMock(return_value=network_fail_resp())
        future = await queue.submit(fn, request_id="test_exhaust")
        result = await future

        assert result.rt_cd == ErrorCode.NETWORK_ERROR.value
        assert fn.call_count == ApiRequestQueue.MAX_RETRIES

    async def test_retry_exhausted_added_to_fail_queue(self, queue):
        fn = AsyncMock(return_value=network_fail_resp())
        future = await queue.submit(fn, request_id="test_exhaust")
        await future

        assert queue.fail_queue.qsize() == 1
        assert queue.done_queue.empty()

    async def test_retry_exhausted_logs_error(self, queue, logger):
        fn = AsyncMock(return_value=network_fail_resp())
        future = await queue.submit(fn, request_id="test_exhaust")
        await future

        logger.error.assert_called()

    async def test_retry_logs_warning_per_attempt(self, queue, logger):
        fn = AsyncMock(side_effect=[network_fail_resp(), success_resp()])
        future = await queue.submit(fn, request_id="test_warn")
        await future

        logger.warning.assert_called()

    async def test_each_retry_attempt_acquires_api_budget(self, logger):
        categories = []

        class RecordingLimiter:
            @asynccontextmanager
            async def acquire(self, category):
                categories.append(category)
                yield

        limited_queue = ApiRequestQueue(
            logger=logger,
            budget_limiter=RecordingLimiter(),
        )
        fn = AsyncMock(side_effect=[network_fail_resp(), success_resp()])

        future = await limited_queue.submit(
            fn,
            request_id="test_budget",
            request_category="quotation",
        )
        result = await future

        assert result.rt_cd == ErrorCode.SUCCESS.value
        assert categories == ["quotation", "quotation"]


class TestExceptionHandling:
    async def test_exception_in_fn_treated_as_none_then_retry(self, queue):
        """fn이 예외를 던지면 result=None으로 처리 → RETRY"""
        fn = AsyncMock(side_effect=[RuntimeError("connection reset"), success_resp()])
        future = await queue.submit(fn, request_id="test_exc")
        result = await future

        assert result.rt_cd == ErrorCode.SUCCESS.value
        assert fn.call_count == 2

    async def test_exception_logs_warning(self, queue, logger):
        fn = AsyncMock(side_effect=[RuntimeError("timeout"), success_resp()])
        future = await queue.submit(fn, request_id="test_exc_warn")
        await future

        logger.warning.assert_called()

    async def test_none_result_treated_as_retry(self, queue):
        """fn이 None을 반환하면 RETRY로 분류"""
        fn = AsyncMock(side_effect=[None, success_resp()])
        future = await queue.submit(fn, request_id="test_none")
        result = await future

        assert result.rt_cd == ErrorCode.SUCCESS.value
        assert fn.call_count == 2


class TestStop:
    async def test_stop_with_no_pending_tasks(self, queue, logger):
        """대기 중인 태스크가 없어도 stop()은 정상 완료"""
        await queue.stop()
        logger.info.assert_called()

    async def test_stop_cancels_pending_tasks(self, queue):
        """stop() 호출 시 _pending_tasks가 모두 취소됨"""
        event = asyncio.Event()
        # 직접 blocking task를 _pending_tasks에 추가
        task = asyncio.create_task(event.wait())
        queue._pending_tasks.add(task)
        task.add_done_callback(queue._pending_tasks.discard)

        assert queue.pending_count == 1
        await queue.stop()
        assert queue.pending_count == 0
        assert task.cancelled()

    async def test_stop_logs_cancelled_count(self, queue, logger):
        event = asyncio.Event()
        task = asyncio.create_task(event.wait())
        queue._pending_tasks.add(task)
        task.add_done_callback(queue._pending_tasks.discard)

        await queue.stop()
        # 로그 메시지에 취소된 태스크 수가 포함되어야 함
        logged_msg = logger.info.call_args[0][0]
        assert "1" in logged_msg


class TestQueueProperties:
    async def test_done_queue_property(self, queue):
        assert isinstance(queue.done_queue, asyncio.Queue)

    async def test_fail_queue_property(self, queue):
        assert isinstance(queue.fail_queue, asyncio.Queue)

    async def test_pending_count_property(self, queue):
        assert queue.pending_count == 0

    async def test_multiple_submits_fill_done_queue(self, queue):
        for i in range(3):
            fn = AsyncMock(return_value=success_resp())
            future = await queue.submit(fn, request_id=f"req_{i}")
            await future

        assert queue.done_queue.qsize() == 3
