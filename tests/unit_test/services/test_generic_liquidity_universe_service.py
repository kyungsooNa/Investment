"""Unit tests for GenericLiquidityUniverseService (P2-2 universe ablation MVP)."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from common.types import ResCommonResponse, ErrorCode


def _ranking_row(code: str, name: str = "", market_cap: int = 0) -> dict:
    return {
        "mksc_shrn_iscd": code,
        "hts_kor_isnm": name or f"종목{code}",
        "stck_avls": str(market_cap // 100_000_000) if market_cap else "0",  # KIS API returns 억 단위 문자열
    }


def _ohlcv_response(rows: list[dict]) -> ResCommonResponse:
    return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data=rows)


def _ohlcv_rows(closes_volumes: list[tuple[int, int]]) -> list[dict]:
    return [
        {
            "date": f"2026010{i+1}",
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "volume": volume,
        }
        for i, (close, volume) in enumerate(closes_volumes)
    ]


@pytest.fixture
def fake_time_manager():
    mc = MagicMock()
    dt = MagicMock()
    dt.strftime.return_value = "20260523"
    mc.get_current_kst_time.return_value = dt
    return mc


@pytest.fixture
def fake_sqs():
    sqs = MagicMock()
    sqs.get_top_trading_value_stocks = AsyncMock()
    sqs.get_recent_daily_ohlcv = AsyncMock()
    return sqs


@pytest.mark.asyncio
async def test_get_watchlist_filters_by_min_trading_value(fake_sqs, fake_time_manager):
    """5d 평균 거래대금 임계값(50억) 미달 코드는 제외된다."""
    from services.generic_liquidity_universe_service import GenericLiquidityUniverseService

    fake_sqs.get_top_trading_value_stocks.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK",
        data=[
            _ranking_row("000001", market_cap=200_000_000_000),  # 2000억
            _ranking_row("000002", market_cap=200_000_000_000),
        ],
    )

    async def ohlcv_router(code, limit=5):
        if code == "000001":
            # 평균 거래대금 100억 (close 10000 * volume 1_000_000)
            return _ohlcv_response(_ohlcv_rows([(10000, 1_000_000)] * 5))
        # 평균 거래대금 1억 (close 10000 * volume 10_000)
        return _ohlcv_response(_ohlcv_rows([(10000, 10_000)] * 5))

    fake_sqs.get_recent_daily_ohlcv.side_effect = ohlcv_router

    svc = GenericLiquidityUniverseService(
        sqs=fake_sqs,
        time_manager=fake_time_manager,
        min_avg_trading_value_5d=5_000_000_000,
        min_market_cap=100_000_000_000,
    )

    watchlist = await svc.get_watchlist()

    assert set(watchlist.keys()) == {"000001"}


@pytest.mark.asyncio
async def test_get_watchlist_filters_by_min_market_cap(fake_sqs, fake_time_manager):
    """시가총액 임계값(1000억) 미달 코드는 제외된다."""
    from services.generic_liquidity_universe_service import GenericLiquidityUniverseService

    fake_sqs.get_top_trading_value_stocks.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK",
        data=[
            _ranking_row("000001", market_cap=200_000_000_000),  # 2000억
            _ranking_row("000002", market_cap=50_000_000_000),   # 500억 (탈락)
        ],
    )
    # 둘 다 거래대금은 충분
    fake_sqs.get_recent_daily_ohlcv.return_value = _ohlcv_response(
        _ohlcv_rows([(10000, 1_000_000)] * 5)
    )

    svc = GenericLiquidityUniverseService(
        sqs=fake_sqs,
        time_manager=fake_time_manager,
        min_avg_trading_value_5d=5_000_000_000,
        min_market_cap=100_000_000_000,
    )

    watchlist = await svc.get_watchlist()

    assert set(watchlist.keys()) == {"000001"}


@pytest.mark.asyncio
async def test_get_watchlist_returns_empty_when_top_ranking_fails(fake_sqs, fake_time_manager):
    """랭킹 API 실패(rt_cd != SUCCESS) 시 빈 dict 반환."""
    from services.generic_liquidity_universe_service import GenericLiquidityUniverseService

    fake_sqs.get_top_trading_value_stocks.return_value = ResCommonResponse(
        rt_cd="1", msg1="error", data=None,
    )

    svc = GenericLiquidityUniverseService(
        sqs=fake_sqs,
        time_manager=fake_time_manager,
    )

    watchlist = await svc.get_watchlist()

    assert watchlist == {}
    fake_sqs.get_recent_daily_ohlcv.assert_not_called()


@pytest.mark.asyncio
async def test_get_watchlist_skips_invalid_ohlcv_response(fake_sqs, fake_time_manager):
    """일부 종목 OHLCV 실패 시 해당 종목만 skip, 나머지는 처리."""
    from services.generic_liquidity_universe_service import GenericLiquidityUniverseService

    fake_sqs.get_top_trading_value_stocks.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK",
        data=[
            _ranking_row("000001", market_cap=200_000_000_000),
            _ranking_row("000002", market_cap=200_000_000_000),
        ],
    )

    async def ohlcv_router(code, limit=5):
        if code == "000002":
            return ResCommonResponse(rt_cd="1", msg1="error", data=None)
        return _ohlcv_response(_ohlcv_rows([(10000, 1_000_000)] * 5))

    fake_sqs.get_recent_daily_ohlcv.side_effect = ohlcv_router

    svc = GenericLiquidityUniverseService(
        sqs=fake_sqs,
        time_manager=fake_time_manager,
    )

    watchlist = await svc.get_watchlist()

    assert set(watchlist.keys()) == {"000001"}


@pytest.mark.asyncio
async def test_get_watchlist_caches_per_day(fake_sqs, fake_time_manager):
    """같은 거래일 안에서는 두 번째 호출 시 sqs를 다시 부르지 않는다."""
    from services.generic_liquidity_universe_service import GenericLiquidityUniverseService

    fake_sqs.get_top_trading_value_stocks.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK",
        data=[_ranking_row("000001", market_cap=200_000_000_000)],
    )
    fake_sqs.get_recent_daily_ohlcv.return_value = _ohlcv_response(
        _ohlcv_rows([(10000, 1_000_000)] * 5)
    )

    svc = GenericLiquidityUniverseService(
        sqs=fake_sqs,
        time_manager=fake_time_manager,
    )

    first = await svc.get_watchlist()
    second = await svc.get_watchlist()

    assert first.keys() == second.keys()
    fake_sqs.get_top_trading_value_stocks.assert_awaited_once()
    fake_sqs.get_recent_daily_ohlcv.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_watchlist_items_have_generic_source(fake_sqs, fake_time_manager):
    """모든 아이템의 source 필드는 'generic_liquidity' 이다."""
    from services.generic_liquidity_universe_service import GenericLiquidityUniverseService

    fake_sqs.get_top_trading_value_stocks.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK",
        data=[_ranking_row("000001", market_cap=200_000_000_000)],
    )
    fake_sqs.get_recent_daily_ohlcv.return_value = _ohlcv_response(
        _ohlcv_rows([(10000, 1_000_000)] * 5)
    )

    svc = GenericLiquidityUniverseService(
        sqs=fake_sqs,
        time_manager=fake_time_manager,
    )

    watchlist = await svc.get_watchlist()

    assert all(item.source == "generic_liquidity" for item in watchlist.values())


@pytest.mark.asyncio
async def test_is_market_timing_ok_delegates_to_regime_service(fake_sqs, fake_time_manager):
    """market_regime_service 가 있으면 classify().is_rising 을 그대로 반환."""
    from services.generic_liquidity_universe_service import GenericLiquidityUniverseService

    regime = MagicMock()
    regime.classify = AsyncMock(return_value=SimpleNamespace(is_rising=False))

    svc = GenericLiquidityUniverseService(
        sqs=fake_sqs,
        time_manager=fake_time_manager,
        market_regime_service=regime,
    )

    assert await svc.is_market_timing_ok("KOSPI") is False
    regime.classify.assert_awaited_once()


@pytest.mark.asyncio
async def test_is_market_timing_ok_returns_true_without_regime_service(fake_sqs, fake_time_manager):
    """market_regime_service 가 None 이면 항상 True 반환 (테스트/ablation 용도)."""
    from services.generic_liquidity_universe_service import GenericLiquidityUniverseService

    svc = GenericLiquidityUniverseService(
        sqs=fake_sqs,
        time_manager=fake_time_manager,
        market_regime_service=None,
    )

    assert await svc.is_market_timing_ok("KOSPI") is True
    assert await svc.is_market_timing_ok("KOSDAQ") is True


@pytest.mark.asyncio
async def test_get_watchlist_limits_to_max_watchlist(fake_sqs, fake_time_manager):
    """후보가 max_watchlist 보다 많으면 거래대금 내림차순 상위만 유지."""
    from services.generic_liquidity_universe_service import GenericLiquidityUniverseService

    fake_sqs.get_top_trading_value_stocks.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK",
        data=[
            _ranking_row("00000A", market_cap=200_000_000_000),
            _ranking_row("00000B", market_cap=200_000_000_000),
            _ranking_row("00000C", market_cap=200_000_000_000),
        ],
    )

    async def ohlcv_router(code, limit=5):
        if code == "00000A":
            tv = 30_000_000_000  # 300억
        elif code == "00000B":
            tv = 20_000_000_000  # 200억
        else:
            tv = 10_000_000_000  # 100억
        return _ohlcv_response(_ohlcv_rows([(10000, tv // 10000)] * 5))

    fake_sqs.get_recent_daily_ohlcv.side_effect = ohlcv_router

    svc = GenericLiquidityUniverseService(
        sqs=fake_sqs,
        time_manager=fake_time_manager,
        max_watchlist=2,
    )

    watchlist = await svc.get_watchlist()

    assert set(watchlist.keys()) == {"00000A", "00000B"}
