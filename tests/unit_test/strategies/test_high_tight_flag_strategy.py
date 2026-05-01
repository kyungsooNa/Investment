# tests/unit_test/test_high_tight_flag_strategy.py
import pytest
from unittest.mock import MagicMock, AsyncMock
from datetime import datetime
from common.types import ResCommonResponse
from strategies.high_tight_flag_strategy import HighTightFlagStrategy
from strategies.oneil_common_types import HTFConfig, HTFPositionState, OSBWatchlistItem
from services.stock_query_service import StockQueryService
from services.oneil_universe_service import OneilUniverseService
from core.market_clock import MarketClock


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

# _save_state_async 파일 I/O 차단 (파일 I/O 전용 TC 제외)
_FILE_IO_TESTS = {"test_state_persistence", "test_save_state_async_logs_error"}

@pytest.fixture(autouse=True)
def _block_async_file_io(monkeypatch, request):
    """check_exits에서 _save_state_async 호출 시 파일 I/O 방지."""
    if request.node.name in _FILE_IO_TESTS:
        yield
        return
    monkeypatch.setattr(HighTightFlagStrategy, "_load_state", MagicMock())
    monkeypatch.setattr(HighTightFlagStrategy, "_save_state_async", AsyncMock())
    yield


@pytest.fixture
def mock_deps():
    sqs = MagicMock(spec=StockQueryService)
    universe = MagicMock(spec=OneilUniverseService)
    tm = MagicMock(spec=MarketClock)
    logger = MagicMock()

    sqs.get_current_price = AsyncMock(spec=StockQueryService.get_current_price)
    sqs.get_stock_conclusion = AsyncMock(spec=StockQueryService.get_stock_conclusion)
    sqs.get_recent_daily_ohlcv = AsyncMock(spec=StockQueryService.get_recent_daily_ohlcv)
    universe.get_watchlist = AsyncMock(spec=OneilUniverseService.get_watchlist)
    universe.is_market_timing_ok = AsyncMock(spec=OneilUniverseService.is_market_timing_ok)

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
        """전체 데이터가 flag_min_days(5)개 이하 → search_end <= 0 → None."""
        strategy = self._make_strategy(mock_deps)
        # n=5, search_end = 5 - 5 = 0 → 즉시 None (peak 탐색 범위 없음)
        ohlcv = _make_ohlcv_pole_and_flag(
            pole_days=4, pole_start_price=5000, pole_end_price=10000,
            flag_days=1, flag_drawdown_pct=10.0,
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
    sqs.get_recent_daily_ohlcv.return_value = ResCommonResponse(rt_cd="0", msg1="OK", data=ohlcv)

    # 현재가: 10150 (밴드 내: min=10050, max=10200) + 거래량 대량
    sqs.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {
            "stck_prpr": "10150",        # 가격 돌파 밴드 내 (+1.5%)
            "stck_hgpr": "10160",        # 캔들 품질: (10150-10050)/(10160-10050)=0.91 (0.7↑ 통과)
            "stck_lwpr": "10050",
            "acml_vol": "700000",        # 거래량: 충분 (200%↑ 통과)
            "pgtr_ntby_qty": "200000",   # 정석 수급: 20.3억/60억=33.8% ≥ 10% & 0.406% ≥ 0.3%
            "acml_tr_pbmn": "6000000000"
        }}
    )

    # 체결강도 151%
    sqs.get_stock_conclusion.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": [{"tday_rltv": "151.0"}]}
    )

    signals = await strategy.scan()

    assert len(signals) == 1
    assert signals[0].code == "005930"
    assert signals[0].action == "BUY"
    assert "HTF돌파" in signals[0].reason
    assert "강도 151.0%" in signals[0].reason
    assert "정석" in signals[0].reason


