# tests/unit_test/test_high_tight_flag_strategy.py
import pytest
from unittest.mock import MagicMock, AsyncMock
from datetime import datetime
from common.types import ResCommonResponse
from strategies.high_tight_flag_strategy import HighTightFlagStrategy
from strategies.oneil_common_types import HTFConfig, HTFPositionState, OSBWatchlistItem


# ── 헬퍼: 합성 OHLCV 데이터 생성 ────────────────────────────────────

def _make_ohlcv_pole_and_flag(
    pole_days=25,
    pole_start_price=5000,
    pole_end_price=10000,
    flag_days=18,
    flag_drawdown_pct=10.0,
    pole_volume=500000,
    flag_volume=100000,
):
    """깃대(상승) + 깃발(횡보) OHLCV 합성 데이터 생성.

    Returns:
        list[dict]: OHLCV 리스트 (날짜 오름차순)
    """
    ohlcv = []
    # 깃대 구간: 선형 상승
    for i in range(pole_days):
        ratio = i / max(pole_days - 1, 1)
        price = int(pole_start_price + (pole_end_price - pole_start_price) * ratio)
        ohlcv.append({
            "date": f"2025{(i // 28 + 1):02d}{(i % 28 + 1):02d}",
            "open": price - 50,
            "high": price + 100,
            "low": price - 100,
            "close": price,
            "volume": pole_volume,
        })
    # 깃대 최고점 (마지막 봉의 high가 peak)
    ohlcv[-1]["high"] = pole_end_price

    # 깃발 구간: 고점 대비 소폭 하락 횡보
    flag_price = int(pole_end_price * (1 - flag_drawdown_pct / 100 / 2))
    flag_low = int(pole_end_price * (1 - flag_drawdown_pct / 100))
    for j in range(flag_days):
        day_idx = pole_days + j
        ohlcv.append({
            "date": f"2025{(day_idx // 28 + 1):02d}{(day_idx % 28 + 1):02d}",
            "open": flag_price - 30,
            "high": flag_price + 50,
            "low": flag_low,
            "close": flag_price,
            "volume": flag_volume,
        })

    return ohlcv


# ── Fixtures ─────────────────────────────────────────────────────────

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
        code="005930", name="테스트종목", market="KOSPI",
        high_20d=10000, ma_20d=9500, ma_50d=9000,
        avg_vol_20d=200000, bb_width_min_20d=500, prev_bb_width=600,
        w52_hgpr=10500, avg_trading_value_5d=50_000_000_000,
        market_cap=500_000_000_000,
    )


@pytest.fixture
def htf_scan_setup(mock_deps, watchlist_item):
    """scan() 테스트 공통 설정."""
    sqs, universe, tm, logger = mock_deps
    strategy = HighTightFlagStrategy(sqs, universe, tm, logger=logger)
    strategy._position_state = {}
    strategy._save_state = MagicMock()

    universe.get_watchlist.return_value = {"005930": watchlist_item}
    universe.is_market_timing_ok.return_value = True

    # 장중 50% 진행 가정
    tm.get_current_kst_time.return_value = datetime(2025, 1, 1, 12, 0, 0)
    tm.get_market_open_time.return_value = datetime(2025, 1, 1, 9, 0, 0)
    tm.get_market_close_time.return_value = datetime(2025, 1, 1, 15, 30, 0)

    return strategy, sqs, universe, tm, logger


# ── Phase 1+2: _detect_pole_and_flag (순수 계산) ─────────────────────

