"""KoreaInvestApiClient 의 위임 계약 테이블 테스트.

클라이언트는 도메인 API(quotations/account/trading/overseas/websocket) 로 인자를
그대로 넘기는 얇은 층이다. 위임처와 인자 전달을 표로 고정해, 메서드가 엉뚱한
하위 API 로 붙거나 인자가 누락되는 회귀를 잡는다.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from brokers.korea_investment.korea_invest_client import KoreaInvestApiClient
from common.types import Exchange


@pytest.fixture
def client(mocker):
    """생성자의 실제 HTTP/provider 조립을 건너뛰고 하위 API 만 mock 으로 채운다."""
    mocker.patch.object(KoreaInvestApiClient, "__init__", lambda self, *a, **k: None)
    instance = KoreaInvestApiClient()
    instance._logger = MagicMock()
    instance._env = MagicMock()
    instance.market_clock = MagicMock()
    for attr in ("_quotations", "_account", "_trading", "_overseas_stock", "_websocketAPI"):
        setattr(instance, attr, MagicMock())
    return instance


# (클라이언트 메서드, 하위 API 속성, 하위 메서드, 인자, 키워드)
ASYNC_DELEGATIONS = [
    ("inquire_unfilled_orders", "_account", "inquire_unfilled_orders", (), {"exchange": Exchange.KRX}),
    ("inquire_filled_history", "_account", "inquire_filled_history", (), {"start_date": "20260801"}),
    ("get_overseas_price", "_overseas_stock", "get_overseas_price", ("AAPL",), {}),
    ("get_overseas_dailyprice", "_overseas_stock", "get_overseas_dailyprice", ("AAPL",), {}),
    ("get_overseas_balance", "_overseas_stock", "get_overseas_balance", (), {}),
    ("inquire_overseas_ccnl", "_overseas_stock", "inquire_overseas_ccnl", (), {}),
    ("inquire_overseas_unfilled", "_overseas_stock", "inquire_overseas_unfilled", (), {}),
    ("place_overseas_limit_order", "_overseas_stock", "place_overseas_limit_order", (), {"symbol": "AAPL"}),
    ("cancel_overseas_order", "_overseas_stock", "cancel_overseas_order", (), {"order_no": "1"}),
    ("inquire_index_price", "_quotations", "inquire_index_price", ("0001",), {}),
    ("get_investor_trade_by_stock_daily", "_quotations", "get_investor_trade_by_stock_daily",
     ("005930", "20260801"), {}),
    ("get_program_trade_by_stock_daily", "_quotations", "get_program_trade_by_stock_daily",
     ("005930", "20260801"), {}),
    ("check_holiday", "_quotations", "check_holiday", ("20260801",), {}),
    ("subscribe_nxt_price", "_websocketAPI", "subscribe_nxt_price", ("005930",), {}),
    ("unsubscribe_nxt_price", "_websocketAPI", "unsubscribe_nxt_price", ("005930",), {}),
    ("subscribe_order_notice", "_websocketAPI", "subscribe_order_notice", (), {}),
    ("unsubscribe_order_notice", "_websocketAPI", "unsubscribe_order_notice", (), {}),
    ("subscribe_market_status", "_websocketAPI", "subscribe_market_status", ("005930",), {}),
    ("unsubscribe_market_status", "_websocketAPI", "unsubscribe_market_status", ("005930",), {}),
    ("subscribe_index_futures_contract", "_websocketAPI", "subscribe_index_futures_contract",
     ("101W09",), {}),
    ("unsubscribe_index_futures_contract", "_websocketAPI", "unsubscribe_index_futures_contract",
     ("101W09",), {}),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("method, attr, target, args, kwargs", ASYNC_DELEGATIONS)
async def test_async_methods_delegate_to_the_owning_api(client, method, attr, target, args, kwargs):
    sentinel = object()
    setattr(getattr(client, attr), target, AsyncMock(return_value=sentinel))

    result = await getattr(client, method)(*args, **kwargs)

    assert result is sentinel
    getattr(getattr(client, attr), target).assert_awaited_once()


@pytest.mark.asyncio
async def test_multi_day_investor_and_program_queries_forward_the_day_count(client):
    client._quotations.get_investor_trade_by_stock_daily_multi = AsyncMock(return_value="투자자")
    client._quotations.get_program_trade_by_stock_daily_multi = AsyncMock(return_value="프로그램")

    assert await client.get_investor_trade_by_stock_daily_multi("005930", "20260801", 5) == "투자자"
    assert await client.get_program_trade_by_stock_daily_multi("005930", "20260801", 5) == "프로그램"

    client._quotations.get_investor_trade_by_stock_daily_multi.assert_awaited_once_with(
        "005930", "20260801", 5
    )
    client._quotations.get_program_trade_by_stock_daily_multi.assert_awaited_once_with(
        "005930", "20260801", 5
    )


@pytest.mark.asyncio
async def test_index_chart_queries_forward_their_date_range(client):
    client._quotations.inquire_daily_indexchartprice = AsyncMock(return_value="일봉")
    client._quotations.inquire_time_indexchartprice = AsyncMock(return_value="분봉")

    assert await client.inquire_daily_indexchartprice(
        "0001", start_date="20260701", end_date="20260801"
    ) == "일봉"
    assert await client.inquire_time_indexchartprice("0001", 60) == "분봉"

    assert client._quotations.inquire_daily_indexchartprice.await_args.args[0] == "0001"
    assert client._quotations.inquire_time_indexchartprice.await_args.args[0] == "0001"


@pytest.mark.asyncio
async def test_investor_daily_by_market_forwards_the_date(client):
    client._quotations.inquire_investor_daily_by_market = AsyncMock(return_value="수급")

    assert await client.inquire_investor_daily_by_market("0001", date="20260801") == "수급"
    client._quotations.inquire_investor_daily_by_market.assert_awaited_once_with(
        "0001", date="20260801"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method, target",
    [
        ("wait_for_unified_price_ack", "wait_for_unified_price_ack"),
        ("wait_for_program_trading_ack", "wait_for_program_trading_ack"),
        ("wait_for_market_status_ack", "wait_for_market_status_ack"),
    ],
)
async def test_subscription_ack_waiters_forward_code_and_timeout(client, method, target):
    setattr(client._websocketAPI, target, AsyncMock(return_value=True))

    assert await getattr(client, method)("005930", 3.0) is True
    getattr(client._websocketAPI, target).assert_awaited_once_with("005930", 3.0)


@pytest.mark.asyncio
async def test_index_futures_ack_waiter_forwards_the_futures_code(client):
    client._websocketAPI.wait_for_index_futures_contract_ack = AsyncMock(return_value=True)

    assert await client.wait_for_index_futures_contract_ack("101W09", 3.0) is True
    client._websocketAPI.wait_for_index_futures_contract_ack.assert_awaited_once_with(
        "101W09", 3.0
    )


def test_receive_alive_probe_is_synchronous_and_delegated(client):
    client._websocketAPI.is_receive_alive = MagicMock(return_value=False)

    assert client.is_websocket_receive_alive() is False
    client._websocketAPI.is_receive_alive.assert_called_once()
