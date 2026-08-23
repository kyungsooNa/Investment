"""해외 dry-run 서비스 5종이 공유하는 스캔 골격/헬퍼 경로 테스트.

`scan_dry_run` 의 후보 스킵·조회 실패 처리와 `_resolve_fx_rate`, `_f`, `_bar_ohlc`
는 전략별 판정 로직과 무관하게 동일한 코드 형태를 가진다. 전략별 테스트 파일은
신호 판정에 집중하고, 여기서는 공통 골격만 한 번에 검증한다.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from common.overseas_types import OverseasExchange
from common.types import ErrorCode, ResCommonResponse
from services.overseas_buyable_gap_up_dryrun_service import OverseasBuyableGapUpDryRunService
from services.overseas_channel_breakout_dryrun_service import (
    OverseasChannelBreakoutDryRunService,
)
from services.overseas_pocket_pivot_dryrun_service import OverseasPocketPivotDryRunService
from services.overseas_rsi2_dryrun_service import OverseasRSI2DryRunService
from services.overseas_squeeze_breakout_dryrun_service import (
    OverseasSqueezeBreakoutDryRunService,
)

SERVICE_CLASSES = [
    OverseasRSI2DryRunService,
    OverseasChannelBreakoutDryRunService,
    OverseasBuyableGapUpDryRunService,
    OverseasPocketPivotDryRunService,
    OverseasSqueezeBreakoutDryRunService,
]


def _build(service_cls, *, candidates, ohlcv=None, **kwargs):
    """서비스별 생성자 차이(ChannelBreakout 만 indicator_service 필요)를 흡수한다."""
    candidate_service = MagicMock()
    candidate_service.get_candidates = AsyncMock(return_value=candidates)
    sqs = MagicMock()
    sqs.get_recent_daily_ohlcv = ohlcv if ohlcv is not None else AsyncMock(
        return_value=ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="ok", data=[])
    )
    args = [candidate_service, sqs]
    if service_cls is OverseasChannelBreakoutDryRunService:
        indicator = MagicMock()
        indicator.calculate_adx = MagicMock(return_value={"adx": 30.0})
        args.append(indicator)
    journal = MagicMock()
    service = service_cls(*args, journal, MagicMock(), **kwargs)
    return service, sqs, journal


@pytest.mark.asyncio
@pytest.mark.parametrize("service_cls", SERVICE_CLASSES)
async def test_candidate_without_code_is_skipped_before_ohlcv_lookup(service_cls):
    service, sqs, journal = _build(service_cls, candidates=[{"name": "코드없음"}, {"code": ""}])

    signals = await service.scan_dry_run(exchange=OverseasExchange.NASD)

    assert signals == []
    sqs.get_recent_daily_ohlcv.assert_not_awaited()
    journal.record.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("service_cls", SERVICE_CLASSES)
async def test_ohlcv_lookup_error_is_logged_and_skipped(service_cls):
    ohlcv = AsyncMock(side_effect=RuntimeError("ohlcv down"))
    service, _, journal = _build(service_cls, candidates=[{"code": "MSFT"}], ohlcv=ohlcv)

    signals = await service.scan_dry_run(exchange=OverseasExchange.NASD)

    assert signals == []
    journal.record.assert_not_called()
    service._logger.warning.assert_called_once()
    assert "ohlcv down" in str(service._logger.warning.call_args)


@pytest.mark.asyncio
@pytest.mark.parametrize("service_cls", SERVICE_CLASSES)
@pytest.mark.parametrize(
    "response",
    [
        None,
        ResCommonResponse(rt_cd=ErrorCode.API_ERROR.value, msg1="fail", data=[{"close": 1}]),
        ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="ok", data=None),
        ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="ok", data=[]),
    ],
)
async def test_unusable_ohlcv_response_is_skipped(service_cls, response):
    ohlcv = AsyncMock(return_value=response)
    service, _, journal = _build(service_cls, candidates=[{"code": "MSFT"}], ohlcv=ohlcv)

    signals = await service.scan_dry_run(exchange=OverseasExchange.NASD)

    assert signals == []
    journal.record.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("service_cls", SERVICE_CLASSES)
async def test_empty_candidate_list_scans_nothing(service_cls):
    service, sqs, _ = _build(service_cls, candidates=None)

    assert await service.scan_dry_run() == []
    sqs.get_recent_daily_ohlcv.assert_not_awaited()
    service._logger.info.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("service_cls", SERVICE_CLASSES)
async def test_fx_rate_is_none_without_sizing_or_fx_provider(service_cls):
    service, _, _ = _build(service_cls, candidates=[])
    assert await service._resolve_fx_rate() is None

    service_with_sizing, _, _ = _build(
        service_cls, candidates=[], position_sizing_service=MagicMock()
    )
    assert await service_with_sizing._resolve_fx_rate() is None

    service_with_fx, _, _ = _build(service_cls, candidates=[], fx_provider=AsyncMock())
    assert await service_with_fx._resolve_fx_rate() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("service_cls", SERVICE_CLASSES)
async def test_fx_provider_error_is_logged_and_yields_none(service_cls):
    service, _, _ = _build(
        service_cls,
        candidates=[],
        position_sizing_service=MagicMock(),
        fx_provider=AsyncMock(side_effect=RuntimeError("fx down")),
    )

    assert await service._resolve_fx_rate() is None
    service._logger.warning.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("service_cls", SERVICE_CLASSES)
@pytest.mark.parametrize(
    "raw, expected",
    [(1380.5, 1380.5), ("1400", 1400.0), (None, None), ("환율없음", None), (0, None), (-3, None)],
)
async def test_fx_rate_accepts_only_positive_numbers(service_cls, raw, expected):
    service, _, _ = _build(
        service_cls,
        candidates=[],
        position_sizing_service=MagicMock(),
        fx_provider=AsyncMock(return_value=raw),
    )

    assert await service._resolve_fx_rate() == expected


@pytest.mark.parametrize("service_cls", SERVICE_CLASSES)
@pytest.mark.parametrize(
    "raw, expected",
    [("1,0", 0.0), (None, 0.0), ("", 0.0), ("12.5", 12.5), (7, 7.0), (object(), 0.0)],
)
def test_float_coercion_defaults_to_zero(service_cls, raw, expected):
    assert service_cls._f(raw) == expected


@pytest.mark.parametrize("service_cls", SERVICE_CLASSES)
def test_bar_ohlc_keeps_only_ohlcv_keys(service_cls):
    bar = {
        "date": "20260521",
        "open": 1,
        "high": 2,
        "low": 3,
        "close": 4,
        "volume": 5,
        "extra": "무시",
    }

    assert service_cls._bar_ohlc(bar) == {
        "date": "20260521",
        "open": 1,
        "high": 2,
        "low": 3,
        "close": 4,
        "volume": 5,
    }
