# tests/unit_test/test_traditional_volume_breakout.py
import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock, AsyncMock, patch
from datetime import datetime, timedelta

import pytz

from common.types import ErrorCode, ResCommonResponse
from strategies.traditional_volume_breakout_strategy import (
    TraditionalVolumeBreakoutStrategy,
    TraditionalVBConfig,
    WatchlistItem,
    PositionState,
)


class TestTraditionalVolumeBreakout(unittest.IsolatedAsyncioTestCase):

    def _make_strategy(self, **config_overrides):
        ts = MagicMock()
        ts.get_top_trading_value_stocks = AsyncMock()
        ts.get_recent_daily_ohlcv = AsyncMock()

        sqs = MagicMock()
        sqs.handle_get_current_stock_price = AsyncMock()

        mapper = MagicMock()
        mapper.is_kosdaq.return_value = True
        mapper.get_name_by_code.return_value = "테스트종목"

        kst = pytz.timezone("Asia/Seoul")
        tm = MagicMock()
        now = kst.localize(datetime(2026, 2, 25, 11, 0))
        open_time = kst.localize(datetime(2026, 2, 25, 9, 0))
        close_time = kst.localize(datetime(2026, 2, 25, 15, 30))
        tm.get_current_kst_time.return_value = now
        tm.get_market_open_time.return_value = open_time
        tm.get_market_close_time.return_value = close_time

        defaults = dict(
            total_portfolio_krw=10_000_000,
            position_size_pct=5.0,
            min_qty=1,
            min_avg_trading_value_5d=10_000_000_000,
            near_high_pct=3.0,
            volume_breakout_multiplier=1.5,
            stop_loss_pct=-3.0,
            trailing_stop_pct=5.0,
        )
        defaults.update(config_overrides)
        config = TraditionalVBConfig(**defaults)

        logger = MagicMock()

        strategy = TraditionalVolumeBreakoutStrategy(
            trading_service=ts,
            stock_query_service=sqs,
            stock_code_mapper=mapper,
            time_manager=tm,
            config=config,
            logger=logger,
        )
        # Clear position state to avoid cross-test pollution
        strategy._position_state = {}
        return strategy, ts, sqs, tm, mapper, logger

    def _make_ohlcv(self, days=20, base_close=10000, base_high=10500, base_vol=500000):
        """20일치 OHLCV mock 데이터 생성."""
        rows = []
        for i in range(days):
            rows.append({
                "date": f"2026020{i+1:02d}",
                "open": base_close - 100,
                "high": base_high,
                "low": base_close - 200,
                "close": base_close,
                "volume": base_vol,
            })
        return rows

    # ── 이름 ──

    def test_name(self):
        strategy, *_ = self._make_strategy()
        self.assertEqual(strategy.name, "거래량돌파(전통)")

    # ── 포지션 사이징 ──

    def test_calculate_qty_normal(self):
        """포트폴리오 1000만 × 5% = 50만 / 주가 10000 = 50주."""
        strategy, *_ = self._make_strategy(total_portfolio_krw=10_000_000, position_size_pct=5.0, use_fixed_qty=False)
        self.assertEqual(strategy._calculate_qty(10000), 50)

    def test_calculate_qty_expensive_stock(self):
        """고가주: 50만 / 60만 = 0 → min_qty=1."""
        strategy, *_ = self._make_strategy(total_portfolio_krw=10_000_000, position_size_pct=5.0, use_fixed_qty=False)
        self.assertEqual(strategy._calculate_qty(600000), 1)

    def test_calculate_qty_zero_price(self):
        """가격 0일 때 min_qty 반환."""
        strategy, *_ = self._make_strategy(use_fixed_qty=False)
        self.assertEqual(strategy._calculate_qty(0), 1)

    def test_calculate_qty_low_pct(self):
        """position_size_pct=1.0이면 1000만×1%=10만 / 10000=10주."""
        strategy, *_ = self._make_strategy(total_portfolio_krw=10_000_000, position_size_pct=1.0, use_fixed_qty=False)
        self.assertEqual(strategy._calculate_qty(10000), 10)

    # ── 워치리스트 빌드 ──

    async def test_build_watchlist_filters_kospi(self):
        """코스피 종목은 워치리스트에서 제외."""
        strategy, ts, sqs, tm, mapper, _ = self._make_strategy()

        ts.get_top_trading_value_stocks.return_value = ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value, msg1="OK",
            data=[{"mksc_shrn_iscd": "005930", "hts_kor_isnm": "삼성전자"}]
        )
        mapper.is_kosdaq.return_value = False  # 코스피

        await strategy._build_watchlist()
        self.assertEqual(len(strategy._watchlist), 0)

    async def test_build_watchlist_filters_low_trading_value(self):
        """5일 평균 거래대금 100억 미만 종목 제외."""
        strategy, ts, sqs, tm, mapper, _ = self._make_strategy()

        ts.get_top_trading_value_stocks.return_value = ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value, msg1="OK",
            data=[{"mksc_shrn_iscd": "123456", "hts_kor_isnm": "소형주"}]
        )
        # 거래량 1000 × 종가 10000 = 거래대금 1000만 (100억 미만)
        ts.get_recent_daily_ohlcv.return_value = self._make_ohlcv(
            days=20, base_close=10000, base_vol=1000
        )

        await strategy._build_watchlist()
        self.assertEqual(len(strategy._watchlist), 0)

    async def test_build_watchlist_filters_below_ma(self):
        """전일 종가가 20일 MA 이하인 종목 제외."""
        strategy, ts, sqs, tm, mapper, _ = self._make_strategy()

        ts.get_top_trading_value_stocks.return_value = ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value, msg1="OK",
            data=[{"mksc_shrn_iscd": "123456", "hts_kor_isnm": "하락종목"}]
        )
        # 종가가 평균보다 낮은 데이터: 마지막 종가를 낮게
        ohlcv = self._make_ohlcv(days=20, base_close=20000, base_high=21000, base_vol=1_000_000)
        ohlcv[-1]["close"] = 15000  # 전일 종가 < 20일 MA(~20000)

        ts.get_recent_daily_ohlcv.return_value = ohlcv

        await strategy._build_watchlist()
        self.assertEqual(len(strategy._watchlist), 0)

    async def test_build_watchlist_filters_far_from_high(self):
        """전일 종가가 20일 최고가의 3% 이상 떨어진 종목 제외."""
        strategy, ts, sqs, tm, mapper, _ = self._make_strategy()

        ts.get_top_trading_value_stocks.return_value = ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value, msg1="OK",
            data=[{"mksc_shrn_iscd": "123456", "hts_kor_isnm": "먼종목"}]
        )
        # 최고가 30000, 종가 28000 → 거리 6.7% > 3%
        ohlcv = self._make_ohlcv(days=20, base_close=28000, base_high=30000, base_vol=1_000_000)
        ts.get_recent_daily_ohlcv.return_value = ohlcv

        await strategy._build_watchlist()
        self.assertEqual(len(strategy._watchlist), 0)

    async def test_build_watchlist_success(self):
        """모든 조건 충족 시 워치리스트에 추가."""
        strategy, ts, sqs, tm, mapper, _ = self._make_strategy()

        ts.get_top_trading_value_stocks.return_value = ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value, msg1="OK",
            data=[{"mksc_shrn_iscd": "123456", "hts_kor_isnm": "좋은종목"}]
        )
        # 종가 29500, 최고가 30000 (1.7% 이내), 거래대금 = 1M × 29500 = 295억 > 100억
        # 전일 종가가 MA보다 높도록 이전 종가들을 낮게 설정
        ohlcv = self._make_ohlcv(days=20, base_close=29000, base_high=30000, base_vol=1_000_000)
        ohlcv[-1]["close"] = 29500  # 전일 종가를 MA(~29000)보다 높게
        ts.get_recent_daily_ohlcv.return_value = ohlcv

        await strategy._build_watchlist()
        self.assertEqual(len(strategy._watchlist), 1)
        self.assertIn("123456", strategy._watchlist)

    # ── 워치리스트 캐싱 ──

    async def test_watchlist_cached_same_day(self):
        """같은 날 두 번 scan해도 워치리스트 빌드는 1회만."""
        strategy, ts, sqs, tm, mapper, _ = self._make_strategy()

        ts.get_top_trading_value_stocks.return_value = ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data=[]
        )
        sqs.handle_get_current_stock_price.return_value = None

        await strategy.scan()
        await strategy.scan()

        # get_top_trading_value_stocks는 1회만 호출
        self.assertEqual(ts.get_top_trading_value_stocks.call_count, 1)

    # ── 매수 시그널 ──

    async def test_scan_buy_signal_price_and_volume_breakout(self):
        """가격+거래량 돌파 AND 조건 충족 시 BUY 시그널."""
        strategy, ts, sqs, tm, mapper, _ = self._make_strategy(use_fixed_qty=False)

        # 워치리스트 수동 설정
        strategy._watchlist = {
            "123456": WatchlistItem(
                code="123456", name="돌파종목",
                high_20d=10000, ma_20d=9500.0,
                avg_vol_20d=500000, avg_trading_value_5d=20_000_000_000,
            )
        }
        strategy._watchlist_date = "20260225"

        # 현재가 10500 (> 10000 돌파), 누적거래량 500000
        # 장 경과 50% → 예상거래량 1000000 >= 500000*1.5=750000
        sqs.handle_get_current_stock_price.return_value = ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value, msg1="OK",
            data={"price": "10500", "acml_vol": "500000"}
        )

        signals = await strategy.scan()
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].action, "BUY")
        self.assertEqual(signals[0].code, "123456")
        self.assertGreater(signals[0].qty, 1)  # 포지션 사이징 적용

    async def test_scan_no_signal_price_not_broken(self):
        """가격 미돌파 시 시그널 없음."""
        strategy, ts, sqs, tm, mapper, _ = self._make_strategy()

        strategy._watchlist = {
            "123456": WatchlistItem(
                code="123456", name="미돌파",
                high_20d=10000, ma_20d=9500.0,
                avg_vol_20d=500000, avg_trading_value_5d=20_000_000_000,
            )
        }
        strategy._watchlist_date = "20260225"

        sqs.handle_get_current_stock_price.return_value = ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value, msg1="OK",
            data={"price": "9800", "acml_vol": "1000000"}
        )

        signals = await strategy.scan()
        self.assertEqual(len(signals), 0)

    async def test_scan_no_signal_volume_insufficient(self):
        """가격은 돌파했지만 거래량 부족 시 시그널 없음."""
        strategy, ts, sqs, tm, mapper, _ = self._make_strategy()

        strategy._watchlist = {
            "123456": WatchlistItem(
                code="123456", name="거래량부족",
                high_20d=10000, ma_20d=9500.0,
                avg_vol_20d=500000, avg_trading_value_5d=20_000_000_000,
            )
        }
        strategy._watchlist_date = "20260225"

        # 현재가 10500 (돌파), 거래량 100000
        # 장 경과 50% → 예상거래량 200000 < 750000
        sqs.handle_get_current_stock_price.return_value = ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value, msg1="OK",
            data={"price": "10500", "acml_vol": "100000"}
        )

        signals = await strategy.scan()
        self.assertEqual(len(signals), 0)

    async def test_scan_creates_position_state(self):
        """매수 시그널 생성 시 포지션 상태가 기록됨."""
        strategy, ts, sqs, tm, mapper, _ = self._make_strategy()

        strategy._watchlist = {
            "123456": WatchlistItem(
                code="123456", name="돌파종목",
                high_20d=10000, ma_20d=9500.0,
                avg_vol_20d=500000, avg_trading_value_5d=20_000_000_000,
            )
        }
        strategy._watchlist_date = "20260225"

        sqs.handle_get_current_stock_price.return_value = ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value, msg1="OK",
            data={"price": "10500", "acml_vol": "500000"}
        )

        await strategy.scan()

        self.assertIn("123456", strategy._position_state)
        self.assertEqual(strategy._position_state["123456"].breakout_level, 10000)
        self.assertEqual(strategy._position_state["123456"].peak_price, 10500)

    async def test_scan_skips_already_held_stock(self):
        """이미 보유 중인 종목은 스캔 대상에서 제외."""
        strategy, ts, sqs, tm, mapper, _ = self._make_strategy()

        # 워치리스트 설정
        strategy._watchlist = {
            "005930": WatchlistItem(
                code="005930", name="삼성전자",
                high_20d=70000, ma_20d=68000,
                avg_vol_20d=1000000, avg_trading_value_5d=50_000_000_000,
            )
        }
        strategy._watchlist_date = "20260225"

        # 이미 보유 중으로 설정
        strategy._position_state["005930"] = PositionState(breakout_level=70000, peak_price=71000)

        # 실행
        signals = await strategy.scan()

        # 검증
        self.assertEqual(len(signals), 0)
        # 보유 중이면 시세 조회를 하지 않아야 함
        sqs.handle_get_current_stock_price.assert_not_called()

    # ── 매도 시그널 ──
    async def test_check_exits_stop_loss(self):
        """진입가 대비 -3% 이하 시 손절 SELL."""
        strategy, ts, sqs, tm, mapper, _ = self._make_strategy()
        strategy._position_state["005930"] = PositionState(breakout_level=10000, peak_price=10500)

        # 매수가 10000, 현재가 9690 → -3.1%
        sqs.handle_get_current_stock_price.return_value = ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value, msg1="OK",
            data={"price": "9690", "acml_vol": "100000"}
        )

        holdings = [{"code": "005930", "buy_price": 10000}]
        signals = await strategy.check_exits(holdings)

        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].action, "SELL")
        self.assertIn("손절", signals[0].reason)
        self.assertNotIn("005930", strategy._position_state)

    async def test_check_exits_fake_breakout(self):
        """현재가가 돌파기준선 아래로 복귀 시 가짜돌파 SELL."""
        strategy, ts, sqs, tm, mapper, _ = self._make_strategy()
        strategy._position_state["005930"] = PositionState(breakout_level=10000, peak_price=10200)

        # 매수가 10100, 현재가 9900 → 손절(-3%) 아닌데 돌파기준(10000) 아래
        sqs.handle_get_current_stock_price.return_value = ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value, msg1="OK",
            data={"price": "9900", "acml_vol": "100000"}
        )

        holdings = [{"code": "005930", "buy_price": 10100}]
        signals = await strategy.check_exits(holdings)

        self.assertEqual(len(signals), 1)
        self.assertIn("가짜돌파", signals[0].reason)

    async def test_check_exits_trailing_stop(self):
        """최고가 대비 -5% 하락 시 트레일링스탑 SELL."""
        strategy, ts, sqs, tm, mapper, _ = self._make_strategy()
        strategy._position_state["005930"] = PositionState(breakout_level=10000, peak_price=12000)

        # 최고가 12000, 현재가 11380 → -5.17%
        sqs.handle_get_current_stock_price.return_value = ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value, msg1="OK",
            data={"price": "11380", "acml_vol": "100000"}
        )

        holdings = [{"code": "005930", "buy_price": 10100}]
        signals = await strategy.check_exits(holdings)

        self.assertEqual(len(signals), 1)
        self.assertIn("트레일링스탑", signals[0].reason)

    async def test_check_exits_trend_end(self):
        """현재가 < 20일 MA 시 추세종료 SELL."""
        strategy, ts, sqs, tm, mapper, _ = self._make_strategy()
        strategy._position_state["005930"] = PositionState(breakout_level=10000, peak_price=11000)

        # 현재가 10500, 매수가 10100 → 손절/가짜돌파/트레일링 해당 없음
        sqs.handle_get_current_stock_price.return_value = ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value, msg1="OK",
            data={"price": "10500", "acml_vol": "100000"}
        )

        # 20일 MA = 10800 (현재가 10500 < MA 10800)
        ohlcv = [{"close": 10800, "high": 11000, "low": 10500, "volume": 100000, "date": f"2026020{i+1:02d}"}
                 for i in range(20)]
        ts.get_recent_daily_ohlcv.return_value = ohlcv

        holdings = [{"code": "005930", "buy_price": 10100}]
        signals = await strategy.check_exits(holdings)

        self.assertEqual(len(signals), 1)
        self.assertIn("추세종료", signals[0].reason)

    async def test_check_exits_peak_price_update(self):
        """현재가가 기존 최고가보다 높으면 peak_price 갱신."""
        strategy, ts, sqs, tm, mapper, _ = self._make_strategy()
        strategy._position_state["005930"] = PositionState(breakout_level=10000, peak_price=10500)

        # 현재가 11000 > peak 10500 → 갱신
        sqs.handle_get_current_stock_price.return_value = ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value, msg1="OK",
            data={"price": "11000", "acml_vol": "100000"}
        )
        # 추세종료 체크용: MA를 현재가보다 낮게
        ohlcv = [{"close": 10000, "high": 11000, "low": 9800, "volume": 100000, "date": f"2026020{i+1:02d}"}
                 for i in range(20)]
        ts.get_recent_daily_ohlcv.return_value = ohlcv

        holdings = [{"code": "005930", "buy_price": 10100}]
        await strategy.check_exits(holdings)

        # 매도 안 됐으면 peak_price가 11000으로 갱신됨
        self.assertEqual(strategy._position_state["005930"].peak_price, 11000)

    async def test_check_exits_no_sell_when_ok(self):
        """모든 조건 미충족 시 매도 없음."""
        strategy, ts, sqs, tm, mapper, _ = self._make_strategy()
        strategy._position_state["005930"] = PositionState(breakout_level=10000, peak_price=10500)

        # 현재가 10400: 손절 X (매수가 대비 +3%), 가짜돌파 X (>10000), 트레일링 X (<5%), 추세종료 X
        sqs.handle_get_current_stock_price.return_value = ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value, msg1="OK",
            data={"price": "10400", "acml_vol": "100000"}
        )
        # MA = 10000 < 현재가 10400 → 추세종료 X
        ohlcv = [{"close": 10000, "high": 10500, "low": 9800, "volume": 100000, "date": f"2026020{i+1:02d}"}
                 for i in range(20)]
        ts.get_recent_daily_ohlcv.return_value = ohlcv

        holdings = [{"code": "005930", "buy_price": 10100}]
        signals = await strategy.check_exits(holdings)

        self.assertEqual(len(signals), 0)

    async def test_check_exits_api_exception(self):
        """매도 체크 중 API 예외 발생 시 로그 기록하고 건너뜀."""
        strategy, ts, sqs, tm, mapper, logger = self._make_strategy()
        
        # 예외 발생 설정
        sqs.handle_get_current_stock_price.side_effect = Exception("Network Error")

        holdings = [{"code": "005930", "buy_price": 70000, "name": "삼성전자"}]
        
        # 실행 (예외가 전파되지 않아야 함)
        signals = await strategy.check_exits(holdings)

        self.assertEqual(len(signals), 0)
        logger.error.assert_called()

    # ── OHLCV 분석 ──

    def test_analyze_ohlcv_returns_none_for_empty(self):
        """빈 OHLCV에 None 반환."""
        strategy, *_ = self._make_strategy()
        result = strategy._analyze_ohlcv("123456", "테스트", [])
        self.assertIsNone(result)

    def test_analyze_ohlcv_returns_none_for_insufficient_data(self):
        """데이터 부족 시 None 반환."""
        strategy, *_ = self._make_strategy()
        ohlcv = self._make_ohlcv(days=10)  # 20일 필요한데 10일만
        result = strategy._analyze_ohlcv("123456", "테스트", ohlcv)
        self.assertIsNone(result)

    # ── 장 경과 비율 ──

    def test_market_progress_ratio(self):
        """11시 = 장 시작(9시) 후 2시간 / 총 6.5시간 ≈ 30.8%."""
        strategy, *_ = self._make_strategy()
        ratio = strategy._get_market_progress_ratio()
        self.assertAlmostEqual(ratio, 2 / 6.5, places=2)

    # ── scan API 실패 ──

    async def test_scan_empty_on_api_failure(self):
        """거래대금 상위 API 실패 시 빈 리스트."""
        strategy, ts, sqs, tm, mapper, _ = self._make_strategy()
        ts.get_top_trading_value_stocks.return_value = ResCommonResponse(
            rt_cd=ErrorCode.API_ERROR.value, msg1="Error", data=None
        )

        signals = await strategy.scan()
        self.assertEqual(len(signals), 0)

    async def test_get_current_ma_exception(self):
        """이동평균 계산 중 예외 발생 시 None 반환."""
        strategy, ts, *_ = self._make_strategy()
        ts.get_recent_daily_ohlcv.side_effect = Exception("API Error")

        ma = await strategy._get_current_ma("005930", 20)
        self.assertIsNone(ma)

    # ── 상태 저장/복원 ──

    def test_save_and_load_state(self):
        """포지션 상태 저장 후 새 인스턴스에서 복원."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = os.path.join(tmpdir, "tvb_state.json")

            strategy, *_ = self._make_strategy()
            strategy.STATE_FILE = state_file

            # 상태 설정 + 저장
            strategy._position_state["005930"] = PositionState(breakout_level=10000, peak_price=11500)
            strategy._position_state["035720"] = PositionState(breakout_level=20000, peak_price=22000)
            strategy._save_state()

            # 파일 존재 확인
            self.assertTrue(os.path.exists(state_file))

            # 새 인스턴스에서 복원
            strategy2, *_ = self._make_strategy()
            strategy2.STATE_FILE = state_file
            strategy2._load_state()

            self.assertEqual(len(strategy2._position_state), 2)
            self.assertEqual(strategy2._position_state["005930"].breakout_level, 10000)
            self.assertEqual(strategy2._position_state["005930"].peak_price, 11500)
            self.assertEqual(strategy2._position_state["035720"].peak_price, 22000)

    def test_load_state_missing_file(self):
        """파일 없을 때 빈 상태 유지."""
        strategy, *_ = self._make_strategy()
        strategy.STATE_FILE = "/nonexistent/path/state.json"
        strategy._load_state()
        self.assertEqual(len(strategy._position_state), 0)

    def test_load_state_corrupted_json(self):
        """손상된 JSON 파일도 에러 없이 처리."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = os.path.join(tmpdir, "tvb_state.json")
            with open(state_file, "w") as f:
                f.write("{invalid json")

            strategy, *_ = self._make_strategy()
            strategy.STATE_FILE = state_file
            strategy._load_state()
            self.assertEqual(len(strategy._position_state), 0)

    def test_save_state_clears_on_empty(self):
        """매도 후 빈 상태도 정상 저장."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = os.path.join(tmpdir, "tvb_state.json")

            strategy, *_ = self._make_strategy()
            strategy.STATE_FILE = state_file

            # 빈 상태 저장
            strategy._save_state()

            with open(state_file, "r") as f:
                data = json.load(f)
            self.assertEqual(data, {})

    # ── 추가 커버리지 테스트 ──

    async def test_scan_market_not_open(self):
        """장 시작 전(progress <= 0)이면 스캔 중단."""
        strategy, ts, sqs, tm, mapper, _ = self._make_strategy()
        
        # 워치리스트 설정
        strategy._watchlist = {"005930": MagicMock()}
        strategy._watchlist_date = "20260225"
        
        # 장 시작 전 시간 설정
        tm.get_current_kst_time.return_value = tm.get_market_open_time.return_value - timedelta(minutes=10)
        
        signals = await strategy.scan()
        self.assertEqual(len(signals), 0)
        sqs.handle_get_current_stock_price.assert_not_called()

    async def test_scan_price_api_failure_in_loop(self):
        """스캔 중 특정 종목 가격 조회 실패 시 해당 종목 건너뛰고 계속 진행."""
        strategy, ts, sqs, tm, mapper, logger = self._make_strategy()
        
        strategy._watchlist = {
            "A": WatchlistItem("A", "StockA", 1000, 900, 1000, 10000),
            "B": WatchlistItem("B", "StockB", 1000, 900, 1000, 10000),
        }
        strategy._watchlist_date = "20260225"
        
        # A는 실패, B는 성공(돌파)
        async def mock_get_price(code):
            if code == "A":
                return ResCommonResponse(rt_cd="1", msg1="Fail")
            return ResCommonResponse(rt_cd="0", msg1="OK", data={"price": "1100", "acml_vol": "5000"})
            
        sqs.handle_get_current_stock_price.side_effect = mock_get_price
        
        signals = await strategy.scan()
        
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].code, "B")

    async def test_scan_current_price_zero(self):
        """현재가가 0이면 스캔 건너뜀."""
        strategy, ts, sqs, tm, mapper, _ = self._make_strategy()
        
        strategy._watchlist = {
            "A": WatchlistItem("A", "StockA", 1000, 900, 1000, 10000),
        }
        strategy._watchlist_date = "20260225"
        
        sqs.handle_get_current_stock_price.return_value = ResCommonResponse(
            rt_cd="0", msg1="OK", data={"price": "0", "acml_vol": "0"}
        )
        
        signals = await strategy.scan()
        self.assertEqual(len(signals), 0)

    async def test_check_exits_missing_holding_info(self):
        """보유 종목 정보에 code나 buy_price가 없으면 건너뜀."""
        strategy, ts, sqs, tm, mapper, _ = self._make_strategy()
        
        holdings = [
            {"code": "", "buy_price": 1000}, # code missing
            {"code": "A", "buy_price": 0},   # buy_price 0
            {"name": "NoCode"}               # code missing
        ]
        
        signals = await strategy.check_exits(holdings)
        self.assertEqual(len(signals), 0)
        sqs.handle_get_current_stock_price.assert_not_called()

    async def test_check_exits_price_api_failure(self):
        """매도 체크 중 가격 조회 실패 시 건너뜀."""
        strategy, ts, sqs, tm, mapper, _ = self._make_strategy()
        
        holdings = [{"code": "A", "buy_price": 1000}]
        sqs.handle_get_current_stock_price.return_value = ResCommonResponse(rt_cd="1", msg1="Fail")
        
        signals = await strategy.check_exits(holdings)
        self.assertEqual(len(signals), 0)

    async def test_check_exits_missing_position_state_restored(self):
        """메모리에 포지션 상태가 없으면 holdings 정보로 복원."""
        strategy, ts, sqs, tm, mapper, logger = self._make_strategy()
        
        # 메모리 상태 비움
        strategy._position_state = {}
        
        holdings = [{"code": "A", "buy_price": 1000}]
        
        # 현재가 1050 (상승)
        sqs.handle_get_current_stock_price.return_value = ResCommonResponse(
            rt_cd="0", msg1="OK", data={"price": "1050"}
        )
        
        # MA 조회 (추세 종료 아님)
        ts.get_recent_daily_ohlcv.return_value = [{"close": 900}] * 20
        
        await strategy.check_exits(holdings)
        
        # 상태가 복원되었는지 확인
        self.assertIn("A", strategy._position_state)
        state = strategy._position_state["A"]
        self.assertEqual(state.breakout_level, 1000)
        self.assertEqual(state.peak_price, 1050) # 현재가로 갱신됨
        
        # 로그 확인
        logger.warning.assert_called()
        args, _ = logger.warning.call_args
        self.assertEqual(args[0]["event"], "missing_position_state")

    async def test_build_watchlist_candidate_missing_code(self):
        """후보 종목에 코드가 없으면 건너뜀."""
        strategy, ts, sqs, tm, mapper, _ = self._make_strategy()
        
        ts.get_top_trading_value_stocks.return_value = ResCommonResponse(
            rt_cd="0", msg1="OK", data=[{"mksc_shrn_iscd": ""}] # Code missing
        )
        
        await strategy._build_watchlist()
        self.assertEqual(len(strategy._watchlist), 0)
        ts.get_recent_daily_ohlcv.assert_not_called()

    async def test_build_watchlist_ohlcv_exception(self):
        """워치리스트 빌드 중 OHLCV 조회 예외 발생 시 로그 남기고 계속."""
        strategy, ts, sqs, tm, mapper, logger = self._make_strategy()
        
        ts.get_top_trading_value_stocks.return_value = ResCommonResponse(
            rt_cd="0", msg1="OK", data=[{"mksc_shrn_iscd": "A"}]
        )
        
        ts.get_recent_daily_ohlcv.side_effect = Exception("API Error")
        
        await strategy._build_watchlist()
        
        self.assertEqual(len(strategy._watchlist), 0)
        logger.error.assert_called()
        args, _ = logger.error.call_args
        self.assertEqual(args[0]["event"], "build_watchlist_error")

    def test_save_state_exception(self):
        """상태 저장 중 예외 발생 시 로그 남김."""
        strategy, _, _, _, _, logger = self._make_strategy()
        
        with patch("builtins.open", side_effect=IOError("Disk full")):
            strategy._save_state()
            
        logger.warning.assert_called()
        args, _ = logger.warning.call_args
        self.assertEqual(args[0]["event"], "save_state_failed")

    async def test_get_current_ma_insufficient_data(self):
        """_get_current_ma: 데이터 부족 시 None 반환."""
        strategy, ts, _, _, _, _ = self._make_strategy()
        
        # Case 1: OHLCV list empty or short
        ts.get_recent_daily_ohlcv.return_value = [{"close": 100}] * 10 # period 20 required
        ma = await strategy._get_current_ma("A", 20)
        self.assertIsNone(ma)
        
        # Case 2: OHLCV exists but closes missing
        ts.get_recent_daily_ohlcv.return_value = [{"open": 100}] * 30 # no close key
        ma = await strategy._get_current_ma("A", 20)
        self.assertIsNone(ma)

    def test_get_market_progress_ratio_before_open(self):
        """_get_market_progress_ratio: 장 시작 전이면 0.0 반환."""
        strategy, _, _, tm, _, _ = self._make_strategy()
        
        tm.get_current_kst_time.return_value = datetime(2026, 2, 25, 8, 59)
        tm.get_market_open_time.return_value = datetime(2026, 2, 25, 9, 0)
        tm.get_market_close_time.return_value = datetime(2026, 2, 25, 15, 30)
        
        ratio = strategy._get_market_progress_ratio()
        self.assertEqual(ratio, 0.0)

    async def test_check_exits_current_price_zero(self):
        """check_exits: 현재가가 0이면 건너뜀."""
        strategy, _, sqs, _, _, _ = self._make_strategy()
        
        holdings = [{"code": "A", "buy_price": 1000}]
        sqs.handle_get_current_stock_price.return_value = ResCommonResponse(
            rt_cd="0", msg1="OK", data={"price": "0"}
        )
        
        signals = await strategy.check_exits(holdings)
        self.assertEqual(len(signals), 0)

    def test_calculate_qty_fixed(self):
        """_calculate_qty: 고정 수량 모드일 때 1 반환."""
        strategy, _, _, _, _, _ = self._make_strategy(use_fixed_qty=True)
        qty = strategy._calculate_qty(10000)
        self.assertEqual(qty, 1)

    def test_analyze_ohlcv_missing_high_or_volume(self):
        """_analyze_ohlcv: high나 volume 데이터가 없으면 None 반환."""
        strategy, _, _, _, _, _ = self._make_strategy()
        
        # closes는 충분하지만 high가 없는 데이터
        ohlcv = [{"close": 100, "volume": 1000} for _ in range(60)]
        result = strategy._analyze_ohlcv("A", "StockA", ohlcv)
        self.assertIsNone(result)
        
        # closes는 충분하지만 volume이 없는 데이터
        ohlcv = [{"close": 100, "high": 110} for _ in range(60)]
        result = strategy._analyze_ohlcv("A", "StockA", ohlcv)
        self.assertIsNone(result)

    async def test_scan_exception_handling(self):
        """scan: 개별 종목 처리 중 예외 발생 시 로그 남기고 계속 진행."""
        strategy, ts, sqs, tm, mapper, logger = self._make_strategy()
        
        strategy._watchlist = {
            "A": WatchlistItem("A", "StockA", 1000, 900, 1000, 10000),
        }
        strategy._watchlist_date = "20260225"
        
        # 예외 발생 유도
        sqs.handle_get_current_stock_price.side_effect = Exception("Unexpected Error")
        
        signals = await strategy.scan()
        self.assertEqual(len(signals), 0)
        logger.error.assert_called()

if __name__ == "__main__":
    unittest.main()
