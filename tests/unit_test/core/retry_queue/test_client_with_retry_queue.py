# tests/unit_test/test_client_with_retry_queue.py

from contextlib import asynccontextmanager

import pytest
from unittest.mock import MagicMock, AsyncMock

from common.types import ResCommonResponse, ErrorCode
from core.retry_queue.api_budget_limiter import (
    DEFAULT_API_BUDGET_LIMITS,
    DEFAULT_API_EMERGENCY_LIMITS,
)
from core.retry_queue.api_request_queue import ApiRequestQueue
from core.retry_queue.client_with_retry_queue import (
    API_BUDGET_COVERAGE_MATRIX,
    ClientWithRetryQueue,
    retry_queue_wrap_client,
    _BUDGET_ONLY_METHOD_CATEGORIES,
    _EXCLUDED_METHODS,
    _budget_category_for_method,
)


def success_resp() -> ResCommonResponse:
    return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="정상", data={})


class FakeClient:
    """테스트용 가짜 클라이언트: 조회/주문/WebSocket 메서드 혼재"""

    async def get_current_price(self, code: str) -> ResCommonResponse:
        return success_resp()

    async def get_account_balance(self) -> ResCommonResponse:
        return success_resp()

    async def inquire_daily_ccld(self) -> ResCommonResponse:
        return success_resp()

    async def inquire_unfilled_orders(self) -> ResCommonResponse:
        return success_resp()

    async def inquire_filled_history(self) -> ResCommonResponse:
        return success_resp()

    async def inquire_daily_itemchartprice(self, stock_code: str) -> ResCommonResponse:
        return success_resp()

    async def inquire_time_itemchartprice(self, stock_code: str) -> ResCommonResponse:
        return success_resp()

    async def inquire_time_dailychartprice(self, stock_code: str) -> ResCommonResponse:
        return success_resp()

    async def get_current_conclusion(self, stock_code: str) -> ResCommonResponse:
        return success_resp()

    async def place_stock_order(self, code, price, qty, is_buy) -> ResCommonResponse:
        return success_resp()

    async def cancel_stock_order(self, **kwargs) -> ResCommonResponse:
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

    async def subscribe_unified_price(self, stock_code: str) -> bool:
        return True

    async def unsubscribe_unified_price(self, stock_code: str) -> bool:
        return True

    async def subscribe_order_notice(self) -> bool:
        return True

    async def unsubscribe_order_notice(self) -> bool:
        return True

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

    async def test_quotation_method_uses_price_budget_category(self, fake_client, queue):
        categories = []

        class RecordingLimiter:
            @asynccontextmanager
            async def acquire(self, category, **kwargs):
                categories.append(category)
                yield

        wrapped = ClientWithRetryQueue(
            fake_client,
            queue,
            budget_limiter=RecordingLimiter(),
        )

        await wrapped.get_current_price("005930")

        assert categories == ["quotation_price"]

    async def test_account_method_uses_balance_budget_category(self, fake_client, queue):
        categories = []

        class RecordingLimiter:
            @asynccontextmanager
            async def acquire(self, category, **kwargs):
                categories.append(category)
                yield

        wrapped = ClientWithRetryQueue(
            fake_client,
            queue,
            budget_limiter=RecordingLimiter(),
        )

        await wrapped.get_account_balance()

        assert categories == ["account_balance"]

    @pytest.mark.parametrize(
        ("method_name", "expected_category"),
        [
            ("get_current_price", "quotation_price"),
            ("get_current_conclusion", "quotation_conclusion"),
            ("inquire_daily_itemchartprice", "quotation_ohlcv"),
            ("inquire_time_itemchartprice", "quotation_ohlcv"),
            ("inquire_time_dailychartprice", "quotation_ohlcv"),
            ("get_account_balance", "account_balance"),
            ("inquire_daily_ccld", "account_reconciliation"),
            ("inquire_unfilled_orders", "account_reconciliation"),
            ("unknown_lookup", "quotation"),
        ],
    )
    def test_budget_category_for_method_uses_endpoint_specific_policy(self, method_name, expected_category):
        assert _budget_category_for_method(method_name) == expected_category


