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
async def test_get_overseas_dailyprice_defaults_bymd_when_date_omitted(overseas_api):
    await overseas_api.get_overseas_dailyprice(
        "AAPL",
        exchange=OverseasExchange.NASD,
        period="D",
    )

    bymd = overseas_api.call_api.await_args.kwargs["params"]["BYMD"]
    assert bymd.isdigit()
    assert len(bymd) == 8


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


# --- 정적 헬퍼 ---

def test_split_account_variants():
    assert KoreaInvestOverseasStockApi._split_account("12345678-99") == ("12345678", "99")
    # 대시 없음 / 잘못된 형식 → 기본 상품코드 "01"
    assert KoreaInvestOverseasStockApi._split_account("12345678") == ("12345678", "01")
    assert KoreaInvestOverseasStockApi._split_account("") == ("", "01")


def test_as_exchange_from_string():
    assert KoreaInvestOverseasStockApi._as_exchange("nasd") == OverseasExchange.NASD
    assert KoreaInvestOverseasStockApi._as_exchange(OverseasExchange.NYSE) == OverseasExchange.NYSE


def test_to_float_and_to_int_invalid_returns_default():
    assert KoreaInvestOverseasStockApi._to_float("abc") == 0.0
    assert KoreaInvestOverseasStockApi._to_float("abc", default=-1.0) == -1.0
    assert KoreaInvestOverseasStockApi._to_float("1,234.5") == 1234.5
    assert KoreaInvestOverseasStockApi._to_int("abc") == 0
    assert KoreaInvestOverseasStockApi._to_int("abc", default=7) == 7
    assert KoreaInvestOverseasStockApi._to_int("1,200") == 1200


# --- 조회 응답 처리 ---

@pytest.mark.asyncio
async def test_get_overseas_price_returns_failure_response(overseas_api):
    overseas_api.call_api = AsyncMock(
        return_value=ResCommonResponse(rt_cd=ErrorCode.API_ERROR.value, msg1="에러", data=None)
    )
    resp = await overseas_api.get_overseas_price("AAPL")
    assert resp.rt_cd == ErrorCode.API_ERROR.value


@pytest.mark.asyncio
async def test_get_overseas_price_normalizes_output(overseas_api):
    overseas_api.call_api = AsyncMock(
        return_value=ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value,
            msg1="OK",
            data={"output": {"last": "150.5", "rate": "1.2", "tvol": "1000", "xymd": "20260512"}},
        )
    )
    resp = await overseas_api.get_overseas_price("aapl", exchange="nasd")
    summary = resp.data
    assert summary.symbol == "AAPL"
    assert summary.price == 150.5
    assert summary.change_rate == 1.2
    assert summary.volume == 1000
    assert summary.timestamp == "20260512"


@pytest.mark.asyncio
async def test_get_overseas_price_handles_non_dict_output(overseas_api):
    overseas_api.call_api = AsyncMock(
        return_value=ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data={"output": "weird"}
        )
    )
    resp = await overseas_api.get_overseas_price("AAPL")
    assert resp.data.price == 0.0  # output 비-dict → 기본값


@pytest.mark.asyncio
async def test_get_overseas_dailyprice_invalid_period(overseas_api):
    resp = await overseas_api.get_overseas_dailyprice("AAPL", period="X")
    assert resp.rt_cd == ErrorCode.INVALID_INPUT.value
    overseas_api.call_api.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_overseas_balance_params(overseas_api):
    await overseas_api.get_overseas_balance(exchange=OverseasExchange.NASD, currency="USD")
    assert overseas_api.call_api.await_args.args[1] == EndpointKey.OVERSEAS_STOCK_INQUIRE_BALANCE
    params = overseas_api.call_api.await_args.kwargs["params"]
    assert params["CANO"] == "12345678"
    assert params["OVRS_EXCG_CD"] == "NASD"
    assert params["TR_CRCY_CD"] == "USD"


@pytest.mark.asyncio
async def test_inquire_overseas_ccnl_params(overseas_api):
    await overseas_api.inquire_overseas_ccnl(
        symbol="AAPL",
        exchange=OverseasExchange.NASD,
        start_date="20260101",
        end_date="20260131",
    )
    assert overseas_api.call_api.await_args.args[1] == EndpointKey.OVERSEAS_STOCK_INQUIRE_CCNL
    params = overseas_api.call_api.await_args.kwargs["params"]
    assert params["PDNO"] == "AAPL"
    assert params["ORD_STRT_DT"] == "20260101"
    assert params["ORD_END_DT"] == "20260131"


