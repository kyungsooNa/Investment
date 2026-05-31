# tests/unit_test/strategies/test_larry_williams_channel_breakout_strategy.py
import pytest
from unittest.mock import MagicMock, AsyncMock
from datetime import datetime
from types import SimpleNamespace

from common.types import ResCommonResponse, TradeSignal
from strategies.larry_williams_channel_breakout_strategy import LarryWilliamsChannelBreakoutStrategy
from strategies.larry_williams_cb_types import LarryWilliamsCBConfig, LarryWilliamsCBPositionState
from strategies.oneil_common_types import OSBWatchlistItem
from services.stock_query_service import StockQueryService
from services.indicator_service import IndicatorService
from services.oneil_universe_service import OneilUniverseService
from core.market_clock import MarketClock


# ── 헬퍼 ──────────────────────────────────────────────────────────

def _price_resp(current="13000", volume="200000"):
    return ResCommonResponse(
        rt_cd="0", msg1="OK",
        data={"output": {"stck_prpr": current, "acml_vol": volume}},
    )


def _ohlcv_resp(n=30, base=10000, spread=300):
    data = []
    for i in range(n):
        close = base + i * 20
        data.append({
            "date": f"2025{(i // 28) + 1:02d}{(i % 28) + 1:02d}",
            "open": close, "high": close + spread // 2,
            "low": close - spread // 2, "close": close, "volume": 150000,
        })
    return ResCommonResponse(rt_cd="0", msg1="OK", data=data)


def _watchlist_item(rs_rating=85, high_20d=12000, avg_vol_20d=100000):
    return OSBWatchlistItem(
        code="005930", name="삼성전자", market="KOSPI",
        high_20d=high_20d, ma_20d=10500, ma_50d=10000,
        avg_vol_20d=avg_vol_20d, bb_width_min_20d=500, prev_bb_width=600,
        w52_hgpr=15000, avg_trading_value_5d=50_000_000_000,
        market_cap=100_000_000_000,
        ma_200d=9500.0, minervini_stage=2,
        rs_rating=rs_rating,
    )


# ── 공통 Fixture ──────────────────────────────────────────────────

@pytest.fixture
def mock_deps():
    sqs = MagicMock(spec=StockQueryService)
    universe = MagicMock(spec=OneilUniverseService)
    indicator = MagicMock(spec=IndicatorService)
    tm = MagicMock(spec=MarketClock)
    logger = MagicMock()

    sqs.get_current_price = AsyncMock(spec=StockQueryService.get_current_price)
    sqs.prefetch_prices = AsyncMock(return_value=0)
    sqs.get_ohlcv = AsyncMock(spec=StockQueryService.get_ohlcv)
    universe.get_watchlist = AsyncMock(spec=OneilUniverseService.get_watchlist)
    # calc_adx_sync은 동기 메서드
    indicator.calc_adx_sync = MagicMock(return_value={
        "adx": 30.0, "plus_di": 25.0, "minus_di": 12.0, "adx_rising": True
    })

    return sqs, universe, indicator, tm, logger


@pytest.fixture
def scan_setup(mock_deps):
    """모든 진입 조건이 통과되도록 구성된 scan() 셋업.
    current=13000 > high_20d=12000, volume=200000 >= avg_vol_20d(100000) × 1.5
    """
    sqs, universe, indicator, tm, logger = mock_deps
    strategy = LarryWilliamsChannelBreakoutStrategy(
        sqs, universe, indicator, tm, logger=logger
    )
    strategy._position_state = {}
    strategy._cooldown = {}
    strategy._save_state = MagicMock()
    strategy._load_state = MagicMock()

    universe.get_watchlist.return_value = {"005930": _watchlist_item()}
    sqs.get_ohlcv.return_value = _ohlcv_resp(n=35)
    sqs.get_current_price.return_value = _price_resp(current="13000", volume="200000")

    # 15:15 (cutoff 15:10 통과)
    tm.get_current_kst_time.return_value = datetime(2025, 1, 2, 15, 15, 0)

    return strategy, sqs, universe, indicator, tm


