"""ApiBudgetLimiter end-to-end integration scenarios."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from common.types import ErrorCode, ResCommonResponse
from core.api_priority import emergency_scope
from core.retry_queue.api_budget_limiter import ApiBudgetLimiter
from core.retry_queue.api_request_queue import ApiRequestQueue
from core.retry_queue.client_with_retry_queue import ClientWithRetryQueue


def _success(data=None) -> ResCommonResponse:
    return ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value,
        msg1="정상처리 되었습니다.",
        data=data or {},
    )


def _make_fake_client() -> MagicMock:
    client = MagicMock()
    client.get_current_price = AsyncMock(return_value=_success({"price": "70000"}))
    client.inquire_daily_itemchartprice = AsyncMock(return_value=_success({"ohlcv": []}))
    client.get_account_balance = AsyncMock(return_value=_success({"balance": []}))
    client.inquire_unfilled_orders = AsyncMock(return_value=_success({"orders": []}))
    client.inquire_daily_ccld = AsyncMock(return_value=_success({"fills": []}))
    client.place_stock_order = AsyncMock(return_value=_success({"ordno": "1"}))
    client.cancel_stock_order = AsyncMock(return_value=_success({"ordno": "1"}))
    client.subscribe_realtime_price = AsyncMock(return_value=True)
    return client


async def test_api_budget_limiter_caps_mixed_live_operation_paths_end_to_end(test_logger):
    """조회/계좌/주문/WebSocket/emergency 호출을 섞어도 shared global budget을 지난다."""
    sleeps: list[float] = []
    now = 100.0

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    limiter = ApiBudgetLimiter(
        limits={
            "quotation_price": 8,
            "quotation_ohlcv": 8,
            "account_balance": 8,
            "account_reconciliation": 8,
            "order_submit": 8,
            "order_cancel": 8,
            "websocket_subscribe": 8,
        },
        rate_limits_per_sec={
            "quotation_price": float("inf"),
            "quotation_ohlcv": float("inf"),
            "account_balance": float("inf"),
            "account_reconciliation": float("inf"),
            "order_submit": float("inf"),
            "order_cancel": float("inf"),
            "websocket_subscribe": float("inf"),
        },
        emergency_limits={"order_submit": 8, "order_cancel": 8},
        emergency_rate_limits_per_sec={
            "order_submit": float("inf"),
            "order_cancel": float("inf"),
        },
        global_rate_limit_per_sec=2.0,
        emergency_global_rate_limit_per_sec=2.0,
        sleep=fake_sleep,
        monotonic=lambda: now,
    )
    queue = ApiRequestQueue(logger=test_logger, budget_limiter=limiter)
    client = _make_fake_client()
    wrapped = ClientWithRetryQueue(client, queue, budget_limiter=limiter)

    await wrapped.get_current_price("005930")
    await wrapped.inquire_daily_itemchartprice("005930")
    await wrapped.get_account_balance()
    await wrapped.inquire_unfilled_orders()
    await wrapped.inquire_daily_ccld()
    await wrapped.place_stock_order("005930", 70000, 1, True)
    await wrapped.cancel_stock_order(broker_order_no="1", order_qty=1)
    await wrapped.subscribe_realtime_price("005930")
    with emergency_scope():
        await wrapped.place_stock_order("005930", 0, 1, False)
        await wrapped.cancel_stock_order(broker_order_no="2", order_qty=1)

    snapshot = limiter.snapshot()
    assert snapshot["_global"]["rate_wait_total"] == 7
    assert snapshot["_global"]["rate_wait_seconds_total"] == 14.0
    assert snapshot["_global"]["emergency"]["rate_wait_total"] == 1
    assert snapshot["_global"]["emergency"]["rate_wait_seconds_total"] == 0.5

    assert snapshot["quotation_price"]["acquired_total"] == 1
    assert snapshot["quotation_ohlcv"]["acquired_total"] == 1
    assert snapshot["account_balance"]["acquired_total"] == 1
    assert snapshot["account_reconciliation"]["acquired_total"] == 2
    assert snapshot["order_submit"]["acquired_total"] == 1
    assert snapshot["order_cancel"]["acquired_total"] == 1
    assert snapshot["websocket_subscribe"]["acquired_total"] == 1
    assert snapshot["order_submit"]["emergency"]["acquired_total"] == 1
    assert snapshot["order_cancel"]["emergency"]["acquired_total"] == 1

    assert sleeps == [0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 0.5]
    assert queue.fail_queue.empty()
    await queue.stop()


async def test_api_budget_limiter_keeps_emergency_path_available_during_normal_burst(test_logger):
    """normal 전역 bucket이 예약된 상태에서도 emergency 전역 bucket은 별도로 진입한다."""
    sleeps: list[float] = []
    now = 100.0

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    limiter = ApiBudgetLimiter(
        limits={"quotation_price": 8, "order_submit": 8},
        rate_limits_per_sec={
            "quotation_price": float("inf"),
            "order_submit": float("inf"),
        },
        emergency_limits={"order_submit": 8},
        emergency_rate_limits_per_sec={"order_submit": float("inf")},
        global_rate_limit_per_sec=1.0,
        emergency_global_rate_limit_per_sec=1.0,
        sleep=fake_sleep,
        monotonic=lambda: now,
    )
    queue = ApiRequestQueue(logger=test_logger, budget_limiter=limiter)
    client = _make_fake_client()
    wrapped = ClientWithRetryQueue(client, queue, budget_limiter=limiter)

    await wrapped.get_current_price("005930")
    await wrapped.get_current_price("000660")
    await wrapped.get_current_price("035420")
    with emergency_scope():
        await wrapped.place_stock_order("005930", 0, 1, False)

    snapshot = limiter.snapshot()
    assert snapshot["_global"]["rate_wait_total"] == 2
    assert snapshot["_global"]["emergency"]["rate_wait_total"] == 0
    assert snapshot["order_submit"]["emergency"]["acquired_total"] == 1
    assert client.place_stock_order.await_args.kwargs == {}
    assert sleeps == [1.0, 2.0]
    await queue.stop()