class TestDetectPoleAndFlag:
    def _make_strategy(self, mock_deps, **cfg_overrides):
        sqs, universe, tm, logger = mock_deps
        config = HTFConfig(**cfg_overrides) if cfg_overrides else HTFConfig()
        strategy = HighTightFlagStrategy(sqs, universe, tm, config=config, logger=logger)
        return strategy

    def test_valid_htf_pattern(self, mock_deps):
        """90%+ 폭등 + 15일 횡보(10% 하락) + 거래량 감소 → 패턴 감지 성공."""
        strategy = self._make_strategy(mock_deps)
        ohlcv = _make_ohlcv_pole_and_flag(
            pole_days=25, pole_start_price=5000, pole_end_price=10000,
            flag_days=18, flag_drawdown_pct=10.0,
            pole_volume=500000, flag_volume=100000,
        )

        result = strategy._detect_pole_and_flag(ohlcv)

        assert result is not None
        assert result["surge_ratio"] >= 1.90
        assert result["flag_days"] == 18
        assert result["drawdown_pct"] <= 20.0

    def test_insufficient_surge(self, mock_deps):
        """50% 폭등 → surge_ratio < 1.90 → None."""
        strategy = self._make_strategy(mock_deps)
        ohlcv = _make_ohlcv_pole_and_flag(
            pole_days=25, pole_start_price=7000, pole_end_price=10000,
            flag_days=18, flag_drawdown_pct=10.0,
            pole_volume=500000, flag_volume=100000,
        )

        result = strategy._detect_pole_and_flag(ohlcv)
        assert result is None

    def test_deep_drawdown(self, mock_deps):
        """깃발 구간 종가 기준 > 20% 하락 → drawdown > 20% → None."""
        strategy = self._make_strategy(mock_deps)
        ohlcv = _make_ohlcv_pole_and_flag(
            pole_days=25, pole_start_price=5000, pole_end_price=10000,
            flag_days=18, flag_drawdown_pct=45.0,  # 종가 기준 22.5% 하락
            pole_volume=500000, flag_volume=100000,
        )

        result = strategy._detect_pole_and_flag(ohlcv)
        assert result is None

    def test_volume_not_shrinking(self, mock_deps):
        """깃발 거래량이 50일 평균 대비 120% 초과 → 거래량 미건조 → None."""
        strategy = self._make_strategy(mock_deps)
        ohlcv = _make_ohlcv_pole_and_flag(
            pole_days=25, pole_start_price=5000, pole_end_price=10000,
            flag_days=18, flag_drawdown_pct=10.0,
            pole_volume=100000, flag_volume=500000,  # 깃발 거래량 >> 50일 평균
        )

        result = strategy._detect_pole_and_flag(ohlcv)
        assert result is None

    def test_flag_too_short(self, mock_deps):
        """횡보 3일 → flag_min_days(5) 미달 → None."""
        strategy = self._make_strategy(mock_deps)
        ohlcv = _make_ohlcv_pole_and_flag(
            pole_days=25, pole_start_price=5000, pole_end_price=10000,
            flag_days=3, flag_drawdown_pct=10.0,
            pole_volume=500000, flag_volume=100000,
        )

        result = strategy._detect_pole_and_flag(ohlcv)
        assert result is None

    def test_flag_too_long(self, mock_deps):
        """횡보 30일 → flag_max_days(25) 초과 → None."""
        strategy = self._make_strategy(mock_deps)
        ohlcv = _make_ohlcv_pole_and_flag(
            pole_days=25, pole_start_price=5000, pole_end_price=10000,
            flag_days=30, flag_drawdown_pct=10.0,
            pole_volume=500000, flag_volume=100000,
        )

        result = strategy._detect_pole_and_flag(ohlcv)
        assert result is None


# ── scan() 통합 테스트 ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_scan_buy_signal(htf_scan_setup):
    """scan: HTF 패턴 + 돌파 조건 충족 → BUY 시그널 생성."""
    strategy, sqs, _, _, _ = htf_scan_setup

    # OHLCV: 유효한 HTF 패턴
    ohlcv = _make_ohlcv_pole_and_flag(
        pole_days=25, pole_start_price=5000, pole_end_price=10000,
        flag_days=18, flag_drawdown_pct=10.0,
        pole_volume=500000, flag_volume=100000,
    )
    sqs.get_recent_daily_ohlcv.return_value = MagicMock(rt_cd="0", data=ohlcv)

    # 현재가: 10500 (> pole_high 10000) + 거래량 대량
    sqs.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {
            "stck_prpr": "10500", "acml_vol": "600000",
        }}
    )

    # 체결강도 130%
    sqs.get_stock_conclusion.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": [{"tday_rltv": "130.0"}]}
    )

    signals = await strategy.scan()

    assert len(signals) == 1
    assert signals[0].code == "005930"
    assert signals[0].action == "BUY"
    assert "HTF돌파" in signals[0].reason
    assert "체결강도 130.0%" in signals[0].reason
    assert "005930" in strategy._position_state