@pytest.mark.asyncio
async def test_inquire_overseas_unfilled_params(overseas_api):
    await overseas_api.inquire_overseas_unfilled(exchange=OverseasExchange.NYSE)
    assert overseas_api.call_api.await_args.args[1] == EndpointKey.OVERSEAS_STOCK_INQUIRE_NCCS
    assert overseas_api.call_api.await_args.kwargs["params"]["OVRS_EXCG_CD"] == "NYSE"


# --- 주문/취소 분기 ---

@pytest.mark.asyncio
async def test_place_order_invalid_side(overseas_api):
    resp = await overseas_api.place_overseas_limit_order(
        symbol="AAPL", exchange=OverseasExchange.NASD, side="hold", qty=1, limit_price="10"
    )
    assert resp.rt_cd == ErrorCode.INVALID_INPUT.value
    overseas_api.call_api.assert_not_awaited()


@pytest.mark.asyncio
async def test_place_order_invalid_qty(overseas_api):
    resp = await overseas_api.place_overseas_limit_order(
        symbol="AAPL", exchange=OverseasExchange.NASD, side="buy", qty=0, limit_price="10"
    )
    assert resp.rt_cd == ErrorCode.INVALID_INPUT.value
    overseas_api.call_api.assert_not_awaited()


@pytest.mark.asyncio
async def test_place_order_hashkey_failure(overseas_api):
    overseas_api._get_hashkey = AsyncMock(return_value=None)
    resp = await overseas_api.place_overseas_limit_order(
        symbol="AAPL", exchange=OverseasExchange.NASD, side="buy", qty=1, limit_price="10"
    )
    assert resp.rt_cd == ErrorCode.MISSING_KEY.value
    overseas_api.call_api.assert_not_awaited()


@pytest.mark.asyncio
async def test_place_order_returns_failure_response(overseas_api):
    overseas_api._get_hashkey = AsyncMock(return_value="HASH")
    overseas_api.call_api = AsyncMock(
        return_value=ResCommonResponse(rt_cd=ErrorCode.API_ERROR.value, msg1="거부", data=None)
    )
    resp = await overseas_api.place_overseas_limit_order(
        symbol="AAPL", exchange=OverseasExchange.NASD, side="sell", qty=1, limit_price="10"
    )
    assert resp.rt_cd == ErrorCode.API_ERROR.value


@pytest.mark.asyncio
async def test_place_order_builds_report_with_order_no(overseas_api):
    overseas_api._get_hashkey = AsyncMock(return_value="HASH")
    overseas_api.call_api = AsyncMock(
        return_value=ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data={"output": {"ODNO": "0001234"}}
        )
    )
    resp = await overseas_api.place_overseas_limit_order(
        symbol="aapl", exchange=OverseasExchange.NASD, side="buy", qty=2, limit_price="150.25"
    )
    assert resp.rt_cd == ErrorCode.SUCCESS.value
    assert resp.data.broker_order_no == "0001234"
    assert resp.data.symbol == "AAPL"
    assert resp.data.qty == 2


@pytest.mark.asyncio
async def test_cancel_overseas_order_success(overseas_api):
    overseas_api._get_hashkey = AsyncMock(return_value="HASH")
    await overseas_api.cancel_overseas_order(
        symbol="AAPL",
        exchange=OverseasExchange.NASD,
        original_order_no="0001234",
        qty=1,
        limit_price="150.0",
    )
    assert overseas_api.call_api.await_args.args[1] == EndpointKey.OVERSEAS_STOCK_ORDER_RVSECNCL
    body = overseas_api.call_api.await_args.kwargs["data"]
    assert body["ORGN_ODNO"] == "0001234"
    assert body["RVSE_CNCL_DVSN_CD"] == "02"


@pytest.mark.asyncio
async def test_cancel_overseas_order_hashkey_failure(overseas_api):
    overseas_api._get_hashkey = AsyncMock(return_value=None)
    resp = await overseas_api.cancel_overseas_order(
        symbol="AAPL",
        exchange=OverseasExchange.NASD,
        original_order_no="0001234",
        qty=1,
        limit_price="150.0",
    )
    assert resp.rt_cd == ErrorCode.MISSING_KEY.value
    overseas_api.call_api.assert_not_awaited()