# ── scan() 테스트 ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_scan_emits_buy_signal_when_all_conditions_met(scan_setup):
    """RS≥80 + ADX≥25 우상향 + 채널 상단 돌파 + 거래량 충족 + 15:10 이후 → BUY 1건."""
    strategy, sqs, *_ = scan_setup
    signals = await strategy.scan()
    assert len(signals) == 1
    sig = signals[0]
    assert isinstance(sig, TradeSignal)
    assert sig.action == "BUY"
    assert sig.code == "005930"
    assert sig.strategy_name == "LarryWilliamsCB"
    assert "채널 돌파" in sig.reason
    # 포지션 상태 등록 확인
    assert "005930" in strategy._position_state
    state = strategy._position_state["005930"]
    assert state.hard_stop_price > 0
    assert state.channel_low_10d > 0
    sqs.prefetch_prices.assert_awaited_once_with(["005930"])


@pytest.mark.asyncio
async def test_scan_skips_before_cutoff_time(scan_setup):
    """15:10 이전이면 종가 베팅 스캔을 건너뛴다."""
    strategy, _, _, _, tm = scan_setup
    tm.get_current_kst_time.return_value = datetime(2025, 1, 2, 14, 0, 0)
    signals = await strategy.scan()
    assert signals == []


@pytest.mark.asyncio
async def test_scan_filters_low_rs_rating(scan_setup):
    """RS Rating < 80인 종목은 ADX 평가 전에 제외."""
    strategy, _, universe, indicator, _ = scan_setup
    universe.get_watchlist.return_value = {"005930": _watchlist_item(rs_rating=75)}
    signals = await strategy.scan()
    assert signals == []
    indicator.calc_adx_sync.assert_not_called()


@pytest.mark.asyncio
async def test_scan_filters_low_adx(scan_setup):
    """ADX < 25이면 채널 돌파 조건과 무관하게 제외."""
    strategy, _, _, indicator, _ = scan_setup
    indicator.calc_adx_sync.return_value = {
        "adx": 20.0, "plus_di": 18.0, "minus_di": 15.0, "adx_rising": True
    }
    signals = await strategy.scan()
    assert signals == []


@pytest.mark.asyncio
async def test_scan_filters_adx_not_rising(scan_setup):
    """ADX ≥ 25이더라도 우상향이 아니면 제외."""
    strategy, _, _, indicator, _ = scan_setup
    indicator.calc_adx_sync.return_value = {
        "adx": 28.0, "plus_di": 22.0, "minus_di": 18.0, "adx_rising": False
    }
    signals = await strategy.scan()
    assert signals == []


@pytest.mark.asyncio
async def test_scan_filters_no_channel_breakout(scan_setup):
    """종가 ≤ 20일 채널 상단 → 돌파 미발생으로 제외."""
    strategy, sqs, _, _, _ = scan_setup
    # current=11000 < high_20d=12000
    sqs.get_current_price.return_value = _price_resp(current="11000", volume="200000")
    signals = await strategy.scan()
    assert signals == []


@pytest.mark.asyncio
async def test_scan_filters_insufficient_volume(scan_setup):
    """당일 거래량 < avg_vol_20d × 1.5 → 거래량 미충족으로 제외."""
    strategy, sqs, _, _, _ = scan_setup
    # avg_vol_20d=100000, multiplier=1.5 → 필요량=150000, 실제=100000
    sqs.get_current_price.return_value = _price_resp(current="13000", volume="100000")
    signals = await strategy.scan()
    assert signals == []


@pytest.mark.asyncio
async def test_scan_skips_when_in_cooldown(scan_setup):
    """쿨다운 기간 내 종목은 재진입 차단."""
    strategy, *_ = scan_setup
    strategy._cooldown = {"005930": "29991231"}
    signals = await strategy.scan()
    assert signals == []


@pytest.mark.asyncio
async def test_scan_skips_already_in_position(scan_setup):
    """이미 보유 중인 종목은 추가 매수 신호를 발행하지 않는다."""
    strategy, *_ = scan_setup
    strategy._position_state["005930"] = LarryWilliamsCBPositionState(
        entry_price=12500, entry_date="20250101",
        hard_stop_price=11625, channel_low_10d=11800,
    )
    signals = await strategy.scan()
    assert signals == []


