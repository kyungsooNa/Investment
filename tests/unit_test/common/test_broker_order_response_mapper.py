from pathlib import Path

from common.broker_order_response_mapper import BrokerOrderResponseMapper
from common.types import ErrorCode, OrderSide, OrderState, ResCommonResponse
from utils.kis_inquire_daily_ccld_fixture_utils import load_fixture_document


def test_extract_broker_order_no_from_submit_response_variants():
    mapper = BrokerOrderResponseMapper()

    assert mapper.extract_broker_order_no(None) is None
    assert mapper.extract_broker_order_no(ResCommonResponse(rt_cd="0", msg1="", data=None)) is None
    assert mapper.extract_broker_order_no(
        ResCommonResponse(rt_cd="0", msg1="", data={"ordno": "A0001"})
    ) == "A0001"
    assert mapper.extract_broker_order_no(
        ResCommonResponse(rt_cd="0", msg1="", data={"ODNO": "A0002"})
    ) == "A0002"
    assert mapper.extract_broker_order_no(
        ResCommonResponse(rt_cd="0", msg1="", data={"ODER_NO": "A0003"})
    ) == "A0003"
    assert mapper.extract_broker_order_no(
        ResCommonResponse(rt_cd="0", msg1="", data={"주문번호": "A0004"})
    ) == "A0004"
    assert mapper.extract_broker_order_no(
        ResCommonResponse(
            rt_cd="0",
            msg1="",
            data={"output": {"KRX_FWDG_ORD_ORGNO": "00950", "ODNO": "A0005"}},
        )
    ) == "A0005"


def test_extract_broker_order_no_from_object_payload():
    class Payload:
        ordno = "OBJ001"

    mapper = BrokerOrderResponseMapper()

    assert mapper.extract_broker_order_no(
        ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="", data=Payload())
    ) == "OBJ001"


def test_from_order_query_uses_real_kis_fixture_row():
    fixture_path = (
        Path(__file__).resolve().parents[2]
        / "fixtures"
        / "kis"
        / "inquire_daily_ccld_output1_real_20260601_001510.json"
    )
    document = load_fixture_document(fixture_path)
    sell_case = document["rows"][0]

    report = BrokerOrderResponseMapper.from_order_query(
        sell_case["row"],
        tr_id=document["tr_id"],
    )

    assert report.broker_order_no == sell_case["expected"]["broker_order_no"]
    assert report.stock_code == "001510"
    assert report.side == OrderSide.SELL
    assert report.event_state == OrderState.FILLED
    assert report.order_qty == 1
    assert report.fill_qty == 1
    assert report.remaining_qty == 0
    assert report.fill_price == 3330
    assert report.source == "polling:TTTC0081R"


def test_from_signing_notice_normalizes_kis_notice_payload():
    report = BrokerOrderResponseMapper.from_signing_notice(
        {
            "ODER_NO": "N0001",
            "OODER_NO": "",
            "STCK_SHRN_ISCD": "001510",
            "SELN_BYOV_CLS": "02",
            "ODER_QTY": "2",
            "CNTG_QTY": "1",
            "CNTG_UNPR": "3330",
            "CNTG_YN": "2",
            "ORD_EXG_GB": "KRX",
            "STCK_CNTG_HOUR": "141904",
        },
        tr_id="H0STCNI0",
    )

    assert report.broker_order_no == "N0001"
    assert report.stock_code == "001510"
    assert report.side == OrderSide.BUY
    assert report.event_state == OrderState.PARTIAL_FILLED
    assert report.order_qty == 2
    assert report.fill_qty == 1
    assert report.fill_price == 3330
    assert report.source == "websocket:H0STCNI0"

