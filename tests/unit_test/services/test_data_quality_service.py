import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from common.operator_alert_types import AlertSource
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


def test_repeated_violations_report_operator_alert_once_per_cooldown(monkeypatch):
    class ScheduledTask:
        def add_done_callback(self, callback):
            return None

    class FakeLoop:
        def __init__(self):
            self.created = []

        def create_task(self, coroutine):
            self.created.append(coroutine)
            coroutine.close()
            return ScheduledTask()

    fake_loop = FakeLoop()
    monkeypatch.setattr(
        "services.data_quality_service.asyncio.get_running_loop",
        lambda: fake_loop,
    )
    operator_alert = MagicMock()
    operator_alert.report = AsyncMock()
    svc = DataQualityService(
        DataQualityConfig(
            violation_alert_threshold=2,
            violation_alert_window_sec=10.0,
            alert_cooldown_sec=60.0,
        ),
        operator_alert_service=operator_alert,
    )

    svc.validate_price_tick({"유가증권단축종목코드": "005930", "주식현재가": "-1", "누적거래량": "1"})
    svc.validate_price_tick({"유가증권단축종목코드": "000660", "주식현재가": "-1", "누적거래량": "1"})

    assert operator_alert.report.call_count == 1
    assert len(fake_loop.created) == 1
    args, kwargs = operator_alert.report.call_args
    assert args[0] == AlertSource.DATA_QUALITY
    assert args[1] == "data_quality:invalid_tick"
    assert args[2] == "error"
    assert kwargs["metadata"]["reason"] == "invalid_tick"
    assert kwargs["metadata"]["violation_count"] == 2

    svc.validate_price_tick({"유가증권단축종목코드": "035420", "주식현재가": "-1", "누적거래량": "1"})

    assert operator_alert.report.call_count == 1


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


def test_config_property_and_disabled_checks_return_info():
    cfg = DataQualityConfig(enabled=False)
    svc = DataQualityService(cfg)

    assert svc.config is cfg
    assert svc.validate_api_response(None, code="005930").reason == "disabled"
    assert svc.validate_price_tick({}).reason == "disabled"
    assert svc.validate_execution_report({}).reason == "disabled"


def test_validate_api_response_rejects_none_data_and_non_dict_required_keys():
    svc = DataQualityService()

    none_data = svc.validate_api_response(
        ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data=None),
        code="005930",
    )
    non_dict = svc.validate_api_response(
        ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data=["row"]),
        code="005930",
        required_data_keys=["output"],
    )

    assert none_data.reason == "rest_invalid"
    assert non_dict.reason == "rest_invalid"
    assert non_dict.metadata["required_data_keys"] == ["output"]


def test_to_float_invalid_object_and_violation_history_trim():
    class BadNumber:
        def __str__(self):
            raise TypeError("bad")

    svc = DataQualityService()
    svc.MAX_VIOLATION_HISTORY = 2

    assert DataQualityService._to_float(BadNumber()) is None
    for code in ("1", "2", "3"):
        svc.validate_api_response(ResCommonResponse(rt_cd="1", msg1="fail", data=None), code=code)

    history = svc.get_violation_history(count=10)
    assert [item["code"] for item in history] == ["3", "2"]


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
    assert stale_result.metadata["reference_price"] == 70000.0
    assert stale_result.metadata["order_price"] == 70000
    assert stale_result.metadata["age_sec"] >= 5.0
    assert stale_result.metadata["reference_source"] == "unknown"
    assert outlier_result.ok is False
    assert outlier_result.reason == "invalid_tick"


@pytest.mark.asyncio
async def test_validate_order_reference_disabled_invalid_cache_failure_and_ok():
    class FailingPriceStream:
        def get_cached_price(self, code):
            raise RuntimeError("cache down")

    class PriceStream:
        def get_cached_price(self, code):
            return {"price": "70000", "received_at": time.time()}

    disabled = DataQualityService(DataQualityConfig(enabled=False))
    invalid = DataQualityService()
    failing = DataQualityService(price_stream_service=FailingPriceStream(), logger=type("L", (), {"warning": lambda *a, **k: None})())
    ok = DataQualityService(price_stream_service=PriceStream())

    assert (await disabled.validate_order_reference(stock_code="005930", price=-1, qty=0)).reason == "disabled"
    assert (await invalid.validate_order_reference(stock_code="005930", price=1, qty=0)).reason == "invalid_tick"
    assert (await failing.validate_order_reference(stock_code="005930", price=70000, qty=1)).reason == "no_realtime_reference"
    ok_result = await ok.validate_order_reference(stock_code="005930", price=70000, qty=1)
    assert ok_result.ok is True
    assert ok_result.reason == "ok"
