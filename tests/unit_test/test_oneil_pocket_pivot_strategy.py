# tests/unit_test/test_oneil_pocket_pivot_strategy.py
import pytest
from unittest.mock import MagicMock, AsyncMock
from unittest.mock import MagicMock, AsyncMock, patch
from datetime import datetime
from common.types import ResCommonResponse, TradeSignal
from strategies.oneil_pocket_pivot_strategy import OneilPocketPivotStrategy
from strategies.oneil_common_types import (
    OneilPocketPivotConfig, PPPositionState, OSBWatchlistItem,
)


def _make_ohlcv(count=60, close=68000, open_=67000, volume=50000):
    """테스트용 OHLCV 데이터 생성 (하락일: close < open)."""
    base_date = 20250101
    return [
        {"date": str(base_date + i), "open": open_, "close": close, "high": close + 500, "low": close - 500, "volume": volume}
        for i in range(count)
    ]

# ── 공통 Fixture ──────────────────────────────────────────────

@pytest.fixture
def mock_deps():
    ts = MagicMock()
    universe = MagicMock()
    tm = MagicMock()
    logger = MagicMock()

    ts.get_current_stock_price = AsyncMock()
    ts.get_stock_conclusion = AsyncMock()
    ts.get_recent_daily_ohlcv = AsyncMock()
    universe.get_watchlist = AsyncMock()
    universe.is_market_timing_ok = AsyncMock()

    return ts, universe, tm, logger


@pytest.fixture
def watchlist_item():
    """PP/BGU 테스트용 워치리스트 아이템."""
    return OSBWatchlistItem(
        code="005930", name="삼성전자", market="KOSPI",
        high_20d=70000, ma_20d=68000, ma_50d=65000,
        avg_vol_20d=100000, bb_width_min_20d=1000, prev_bb_width=1100,
        w52_hgpr=80000, avg_trading_value_5d=50_000_000_000,
        market_cap=100_000_000_000,
    )


def _make_ohlcv(count=60, close=68000, open_=67000, volume=50000):
    """테스트용 OHLCV 데이터 생성 (하락일: close < open)."""
    base_date = 20250101
    return [
        {"date": str(base_date + i), "open": open_, "close": close, "high": close + 500, "low": close - 500, "volume": volume}
        for i in range(count)
    ]


def _make_ohlcv_with_dates(dates, close=68000, open_=67000, volume=50000):
    """특정 날짜 리스트로 OHLCV 생성."""
    return [
        {"date": d, "open": open_, "close": close, "high": close + 500, "low": close - 500, "volume": volume}
        for d in dates
    ]


@pytest.fixture
def pp_scan_setup(mock_deps, watchlist_item):
    """scan() 테스트 공통 셋업 (Pocket Pivot 조건에 맞게)."""
    ts, universe, tm, logger = mock_deps
    strategy = OneilPocketPivotStrategy(ts, universe, tm, logger=logger)
    strategy._position_state = {}
    strategy._save_state = MagicMock()

    universe.get_watchlist.return_value = {"005930": watchlist_item}
    universe.is_market_timing_ok.return_value = True

    # 장중 50% 경과
    tm.get_current_kst_time.return_value = datetime(2025, 1, 1, 12, 0, 0)
    tm.get_market_open_time.return_value = datetime(2025, 1, 1, 9, 0, 0)
    tm.get_market_close_time.return_value = datetime(2025, 1, 1, 15, 30, 0)

    # OHLCV: 하락일(close < open) 거래량 50000
    ohlcv = _make_ohlcv(60, close=67500, open_=68000, volume=50000)
    ts.get_recent_daily_ohlcv.return_value = ohlcv

    return strategy, ts, universe, tm, logger


def _pp_price_output(
    current="68500", vol="200000", pg_buy="30000",
    trade_value="14200000000", today_open="68000",
    today_low="67500", prev_close="67000",
):
    """Pocket Pivot 매수 조건에 맞는 현재가 API 응답 생성."""
    return ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {
            "stck_prpr": current, "acml_vol": vol,
            "pgtr_ntby_qty": pg_buy, "acml_tr_pbmn": trade_value,
            "stck_oprc": today_open, "stck_lwpr": today_low,
            "stck_prdy_clpr": prev_close,
        }}
    )


def _cgld_output(strength="150.0"):
    return ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": [{"tday_rltv": strength}]}
    )


# ════════════════════════════════════════════════════════════════
# scan() — Pocket Pivot 진입
# ════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_scan_pp_buy_signal(pp_scan_setup):
    """PP: 모든 조건 충족 시 매수 시그널 생성."""
    strategy, ts, _, _, _ = pp_scan_setup

    # 현재가 68500 → 20MA(68000)의 -2%~+4% 범위 내, > 전일종가 67000
    # 환산 거래량: 200000/0.5=400000 > 하락일 MAX 50000
    # PG: 30000*68500=20.55억, 거래대금 142억 → 14.5%(>10%), 시총 1000억 → 2.05%(>0.3%)
    ts.get_current_stock_price.return_value = _pp_price_output()
    ts.get_stock_conclusion.return_value = _cgld_output("150.0")

    signals = await strategy.scan()

    assert len(signals) == 1
    assert signals[0].action == "BUY"
    assert "PP진입" in signals[0].reason
    assert "MA지지" in signals[0].reason
    assert "005930" in strategy._position_state
    assert strategy._position_state["005930"].entry_type == "PP"
    assert strategy._position_state["005930"].supporting_ma in ("10", "20", "50")


