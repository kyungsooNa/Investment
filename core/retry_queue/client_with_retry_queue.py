# core/retry_queue/client_with_retry_queue.py
import asyncio
from core.retry_queue.api_request_queue import ApiRequestQueue


# 주문(멱등성 우려) 및 WebSocket(상태 기반) 메서드는 큐를 통하지 않고 직접 호출
_EXCLUDED_METHODS = frozenset({
    # --- Trading (멱등성 보장 불가) ---
    "place_stock_order",
    "cancel_stock_order",

    # --- WebSocket (상태 기반, 재시도 의미 없음) ---
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
})

_BUDGET_ONLY_METHOD_CATEGORIES = {
    "place_stock_order": "order_submit",
    "cancel_stock_order": "order_cancel",
    "connect_websocket": "websocket_connect",
    "disconnect_websocket": "websocket_connect",
    "subscribe_realtime_price": "websocket_subscribe",
    "unsubscribe_realtime_price": "websocket_subscribe",
    "subscribe_realtime_quote": "websocket_subscribe",
    "unsubscribe_realtime_quote": "websocket_subscribe",
    "subscribe_program_trading": "websocket_subscribe",
    "unsubscribe_program_trading": "websocket_subscribe",
    "subscribe_unified_price": "websocket_subscribe",
    "unsubscribe_unified_price": "websocket_subscribe",
    "subscribe_order_notice": "websocket_subscribe",
    "unsubscribe_order_notice": "websocket_subscribe",
}

_ACCOUNT_METHODS = frozenset({
    "get_account_balance",
    "inquire_daily_ccld",
    "inquire_unfilled_orders",
    "inquire_filled_history",
})

_METHOD_BUDGET_CATEGORIES = {
    "get_current_price": "quotation_price",
    "get_current_conclusion": "quotation_conclusion",
    "inquire_daily_itemchartprice": "quotation_ohlcv",
    "inquire_time_itemchartprice": "quotation_ohlcv",
    "inquire_time_dailychartprice": "quotation_ohlcv",
    "get_account_balance": "account_balance",
    "inquire_daily_ccld": "account_reconciliation",
    "inquire_unfilled_orders": "account_reconciliation",
    "inquire_filled_history": "account_reconciliation",
}


class ClientWithRetryQueue:
    """
    BrokerAPIWrapper._client 를 감싸는 retry-queue 프록시.

    - 조회/계좌 API: submit() 을 통해 실패 시 자동 재시도
    - 주문/WebSocket API: retry queue 우회, global budget 만 적용 후 직접 위임
    """

    def __init__(self, client, queue: ApiRequestQueue, budget_limiter=None):
        self._client = client
        self._queue = queue
        self._budget_limiter = budget_limiter
        # 캐시된 래퍼 함수 저장: 동적 함수 객체 생성을 방지
        self._method_cache: dict = {}

    def __getattr__(self, name: str):
        # 이미 생성된 래퍼가 있으면 재사용
        if name in self._method_cache:
            return self._method_cache[name]

        attr = getattr(self._client, name)

        # 동기 메서드 또는 budget 적용 대상이 아닌 제외 목록 → 그대로 반환
        if not asyncio.iscoroutinefunction(attr):
            return attr
        if name in _EXCLUDED_METHODS:
            category = _BUDGET_ONLY_METHOD_CATEGORIES.get(name)
            if self._budget_limiter is None or category is None:
                return attr

            async def budgeted_direct(*args, **kwargs):
                async with self._budget_limiter.acquire(category):
                    return await attr(*args, **kwargs)

            self._method_cache[name] = budgeted_direct
            return budgeted_direct

        # 비동기 조회 메서드 → 큐를 통해 실행
        async def queued(*args, **kwargs):
            future = await self._queue.submit(
                attr,
                *args,
                request_id=name,
                request_category=_budget_category_for_method(name),
                budget_limiter=self._budget_limiter,
                **kwargs,
            )
            return await future

        # 캐싱 후 반환
        self._method_cache[name] = queued
        return queued


def _budget_category_for_method(name: str) -> str:
    category = _METHOD_BUDGET_CATEGORIES.get(name)
    if category is not None:
        return category
    if name in _ACCOUNT_METHODS:
        return "account"
    return "quotation"


def retry_queue_wrap_client(client, queue: ApiRequestQueue, budget_limiter=None) -> ClientWithRetryQueue:
    """BrokerAPIWrapper.__init__ 에서 호출하는 팩토리 함수."""
    return ClientWithRetryQueue(client, queue, budget_limiter=budget_limiter)