@pytest.mark.asyncio
async def test_scan_no_breakout_price(htf_scan_setup):
    """scan: 현재가 <= 40일 최고가 → 시그널 없음."""
    strategy, sqs, _, _, _ = htf_scan_setup

    ohlcv = _make_ohlcv_pole_and_flag(
        pole_days=25, pole_start_price=5000, pole_end_price=10000,
        flag_days=18, flag_drawdown_pct=10.0,
        pole_volume=500000, flag_volume=100000,
    )
    sqs.get_recent_daily_ohlcv.return_value = MagicMock(rt_cd="0", data=ohlcv)

    # 현재가 9800 (< pole_high 10000) → 돌파 실패
    sqs.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {
            "stck_prpr": "9800", "acml_vol": "600000",
        }}
    )

    signals = await strategy.scan()
    assert len(signals) == 0


@pytest.mark.asyncio
async def test_scan_low_volume(htf_scan_setup):
    """scan: 예상거래량 부족 → 시그널 없음."""
    strategy, sqs, _, _, _ = htf_scan_setup

    ohlcv = _make_ohlcv_pole_and_flag(
        pole_days=25, pole_start_price=5000, pole_end_price=10000,
        flag_days=18, flag_drawdown_pct=10.0,
        pole_volume=500000, flag_volume=100000,
    )
    sqs.get_recent_daily_ohlcv.return_value = MagicMock(rt_cd="0", data=ohlcv)

    # 현재가 돌파하지만 거래량 부족 (10000 / 0.5 = 20000 < avg * 2.0)
    sqs.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {
            "stck_prpr": "10500", "acml_vol": "10000",
        }}
    )

    signals = await strategy.scan()
    assert len(signals) == 0


@pytest.mark.asyncio
async def test_scan_low_execution_strength(htf_scan_setup):
    """scan: 체결강도 < 120% → 시그널 없음."""
    strategy, sqs, _, _, _ = htf_scan_setup

    ohlcv = _make_ohlcv_pole_and_flag(
        pole_days=25, pole_start_price=5000, pole_end_price=10000,
        flag_days=18, flag_drawdown_pct=10.0,
        pole_volume=500000, flag_volume=100000,
    )
    sqs.get_recent_daily_ohlcv.return_value = MagicMock(rt_cd="0", data=ohlcv)

    sqs.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {
            "stck_prpr": "10500", "acml_vol": "600000",
        }}
    )

    # 체결강도 110% (< 120%)
    sqs.get_stock_conclusion.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": [{"tday_rltv": "110.0"}]}
    )

    signals = await strategy.scan()
    assert len(signals) == 0


@pytest.mark.asyncio
async def test_scan_empty_watchlist(htf_scan_setup):
    """scan: 워치리스트가 비어있으면 빈 리스트."""
    strategy, _, universe, _, _ = htf_scan_setup
    universe.get_watchlist.return_value = {}

    signals = await strategy.scan()
    assert len(signals) == 0


@pytest.mark.asyncio
async def test_scan_skip_existing_position(htf_scan_setup):
    """scan: 이미 보유 중인 종목은 스캔 제외."""
    strategy, sqs, _, _, _ = htf_scan_setup
    strategy._position_state["005930"] = HTFPositionState(10000, "20250101", 10000, 10000)

    sqs.get_current_price.reset_mock()
    signals = await strategy.scan()
    assert len(signals) == 0
    sqs.get_current_price.assert_not_called()


@pytest.mark.asyncio
async def test_scan_bad_market_timing(htf_scan_setup):
    """scan: 마켓 타이밍 불량 → 스캔 제외."""
    strategy, sqs, universe, _, _ = htf_scan_setup
    universe.is_market_timing_ok.return_value = False

    signals = await strategy.scan()
    assert len(signals) == 0
    sqs.get_recent_daily_ohlcv.assert_not_called()


# ── check_exits() 테스트 ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_exit_hard_stop(mock_deps):
    """check_exits: -5% 칼손절 발동."""
    sqs, universe, tm, logger = mock_deps
    strategy = HighTightFlagStrategy(sqs, universe, tm, logger=logger)
    strategy._save_state = MagicMock()

    strategy._position_state["005930"] = HTFPositionState(
        entry_price=10000, entry_date="20250101", peak_price=10000, pole_high=9500,
    )

    # 현재가 9400 → PnL = -6%
    sqs.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {"stck_prpr": "9400"}}
    )

    holdings = [{"code": "005930", "buy_price": 10000, "qty": 50, "name": "테스트종목"}]
    signals = await strategy.check_exits(holdings)

    assert len(signals) == 1
    assert signals[0].action == "SELL"
    assert signals[0].qty == 50  # 전량 매도
    assert "칼손절" in signals[0].reason
    assert "005930" not in strategy._position_state