@pytest.mark.asyncio
async def test_scan_no_breakout_price(htf_scan_setup):
    """scan: 현재가 <= 40일 최고가 → 시그널 없음."""
    strategy, sqs, _, _, _ = htf_scan_setup

    ohlcv = _make_ohlcv_pole_and_flag(
        pole_days=25, pole_start_price=5000, pole_end_price=10000,
        flag_days=18, flag_drawdown_pct=10.0,
        pole_volume=500000, flag_volume=100000,
    )
    sqs.get_recent_daily_ohlcv.return_value = ResCommonResponse(rt_cd="0", msg1="OK", data=ohlcv)

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
    sqs.get_recent_daily_ohlcv.return_value = ResCommonResponse(rt_cd="0", msg1="OK", data=ohlcv)

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
    sqs.get_recent_daily_ohlcv.return_value = ResCommonResponse(rt_cd="0", msg1="OK", data=ohlcv)

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
async def test_scan_poor_candle_quality(htf_scan_setup):
    """scan: 캔들 품질(relative_pos < 0.7) 미달 → 시그널 없음."""
    strategy, sqs, _, _, _ = htf_scan_setup

    ohlcv = _make_ohlcv_pole_and_flag(
        pole_days=25, pole_start_price=5000, pole_end_price=10000,
        flag_days=18, flag_drawdown_pct=10.0,
        pole_volume=500000, flag_volume=100000,
    )
    sqs.get_recent_daily_ohlcv.return_value = ResCommonResponse(rt_cd="0", msg1="OK", data=ohlcv)

    # 현재가 10500으로 가격 돌파는 통과하지만 relative_pos = (10500-10000)/(12000-10000) = 0.25 < 0.7
    sqs.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {
            "stck_prpr": "10500",
            "stck_hgpr": "12000",  # 고가
            "stck_lwpr": "10000",  # 저가 → relative_pos = 0.25 (< 0.7 거부)
            "acml_vol": "700000",
            "pgtr_ntby_qty": "100000",
            "acml_tr_pbmn": "6000000000",
        }}
    )
    sqs.get_stock_conclusion.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": [{"tday_rltv": "151.0"}]}
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
    """check_exits: 5일 MA 하향이탈 → 트레일링스탑."""
    sqs, universe, tm, logger = mock_deps
    strategy = HighTightFlagStrategy(sqs, universe, tm, logger=logger)
    strategy._save_state = MagicMock()

    strategy._position_state["005930"] = HTFPositionState(
        entry_price=10000, entry_date="20250101", peak_price=12000, pole_high=11500,
    )

    # 현재가 10500 (PnL +5%, 손절 아님)
    # pole_high=11500 → grace 기준 11500*0.99=11385 > 10500 → grace 미적용 → stop 발동
    sqs.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {"stck_prpr": "10500"}}
    )

    # 5일 OHLCV: 종가 11000 일정 → 5MA = 11000, 현재가 10500 < 11000
    ohlcv = [{"close": 11000, "volume": 100000} for _ in range(5)]
    sqs.get_recent_daily_ohlcv.return_value = ResCommonResponse(rt_cd="0", msg1="OK", data=ohlcv)

    holdings = [{"code": "005930", "buy_price": 10000, "qty": 30, "name": "테스트종목"}]
    signals = await strategy.check_exits(holdings)

    assert len(signals) == 1
    assert signals[0].action == "SELL"
    assert signals[0].qty == 30  # 전량 매도
    assert "트레일링스탑" in signals[0].reason
    assert "5MA" in signals[0].reason


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
    sqs.get_recent_daily_ohlcv.return_value = ResCommonResponse(rt_cd="0", msg1="OK", data=ohlcv)

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

    # 기본: 1000만 * 5% * 0.5(HTF 비중 절반) = 25만, 25만 / 10000 = 25주
    assert strategy._calculate_qty(10000) == 25
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
    sqs.get_recent_daily_ohlcv.return_value = ResCommonResponse(rt_cd="0", msg1="OK", data=ohlcv)

    signals = await strategy.scan()
    assert len(signals) == 0


@pytest.mark.asyncio
async def test_scan_current_price_api_failure(htf_scan_setup):
    """scan: 현재가 조회 실패 시 스킵."""
    strategy, sqs, _, _, _ = htf_scan_setup

    # OHLCV는 정상 (패턴 감지 성공 유도)
    ohlcv = _make_ohlcv_pole_and_flag()
    sqs.get_recent_daily_ohlcv.return_value = ResCommonResponse(rt_cd="0", msg1="OK", data=ohlcv)

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
    sqs.get_recent_daily_ohlcv.return_value = ResCommonResponse(rt_cd="0", msg1="OK", data=ohlcv)

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
    sqs.get_recent_daily_ohlcv.return_value = ResCommonResponse(rt_cd="0", msg1="OK", data=ohlcv)

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

