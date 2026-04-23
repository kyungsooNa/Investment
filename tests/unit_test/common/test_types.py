# tests/unit_test/test_types.py
import pytest
from dataclasses import asdict
from pathlib import Path
from pydantic import ValidationError
from common.types import (
    ResCommonResponse,
    ResStockFullInfoApiOutput,
    ResTopMarketCapApiItem,
    ResDailyChartApiItem,
    ResFluctuation,
    ResPriceSummary,
    TradeSignal,
    OrderExecutionReport,
    OrderSide,
    OrderState,
)
from utils.kis_inquire_daily_ccld_fixture_utils import discover_inquire_daily_ccld_fixture_documents


KIS_FIXTURE_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "kis"


def _load_inquire_daily_ccld_cases():
    cases = []
    for fixture_path, payload in discover_inquire_daily_ccld_fixture_documents(KIS_FIXTURE_DIR):
        fixture_name = payload.get("fixture_name") or fixture_path.stem
        tr_id = payload.get("tr_id", "")
        for row in payload["rows"]:
            case = dict(row)
            case["_fixture_name"] = fixture_name
            case["_fixture_path"] = fixture_path.name
            case["_tr_id"] = tr_id
            cases.append(case)
    return cases


def _case_id(case):
    return f"{case['_fixture_name']}:{case['case']}"


# --- Test for ResCommonResponse.to_dict ---

@pytest.fixture
def simple_dataclass():
    """A simple dataclass for serialization tests."""
    from dataclasses import dataclass

    @dataclass
    class SimpleData:
        id: int
        value: str

        def to_dict(self):
            return asdict(self)

    return SimpleData


def test_res_common_response_to_dict_with_dataclass(simple_dataclass):
    """ResCommonResponse.to_dict with a single dataclass in data."""
    data = simple_dataclass(id=1, value="test")
    resp = ResCommonResponse(rt_cd="0", msg1="OK", data=data)
    result = resp.to_dict()
    assert result == {
        "rt_cd": "0",
        "msg1": "OK",
        "data": {"id": 1, "value": "test"}
    }


def test_res_common_response_to_dict_with_list_of_dataclasses(simple_dataclass):
    """ResCommonResponse.to_dict with a list of dataclasses."""
    data = [simple_dataclass(id=1, value="a"), simple_dataclass(id=2, value="b")]
    resp = ResCommonResponse(rt_cd="0", msg1="OK", data=data)
    result = resp.to_dict()
    assert result == {
        "rt_cd": "0",
        "msg1": "OK",
        "data": [
            {"id": 1, "value": "a"},
            {"id": 2, "value": "b"}
        ]
    }


def test_res_common_response_to_dict_with_mixed_list(simple_dataclass):
    """ResCommonResponse.to_dict with a list of mixed types."""
    data = [
        simple_dataclass(id=1, value="a"),
        {"raw_id": 2},
        "primitive_string",
        [1, 2, 3]
    ]
    resp = ResCommonResponse(rt_cd="0", msg1="OK", data=data)
    result = resp.to_dict()
    assert result == {
        "rt_cd": "0",
        "msg1": "OK",
        "data": [
            {"id": 1, "value": "a"},
            {"raw_id": 2},
            "primitive_string",
            [1, 2, 3]
        ]
    }


def test_res_common_response_to_dict_with_dict_of_dataclasses(simple_dataclass):
    """ResCommonResponse.to_dict with a dictionary containing dataclasses."""
    data = {
        "item1": simple_dataclass(id=1, value="a"),
        "item2": simple_dataclass(id=2, value="b")
    }
    resp = ResCommonResponse(rt_cd="0", msg1="OK", data=data)
    result = resp.to_dict()
    assert result == {
        "rt_cd": "0",
        "msg1": "OK",
        "data": {
            "item1": {"id": 1, "value": "a"},
            "item2": {"id": 2, "value": "b"}
        }
    }


def test_res_common_response_to_dict_with_primitive_data():
    """ResCommonResponse.to_dict with primitive data types."""
    resp_int = ResCommonResponse(rt_cd="0", msg1="OK", data=123)
    assert resp_int.to_dict()["data"] == 123

    resp_str = ResCommonResponse(rt_cd="0", msg1="OK", data="hello")
    assert resp_str.to_dict()["data"] == "hello"

    resp_none = ResCommonResponse(rt_cd="0", msg1="OK", data=None)
    assert resp_none.to_dict()["data"] is None


# --- Test for ResTopMarketCapApiItem ---

def test_res_top_market_cap_item_post_init_compatibility():
    """ResTopMarketCapApiItem.__post_init__ for alias compatibility."""
    # iscd is None, mksc_shrn_iscd is set -> iscd should be populated
    item1 = ResTopMarketCapApiItem(mksc_shrn_iscd="005930", data_rank="1", hts_kor_isnm="Samsung", stck_avls="100")
    assert item1.iscd == "005930"

    # acc_trdvol is set, acml_vol is None -> acml_vol should be populated
    item2 = ResTopMarketCapApiItem(mksc_shrn_iscd="005930", data_rank="1", hts_kor_isnm="Samsung", stck_avls="100", acc_trdvol="12345")
    assert item2.acml_vol == "12345"

    # acml_vol is set, acc_trdvol is None -> acc_trdvol should be populated
    item3 = ResTopMarketCapApiItem(mksc_shrn_iscd="005930", data_rank="1", hts_kor_isnm="Samsung", stck_avls="100", acml_vol="54321")
    assert item3.acc_trdvol == "54321"