class TestApiBudgetCoverageMatrix:
    def test_coverage_matrix_includes_live_operation_paths(self):
        operations = {entry["operation"] for entry in API_BUDGET_COVERAGE_MATRIX}

        assert {
            "current_price_rest",
            "ohlcv_daily_rest",
            "ohlcv_intraday_rest",
            "conclusion_rest",
            "account_balance_rest",
            "daily_ccld_reconciliation_rest",
            "unfilled_reconciliation_rest",
            "filled_history_reconciliation_rest",
            "order_submit_rest",
            "order_cancel_rest",
            "websocket_connect",
            "websocket_disconnect",
            "websocket_price_subscribe",
            "websocket_price_unsubscribe",
            "websocket_quote_subscribe",
            "websocket_quote_unsubscribe",
            "websocket_program_subscribe",
            "websocket_program_unsubscribe",
            "websocket_unified_price_subscribe",
            "websocket_unified_price_unsubscribe",
            "websocket_order_notice_subscribe",
            "websocket_order_notice_unsubscribe",
            "emergency_sell_order_submit",
            "emergency_sell_order_cancel",
        }.issubset(operations)

    @pytest.mark.parametrize("entry", API_BUDGET_COVERAGE_MATRIX)
    def test_coverage_matrix_matches_runtime_method_routing(self, entry):
        method_name = entry["method_name"]
        if entry["execution_path"] == "retry_queue":
            assert _budget_category_for_method(method_name) == entry["category"]
            assert method_name not in _EXCLUDED_METHODS
        else:
            assert method_name in _EXCLUDED_METHODS
            assert _BUDGET_ONLY_METHOD_CATEGORIES[method_name] == entry["category"]

    def test_coverage_matrix_marks_emergency_sell_as_emergency_lane(self):
        emergency_entries = {
            entry["operation"]: entry
            for entry in API_BUDGET_COVERAGE_MATRIX
            if entry["operation"].startswith("emergency_sell_")
        }

        assert emergency_entries["emergency_sell_order_submit"]["lane"] == "emergency"
        assert emergency_entries["emergency_sell_order_submit"]["category"] == "order_submit"
        assert emergency_entries["emergency_sell_order_cancel"]["lane"] == "emergency"
        assert emergency_entries["emergency_sell_order_cancel"]["category"] == "order_cancel"

    @pytest.mark.parametrize("entry", API_BUDGET_COVERAGE_MATRIX)
    def test_coverage_matrix_categories_are_configured_in_default_limiter(self, entry):
        assert entry["category"] in DEFAULT_API_BUDGET_LIMITS
        if entry["lane"] == "emergency":
            assert entry["category"] in DEFAULT_API_EMERGENCY_LIMITS


class TestExcludedMethodsBypassQueue:
    async def test_place_stock_order_bypasses_queue(self, wrapped, queue):
        """멱등성 우려 — 주문 API는 큐를 통하지 않음"""
        result = await wrapped.place_stock_order("005930", 70000, 1, True)

        assert result.rt_cd == ErrorCode.SUCCESS.value
        assert queue.done_queue.empty()

    async def test_place_stock_order_uses_order_submit_budget_without_retry_queue(self, fake_client, queue):
        calls = []

        class RecordingLimiter:
            @asynccontextmanager
            async def acquire(self, category, **kwargs):
                calls.append(category)
                yield

        wrapped = ClientWithRetryQueue(
            fake_client,
            queue,
            budget_limiter=RecordingLimiter(),
        )

        result = await wrapped.place_stock_order("005930", 70000, 1, True)

        assert result.rt_cd == ErrorCode.SUCCESS.value
        assert queue.done_queue.empty()
        assert calls == ["order_submit"]

    async def test_cancel_stock_order_uses_order_cancel_budget_without_retry_queue(self, fake_client, queue):
        calls = []

        class RecordingLimiter:
            @asynccontextmanager
            async def acquire(self, category, **kwargs):
                calls.append(category)
                yield

        wrapped = ClientWithRetryQueue(
            fake_client,
            queue,
            budget_limiter=RecordingLimiter(),
        )

        result = await wrapped.cancel_stock_order(broker_order_no="123", order_qty=1)

        assert result.rt_cd == ErrorCode.SUCCESS.value
        assert queue.done_queue.empty()
        assert calls == ["order_cancel"]

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

    async def test_subscribe_realtime_price_uses_websocket_subscribe_budget_without_retry_queue(self, fake_client, queue):
        calls = []

        class RecordingLimiter:
            @asynccontextmanager
            async def acquire(self, category, **kwargs):
                calls.append(category)
                yield

        wrapped = ClientWithRetryQueue(
            fake_client,
            queue,
            budget_limiter=RecordingLimiter(),
        )

        await wrapped.subscribe_realtime_price("005930")

        assert queue.done_queue.empty()
        assert calls == ["websocket_subscribe"]

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

    async def test_subscribe_unified_price_bypasses_queue(self, wrapped, queue):
        """bool 반환 메서드가 retry queue를 거치면 classify()에서 AttributeError 발생 — 반드시 제외되어야 함"""
        result = await wrapped.subscribe_unified_price("005930")
        assert result is True
        assert queue.done_queue.empty()

    async def test_unsubscribe_unified_price_bypasses_queue(self, wrapped, queue):
        result = await wrapped.unsubscribe_unified_price("005930")
        assert result is True
        assert queue.done_queue.empty()

    async def test_subscribe_order_notice_bypasses_queue(self, wrapped, queue):
        result = await wrapped.subscribe_order_notice()
        assert result is True
        assert queue.done_queue.empty()

    async def test_unsubscribe_order_notice_bypasses_queue(self, wrapped, queue):
        result = await wrapped.unsubscribe_order_notice()
        assert result is True
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

    def test_retry_queue_wrap_client_accepts_budget_limiter(self, fake_client, queue):
        limiter = MagicMock()
        client = retry_queue_wrap_client(fake_client, queue, budget_limiter=limiter)
        assert client._budget_limiter is limiter


