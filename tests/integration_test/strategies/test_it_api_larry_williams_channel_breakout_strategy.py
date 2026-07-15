# tests/integration_test/strategies/test_it_api_larry_williams_channel_breakout_strategy.py
"""래리 윌리엄스 채널 돌파 전략 통합 테스트.

전략 로직 ↔ StockQueryService / UniverseService / IndicatorService 경계를 검증.
HTTP/브로커 계층은 service-level mock으로 대체하여 전략 조건 흐름 전체를 통합 검증한다.

검증 범위:
  - scan(): 시간 컷오프 / RS / ADX / 채널 상단 돌파 / 거래량 / 포지션 중복 / 쿨다운
  - check_exits(): 칼손절 / 트레일링 스탑 / channel_low_10d 상향 갱신
  - 상태 파일 영속성 (save/load round-trip)
"""
import pytest
from datetime import datetime
from pytz import timezone
from unittest.mock import MagicMock, AsyncMock

from common.types import ResCommonResponse
from services.stock_query_service import StockQueryService
from services.indicator_service import IndicatorService
from services.oneil_universe_service import OneilUniverseService
from strategies.larry_williams_channel_breakout_strategy import LarryWilliamsChannelBreakoutStrategy
from strategies.larry_williams_cb_types import LarryWilliamsCBConfig, LarryWilliamsCBPositionState
from strategies.oneil_common_types import OSBWatchlistItem

KST = timezone("Asia/Seoul")
_CODE = "005930"


# ─── 테스트 데이터 팩토리 ─────────────────────────────────────────────────────

def _make_ohlcv_data(days: int = 35, base_low: int = 65_000) -> list:
    """ADX 계산 최소 봉 수(period*2+slope = 14*2+3=31) 이상의 일봉 데이터.

    low를 고정하여 _calc_channel_low 결과값이 예측 가능하도록 구성.
    """
    data = []
    for i in range(days):
        close = 70_000 + i * 100
        data.append({
            "date": f"20260{1 + i // 28:02d}{1 + i % 28:02d}",
            "open": close - 100,
            "high": close + 500,
            "low": base_low,
            "close": close,
            "volume": 1_000_000,
        })
    return data


def _make_ohlcv_resp(days: int = 35, base_low: int = 65_000) -> ResCommonResponse:
    return ResCommonResponse(rt_cd="0", msg1="ok", data=_make_ohlcv_data(days, base_low))


def _make_price_resp(price: int = 75_000, vol: int = 2_000_000) -> ResCommonResponse:
    return ResCommonResponse(
        rt_cd="0", msg1="ok",
        data={"output": {"stck_prpr": str(price), "acml_vol": str(vol)}},
    )


def _make_watchlist_item(
    code: str = _CODE,
    rs_rating: int = 85,
    high_20d: int = 74_000,
    avg_vol_20d: float = 1_000_000.0,
) -> OSBWatchlistItem:
    return OSBWatchlistItem(
        code=code, name=f"종목{code}", market="KOSPI",
        high_20d=high_20d,
        ma_20d=68_000.0, ma_50d=65_000.0,
        avg_vol_20d=avg_vol_20d,
        bb_width_min_20d=0.03, prev_bb_width=0.04,
        w52_hgpr=80_000,
        avg_trading_value_5d=500_000_000_000,
        rs_rating=rs_rating,
    )


# ─── fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_tm():
    """15:15 KST — 진입 컷오프(15:10) 이후."""
    tm = MagicMock()
    tm.get_current_kst_time.return_value = datetime(2026, 4, 30, 15, 15, tzinfo=KST)
    return tm


@pytest.fixture
def mock_sqs():
    sqs = MagicMock(spec=StockQueryService)
    sqs.get_ohlcv = AsyncMock(return_value=_make_ohlcv_resp())
    # current=75_000 > high_20d=74_000, vol=2_000_000 >= avg_vol*1.5=1_500_000
    sqs.get_current_price = AsyncMock(return_value=_make_price_resp(75_000, 2_000_000))
    return sqs


