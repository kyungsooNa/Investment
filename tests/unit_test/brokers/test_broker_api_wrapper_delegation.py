"""BrokerAPIWrapper 의 위임 계약 테이블 테스트.

래퍼 메서드 대부분은 `KoreaInvestApiClient` 로 인자를 그대로 넘기는 한 줄짜리
층이다. 위임처와 인자 전달을 표로 고정해, 메서드가 엉뚱한 클라이언트 메서드에
붙거나 인자가 누락되는 회귀를 잡는다. 해외주식 주문의 서킷 브레이커 분기는
위임이 아니라 판단 로직이므로 별도 테스트로 다룬다.
"""
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from brokers.broker_api_wrapper import BrokerAPIWrapper
from common.types import ErrorCode, Exchange, ResCommonResponse


@pytest.fixture
def wrapper(mocker):
    """생성자의 실제 클라이언트/캐시/재시도 조립을 건너뛴 래퍼."""
    mocker.patch.object(BrokerAPIWrapper, "__init__", lambda self, *a, **k: None)
    instance = BrokerAPIWrapper()
    instance._client = MagicMock()
    instance._logger = MagicMock()
    instance._cb_threshold = 3
    instance._cb_timeout_min = 5
    instance._cb_consecutive_failures = 0
    instance._cb_open_until = None
    return instance


