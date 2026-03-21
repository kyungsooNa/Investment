# tests/integration_test/test_it_retry_queue.py
"""
RetryQueue 통합 테스트.

ClientWithRetryQueue → ApiRequestQueue → 실제 API 클라이언트(AsyncMock) 계층을
실제 컴포넌트로 구성하고, 다음 세 가지 시나리오를 검증합니다.

  1. 최초 성공   : API 호출이 즉시 성공 → Future에 성공 결과 반영
  2. 재시도 후 성공: 첫 호출 실패(NETWORK_ERROR) → RetryQueue 재시도 → 두 번째 호출 성공
  3. 최종 실패   : MAX_RETRIES 소진 후에도 계속 실패 → Future에 마지막 실패 결과 반영

asyncio.sleep(백오프 지연)은 테스트 속도를 위해 모킹합니다.
"""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from common.types import ResCommonResponse, ErrorCode
from core.retry_queue.api_request_queue import ApiRequestQueue
from core.retry_queue.client_with_retry_queue import ClientWithRetryQueue


# ---------------------------------------------------------------------------
# 헬퍼 팩토리
# ---------------------------------------------------------------------------

def _success(data=None) -> ResCommonResponse:
    return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="정상처리 되었습니다.", data=data or {})


def _network_fail() -> ResCommonResponse:
    """일시적 네트워크 오류 → RETRY 분류."""
    return ResCommonResponse(rt_cd=ErrorCode.NETWORK_ERROR.value, msg1="네트워크 오류", data=None)


def _business_fail() -> ResCommonResponse:
    """비즈니스 오류 (장 마감) → FAIL 분류 (재시도 무의미)."""
    return ResCommonResponse(rt_cd=ErrorCode.MARKET_CLOSED.value, msg1="장 마감 시간입니다.", data=None)


# ---------------------------------------------------------------------------
# 픽스처
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_sleep():
    """백오프 지연을 즉시 통과시켜 테스트 속도를 보장합니다."""
    with patch("core.retry_queue.api_request_queue.asyncio.sleep", new_callable=AsyncMock) as m:
        yield m


@pytest.fixture
def queue(test_logger, mock_sleep):
    """실제 ApiRequestQueue (logger는 진짜 Logger 사용)."""
    return ApiRequestQueue(logger=test_logger)


@pytest.fixture
def make_wrapped(queue):
    """
    AsyncMock을 기반 클라이언트로 받아 ClientWithRetryQueue를 조립하는 팩토리.
    Usage: wrapped, mock_client = make_wrapped({"get_current_price": [resp1, resp2]})
    """
    def _factory(method_effects: dict) -> tuple[ClientWithRetryQueue, MagicMock]:
        mock_client = MagicMock()
        for method_name, side_effects in method_effects.items():
            async_mock = AsyncMock(side_effect=side_effects)
            setattr(mock_client, method_name, async_mock)
        wrapped = ClientWithRetryQueue(mock_client, queue)
        return wrapped, mock_client
    return _factory


# ---------------------------------------------------------------------------
# 시나리오 1: 최초 성공
# ---------------------------------------------------------------------------

class TestImmediateSuccess:
    async def test_result_is_success(self, make_wrapped, queue):
        """API 최초 호출에서 성공 → Future 즉시 성공 결과 반영."""
        wrapped, mock_client = make_wrapped({"get_current_price": [_success({"stck_prpr": "70500"})]})

        result = await wrapped.get_current_price("005930")

        assert result.rt_cd == ErrorCode.SUCCESS.value

    async def test_underlying_api_called_exactly_once(self, make_wrapped):
        """재시도 없이 1회만 호출."""
        wrapped, mock_client = make_wrapped({"get_current_price": [_success()]})

        await wrapped.get_current_price("005930")

        mock_client.get_current_price.assert_called_once_with("005930")

    async def test_result_lands_in_done_queue(self, make_wrapped, queue):
        """성공 결과는 done_queue에 적재."""
        wrapped, _ = make_wrapped({"get_current_price": [_success()]})

        await wrapped.get_current_price("005930")

        assert not queue.done_queue.empty()
        req, result = await queue.done_queue.get()
        assert result.rt_cd == ErrorCode.SUCCESS.value
        assert req.request_id == "get_current_price"

    async def test_fail_queue_is_empty_on_success(self, make_wrapped, queue):
        """성공 시 fail_queue는 비어 있어야 함."""
        wrapped, _ = make_wrapped({"get_current_price": [_success()]})

        await wrapped.get_current_price("005930")

        assert queue.fail_queue.empty()