@pytest.fixture
def mock_indicator():
    ind = MagicMock(spec=IndicatorService)
    ind.calc_adx_sync.return_value = {
        "adx": 30.0, "plus_di": 25.0, "minus_di": 15.0, "adx_rising": True,
    }
    return ind


@pytest.fixture
def mock_universe():
    uni = MagicMock(spec=OneilUniverseService)
    uni.get_watchlist = AsyncMock(return_value={_CODE: _make_watchlist_item()})
    return uni


@pytest.fixture
def tmp_state(tmp_path):
    return str(tmp_path / "lwcb_state.json")


@pytest.fixture
def strategy(mock_sqs, mock_universe, mock_indicator, mock_tm, tmp_state):
    return LarryWilliamsChannelBreakoutStrategy(
        stock_query_service=mock_sqs,
        universe_service=mock_universe,
        indicator_service=mock_indicator,
        market_clock=mock_tm,
        config=LarryWilliamsCBConfig(cooldown_days=2),
        state_file=tmp_state,
    )


# ─── scan: 시간 컷오프 ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_scan_before_cutoff_returns_empty(strategy, mock_tm, mock_universe):
    """15:10 이전이면 watchlist 조회 없이 즉시 빈 리스트 반환."""
    mock_tm.get_current_kst_time.return_value = datetime(2026, 4, 30, 14, 59, tzinfo=KST)
    signals = await strategy.scan()
    assert signals == []
    mock_universe.get_watchlist.assert_not_called()


@pytest.mark.asyncio
async def test_scan_empty_watchlist_returns_empty(strategy, mock_universe):
    """watchlist가 비어있으면 신호 없음."""
    mock_universe.get_watchlist.return_value = {}
    signals = await strategy.scan()
    assert signals == []


# ─── scan: RS Rating 필터 ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_scan_rs_rating_below_min_skipped(strategy, mock_universe):
    """RS Rating 79 < 임계값 80 → 후보 제외."""
    mock_universe.get_watchlist.return_value = {
        _CODE: _make_watchlist_item(rs_rating=79)
    }
    signals = await strategy.scan()
    assert signals == []


# ─── scan: ADX 필터 ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_scan_adx_below_threshold_skipped(strategy, mock_indicator):
    """ADX 20 < 임계값 25 → 스킵."""
    mock_indicator.calc_adx_sync.return_value = {
        "adx": 20.0, "plus_di": 18.0, "minus_di": 12.0, "adx_rising": True,
    }
    signals = await strategy.scan()
    assert signals == []


@pytest.mark.asyncio
async def test_scan_adx_not_rising_skipped(strategy, mock_indicator):
    """ADX ≥ 25 이지만 우상향 아님 → 스킵."""
    mock_indicator.calc_adx_sync.return_value = {
        "adx": 30.0, "plus_di": 25.0, "minus_di": 15.0, "adx_rising": False,
    }
    signals = await strategy.scan()
    assert signals == []


@pytest.mark.asyncio
async def test_scan_adx_calc_failure_skipped(strategy, mock_indicator):
    """OHLCV 데이터 부족으로 ADX 계산 실패(빈 dict) → 스킵."""
    mock_indicator.calc_adx_sync.return_value = {}
    signals = await strategy.scan()
    assert signals == []


# ─── scan: 채널 상단 돌파 필터 ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_scan_price_not_above_high_20d_skipped(strategy, mock_sqs, mock_universe):
    """현재가(75000) ≤ high_20d(76000) → 채널 돌파 실패, 스킵."""
    mock_universe.get_watchlist.return_value = {
        _CODE: _make_watchlist_item(high_20d=76_000)
    }
    signals = await strategy.scan()
    assert signals == []


# ─── scan: 거래량 필터 ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_scan_volume_insufficient_skipped(strategy, mock_sqs, mock_universe):
    """당일 거래량(1_000_000) < avg_vol_20d(1_000_000) × 1.5 → 스킵."""
    mock_sqs.get_current_price.return_value = _make_price_resp(75_000, vol=1_000_000)
    mock_universe.get_watchlist.return_value = {
        _CODE: _make_watchlist_item(high_20d=74_000, avg_vol_20d=1_000_000.0)
    }
    signals = await strategy.scan()
    assert signals == []