@pytest.mark.asyncio
async def test_scan_pp_rejects_price_not_near_ma(pp_scan_setup):
    """PP: 현재가가 모든 MA에서 벗어나면 시그널 없음."""
    strategy, ts, _, _, _ = pp_scan_setup

    # 현재가 75000 → 10MA(~67500)의 +4% 범위 초과
    ts.get_current_stock_price.return_value = _pp_price_output(current="75000", prev_close="67000")
    ts.get_stock_conclusion.return_value = _cgld_output()

    signals = await strategy.scan()
    assert len(signals) == 0


@pytest.mark.asyncio
async def test_scan_pp_rejects_down_day(pp_scan_setup):
    """PP: 당일 하락일(현재가 <= 전일종가)이면 시그널 없음."""
    strategy, ts, _, _, _ = pp_scan_setup

    # 현재가 67000 <= 전일종가 67000 → 하락일
    ts.get_current_stock_price.return_value = _pp_price_output(current="67000", prev_close="67000")
    ts.get_stock_conclusion.return_value = _cgld_output()

    signals = await strategy.scan()
    assert len(signals) == 0


@pytest.mark.asyncio
async def test_scan_pp_rejects_low_volume(pp_scan_setup):
    """PP: 환산 거래량이 하락일 최대 거래량 이하이면 시그널 없음."""
    strategy, ts, _, _, _ = pp_scan_setup

    # 거래량 20000 → 환산 40000 < 하락일 MAX 50000
    ts.get_current_stock_price.return_value = _pp_price_output(vol="20000")
    ts.get_stock_conclusion.return_value = _cgld_output()

    signals = await strategy.scan()
    assert len(signals) == 0


@pytest.mark.asyncio
async def test_scan_pp_rejects_low_execution_strength(pp_scan_setup):
    """PP: 체결강도 < 120%이면 시그널 없음."""
    strategy, ts, _, _, _ = pp_scan_setup

    ts.get_current_stock_price.return_value = _pp_price_output()
    ts.get_stock_conclusion.return_value = _cgld_output("110.0")

    signals = await strategy.scan()
    assert len(signals) == 0


@pytest.mark.asyncio
async def test_scan_pp_rejects_low_smart_money(pp_scan_setup):
    """PP: 스마트머니 필터(PG비율) 미달 시 시그널 없음."""
    strategy, ts, _, _, _ = pp_scan_setup

    # PG 순매수 500주 * 68500 = 3425만원 → 거래대금 142억 대비 0.24% (< 10%)
    ts.get_current_stock_price.return_value = _pp_price_output(pg_buy="500")
    ts.get_stock_conclusion.return_value = _cgld_output()

    signals = await strategy.scan()
    assert len(signals) == 0


# ════════════════════════════════════════════════════════════════
# scan() — BGU 진입
# ════════════════════════════════════════════════════════════════

@pytest.fixture
def bgu_scan_setup(mock_deps, watchlist_item):
    """BGU scan() 전용 셋업."""
    ts, universe, tm, logger = mock_deps
    strategy = OneilPocketPivotStrategy(ts, universe, tm, logger=logger)
    strategy._position_state = {}
    strategy._save_state = MagicMock()

    universe.get_watchlist.return_value = {"005930": watchlist_item}
    universe.is_market_timing_ok.return_value = True

    # 장 시작 후 30분 (09:30) — BGU 휩소 필터 통과
    tm.get_current_kst_time.return_value = datetime(2025, 1, 1, 9, 30, 0)
    tm.get_market_open_time.return_value = datetime(2025, 1, 1, 9, 0, 0)
    tm.get_market_close_time.return_value = datetime(2025, 1, 1, 15, 30, 0)

    # OHLCV: 상승일(close > open) — PP에는 안 걸리도록 거래량 크게
    ohlcv = _make_ohlcv(60, close=69000, open_=68500, volume=100000)
    ts.get_recent_daily_ohlcv.return_value = ohlcv

    return strategy, ts, universe, tm, logger


def _bgu_price_output(
    current="72900", vol="500000", pg_buy="30000",
    trade_value="14200000000", today_open="72800",
    today_low="72000", prev_close="70000",
):
    """BGU 매수 조건에 맞는 현재가 API 응답 (갭 +4%)."""
    return ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {
            "stck_prpr": current, "acml_vol": vol,
            "pgtr_ntby_qty": pg_buy, "acml_tr_pbmn": trade_value,
            "stck_oprc": today_open, "stck_lwpr": today_low,
            "stck_prdy_clpr": prev_close,
        }}
    )


@pytest.mark.asyncio
async def test_scan_bgu_buy_signal(bgu_scan_setup):
    """BGU: 갭 4%+ & 환산 거래량 300%+ & 09:10+ & 시가 지지 시 매수 시그널."""
    strategy, ts, _, _, _ = bgu_scan_setup

    # 시가 72800 vs 전일종가 70000 → 갭 +4%
    # 환산 거래량: 500000/progress → 500000/(30/390)=6,500,000 >> 100000*3
    ts.get_current_stock_price.return_value = _bgu_price_output()
    ts.get_stock_conclusion.return_value = _cgld_output("130.0")

    signals = await strategy.scan()

    assert len(signals) == 1
    assert "BGU진입" in signals[0].reason
    assert "005930" in strategy._position_state
    assert strategy._position_state["005930"].entry_type == "BGU"
    assert strategy._position_state["005930"].gap_day_low == 72000