@pytest.mark.asyncio
async def test_check_exits_signal_name_fallback(mock_deps):
    """매도 시그널 생성 시 holdings의 name이 TradeSignal에 정상 반영되는지 검증."""
    sqs, universe, tm, logger = mock_deps
    strategy = HighTightFlagStrategy(sqs, universe, tm, logger=logger)
    strategy._save_state = MagicMock()
    
    sqs.get_current_price.return_value = ResCommonResponse(rt_cd="0", msg1="OK", data={"output": {"stck_prpr": "9400"}})
    holdings = [{"code": "005930", "buy_price": 10000, "name": "기존이름"}]
    signals = await strategy.check_exits(holdings)
    
    assert len(signals) == 1
    assert signals[0].name == "기존이름"

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

    # OHLCV 데이터가 5MA 기준(5일) 미만 (4일) → 데이터 부족
    ohlcv = [{"close": 11000} for _ in range(4)]
    sqs.get_recent_daily_ohlcv.return_value = ResCommonResponse(rt_cd="0", msg1="OK", data=ohlcv)

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
    sqs.get_recent_daily_ohlcv.return_value = ResCommonResponse(rt_cd="0", msg1="OK", data=ohlcv)

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
    item.market_cap = 500_000_000_000  # 시가총액 5000억 추가
    pattern = {
        "pole_high": 10000,
        "surge_ratio": 2.0,
        "flag_days": 15,
        "drawdown_pct": 10.0,
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

    # 1. 시가총액 설정 (Standard 판정 기준인 0.3% 계산용)
    item.market_cap = 500_000_000_000 # 5000억

    # 2. Mock 객체 보강 (필수 필드 모두 추가)
    class MockPriceOutput:
        stck_prpr = "10150"          # 현재가 (밴드 내: min=10050, max=10200)
        acml_vol = "800000"          # 거래량 (평균 10만 대비 충분히 높게)
        stck_hgpr = "10160"          # 고가 (캔들 품질: (10150-10050)/(10160-10050)=0.91)
        stck_lwpr = "10050"          # 저가
        # 정석 기준: pg_buy=150000*10150=1.522B, pg_to_tv=15.2% ≥10% ✓, pg_to_mc=0.304% ≥0.3% ✓
        pgtr_ntby_qty = "150000"     # 프로그램 매수 (정석 수급 기준 통과)
        acml_tr_pbmn = "10000000000" # 거래대금 100억 (프로그램 비중 15.2%로 10% 초과)

    sqs.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": MockPriceOutput()}
    )
    
    # 체결강도 130.0% (Standard 수급 기준을 만족하므로 120%만 넘으면 통과)
    sqs.get_stock_conclusion.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": [{"tday_rltv": "130.0"}]}
    )

    signal = await strategy._check_breakout(code, item, pattern, ohlcv, progress)
    
    assert signal is not None
    assert signal.action == "BUY"
    assert "정석" in signal.reason # Standard 판정 확인


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
    """_check_breakout: 장 초반(progress < 0.05) 거래량 계산 시 effective_progress(0.05) 적용 검증.
    progress=0.04: 장초반 가드(0.04*390=15.6>=15) 통과하지만 effective_progress=max(0.04,0.05)=0.05 적용."""
    strategy, sqs, code, item, pattern, ohlcv, progress = breakout_setup

    progress = 0.04
    item.market_cap = 500_000_000_000

    # 오전 시각 고정 (11시): 오후 가중치(3x) 미적용 → 2x 허들 적용
    from datetime import datetime
    strategy._tm.get_current_kst_time.return_value = datetime(2025, 1, 1, 11, 0, 0)

    sqs.get_stock_conclusion.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": [{"tday_rltv": "130.0"}]}
    )

    # Case 1: 거래량 부족 (proj_vol = 9900 / 0.05 = 198,000 < avg*2.0=200,000)
    # 가격·캔들 품질 통과 후 거래량에서 거부
    sqs.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {
            "stck_prpr": "10150", "acml_vol": "9900",
            "stck_hgpr": "10160", "stck_lwpr": "10050",
        }}
    )
    signal = await strategy._check_breakout(code, item, pattern, ohlcv, progress)
    assert signal is None

    # Case 2: 거래량 충분 (proj_vol = 10000 / 0.05 = 200,000 >= avg*2.0=200,000)
    # 정석 수급: pg_buy=200000*10150=2.03B, pg_to_tv=2.03B/6B=33.8% ≥10% ✓, pg_to_mc=0.406% ≥0.3% ✓
    sqs.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {
            "stck_prpr": "10150",
            "stck_hgpr": "10160", "stck_lwpr": "10050",
            "acml_vol": "10000",
            "pgtr_ntby_qty": "200000",
            "acml_tr_pbmn": "6000000000",
        }}
    )

    sqs.get_stock_conclusion.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": [{"tday_rltv": "130.0"}]}
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