@pytest.mark.asyncio
async def test_scan_logs_exception_from_entry_check(scan_setup):
    strategy, *_ = scan_setup
    strategy._check_entry = AsyncMock(side_effect=RuntimeError("entry boom"))

    signals = await strategy.scan()

    assert signals == []
    strategy._logger.error.assert_called_once()


@pytest.mark.asyncio
async def test_check_entry_returns_none_for_bad_ohlcv_or_price(scan_setup):
    strategy, sqs, *_ = scan_setup

    sqs.get_ohlcv.return_value = ResCommonResponse(rt_cd="1", msg1="fail", data=None)
    assert await strategy._check_entry("005930", _watchlist_item()) is None

    sqs.get_ohlcv.return_value = _ohlcv_resp(n=35)
    sqs.get_current_price.return_value = _price_resp(current="0", volume="200000")
    assert await strategy._check_entry("005930", _watchlist_item()) is None


# ── check_exits() 테스트 ─────────────────────────────────────────

@pytest.fixture
def exit_setup(mock_deps):
    sqs, universe, indicator, tm, logger = mock_deps
    strategy = LarryWilliamsChannelBreakoutStrategy(
        sqs, universe, indicator, tm, logger=logger
    )
    strategy._position_state = {
        "005930": LarryWilliamsCBPositionState(
            entry_price=12500, entry_date="20250101",
            hard_stop_price=11625,  # 진입가 × 0.93
            channel_low_10d=11800,
        )
    }
    strategy._cooldown = {}
    strategy._save_state = MagicMock()
    return strategy, sqs


@pytest.mark.asyncio
async def test_check_exits_hard_stop_triggered(exit_setup):
    """현재가 ≤ hard_stop_price → 칼손절 SELL."""
    strategy, sqs = exit_setup
    sqs.get_current_price.return_value = _price_resp(current="11600")  # ≤ 11625
    holdings = [{"code": "005930", "name": "삼성전자", "buy_price": 12500, "qty": 3}]
    signals = await strategy.check_exits(holdings)
    assert len(signals) == 1
    assert signals[0].action == "SELL"
    assert "칼손절" in signals[0].reason
    assert signals[0].qty == 3
    assert "005930" not in strategy._position_state
    assert "005930" in strategy._cooldown


@pytest.mark.asyncio
async def test_check_exits_trailing_stop_triggered(exit_setup):
    """현재가 < channel_low_10d → trailing stop SELL."""
    strategy, sqs = exit_setup
    sqs.get_current_price.return_value = _price_resp(current="11700")  # < 11800
    holdings = [{"code": "005930", "name": "삼성전자", "buy_price": 12500, "qty": 3}]
    signals = await strategy.check_exits(holdings)
    assert len(signals) == 1
    assert signals[0].action == "SELL"
    assert "트레일링" in signals[0].reason


@pytest.mark.asyncio
async def test_check_exits_no_signal_updates_channel_low(exit_setup):
    """청산 조건 미충족 + 신규 10일 저가 > 현재 channel_low_10d → 상향 갱신."""
    strategy, sqs = exit_setup
    sqs.get_current_price.return_value = _price_resp(current="13500")  # 상승 추세
    # OHLCV의 최근 10일 저가가 12000 (현재 channel_low_10d=11800 보다 높음)
    sqs.get_ohlcv.return_value = _ohlcv_resp(n=30, base=12000, spread=200)
    holdings = [{"code": "005930", "name": "삼성전자", "buy_price": 12500, "qty": 3}]
    signals = await strategy.check_exits(holdings)
    assert signals == []
    # channel_low_10d가 11800보다 올라가야 함
    assert strategy._position_state["005930"].channel_low_10d >= 11800


@pytest.mark.asyncio
async def test_check_exits_no_signal_when_holding_range(exit_setup):
    """칼손절/trailing 미도달, channel_low_10d 유지 → 신호·상태 변경 없음."""
    strategy, sqs = exit_setup
    sqs.get_current_price.return_value = _price_resp(current="13000")  # 안전 구간
    # OHLCV 저가가 현재 channel_low_10d(11800)보다 낮아 갱신 없음
    sqs.get_ohlcv.return_value = _ohlcv_resp(n=30, base=9000, spread=200)
    holdings = [{"code": "005930", "name": "삼성전자", "buy_price": 12500, "qty": 3}]
    before_low = strategy._position_state["005930"].channel_low_10d
    signals = await strategy.check_exits(holdings)
    assert signals == []
    assert strategy._position_state["005930"].channel_low_10d == before_low