@pytest.mark.asyncio
async def test_scan_bgu_rejects_small_gap(bgu_scan_setup):
    """BGU: 갭 < 4%이면 시그널 없음."""
    strategy, ts, _, _, _ = bgu_scan_setup

    # 시가 72000 vs 전일종가 70000 → 갭 2.86% < 4%
    ts.get_current_stock_price.return_value = _bgu_price_output(today_open="72000", current="72500")
    ts.get_stock_conclusion.return_value = _cgld_output()

    signals = await strategy.scan()
    assert len(signals) == 0


@pytest.mark.asyncio
async def test_scan_bgu_rejects_before_whipsaw_time(bgu_scan_setup):
    """BGU: 장 시작 후 10분 미만(09:10 전)이면 시그널 없음."""
    strategy, ts, _, tm, _ = bgu_scan_setup

    # 09:05 → 장 시작 후 5분 < 10분
    tm.get_current_kst_time.return_value = datetime(2025, 1, 1, 9, 5, 0)

    ts.get_current_stock_price.return_value = _bgu_price_output()
    ts.get_stock_conclusion.return_value = _cgld_output()

    signals = await strategy.scan()
    assert len(signals) == 0


@pytest.mark.asyncio
async def test_scan_bgu_rejects_price_below_open(bgu_scan_setup):
    """BGU: 현재가 < 시가(가격 지지 실패)이면 시그널 없음."""
    strategy, ts, _, _, _ = bgu_scan_setup

    # 현재가 72500 < 시가 72800
    ts.get_current_stock_price.return_value = _bgu_price_output(current="72500")
    ts.get_stock_conclusion.return_value = _cgld_output()

    signals = await strategy.scan()
    assert len(signals) == 0


@pytest.mark.asyncio
async def test_scan_bgu_rejects_low_volume(bgu_scan_setup):
    """BGU: 환산 거래량 < 50일 평균 × 300%이면 시그널 없음."""
    strategy, ts, _, _, _ = bgu_scan_setup

    # 거래량 5000 → 환산 = 5000/(30/390)=65000 < 100000*3
    ts.get_current_stock_price.return_value = _bgu_price_output(vol="5000")
    ts.get_stock_conclusion.return_value = _cgld_output()

    signals = await strategy.scan()
    assert len(signals) == 0


# ════════════════════════════════════════════════════════════════
# scan() — 공통 edge case
# ════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_scan_empty_watchlist(pp_scan_setup):
    """빈 워치리스트 → 빈 리스트."""
    strategy, _, universe, _, _ = pp_scan_setup
    universe.get_watchlist.return_value = {}
    assert await strategy.scan() == []


@pytest.mark.asyncio
async def test_scan_market_not_ready(pp_scan_setup):
    """장 시작 전(progress <= 0) → 빈 리스트."""
    strategy, _, _, tm, _ = pp_scan_setup
    tm.get_current_kst_time.return_value = datetime(2025, 1, 1, 9, 0, 0)
    tm.get_market_open_time.return_value = datetime(2025, 1, 1, 9, 0, 0)
    assert await strategy.scan() == []


@pytest.mark.asyncio
async def test_scan_skip_existing_position(pp_scan_setup):
    """이미 보유 중인 종목 스캔 제외."""
    strategy, ts, _, _, _ = pp_scan_setup
    strategy._position_state["005930"] = PPPositionState(
        "PP", 68000, "20250101", 68000, "20", 0, False, ""
    )
    ts.get_current_stock_price.reset_mock()
    assert await strategy.scan() == []
    ts.get_current_stock_price.assert_not_called()


@pytest.mark.asyncio
async def test_scan_bad_market_timing(pp_scan_setup):
    """마켓 타이밍 불량 시 스캔 제외."""
    strategy, ts, universe, _, _ = pp_scan_setup
    universe.is_market_timing_ok.return_value = False
    assert await strategy.scan() == []
    ts.get_current_stock_price.assert_not_called()


@pytest.mark.asyncio
async def test_scan_api_failure(pp_scan_setup):
    """현재가 API 실패 시 시그널 없음."""
    strategy, ts, _, _, _ = pp_scan_setup
    ts.get_current_stock_price.return_value = ResCommonResponse(rt_cd="1", msg1="Fail")
    assert await strategy.scan() == []


@pytest.mark.asyncio
async def test_scan_exception_handling(pp_scan_setup):
    """개별 종목 예외 발생 시 로그 남기고 계속 진행."""
    strategy, ts, _, _, logger = pp_scan_setup
    ts.get_current_stock_price.side_effect = Exception("API Timeout")
    signals = await strategy.scan()
    assert len(signals) == 0
    logger.error.assert_called()


# ════════════════════════════════════════════════════════════════
# check_exits() — PP 손절
# ════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_exits_pp_stop_loss(mock_deps):
    """PP 손절: 지지 MA -2% 이하 이탈 시 매도."""
    ts, universe, tm, logger = mock_deps
    strategy = OneilPocketPivotStrategy(ts, universe, tm, logger=logger)
    strategy._save_state = MagicMock()
    universe.is_market_timing_ok.return_value = True

    # 20MA 지지로 진입, entry_price=68000
    strategy._position_state["005930"] = PPPositionState(
        "PP", 68000, "20250101", 68000, "20", 0, False, ""
    )

    # OHLCV: closes 평균 68000 → 20MA ≈ 68000, threshold = 68000 * 0.98 = 66640
    ohlcv = _make_ohlcv(60, close=68000, open_=67000, volume=50000)
    ts.get_recent_daily_ohlcv.return_value = ohlcv
    tm.get_current_kst_time.return_value = datetime(2025, 1, 1, 12, 0, 0)

    # 현재가 66000 < 66640 → 손절
    ts.get_current_stock_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {"stck_prpr": "66000"}}
    )

    signals = await strategy.check_exits([{"code": "005930", "buy_price": 68000, "market": "KOSPI"}])

    assert len(signals) == 1
    assert signals[0].action == "SELL"
    assert "PP손절" in signals[0].reason
    assert "005930" not in strategy._position_state