@pytest.mark.asyncio
async def test_scan_logs_candidate_exception(htf_scan_setup):
    strategy, _, _, _, logger = htf_scan_setup
    strategy._check_htf_setup = AsyncMock(side_effect=RuntimeError("boom"))

    signals = await strategy.scan()

    assert signals == []
    logger.error.assert_called_once()


def test_detect_pole_and_flag_rejects_zero_pole_low(mock_deps):
    sqs, universe, tm, logger = mock_deps
    strategy = HighTightFlagStrategy(sqs, universe, tm, logger=logger)
    ohlcv = _make_ohlcv_pole_and_flag()
    for row in ohlcv[:25]:
        row["low"] = 0

    assert strategy._detect_pole_and_flag(ohlcv) is None


def test_detect_pole_and_flag_rejects_zero_average_volume(mock_deps):
    sqs, universe, tm, logger = mock_deps
    strategy = HighTightFlagStrategy(sqs, universe, tm, logger=logger)
    ohlcv = _make_ohlcv_pole_and_flag(pole_volume=0, flag_volume=0)

    assert strategy._detect_pole_and_flag(ohlcv) is None


@pytest.mark.asyncio
async def test_check_breakout_early_morning_guard(breakout_setup):
    strategy, sqs, code, item, pattern, ohlcv, _ = breakout_setup
    sqs.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {"stck_prpr": "10500"}}
    )

    signal = await strategy._check_breakout(code, item, pattern, ohlcv, progress=0.03)

    assert signal is None
    sqs.get_stock_conclusion.assert_not_called()


@pytest.mark.asyncio
async def test_check_breakout_rejects_smart_money_filter(breakout_setup):
    strategy, sqs, code, item, pattern, ohlcv, progress = breakout_setup
    sqs.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {
            "stck_prpr": "10500",
            "stck_hgpr": "10510",
            "stck_lwpr": "10400",
            "acml_vol": "800000",
            "pgtr_ntby_qty": "0",
            "acml_tr_pbmn": "10000000000",
        }}
    )
    sqs.get_stock_conclusion.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": [{"tday_rltv": "151.0"}]}
    )

    signal = await strategy._check_breakout(code, item, pattern, ohlcv, progress)

    assert signal is None


@pytest.mark.asyncio
async def test_check_exits_empty_holdings(mock_deps):
    sqs, universe, tm, logger = mock_deps
    strategy = HighTightFlagStrategy(sqs, universe, tm, logger=logger)

    assert await strategy.check_exits([]) == []


@pytest.mark.asyncio
async def test_check_exits_logs_single_exit_exception(mock_deps):
    sqs, universe, tm, logger = mock_deps
    strategy = HighTightFlagStrategy(sqs, universe, tm, logger=logger)
    strategy._check_single_exit = AsyncMock(side_effect=RuntimeError("exit boom"))

    signals = await strategy.check_exits([{"code": "005930", "buy_price": 10000}])

    assert signals == []
    logger.error.assert_called_once()


@pytest.mark.asyncio
async def test_check_exits_partial_profit_sell(mock_deps):
    sqs, universe, tm, logger = mock_deps
    strategy = HighTightFlagStrategy(sqs, universe, tm, logger=logger)
    strategy._position_state["005930"] = HTFPositionState(10000, "20250101", 11000, 9500)
    sqs.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {"stck_prpr": "12100"}}
    )

    signals = await strategy.check_exits([{"code": "005930", "buy_price": 10000, "qty": 10}])

    assert len(signals) == 1
    assert signals[0].qty == 5
    state = strategy._position_state["005930"]
    assert state.last_partial_sell_price == 12100
    assert state.breakeven_armed is True


@pytest.mark.asyncio
async def test_check_exits_partial_profit_full_sell_when_qty_one(mock_deps):
    sqs, universe, tm, logger = mock_deps
    strategy = HighTightFlagStrategy(sqs, universe, tm, logger=logger)
    strategy._position_state["005930"] = HTFPositionState(10000, "20250101", 10000, 9500)
    sqs.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {"stck_prpr": "12100"}}
    )

    signals = await strategy.check_exits([{"code": "005930", "buy_price": 10000, "qty": 1}])

    assert len(signals) == 1
    assert signals[0].qty == 1


