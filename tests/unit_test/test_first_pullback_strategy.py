# tests/unit_test/test_first_pullback_strategy.py
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from datetime import datetime
from common.types import ResCommonResponse, TradeSignal
from strategies.first_pullback_strategy import FirstPullbackStrategy
from strategies.first_pullback_types import FirstPullbackConfig, FPPositionState
from strategies.oneil_common_types import OSBWatchlistItem


# ── 헬퍼 함수 ──────────────────────────────────────────────────

def _make_ohlcv(count=30, close=10000, open_=9800, high=10200, low=9700, volume=50000):
    """기본 OHLCV 데이터 생성."""
    base_date = 20250101
    return [
        {"date": str(base_date + i), "open": open_, "close": close,
         "high": high, "low": low, "volume": volume}
        for i in range(count)
    ]


def _make_surge_ohlcv(surge_type="upper_limit"):
    """급등 이력이 포함된 OHLCV 생성 (30일).

    surge_type:
      "upper_limit": 15일차에 상한가(+29%) 발생
      "rapid_surge": 10~17일차에 걸쳐 +30% 급등
      "none": 급등 없음
    """
    base = 10000
    data = []
    for i in range(30):
        if surge_type == "upper_limit" and i == 15:
            # 상한가: 전일 종가 대비 +30%
            prev_close = base
            c = int(base * 1.30)
            data.append({"date": str(20250101 + i), "open": base + 100, "close": c,
                         "high": c + 200, "low": base, "volume": 200000})
            base = c
        elif surge_type == "rapid_surge" and 10 <= i <= 17:
            # 8일간 급등 (일 약 4.3%) → 총 약 +40%로 30% 기준 충분히 초과
            c = int(base * 1.043)
            data.append({"date": str(20250101 + i), "open": base, "close": c,
                         "high": c + 100, "low": base - 100, "volume": 150000})
            base = c
        else:
            # 평탄한 날 (소폭 상승으로 20MA 우상향 유지)
            c = int(base * 1.002)
            data.append({"date": str(20250101 + i), "open": base, "close": c,
                         "high": c + 100, "low": base - 100, "volume": 30000})
            base = c
    return data


def _make_uptrend_ohlcv(count=30, start_close=10000, daily_rise=20):
    """20MA가 5일 연속 우상향하는 OHLCV 생성."""
    data = []
    c = start_close
    for i in range(count):
        c = c + daily_rise
        data.append({"date": str(20250101 + i), "open": c - 10, "close": c,
                     "high": c + 50, "low": c - 50, "volume": 50000})
    return data


def _fp_price_output(
    current="10500", today_open="10300", today_low="10100",
    prev_close="10400",
):
    """현재가 API 응답 생성."""
    return ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {
            "stck_prpr": current, "stck_oprc": today_open,
            "stck_lwpr": today_low, "stck_prdy_clpr": prev_close,
        }}
    )


def _cgld_output(strength="105.0"):
    return ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": [{"tday_rltv": strength}]}
    )


# ── 공통 Fixture ──────────────────────────────────────────────

@pytest.fixture
def mock_deps():
    sqs = MagicMock()
    universe = MagicMock()
    tm = MagicMock()
    logger = MagicMock()

    sqs.get_current_price = AsyncMock()
    sqs.get_stock_conclusion = AsyncMock()
    sqs.get_recent_daily_ohlcv = AsyncMock()
    universe.get_watchlist = AsyncMock()
    universe.is_market_timing_ok = AsyncMock()

    return sqs, universe, tm, logger


@pytest.fixture
def watchlist_item():
    return OSBWatchlistItem(
        code="005930", name="삼성전자", market="KOSPI",
        high_20d=12000, ma_20d=10500, ma_50d=10000,
        avg_vol_20d=100000, bb_width_min_20d=500, prev_bb_width=600,
        w52_hgpr=15000, avg_trading_value_5d=50_000_000_000,
        market_cap=100_000_000_000,
    )