@pytest.mark.asyncio
async def test_exits_pp_no_stop_above_ma(mock_deps):
    """PP: 현재가가 MA 위에 있으면 손절 안 함."""
    ts, universe, tm, logger = mock_deps
    strategy = OneilPocketPivotStrategy(ts, universe, tm, logger=logger)
    strategy._save_state = MagicMock()
    universe.is_market_timing_ok.return_value = True

    strategy._position_state["005930"] = PPPositionState(
        "PP", 68000, "20250101", 69000, "20", 0, False, ""
    )

    ohlcv = _make_ohlcv(60, close=68000, open_=67000, volume=50000)
    ts.get_recent_daily_ohlcv.return_value = ohlcv
    tm.get_current_kst_time.return_value = datetime(2025, 1, 1, 12, 0, 0)

    # 현재가 69000 > 66640 → 안전
    ts.get_current_stock_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {"stck_prpr": "69000"}}
    )

    signals = await strategy.check_exits([{"code": "005930", "buy_price": 68000, "market": "KOSPI"}])
    assert len(signals) == 0


# ════════════════════════════════════════════════════════════════
# check_exits() — BGU 손절
# ════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_exits_bgu_stop_loss(mock_deps):
    """BGU 손절: 갭업 당일 저가 이탈 시 매도."""
    ts, universe, tm, logger = mock_deps
    strategy = OneilPocketPivotStrategy(ts, universe, tm, logger=logger)
    strategy._save_state = MagicMock()
    universe.is_market_timing_ok.return_value = True

    strategy._position_state["005930"] = PPPositionState(
        "BGU", 73000, "20250101", 73000, "", 72000, False, ""
    )

    ts.get_recent_daily_ohlcv.return_value = _make_ohlcv(60, close=73000)
    tm.get_current_kst_time.return_value = datetime(2025, 1, 1, 12, 0, 0)

    # 현재가 71500 < gap_day_low 72000 → 손절
    ts.get_current_stock_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {"stck_prpr": "71500"}}
    )

    signals = await strategy.check_exits([{"code": "005930", "buy_price": 73000, "market": "KOSPI"}])

    assert len(signals) == 1
    assert "BGU손절" in signals[0].reason
    assert "005930" not in strategy._position_state


@pytest.mark.asyncio
async def test_exits_bgu_no_stop_above_low(mock_deps):
    """BGU: 현재가가 갭업 저가 위에 있으면 손절 안 함."""
    ts, universe, tm, logger = mock_deps
    strategy = OneilPocketPivotStrategy(ts, universe, tm, logger=logger)
    strategy._save_state = MagicMock()
    universe.is_market_timing_ok.return_value = True

    strategy._position_state["005930"] = PPPositionState(
        "BGU", 73000, "20250101", 74000, "", 72000, False, ""
    )

    ts.get_recent_daily_ohlcv.return_value = _make_ohlcv(60, close=73000)
    tm.get_current_kst_time.return_value = datetime(2025, 1, 1, 12, 0, 0)

    ts.get_current_stock_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {"stck_prpr": "73500"}}
    )

    signals = await strategy.check_exits([{"code": "005930", "buy_price": 73000, "market": "KOSPI"}])
    assert len(signals) == 0


# ════════════════════════════════════════════════════════════════
# check_exits() — 하드 스탑
# ════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_exits_hard_stop_market_timing(mock_deps):
    """하드 스탑: 마켓타이밍 악화 시 즉시 청산."""
    ts, universe, tm, logger = mock_deps
    strategy = OneilPocketPivotStrategy(ts, universe, tm, logger=logger)
    strategy._save_state = MagicMock()
    universe.is_market_timing_ok.return_value = False  # 마켓타이밍 악화

    strategy._position_state["005930"] = PPPositionState(
        "PP", 68000, "20250101", 70000, "20", 0, False, ""
    )

    ts.get_recent_daily_ohlcv.return_value = _make_ohlcv(60, close=69000)
    tm.get_current_kst_time.return_value = datetime(2025, 1, 1, 12, 0, 0)

    ts.get_current_stock_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {"stck_prpr": "69000"}}
    )

    signals = await strategy.check_exits([{"code": "005930", "buy_price": 68000, "market": "KOSPI"}])

    assert len(signals) == 1
    assert "하드스탑" in signals[0].reason
    assert "마켓타이밍" in signals[0].reason


@pytest.mark.asyncio
async def test_exits_hard_stop_peak_drawdown(mock_deps):
    """하드 스탑: 고점 대비 -10% 하락 시 즉시 청산."""
    ts, universe, tm, logger = mock_deps
    strategy = OneilPocketPivotStrategy(ts, universe, tm, logger=logger)
    strategy._save_state = MagicMock()
    universe.is_market_timing_ok.return_value = True

    # peak_price=80000, 현재가 71000 → -11.25%
    strategy._position_state["005930"] = PPPositionState(
        "PP", 70000, "20250101", 80000, "20", 0, False, ""
    )

    ts.get_recent_daily_ohlcv.return_value = _make_ohlcv(60, close=75000)
    tm.get_current_kst_time.return_value = datetime(2025, 1, 1, 12, 0, 0)

    ts.get_current_stock_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {"stck_prpr": "71000"}}
    )

    signals = await strategy.check_exits([{"code": "005930", "buy_price": 70000, "market": "KOSPI"}])

    assert len(signals) == 1
    assert "하드스탑" in signals[0].reason
    assert "고점대비" in signals[0].reason


