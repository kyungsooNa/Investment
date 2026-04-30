import time

import pytest

from common.types import ErrorCode, ResCommonResponse
from config.config_loader import DataQualityConfig
from services.data_quality_service import DataQualityService


def test_validate_api_response_ok():
    svc = DataQualityService()
    result = svc.validate_api_response(
        ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data={"output": {"stck_prpr": "70000"}}),
        code="005930",
        require_output=True,
    )

    assert result.ok is True
    assert result.reason == "ok"


def test_apply_trading_mode_uses_paper_threshold_profile():
    svc = DataQualityService(DataQualityConfig())

    svc.apply_trading_mode(is_paper_trading=True)
    health = svc.get_health()

    assert health["profile"] == "paper"
    assert health["max_tick_age_sec"] == 60.0
    assert health["max_rest_age_sec"] == 15.0
    assert health["max_price_jump_pct"] == 20.0


def test_apply_trading_mode_uses_real_threshold_profile():
    svc = DataQualityService(DataQualityConfig())

    svc.apply_trading_mode(is_paper_trading=False)
    health = svc.get_health()

    assert health["profile"] == "real"
    assert health["max_tick_age_sec"] == 30.0
    assert health["max_rest_age_sec"] == 10.0
    assert health["max_price_jump_pct"] == 15.0


def test_apply_trading_mode_allows_explicit_profile_overrides():
    svc = DataQualityService(
        DataQualityConfig(
            paper_max_tick_age_sec=45.0,
            paper_max_rest_age_sec=12.0,
            paper_max_price_jump_pct=18.0,
        )
    )

    svc.apply_trading_mode(is_paper_trading=True)
    health = svc.get_health()

    assert health["max_tick_age_sec"] == 45.0
    assert health["max_rest_age_sec"] == 12.0
    assert health["max_price_jump_pct"] == 18.0


def test_violation_history_records_and_filters_failures():
    svc = DataQualityService()

    svc.validate_api_response(ResCommonResponse(rt_cd="1", msg1="fail", data=None), code="005930")
    svc.validate_price_tick({"유가증권단축종목코드": "000660", "주식현재가": "-1", "누적거래량": "1"})

    all_items = svc.get_violation_history()
    by_code = svc.get_violation_history(code="005930")
    by_reason = svc.get_violation_history(reason="invalid_tick")
    health = svc.get_health()

    assert len(all_items) == 2
    assert by_code[0]["code"] == "005930"
    assert by_reason[0]["code"] == "000660"
    assert health["violation_count"] == 2


def test_validate_api_response_required_data_keys():
    svc = DataQualityService()

    result = svc.validate_api_response(
        ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data={"output": {}}),
        code="005930",
        required_data_keys=["output1"],
    )

    assert result.ok is False
    assert result.reason == "rest_invalid"
    assert result.metadata["missing_keys"] == ["output1"]


def test_validate_api_response_failure_and_invalid_output():
    svc = DataQualityService()

    failed = svc.validate_api_response(ResCommonResponse(rt_cd="1", msg1="fail", data=None), code="005930")
    invalid = svc.validate_api_response(
        ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data={}),
        code="005930",
        require_output=True,
    )

    assert failed.ok is False
    assert failed.reason == "rest_failed"
    assert invalid.ok is False
    assert invalid.reason == "rest_invalid"


def test_validate_price_tick_rejects_missing_negative_and_range():
    svc = DataQualityService()

    missing = svc.validate_price_tick({"유가증권단축종목코드": "005930"})
    negative = svc.validate_price_tick({"유가증권단축종목코드": "005930", "주식현재가": "-1"})
    out_of_range = svc.validate_price_tick({
        "유가증권단축종목코드": "005930",
        "주식현재가": "120",
        "누적거래량": "1",
        "주식최고가": "110",
        "주식최저가": "90",
    })

    assert missing.reason == "invalid_tick"
    assert negative.reason == "invalid_tick"
    assert out_of_range.reason == "invalid_tick"


def test_validate_price_tick_rejects_jump_and_latency(monkeypatch):
    svc = DataQualityService(DataQualityConfig(max_price_jump_pct=10.0, max_tick_age_sec=5.0))
    assert svc.validate_price_tick({"유가증권단축종목코드": "005930", "주식현재가": "100", "누적거래량": "1"}).ok

    jump = svc.validate_price_tick({"유가증권단축종목코드": "005930", "주식현재가": "120", "누적거래량": "1"})
    latency = svc.validate_price_tick({
        "유가증권단축종목코드": "000660",
        "주식현재가": "100",
        "누적거래량": "1",
        "source_ts": str(time.time() - 10),
    })

    assert jump.ok is False
    assert jump.reason == "invalid_tick"
    assert latency.ok is False
    assert latency.reason == "latency_exceeded"


def test_validate_execution_report_missing_fields():
    svc = DataQualityService()

    bad = svc.validate_execution_report({"주문번호": "1"})
    ok = svc.validate_execution_report({
        "주문번호": "1",
        "주식단축종목코드": "005930",
        "매도매수구분": "02",
        "체결수량": "1",
        "체결단가": "70000",
        "주식체결시간": "090001",
    })

    assert bad.ok is False
    assert bad.reason == "invalid_execution_report"
    assert ok.ok is True


@pytest.mark.asyncio
async def test_validate_order_reference_blocks_stale_and_outlier():
    class PriceStream:
        def __init__(self, cached):
            self.cached = cached

        def get_cached_price(self, code):
            return self.cached

    stale = DataQualityService(
        DataQualityConfig(max_tick_age_sec=1.0),
        price_stream_service=PriceStream({"price": "70000", "received_at": time.time() - 5}),
    )
    outlier = DataQualityService(
        DataQualityConfig(max_price_jump_pct=10.0),
        price_stream_service=PriceStream({"price": "70000", "received_at": time.time()}),
    )

    stale_result = await stale.validate_order_reference(stock_code="005930", price=70000, qty=1)
    outlier_result = await outlier.validate_order_reference(stock_code="005930", price=90000, qty=1)

    assert stale_result.ok is False
    assert stale_result.reason == "stale_price"
    assert outlier_result.ok is False
    assert outlier_result.reason == "invalid_tick"