@pytest.mark.asyncio
async def test_exit_trailing_ma_stop(mock_deps):
    """check_exits: 10일 MA 하향이탈 → 트레일링스탑."""
    sqs, universe, tm, logger = mock_deps
    strategy = HighTightFlagStrategy(sqs, universe, tm, logger=logger)
    strategy._save_state = MagicMock()

    strategy._position_state["005930"] = HTFPositionState(
        entry_price=10000, entry_date="20250101", peak_price=12000, pole_high=9500,
    )

    # 현재가 10500 (PnL +5%, 손절 아님)
    sqs.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {"stck_prpr": "10500"}}
    )

    # 10일 OHLCV: 종가 11000 일정 → 10MA = 11000, 현재가 10500 < 11000
    ohlcv = [{"close": 11000, "volume": 100000} for _ in range(10)]
    sqs.get_recent_daily_ohlcv.return_value = MagicMock(rt_cd="0", data=ohlcv)

    holdings = [{"code": "005930", "buy_price": 10000, "qty": 30, "name": "테스트종목"}]
    signals = await strategy.check_exits(holdings)

    assert len(signals) == 1
    assert signals[0].action == "SELL"
    assert signals[0].qty == 30  # 전량 매도
    assert "트레일링스탑" in signals[0].reason
    assert "10MA" in signals[0].reason


@pytest.mark.asyncio
async def test_exit_hold(mock_deps):
    """check_exits: 손절/MA 조건 모두 미충족 → 시그널 없음 (계속 보유)."""
    sqs, universe, tm, logger = mock_deps
    strategy = HighTightFlagStrategy(sqs, universe, tm, logger=logger)
    strategy._save_state = MagicMock()

    strategy._position_state["005930"] = HTFPositionState(
        entry_price=10000, entry_date="20250101", peak_price=11000, pole_high=9500,
    )

    # 현재가 11500 (PnL +15%, 신고가)
    sqs.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {"stck_prpr": "11500"}}
    )

    # 10MA = 11000, 현재가 11500 > 11000 → MA 위
    ohlcv = [{"close": 11000, "volume": 100000} for _ in range(10)]
    sqs.get_recent_daily_ohlcv.return_value = MagicMock(rt_cd="0", data=ohlcv)

    holdings = [{"code": "005930", "buy_price": 10000, "qty": 50, "name": "테스트종목"}]
    signals = await strategy.check_exits(holdings)

    assert len(signals) == 0
    # 최고가 갱신 확인
    assert strategy._position_state["005930"].peak_price == 11500


# ── 상태 영속화 테스트 ───────────────────────────────────────────────

def test_state_persistence(mock_deps, tmp_path):
    """_save_state / _load_state: 파일 입출력 동작 검증."""
    sqs, universe, tm, logger = mock_deps
    strategy = HighTightFlagStrategy(sqs, universe, tm, logger=logger)

    test_file = tmp_path / "htf_state.json"
    strategy.STATE_FILE = str(test_file)

    strategy._position_state = {
        "005930": HTFPositionState(10000, "20250101", 12000, 9500)
    }
    strategy._save_state()
    assert test_file.exists()

    strategy2 = HighTightFlagStrategy(sqs, universe, tm, logger=logger)
    strategy2.STATE_FILE = str(test_file)
    strategy2._position_state = {}
    strategy2._load_state()

    assert "005930" in strategy2._position_state
    assert strategy2._position_state["005930"].peak_price == 12000
    assert strategy2._position_state["005930"].pole_high == 9500


# ── 기타 헬퍼 테스트 ─────────────────────────────────────────────────

def test_calculate_qty(mock_deps):
    """_calculate_qty: 정상 계산 및 경계값."""
    sqs, universe, tm, logger = mock_deps
    strategy = HighTightFlagStrategy(sqs, universe, tm, logger=logger)

    # 기본: 1000만 * 5% = 50만, 50만 / 10000 = 50주
    assert strategy._calculate_qty(10000) == 50
    # 가격 0 → min_qty(1)
    assert strategy._calculate_qty(0) == 1
    assert strategy._calculate_qty(-100) == 1


# ── Edge Cases & Error Handling ──────────────────────────────────────

