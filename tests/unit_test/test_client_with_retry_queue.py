# tests/unit_test/test_client_with_retry_queue.py

import pytest
from unittest.mock import MagicMock, AsyncMock

from common.types import ResCommonResponse, ErrorCode
from core.retry_queue.api_request_queue import ApiRequestQueue
from core.retry_queue.client_with_retry_queue import (
    ClientWithRetryQueue,
    retry_queue_wrap_client,
    _EXCLUDED_METHODS,
)


def success_resp() -> ResCommonResponse:
    return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="정상", data={})


class FakeClient:
    """테스트용 가짜 클라이언트: 조회/주문/WebSocket 메서드 혼재"""

    async def get_current_price(self, code: str) -> ResCommonResponse:
        return success_resp()

    async def get_account_balance(self) -> ResCommonResponse:
        return success_resp()

    async def place_stock_order(self, code, price, qty, is_buy) -> ResCommonResponse:
        return success_resp()

    async def connect_websocket(self, on_message_callback=None) -> bool:
        return True

    async def disconnect_websocket(self) -> None:
        pass

    async def subscribe_realtime_price(self, stock_code: str) -> None:
        pass

    async def unsubscribe_realtime_price(self, stock_code: str) -> None:
        pass

    async def subscribe_realtime_quote(self, stock_code: str) -> None:
        pass

    async def unsubscribe_realtime_quote(self, stock_code: str) -> None:
        pass

    async def subscribe_program_trading(self, stock_code: str) -> None:
        pass

    async def unsubscribe_program_trading(self, stock_code: str) -> None:
        pass

    def is_websocket_receive_alive(self) -> bool:
        return True

    def sync_utility(self) -> str:
        return "sync_result"


@pytest.fixture
def queue():
    return ApiRequestQueue(logger=MagicMock())


@pytest.fixture
def fake_client():
    return FakeClient()


@pytest.fixture
def wrapped(fake_client, queue):
    return ClientWithRetryQueue(fake_client, queue)


class TestAsyncMethodThroughQueue:
    async def test_quotation_method_uses_queue(self, wrapped, queue):
        result = await wrapped.get_current_price("005930")

        assert result.rt_cd == ErrorCode.SUCCESS.value
        assert not queue.done_queue.empty()

    async def test_account_method_uses_queue(self, wrapped, queue):
        result = await wrapped.get_account_balance()

        assert result.rt_cd == ErrorCode.SUCCESS.value
        assert not queue.done_queue.empty()

    async def test_queue_contains_request_with_correct_id(self, wrapped, queue):
        await wrapped.get_current_price("005930")
        req, _ = await queue.done_queue.get()

        assert req.request_id == "get_current_price"


class TestExcludedMethodsBypassQueue:
    async def test_place_stock_order_bypasses_queue(self, wrapped, queue):
        """멱등성 우려 — 주문 API는 큐를 통하지 않음"""
        result = await wrapped.place_stock_order("005930", 70000, 1, True)

        assert result.rt_cd == ErrorCode.SUCCESS.value
        assert queue.done_queue.empty()

    async def test_connect_websocket_bypasses_queue(self, wrapped, queue):
        result = await wrapped.connect_websocket()

        assert result is True
        assert queue.done_queue.empty()

    async def test_disconnect_websocket_bypasses_queue(self, wrapped, queue):
        await wrapped.disconnect_websocket()
        assert queue.done_queue.empty()

    async def test_subscribe_realtime_price_bypasses_queue(self, wrapped, queue):
        await wrapped.subscribe_realtime_price("005930")
        assert queue.done_queue.empty()

    async def test_unsubscribe_realtime_price_bypasses_queue(self, wrapped, queue):
        await wrapped.unsubscribe_realtime_price("005930")
        assert queue.done_queue.empty()

    async def test_subscribe_realtime_quote_bypasses_queue(self, wrapped, queue):
        await wrapped.subscribe_realtime_quote("005930")
        assert queue.done_queue.empty()

    async def test_unsubscribe_realtime_quote_bypasses_queue(self, wrapped, queue):
        await wrapped.unsubscribe_realtime_quote("005930")
        assert queue.done_queue.empty()

    async def test_subscribe_program_trading_bypasses_queue(self, wrapped, queue):
        await wrapped.subscribe_program_trading("005930")
        assert queue.done_queue.empty()

    async def test_unsubscribe_program_trading_bypasses_queue(self, wrapped, queue):
        await wrapped.unsubscribe_program_trading("005930")
        assert queue.done_queue.empty()


class TestSyncMethodsBypassQueue:
    def test_sync_method_bypasses_queue(self, wrapped, queue):
        result = wrapped.is_websocket_receive_alive()

        assert result is True
        assert queue.done_queue.empty()

    def test_sync_utility_bypasses_queue(self, wrapped, queue):
        result = wrapped.sync_utility()

        assert result == "sync_result"
        assert queue.done_queue.empty()


class TestRetryViaProxy:
    async def test_async_method_retries_on_failure(self, queue):
        """큐를 거치는 메서드가 실패 시 자동 재시도되는지 확인"""
        call_count = 0

        class FailOnceThenSucceedClient:
            async def get_data(self) -> ResCommonResponse:
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    return ResCommonResponse(
                        rt_cd=ErrorCode.NETWORK_ERROR.value, msg1="네트워크 오류", data=None
                    )
                return success_resp()

        wrapped = ClientWithRetryQueue(FailOnceThenSucceedClient(), queue)
        result = await wrapped.get_data()

        assert result.rt_cd == ErrorCode.SUCCESS.value
        assert call_count == 2


class TestFactoryFunction:
    def test_retry_queue_wrap_client_returns_correct_type(self, fake_client, queue):
        client = retry_queue_wrap_client(fake_client, queue)
        assert isinstance(client, ClientWithRetryQueue)

    def test_wrapped_client_has_correct_inner_client(self, fake_client, queue):
        client = retry_queue_wrap_client(fake_client, queue)
        assert client._client is fake_client

    def test_wrapped_client_has_correct_queue(self, fake_client, queue):
        client = retry_queue_wrap_client(fake_client, queue)
        assert client._queue is queue


class TestExcludedMethodsSet:
    def test_place_stock_order_is_excluded(self):
        assert "place_stock_order" in _EXCLUDED_METHODS

    def test_all_websocket_methods_are_excluded(self):
        expected = {
            "connect_websocket",
            "disconnect_websocket",
            "subscribe_realtime_price",
            "unsubscribe_realtime_price",
            "subscribe_realtime_quote",
            "unsubscribe_realtime_quote",
            "subscribe_program_trading",
            "unsubscribe_program_trading",
            "is_websocket_receive_alive",
        }
        assert expected.issubset(_EXCLUDED_METHODS)