@pytest.fixture
def fp_scan_setup(mock_deps, watchlist_item):
    """scan() 테스트 공통 셋업: 모든 조건 통과하도록 구성."""
    sqs, universe, tm, logger = mock_deps
    strategy = FirstPullbackStrategy(sqs, universe, tm, logger=logger)
    strategy._position_state = {}
    strategy._save_state = MagicMock()

    universe.get_watchlist.return_value = {"005930": watchlist_item}
    universe.is_market_timing_ok.return_value = True

    # 장중 50% 경과
    tm.get_current_kst_time.return_value = datetime(2025, 1, 1, 12, 0, 0)
    tm.get_market_open_time.return_value = datetime(2025, 1, 1, 9, 0, 0)
    tm.get_market_close_time.return_value = datetime(2025, 1, 1, 15, 30, 0)

    # OHLCV: 상한가 급등 + 우상향 MA (상한가 후 소폭 상승 유지)
    ohlcv = _make_surge_ohlcv("upper_limit")
    sqs.get_recent_daily_ohlcv.return_value = ResCommonResponse(rt_cd="0", msg1="OK", data=ohlcv)

    # 급등일 거래량 200000 대비 최근 3일 거래량 30000 → 15% (< 50%)
    # 20MA는 ohlcv의 최근 20일 close 평균
    # 현재가: 20MA 근처에서 양봉 전환
    closes = [r["close"] for r in ohlcv]
    ma_20 = sum(closes[-20:]) / 20
    today_low = int(ma_20 * 1.01)  # MA +1% (범위 내)
    current = int(ma_20 * 1.02)    # 시가보다 높음 (양봉)
    today_open = int(ma_20 * 1.005)

    sqs.get_current_price.return_value = _fp_price_output(
        current=str(current), today_open=str(today_open),
        today_low=str(today_low), prev_close=str(int(ma_20 * 0.99)),
    )
    sqs.get_stock_conclusion.return_value = _cgld_output("105.0")

    return strategy, sqs, universe, tm, logger


# ════════════════════════════════════════════════════════════════
# Phase 1: Setup — 급등 이력
# ════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_scan_surge_upper_limit_passes(fp_scan_setup):
    """Phase 1: 상한가(+29%) 이력 있는 종목 → 통과."""
    strategy, sqs, _, _, _ = fp_scan_setup
    signals = await strategy.scan()
    assert len(signals) == 1
    assert signals[0].action == "BUY"
    assert "첫눌림목" in signals[0].reason


@pytest.mark.asyncio
async def test_scan_surge_rapid_surge_passes(fp_scan_setup):
    """Phase 1: 5~10일 +30% 급등 이력 → 통과."""
    strategy, sqs, _, _, _ = fp_scan_setup
    ohlcv = _make_surge_ohlcv("rapid_surge")
    sqs.get_recent_daily_ohlcv.return_value = ResCommonResponse(rt_cd="0", msg1="OK", data=ohlcv)

    # 현재가를 급등 후 OHLCV의 20MA 범위에 맞게 재설정
    closes = [r["close"] for r in ohlcv]
    ma_20 = sum(closes[-20:]) / 20
    today_low = int(ma_20 * 1.01)
    current = int(ma_20 * 1.02)
    today_open = int(ma_20 * 1.005)
    sqs.get_current_price.return_value = _fp_price_output(
        current=str(current), today_open=str(today_open),
        today_low=str(today_low), prev_close=str(int(ma_20 * 0.99)),
    )

    signals = await strategy.scan()
    assert len(signals) == 1
    assert signals[0].action == "BUY"


@pytest.mark.asyncio
async def test_scan_no_surge_rejected(fp_scan_setup):
    """Phase 1: 급등 이력 없음 → 거부."""
    strategy, sqs, _, _, _ = fp_scan_setup
    ohlcv = _make_surge_ohlcv("none")
    sqs.get_recent_daily_ohlcv.return_value = ResCommonResponse(rt_cd="0", msg1="OK", data=ohlcv)
    signals = await strategy.scan()
    assert len(signals) == 0