@pytest.mark.asyncio
async def test_scan_market_not_open(htf_scan_setup):
    """scan: 장 시작 전(progress <= 0)이면 스캔 중단."""
    strategy, _, _, tm, _ = htf_scan_setup

    # 장 시작 전 시간 설정 (08:59)
    tm.get_current_kst_time.return_value = datetime(2025, 1, 1, 8, 59, 0)
    tm.get_market_open_time.return_value = datetime(2025, 1, 1, 9, 0, 0)
    tm.get_market_close_time.return_value = datetime(2025, 1, 1, 15, 30, 0)

    signals = await strategy.scan()
    assert len(signals) == 0


@pytest.mark.asyncio
async def test_scan_ohlcv_api_failure(htf_scan_setup):
    """scan: OHLCV 조회 실패 시 해당 종목 스킵."""
    strategy, sqs, _, _, _ = htf_scan_setup

    # OHLCV API 에러 응답
    sqs.get_recent_daily_ohlcv.return_value = ResCommonResponse(
        rt_cd="1", msg1="Error", data=[]
    )

    signals = await strategy.scan()
    assert len(signals) == 0


@pytest.mark.asyncio
async def test_scan_ohlcv_data_insufficient(htf_scan_setup):
    """scan: OHLCV 데이터가 pole_lookback_days보다 적으면 스킵."""
    strategy, sqs, _, _, _ = htf_scan_setup
    strategy._cfg.pole_lookback_days = 40

    # 39일치 데이터만 반환
    ohlcv = _make_ohlcv_pole_and_flag(pole_days=20, flag_days=19)  # total 39 days
    sqs.get_recent_daily_ohlcv.return_value = MagicMock(rt_cd="0", data=ohlcv)

    signals = await strategy.scan()
    assert len(signals) == 0


@pytest.mark.asyncio
async def test_scan_current_price_api_failure(htf_scan_setup):
    """scan: 현재가 조회 실패 시 스킵."""
    strategy, sqs, _, _, _ = htf_scan_setup

    # OHLCV는 정상 (패턴 감지 성공 유도)
    ohlcv = _make_ohlcv_pole_and_flag()
    sqs.get_recent_daily_ohlcv.return_value = MagicMock(rt_cd="0", data=ohlcv)

    # 현재가 조회 실패
    sqs.get_current_price.return_value = ResCommonResponse(
        rt_cd="1", msg1="Error", data=None
    )

    signals = await strategy.scan()
    assert len(signals) == 0


@pytest.mark.asyncio
async def test_scan_conclusion_api_failure(htf_scan_setup):
    """scan: 체결강도 조회 실패(예외 발생) 시 스킵."""
    strategy, sqs, _, _, logger = htf_scan_setup

    # OHLCV 정상
    ohlcv = _make_ohlcv_pole_and_flag()
    sqs.get_recent_daily_ohlcv.return_value = MagicMock(rt_cd="0", data=ohlcv)

    # 현재가 정상 (돌파 조건 충족)
    sqs.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {
            "stck_prpr": "10500", "acml_vol": "600000",
        }}
    )

    # 체결강도 조회 중 예외 발생
    sqs.get_stock_conclusion.side_effect = Exception("API Error")

    signals = await strategy.scan()
    assert len(signals) == 0


@pytest.mark.asyncio
async def test_scan_insufficient_volume_data_for_avg(htf_scan_setup):
    """scan: 거래량 이동평균 계산을 위한 데이터가 20일 미만이면 스킵."""
    strategy, sqs, _, _, _ = htf_scan_setup

    # OHLCV는 HTF 패턴을 만족하지만, 데이터 길이는 19일.
    ohlcv = _make_ohlcv_pole_and_flag(pole_days=10, flag_days=8)  # total 18 days
    sqs.get_recent_daily_ohlcv.return_value = MagicMock(rt_cd="0", data=ohlcv)

    # 현재가 및 체결강도는 돌파 조건 만족
    sqs.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {"stck_prpr": "10500", "acml_vol": "600000"}}
    )
    sqs.get_stock_conclusion.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": [{"tday_rltv": "130.0"}]}
    )

    signals = await strategy.scan()
    assert len(signals) == 0