@pytest.mark.asyncio
async def test_check_exits_breakeven_stop_after_partial_profit(mock_deps):
    sqs, universe, tm, logger = mock_deps
    strategy = HighTightFlagStrategy(sqs, universe, tm, logger=logger)
    strategy._position_state["005930"] = HTFPositionState(
        10000, "20250101", 12100, 9500, last_partial_sell_price=12100, breakeven_armed=True
    )
    sqs.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {"stck_prpr": "9900"}}
    )

    signals = await strategy.check_exits([{"code": "005930", "buy_price": 10000, "qty": 3}])

    assert len(signals) == 1
    assert signals[0].qty == 3
    assert "005930" in strategy._cooldown


@pytest.mark.asyncio
async def test_check_trailing_ma_stop_peak_drop(mock_deps):
    sqs, universe, tm, logger = mock_deps
    strategy = HighTightFlagStrategy(sqs, universe, tm, logger=logger)
    state = HTFPositionState(10000, "20250101", 12000, 9500)
    sqs.get_recent_daily_ohlcv.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data=[{"close": 10000} for _ in range(5)]
    )

    is_break, reason = await strategy._check_trailing_ma_stop("005930", 11000, state)

    assert is_break is True
    assert "고점" in reason


@pytest.mark.asyncio
async def test_check_trailing_ma_stop_ignores_missing_closes(mock_deps):
    sqs, universe, tm, logger = mock_deps
    strategy = HighTightFlagStrategy(sqs, universe, tm, logger=logger)
    state = HTFPositionState(10000, "20250101", 12000, 9500)
    sqs.get_recent_daily_ohlcv.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data=[{"close": 0} for _ in range(5)]
    )

    assert await strategy._check_trailing_ma_stop("005930", 11000, state) == (False, "")


@pytest.mark.asyncio
async def test_check_single_exit_price_output_object(mock_deps):
    sqs, universe, tm, logger = mock_deps
    strategy = HighTightFlagStrategy(sqs, universe, tm, logger=logger)
    strategy._position_state["005930"] = HTFPositionState(10000, "20250101", 10000, 9500)

    class PriceOutput:
        stck_prpr = "9400"

    sqs.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": PriceOutput()}
    )

    signals, dirty = await strategy._check_single_exit({"code": "005930", "buy_price": 10000, "qty": 2})

    assert dirty is True
    assert len(signals) == 1
    assert signals[0].qty == 2


def test_smart_money_market_cap_thresholds(mock_deps):
    sqs, universe, tm, logger = mock_deps
    strategy = HighTightFlagStrategy(sqs, universe, tm, logger=logger)

    large_ok, large_metrics = strategy._is_smart_money_ok(
        "000001", current=10000, pg_buy=1100000, trade_value=100_000_000_000,
        market_cap=10_000_000_000_000, cgld_val=130.0
    )
    mid_ok, mid_metrics = strategy._is_smart_money_ok(
        "000002", current=10000, pg_buy=250000, trade_value=20_000_000_000,
        market_cap=1_000_000_000_000, cgld_val=130.0
    )

    assert large_ok is True
    assert large_metrics["mc_threshold"] == 0.1
    assert mid_ok is True
    assert mid_metrics["mc_threshold"] == 0.2


@pytest.mark.asyncio
async def test_load_state_async_missing_file_returns(mock_deps, tmp_path):
    _, _, _, logger = mock_deps
    strategy = object.__new__(HighTightFlagStrategy)
    strategy._logger = logger
    strategy._position_state = {}
    strategy._cooldown = {}
    strategy.STATE_FILE = str(tmp_path / "missing.json")

    await strategy._load_state_async()

    assert strategy._position_state == {}


@pytest.mark.asyncio
async def test_save_state_async_logs_error(mock_deps, monkeypatch):
    _, _, _, logger = mock_deps
    strategy = object.__new__(HighTightFlagStrategy)
    strategy._logger = logger
    strategy._position_state = {}
    strategy._cooldown = {}
    strategy.STATE_FILE = "data/htf_position_state.json"
    monkeypatch.setattr("strategies.high_tight_flag_strategy.asyncio.to_thread", AsyncMock(side_effect=OSError("disk")))

    await strategy._save_state_async()

    logger.error.assert_called()