# ════════════════════════════════════════════════════════════════
# Phase 1: Setup — MA 우상향
# ════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_scan_ma_not_uptrending_rejected(fp_scan_setup):
    """Phase 1: 20MA 우상향이 아닌 경우 → 거부."""
    strategy, sqs, _, _, _ = fp_scan_setup

    # 하락 추세 OHLCV: 매일 close가 감소 → MA 하향
    declining_ohlcv = []
    base = 15000
    for i in range(30):
        # 15일차에 상한가 넣어서 surge는 통과하게
        if i == 15:
            c = int(base * 1.30)
            declining_ohlcv.append({"date": str(20250101 + i), "open": base, "close": c,
                                    "high": c, "low": base, "volume": 200000})
            base = c
        else:
            c = int(base * 0.99)  # 매일 -1%씩 하락
            declining_ohlcv.append({"date": str(20250101 + i), "open": base, "close": c,
                                    "high": base, "low": c, "volume": 30000})
            base = c

    sqs.get_recent_daily_ohlcv.return_value = ResCommonResponse(rt_cd="0", msg1="OK", data=declining_ohlcv)
    signals = await strategy.scan()
    assert len(signals) == 0


# ════════════════════════════════════════════════════════════════
# Phase 2: Pullback — 이격도
# ════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_scan_pullback_too_deep_rejected(fp_scan_setup):
    """Phase 2: 장중 저가가 20MA -1% 이하 → 거부."""
    strategy, sqs, _, _, _ = fp_scan_setup
    ohlcv = sqs.get_recent_daily_ohlcv.return_value.data
    closes = [r["close"] for r in ohlcv]
    ma_20 = sum(closes[-20:]) / 20

    # 저가가 MA -3% (범위 밖)
    too_low = int(ma_20 * 0.97)
    sqs.get_current_price.return_value = _fp_price_output(
        current=str(int(ma_20 * 1.02)), today_open=str(int(ma_20)),
        today_low=str(too_low), prev_close=str(int(ma_20 * 0.99)),
    )
    signals = await strategy.scan()
    assert len(signals) == 0


@pytest.mark.asyncio
async def test_scan_pullback_too_high_rejected(fp_scan_setup):
    """Phase 2: 장중 저가가 20MA +3% 초과 → 거부 (아직 조정이 충분하지 않음)."""
    strategy, sqs, _, _, _ = fp_scan_setup
    ohlcv = sqs.get_recent_daily_ohlcv.return_value.data
    closes = [r["close"] for r in ohlcv]
    ma_20 = sum(closes[-20:]) / 20

    too_high = int(ma_20 * 1.05)
    sqs.get_current_price.return_value = _fp_price_output(
        current=str(int(ma_20 * 1.06)), today_open=str(int(ma_20 * 1.04)),
        today_low=str(too_high), prev_close=str(int(ma_20 * 1.04)),
    )
    signals = await strategy.scan()
    assert len(signals) == 0


# ════════════════════════════════════════════════════════════════
# Phase 2: Pullback — 거래량 고갈
# ════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_scan_volume_not_dry_rejected(fp_scan_setup):
    """Phase 2: 최근 3일 거래량이 급등일의 50% 초과 → 거부."""
    strategy, sqs, _, _, _ = fp_scan_setup

    # 거래량이 큰 OHLCV: 급등일 200000이고 최근도 150000 (75% > 50%)
    ohlcv = _make_surge_ohlcv("upper_limit")
    # 마지막 3일 거래량을 높게 설정
    for i in range(-3, 0):
        ohlcv[i]["volume"] = 150000

    sqs.get_recent_daily_ohlcv.return_value = ResCommonResponse(rt_cd="0", msg1="OK", data=ohlcv)
    signals = await strategy.scan()
    assert len(signals) == 0


# ════════════════════════════════════════════════════════════════
# Phase 3: Trigger — 양봉 전환 / 체결강도
# ════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_scan_no_bullish_reversal_rejected(fp_scan_setup):
    """Phase 3: 현재가 < 시가 & 전일고가 → 거부."""
    strategy, sqs, _, _, _ = fp_scan_setup
    ohlcv = sqs.get_recent_daily_ohlcv.return_value.data
    closes = [r["close"] for r in ohlcv]
    ma_20 = sum(closes[-20:]) / 20

    # 현재가 < 시가 (음봉), 전일 고가보다도 낮음
    current = int(ma_20 * 1.00)
    today_open = int(ma_20 * 1.02)
    prev_high = ohlcv[-1]["high"]
    sqs.get_current_price.return_value = _fp_price_output(
        current=str(min(current, prev_high - 100)),
        today_open=str(today_open),
        today_low=str(int(ma_20 * 1.00)),
        prev_close=str(int(ma_20 * 0.99)),
    )
    signals = await strategy.scan()
    assert len(signals) == 0


