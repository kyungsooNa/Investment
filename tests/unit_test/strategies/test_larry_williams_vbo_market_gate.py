"""LarryWilliamsVBO 전략과 시장 국면의 관계를 잠그는 단위 테스트.

#766 이 마켓타이밍 차단을 스케줄러 공통 주문 게이트로 옮긴 뒤로, 이 전략은 레짐에 따라
스캔을 바꾸지 않는다. 그래서 여기서 잠그는 것은 '차단'이 아니라 **차단하지 않음**이다.

- bear regime 이어도 관찰용 진입 평가는 계속한다
- 전략 수준의 entry_rejected/market_timing_off 는 남기지 않는다
- 마켓타이밍을 조회하지도 않는다
- universe_service 없음 → fallback 경로 호환
"""
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytz

from common.types import ErrorCode, ResCommonResponse
from strategies.larry_williams_vbo_strategy import (
    LarryWilliamsVBOConfig,
    LarryWilliamsVBOStrategy,
)

KST = pytz.timezone("Asia/Seoul")


def _kst(h: int, m: int, date: str = "2026-01-15") -> datetime:
    y, mo, d = (int(x) for x in date.split("-"))
    return KST.localize(datetime(y, mo, d, h, m))


def _make_universe(timing_by_market: dict):
    universe = MagicMock()

    async def _is_ok(market, caller="", logger=None):
        return timing_by_market.get(market, True)

    async def _get_watchlist(logger=None):
        # KOSDAQ 종목 1개를 반환
        item = MagicMock()
        item.code = "035720"
        item.name = "카카오"
        item.market = "KOSDAQ"
        item.market_cap = 500_000_000_000
        item.avg_trading_value_5d = 100_000_000_000
        return {"035720": item}

    universe.is_market_timing_ok = AsyncMock(side_effect=_is_ok)
    universe.get_watchlist = AsyncMock(side_effect=_get_watchlist)
    return universe


def _make_strategy(universe, now_time=None):
    sqs = MagicMock()
    sqs.handle_get_current_stock_price = AsyncMock()
    sqs.prefetch_prices = AsyncMock(return_value=0)
    sqs.get_recent_daily_ohlcv = AsyncMock()

    tm = MagicMock()
    tm.get_current_kst_time.return_value = now_time or _kst(10, 0)

    cfg = LarryWilliamsVBOConfig(
        k_value=0.5, min_market_cap=0, min_5d_trading_value=0,
        confidence_threshold=120.0, program_buy_ratio=0.10, stop_loss_pct=-3.0,
    )
    return LarryWilliamsVBOStrategy(
        stock_query_service=sqs,
        market_clock=tm,
        universe_service=universe,
        config=cfg,
        logger=MagicMock(),
    ), sqs


@pytest.mark.asyncio
async def test_bear_regime_keeps_observation_scan():
    """KOSDAQ 베어여도 관찰용 진입 평가는 계속한다."""
    universe = _make_universe({"KOSPI": True, "KOSDAQ": False})
    strategy, sqs = _make_strategy(universe)

    signals = await strategy.scan()

    assert signals == []
    sqs.handle_get_current_stock_price.assert_called()
    assert strategy._bought_today == set()


@pytest.mark.asyncio
async def test_bear_regime_does_not_emit_strategy_level_rejection():
    """시장 레짐 차단 로그는 스케줄러 공통 주문 게이트가 남긴다."""
    universe = _make_universe({"KOSPI": True, "KOSDAQ": False})
    strategy, _ = _make_strategy(universe)
    await strategy.scan()

    logger = strategy._logger
    rejection_calls = [
        c for c in logger.info.call_args_list
        if isinstance(c.args[0], dict) and c.args[0].get("event") == "entry_rejected"
    ]
    assert not any(c.args[0].get("reason") == "market_timing_off" for c in rejection_calls)


@pytest.mark.asyncio
async def test_bull_regime_passes_gate_and_evaluates_filters():
    """KOSDAQ 불 → 게이트 통과, 가격 조회로 진입 평가가 진행되어야 한다."""
    universe = _make_universe({"KOSPI": True, "KOSDAQ": True})
    strategy, sqs = _make_strategy(universe)

    # Range 캐시가 비어있으면 range_unavailable 로 reject 되지만 게이트는 통과한 것임
    sqs.handle_get_current_stock_price.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK",
        data={"price": "50000", "open": "49000", "pgtr_ntby_qty": "0", "acml_tr_pbmn": "0"},
    )
    sqs.get_recent_daily_ohlcv.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK",
        data=[{"date": "20260114", "open": 49000, "high": 50500, "low": 48500, "close": 49500}] * 2,
    )

    await strategy.scan()
    sqs.handle_get_current_stock_price.assert_awaited()  # 게이트를 통과해 가격 조회로 진입


@pytest.mark.asyncio
async def test_universe_none_bypasses_market_gate():
    """universe_service 미주입 시 게이트 우회 (fallback 호환)."""
    strategy, sqs = _make_strategy(universe=None)
    # fallback 경로 — universe 미주입이지만 dict 직접 주입으로 validity filter 통과까지 검증
    sqs.get_top_trading_value_stocks = AsyncMock(return_value=ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK",
        data=[{"mksc_shrn_iscd": "005930", "hts_kor_isnm": "삼성전자", "stck_avls": "500000000000"}],
    ))
    strategy._load_pool_b = AsyncMock(return_value=[{
        "code": "005930", "name": "삼성전자", "market": "",
        "market_cap": 500_000_000_000, "avg_5d_tv": 50_000_000_000,
    }])
    sqs.handle_get_current_stock_price.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK",
        data={"price": "70000", "open": "69000", "pgtr_ntby_qty": "0", "acml_tr_pbmn": "0"},
    )
    sqs.get_recent_daily_ohlcv.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK",
        data=[{"date": "20260114", "open": 69000, "high": 70500, "low": 68500, "close": 69500}] * 2,
    )

    # universe 없으면 게이트 미수행 — 가격 조회까지 도달
    await strategy.scan()
    sqs.handle_get_current_stock_price.assert_awaited()


@pytest.mark.asyncio
async def test_scan_does_not_query_market_timing_at_all():
    """마켓타이밍 판정은 스케줄러 공통 주문 게이트가 한다 — 전략은 조회하지 않는다.

    #766 이 차단 코드를 스케줄러로 옮기면서 전략에는 결과를 쓰지 않는 조회만 남았다.
    조회가 남아 있으면 '이 전략이 마켓타이밍을 본다'고 코드가 거짓말을 한다.
    """
    universe = _make_universe({"KOSPI": True, "KOSDAQ": False})
    strategy, _ = _make_strategy(universe)

    await strategy.scan()

    universe.is_market_timing_ok.assert_not_called()
