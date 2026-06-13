from unittest.mock import AsyncMock, MagicMock

import pytest

from brokers.korea_investment.korea_invest_header_provider import KoreaInvestHeaderProvider
from brokers.korea_investment.korea_invest_overseas_stock_api import KoreaInvestOverseasStockApi
from brokers.korea_investment.korea_invest_trid_provider import KoreaInvestTrIdProvider
from brokers.korea_investment.korea_invest_url_keys import EndpointKey
from brokers.korea_investment.korea_invest_url_provider import KoreaInvestUrlProvider
from common.overseas_types import OverseasExchange
from common.types import ErrorCode, ResCommonResponse


@pytest.fixture
def overseas_env():
    env = MagicMock()
    env.is_paper_trading = True
    env.my_agent = "pytest-agent"
    env.active_config = {
        "api_key": "app",
        "api_secret_key": "secret",
        "stock_account_number": "12345678-01",
        "custtype": "P",
    }
    return env


@pytest.fixture
def overseas_api(overseas_env):
    paths = {
        "hashkey": "/uapi/hashkey",
        "overseas_stock_price": "/uapi/overseas-price/v1/quotations/price",
        "overseas_stock_dailyprice": "/uapi/overseas-price/v1/quotations/dailyprice",
        "overseas_stock_inquire_balance": "/uapi/overseas-stock/v1/trading/inquire-balance",
        "overseas_stock_inquire_ccnl": "/uapi/overseas-stock/v1/trading/inquire-ccnl",
        "overseas_stock_inquire_nccs": "/uapi/overseas-stock/v1/trading/inquire-nccs",
        "overseas_stock_order": "/uapi/overseas-stock/v1/trading/order",
        "overseas_stock_order_rvsecncl": "/uapi/overseas-stock/v1/trading/order-rvsecncl",
    }
    tr_ids = {
        "overseas_stock": {
            "price": "HHDFS00000300",
            "dailyprice": "HHDFS76240000",
            "inquire_balance_real": "TTTS3012R",
            "inquire_balance_paper": "VTTS3012R",
            "inquire_ccnl_real": "TTTS3035R",
            "inquire_ccnl_paper": "VTTS3035R",
            "inquire_nccs_real": "TTTS3018R",
            "inquire_nccs_paper": "VTTS3018R",
            "order_buy_real": "TTTS6036U",
            "order_sell_real": "TTTS6037U",
            "order_rvsecncl_real": "TTTS6038U",
        }
    }
    api = KoreaInvestOverseasStockApi(
        overseas_env,
        logger=MagicMock(),
        market_clock=MagicMock(),
        header_provider=KoreaInvestHeaderProvider(my_agent="pytest-agent"),
        url_provider=KoreaInvestUrlProvider(lambda: "https://mock.kis", paths),
        trid_provider=KoreaInvestTrIdProvider(overseas_env, tr_ids),
    )
    api.call_api = AsyncMock(
        return_value=ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data={"output": {}})
    )
    return api


@pytest.mark.asyncio
async def test_get_overseas_price_uses_overseas_endpoint_trid_and_params(overseas_api):
    await overseas_api.get_overseas_price("AAPL", exchange=OverseasExchange.NASD)

    overseas_api.call_api.assert_awaited_once()
    _, key = overseas_api.call_api.await_args.args[:2]
    assert key == EndpointKey.OVERSEAS_STOCK_PRICE
    assert overseas_api.call_api.await_args.kwargs["params"] == {
        "AUTH": "",
        "EXCD": "NAS",
        "SYMB": "AAPL",
    }
    assert overseas_api._headers.build()["tr_id"] == "HHDFS00000300"


@pytest.mark.asyncio
async def test_get_overseas_dailyprice_uses_period_and_date_params(overseas_api):
    await overseas_api.get_overseas_dailyprice(
        "MSFT",
        exchange=OverseasExchange.NYSE,
        start_date="20260101",
        end_date="20260131",
        period="D",
    )

    assert overseas_api.call_api.await_args.args[1] == EndpointKey.OVERSEAS_STOCK_DAILYPRICE
    assert overseas_api.call_api.await_args.kwargs["params"] == {
        "AUTH": "",
        "EXCD": "NYS",
        "SYMB": "MSFT",
        "GUBN": "0",
        "BYMD": "20260131",
        "MODP": "1",
    }


@pytest.mark.asyncio
async def test_place_overseas_limit_order_uses_overseas_body_and_hashkey(overseas_api):
    overseas_api._get_hashkey = AsyncMock(return_value="HASH")

    await overseas_api.place_overseas_limit_order(
        symbol="AAPL",
        exchange=OverseasExchange.NASD,
        side="buy",
        qty=3,
        limit_price="150.25",
    )

    overseas_api._get_hashkey.assert_awaited_once()
    body = overseas_api._get_hashkey.await_args.args[0]
    assert body == {
        "CANO": "12345678",
        "ACNT_PRDT_CD": "01",
        "OVRS_EXCG_CD": "NASD",
        "PDNO": "AAPL",
        "ORD_QTY": "3",
        "OVRS_ORD_UNPR": "150.25",
        "ORD_SVR_DVSN_CD": "0",
        "ORD_DVSN": "00",
    }
    assert overseas_api.call_api.await_args.args[1] == EndpointKey.OVERSEAS_STOCK_ORDER
    assert overseas_api.call_api.await_args.kwargs["data"] == body
    assert overseas_api._headers.build()["tr_id"] == "TTTS6036U"


@pytest.mark.asyncio
async def test_place_overseas_order_blocks_market_order(overseas_api):
    resp = await overseas_api.place_overseas_limit_order(
        symbol="AAPL",
        exchange=OverseasExchange.NASD,
        side="buy",
        qty=1,
        limit_price="0",
    )

    assert resp.rt_cd == ErrorCode.ORDER_POLICY_BLOCKED.value
    assert "지정가" in resp.msg1
    overseas_api.call_api.assert_not_awaited()