@pytest.mark.asyncio
async def test_scan_execution_strength_low_rejected(fp_scan_setup):
    """Phase 3: 체결강도 < 100% → 거부."""
    strategy, sqs, _, _, _ = fp_scan_setup
    sqs.get_stock_conclusion.return_value = _cgld_output("85.0")
    signals = await strategy.scan()
    assert len(signals) == 0


@pytest.mark.asyncio
async def test_scan_execution_strength_exact_100_passes(fp_scan_setup):
    """Phase 3: 체결강도 == 100% → 통과."""
    strategy, sqs, _, _, _ = fp_scan_setup
    sqs.get_stock_conclusion.return_value = _cgld_output("100.0")
    signals = await strategy.scan()
    assert len(signals) == 1


# ════════════════════════════════════════════════════════════════
# scan() — 공통 edge case
# ════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_scan_empty_watchlist(fp_scan_setup):
    """빈 워치리스트 → 빈 리스트."""
    strategy, _, universe, _, _ = fp_scan_setup
    universe.get_watchlist.return_value = {}
    assert await strategy.scan() == []


@pytest.mark.asyncio
async def test_scan_market_not_ready(fp_scan_setup):
    """장 시작 전(progress <= 0) → 빈 리스트."""
    strategy, _, _, tm, _ = fp_scan_setup
    tm.get_current_kst_time.return_value = datetime(2025, 1, 1, 9, 0, 0)
    tm.get_market_open_time.return_value = datetime(2025, 1, 1, 9, 0, 0)
    assert await strategy.scan() == []


@pytest.mark.asyncio
async def test_scan_skip_existing_position(fp_scan_setup):
    """이미 보유 중인 종목 스캔 제외."""
    strategy, sqs, _, _, _ = fp_scan_setup
    strategy._position_state["005930"] = FPPositionState(10000, "20250101", 10500, 12000, False)
    sqs.get_current_price.reset_mock()
    sqs.get_recent_daily_ohlcv.reset_mock()
    assert await strategy.scan() == []
    sqs.get_recent_daily_ohlcv.assert_not_called()


@pytest.mark.asyncio
async def test_scan_bad_market_timing(fp_scan_setup):
    """마켓 타이밍 불량 시 스캔 제외."""
    strategy, sqs, universe, _, _ = fp_scan_setup
    universe.is_market_timing_ok.return_value = False
    assert await strategy.scan() == []
    sqs.get_recent_daily_ohlcv.assert_not_called()


@pytest.mark.asyncio
async def test_scan_api_failure(fp_scan_setup):
    """OHLCV API 실패 시 시그널 없음."""
    strategy, sqs, _, _, _ = fp_scan_setup
    sqs.get_recent_daily_ohlcv.return_value = ResCommonResponse(rt_cd="1", msg1="Fail")
    assert await strategy.scan() == []


@pytest.mark.asyncio
async def test_scan_exception_handling_in_loop(fp_scan_setup):
    """개별 종목 예외 발생 시 로그 남기고 계속 진행."""
    strategy, sqs, _, _, logger = fp_scan_setup
    sqs.get_recent_daily_ohlcv.side_effect = Exception("API Timeout")
    signals = await strategy.scan()
    assert len(signals) == 0
    logger.error.assert_called()


@pytest.mark.asyncio
async def test_scan_cgld_exception_returns_none(fp_scan_setup):
    """체결강도 조회 예외 시 시그널 없음 + 경고 로그."""
    strategy, sqs, _, _, logger = fp_scan_setup
    sqs.get_stock_conclusion.side_effect = Exception("API Error")
    signals = await strategy.scan()
    assert len(signals) == 0
    assert any("cgld_check_failed" in str(call) for call in logger.warning.call_args_list)


