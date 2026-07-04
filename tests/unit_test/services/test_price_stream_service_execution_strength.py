from unittest.mock import MagicMock

import pytest

from services.data_quality_service import DataQualityResult
from services.price_stream_service import PriceStreamService


def _tick(code="005930"):
    return {
        '유가증권단축종목코드': code,
        '주식현재가': '75000',
        '누적거래량': '1500000',
        '체결강도': '123.45',
        '주식체결시간': '091001',
        '영업일자': '20260704',
    }


@pytest.fixture
def recorder():
    return MagicMock()


@pytest.fixture
def service(recorder):
    return PriceStreamService(
        stock_repo=MagicMock(),
        logger=MagicMock(),
        execution_strength_recorder=recorder,
    )


def test_on_price_tick_records_execution_strength(service, recorder):
    service.on_price_tick(_tick())

    recorder.record_tick.assert_called_once_with(
        '005930', '123.45', '091001', '20260704'
    )


def test_recorder_error_does_not_break_tick_path(service, recorder):
    recorder.record_tick.side_effect = RuntimeError("boom")

    service.on_price_tick(_tick())

    # recorder 실패에도 틱 처리(캐시 갱신)는 지속되어야 한다
    assert service.get_cached_price('005930') is not None


def test_no_recorder_is_noop():
    service = PriceStreamService(stock_repo=MagicMock(), logger=MagicMock())

    service.on_price_tick(_tick())

    assert service.get_cached_price('005930') is not None


def test_quality_rejected_tick_not_recorded(recorder):
    dq = MagicMock()
    dq.validate_price_tick.return_value = DataQualityResult(
        ok=False, severity="error", reason="invalid_tick", code="005930"
    )
    service = PriceStreamService(
        stock_repo=MagicMock(),
        logger=MagicMock(),
        data_quality_service=dq,
        execution_strength_recorder=recorder,
    )

    service.on_price_tick(_tick())

    recorder.record_tick.assert_not_called()