def test_res_top_market_cap_item_from_api():
    """ResTopMarketCapApiItem.from_api for alias compatibility."""
    payload = {"iscd": "005930", "data_rank": "1", "hts_kor_isnm": "Samsung", "stck_avls": "100", "acc_trdvol": "12345"}
    item = ResTopMarketCapApiItem.from_api(payload)
    assert item.mksc_shrn_iscd == "005930"
    assert item.acml_vol == "12345"


# --- Test for ResStockFullInfoApiOutput ---

def test_res_stock_full_info_output_from_dict_missing_required_fields_raises_error():
    """ResStockFullInfoApiOutput.from_dict raises ValidationError if required fields are missing."""
    payload = {"stck_prpr": "70000"}  # 하나만 제공
    with pytest.raises(ValidationError):
        ResStockFullInfoApiOutput.from_dict(payload)


# --- Test for ResFluctuation ---

def test_res_fluctuation_from_dict_missing_required_fields_raises_error():
    """ResFluctuation.from_dict raises ValidationError if required fields are missing."""
    payload = {"stck_shrn_iscd": "005930"}  # stck_prpr 누락
    with pytest.raises(ValidationError):
        ResFluctuation.from_dict(payload)

def test_res_daily_chart_api_item_missing_required_fields_raises_error():
    """ResDailyChartApiItem.from_dict raises ValidationError if required fields are missing."""
    payload = {"stck_bsop_date": "20260325"}
    with pytest.raises(ValidationError):
        ResDailyChartApiItem.from_dict(payload)


def test_order_query_report_missing_order_qty_does_not_raise():
    report = OrderExecutionReport.from_order_query({
        "odno": "A0001",
        "pdno": "005930",
        "sll_buy_dvsn_cd": "02",
        "tot_ccld_qty": "3",
        "avg_prvs": "70000",
    })

    assert report.order_qty is None
    assert report.event_state == OrderState.PARTIAL_FILLED
    assert report.cumulative_filled_qty == 3


def test_order_query_report_reject_qty_without_order_qty_is_rejected():
    report = OrderExecutionReport.from_order_query({
        "odno": "A0001",
        "pdno": "005930",
        "sll_buy_dvsn_cd": "02",
        "tot_ccld_qty": "0",
        "rjct_qty": "10",
    })

    assert report.order_qty is None
    assert report.event_state == OrderState.REJECTED


def test_order_query_report_cancel_qty_without_remaining_qty_is_canceled():
    report = OrderExecutionReport.from_order_query({
        "odno": "A0001",
        "pdno": "005930",
        "sll_buy_dvsn_cd": "02",
        "ord_qty": "10",
        "tot_ccld_qty": "4",
        "cncl_cfrm_qty": "6",
    })

    assert report.remaining_qty is None
    assert report.event_state == OrderState.CANCELED


def test_order_query_report_cancel_yn_without_remaining_qty_is_canceled():
    report = OrderExecutionReport.from_order_query({
        "odno": "A0001",
        "pdno": "005930",
        "sll_buy_dvsn_cd": "02",
        "ord_qty": "10",
        "tot_ccld_qty": "0",
        "cncl_yn": "Y",
    })

    assert report.remaining_qty is None
    assert report.event_state == OrderState.CANCELED


@pytest.mark.parametrize(
    "case",
    _load_inquire_daily_ccld_cases(),
    ids=_case_id,
)
def test_order_query_report_from_kis_inquire_daily_ccld_fixture(case):
    report = OrderExecutionReport.from_order_query(case["row"], tr_id=case["_tr_id"])
    expected = case["expected"]

    assert report.broker_order_no == expected["broker_order_no"]
    assert report.stock_code == expected["stock_code"]
    assert report.side == (OrderSide(expected["side"]) if expected["side"] else None)
    assert report.event_state == OrderState(expected["event_state"])
    assert report.order_qty == expected["order_qty"]
    assert report.fill_qty == expected["fill_qty"]
    assert report.cumulative_filled_qty == expected["cumulative_filled_qty"]
    assert report.remaining_qty == expected["remaining_qty"]
    assert report.fill_price == expected["fill_price"]
    assert report.event_time == expected["event_time"]
    assert report.source == (f"polling:{case['_tr_id']}" if case["_tr_id"] else "polling")
    assert report.raw == case["row"]


# --- Test for other simple to_dict methods ---

def test_simple_dataclass_to_dict():
    """Test to_dict for other simple dataclasses."""
    signal = TradeSignal(code="005930", name="Samsung", action="BUY", price=70000)
    assert signal.to_dict()["code"] == "005930"

    price_summary = ResPriceSummary(symbol="005930", open=70000, current=71000, change_rate=1.4, prdy_ctrt=1.5)
    assert price_summary.to_dict()["symbol"] == "005930"