@pytest.mark.asyncio
async def test_check_exits_price_api_failure(mock_deps):
    """check_exits: 현재가 조회 실패 시 홀딩 유지 (시그널 없음)."""
    sqs, universe, tm, logger = mock_deps
    strategy = HighTightFlagStrategy(sqs, universe, tm, logger=logger)
    strategy._save_state = MagicMock()

    strategy._position_state["005930"] = HTFPositionState(10000, "20250101", 10000, 10000)

    # 현재가 API 실패
    sqs.get_current_price.return_value = ResCommonResponse(rt_cd="1", msg1="Fail", data=None)

    holdings = [{"code": "005930", "buy_price": 10000, "name": "테스트종목"}]
    signals = await strategy.check_exits(holdings)

    assert len(signals) == 0


@pytest.mark.asyncio
async def test_check_exits_for_untracked_holding(mock_deps):
    """check_exits: _position_state에 없는 보유종목은 새로 상태를 만들어 손절 로직 적용."""
    sqs, universe, tm, logger = mock_deps
    strategy = HighTightFlagStrategy(sqs, universe, tm, logger=logger)
    strategy._save_state = MagicMock()

    assert "005930" not in strategy._position_state

    # 현재가 9400, 매수가 10000 -> -6% 손실
    sqs.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {"stck_prpr": "9400"}}
    )

    # state가 없어도, buy_price 기준으로 stop_loss_pct(-5%)가 적용되어야 함
    holdings = [{"code": "005930", "buy_price": 10000, "name": "테스트종목"}]
    signals = await strategy.check_exits(holdings)

    assert len(signals) == 1
    assert signals[0].action == "SELL"
    assert "칼손절" in signals[0].reason
    assert "005930" not in strategy._position_state


# ── Edge Cases & Error Handling ──────────────────────────────────────

@pytest.mark.asyncio
async def test_scan_market_not_open(htf_scan_setup):
    """scan: 장 시작 전(progress <= 0)이면 스캔 중단."""
    strategy, _, _, tm, _ = htf_scan_setup

    # 장 시작 전 시간 설정 (08:59)
    tm.get_current_kst_time.return_value = datetime(2025, 1, 1, 8, 59, 0)
    tm.get_market_open_time.return_value = datetime(2025, 1, 1, 9, 0, 0)
    tm.get_market_close_time.return_value = datetime(2025, 1, 1, 15, 30, 0)

    signals = await strategy.scan()
    assert len(signals) == 0


@pytest.mark.asyncio
async def test_scan_ohlcv_api_failure(htf_scan_setup):
    """scan: OHLCV 조회 실패 시 해당 종목 스킵."""
    strategy, sqs, _, _, _ = htf_scan_setup

    # OHLCV API 에러 응답
    sqs.get_recent_daily_ohlcv.return_value = ResCommonResponse(
        rt_cd="1", msg1="Error", data=[]
    )

    signals = await strategy.scan()
    assert len(signals) == 0


@pytest.mark.asyncio
async def test_scan_current_price_api_failure(htf_scan_setup):
    """scan: 현재가 조회 실패 시 스킵."""
    strategy, sqs, _, _, _ = htf_scan_setup

    # OHLCV는 정상 (패턴 감지 성공 유도)
    ohlcv = _make_ohlcv_pole_and_flag()
    sqs.get_recent_daily_ohlcv.return_value = MagicMock(rt_cd="0", data=ohlcv)

    # 현재가 조회 실패
    sqs.get_current_price.return_value = ResCommonResponse(
        rt_cd="1", msg1="Error", data=None
    )

    signals = await strategy.scan()
    assert len(signals) == 0


@pytest.mark.asyncio
async def test_scan_conclusion_api_failure(htf_scan_setup):
    """scan: 체결강도 조회 실패(예외 발생) 시 스킵."""
    strategy, sqs, _, _, logger = htf_scan_setup

    # OHLCV 정상
    ohlcv = _make_ohlcv_pole_and_flag()
    sqs.get_recent_daily_ohlcv.return_value = MagicMock(rt_cd="0", data=ohlcv)

    # 현재가 정상 (돌파 조건 충족)
    sqs.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {
            "stck_prpr": "10500", "acml_vol": "600000",
        }}
    )

    # 체결강도 조회 중 예외 발생
    sqs.get_stock_conclusion.side_effect = Exception("API Error")

    signals = await strategy.scan()
    assert len(signals) == 0


