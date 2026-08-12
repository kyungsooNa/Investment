"""홈 지수 패널용 지수 수급(투자자 순매수 + 등락 종목수) 서비스 단위 테스트."""
from datetime import datetime

import pytest
from unittest.mock import AsyncMock, MagicMock

from services.stock_query_service import StockQueryService
from common.types import ResCommonResponse, ErrorCode


def _service(broker):
    clock = MagicMock()
    clock.get_current_kst_time.return_value = datetime(2026, 8, 12, 15, 40)
    return StockQueryService(
        market_data_service=MagicMock(),
        logger=MagicMock(),
        market_clock=clock,
        broker_api_wrapper=broker,
    )


def _ok(data):
    return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="정상", data=data)


def _fail():
    return ResCommonResponse(rt_cd=ErrorCode.API_ERROR.value, msg1="모의투자 미지원", data=None)


def _broker(investor=None, breadth=None):
    broker = MagicMock()
    broker.inquire_investor_daily_by_market = AsyncMock(
        return_value=investor if investor is not None else _ok([{
            "stck_bsop_date": "20260812",
            # 단위: 백만원
            "prsn_ntby_tr_pbmn": "-3191200",
            "frgn_ntby_tr_pbmn": "2835700",
            "orgn_ntby_tr_pbmn": "527700",
        }])
    )
    broker.inquire_index_price = AsyncMock(
        return_value=breadth if breadth is not None else _ok({
            "ascn_issu_cnt": "380",
            "uplm_issu_cnt": "3",
            "stnr_issu_cnt": "53",
            "down_issu_cnt": "477",
            "lslm_issu_cnt": "0",
        })
    )
    return broker


@pytest.mark.asyncio
async def test_index_flow_normalizes_investors_to_eok():
    result = await _service(_broker()).get_index_flow("0001")

    assert result.rt_cd == ErrorCode.SUCCESS.value
    # KIS 는 백만원 단위로 준다. 네이버 카드와 같은 억원 표기로 맞춘다.
    assert result.data["investors"] == {
        "individual": -31912,
        "foreign": 28357,
        "institution": 5277,
    }


@pytest.mark.asyncio
async def test_index_flow_normalizes_breadth_counts():
    result = await _service(_broker()).get_index_flow("0001")

    assert result.data["breadth"] == {
        "up": 380,
        "upper_limit": 3,
        "unchanged": 53,
        "down": 477,
        "lower_limit": 0,
    }


@pytest.mark.asyncio
async def test_index_flow_queries_today_for_the_requested_index():
    broker = _broker()

    await _service(broker).get_index_flow("1001")

    broker.inquire_investor_daily_by_market.assert_awaited_once_with("1001", date="20260812")
    broker.inquire_index_price.assert_awaited_once_with("1001")


@pytest.mark.asyncio
async def test_index_flow_keeps_breadth_when_investor_query_fails():
    # 모의투자에서는 한쪽만 막힐 수 있다. 살아있는 쪽은 그대로 보여준다.
    result = await _service(_broker(investor=_fail())).get_index_flow("0001")

    assert result.rt_cd == ErrorCode.SUCCESS.value
    assert result.data["investors"] is None
    assert result.data["breadth"]["up"] == 380


@pytest.mark.asyncio
async def test_index_flow_keeps_investors_when_breadth_query_fails():
    result = await _service(_broker(breadth=_fail())).get_index_flow("0001")

    assert result.rt_cd == ErrorCode.SUCCESS.value
    assert result.data["breadth"] is None
    assert result.data["investors"]["foreign"] == 28357


@pytest.mark.asyncio
async def test_index_flow_fails_when_both_queries_fail():
    result = await _service(_broker(investor=_fail(), breadth=_fail())).get_index_flow("0001")

    assert result.rt_cd != ErrorCode.SUCCESS.value
    assert result.data is None


@pytest.mark.asyncio
async def test_index_flow_rejects_unsupported_index_without_calling_broker():
    broker = _broker()

    result = await _service(broker).get_index_flow("2001")

    assert result.rt_cd == ErrorCode.INVALID_INPUT.value
    broker.inquire_investor_daily_by_market.assert_not_awaited()
    broker.inquire_index_price.assert_not_awaited()


@pytest.mark.asyncio
async def test_index_flow_drops_investors_when_fields_are_unparsable():
    result = await _service(_broker(investor=_ok([{"stck_bsop_date": "20260812"}]))).get_index_flow("0001")

    assert result.data["investors"] is None


@pytest.mark.asyncio
async def test_index_flow_survives_broker_exception():
    broker = _broker()
    broker.inquire_index_price = AsyncMock(side_effect=RuntimeError("boom"))

    result = await _service(broker).get_index_flow("0001")

    assert result.rt_cd == ErrorCode.SUCCESS.value
    assert result.data["breadth"] is None
    assert result.data["investors"]["individual"] == -31912