# ── 필살기 4종 신규 테스트 ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_check_breakout_rejects_below_buffer(breakout_setup):
    """_check_breakout: 현재가가 진입 버퍼(+0.5%) 미달 → None (out_of_entry_band)."""
    strategy, sqs, code, item, pattern, ohlcv, progress = breakout_setup
    # pole_high=10000, min_entry=10050 → 10030은 미달
    sqs.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {
            "stck_prpr": "10030",
            "stck_hgpr": "10035",
            "stck_lwpr": "9900",
            "acml_vol": "800000",
        }}
    )

    signal = await strategy._check_breakout(code, item, pattern, ohlcv, progress)
    assert signal is None


@pytest.mark.asyncio
async def test_check_breakout_rejects_over_extended(breakout_setup):
    """_check_breakout: 현재가가 과확장 캡(+2%) 초과 → None (out_of_entry_band)."""
    strategy, sqs, code, item, pattern, ohlcv, progress = breakout_setup
    # pole_high=10000, max_entry=10200 → 10250은 초과
    sqs.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {
            "stck_prpr": "10250",
            "stck_hgpr": "10260",
            "stck_lwpr": "10200",
            "acml_vol": "800000",
        }}
    )

    signal = await strategy._check_breakout(code, item, pattern, ohlcv, progress)
    assert signal is None


@pytest.mark.asyncio
async def test_check_breakout_accepts_within_entry_band(breakout_setup):
    """_check_breakout: 현재가가 진입 밴드(+0.5%~+2%) 내 → BUY 시그널."""
    strategy, sqs, code, item, pattern, ohlcv, progress = breakout_setup
    # pole_high=10000, current=10150 (밴드 내)
    # 정석 판정: pg_to_tv=10500*150000/50억 ≈ 3.15% (정석 10% 미달) → 유연도 제거됐으므로
    # 정석 통과시키려면 pg_to_tv >= 10% → trade_value를 작게 or pg_buy를 크게
    # pg_to_tv = 10150*600000/6150000000 = 990%: trade_value=6150000000 기준
    item.market_cap = 500_000_000_000  # 5000억 → mc_threshold=0.3%

    sqs.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {
            "stck_prpr": "10150",
            "stck_hgpr": "10160",  # relative_pos=(10150-10050)/(10160-10050)=0.91 ≥ 0.7
            "stck_lwpr": "10050",
            "acml_vol": "800000",  # proj_vol=800000/0.5=1600000, avg=100000, 1600000 ≥ 100000*2 ✓
            "pgtr_ntby_qty": "500000",    # pg_buy_amount=500000*10150=50.75억
            "acml_tr_pbmn": "5000000000", # trade_value=50억 → pg_to_tv=101.5% ≥ 10% ✓
                                          # pg_to_mc=50.75억/5000억=1.015% ≥ 0.3% ✓
        }}
    )
    sqs.get_stock_conclusion.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": [{"tday_rltv": "130.0"}]}
    )

    signal = await strategy._check_breakout(code, item, pattern, ohlcv, progress)
    assert signal is not None
    assert signal.action == "BUY"
    assert "정석" in signal.reason


