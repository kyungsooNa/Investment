"""LarryWilliamsVBOStrategy.evaluate_single 단위 테스트 (P2 2-4 PR-2).

evaluate_single 은 event-driven shadow 평가의 fast-path 다. 폴링 경로(scan)
가 매번 REST 로 가져오던 데이터 대신, snapshot(WebSocket 캐시) + scan 이
당일 미리 캐시한 Range 만으로 BUY 조건을 평가한다.

shadow 모드 한정으로 execution_strength / program_buy 필터는 생략한다
(폴링 경로가 안전망으로 동일 시점에 다시 검증한다).
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional
from unittest.mock import MagicMock

import pytest
import pytz

from strategies.larry_williams_vbo_strategy import (
    LarryWilliamsVBOConfig,
    LarryWilliamsVBOStrategy,
)


KST = pytz.timezone("Asia/Seoul")


def _market_clock(hh: int, mm: int):
    """KST 기준 현재 시각을 고정한 mock market_clock."""
    fixed_now = KST.localize(datetime(2026, 5, 18, hh, mm, 0))
    mc = MagicMock()
    mc.get_current_kst_time.return_value = fixed_now
    return mc


def _make_strategy(hh: int = 10, mm: int = 0) -> LarryWilliamsVBOStrategy:
    s = LarryWilliamsVBOStrategy(
        stock_query_service=MagicMock(),
        market_clock=_market_clock(hh, mm),
        universe_service=None,
        config=LarryWilliamsVBOConfig(k_value=0.5),
        logger=MagicMock(),
    )
    return s


def _seed(strategy: LarryWilliamsVBOStrategy, code: str, range_value: float):
    """scan() 가 채웠을 상태를 직접 주입."""
    strategy._current_candidate_codes_set = {code}
    strategy._range_cache.date = strategy._tm.get_current_kst_time().strftime("%Y%m%d")
    strategy._range_cache.ranges[code] = range_value


@pytest.mark.asyncio
async def test_evaluate_single_returns_buy_when_price_above_target():
    s = _make_strategy(10, 0)
    _seed(s, "005930", range_value=1000.0)
    snapshot = {"open": 70000.0, "price": "70600"}  # target = 70000 + 1000*0.5 = 70500

    sig = await s.evaluate_single("005930", snapshot)

    assert sig is not None
    assert sig.action == "BUY"
    assert sig.code == "005930"
    assert sig.price == 70600
    assert sig.strategy_name == s.name


@pytest.mark.asyncio
async def test_evaluate_single_returns_none_when_below_target():
    s = _make_strategy(10, 0)
    _seed(s, "005930", range_value=1000.0)
    snapshot = {"open": 70000.0, "price": "70400"}  # target = 70500

    sig = await s.evaluate_single("005930", snapshot)
    assert sig is None


@pytest.mark.asyncio
async def test_evaluate_single_returns_none_outside_entry_window():
    s = _make_strategy(8, 30)  # before _ENTRY_START
    _seed(s, "005930", range_value=1000.0)
    snapshot = {"open": 70000.0, "price": "75000"}

    sig = await s.evaluate_single("005930", snapshot)
    assert sig is None


@pytest.mark.asyncio
async def test_evaluate_single_returns_none_after_entry_cutoff():
    s = _make_strategy(14, 30)  # after _ENTRY_CUTOFF (14:00)
    _seed(s, "005930", range_value=1000.0)
    snapshot = {"open": 70000.0, "price": "75000"}

    sig = await s.evaluate_single("005930", snapshot)
    assert sig is None


@pytest.mark.asyncio
async def test_evaluate_single_returns_none_for_non_candidate():
    s = _make_strategy(10, 0)
    _seed(s, "005930", range_value=1000.0)
    snapshot = {"open": 70000.0, "price": "75000"}

    sig = await s.evaluate_single("000660", snapshot)  # not in candidate set
    assert sig is None


@pytest.mark.asyncio
async def test_evaluate_single_returns_none_when_range_missing():
    s = _make_strategy(10, 0)
    # candidate but no range cached
    s._current_candidate_codes_set = {"005930"}
    snapshot = {"open": 70000.0, "price": "75000"}

    sig = await s.evaluate_single("005930", snapshot)
    assert sig is None


@pytest.mark.asyncio
async def test_evaluate_single_returns_none_when_already_bought_today():
    s = _make_strategy(10, 0)
    _seed(s, "005930", range_value=1000.0)
    s._bought_today.add("005930")
    s._last_date = s._tm.get_current_kst_time().strftime("%Y%m%d")
    snapshot = {"open": 70000.0, "price": "75000"}

    sig = await s.evaluate_single("005930", snapshot)
    assert sig is None


@pytest.mark.asyncio
async def test_evaluate_single_returns_none_when_snapshot_missing_price():
    s = _make_strategy(10, 0)
    _seed(s, "005930", range_value=1000.0)
    snapshot = {"open": 70000.0}  # no price

    sig = await s.evaluate_single("005930", snapshot)
    assert sig is None


@pytest.mark.asyncio
async def test_evaluate_single_handles_zero_open_price():
    s = _make_strategy(10, 0)
    _seed(s, "005930", range_value=1000.0)
    snapshot = {"open": 0, "price": "75000"}

    sig = await s.evaluate_single("005930", snapshot)
    assert sig is None


@pytest.mark.asyncio
async def test_evaluate_single_records_signal_stat():
    s = _make_strategy(10, 0)
    _seed(s, "005930", range_value=1000.0)

    sig = await s.evaluate_single("005930", {"open": 70000.0, "price": "70600"})

    assert sig is not None
    assert s._shadow_eval_stats["005930"]["evaluated"] == 1
    assert s._shadow_eval_stats["005930"]["signal"] == 1


@pytest.mark.asyncio
async def test_evaluate_single_records_invalid_open_stat():
    """open 누락 시 reject_invalid_open 으로 집계 (023530 진단 가설)."""
    s = _make_strategy(10, 0)
    _seed(s, "005930", range_value=1000.0)

    sig = await s.evaluate_single("005930", {"price": "70600"})  # open 없음

    assert sig is None
    stats = s._shadow_eval_stats["005930"]
    assert stats["evaluated"] == 1
    assert stats["reject_invalid_open"] == 1
    assert stats["signal"] == 0


@pytest.mark.asyncio
async def test_evaluate_single_records_below_target_stat():
    s = _make_strategy(10, 0)
    _seed(s, "005930", range_value=1000.0)

    sig = await s.evaluate_single("005930", {"open": 70000.0, "price": "70400"})  # target 70500

    assert sig is None
    assert s._shadow_eval_stats["005930"]["reject_below_target"] == 1


@pytest.mark.asyncio
async def test_evaluate_single_records_not_candidate_stat():
    s = _make_strategy(10, 0)
    _seed(s, "005930", range_value=1000.0)

    sig = await s.evaluate_single("000660", {"open": 70000.0, "price": "75000"})

    assert sig is None
    assert s._shadow_eval_stats["000660"]["reject_not_candidate"] == 1


def test_current_candidate_codes_reflects_scan_state():
    s = _make_strategy(10, 0)
    assert s.current_candidate_codes() == []

    s._current_candidate_codes_set = {"005930", "000660"}
    codes = s.current_candidate_codes()
    assert sorted(codes) == ["000660", "005930"]