class TestExcludedMethodsSet:
    def test_place_stock_order_is_excluded(self):
        assert "place_stock_order" in _EXCLUDED_METHODS
        assert "cancel_stock_order" in _EXCLUDED_METHODS

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
            "subscribe_unified_price",
            "unsubscribe_unified_price",
            "subscribe_order_notice",
            "unsubscribe_order_notice",
            "is_websocket_receive_alive",
        }
        assert expected.issubset(_EXCLUDED_METHODS)


class TestEmergencyPriorityPropagation:
    """ContextVar priority 가 budgeted_direct 경로로 전파되는지 검증."""

    async def test_place_stock_order_passes_normal_priority_by_default(self, fake_client, queue):
        from core.api_priority import PRIORITY_NORMAL

        recorded: list[str] = []

        class RecordingLimiter:
            @asynccontextmanager
            async def acquire(self, category, *, priority="normal"):
                recorded.append(priority)
                yield

        wrapped = ClientWithRetryQueue(
            fake_client,
            queue,
            budget_limiter=RecordingLimiter(),
        )

        await wrapped.place_stock_order("005930", 70000, 1, True)

        assert recorded == [PRIORITY_NORMAL]

    async def test_place_stock_order_inside_emergency_scope_passes_emergency_priority(
        self, fake_client, queue
    ):
        from core.api_priority import PRIORITY_EMERGENCY, emergency_scope

        recorded: list[str] = []

        class RecordingLimiter:
            @asynccontextmanager
            async def acquire(self, category, *, priority="normal"):
                recorded.append(priority)
                yield

        wrapped = ClientWithRetryQueue(
            fake_client,
            queue,
            budget_limiter=RecordingLimiter(),
        )

        with emergency_scope():
            await wrapped.place_stock_order("005930", 70000, 1, True)

        assert recorded == [PRIORITY_EMERGENCY]

    async def test_cancel_stock_order_inside_emergency_scope_passes_emergency_priority(
        self, fake_client, queue
    ):
        from core.api_priority import PRIORITY_EMERGENCY, emergency_scope

        recorded: list[tuple[str, str]] = []

        class RecordingLimiter:
            @asynccontextmanager
            async def acquire(self, category, *, priority="normal"):
                recorded.append((category, priority))
                yield

        wrapped = ClientWithRetryQueue(
            fake_client,
            queue,
            budget_limiter=RecordingLimiter(),
        )

        with emergency_scope():
            await wrapped.cancel_stock_order(broker_order_no="123", order_qty=1)

        assert recorded == [("order_cancel", PRIORITY_EMERGENCY)]

    async def test_priority_does_not_leak_after_emergency_scope_exits(
        self, fake_client, queue
    ):
        from core.api_priority import PRIORITY_EMERGENCY, PRIORITY_NORMAL, emergency_scope

        recorded: list[str] = []

        class RecordingLimiter:
            @asynccontextmanager
            async def acquire(self, category, *, priority="normal"):
                recorded.append(priority)
                yield

        wrapped = ClientWithRetryQueue(
            fake_client,
            queue,
            budget_limiter=RecordingLimiter(),
        )

        with emergency_scope():
            await wrapped.place_stock_order("005930", 70000, 1, True)
        await wrapped.place_stock_order("005930", 70000, 1, True)

        assert recorded == [PRIORITY_EMERGENCY, PRIORITY_NORMAL]