@pytest.mark.asyncio
async def test_check_breakout_afternoon_volume_threshold(breakout_setup):
    """_check_breakout: 12시 이후 volume_multiplier=3.0 적용 — 기존 2배 거래량은 거부됨."""
    strategy, sqs, code, item, pattern, ohlcv, progress = breakout_setup
    # ohlcv 평균 거래량=100000
    # vol=200000, progress=0.5 → proj_vol=400000
    # 오전(11시): threshold=100000*2.0=200000 → 400000 ≥ 200000 → 통과 가능
    # 오후(13시): threshold=100000*3.0=300000 → 400000 ≥ 300000 → 통과 (여전히 통과)
    # 오후 거부 케이스: vol=100000, progress=0.5 → proj_vol=200000
    # 오전: 200000 ≥ 200000 → 통과 / 오후: 200000 < 300000 → 거부

    item.market_cap = 500_000_000_000

    afternoon_price_data = {
        "stck_prpr": "10150",
        "stck_hgpr": "10160",
        "stck_lwpr": "10050",
        "acml_vol": "100000",   # proj=200000: 오후 threshold(300000) 미달
        "pgtr_ntby_qty": "500000",
        "acml_tr_pbmn": "5000000000",
    }

    # 오후 13시 → 거부
    from datetime import datetime
    strategy._tm.get_current_kst_time.return_value = datetime(2025, 1, 1, 13, 0, 0)
    sqs.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": afternoon_price_data}
    )
    sqs.get_stock_conclusion.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": [{"tday_rltv": "130.0"}]}
    )

    signal_afternoon = await strategy._check_breakout(code, item, pattern, ohlcv, progress)
    assert signal_afternoon is None, "오후에는 거래량 3배 허들 미달 → 거부"

    # 오전 11시 → 통과 가능 (거래량은 2배 허들인 200000 통과)
    strategy._tm.get_current_kst_time.return_value = datetime(2025, 1, 1, 11, 0, 0)
    # 거래량을 2배 허들 통과 수준으로 올림: vol=100001, proj=200002 ≥ 200000
    morning_price_data = {**afternoon_price_data, "acml_vol": "101000"}
    sqs.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": morning_price_data}
    )

    signal_morning = await strategy._check_breakout(code, item, pattern, ohlcv, progress)
    assert signal_morning is not None, "오전에는 거래량 2배 허들 통과 → BUY"


def test_smart_money_flexible_path_removed(mock_deps):
    """_is_smart_money_ok: 유연 판정 제거 확인 — 정석 미달 시 pg_to_tv=8%, cgld=160%여도 거부."""
    sqs, universe, tm, logger = mock_deps
    strategy = HighTightFlagStrategy(sqs, universe, tm, logger=logger)

    # pg_to_tv=8%, cgld=160% → 구버전 유연 판정(7%+150%)으로는 통과했으나
    # 유연 판정 제거 후에는 정석(10% + mc_threshold) 미달 → 거부
    # market_cap=5000억 → mc_threshold=0.3%
    # pg_buy_amount=80주*10000원=80만원, trade_value=1000만원 → pg_to_tv=8%
    # pg_to_mc=80만/5000억 << 0.3% → 정석 미달
    market_cap = 500_000_000_000
    current = 10000
    pg_buy = 80       # 80주
    trade_value = 10_000_000  # 1000만원 → pg_to_tv=8%

    ok, metrics = strategy._is_smart_money_ok(
        "005930", current=current, pg_buy=pg_buy,
        trade_value=trade_value, market_cap=market_cap, cgld_val=160.0
    )

    assert ok is False, "유연 판정 제거 후 정석 미달이면 거부"
    assert metrics.get("pass_type") == "정석", "pass_type은 항상 정석"


@pytest.mark.asyncio
async def test_trailing_ma_stop_grace_when_pole_supported(mock_deps):
    """_check_trailing_ma_stop: MA 하향이탈이지만 pole_high 99% 이상 지지 → 유예 (False, '')."""
    sqs, universe, tm, logger = mock_deps
    strategy = HighTightFlagStrategy(sqs, universe, tm, logger=logger)

    state = HTFPositionState(10000, "20250101", 12000, 10500)  # pole_high=10500
    # current=11000, ma=11500 → MA 이탈
    # pole_high * 0.99 = 10395 → 11000 >= 10395 → 유예
    sqs.get_recent_daily_ohlcv.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data=[{"close": 11500} for _ in range(5)]
    )

    is_break, reason = await strategy._check_trailing_ma_stop("005930", 11000, state)

    assert is_break is False
    assert reason == ""


@pytest.mark.asyncio
async def test_trailing_ma_stop_fires_when_pole_broken(mock_deps):
    """_check_trailing_ma_stop: MA 하향이탈 + pole_high 99% 미달 → 트레일링스탑 발동."""
    sqs, universe, tm, logger = mock_deps
    strategy = HighTightFlagStrategy(sqs, universe, tm, logger=logger)

    state = HTFPositionState(10000, "20250101", 12000, 10500)  # pole_high=10500
    # current=10300, ma=11500 → MA 이탈
    # pole_high * 0.99 = 10395 → 10300 < 10395 → 유예 없음 → 발동
    sqs.get_recent_daily_ohlcv.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data=[{"close": 11500} for _ in range(5)]
    )

    is_break, reason = await strategy._check_trailing_ma_stop("005930", 10300, state)

    assert is_break is True
    assert "트레일링스탑" in reason