# ---------------------------------------------------------------------------
# 시나리오 2: 최초 실패 → 재시도 → 성공
# ---------------------------------------------------------------------------

class TestRetryThenSuccess:
    async def test_result_is_success_after_retry(self, make_wrapped, queue):
        """첫 번째 NETWORK_ERROR 이후 재시도에서 성공 → 최종 결과는 성공."""
        wrapped, _ = make_wrapped({"get_current_price": [_network_fail(), _success()]})

        result = await wrapped.get_current_price("005930")

        assert result.rt_cd == ErrorCode.SUCCESS.value

    async def test_underlying_api_called_twice(self, make_wrapped):
        """최초 실패 + 1회 재시도 = 총 2회 호출."""
        wrapped, mock_client = make_wrapped({"get_current_price": [_network_fail(), _success()]})

        await wrapped.get_current_price("005930")

        assert mock_client.get_current_price.call_count == 2

    async def test_backoff_sleep_is_triggered(self, make_wrapped, mock_sleep):
        """재시도 전 지수 백오프 sleep이 1회 호출되었는지 검증."""
        wrapped, _ = make_wrapped({"get_current_price": [_network_fail(), _success()]})

        await wrapped.get_current_price("005930")

        mock_sleep.assert_called_once()

    async def test_result_lands_in_done_queue_after_retry(self, make_wrapped, queue):
        """재시도 후 성공 결과도 done_queue에 적재."""
        wrapped, _ = make_wrapped({"get_current_price": [_network_fail(), _success()]})

        await wrapped.get_current_price("005930")

        assert not queue.done_queue.empty()

    async def test_warning_is_logged_on_retry(self, make_wrapped, test_logger):
        """재시도 시 WARNING 로그가 남는지 검증."""
        wrapped, _ = make_wrapped({"get_current_price": [_network_fail(), _success()]})

        await wrapped.get_current_price("005930")

        test_logger.warning.assert_called()

    async def test_multiple_retries_then_success(self, make_wrapped):
        """3번 실패 후 4번째에서 성공하는 시나리오."""
        wrapped, mock_client = make_wrapped({
            "get_current_price": [
                _network_fail(), _network_fail(), _network_fail(), _success()
            ]
        })

        result = await wrapped.get_current_price("005930")

        assert result.rt_cd == ErrorCode.SUCCESS.value
        assert mock_client.get_current_price.call_count == 4

    async def test_exception_from_api_triggers_retry(self, queue):
        """API 클라이언트가 예외를 던져도 재시도 후 성공."""
        call_count = 0

        async def flaky_get_current_price(code):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ConnectionError("Connection reset by peer")
            return _success()

        mock_client = MagicMock()
        mock_client.get_current_price = flaky_get_current_price
        wrapped = ClientWithRetryQueue(mock_client, queue)

        result = await wrapped.get_current_price("005930")

        assert result.rt_cd == ErrorCode.SUCCESS.value
        assert call_count == 2


# ---------------------------------------------------------------------------
# 시나리오 3: 최종 실패 (MAX_RETRIES 소진)
# ---------------------------------------------------------------------------