# ════════════════════════════════════════════════════════════════
# check_exits() — 부분 익절
# ════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_exits_partial_profit_50pct(mock_deps):
    """부분 익절: +15% 도달 시 50% 매도 (4주 → 2주 매도)."""
    ts, universe, tm, logger = mock_deps
    strategy = OneilPocketPivotStrategy(ts, universe, tm, logger=logger)
    strategy._save_state = MagicMock()
    universe.is_market_timing_ok.return_value = True

    strategy._position_state["005930"] = PPPositionState(
        "PP", 10000, "20250101", 11500, "20", 0, False, "20250105"
    )

    ohlcv = _make_ohlcv(60, close=11000)
    ts.get_recent_daily_ohlcv.return_value = ohlcv
    tm.get_current_kst_time.return_value = datetime(2025, 1, 1, 12, 0, 0)

    # 현재가 11600 → +16% (>= 15%)
    ts.get_current_stock_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {"stck_prpr": "11600"}}
    )

    signals = await strategy.check_exits([
        {"code": "005930", "buy_price": 10000, "qty": 4, "market": "KOSPI"}
    ])

    assert len(signals) == 1
    assert signals[0].action == "SELL"
    assert "부분익절" in signals[0].reason
    assert signals[0].qty == 2  # 4주의 50%
    assert strategy._position_state["005930"].partial_sold is True


@pytest.mark.asyncio
async def test_exits_partial_profit_1_share_full_exit(mock_deps):
    """부분 익절: 잔고 1주뿐이면 전량 익절로 전환."""
    ts, universe, tm, logger = mock_deps
    strategy = OneilPocketPivotStrategy(ts, universe, tm, logger=logger)
    strategy._save_state = MagicMock()
    universe.is_market_timing_ok.return_value = True

    strategy._position_state["005930"] = PPPositionState(
        "PP", 10000, "20250101", 12000, "20", 0, False, "20250105"
    )

    ts.get_recent_daily_ohlcv.return_value = _make_ohlcv(60, close=11000)
    tm.get_current_kst_time.return_value = datetime(2025, 1, 1, 12, 0, 0)

    ts.get_current_stock_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {"stck_prpr": "12000"}}
    )

    signals = await strategy.check_exits([
        {"code": "005930", "buy_price": 10000, "qty": 1, "market": "KOSPI"}
    ])

    assert len(signals) == 1
    assert "전량익절" in signals[0].reason
    assert signals[0].qty == 1


@pytest.mark.asyncio
async def test_exits_no_partial_if_already_done(mock_deps):
    """부분 익절: partial_sold=True이면 재실행 안 함."""
    ts, universe, tm, logger = mock_deps
    strategy = OneilPocketPivotStrategy(ts, universe, tm, logger=logger)
    strategy._save_state = MagicMock()
    universe.is_market_timing_ok.return_value = True

    # partial_sold=True → 이미 50% 익절 완료
    strategy._position_state["005930"] = PPPositionState(
        "PP", 10000, "20250101", 12000, "20", 0, True, "20250105"
    )

    ts.get_recent_daily_ohlcv.return_value = _make_ohlcv(60, close=11000)
    tm.get_current_kst_time.return_value = datetime(2025, 1, 1, 12, 0, 0)

    ts.get_current_stock_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {"stck_prpr": "12000"}}
    )

    signals = await strategy.check_exits([
        {"code": "005930", "buy_price": 10000, "qty": 2, "market": "KOSPI"}
    ])
    assert len(signals) == 0


# ════════════════════════════════════════════════════════════════
# check_exits() — 수익 안착 + 7주 룰
# ════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_exits_holding_start_date_recorded_at_5pct(mock_deps):
    """수익 안착: +5% 도달 시 holding_start_date가 1회만 기록."""
    ts, universe, tm, logger = mock_deps
    strategy = OneilPocketPivotStrategy(ts, universe, tm, logger=logger)
    strategy._save_state = MagicMock()
    universe.is_market_timing_ok.return_value = True

    state = PPPositionState("PP", 10000, "20250101", 10500, "20", 0, False, "")
    strategy._position_state["005930"] = state

    ts.get_recent_daily_ohlcv.return_value = _make_ohlcv(60, close=10000)
    tm.get_current_kst_time.return_value = datetime(2025, 1, 10, 12, 0, 0)

    # 현재가 10600 → +6% (>= 5%)
    ts.get_current_stock_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {"stck_prpr": "10600"}}
    )

    await strategy.check_exits([{"code": "005930", "buy_price": 10000, "market": "KOSPI"}])
    assert state.holding_start_date == "20250110"


@pytest.mark.asyncio
async def test_exits_holding_start_date_not_overwritten(mock_deps):
    """수익 안착: 이미 기록된 holding_start_date는 덮어쓰지 않음."""
    ts, universe, tm, logger = mock_deps
    strategy = OneilPocketPivotStrategy(ts, universe, tm, logger=logger)
    strategy._save_state = MagicMock()
    universe.is_market_timing_ok.return_value = True

    state = PPPositionState("PP", 10000, "20250101", 11000, "20", 0, False, "20250105")
    strategy._position_state["005930"] = state

    ts.get_recent_daily_ohlcv.return_value = _make_ohlcv(60, close=10000)
    tm.get_current_kst_time.return_value = datetime(2025, 1, 15, 12, 0, 0)

    ts.get_current_stock_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {"stck_prpr": "11000"}}
    )

    await strategy.check_exits([{"code": "005930", "buy_price": 10000, "market": "KOSPI"}])
    assert state.holding_start_date == "20250105"  # 변경 없음