# ─── scan: BUY 신호 생성 ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_scan_generates_buy_signal(strategy):
    """모든 조건 충족 시 BUY TradeSignal 반환 및 포지션 상태 등록."""
    signals = await strategy.scan()

    assert len(signals) == 1
    sig = signals[0]
    assert sig.code == _CODE
    assert sig.action == "BUY"
    assert sig.price == 75_000
    assert sig.strategy_name == "LarryWilliamsCB"
    assert _CODE in strategy._position_state


@pytest.mark.asyncio
async def test_scan_hard_stop_price_is_max_of_channel_and_price_stop(strategy):
    """hard_stop = max(channel_low_20d, entry_price × (1 + hard_stop_pct/100)).

    base_low=65_000 → channel_low_20d=65_000
    price_stop = int(75_000 × 0.93) = 69_750
    hard_stop = max(65_000, 69_750) = 69_750
    """
    await strategy.scan()
    state = strategy._position_state[_CODE]
    expected = max(65_000, int(75_000 * (1 + -7.0 / 100)))
    assert state.hard_stop_price == expected
    assert state.entry_price == 75_000
    assert state.channel_low_10d == 65_000


# ─── scan: 재진입 차단 ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_scan_already_in_position_skipped(strategy):
    """이미 포지션 보유 종목은 재스캔 시 스킵."""
    strategy._position_state[_CODE] = LarryWilliamsCBPositionState(
        entry_price=70_000, entry_date="20260429",
        hard_stop_price=65_100, channel_low_10d=67_000,
    )
    signals = await strategy.scan()
    assert signals == []


@pytest.mark.asyncio
async def test_scan_cooldown_active_skipped(strategy):
    """청산 후 쿨다운 해제일 이전 → 재진입 차단."""
    strategy._cooldown[_CODE] = "20260501"  # 미래 날짜
    signals = await strategy.scan()
    assert signals == []


# ─── check_exits: 칼손절 ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_check_exits_hard_stop_triggered(strategy, mock_sqs):
    """현재가(69_000) ≤ hard_stop(69_750) → 칼손절 SELL + 쿨다운 등록."""
    strategy._position_state[_CODE] = LarryWilliamsCBPositionState(
        entry_price=75_000, entry_date="20260429",
        hard_stop_price=69_750, channel_low_10d=65_000,
    )
    mock_sqs.get_current_price.return_value = _make_price_resp(69_000)

    holdings = [{"code": _CODE, "name": "삼성전자", "buy_price": 75_000, "qty": 1}]
    signals = await strategy.check_exits(holdings)

    assert len(signals) == 1
    sig = signals[0]
    assert sig.code == _CODE
    assert sig.action == "SELL"
    assert "칼손절" in sig.reason
    assert _CODE not in strategy._position_state
    assert _CODE in strategy._cooldown


@pytest.mark.asyncio
async def test_check_exits_hard_stop_uses_buy_price_fallback(strategy, mock_sqs):
    """holdings에 buy_price가 없으면 entry_price를 pnl 계산에 사용."""
    strategy._position_state[_CODE] = LarryWilliamsCBPositionState(
        entry_price=75_000, entry_date="20260429",
        hard_stop_price=69_750, channel_low_10d=65_000,
    )
    mock_sqs.get_current_price.return_value = _make_price_resp(69_000)
    # buy_price 없는 holding
    holdings = [{"code": _CODE, "name": "삼성전자", "qty": 2}]
    signals = await strategy.check_exits(holdings)

    assert len(signals) == 1
    assert signals[0].qty == 2