class TestFinalFailure:
    async def test_result_is_last_failure_after_exhaustion(self, make_wrapped, queue):
        """MAX_RETRIES 소진 후 Future에 마지막 실패 결과가 설정됨."""
        wrapped, _ = make_wrapped({
            "get_current_price": [_network_fail()] * ApiRequestQueue.MAX_RETRIES
        })

        result = await wrapped.get_current_price("005930")

        assert result.rt_cd == ErrorCode.NETWORK_ERROR.value

    async def test_underlying_api_called_max_retries_times(self, make_wrapped, queue):
        """MAX_RETRIES 횟수만큼 API를 시도."""
        wrapped, mock_client = make_wrapped({
            "get_current_price": [_network_fail()] * ApiRequestQueue.MAX_RETRIES
        })

        await wrapped.get_current_price("005930")

        assert mock_client.get_current_price.call_count == ApiRequestQueue.MAX_RETRIES

    async def test_result_lands_in_fail_queue(self, make_wrapped, queue):
        """최종 실패 결과는 fail_queue에 적재."""
        wrapped, _ = make_wrapped({
            "get_current_price": [_network_fail()] * ApiRequestQueue.MAX_RETRIES
        })

        await wrapped.get_current_price("005930")

        assert not queue.fail_queue.empty()

    async def test_done_queue_is_empty_on_final_failure(self, make_wrapped, queue):
        """최종 실패 시 done_queue는 비어 있어야 함."""
        wrapped, _ = make_wrapped({
            "get_current_price": [_network_fail()] * ApiRequestQueue.MAX_RETRIES
        })

        await wrapped.get_current_price("005930")

        assert queue.done_queue.empty()

    async def test_error_is_logged_on_final_failure(self, make_wrapped, test_logger):
        """최종 실패 시 ERROR 로그가 남는지 검증."""
        wrapped, _ = make_wrapped({
            "get_current_price": [_network_fail()] * ApiRequestQueue.MAX_RETRIES
        })

        await wrapped.get_current_price("005930")

        test_logger.error.assert_called()

    async def test_backoff_sleep_called_for_each_retry(self, make_wrapped, mock_sleep):
        """재시도마다 sleep이 호출 (MAX_RETRIES - 1회: 마지막 실패는 sleep 없이 종료)."""
        wrapped, _ = make_wrapped({
            "get_current_price": [_network_fail()] * ApiRequestQueue.MAX_RETRIES
        })

        await wrapped.get_current_price("005930")

        # 5회 시도 중 마지막은 즉시 종료 → sleep은 4회 = MAX_RETRIES - 1
        assert mock_sleep.call_count == ApiRequestQueue.MAX_RETRIES - 1


# ---------------------------------------------------------------------------
# 시나리오 4: 비즈니스 오류 (재시도 없는 즉시 실패)
# ---------------------------------------------------------------------------

class TestNonRetriableFailure:
    async def test_business_error_fails_immediately(self, make_wrapped, queue):
        """장 마감 등 비즈니스 오류는 재시도 없이 즉시 FAIL."""
        wrapped, mock_client = make_wrapped({
            "get_current_price": [_business_fail()]
        })

        result = await wrapped.get_current_price("005930")

        assert result.rt_cd == ErrorCode.MARKET_CLOSED.value
        mock_client.get_current_price.assert_called_once()

    async def test_business_error_no_sleep(self, make_wrapped, mock_sleep):
        """비즈니스 오류는 sleep(재시도 지연) 없이 즉시 종료."""
        wrapped, _ = make_wrapped({
            "get_current_price": [_business_fail()]
        })

        await wrapped.get_current_price("005930")

        mock_sleep.assert_not_called()

    async def test_business_error_lands_in_fail_queue(self, make_wrapped, queue):
        """비즈니스 오류 결과도 fail_queue에 적재."""
        wrapped, _ = make_wrapped({
            "get_current_price": [_business_fail()]
        })

        await wrapped.get_current_price("005930")

        assert not queue.fail_queue.empty()


# ---------------------------------------------------------------------------
# 시나리오 5: 제외 메서드 (주문/WebSocket) 큐 미사용 확인
# ---------------------------------------------------------------------------

class TestExcludedMethodsSkipQueue:
    async def test_place_stock_order_bypasses_queue(self, make_wrapped, queue):
        """주문 API는 RetryQueue를 통하지 않으므로 done_queue/fail_queue 모두 비어 있어야 함."""
        wrapped, mock_client = make_wrapped({
            "place_stock_order": [_success()]
        })

        result = await wrapped.place_stock_order("005930", 70000, 1, True)

        assert result.rt_cd == ErrorCode.SUCCESS.value
        assert queue.done_queue.empty()
        assert queue.fail_queue.empty()

    async def test_place_stock_order_not_retried_on_failure(self, make_wrapped, queue):
        """주문 실패 시 RetryQueue가 재시도하지 않음 (멱등성 보장)."""
        wrapped, mock_client = make_wrapped({
            "place_stock_order": [_network_fail()]
        })

        result = await wrapped.place_stock_order("005930", 70000, 1, True)

        assert result.rt_cd == ErrorCode.NETWORK_ERROR.value
        mock_client.place_stock_order.assert_called_once()
        assert queue.fail_queue.empty()