# ════════════════════════════════════════════════════════════════
# check_exits() — 손절
# ════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_exits_stop_loss_below_ma(mock_deps):
    """손절: 현재가 < 20MA * 0.98 → 잔량 전체 매도."""
    sqs, universe, tm, logger = mock_deps
    strategy = FirstPullbackStrategy(sqs, universe, tm, logger=logger)
    strategy._save_state = MagicMock()

    strategy._position_state["005930"] = FPPositionState(10000, "20250101", 10500, 12000, False)

    # OHLCV: 모든 close가 10000 → 20MA = 10000, threshold = 9800
    ohlcv = _make_ohlcv(20, close=10000)
    sqs.get_recent_daily_ohlcv.return_value = ResCommonResponse(rt_cd="0", msg1="OK", data=ohlcv)

    # 현재가 9700 < 9800 → 손절
    sqs.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {"stck_prpr": "9700"}}
    )

    signals = await strategy.check_exits([
        {"code": "005930", "buy_price": 10000, "qty": 4, "market": "KOSPI"}
    ])

    assert len(signals) == 1
    assert signals[0].action == "SELL"
    assert "손절" in signals[0].reason
    assert "20MA" in signals[0].reason
    assert signals[0].qty == 4  # 잔량 전체
    assert "005930" not in strategy._position_state


@pytest.mark.asyncio
async def test_exits_no_stop_above_ma(mock_deps):
    """현재가가 20MA 위에 있으면 손절 안 함."""
    sqs, universe, tm, logger = mock_deps
    strategy = FirstPullbackStrategy(sqs, universe, tm, logger=logger)
    strategy._save_state = MagicMock()

    strategy._position_state["005930"] = FPPositionState(10000, "20250101", 10500, 12000, False)

    ohlcv = _make_ohlcv(20, close=10000)
    sqs.get_recent_daily_ohlcv.return_value = ResCommonResponse(rt_cd="0", msg1="OK", data=ohlcv)

    # 현재가 10200 > 9800 → 안전
    sqs.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {"stck_prpr": "10200"}}
    )

    signals = await strategy.check_exits([
        {"code": "005930", "buy_price": 10000, "qty": 4, "market": "KOSPI"}
    ])
    assert len(signals) == 0


# ════════════════════════════════════════════════════════════════
# check_exits() — 부분 익절
# ════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_exits_partial_profit_50pct(mock_deps):
    """부분 익절: +10% 이상 도달 시 50% 매도 (4주 → 2주 매도)."""
    sqs, universe, tm, logger = mock_deps
    strategy = FirstPullbackStrategy(sqs, universe, tm, logger=logger)
    strategy._save_state = MagicMock()

    strategy._position_state["005930"] = FPPositionState(10000, "20250101", 11200, 12000, False)

    ohlcv = _make_ohlcv(20, close=11000)
    sqs.get_recent_daily_ohlcv.return_value = ResCommonResponse(rt_cd="0", msg1="OK", data=ohlcv)

    # 현재가 11200 → +12% (>= 10%)
    sqs.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {"stck_prpr": "11200"}}
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
    sqs, universe, tm, logger = mock_deps
    strategy = FirstPullbackStrategy(sqs, universe, tm, logger=logger)
    strategy._save_state = MagicMock()

    strategy._position_state["005930"] = FPPositionState(10000, "20250101", 11500, 12000, False)

    ohlcv = _make_ohlcv(20, close=11000)
    sqs.get_recent_daily_ohlcv.return_value = ResCommonResponse(rt_cd="0", msg1="OK", data=ohlcv)

    sqs.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {"stck_prpr": "11500"}}
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
    sqs, universe, tm, logger = mock_deps
    strategy = FirstPullbackStrategy(sqs, universe, tm, logger=logger)
    strategy._save_state = MagicMock()

    # partial_sold=True → 이미 50% 익절 완료
    strategy._position_state["005930"] = FPPositionState(10000, "20250101", 12000, 12000, True)

    ohlcv = _make_ohlcv(20, close=11000)
    sqs.get_recent_daily_ohlcv.return_value = ResCommonResponse(rt_cd="0", msg1="OK", data=ohlcv)

    sqs.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {"stck_prpr": "12000"}}
    )

    signals = await strategy.check_exits([
        {"code": "005930", "buy_price": 10000, "qty": 2, "market": "KOSPI"}
    ])
    assert len(signals) == 0