# ─── check_exits: 트레일링 스탑 ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_check_exits_trailing_stop_triggered(strategy, mock_sqs):
    """현재가(64_500) < channel_low_10d(65_000) → 트레일링 스탑 SELL.

    hard_stop은 현재가 아래로 설정해야 칼손절보다 trailing stop이 먼저 판정된다.
    전략 우선순위: hard_stop(≤) → trailing(< channel_low).
    """
    strategy._position_state[_CODE] = LarryWilliamsCBPositionState(
        entry_price=75_000, entry_date="20260429",
        hard_stop_price=60_000,    # 현재가(64_500) 아래 → 칼손절 미발동
        channel_low_10d=65_000,    # 현재가 < 65_000 → trailing stop 발동
    )
    mock_sqs.get_current_price.return_value = _make_price_resp(64_500)

    holdings = [{"code": _CODE, "name": "삼성전자", "buy_price": 75_000, "qty": 1}]
    signals = await strategy.check_exits(holdings)

    assert len(signals) == 1
    sig = signals[0]
    assert sig.action == "SELL"
    assert "트레일링" in sig.reason
    assert _CODE not in strategy._position_state


@pytest.mark.asyncio
async def test_check_exits_price_exactly_at_channel_low_no_exit(strategy, mock_sqs):
    """현재가 == channel_low_10d → trailing stop 조건은 '< '이므로 청산 아님.

    hard_stop도 현재가 아래로 설정하여 칼손절이 먼저 트리거되지 않도록 한다.
    """
    strategy._position_state[_CODE] = LarryWilliamsCBPositionState(
        entry_price=75_000, entry_date="20260429",
        hard_stop_price=60_000,    # 현재가(65_000) 아래 → 칼손절 미발동
        channel_low_10d=65_000,
    )
    mock_sqs.get_current_price.return_value = _make_price_resp(65_000)
    mock_sqs.get_ohlcv.return_value = _make_ohlcv_resp(base_low=65_000)

    holdings = [{"code": _CODE, "name": "삼성전자", "buy_price": 75_000, "qty": 1}]
    signals = await strategy.check_exits(holdings)
    assert signals == []


# ─── check_exits: channel_low_10d 상향 갱신 ──────────────────────────────────

@pytest.mark.asyncio
async def test_check_exits_raises_channel_low_when_no_exit(strategy, mock_sqs):
    """청산 조건 미충족 시 새 channel_low_10d가 기존보다 높으면 상향 갱신."""
    # OHLCV low=68_000 > 현재 channel_low_10d=65_000 → 상향
    mock_sqs.get_ohlcv.return_value = _make_ohlcv_resp(base_low=68_000)
    mock_sqs.get_current_price.return_value = _make_price_resp(80_000)

    strategy._position_state[_CODE] = LarryWilliamsCBPositionState(
        entry_price=75_000, entry_date="20260429",
        hard_stop_price=69_750, channel_low_10d=65_000,
    )
    holdings = [{"code": _CODE, "name": "삼성전자", "buy_price": 75_000, "qty": 1}]
    signals = await strategy.check_exits(holdings)

    assert signals == []
    assert strategy._position_state[_CODE].channel_low_10d == 68_000


@pytest.mark.asyncio
async def test_check_exits_does_not_lower_channel_low(strategy, mock_sqs):
    """새 channel_low가 기존보다 낮으면 갱신하지 않는다 (하향 방지)."""
    mock_sqs.get_ohlcv.return_value = _make_ohlcv_resp(base_low=60_000)
    mock_sqs.get_current_price.return_value = _make_price_resp(80_000)

    strategy._position_state[_CODE] = LarryWilliamsCBPositionState(
        entry_price=75_000, entry_date="20260429",
        hard_stop_price=69_750, channel_low_10d=65_000,
    )
    holdings = [{"code": _CODE, "name": "삼성전자", "buy_price": 75_000, "qty": 1}]
    await strategy.check_exits(holdings)

    assert strategy._position_state[_CODE].channel_low_10d == 65_000


@pytest.mark.asyncio
async def test_check_exits_no_holdings_returns_empty(strategy):
    """보유 목록이 비어있으면 즉시 빈 리스트 반환."""
    signals = await strategy.check_exits([])
    assert signals == []


