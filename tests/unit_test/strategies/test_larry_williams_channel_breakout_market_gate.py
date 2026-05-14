"""LarryWilliamsCB 전략의 시장 국면 게이트 단위 테스트.

- bear regime → 종목 skip, entry_rejected/reason=market_timing_off
- bull regime → 기존 로직 진행
"""
import os
import tempfile
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytz

from strategies.larry_williams_channel_breakout_strategy import (
    LarryWilliamsChannelBreakoutStrategy,
)
from strategies.larry_williams_cb_types import LarryWilliamsCBConfig

KST = pytz.timezone("Asia/Seoul")


def _kst(h: int, m: int, date: str = "2026-01-15") -> datetime:
    y, mo, d = (int(x) for x in date.split("-"))
    return KST.localize(datetime(y, mo, d, h, m))


def _make_watchlist_item(code: str, market: str, name: str = "X"):
    item = MagicMock()
    item.code = code
    item.name = name
    item.market = market
    item.rs_rating = 90  # rs_rating_min(80) 통과
    item.high_20d = 100
    item.avg_vol_20d = 1_000_000
    return item


def _make_universe(timing_by_market: dict, watchlist):
    universe = MagicMock()

    async def _is_ok(market, caller="", logger=None):
        return timing_by_market.get(market, True)

    universe.is_market_timing_ok = AsyncMock(side_effect=_is_ok)
    universe.get_watchlist = AsyncMock(return_value=watchlist)
    return universe


def _make_strategy(universe, now_time=None, state_file=None):
    sqs = MagicMock()
    sqs.get_ohlcv = AsyncMock()
    sqs.get_current_price = AsyncMock()
    indicator = MagicMock()
    indicator.calc_adx_sync = MagicMock(return_value=None)

    tm = MagicMock()
    tm.get_current_kst_time.return_value = now_time or _kst(15, 20)

    cfg = LarryWilliamsCBConfig()
    return LarryWilliamsChannelBreakoutStrategy(
        stock_query_service=sqs,
        universe_service=universe,
        indicator_service=indicator,
        market_clock=tm,
        config=cfg,
        logger=MagicMock(),
        state_file=state_file,
    ), sqs


@pytest.mark.asyncio
async def test_bear_regime_skips_stock_and_logs_market_timing_off(tmp_path):
    """KOSDAQ 베어 → KOSDAQ 종목 skip, _check_entry 호출되지 않음."""
    state_file = tmp_path / "lwcb_state.json"
    watchlist = {"035720": _make_watchlist_item("035720", "KOSDAQ", "카카오")}
    universe = _make_universe({"KOSPI": True, "KOSDAQ": False}, watchlist)
    strategy, sqs = _make_strategy(universe, state_file=str(state_file))

    signals = await strategy.scan()

    assert signals == []
    # OHLCV 조회조차 안 했어야 함 — 진입 평가 자체가 차단됨
    sqs.get_ohlcv.assert_not_called()
    # state 변경 없음
    assert strategy._position_state == {}


@pytest.mark.asyncio
async def test_bear_regime_emits_market_timing_off_log(tmp_path):
    """KOSDAQ 베어 → reason=market_timing_off 로그가 남는다."""
    state_file = tmp_path / "lwcb_state.json"
    watchlist = {"035720": _make_watchlist_item("035720", "KOSDAQ")}
    universe = _make_universe({"KOSPI": True, "KOSDAQ": False}, watchlist)
    strategy, _ = _make_strategy(universe, state_file=str(state_file))

    await strategy.scan()
    logger = strategy._logger
    rejections = [
        c.args[0] for c in logger.info.call_args_list
        if isinstance(c.args[0], dict) and c.args[0].get("event") == "entry_rejected"
    ]
    assert any(r.get("reason") == "market_timing_off" for r in rejections), (
        f"market_timing_off 로그 부재. rejects={rejections}"
    )


@pytest.mark.asyncio
async def test_bull_regime_proceeds_to_entry_check(tmp_path):
    """KOSPI/KOSDAQ 모두 불 → 게이트 통과해 OHLCV 조회로 진입."""
    state_file = tmp_path / "lwcb_state.json"
    watchlist = {"005930": _make_watchlist_item("005930", "KOSPI", "삼성전자")}
    universe = _make_universe({"KOSPI": True, "KOSDAQ": True}, watchlist)
    strategy, sqs = _make_strategy(universe, state_file=str(state_file))

    # OHLCV 응답을 적당히 줘서 게이트 통과 후 _check_entry 가 호출되는 것 확인
    sqs.get_ohlcv.return_value = MagicMock(rt_cd="0", data=[{"close": 100}])
    await strategy.scan()
    sqs.get_ohlcv.assert_awaited()  # 게이트 통과 → 진입 평가 시작