@pytest.mark.asyncio
async def test_exits_stop_loss_after_partial_sold(mock_deps):
    """부분 익절 후 남은 잔량이 20MA 이탈 시 전체 매도."""
    sqs, universe, tm, logger = mock_deps
    strategy = FirstPullbackStrategy(sqs, universe, tm, logger=logger)
    strategy._save_state = MagicMock()

    # partial_sold=True, 잔량 2주
    strategy._position_state["005930"] = FPPositionState(10000, "20250101", 11500, 12000, True)

    ohlcv = _make_ohlcv(20, close=10000)
    sqs.get_recent_daily_ohlcv.return_value = ResCommonResponse(rt_cd="0", msg1="OK", data=ohlcv)

    # 현재가 9700 < 20MA(10000) * 0.98 = 9800 → 손절
    sqs.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {"stck_prpr": "9700"}}
    )

    signals = await strategy.check_exits([
        {"code": "005930", "buy_price": 10000, "qty": 2, "market": "KOSPI"}
    ])

    assert len(signals) == 1
    assert "손절" in signals[0].reason
    assert signals[0].qty == 2  # 남은 잔량 전체
    assert "005930" not in strategy._position_state


# ════════════════════════════════════════════════════════════════
# check_exits() — 최고가 갱신
# ════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_exits_peak_price_update(mock_deps):
    """최고가 갱신 검증."""
    sqs, universe, tm, logger = mock_deps
    strategy = FirstPullbackStrategy(sqs, universe, tm, logger=logger)
    strategy._save_state = MagicMock()

    strategy._position_state["005930"] = FPPositionState(10000, "20250101", 10500, 12000, False)

    ohlcv = _make_ohlcv(20, close=10000)
    sqs.get_recent_daily_ohlcv.return_value = ResCommonResponse(rt_cd="0", msg1="OK", data=ohlcv)

    # 현재가 10800 > peak 10500 → 갱신
    sqs.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {"stck_prpr": "10800"}}
    )

    await strategy.check_exits([{"code": "005930", "buy_price": 10000, "qty": 2, "market": "KOSPI"}])
    assert strategy._position_state["005930"].peak_price == 10800


# ════════════════════════════════════════════════════════════════
# check_exits() — edge case
# ════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_exits_missing_code_or_price(mock_deps):
    """holdings에 code/buy_price 누락 시 무시."""
    sqs, universe, tm, logger = mock_deps
    strategy = FirstPullbackStrategy(sqs, universe, tm, logger=logger)

    signals = await strategy.check_exits([{"name": "Incomplete"}])
    assert len(signals) == 0
    sqs.get_current_price.assert_not_called()


@pytest.mark.asyncio
async def test_exits_api_failure(mock_deps):
    """현재가 API 실패 시 시그널 없음."""
    sqs, universe, tm, logger = mock_deps
    strategy = FirstPullbackStrategy(sqs, universe, tm, logger=logger)
    strategy._save_state = MagicMock()

    strategy._position_state["005930"] = FPPositionState(10000, "20250101", 10500, 12000, False)
    sqs.get_current_price.return_value = ResCommonResponse(rt_cd="1", msg1="Fail")

    signals = await strategy.check_exits([{"code": "005930", "buy_price": 10000}])
    assert len(signals) == 0


@pytest.mark.asyncio
async def test_exits_no_state_creates_default(mock_deps):
    """포지션 상태 없으면 기본값으로 생성하여 처리."""
    sqs, universe, tm, logger = mock_deps
    strategy = FirstPullbackStrategy(sqs, universe, tm, logger=logger)
    strategy._save_state = MagicMock()
    strategy._position_state = {}

    ohlcv = _make_ohlcv(20, close=10000)
    sqs.get_recent_daily_ohlcv.return_value = ResCommonResponse(rt_cd="0", msg1="OK", data=ohlcv)

    # 현재가 9700 < threshold 9800 → 손절
    sqs.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {"stck_prpr": "9700"}}
    )

    signals = await strategy.check_exits([{"code": "005930", "buy_price": 10000, "qty": 2}])
    assert len(signals) == 1
    assert "손절" in signals[0].reason