@pytest.mark.asyncio
async def test_check_exits_logs_exceptions_and_skips_none_results(exit_setup):
    strategy, _ = exit_setup
    strategy._check_single_exit = AsyncMock(side_effect=[RuntimeError("exit boom"), None])

    signals = await strategy.check_exits([
        {"code": "005930", "buy_price": 1, "qty": 1},
        {"code": "000660", "buy_price": 1, "qty": 1},
    ])

    assert signals == []
    strategy._logger.error.assert_called_once()


@pytest.mark.asyncio
async def test_check_single_exit_skips_missing_code_and_bad_price(exit_setup):
    strategy, sqs = exit_setup

    assert await strategy._check_single_exit({}) is None

    sqs.get_current_price.return_value = _price_resp(current="0")
    assert await strategy._check_single_exit({"code": "005930"}) is None


# ── P0 0-8: 당일 미확정 봉 제외 ───────────────────────────────────

def _ohlcv_with_today(today_str, n_confirmed=34, confirmed_low=12500, today_low=50):
    """확정봉 n개(date < today) + 마지막에 당일(미확정) 봉 1개.
    get_ohlcv가 장중 붙이는 today 행을 모사한다. today_low는 채널/ADX를 오염시키는 극단값."""
    data = []
    for i in range(n_confirmed):
        data.append({
            "date": f"2024{(i // 28) + 1:02d}{(i % 28) + 1:02d}",
            "open": 13000, "high": 13100,
            "low": confirmed_low, "close": 13000, "volume": 150000,
        })
    data.append({
        "date": today_str,
        "open": 13000, "high": 14000,
        "low": today_low, "close": 13500, "volume": 90000,
    })
    return ResCommonResponse(rt_cd="0", msg1="OK", data=data)


@pytest.mark.asyncio
async def test_check_entry_excludes_today_bar_from_adx_and_channel(scan_setup):
    """라이브 진입: get_ohlcv가 붙인 당일 미확정 봉을 ADX/채널 하단 계산에서 제외한다."""
    strategy, sqs, _, indicator, tm = scan_setup
    tm.get_current_kst_time.return_value = datetime(2025, 1, 2, 15, 15, 0)  # today=20250102
    sqs.get_ohlcv.return_value = _ohlcv_with_today("20250102", confirmed_low=12500, today_low=50)

    sig = await strategy._check_entry("005930", _watchlist_item())

    assert sig is not None and sig.action == "BUY"
    # calc_adx_sync에는 당일 봉이 빠진 확정봉만 전달되어야 한다
    passed_ohlcv = indicator.calc_adx_sync.call_args.args[0]
    assert all(row["date"] != "20250102" for row in passed_ohlcv)
    # 채널 하단/칼손절이 당일 극단 저가(50)에 오염되지 않고 확정봉(12500) 기준으로 계산
    state = strategy._position_state["005930"]
    assert state.channel_low_10d == 12500
    assert state.hard_stop_price >= 12500


@pytest.mark.asyncio
async def test_check_exits_trailing_update_excludes_today_bar(exit_setup):
    """라이브 청산: trailing stop 상향 갱신도 당일 미확정 봉 저가를 제외한 확정봉 기준."""
    strategy, sqs = exit_setup
    strategy._tm.get_current_kst_time.return_value = datetime(2025, 1, 2, 15, 15, 0)
    sqs.get_current_price.return_value = _price_resp(current="13500")  # 청산 미발동
    # 확정봉 최근 10일 저가=12500, 당일 봉 저가=12000(오염값)
    sqs.get_ohlcv.return_value = _ohlcv_with_today("20250102", confirmed_low=12500, today_low=12000)

    holdings = [{"code": "005930", "name": "삼성전자", "buy_price": 12500, "qty": 3}]
    signals = await strategy.check_exits(holdings)

    assert signals == []
    # 당일 저가(12000)가 아니라 확정봉 저가(12500)로 상향되어야 한다
    assert strategy._position_state["005930"].channel_low_10d == 12500