@pytest.mark.asyncio
async def test_check_exits_no_position_state_no_exit(strategy, mock_sqs):
    """position_state에 없는 종목은 청산 조건 판단 불가 → 신호 없음."""
    mock_sqs.get_current_price.return_value = _make_price_resp(50_000)  # 어떤 값이어도
    holdings = [{"code": _CODE, "name": "삼성전자", "buy_price": 75_000, "qty": 1}]
    signals = await strategy.check_exits(holdings)
    assert signals == []


# ─── state persistence ────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
async def _reset_strategy_state_io():
    """StrategyStateIO class-level lock/pending 상태는 event loop 사이 leak 발생.
    각 테스트 전후 reset 으로 분리.
    """
    from utils.strategy_state_io import StrategyStateIO
    StrategyStateIO._reset_for_test()
    yield
    await StrategyStateIO.flush_pending(timeout=5.0)
    StrategyStateIO._reset_for_test()


@pytest.mark.asyncio
async def test_state_saved_and_loaded_across_instances(
    mock_sqs, mock_universe, mock_indicator, mock_tm, tmp_state
):
    """scan() 후 상태 파일이 저장되고, 새 인스턴스에서 포지션이 복원된다.

    P3-4: 비동기 컨텍스트에서는 _save_state/_load_state 가 background task 로
    위임되므로, save 완료 대기 + 명시적 load_state() await 필요.
    """
    from utils.strategy_state_io import StrategyStateIO

    s1 = LarryWilliamsChannelBreakoutStrategy(
        stock_query_service=mock_sqs, universe_service=mock_universe,
        indicator_service=mock_indicator, market_clock=mock_tm,
        config=LarryWilliamsCBConfig(), state_file=tmp_state,
    )
    signals = await s1.scan()
    assert len(signals) == 1

    await StrategyStateIO.flush_pending()

    s2 = LarryWilliamsChannelBreakoutStrategy(
        stock_query_service=mock_sqs, universe_service=mock_universe,
        indicator_service=mock_indicator, market_clock=mock_tm,
        config=LarryWilliamsCBConfig(), state_file=tmp_state,
    )
    await s2.load_state()
    assert _CODE in s2._position_state
    assert s2._position_state[_CODE].entry_price == 75_000


@pytest.mark.asyncio
async def test_cooldown_persisted_after_hard_stop_exit(
    mock_sqs, mock_universe, mock_indicator, mock_tm, tmp_state
):
    """칼손절 SELL 후 쿨다운이 파일에 저장되어 재시작 후에도 유지된다.

    P3-4: 비동기 컨텍스트에서는 _save_state/_load_state 가 background task 로
    위임되므로, save 완료 대기 + 명시적 load_state() await 필요.
    """
    from utils.strategy_state_io import StrategyStateIO

    s1 = LarryWilliamsChannelBreakoutStrategy(
        stock_query_service=mock_sqs, universe_service=mock_universe,
        indicator_service=mock_indicator, market_clock=mock_tm,
        config=LarryWilliamsCBConfig(cooldown_days=2), state_file=tmp_state,
    )
    s1._position_state[_CODE] = LarryWilliamsCBPositionState(
        entry_price=75_000, entry_date="20260429",
        hard_stop_price=69_750, channel_low_10d=65_000,
    )
    mock_sqs.get_current_price.return_value = _make_price_resp(69_000)
    await s1.check_exits([{"code": _CODE, "name": "삼성전자", "buy_price": 75_000, "qty": 1}])

    await StrategyStateIO.flush_pending()

    s2 = LarryWilliamsChannelBreakoutStrategy(
        stock_query_service=mock_sqs, universe_service=mock_universe,
        indicator_service=mock_indicator, market_clock=mock_tm,
        config=LarryWilliamsCBConfig(cooldown_days=2), state_file=tmp_state,
    )
    await s2.load_state()
    assert _CODE in s2._cooldown
    assert s2._cooldown[_CODE] >= "20260502"
