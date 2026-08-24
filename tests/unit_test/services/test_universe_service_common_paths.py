"""유동성 기반 universe 서비스 3종이 공유하는 필터/캐시 경로 테스트.

`GenericLiquidityUniverseService`, `Rsi2MeanReversionUniverseService`,
`VboVolatilityUniverseService` 는 상위 거래대금 종목을 받아 시총·5일 평균 거래대금으로
거르고 당일 캐시에 담는 골격이 동일하다(변동성 게이트만 뒤에 더 붙는다). 전략별
테스트 파일은 고유 게이트에 집중하고, 여기서는 공통 골격만 한 번에 검증한다.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from common.types import ErrorCode, ResCommonResponse
from services.generic_liquidity_universe_service import GenericLiquidityUniverseService
from services.rsi2_mean_reversion_universe_service import Rsi2MeanReversionUniverseService
from services.vbo_volatility_universe_service import VboVolatilityUniverseService

SERVICE_CLASSES = [
    GenericLiquidityUniverseService,
    Rsi2MeanReversionUniverseService,
    VboVolatilityUniverseService,
]


def _ok(data):
    return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="ok", data=data)


def _rank_row(code="005930", cap_billion=2_000_000, name="삼성전자"):
    """상위 거래대금 랭킹 한 행. 시총은 억 단위(stck_avls)."""
    return {"mksc_shrn_iscd": code, "hts_kor_isnm": name,
            "stck_avls": str(cap_billion), "market": "KOSPI"}


def _ohlcv_rows(close=70_000, volume=1_000_000, days=25):
    return [{"close": str(close), "volume": str(volume)} for _ in range(days)]


def _build(service_cls, *, rank_rows, ohlcv=None, **kwargs):
    sqs = MagicMock()
    sqs.get_top_trading_value_stocks = AsyncMock(return_value=_ok(rank_rows))
    sqs.get_recent_daily_ohlcv = ohlcv if ohlcv is not None else AsyncMock(
        return_value=_ok(_ohlcv_rows())
    )
    clock = MagicMock()
    clock.get_current_kst_time.return_value.strftime.return_value = "20260821"
    return service_cls(sqs=sqs, time_manager=clock, logger=MagicMock(), **kwargs), sqs, clock


@pytest.mark.asyncio
@pytest.mark.parametrize("service_cls", SERVICE_CLASSES)
async def test_failed_ranking_response_empties_the_watchlist(service_cls):
    service, sqs, _ = _build(service_cls, rank_rows=[])
    sqs.get_top_trading_value_stocks = AsyncMock(
        return_value=ResCommonResponse(rt_cd=ErrorCode.API_ERROR.value, msg1="fail", data=None)
    )

    assert await service.get_watchlist() == {}
    sqs.get_recent_daily_ohlcv.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("service_cls", SERVICE_CLASSES)
async def test_rows_without_code_are_skipped(service_cls):
    service, sqs, _ = _build(service_cls, rank_rows=[{"hts_kor_isnm": "코드없음"}])

    assert await service.get_watchlist() == {}
    sqs.get_recent_daily_ohlcv.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("service_cls", SERVICE_CLASSES)
async def test_rows_below_market_cap_floor_are_skipped(service_cls):
    service, sqs, _ = _build(
        service_cls,
        rank_rows=[_rank_row(cap_billion=1)],  # 1억 → 최소 시총 미만
        min_market_cap=100_000_000_000,
    )

    assert await service.get_watchlist() == {}
    sqs.get_recent_daily_ohlcv.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("service_cls", SERVICE_CLASSES)
@pytest.mark.parametrize(
    "ohlcv_response",
    [
        None,
        ResCommonResponse(rt_cd=ErrorCode.API_ERROR.value, msg1="fail", data=None),
        ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="ok", data=[]),
        ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="ok", data=None),
    ],
)
async def test_unusable_ohlcv_response_skips_the_code(service_cls, ohlcv_response):
    service, _, _ = _build(
        service_cls,
        rank_rows=[_rank_row()],
        ohlcv=AsyncMock(return_value=ohlcv_response),
    )

    assert await service.get_watchlist() == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("service_cls", SERVICE_CLASSES)
async def test_rows_without_positive_trading_value_are_skipped(service_cls):
    service, _, _ = _build(
        service_cls,
        rank_rows=[_rank_row()],
        ohlcv=AsyncMock(return_value=_ok([{"close": "0", "volume": "0"},
                                          {"close": "", "volume": "-"}])),
    )

    assert await service.get_watchlist() == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("service_cls", SERVICE_CLASSES)
async def test_rows_below_average_trading_value_floor_are_skipped(service_cls):
    service, _, _ = _build(
        service_cls,
        rank_rows=[_rank_row()],
        ohlcv=AsyncMock(return_value=_ok(_ohlcv_rows(close=10, volume=10))),
        min_avg_trading_value_5d=5_000_000_000,
    )

    assert await service.get_watchlist() == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("service_cls", SERVICE_CLASSES)
async def test_same_day_second_call_reuses_the_cached_watchlist(service_cls):
    service, sqs, _ = _build(service_cls, rank_rows=[_rank_row()])
    service._watchlist = {"005930": object()}
    service._watchlist_date = "20260821"

    assert await service.get_watchlist() == service._watchlist
    sqs.get_top_trading_value_stocks.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("service_cls", SERVICE_CLASSES)
async def test_new_day_clears_yesterdays_exclusions(service_cls):
    service, _, _ = _build(service_cls, rank_rows=[])
    service._excluded_codes = {"005930"}
    service._watchlist_date = "20260820"

    await service.get_watchlist()

    assert service._excluded_codes == set()


@pytest.mark.asyncio
@pytest.mark.parametrize("service_cls", SERVICE_CLASSES)
async def test_excluded_codes_are_skipped_on_the_same_day(service_cls):
    service, sqs, _ = _build(service_cls, rank_rows=[_rank_row()])
    service._watchlist_date = "20260821"
    service._excluded_codes = {"005930"}

    assert await service.get_watchlist() == {}
    sqs.get_recent_daily_ohlcv.assert_not_awaited()


@pytest.mark.parametrize("service_cls", SERVICE_CLASSES)
def test_exclude_code_ignores_blank_codes_and_drops_cached_entries(service_cls):
    service, _, _ = _build(service_cls, rank_rows=[])
    service._watchlist = {"005930": object()}

    service.exclude_code_for_today("")
    assert service._excluded_codes == set()
    assert "005930" in service._watchlist

    service.exclude_code_for_today("005930", reason="손절")
    assert service._excluded_codes == {"005930"}
    assert "005930" not in service._watchlist


@pytest.mark.asyncio
@pytest.mark.parametrize("service_cls", SERVICE_CLASSES)
async def test_market_timing_passes_without_regime_service(service_cls):
    service, _, _ = _build(service_cls, rank_rows=[])

    assert await service.is_market_timing_ok("KOSPI") is True


@pytest.mark.asyncio
@pytest.mark.parametrize("service_cls", SERVICE_CLASSES)
@pytest.mark.parametrize("is_rising, expected", [(True, True), (False, False)])
async def test_market_timing_follows_regime_snapshot(service_cls, is_rising, expected):
    regime = MagicMock()
    regime.classify = AsyncMock(return_value=MagicMock(is_rising=is_rising))
    service, _, _ = _build(service_cls, rank_rows=[], market_regime_service=regime)

    assert await service.is_market_timing_ok("KOSPI") is expected


@pytest.mark.parametrize("service_cls", SERVICE_CLASSES)
@pytest.mark.parametrize(
    "raw, expected",
    [(None, 0), ("", 0), ("-", 0), ("1050", 1050), ("숫자아님", 0), (7.9, 7), (object(), 0)],
)
def test_safe_int_coercion(service_cls, raw, expected):
    import importlib

    module = importlib.import_module(service_cls.__module__)

    assert module._safe_int(raw) == expected


# --- 변동성 게이트 (rsi2 / vbo 전용) ----------------------------------------

VOLATILITY_GATED_CLASSES = [Rsi2MeanReversionUniverseService, VboVolatilityUniverseService]


def _flat_ohlcv(days=25):
    """등락이 없는 종가 — 연환산 변동성 0."""
    return [{"close": "70000", "volume": "1000000"} for _ in range(days)]


def _choppy_ohlcv(days=25):
    """하루 걸러 ±10% 로 흔들리는 종가 — 변동성 게이트를 넉넉히 통과한다."""
    rows = []
    for i in range(days):
        close = 70_000 if i % 2 == 0 else 77_000
        rows.append({"close": str(close), "volume": "1000000"})
    return rows


@pytest.mark.asyncio
@pytest.mark.parametrize("service_cls", VOLATILITY_GATED_CLASSES)
async def test_low_volatility_codes_are_skipped(service_cls):
    service, _, _ = _build(
        service_cls,
        rank_rows=[_rank_row()],
        ohlcv=AsyncMock(return_value=_ok(_flat_ohlcv())),
    )

    assert await service.get_watchlist() == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("service_cls", VOLATILITY_GATED_CLASSES)
async def test_short_history_yields_no_volatility_and_is_skipped(service_cls):
    service, _, _ = _build(
        service_cls,
        rank_rows=[_rank_row()],
        ohlcv=AsyncMock(return_value=_ok(_choppy_ohlcv(days=5))),
    )

    assert await service.get_watchlist() == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("service_cls", VOLATILITY_GATED_CLASSES)
async def test_volatile_code_enters_the_watchlist(service_cls):
    service, _, _ = _build(
        service_cls,
        rank_rows=[_rank_row()],
        ohlcv=AsyncMock(return_value=_ok(_choppy_ohlcv())),
    )

    watchlist = await service.get_watchlist()

    assert list(watchlist) == ["005930"]
    assert watchlist["005930"].name == "삼성전자"


@pytest.mark.asyncio
async def test_generic_liquidity_service_has_no_volatility_gate():
    """유동성 전용 서비스는 변동성이 0이어도 후보로 남긴다."""
    service, _, _ = _build(
        GenericLiquidityUniverseService,
        rank_rows=[_rank_row()],
        ohlcv=AsyncMock(return_value=_ok(_flat_ohlcv())),
    )

    assert list(await service.get_watchlist()) == ["005930"]