@pytest.mark.asyncio
async def test_check_exits_price_api_failure(mock_deps):
    """check_exits: 현재가 조회 실패 시 홀딩 유지 (시그널 없음)."""
    sqs, universe, tm, logger = mock_deps
    strategy = HighTightFlagStrategy(sqs, universe, tm, logger=logger)
    strategy._save_state = MagicMock()

    strategy._position_state["005930"] = HTFPositionState(10000, "20250101", 10000, 10000)

    # 현재가 API 실패
    sqs.get_current_price.return_value = ResCommonResponse(rt_cd="1", msg1="Fail", data=None)

    holdings = [{"code": "005930", "buy_price": 10000, "name": "테스트종목"}]
    signals = await strategy.check_exits(holdings)

    assert len(signals) == 0


@pytest.mark.asyncio
async def test_check_exits_trailing_ma_data_insufficient(mock_deps):
    """check_exits: 트레일링스탑 계산 시 데이터 부족하면 스킵."""
    sqs, universe, tm, logger = mock_deps
    strategy = HighTightFlagStrategy(sqs, universe, tm, logger=logger)
    strategy._save_state = MagicMock()

    strategy._position_state["005930"] = HTFPositionState(10000, "20250101", 12000, 10000)

    # 현재가 10500 (고점 대비 하락했으나 손절가는 아님)
    sqs.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {"stck_prpr": "10500"}}
    )

    # OHLCV 데이터가 10일치 미만 (5일)
    ohlcv = [{"close": 11000} for _ in range(5)]
    sqs.get_recent_daily_ohlcv.return_value = MagicMock(rt_cd="0", data=ohlcv)

    holdings = [{"code": "005930", "buy_price": 10000, "name": "테스트종목"}]
    signals = await strategy.check_exits(holdings)

    assert len(signals) == 0  # 데이터 부족으로 MA 계산 불가 -> 매도 안 함


# ── _check_htf_setup / _check_breakout 단위 테스트 ───────────────────

@pytest.mark.asyncio
async def test_check_htf_setup_no_pattern(htf_scan_setup):
    """_check_htf_setup: _detect_pole_and_flag가 None을 반환하면 즉시 종료."""
    strategy, sqs, universe, tm, logger = htf_scan_setup
    watchlist = await universe.get_watchlist()
    item = watchlist.get("005930")

    # OHLCV는 정상적으로 조회되나, 패턴 감지에는 실패하는 데이터 (surge_ratio < 1.9)
    ohlcv = _make_ohlcv_pole_and_flag(pole_start_price=8000, pole_end_price=10000)
    sqs.get_recent_daily_ohlcv.return_value = MagicMock(rt_cd="0", data=ohlcv)

    # _detect_pole_and_flag가 None을 반환하므로 _check_breakout은 호출되지 않아야 함
    sqs.get_current_price.reset_mock()

    signal = await strategy._check_htf_setup("005930", item, 0.5)

    assert signal is None
    sqs.get_current_price.assert_not_called()


@pytest.fixture
def breakout_setup(htf_scan_setup):
    """_check_breakout 메서드 테스트를 위한 공통 설정."""
    strategy, sqs, _, _, _ = htf_scan_setup

    code = "005930"
    item = MagicMock()
    item.name = "테스트종목"
    pattern = {
        "pole_high": 10000,
        "surge_ratio": 2.0,
        "flag_days": 15,
    }
    # 50일치 데이터, 평균 거래량 100,000
    ohlcv = [{"volume": 100000} for _ in range(50)]
    progress = 0.5  # 50%

    return strategy, sqs, code, item, pattern, ohlcv, progress


@pytest.mark.asyncio
async def test_check_breakout_no_price_output(breakout_setup):
    """_check_breakout: 현재가 API 응답에 'output'이 없으면 None 반환."""
    strategy, sqs, code, item, pattern, ohlcv, progress = breakout_setup

    sqs.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": None}
    )

    signal = await strategy._check_breakout(code, item, pattern, ohlcv, progress)
    assert signal is None


@pytest.mark.asyncio
async def test_check_breakout_current_price_zero(breakout_setup):
    """_check_breakout: 현재가가 0이면 None 반환."""
    strategy, sqs, code, item, pattern, ohlcv, progress = breakout_setup

    sqs.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {"stck_prpr": "0", "acml_vol": "100000"}}
    )

    signal = await strategy._check_breakout(code, item, pattern, ohlcv, progress)
    assert signal is None


