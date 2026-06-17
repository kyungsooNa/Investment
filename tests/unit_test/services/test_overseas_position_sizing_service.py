"""해외 캐너리 포지션 사이징 테스트 (Phase 4 — 고정 USD 슬롯).

실주문 경로 없음(순수 계산). 고정 USD 슬롯 ÷ 지정가 = 수량(floor).
환율은 KRW 환산 노출용 부가값이며 KIS 잔고 응답에서 관용 추출한다.
"""
import pytest

from services.overseas_position_sizing_service import (
    OverseasPositionSizingService,
    extract_fx_krw_per_usd,
)


def _svc(slot_usd=1000.0, max_qty=None):
    return OverseasPositionSizingService(slot_usd=slot_usd, max_qty=max_qty)


def test_qty_is_floor_of_slot_over_price():
    res = _svc(slot_usd=1000.0).size(limit_price_usd=150.0)
    assert res["qty"] == 6  # 1000/150 = 6.66 → 6
    assert res["notional_usd"] == pytest.approx(900.0)
    assert res["reason"] == "slot"


def test_invalid_price_returns_zero_qty():
    res = _svc().size(limit_price_usd=0.0)
    assert res["qty"] == 0
    assert res["reason"] == "invalid_price"


def test_slot_too_small_returns_zero_qty():
    res = _svc(slot_usd=100.0).size(limit_price_usd=150.0)
    assert res["qty"] == 0
    assert res["reason"] == "slot_too_small"


def test_available_usd_caps_qty():
    res = _svc(slot_usd=1000.0).size(limit_price_usd=150.0, available_usd=500.0)
    assert res["qty"] == 3  # 500/150 = 3.33 → 3 < 6
    assert res["reason"] == "capped_by_available_usd"


def test_insufficient_usd_returns_zero_qty():
    res = _svc(slot_usd=1000.0).size(limit_price_usd=150.0, available_usd=100.0)
    assert res["qty"] == 0
    assert res["reason"] == "insufficient_usd"


def test_max_qty_caps_qty():
    res = _svc(slot_usd=1000.0, max_qty=5).size(limit_price_usd=10.0)
    assert res["qty"] == 5  # 1000/10 = 100 → capped 5
    assert res["reason"] == "capped_by_max_qty"


def test_fx_yields_krw_exposure():
    res = _svc(slot_usd=1000.0).size(limit_price_usd=150.0, fx_krw_per_usd=1350.0)
    assert res["fx_krw_per_usd"] == 1350.0
    assert res["krw_exposure"] == pytest.approx(900.0 * 1350.0)


def test_no_fx_leaves_krw_exposure_none():
    res = _svc(slot_usd=1000.0).size(limit_price_usd=150.0)
    assert res["krw_exposure"] is None
    assert res["fx_krw_per_usd"] is None


def test_init_rejects_nonpositive_slot():
    with pytest.raises(ValueError):
        OverseasPositionSizingService(slot_usd=0.0)
    with pytest.raises(ValueError):
        OverseasPositionSizingService(slot_usd=1000.0, max_qty=0)


# --- extract_fx_krw_per_usd ---

def test_extract_fx_from_output2_dict():
    data = {"output2": {"frst_bltn_exrt": "1342.50"}}
    assert extract_fx_krw_per_usd(data) == pytest.approx(1342.50)


def test_extract_fx_from_output1_row():
    data = {"output1": [{"pdno": "AAPL", "bass_exrt": "1310.00"}]}
    assert extract_fx_krw_per_usd(data) == pytest.approx(1310.00)


def test_extract_fx_missing_returns_none():
    assert extract_fx_krw_per_usd({"output2": {"tot_evlu_amt": "1000"}}) is None


def test_extract_fx_nonpositive_returns_none():
    assert extract_fx_krw_per_usd({"output2": {"frst_bltn_exrt": "0"}}) is None


def test_extract_fx_non_dict_returns_none():
    assert extract_fx_krw_per_usd(None) is None
    assert extract_fx_krw_per_usd([1, 2, 3]) is None
