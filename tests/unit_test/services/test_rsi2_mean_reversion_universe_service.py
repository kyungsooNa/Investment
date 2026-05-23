"""Unit tests for Rsi2MeanReversionUniverseService (P2-2 Phase 1 잔여).

Strategy-specific prefilter on top of GenericLiquidity-style baseline:
  - 5d avg trading value ≥ min_avg_trading_value_5d
  - market cap ≥ min_market_cap
  - 20-day annualized close-to-close volatility ≥ min_volatility_20d_annualized (default 0.30)

Volatility is computed via utils.volatility_utils.annualized_return_std which
needs lookback+1 = 21 valid closes; we therefore fetch ohlcv with limit=21.
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
    """21 closes that alternate between two prices.

    log return magnitude per step ≈ |ln(high/low)| ≈ 0.105
    → annualized stdev ≈ 0.105 * sqrt(252) ≈ 1.67 (well above 0.30 threshold).
    """
    seq: list[tuple[int, int]] = []
    for i in range(21):
        seq.append((low if i % 2 == 0 else high, volume))
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
    """20일 변동성이 임계값(0.30) 미달인 코드는 제외된다."""
    from services.rsi2_mean_reversion_universe_service import (
        Rsi2MeanReversionUniverseService,
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

    svc = Rsi2MeanReversionUniverseService(
        sqs=fake_sqs,
        time_manager=fake_time_manager,
    )

    watchlist = await svc.get_watchlist()

    assert set(watchlist.keys()) == {"000001"}


@pytest.mark.asyncio
async def test_filters_by_baseline_trading_value(fake_sqs, fake_time_manager):
    """기본 baseline(5d 평균 거래대금) 미달 코드는 제외된다."""
    from services.rsi2_mean_reversion_universe_service import (
        Rsi2MeanReversionUniverseService,
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
        # volume 1만 → close 9000~10000 × 10000 ≈ 9500만 → 1억 미만, 50억 미달
        return _ohlcv_response(_alternating_closes(volume=10_000))

    fake_sqs.get_recent_daily_ohlcv.side_effect = ohlcv_router

    svc = Rsi2MeanReversionUniverseService(
        sqs=fake_sqs,
        time_manager=fake_time_manager,
        min_avg_trading_value_5d=5_000_000_000,
    )

    watchlist = await svc.get_watchlist()

    assert set(watchlist.keys()) == {"000001"}


@pytest.mark.asyncio
async def test_filters_by_baseline_market_cap(fake_sqs, fake_time_manager):
    """기본 baseline(시가총액) 미달 코드는 제외된다."""
    from services.rsi2_mean_reversion_universe_service import (
        Rsi2MeanReversionUniverseService,
    )

    fake_sqs.get_top_trading_value_stocks.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK",
        data=[
            _ranking_row("000001", market_cap=200_000_000_000),
            _ranking_row("000002", market_cap=50_000_000_000),  # 500억 < 1000억
        ],
    )
    fake_sqs.get_recent_daily_ohlcv.return_value = _ohlcv_response(
        _alternating_closes()
    )

    svc = Rsi2MeanReversionUniverseService(
        sqs=fake_sqs,
        time_manager=fake_time_manager,
        min_market_cap=100_000_000_000,
    )

    watchlist = await svc.get_watchlist()

    assert set(watchlist.keys()) == {"000001"}


@pytest.mark.asyncio
async def test_returns_empty_when_top_ranking_fails(fake_sqs, fake_time_manager):
    """랭킹 API 실패 시 빈 dict 반환 + ohlcv 호출 없음."""
    from services.rsi2_mean_reversion_universe_service import (
        Rsi2MeanReversionUniverseService,
    )

    fake_sqs.get_top_trading_value_stocks.return_value = ResCommonResponse(
        rt_cd="1", msg1="error", data=None,
    )

    svc = Rsi2MeanReversionUniverseService(
        sqs=fake_sqs,
        time_manager=fake_time_manager,
    )

    watchlist = await svc.get_watchlist()

    assert watchlist == {}
    fake_sqs.get_recent_daily_ohlcv.assert_not_called()


@pytest.mark.asyncio
async def test_skips_when_ohlcv_response_fails(fake_sqs, fake_time_manager):
    """일부 종목 ohlcv 실패 시 해당 종목만 skip."""
    from services.rsi2_mean_reversion_universe_service import (
        Rsi2MeanReversionUniverseService,
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

    svc = Rsi2MeanReversionUniverseService(
        sqs=fake_sqs,
        time_manager=fake_time_manager,
    )

    watchlist = await svc.get_watchlist()

    assert set(watchlist.keys()) == {"000001"}


@pytest.mark.asyncio
async def test_skips_insufficient_ohlcv_rows(fake_sqs, fake_time_manager):
    """21개 미만 일봉으로는 변동성 계산 불가 → 제외."""
    from services.rsi2_mean_reversion_universe_service import (
        Rsi2MeanReversionUniverseService,
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

    svc = Rsi2MeanReversionUniverseService(
        sqs=fake_sqs,
        time_manager=fake_time_manager,
    )

    watchlist = await svc.get_watchlist()

    assert set(watchlist.keys()) == {"000001"}


@pytest.mark.asyncio
async def test_items_have_rsi2_mean_reversion_source(fake_sqs, fake_time_manager):
    """모든 아이템의 source 필드는 'rsi2_mean_reversion'."""
    from services.rsi2_mean_reversion_universe_service import (
        Rsi2MeanReversionUniverseService,
    )

    fake_sqs.get_top_trading_value_stocks.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK",
        data=[_ranking_row("000001", market_cap=200_000_000_000)],
    )
    fake_sqs.get_recent_daily_ohlcv.return_value = _ohlcv_response(
        _alternating_closes()
    )

    svc = Rsi2MeanReversionUniverseService(
        sqs=fake_sqs,
        time_manager=fake_time_manager,
    )

    watchlist = await svc.get_watchlist()

    assert all(item.source == "rsi2_mean_reversion" for item in watchlist.values())


@pytest.mark.asyncio
async def test_caches_per_day(fake_sqs, fake_time_manager):
    """같은 거래일 안에서는 두 번째 호출 시 sqs 재호출 없음."""
    from services.rsi2_mean_reversion_universe_service import (
        Rsi2MeanReversionUniverseService,
    )

    fake_sqs.get_top_trading_value_stocks.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK",
        data=[_ranking_row("000001", market_cap=200_000_000_000)],
    )
    fake_sqs.get_recent_daily_ohlcv.return_value = _ohlcv_response(
        _alternating_closes()
    )

    svc = Rsi2MeanReversionUniverseService(
        sqs=fake_sqs,
        time_manager=fake_time_manager,
    )

    first = await svc.get_watchlist()
    second = await svc.get_watchlist()

    assert first.keys() == second.keys()
    fake_sqs.get_top_trading_value_stocks.assert_awaited_once()
    fake_sqs.get_recent_daily_ohlcv.assert_awaited_once()


@pytest.mark.asyncio
async def test_default_min_volatility_is_0_30(fake_sqs, fake_time_manager):
    """기본 변동성 임계값은 0.30 (RSI2 mean-reversion 정책 기본값)."""
    from services.rsi2_mean_reversion_universe_service import (
        Rsi2MeanReversionUniverseService,
    )

    svc = Rsi2MeanReversionUniverseService(
        sqs=fake_sqs,
        time_manager=fake_time_manager,
    )

    assert svc.min_volatility_20d_annualized == pytest.approx(0.30)


@pytest.mark.asyncio
async def test_is_market_timing_ok_delegates_to_regime_service(fake_sqs, fake_time_manager):
    """market_regime_service.classify().is_rising 을 그대로 반환."""
    from services.rsi2_mean_reversion_universe_service import (
        Rsi2MeanReversionUniverseService,
    )

    regime = MagicMock()
    regime.classify = AsyncMock(return_value=SimpleNamespace(is_rising=False))

    svc = Rsi2MeanReversionUniverseService(
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
    """regime service None 이면 항상 True (ablation 비교군 용)."""
    from services.rsi2_mean_reversion_universe_service import (
        Rsi2MeanReversionUniverseService,
    )

    svc = Rsi2MeanReversionUniverseService(
        sqs=fake_sqs,
        time_manager=fake_time_manager,
        market_regime_service=None,
    )

    assert await svc.is_market_timing_ok("KOSPI") is True
    assert await svc.is_market_timing_ok("KOSDAQ") is True