# ════════════════════════════════════════════════════════════════
# 내부 메서드 단위 테스트
# ════════════════════════════════════════════════════════════════

def test_check_surge_history_upper_limit(mock_deps):
    """_check_surge_history: 상한가 감지."""
    sqs, universe, tm, logger = mock_deps
    strategy = FirstPullbackStrategy(sqs, universe, tm, logger=logger)
    ohlcv = _make_surge_ohlcv("upper_limit")
    result = strategy._check_surge_history(ohlcv)
    assert result is not None
    surge_vol, surge_high = result
    assert surge_vol == 200000
    assert surge_high > 0


def test_check_surge_history_rapid(mock_deps):
    """_check_surge_history: 단기 급등 감지."""
    sqs, universe, tm, logger = mock_deps
    strategy = FirstPullbackStrategy(sqs, universe, tm, logger=logger)
    ohlcv = _make_surge_ohlcv("rapid_surge")
    result = strategy._check_surge_history(ohlcv)
    assert result is not None


def test_check_surge_history_none(mock_deps):
    """_check_surge_history: 급등 없음."""
    sqs, universe, tm, logger = mock_deps
    strategy = FirstPullbackStrategy(sqs, universe, tm, logger=logger)
    ohlcv = _make_surge_ohlcv("none")
    result = strategy._check_surge_history(ohlcv)
    assert result is None


def test_check_ma_uptrend_rising(mock_deps):
    """_check_ma_uptrend: 꾸준히 상승하는 MA → True."""
    sqs, universe, tm, logger = mock_deps
    strategy = FirstPullbackStrategy(sqs, universe, tm, logger=logger)
    ohlcv = _make_uptrend_ohlcv(30, start_close=10000, daily_rise=20)
    assert strategy._check_ma_uptrend(ohlcv) is True


def test_check_ma_uptrend_flat(mock_deps):
    """_check_ma_uptrend: 평탄한 MA → False."""
    sqs, universe, tm, logger = mock_deps
    strategy = FirstPullbackStrategy(sqs, universe, tm, logger=logger)
    ohlcv = _make_ohlcv(30, close=10000)  # 모든 close 동일
    assert strategy._check_ma_uptrend(ohlcv) is False


def test_check_ma_uptrend_insufficient_data(mock_deps):
    """_check_ma_uptrend: 데이터 부족 → False."""
    sqs, universe, tm, logger = mock_deps
    strategy = FirstPullbackStrategy(sqs, universe, tm, logger=logger)
    ohlcv = _make_ohlcv(10)  # 10일 < 25일 필요
    assert strategy._check_ma_uptrend(ohlcv) is False


def test_check_pullback_to_ma(mock_deps):
    """_check_pullback_to_ma: 범위 내/외 검증."""
    sqs, universe, tm, logger = mock_deps
    strategy = FirstPullbackStrategy(sqs, universe, tm, logger=logger)

    # MA = 10000, 범위: 9900 ~ 10300
    assert strategy._check_pullback_to_ma(10100, 10000) is True   # +1%
    assert strategy._check_pullback_to_ma(9900, 10000) is True    # -1% (경계)
    assert strategy._check_pullback_to_ma(10300, 10000) is True   # +3% (경계)
    assert strategy._check_pullback_to_ma(9800, 10000) is False   # -2% (범위 밖)
    assert strategy._check_pullback_to_ma(10400, 10000) is False  # +4% (범위 밖)


