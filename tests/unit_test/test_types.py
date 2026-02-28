# tests/unit_test/test_types.py
import pytest
from dataclasses import asdict
from common.types import (
    ResCommonResponse,
    ResStockFullInfoApiOutput,
    ResTopMarketCapApiItem,
    ResFluctuation,
    ResPriceSummary,
    TradeSignal,
)


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

def test_res_stock_full_info_output_from_dict_missing_fields():
    """ResStockFullInfoApiOutput.from_dict sets missing required fields to 'N/A'."""
    payload = {"stck_prpr": "70000"}  # Only provide one field
    item = ResStockFullInfoApiOutput.from_dict(payload)
    assert item.stck_prpr == "70000"
    assert item.acml_vol == "N/A"  # A required field that was missing
    assert item.stck_oprc == "N/A"


# --- Test for ResFluctuation ---

def test_res_fluctuation_from_dict_missing_fields():
    """ResFluctuation.from_dict sets missing fields to None."""
    payload = {"stck_shrn_iscd": "005930", "prdy_ctrt": "10.5"}
    item = ResFluctuation.from_dict(payload)
    assert item.stck_shrn_iscd == "005930"
    assert item.prdy_ctrt == "10.5"
    assert item.hts_kor_isnm is None  # A missing field
    assert item.acml_vol is None


# --- Test for other simple to_dict methods ---

def test_simple_dataclass_to_dict():
    """Test to_dict for other simple dataclasses."""
    signal = TradeSignal(code="005930", name="Samsung", action="BUY", price=70000)
    assert signal.to_dict()["code"] == "005930"

    price_summary = ResPriceSummary(symbol="005930", open=70000, current=71000, change_rate=1.4, prdy_ctrt=1.5)
    assert price_summary.to_dict()["symbol"] == "005930"