# ── 헬퍼 / 인터페이스 ────────────────────────────────────────────

def test_name_property(mock_deps):
    sqs, universe, indicator, tm, logger = mock_deps
    strategy = LarryWilliamsChannelBreakoutStrategy(
        sqs, universe, indicator, tm, logger=logger
    )
    assert strategy.name == "LarryWilliamsCB"


def test_calc_channel_low_returns_min_of_period(mock_deps):
    """_calc_channel_low: 최근 period 봉 저가의 최솟값 반환."""
    ohlcv = [{"low": 10000 + i * 100} for i in range(20)]
    result = LarryWilliamsChannelBreakoutStrategy._calc_channel_low(ohlcv, period=10)
    # 최근 10봉: low = 11000 ~ 11900 → min = 11000
    assert result == 11000


def test_calc_channel_low_empty_returns_zero(mock_deps):
    result = LarryWilliamsChannelBreakoutStrategy._calc_channel_low([], period=10)
    assert result == 0


def test_extract_price_and_volume_handle_failure_object_and_invalid_values():
    assert LarryWilliamsChannelBreakoutStrategy._extract_current_price(None) == 0
    assert LarryWilliamsChannelBreakoutStrategy._extract_current_price(ResCommonResponse(rt_cd="1", msg1="fail")) == 0
    assert LarryWilliamsChannelBreakoutStrategy._extract_current_price(
        ResCommonResponse(rt_cd="0", msg1="OK", data=SimpleNamespace(stck_prpr="12345"))
    ) == 12345
    assert LarryWilliamsChannelBreakoutStrategy._extract_current_price(
        ResCommonResponse(rt_cd="0", msg1="OK", data={"output": {"stck_prpr": "bad"}})
    ) == 0

    assert LarryWilliamsChannelBreakoutStrategy._extract_today_volume(None) == 0
    assert LarryWilliamsChannelBreakoutStrategy._extract_today_volume(ResCommonResponse(rt_cd="1", msg1="fail")) == 0
    assert LarryWilliamsChannelBreakoutStrategy._extract_today_volume(
        ResCommonResponse(rt_cd="0", msg1="OK", data=SimpleNamespace(acml_vol="123"))
    ) == 123
    assert LarryWilliamsChannelBreakoutStrategy._extract_today_volume(
        ResCommonResponse(rt_cd="0", msg1="OK", data={"output": {"acml_vol": "bad"}})
    ) == 0


def test_calc_channel_low_invalid_rows_returns_zero():
    assert LarryWilliamsChannelBreakoutStrategy._calc_channel_low([{"low": object()}], period=10) == 0


def test_state_load_and_save_error_paths(mock_deps, tmp_path):
    sqs, universe, indicator, tm, logger = mock_deps
    bad_state = tmp_path / "bad_state.json"
    bad_state.write_text("{bad json", encoding="utf-8")

    strategy = LarryWilliamsChannelBreakoutStrategy(
        sqs, universe, indicator, tm, logger=logger, state_file=str(bad_state)
    )
    assert strategy._position_state == {}
    logger.error.assert_called()

    logger.reset_mock()
    strategy.STATE_FILE = str(tmp_path)
    strategy._save_state()
    logger.error.assert_called()


def test_state_roundtrip_loads_positions_and_cooldown(mock_deps, tmp_path):
    sqs, universe, indicator, tm, logger = mock_deps
    state_file = tmp_path / "state.json"
    state_file.write_text(
        (
            '{"positions":{"005930":{"entry_price":100,"entry_date":"20250101",'
            '"hard_stop_price":90,"channel_low_10d":95,"entry_adx":30}},'
            '"cooldown":{"000660":"20250110"}}'
        ),
        encoding="utf-8",
    )

    strategy = LarryWilliamsChannelBreakoutStrategy(
        sqs, universe, indicator, tm, logger=logger, state_file=str(state_file)
    )

    assert strategy._position_state["005930"].entry_price == 100
    assert strategy._cooldown == {"000660": "20250110"}