def test_check_volume_dryup(mock_deps):
    """_check_volume_dryup: 거래량 고갈 판단."""
    sqs, universe, tm, logger = mock_deps
    strategy = FirstPullbackStrategy(sqs, universe, tm, logger=logger)

    # 급등일 거래량 200000, 최근 3일 평균 80000 (40% < 50%) → True
    ohlcv = [{"volume": 80000}, {"volume": 80000}, {"volume": 80000}]
    assert strategy._check_volume_dryup(ohlcv, 200000) is True

    # 최근 3일 평균 120000 (60% > 50%) → False
    ohlcv = [{"volume": 120000}, {"volume": 120000}, {"volume": 120000}]
    assert strategy._check_volume_dryup(ohlcv, 200000) is False

    # surge_volume 0 → False
    assert strategy._check_volume_dryup(ohlcv, 0) is False


def test_check_bullish_reversal(mock_deps):
    """_check_bullish_reversal: 양봉/전일고가 돌파 판단."""
    sqs, universe, tm, logger = mock_deps
    strategy = FirstPullbackStrategy(sqs, universe, tm, logger=logger)

    # 양봉: current > open
    assert strategy._check_bullish_reversal(10500, 10300, 10400) is True
    # 전일고가 돌파: current > prev_high
    assert strategy._check_bullish_reversal(10500, 10600, 10400) is True
    # 둘 다 실패
    assert strategy._check_bullish_reversal(10200, 10300, 10400) is False


# ════════════════════════════════════════════════════════════════
# 헬퍼 메서드
# ════════════════════════════════════════════════════════════════

def test_calculate_qty_min_2(mock_deps):
    """_calculate_qty: 최소 2주 보장."""
    sqs, universe, tm, logger = mock_deps
    strategy = FirstPullbackStrategy(sqs, universe, tm, logger=logger)

    assert strategy._calculate_qty(0) == 2
    assert strategy._calculate_qty(-1) == 2
    # budget=500,000, 가격 300,000 → 1주이지만 min_qty=2로 올림
    assert strategy._calculate_qty(300000) == 2
    # budget=500,000, 가격 100,000 → 5주
    assert strategy._calculate_qty(100000) == 5


def test_load_save_state(mock_deps, tmp_path):
    """상태 파일 저장/로드 검증."""
    sqs, universe, tm, logger = mock_deps
    strategy = FirstPullbackStrategy(sqs, universe, tm, logger=logger)

    test_file = tmp_path / "test_fp_state.json"
    strategy.STATE_FILE = str(test_file)

    strategy._position_state = {
        "005930": FPPositionState(10000, "20250101", 10500, 12000, False),
        "035420": FPPositionState(30000, "20250102", 33000, 35000, True),
    }
    strategy._save_state()

    assert test_file.exists()

    strategy2 = FirstPullbackStrategy(sqs, universe, tm, logger=logger)
    strategy2.STATE_FILE = str(test_file)
    strategy2._position_state = {}
    strategy2._load_state()

    assert "005930" in strategy2._position_state
    assert strategy2._position_state["005930"].entry_price == 10000
    assert strategy2._position_state["005930"].partial_sold is False

    assert "035420" in strategy2._position_state
    assert strategy2._position_state["035420"].partial_sold is True
    assert strategy2._position_state["035420"].surge_day_high == 35000


def test_load_state_corrupted(mock_deps, tmp_path):
    """손상된 상태 파일 로드 시 빈 상태 유지."""
    sqs, universe, tm, logger = mock_deps
    strategy = FirstPullbackStrategy(sqs, universe, tm, logger=logger)

    test_file = tmp_path / "corrupted.json"
    test_file.write_text("{invalid json")
    strategy.STATE_FILE = str(test_file)
    strategy._load_state()
    assert strategy._position_state == {}


def test_save_state_permission_error(mock_deps):
    """저장 실패 시 예외가 전파되지 않음."""
    sqs, universe, tm, logger = mock_deps
    strategy = FirstPullbackStrategy(sqs, universe, tm, logger=logger)

    strategy.STATE_FILE = "/unwritable/path/state.json"
    with patch("os.makedirs", side_effect=OSError("Permission denied")):
        strategy._save_state()  # 예외 발생 안 함


def test_name_property(mock_deps):
    """전략 이름 확인."""
    sqs, universe, tm, logger = mock_deps
    strategy = FirstPullbackStrategy(sqs, universe, tm, logger=logger)
    assert strategy.name == "첫눌림목"