# (래퍼 메서드, 클라이언트 메서드, 인자, 키워드)
ASYNC_DELEGATIONS = [
    ("inquire_daily_indexchartprice", "inquire_daily_indexchartprice",
     ("0001", "20260701", "20260801"), {}),
    ("inquire_time_indexchartprice", "inquire_time_indexchartprice", ("0001",), {}),
    ("inquire_index_price", "inquire_index_price", ("0001",), {}),
    ("inquire_investor_daily_by_market", "inquire_investor_daily_by_market", ("0001",), {}),
    ("inquire_daily_ccld", "inquire_daily_ccld", (), {"start_date": "20260801"}),
    ("inquire_unfilled_orders", "inquire_unfilled_orders", (), {"exchange": Exchange.NXT}),
    ("inquire_filled_history", "inquire_filled_history", (), {"start_date": "20260801"}),
    ("get_overseas_price", "get_overseas_price", ("AAPL",), {}),
    ("get_overseas_dailyprice", "get_overseas_dailyprice", ("AAPL",), {}),
    ("get_overseas_balance", "get_overseas_balance", (), {}),
    ("inquire_overseas_ccnl", "inquire_overseas_ccnl", (), {}),
    ("inquire_overseas_unfilled", "inquire_overseas_unfilled", (), {}),
    ("cancel_overseas_order", "cancel_overseas_order", (), {"order_no": "1"}),
    ("subscribe_unified_price", "subscribe_unified_price", ("005930",), {}),
    ("subscribe_nxt_price", "subscribe_nxt_price", ("005930",), {}),
    ("unsubscribe_nxt_price", "unsubscribe_nxt_price", ("005930",), {}),
    ("subscribe_order_notice", "subscribe_order_notice", (), {}),
    ("unsubscribe_order_notice", "unsubscribe_order_notice", (), {}),
    ("subscribe_market_status", "subscribe_market_status", ("005930",), {}),
    ("unsubscribe_market_status", "unsubscribe_market_status", ("005930",), {}),
    ("subscribe_index_futures_contract", "subscribe_index_futures_contract", ("101W09",), {}),
    ("unsubscribe_index_futures_contract", "unsubscribe_index_futures_contract", ("101W09",), {}),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("method, target, args, kwargs", ASYNC_DELEGATIONS)
async def test_async_methods_delegate_to_the_client(wrapper, method, target, args, kwargs):
    sentinel = object()
    setattr(wrapper._client, target, AsyncMock(return_value=sentinel))

    result = await getattr(wrapper, method)(*args, **kwargs)

    assert result is sentinel
    getattr(wrapper._client, target).assert_awaited_once()


@pytest.mark.asyncio
async def test_index_chart_delegations_forward_their_range_arguments(wrapper):
    wrapper._client.inquire_daily_indexchartprice = AsyncMock(return_value="일봉")
    wrapper._client.inquire_time_indexchartprice = AsyncMock(return_value="분봉")

    await wrapper.inquire_daily_indexchartprice("0001", "20260701", "20260801", period="W")
    await wrapper.inquire_time_indexchartprice("0001", interval_seconds=300)

    wrapper._client.inquire_daily_indexchartprice.assert_awaited_once_with(
        "0001", start_date="20260701", end_date="20260801", period="W"
    )
    wrapper._client.inquire_time_indexchartprice.assert_awaited_once_with(
        "0001", interval_seconds=300
    )


@pytest.mark.asyncio
async def test_investor_daily_by_market_forwards_the_date(wrapper):
    wrapper._client.inquire_investor_daily_by_market = AsyncMock(return_value="수급")

    await wrapper.inquire_investor_daily_by_market("0001", date="20260801")

    wrapper._client.inquire_investor_daily_by_market.assert_awaited_once_with(
        "0001", date="20260801"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method, target, code",
    [
        ("wait_unified_price_ack", "wait_for_unified_price_ack", "005930"),
        ("wait_program_trading_ack", "wait_for_program_trading_ack", "005930"),
        ("wait_market_status_ack", "wait_for_market_status_ack", "005930"),
        ("wait_index_futures_contract_ack", "wait_for_index_futures_contract_ack", "101W09"),
    ],
)
async def test_ack_waiters_forward_code_and_timeout(wrapper, method, target, code):
    setattr(wrapper._client, target, AsyncMock(return_value=True))

    assert await getattr(wrapper, method)(code, 3.0) is True
    getattr(wrapper._client, target).assert_awaited_once_with(code, 3.0)


# --- 해외주식 주문 서킷 브레이커 -------------------------------------------

def _resp(rt_cd, msg1="msg"):
    return ResCommonResponse(rt_cd=rt_cd, msg1=msg1, data=None)


@pytest.mark.asyncio
async def test_overseas_order_is_blocked_while_the_circuit_is_open(wrapper):
    wrapper._cb_open_until = datetime.now() + timedelta(minutes=7)
    wrapper._client.place_overseas_limit_order = AsyncMock()

    resp = await wrapper.place_overseas_limit_order(symbol="AAPL")

    assert resp.rt_cd == ErrorCode.API_ERROR.value
    assert "서킷 브레이커 개방" in resp.msg1
    wrapper._client.place_overseas_limit_order.assert_not_awaited()
    wrapper._logger.warning.assert_called_once()


@pytest.mark.asyncio
async def test_overseas_order_success_clears_the_failure_streak(wrapper):
    wrapper._cb_consecutive_failures = 2
    wrapper._client.place_overseas_limit_order = AsyncMock(
        return_value=_resp(ErrorCode.SUCCESS.value)
    )

    await wrapper.place_overseas_limit_order(symbol="AAPL")

    assert wrapper._cb_consecutive_failures == 0


@pytest.mark.asyncio
async def test_overseas_order_business_rejection_is_not_counted_as_a_failure(wrapper, mocker):
    mocker.patch("brokers.broker_api_wrapper.is_non_retriable_business_error", return_value=True)
    wrapper._client.place_overseas_limit_order = AsyncMock(
        return_value=_resp(ErrorCode.API_ERROR.value, "주문가능금액 부족")
    )

    await wrapper.place_overseas_limit_order(symbol="AAPL")

    assert wrapper._cb_consecutive_failures == 0
    wrapper._logger.warning.assert_called_once()


@pytest.mark.asyncio
async def test_overseas_order_failures_open_the_circuit_at_the_threshold(wrapper, mocker):
    mocker.patch("brokers.broker_api_wrapper.is_non_retriable_business_error", return_value=False)
    wrapper._client.place_overseas_limit_order = AsyncMock(
        return_value=_resp(ErrorCode.API_ERROR.value)
    )

    for _ in range(wrapper._cb_threshold):
        await wrapper.place_overseas_limit_order(symbol="AAPL")

    assert wrapper._cb_consecutive_failures == wrapper._cb_threshold
    assert wrapper._cb_open_until is not None


@pytest.mark.asyncio
async def test_overseas_order_treats_a_missing_response_as_a_failure(wrapper, mocker):
    mocker.patch("brokers.broker_api_wrapper.is_non_retriable_business_error", return_value=False)
    wrapper._client.place_overseas_limit_order = AsyncMock(return_value=None)

    assert await wrapper.place_overseas_limit_order(symbol="AAPL") is None
    assert wrapper._cb_consecutive_failures == 1


def test_subscription_ledger_probe_is_synchronous_and_delegated(wrapper):
    wrapper._client.get_subscription_ledger = MagicMock(return_value={"005930": "active"})

    assert wrapper.get_subscription_ledger() == {"005930": "active"}
    wrapper._client.get_subscription_ledger.assert_called_once()
