# core/retry_queue/client_with_retry_queue.py
import asyncio
from core.retry_queue.api_request_queue import ApiRequestQueue


# 주문(멱등성 우려) 및 WebSocket(상태 기반) 메서드는 큐를 통하지 않고 직접 호출
_EXCLUDED_METHODS = frozenset({
    # --- Trading (멱등성 보장 불가) ---
    "place_stock_order",

    # --- WebSocket (상태 기반, 재시도 의미 없음) ---
    "connect_websocket",
    "disconnect_websocket",
    "subscribe_realtime_price",
    "unsubscribe_realtime_price",
    "subscribe_realtime_quote",
    "unsubscribe_realtime_quote",
    "subscribe_program_trading",
    "unsubscribe_program_trading",
    "is_websocket_receive_alive",
})


class ClientWithRetryQueue:
    """
    BrokerAPIWrapper._client 를 감싸는 retry-queue 프록시.

    - 조회/계좌 API: submit() 을 통해 실패 시 자동 재시도
    - 주문/WebSocket API: 큐 우회, 직접 위임 (기존 동작 유지)
    """

    def __init__(self, client, queue: ApiRequestQueue):
        self._client = client
        self._queue = queue

    def __getattr__(self, name: str):
        attr = getattr(self._client, name)

        # 동기 메서드 또는 제외 목록 → 그대로 반환
        if name in _EXCLUDED_METHODS or not asyncio.iscoroutinefunction(attr):
            return attr

        # 비동기 조회 메서드 → 큐를 통해 실행
        async def queued(*args, **kwargs):
            future = await self._queue.submit(attr, *args, request_id=name, **kwargs)
            return await future

        return queued


def retry_queue_wrap_client(client, queue: ApiRequestQueue) -> ClientWithRetryQueue:
    """BrokerAPIWrapper.__init__ 에서 호출하는 팩토리 함수."""
    return ClientWithRetryQueue(client, queue)