@pytest.mark.asyncio
async def test_exits_7week_rule_triggered(mock_deps):
    """7주 룰: 35거래일 경과 & 50MA 이탈 시 전량 청산."""
    ts, universe, tm, logger = mock_deps
    strategy = OneilPocketPivotStrategy(ts, universe, tm, logger=logger)
    strategy._save_state = MagicMock()
    universe.is_market_timing_ok.return_value = True

    # BGU 진입 (PP 손절 로직을 우회하기 위해), gap_day_low=8000 (현재가 이하 아님)
    # peak_price=9000 → 현재가 8800과 차이 -2.2% (하드스탑 -10% 미달)
    state = PPPositionState("BGU", 10000, "20241201", 9000, "", 8000, True, "20250101")
    strategy._position_state["005930"] = state

    # 60일치 데이터: 12월 20일분(holding_start_date 이전) + 1월 30일분 + 2월 10일분
    # holding_start_date("20250101") 이후 날짜가 36개 이상 있어야 함
    dates_before = [f"202412{i:02d}" for i in range(1, 21)]  # 20241201~20241220 (20개)
    dates_jan = [f"202501{i:02d}" for i in range(2, 32)]     # 20250102~20250131 (30개)
    dates_feb = [f"202502{i:02d}" for i in range(1, 11)]     # 20250201~20250210 (10개)
    dates = dates_before + dates_jan + dates_feb              # 총 60개 (>= 50 for MA)
    ohlcv = [{"date": d, "close": 9000, "volume": 50000} for d in dates]
    ts.get_recent_daily_ohlcv.return_value = ohlcv

    tm.get_current_kst_time.return_value = datetime(2025, 2, 10, 12, 0, 0)

    # 현재가 8800 < 50MA(9000) → 이탈 (peak 9000 대비 -2.2%이므로 하드스탑 미발동)
    # gap_day_low=8000이므로 BGU 손절도 미발동 (8800 > 8000)
    ts.get_current_stock_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {"stck_prpr": "8800"}}
    )

    signals = await strategy.check_exits([{"code": "005930", "buy_price": 10000, "market": "KOSPI"}])

    assert len(signals) == 1
    assert "7주룰" in signals[0].reason
    assert "50MA" in signals[0].reason


@pytest.mark.asyncio
async def test_exits_7week_rule_not_triggered_above_ma(mock_deps):
    """7주 룰: 35거래일 경과했어도 50MA 위이면 홀딩 유지."""
    ts, universe, tm, logger = mock_deps
    strategy = OneilPocketPivotStrategy(ts, universe, tm, logger=logger)
    strategy._save_state = MagicMock()
    universe.is_market_timing_ok.return_value = True

    # BGU 진입 (PP MA 손절 우회), peak_price=9800 → 현재가 9500 대비 -3.1% (하드스탑 미발동)
    state = PPPositionState("BGU", 10000, "20241201", 9800, "", 8000, True, "20250101")
    strategy._position_state["005930"] = state

    dates_before = [f"202412{i:02d}" for i in range(1, 21)]
    dates_jan = [f"202501{i:02d}" for i in range(2, 32)]
    dates_feb = [f"202502{i:02d}" for i in range(1, 11)]
    dates = dates_before + dates_jan + dates_feb  # 60개 (>= 50 for MA)
    ohlcv = [{"date": d, "close": 9000, "volume": 50000} for d in dates]
    ts.get_recent_daily_ohlcv.return_value = ohlcv

    tm.get_current_kst_time.return_value = datetime(2025, 2, 10, 12, 0, 0)

    # 현재가 9500 > 50MA(9000) → 안전 (peak 9800 대비 -3.1%이므로 하드스탑 미발동)
    ts.get_current_stock_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {"stck_prpr": "9500"}}
    )

    signals = await strategy.check_exits([{"code": "005930", "buy_price": 10000, "market": "KOSPI"}])
    assert len(signals) == 0


# ════════════════════════════════════════════════════════════════
# check_exits() — 최고가 갱신
# ════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_exits_peak_price_update(mock_deps):
    """최고가 갱신 검증."""
    ts, universe, tm, logger = mock_deps
    strategy = OneilPocketPivotStrategy(ts, universe, tm, logger=logger)
    strategy._save_state = MagicMock()
    universe.is_market_timing_ok.return_value = True

    strategy._position_state["005930"] = PPPositionState(
        "PP", 68000, "20250101", 69000, "20", 0, False, ""
    )

    ts.get_recent_daily_ohlcv.return_value = _make_ohlcv(60, close=68000)
    tm.get_current_kst_time.return_value = datetime(2025, 1, 1, 12, 0, 0)

    # 현재가 71000 > peak 69000 → 갱신
    ts.get_current_stock_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {"stck_prpr": "71000"}}
    )

    await strategy.check_exits([{"code": "005930", "buy_price": 68000, "market": "KOSPI"}])
    assert strategy._position_state["005930"].peak_price == 71000


# ════════════════════════════════════════════════════════════════
# check_exits() — API 실패 / edge case
# ════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_exits_api_failure(mock_deps):
    """현재가 API 실패 시 시그널 없음."""
    ts, universe, tm, logger = mock_deps
    strategy = OneilPocketPivotStrategy(ts, universe, tm, logger=logger)
    strategy._save_state = MagicMock()

    strategy._position_state["005930"] = PPPositionState(
        "PP", 68000, "20250101", 68000, "20", 0, False, ""
    )

    ts.get_current_stock_price.return_value = ResCommonResponse(rt_cd="1", msg1="Fail")

    signals = await strategy.check_exits([{"code": "005930", "buy_price": 68000}])
    assert len(signals) == 0