@pytest.mark.asyncio
async def test_check_breakout_price_output_is_object(breakout_setup):
    """_check_breakout: 현재가 API 응답의 output이 객체일 때도 정상 처리."""
    strategy, sqs, code, item, pattern, ohlcv, progress = breakout_setup

    class MockPriceOutput:
        stck_prpr = "10500"
        acml_vol = "600000"

    sqs.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": MockPriceOutput()}
    )
    sqs.get_stock_conclusion.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": [{"tday_rltv": "130.0"}]}
    )

    signal = await strategy._check_breakout(code, item, pattern, ohlcv, progress)
    assert signal is not None
    assert signal.action == "BUY"


@pytest.mark.asyncio
async def test_check_breakout_conclusion_empty_list(breakout_setup):
    """_check_breakout: 체결강도 API 응답의 output 리스트가 비어있으면 None 반환."""
    strategy, sqs, code, item, pattern, ohlcv, progress = breakout_setup

    sqs.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {"stck_prpr": "10500", "acml_vol": "600000"}}
    )
    sqs.get_stock_conclusion.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": []}
    )

    signal = await strategy._check_breakout(code, item, pattern, ohlcv, progress)
    assert signal is None


@pytest.mark.asyncio
async def test_check_breakout_conclusion_no_tday_rltv(breakout_setup):
    """_check_breakout: 체결강도 응답에 'tday_rltv' 필드가 없으면 None 반환."""
    strategy, sqs, code, item, pattern, ohlcv, progress = breakout_setup

    sqs.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {"stck_prpr": "10500", "acml_vol": "600000"}}
    )
    sqs.get_stock_conclusion.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": [{"some_other_key": "130.0"}]}
    )

    signal = await strategy._check_breakout(code, item, pattern, ohlcv, progress)
    assert signal is None


@pytest.mark.asyncio
async def test_check_breakout_early_market_volume_defense(breakout_setup):
    """_check_breakout: 장 초반(progress < 0.05) 거래량 계산 시 effective_progress(0.05) 적용 검증."""
    strategy, sqs, code, item, pattern, ohlcv, progress = breakout_setup

    progress = 0.01

    sqs.get_stock_conclusion.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": [{"tday_rltv": "130.0"}]}
    )

    # Case 1: 거래량 부족 (proj_vol = 9900 / 0.05 = 198,000 < 200,000)
    sqs.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {"stck_prpr": "10500", "acml_vol": "9900"}}
    )
    signal = await strategy._check_breakout(code, item, pattern, ohlcv, progress)
    assert signal is None

    # Case 2: 거래량 충분 (proj_vol = 10000 / 0.05 = 200,000 >= 200,000)
    sqs.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {"stck_prpr": "10500", "acml_vol": "10000"}}
    )
    signal = await strategy._check_breakout(code, item, pattern, ohlcv, progress)
    assert signal is not None
    assert signal.action == "BUY"


# ── check_exits() Edge Cases ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_check_exits_incomplete_holding_info(mock_deps):
    """check_exits: holdings 리스트의 항목에 code나 buy_price가 없으면 스킵."""
    sqs, universe, tm, logger = mock_deps
    strategy = HighTightFlagStrategy(sqs, universe, tm, logger=logger)

    holdings = [
        {"name": "No Code", "buy_price": 10000},
        {"code": "005930", "name": "No Buy Price"}
    ]

    signals = await strategy.check_exits(holdings)

    assert len(signals) == 0
    sqs.get_current_price.assert_not_called()


@pytest.mark.asyncio
async def test_check_exits_invalid_price_data(mock_deps):
    """check_exits: 현재가 API 응답이 비정상이면 스킵."""
    sqs, universe, tm, logger = mock_deps
    strategy = HighTightFlagStrategy(sqs, universe, tm, logger=logger)
    strategy._save_state = MagicMock()
    strategy._position_state["005930"] = HTFPositionState(10000, "20250101", 10000, 10000)
    holdings = [{"code": "005930", "buy_price": 10000, "name": "테스트종목"}]

    # Case 1: output is None
    sqs.get_current_price.return_value = ResCommonResponse(rt_cd="0", msg1="OK", data={"output": None})
    signals = await strategy.check_exits(holdings)
    assert len(signals) == 0

    # Case 2: current price is 0
    sqs.get_current_price.return_value = ResCommonResponse(rt_cd="0", msg1="OK", data={"output": {"stck_prpr": "0"}})
    signals = await strategy.check_exits(holdings)
    assert len(signals) == 0
