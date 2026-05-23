"""Unit tests for VboVolatilityUniverseService (P2-2 Phase 1 잔여).

Strategy-specific prefilter on top of GenericLiquidity-style baseline for
LarryWilliams VBO (Volatility Breakout). VBO entry signal frequency and
edge depend on stock-level volatility; we therefore add a 20-day annualized
volatility floor (default 0.35).

Volatility is computed via utils.volatility_utils.annualized_return_std which
needs lookback+1 = 21 valid closes; we fetch ohlcv with limit=21.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from common.types import ResCommonResponse, ErrorCode


def _ranking_row(code: str, name: str = "", market_cap: int = 0) -> dict:
    return {
        "mksc_shrn_iscd": code,
        "hts_kor_isnm": name or f"종목{code}",
        "stck_avls": str(market_cap // 100_000_000) if market_cap else "0",
    }


def _ohlcv_response(rows: list[dict]) -> ResCommonResponse:
    return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data=rows)


def _ohlcv_rows(closes_volumes: list[tuple[int, int]]) -> list[dict]:
    return [
        {
            "date": f"202601{i+1:02d}",
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "volume": volume,
        }
        for i, (close, volume) in enumerate(closes_volumes)
    ]


def _flat_closes(price: int = 10_000, volume: int = 1_000_000) -> list[dict]:
    """21 identical closes → annualized volatility = 0.0 → fails threshold."""
    return _ohlcv_rows([(price, volume)] * 21)


def _alternating_closes(
    low: int = 9_000, high: int = 10_000, volume: int = 1_000_000
) -> list[dict]:
    """Annualized volatility ≈ 1.67 → comfortably above 0.35 default."""
    seq: list[tuple[int, int]] = []
    for i in range(21):
        seq.append((low if i % 2 == 0 else high, volume))
    return _ohlcv_rows(seq)


def _mild_volatility_closes(volume: int = 1_000_000) -> list[dict]:
    """변동성 약 0.32 (0.30 통과, 0.35 미달) 구간 생성.

    1% 일일 변동 (log return ≈ 0.01) → annualized ≈ 0.01 * sqrt(252) ≈ 0.159.
    1.5% → ≈ 0.238, 2% → ≈ 0.317, 2.1% → ≈ 0.333, 2.2% → ≈ 0.349.
    실제로는 alternating 2.1% 이 0.30 < vol < 0.35 영역에 위치한다.
    """
    base = 10_000
    delta = 210  # 2.1% of base
    seq: list[tuple[int, int]] = []
    for i in range(21):
        price = base + (delta if i % 2 == 0 else 0)
        seq.append((price, volume))
    return _ohlcv_rows(seq)


@pytest.fixture
def fake_time_manager():
    mc = MagicMock()
    dt = MagicMock()
    dt.strftime.return_value = "20260524"
    mc.get_current_kst_time.return_value = dt
    return mc


@pytest.fixture
def fake_sqs():
    sqs = MagicMock()
    sqs.get_top_trading_value_stocks = AsyncMock()
    sqs.get_recent_daily_ohlcv = AsyncMock()
    return sqs


@pytest.mark.asyncio
async def test_filters_low_volatility_below_floor(fake_sqs, fake_time_manager):
    """20일 변동성이 임계값(0.35) 미달인 코드는 제외된다."""
    from services.vbo_volatility_universe_service import (
        VboVolatilityUniverseService,
    )

    fake_sqs.get_top_trading_value_stocks.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK",
        data=[
            _ranking_row("000001", market_cap=200_000_000_000),
            _ranking_row("000002", market_cap=200_000_000_000),
        ],
    )

    async def ohlcv_router(code, limit=21):
        if code == "000001":
            return _ohlcv_response(_alternating_closes())
        return _ohlcv_response(_flat_closes())

    fake_sqs.get_recent_daily_ohlcv.side_effect = ohlcv_router

    svc = VboVolatilityUniverseService(
        sqs=fake_sqs,
        time_manager=fake_time_manager,
    )

    watchlist = await svc.get_watchlist()

    assert set(watchlist.keys()) == {"000001"}


@pytest.mark.asyncio
async def test_vbo_floor_stricter_than_rsi2(fake_sqs, fake_time_manager):
    """VBO floor(0.35) 가 RSI2 floor(0.30) 보다 엄격함을 회귀로 잠근다."""
    from services.vbo_volatility_universe_service import (
        VboVolatilityUniverseService,
    )

    fake_sqs.get_top_trading_value_stocks.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK",
        data=[_ranking_row("000001", market_cap=200_000_000_000)],
    )
    # mild volatility ≈ 0.33: RSI2(0.30) 통과, VBO(0.35) 차단
    fake_sqs.get_recent_daily_ohlcv.return_value = _ohlcv_response(
        _mild_volatility_closes()
    )

    svc = VboVolatilityUniverseService(
        sqs=fake_sqs,
        time_manager=fake_time_manager,
    )

    watchlist = await svc.get_watchlist()

    assert watchlist == {}


@pytest.mark.asyncio
async def test_filters_by_baseline_trading_value(fake_sqs, fake_time_manager):
    """기본 baseline(5d 평균 거래대금) 미달 코드는 제외된다."""
    from services.vbo_volatility_universe_service import (
        VboVolatilityUniverseService,
    )

    fake_sqs.get_top_trading_value_stocks.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK",
        data=[
            _ranking_row("000001", market_cap=200_000_000_000),
            _ranking_row("000002", market_cap=200_000_000_000),
        ],
    )

    async def ohlcv_router(code, limit=21):
        if code == "000001":
            return _ohlcv_response(_alternating_closes(volume=1_000_000))
        return _ohlcv_response(_alternating_closes(volume=10_000))

    fake_sqs.get_recent_daily_ohlcv.side_effect = ohlcv_router

    svc = VboVolatilityUniverseService(
        sqs=fake_sqs,
        time_manager=fake_time_manager,
        min_avg_trading_value_5d=5_000_000_000,
    )

    watchlist = await svc.get_watchlist()

    assert set(watchlist.keys()) == {"000001"}


@pytest.mark.asyncio
async def test_filters_by_baseline_market_cap(fake_sqs, fake_time_manager):
    """기본 baseline(시가총액) 미달 코드는 제외된다."""
    from services.vbo_volatility_universe_service import (
        VboVolatilityUniverseService,
    )

    fake_sqs.get_top_trading_value_stocks.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK",
        data=[
            _ranking_row("000001", market_cap=200_000_000_000),
            _ranking_row("000002", market_cap=50_000_000_000),
        ],
    )
    fake_sqs.get_recent_daily_ohlcv.return_value = _ohlcv_response(
        _alternating_closes()
    )

    svc = VboVolatilityUniverseService(
        sqs=fake_sqs,
        time_manager=fake_time_manager,
        min_market_cap=100_000_000_000,
    )

    watchlist = await svc.get_watchlist()

    assert set(watchlist.keys()) == {"000001"}


@pytest.mark.asyncio
async def test_returns_empty_when_top_ranking_fails(fake_sqs, fake_time_manager):
    from services.vbo_volatility_universe_service import (
        VboVolatilityUniverseService,
    )

    fake_sqs.get_top_trading_value_stocks.return_value = ResCommonResponse(
        rt_cd="1", msg1="error", data=None,
    )

    svc = VboVolatilityUniverseService(
        sqs=fake_sqs,
        time_manager=fake_time_manager,
    )

    watchlist = await svc.get_watchlist()

    assert watchlist == {}
    fake_sqs.get_recent_daily_ohlcv.assert_not_called()


@pytest.mark.asyncio
async def test_skips_when_ohlcv_response_fails(fake_sqs, fake_time_manager):
    from services.vbo_volatility_universe_service import (
        VboVolatilityUniverseService,
    )

    fake_sqs.get_top_trading_value_stocks.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK",
        data=[
            _ranking_row("000001", market_cap=200_000_000_000),
            _ranking_row("000002", market_cap=200_000_000_000),
        ],
    )

    async def ohlcv_router(code, limit=21):
        if code == "000002":
            return ResCommonResponse(rt_cd="1", msg1="error", data=None)
        return _ohlcv_response(_alternating_closes())

    fake_sqs.get_recent_daily_ohlcv.side_effect = ohlcv_router

    svc = VboVolatilityUniverseService(
        sqs=fake_sqs,
        time_manager=fake_time_manager,
    )

    watchlist = await svc.get_watchlist()

    assert set(watchlist.keys()) == {"000001"}


@pytest.mark.asyncio
async def test_skips_insufficient_ohlcv_rows(fake_sqs, fake_time_manager):
    """21개 미만 일봉으로는 변동성 계산 불가 → 제외."""
    from services.vbo_volatility_universe_service import (
        VboVolatilityUniverseService,
    )

    fake_sqs.get_top_trading_value_stocks.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK",
        data=[
            _ranking_row("000001", market_cap=200_000_000_000),
            _ranking_row("000002", market_cap=200_000_000_000),
        ],
    )

    async def ohlcv_router(code, limit=21):
        if code == "000001":
            return _ohlcv_response(_alternating_closes())
        return _ohlcv_response(_alternating_closes()[:10])

    fake_sqs.get_recent_daily_ohlcv.side_effect = ohlcv_router

    svc = VboVolatilityUniverseService(
        sqs=fake_sqs,
        time_manager=fake_time_manager,
    )

    watchlist = await svc.get_watchlist()

    assert set(watchlist.keys()) == {"000001"}


@pytest.mark.asyncio
async def test_items_have_vbo_volatility_source(fake_sqs, fake_time_manager):
    """모든 아이템의 source 필드는 'vbo_volatility'."""
    from services.vbo_volatility_universe_service import (
        VboVolatilityUniverseService,
    )

    fake_sqs.get_top_trading_value_stocks.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK",
        data=[_ranking_row("000001", market_cap=200_000_000_000)],
    )
    fake_sqs.get_recent_daily_ohlcv.return_value = _ohlcv_response(
        _alternating_closes()
    )

    svc = VboVolatilityUniverseService(
        sqs=fake_sqs,
        time_manager=fake_time_manager,
    )

    watchlist = await svc.get_watchlist()

    assert all(item.source == "vbo_volatility" for item in watchlist.values())


@pytest.mark.asyncio
async def test_caches_per_day(fake_sqs, fake_time_manager):
    from services.vbo_volatility_universe_service import (
        VboVolatilityUniverseService,
    )

    fake_sqs.get_top_trading_value_stocks.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK",
        data=[_ranking_row("000001", market_cap=200_000_000_000)],
    )
    fake_sqs.get_recent_daily_ohlcv.return_value = _ohlcv_response(
        _alternating_closes()
    )

    svc = VboVolatilityUniverseService(
        sqs=fake_sqs,
        time_manager=fake_time_manager,
    )

    first = await svc.get_watchlist()
    second = await svc.get_watchlist()

    assert first.keys() == second.keys()
    fake_sqs.get_top_trading_value_stocks.assert_awaited_once()
    fake_sqs.get_recent_daily_ohlcv.assert_awaited_once()


@pytest.mark.asyncio
async def test_default_min_volatility_is_0_35(fake_sqs, fake_time_manager):
    """기본 변동성 임계값은 0.35 (VBO 단기 변동성 정책 기본값)."""
    from services.vbo_volatility_universe_service import (
        VboVolatilityUniverseService,
    )

    svc = VboVolatilityUniverseService(
        sqs=fake_sqs,
        time_manager=fake_time_manager,
    )

    assert svc.min_volatility_20d_annualized == pytest.approx(0.35)


@pytest.mark.asyncio
async def test_is_market_timing_ok_delegates_to_regime_service(fake_sqs, fake_time_manager):
    from services.vbo_volatility_universe_service import (
        VboVolatilityUniverseService,
    )

    regime = MagicMock()
    regime.classify = AsyncMock(return_value=SimpleNamespace(is_rising=False))

    svc = VboVolatilityUniverseService(
        sqs=fake_sqs,
        time_manager=fake_time_manager,
        market_regime_service=regime,
    )

    assert await svc.is_market_timing_ok("KOSPI") is False
    regime.classify.assert_awaited_once()


@pytest.mark.asyncio
async def test_is_market_timing_ok_returns_true_without_regime_service(
    fake_sqs, fake_time_manager
):
    from services.vbo_volatility_universe_service import (
        VboVolatilityUniverseService,
    )

    svc = VboVolatilityUniverseService(
        sqs=fake_sqs,
        time_manager=fake_time_manager,
        market_regime_service=None,
    )

    assert await svc.is_market_timing_ok("KOSPI") is True
    assert await svc.is_market_timing_ok("KOSDAQ") is True