# ════════════════════════════════════════════════════════════════
# 헬퍼 메서드
# ════════════════════════════════════════════════════════════════

def test_calculate_qty_min_2(mock_deps):
    """_calculate_qty: 최소 2주 보장."""
    ts, universe, tm, logger = mock_deps
    strategy = OneilPocketPivotStrategy(ts, universe, tm, logger=logger)

    # 가격 0 → min_qty=2
    assert strategy._calculate_qty(0) == 2
    assert strategy._calculate_qty(-1) == 2

    # budget=500,000, 가격 300,000 → 1주이지만 min_qty=2로 올림
    assert strategy._calculate_qty(300000) == 2

    # budget=500,000, 가격 100,000 → 5주
    assert strategy._calculate_qty(100000) == 5


def test_load_save_state(mock_deps, tmp_path):
    """상태 파일 저장/로드 검증."""
    ts, universe, tm, logger = mock_deps
    strategy = OneilPocketPivotStrategy(ts, universe, tm, logger=logger)

    test_file = tmp_path / "test_pp_state.json"
    strategy.STATE_FILE = str(test_file)

    strategy._position_state = {
        "005930": PPPositionState("PP", 68000, "20250101", 70000, "20", 0, False, "20250105"),
        "035420": PPPositionState("BGU", 350000, "20250102", 370000, "", 340000, True, "20250103"),
    }
    strategy._save_state()

    assert test_file.exists()

    strategy2 = OneilPocketPivotStrategy(ts, universe, tm, logger=logger)
    strategy2.STATE_FILE = str(test_file)
    strategy2._position_state = {}
    strategy2._load_state()

    assert "005930" in strategy2._position_state
    assert strategy2._position_state["005930"].entry_type == "PP"
    assert strategy2._position_state["005930"].supporting_ma == "20"
    assert strategy2._position_state["005930"].holding_start_date == "20250105"

    assert "035420" in strategy2._position_state
    assert strategy2._position_state["035420"].entry_type == "BGU"
    assert strategy2._position_state["035420"].gap_day_low == 340000
    assert strategy2._position_state["035420"].partial_sold is True


def test_name_property(mock_deps):
    """전략 이름 확인."""
    ts, universe, tm, logger = mock_deps
    strategy = OneilPocketPivotStrategy(ts, universe, tm, logger=logger)
    assert strategy.name == "오닐PP/BGU"

# ════════════════════════════════════════════════════════════════
# 추가된 테스트 케이스
# ════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
@pytest.mark.parametrize("price_output, ohlcv_data, conclusion_resp, expected_log_event", [
    (ResCommonResponse(rt_cd="0", msg1="OK", data={"output": None}), _make_ohlcv(), _cgld_output(), None),
    (_pp_price_output(current="0"), _make_ohlcv(), _cgld_output(), None),
    (_pp_price_output(), _make_ohlcv(count=5), _cgld_output(), None),
    (_pp_price_output(), _make_ohlcv(), ResCommonResponse(rt_cd="1", msg1="Fail"), None),
    (_pp_price_output(), _make_ohlcv(), ResCommonResponse(rt_cd="0", msg1="OK", data={"output": None}), None),
])
async def test_scan_api_failures_and_edge_cases(pp_scan_setup, price_output, ohlcv_data, conclusion_resp, expected_log_event):
    """scan: 다양한 API 실패 및 데이터 엣지 케이스에서 시그널이 없는지 검증."""
    strategy, ts, _, _, logger = pp_scan_setup

    ts.get_current_stock_price.return_value = price_output
    ts.get_recent_daily_ohlcv.return_value = ohlcv_data
    ts.get_stock_conclusion.return_value = conclusion_resp

    signals = await strategy.scan()

    assert len(signals) == 0
    if expected_log_event:
        assert any(expected_log_event in call.args[0].get("event", "") for call in logger.warning.call_args_list)

@pytest.mark.asyncio
async def test_scan_exception_logging(pp_scan_setup):
    """scan: 체결강도 조회 중 예외 발생 시 경고 로그 검증."""
    strategy, ts, _, _, logger = pp_scan_setup
    
    # Setup for success up to conclusion check
    ts.get_current_stock_price.return_value = _pp_price_output()
    ts.get_recent_daily_ohlcv.return_value = _make_ohlcv()
    
    # Make conclusion check raise exception
    ts.get_stock_conclusion.side_effect = Exception("API Error")
    
    await strategy.scan()
    
    # Verify warning log
    assert any("cgld_check_failed" in call.args[0].get("event", "") for call in logger.warning.call_args_list)

@pytest.mark.asyncio
async def test_check_pocket_pivot_edge_cases(pp_scan_setup):
    """_check_pocket_pivot: 데이터 부족, MA 0, 하락일 없음 등 엣지 케이스 검증."""
    strategy, ts, _, _, _ = pp_scan_setup

    # Case 1: OHLCV 데이터 부족 (10일 미만)
    ts.get_recent_daily_ohlcv.return_value = _make_ohlcv(count=5)
    ts.get_current_stock_price.return_value = _pp_price_output()
    ts.get_stock_conclusion.return_value = _cgld_output()
    signals = await strategy.scan()
    assert len(signals) == 0

    # Case 2: MA가 0 이하 (모든 종가가 0인 경우)
    ts.get_recent_daily_ohlcv.return_value = _make_ohlcv(count=60, close=0)
    signals = await strategy.scan()
    assert len(signals) == 0

    # Case 3: 하락일이 없는 경우 (down_day_volumes가 비어 max() 에러 대신 통과)
    ts.get_recent_daily_ohlcv.return_value = _make_ohlcv(count=60, close=68000, open_=67000)
    ts.get_current_stock_price.return_value = _pp_price_output()
    ts.get_stock_conclusion.return_value = _cgld_output()
    signals = await strategy.scan()
    assert len(signals) == 1

@pytest.mark.asyncio
async def test_check_bgu_edge_cases(bgu_scan_setup):
    """_check_bgu: 50일 거래량 데이터 부족 시나리오 검증."""
    strategy, ts, _, _, _ = bgu_scan_setup

    ts.get_recent_daily_ohlcv.return_value = _make_ohlcv(count=15)
    ts.get_current_stock_price.return_value = _bgu_price_output()
    ts.get_stock_conclusion.return_value = _cgld_output()

    signals = await strategy.scan()
    assert len(signals) == 0

@pytest.mark.parametrize("pg_buy, trade_value, market_cap, expected", [
    (0, 1000, 10000, False),
    (-100, 1000, 10000, False),
    (100, 0, 10000, True),
    (100, 10000, 0, True),
    (100, 10000, 10000, True),
    (1000, 1000, 100000, True),
])
def test_check_smart_money_filters(mock_deps, pg_buy, trade_value, market_cap, expected):
    """_check_smart_money: 다양한 필터링 조건 검증."""
    ts, universe, tm, logger = mock_deps
    strategy = OneilPocketPivotStrategy(ts, universe, tm, logger=logger)
    result = strategy._check_smart_money(100, pg_buy, trade_value, market_cap)
    assert result is expected

@pytest.mark.asyncio
async def test_check_exits_edge_cases(mock_deps):
    """check_exits: holdings 데이터 누락, API 실패 등 엣지 케이스 검증."""
    ts, universe, tm, logger = mock_deps
    strategy = OneilPocketPivotStrategy(ts, universe, tm, logger=logger)
    universe.is_market_timing_ok.return_value = True

    # Case 1: holdings에 code 또는 buy_price 누락
    signals = await strategy.check_exits([{"name": "Incomplete"}])
    assert len(signals) == 0
    ts.get_current_stock_price.assert_not_called()

    # Case 2: state가 없어도 새로 생성해서 처리
    strategy._position_state = {}
    ts.get_current_stock_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {"stck_prpr": "9000"}}
    )
    signals = await strategy.check_exits([{"code": "005930", "buy_price": 10000, "market": "KOSPI"}])
    assert len(signals) == 1
    assert "하드스탑" in signals[0].reason

    # Case 3: get_current_stock_price 응답에 output이 없음
    ts.get_current_stock_price.return_value = ResCommonResponse(rt_cd="0", msg1="OK", data={"output": None})
    signals = await strategy.check_exits([{"code": "005930", "buy_price": 10000}])
    assert len(signals) == 0

    # Case 4: 현재가가 0
    ts.get_current_stock_price.return_value = ResCommonResponse(rt_cd="0", msg1="OK", data={"output": {"stck_prpr": "0"}})
    signals = await strategy.check_exits([{"code": "005930", "buy_price": 10000}])
    assert len(signals) == 0

def test_check_pp_stop_loss_no_data(mock_deps):
    """_check_pp_stop_loss: OHLCV 데이터 부족 또는 MA 정보 없을 때 None 반환 검증."""
    ts, universe, tm, logger = mock_deps
    strategy = OneilPocketPivotStrategy(ts, universe, tm, logger=logger)
    state = PPPositionState("PP", 10000, "20250101", 10000, "20", 0, False, "")

    assert strategy._check_pp_stop_loss(state, 9000, None) is None
    state.supporting_ma = ""
    assert strategy._check_pp_stop_loss(state, 9000, _make_ohlcv(60)) is None
    state.supporting_ma = "20"
    assert strategy._check_pp_stop_loss(state, 9000, _make_ohlcv(10)) is None

def test_check_7week_rule_not_triggered(mock_deps):
    """_check_7week_hold: 다양한 미발동 조건 검증."""
    ts, universe, tm, logger = mock_deps
    strategy = OneilPocketPivotStrategy(ts, universe, tm, logger=logger)
    state = PPPositionState("PP", 10000, "20250101", 11000, "50", 0, False, "")

    assert strategy._check_7week_hold(state, 10500, _make_ohlcv(60)) is None
    state.holding_start_date = "20250110"
    assert strategy._check_7week_hold(state, 10500, None) is None
    dates = [f"202501{i:02d}" for i in range(11, 32)] + [f"202502{i:02d}" for i in range(1, 10)]
    ohlcv_short = _make_ohlcv_with_dates(dates)
    assert strategy._check_7week_hold(state, 10500, ohlcv_short) is None
    ohlcv_insufficient_ma = _make_ohlcv(40)
    assert strategy._check_7week_hold(state, 10500, ohlcv_insufficient_ma) is None

def test_load_save_state_exceptions(mock_deps, tmp_path):
    """_load_state, _save_state: 파일 입출력 예외 처리 검증."""
    ts, universe, tm, logger = mock_deps
    strategy = OneilPocketPivotStrategy(ts, universe, tm, logger=logger)
    
    test_file = tmp_path / "corrupted.json"
    test_file.write_text("{invalid json")
    strategy.STATE_FILE = str(test_file)
    strategy._load_state()
    assert strategy._position_state == {}

    strategy.STATE_FILE = "/unwritable/path/state.json"
    with patch("os.makedirs", side_effect=OSError("Permission denied")):
        strategy._save_state()
