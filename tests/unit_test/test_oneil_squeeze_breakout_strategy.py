# tests/unit_test/test_oneil_squeeze_breakout_strategy.py
import json
import os
import unittest
from unittest.mock import MagicMock, AsyncMock, patch
from datetime import datetime

import pytz

from common.types import ErrorCode, ResCommonResponse, ResBollingerBand
from strategies.oneil_squeeze_breakout_strategy import (
    OneilSqueezeBreakoutStrategy,
    OneilSqueezeConfig,
    OSBWatchlistItem,
    OSBPositionState,
)

KST = pytz.timezone("Asia/Seoul")


def _kst_dt(year=2026, month=2, day=25, hour=10, minute=0):
    return KST.localize(datetime(year, month, day, hour, minute))


def _make_ohlcv(n=60, base_close=10000, base_vol=100000):
    """n일치 OHLCV 생성. close는 서서히 상승하는 정배열 데이터."""
    data = []
    for i in range(n):
        c = base_close + i * 50
        data.append({
            "date": f"2026{(1 + i // 28):02d}{(1 + i % 28):02d}",
            "open": c - 30, "high": c + 50, "low": c - 50,
            "close": c, "volume": base_vol + i * 1000,
        })
    return data


def _make_bb_response(ohlcv, period=20):
    """OHLCV로부터 간이 BB response 생성."""
    bands = []
    for i in range(len(ohlcv)):
        c = ohlcv[i]["close"]
        if i >= period - 1:
            window = [ohlcv[j]["close"] for j in range(i - period + 1, i + 1)]
            mean = sum(window) / len(window)
            std = (sum((x - mean) ** 2 for x in window) / len(window)) ** 0.5
            bands.append(ResBollingerBand(
                code="TEST", date=ohlcv[i]["date"], close=c,
                middle=mean, upper=mean + 2 * std, lower=mean - 2 * std,
            ))
        else:
            bands.append(ResBollingerBand(
                code="TEST", date=ohlcv[i]["date"], close=c,
                middle=None, upper=None, lower=None,
            ))
    return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data=bands)


class TestOneilSqueezeBreakoutStrategy(unittest.IsolatedAsyncioTestCase):

    def _make_strategy(self, **config_overrides):
        ts = MagicMock()
        ts.get_top_trading_value_stocks = AsyncMock()
        ts.get_top_rise_fall_stocks = AsyncMock()
        ts.get_top_volume_stocks = AsyncMock()
        ts.get_current_stock_price = AsyncMock()
        ts.get_recent_daily_ohlcv = AsyncMock()
        ts.get_financial_ratio = AsyncMock(return_value=ResCommonResponse(
            rt_cd=ErrorCode.API_ERROR.value, msg1="not available", data=None,
        ))
        sqs = MagicMock()
        indicator = MagicMock()
        indicator.get_bollinger_bands = AsyncMock()
        indicator.get_relative_strength = AsyncMock(return_value=ResCommonResponse(
            rt_cd=ErrorCode.EMPTY_VALUES.value, msg1="데이터 부족", data=None,
        ))
        mapper = MagicMock()
        mapper.is_kosdaq.return_value = True
        mapper.get_name_by_code.return_value = "테스트종목"
        tm = MagicMock()

        now = _kst_dt(hour=10, minute=0)
        open_time = _kst_dt(hour=9, minute=0)
        close_time = _kst_dt(hour=15, minute=30)
        tm.get_current_kst_time.return_value = now
        tm.get_market_open_time.return_value = open_time
        tm.get_market_close_time.return_value = close_time

        logger = MagicMock()
        config = OneilSqueezeConfig(**config_overrides)
        strategy = OneilSqueezeBreakoutStrategy(
            trading_service=ts,
            stock_query_service=sqs,
            indicator_service=indicator,
            stock_code_mapper=mapper,
            time_manager=tm,
            config=config,
            logger=logger,
        )
        return strategy, ts, indicator, mapper, tm

    # ── 기본 ──

    def test_name(self):
        strategy, *_ = self._make_strategy()
        self.assertEqual(strategy.name, "오닐스퀴즈돌파")

    # ── 마켓 타이밍 ──

    async def test_etf_ma_rising_true(self):
        """ETF MA가 3일 연속 상승이면 True."""
        strategy, ts, *_ = self._make_strategy()
        # 25일치 데이터, 종가가 꾸준히 상승
        ohlcv = _make_ohlcv(n=25, base_close=5000)
        ts.get_recent_daily_ohlcv.return_value = ohlcv

        result = await strategy._check_etf_ma_rising("229200")
        self.assertTrue(result)

    async def test_etf_ma_rising_false_when_declining(self):
        """ETF MA가 하락 중이면 False."""
        strategy, ts, *_ = self._make_strategy()
        # 종가가 하락하는 데이터
        ohlcv = []
        for i in range(25):
            c = 10000 - i * 100
            ohlcv.append({"date": f"202602{i+1:02d}", "open": c, "high": c + 10, "low": c - 10, "close": c, "volume": 10000})
        ts.get_recent_daily_ohlcv.return_value = ohlcv

        result = await strategy._check_etf_ma_rising("229200")
        self.assertFalse(result)

    # ── 워치리스트 빌드 ──

    async def test_build_watchlist_filters_correctly(self):
        """정배열 + 거래대금 + 52주고가 + BB 조건 통과 종목만 워치리스트에 포함."""
        strategy, ts, indicator, mapper, _ = self._make_strategy()

        # 3가지 소스: 거래대금/상승률/거래량
        trading_val_data = ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value, msg1="OK",
            data=[
                {"mksc_shrn_iscd": "005930", "hts_kor_isnm": "삼성전자"},
            ]
        )
        rise_data = ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value, msg1="OK",
            data=[
                {"mksc_shrn_iscd": "000660", "hts_kor_isnm": "SK하이닉스"},
            ]
        )
        volume_data = ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data=[]
        )
        ts.get_top_trading_value_stocks.return_value = trading_val_data
        ts.get_top_rise_fall_stocks.return_value = rise_data
        ts.get_top_volume_stocks.return_value = volume_data

        # 005930: 정배열 O, 거래대금 O
        ohlcv_good = _make_ohlcv(n=60, base_close=50000, base_vol=300000)
        # 000660: 거래대금 부족 (vol 작음)
        ohlcv_bad = _make_ohlcv(n=60, base_close=100, base_vol=10)

        async def mock_ohlcv(code, limit=100):
            if code == "005930":
                return ohlcv_good[-limit:]
            return ohlcv_bad[-limit:]

        ts.get_recent_daily_ohlcv.side_effect = mock_ohlcv

        # 52주 고가 근접
        async def mock_price(code):
            if code == "005930":
                return ResCommonResponse(
                    rt_cd=ErrorCode.SUCCESS.value, msg1="OK",
                    data={"output": {"w52_hgpr": "55000", "stck_prpr": "52950", "stck_llam": "300000000000"}},
                )
            return ResCommonResponse(
                rt_cd=ErrorCode.SUCCESS.value, msg1="OK",
                data={"output": {"w52_hgpr": "200", "stck_prpr": "105", "stck_llam": "1000000"}},
            )

        ts.get_current_stock_price.side_effect = mock_price

        # BB
        indicator.get_bollinger_bands.return_value = _make_bb_response(ohlcv_good)

        await strategy._build_watchlist()

        self.assertIn("005930", strategy._watchlist)
        self.assertNotIn("000660", strategy._watchlist)

    # ── 매수 조건 ──

    async def test_buy_signal_all_conditions_pass(self):
        """모든 매수 조건 통과 시 BUY 시그널 생성."""
        strategy, ts, *_ = self._make_strategy()

        # 워치리스트에 종목 직접 삽입
        strategy._watchlist = {
            "005930": OSBWatchlistItem(
                code="005930", name="삼성전자", market="KOSDAQ",
                high_20d=70000, ma_20d=65000, ma_50d=60000,
                avg_vol_20d=100000, bb_width_min_20d=1000.0,
                prev_bb_width=1100.0,  # 1100 <= 1000 * 1.2 = 1200 → 스퀴즈 OK
                w52_hgpr=75000, avg_trading_value_5d=20_000_000_000,
            )
        }
        strategy._watchlist_date = "20260225"
        strategy._watchlist_refresh_done = {10, 30, 60, 90}

        # 마켓 타이밍 통과
        strategy._market_timing_cache = {"KOSDAQ": True, "KOSPI": True}
        strategy._market_timing_date = "20260225"

        # 현재가: 돌파 + 거래량 + 프로그램 필터 통과
        ts.get_current_stock_price.return_value = ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value, msg1="OK",
            data={"output": {
                "stck_prpr": "71000",      # > 70000 (돌파)
                "acml_vol": "200000",       # 환산 = 200000 / (1/6.5) ≈ 충분
                "acml_tr_pbmn": "5000000000",
                "pgtr_ntby_qty": "10000",   # > 0
                "stck_llam": "100000000000",
                "hts_kor_isnm": "삼성전자",
            }}
        )

        signals = await strategy.scan()

        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].code, "005930")
        self.assertEqual(signals[0].action, "BUY")
        self.assertIn("스퀴즈돌파", signals[0].reason)
        self.assertEqual(signals[0].strategy_name, "오닐스퀴즈돌파")

    async def test_buy_rejected_no_squeeze(self):
        """BB 스퀴즈 조건 불충족 시 시그널 없음."""
        strategy, ts, *_ = self._make_strategy()

        strategy._watchlist = {
            "005930": OSBWatchlistItem(
                code="005930", name="삼성전자", market="KOSDAQ",
                high_20d=70000, ma_20d=65000, ma_50d=60000,
                avg_vol_20d=100000, bb_width_min_20d=1000.0,
                prev_bb_width=1500.0,  # 1500 > 1000 * 1.2 = 1200 → 스퀴즈 X
                w52_hgpr=75000, avg_trading_value_5d=20_000_000_000,
            )
        }
        strategy._watchlist_date = "20260225"
        strategy._watchlist_refresh_done = {10, 30, 60, 90}
        strategy._market_timing_cache = {"KOSDAQ": True}
        strategy._market_timing_date = "20260225"

        signals = await strategy.scan()
        self.assertEqual(len(signals), 0)

    async def test_buy_rejected_market_timing_fail(self):
        """마켓 타이밍 실패 시 시그널 없음."""
        strategy, ts, *_ = self._make_strategy()

        strategy._watchlist = {
            "005930": OSBWatchlistItem(
                code="005930", name="삼성전자", market="KOSDAQ",
                high_20d=70000, ma_20d=65000, ma_50d=60000,
                avg_vol_20d=100000, bb_width_min_20d=1000.0,
                prev_bb_width=1100.0,
                w52_hgpr=75000, avg_trading_value_5d=20_000_000_000,
            )
        }
        strategy._watchlist_date = "20260225"
        strategy._watchlist_refresh_done = {10, 30, 60, 90}
        strategy._market_timing_cache = {"KOSDAQ": False}  # 타이밍 실패
        strategy._market_timing_date = "20260225"

        signals = await strategy.scan()
        self.assertEqual(len(signals), 0)

    async def test_buy_rejected_no_price_breakout(self):
        """가격 돌파 미달 시 시그널 없음."""
        strategy, ts, *_ = self._make_strategy()

        strategy._watchlist = {
            "005930": OSBWatchlistItem(
                code="005930", name="삼성전자", market="KOSDAQ",
                high_20d=70000, ma_20d=65000, ma_50d=60000,
                avg_vol_20d=100000, bb_width_min_20d=1000.0,
                prev_bb_width=1100.0,
                w52_hgpr=75000, avg_trading_value_5d=20_000_000_000,
            )
        }
        strategy._watchlist_date = "20260225"
        strategy._watchlist_refresh_done = {10, 30, 60, 90}
        strategy._market_timing_cache = {"KOSDAQ": True}
        strategy._market_timing_date = "20260225"

        # 현재가 69000 < 70000 (돌파 안됨)
        ts.get_current_stock_price.return_value = ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value, msg1="OK",
            data={"output": {
                "stck_prpr": "69000",
                "acml_vol": "200000",
                "acml_tr_pbmn": "5000000000",
                "pgtr_ntby_qty": "10000",
                "stck_llam": "100000000000",
            }}
        )

        signals = await strategy.scan()
        self.assertEqual(len(signals), 0)

    async def test_buy_rejected_program_filter(self):
        """프로그램 순매수 부족 시 시그널 없음."""
        strategy, ts, *_ = self._make_strategy()

        strategy._watchlist = {
            "005930": OSBWatchlistItem(
                code="005930", name="삼성전자", market="KOSDAQ",
                high_20d=70000, ma_20d=65000, ma_50d=60000,
                avg_vol_20d=100000, bb_width_min_20d=1000.0,
                prev_bb_width=1100.0,
                w52_hgpr=75000, avg_trading_value_5d=20_000_000_000,
            )
        }
        strategy._watchlist_date = "20260225"
        strategy._watchlist_refresh_done = {10, 30, 60, 90}
        strategy._market_timing_cache = {"KOSDAQ": True}
        strategy._market_timing_date = "20260225"

        # 프로그램 순매수 음수
        ts.get_current_stock_price.return_value = ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value, msg1="OK",
            data={"output": {
                "stck_prpr": "71000",
                "acml_vol": "200000",
                "acml_tr_pbmn": "5000000000",
                "pgtr_ntby_qty": "-100",  # 음수
                "stck_llam": "100000000000",
            }}
        )

        signals = await strategy.scan()
        self.assertEqual(len(signals), 0)

    async def test_scan_skips_already_held_stock(self):
        """scan()이 이미 보유 중인 종목에 대해 매수 신호를 생성하지 않는지 검증."""
        # 1. Setup
        strategy, ts, *_ = self._make_strategy()
        test_code = "005930"

        # 워치리스트에 종목 추가 (모든 조건 통과 가능하도록)
        strategy._watchlist = {
            test_code: OSBWatchlistItem(
                code=test_code, name="삼성전자", market="KOSDAQ",
                high_20d=70000, ma_20d=65000, ma_50d=60000,
                avg_vol_20d=100000, bb_width_min_20d=1000.0,
                prev_bb_width=1100.0,  # Squeeze OK
                w52_hgpr=75000, avg_trading_value_5d=20_000_000_000,
            )
        }
        strategy._watchlist_date = "20260225"
        strategy._watchlist_refresh_done = {10, 30, 60, 90}

        # 이미 보유 중인 것으로 상태 설정
        strategy._position_state = {
            test_code: OSBPositionState(
                entry_price=71000, entry_date="20260224",
                peak_price=72000, breakout_level=70000
            )
        }

        # 마켓 타이밍 OK로 설정
        strategy._market_timing_cache = {"KOSDAQ": True}
        strategy._market_timing_date = "20260225"

        # 2. Execute
        signals = await strategy.scan()

        # 3. Assert
        self.assertEqual(len(signals), 0)
        ts.get_current_stock_price.assert_not_called()
        strategy._logger.debug.assert_called_with({
            "event": "scan_skipped_already_holding",
            "code": test_code,
            "name": "삼성전자",
        })

    # ── 매도 조건 ──

    async def test_exit_stop_loss(self):
        """진입가 대비 -5% 이하면 손절."""
        strategy, ts, *_ = self._make_strategy()

        strategy._position_state["005930"] = OSBPositionState(
            entry_price=70000, entry_date="20260220",
            peak_price=71000, breakout_level=69000,
        )

        # 현재가 66400 → pnl = (66400-70000)/70000 = -5.14%
        ts.get_current_stock_price.return_value = ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value, msg1="OK",
            data={"output": {
                "stck_prpr": "66400", "acml_vol": "50000",
                "hts_kor_isnm": "삼성전자",
            }}
        )

        holdings = [{"code": "005930", "buy_price": 70000, "name": "삼성전자"}]
        signals = await strategy.check_exits(holdings)

        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].action, "SELL")
        self.assertIn("손절", signals[0].reason)

    async def test_exit_trailing_stop(self):
        """최고가 대비 -8% 이하면 트레일링 스탑."""
        strategy, ts, *_ = self._make_strategy()

        strategy._position_state["005930"] = OSBPositionState(
            entry_price=70000, entry_date="20260220",
            peak_price=80000, breakout_level=69000,
        )

        # 현재가 73500 → (73500-80000)/80000 = -8.125%
        ts.get_current_stock_price.return_value = ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value, msg1="OK",
            data={"output": {
                "stck_prpr": "73500", "acml_vol": "50000",
                "hts_kor_isnm": "삼성전자",
            }}
        )

        holdings = [{"code": "005930", "buy_price": 70000, "name": "삼성전자"}]
        signals = await strategy.check_exits(holdings)

        self.assertEqual(len(signals), 1)
        self.assertIn("트레일링스탑", signals[0].reason)

    async def test_exit_time_stop_sideways(self):
        """5일 박스권 횡보 시 시간 손절."""
        strategy, ts, *_ = self._make_strategy()

        strategy._position_state["005930"] = OSBPositionState(
            entry_price=70000, entry_date="20260218",
            peak_price=70500, breakout_level=69000,
        )

        # 현재가는 손절/트레일링 해당 없음
        ts.get_current_stock_price.return_value = ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value, msg1="OK",
            data={"output": {
                "stck_prpr": "70200", "acml_vol": "50000",
                "hts_kor_isnm": "삼성전자",
            }}
        )

        # 5일간 횡보 데이터: 고가-저가 범위가 매우 좁음
        sideways_ohlcv = []
        for i in range(10):
            date = f"202602{18+i:02d}" if 18+i <= 28 else f"2026030{18+i-28:02d}"
            sideways_ohlcv.append({
                "date": date,
                "open": 70100, "high": 70200, "low": 70000,
                "close": 70100, "volume": 50000,
            })

        ts.get_recent_daily_ohlcv.return_value = sideways_ohlcv

        holdings = [{"code": "005930", "buy_price": 70000, "name": "삼성전자"}]
        signals = await strategy.check_exits(holdings)

        self.assertEqual(len(signals), 1)
        self.assertIn("시간청산", signals[0].reason)

    async def test_exit_trend_break(self):
        """10일 MA 하향 이탈 + 대량 거래량 시 추세이탈 매도."""
        strategy, ts, *_ = self._make_strategy()

        strategy._position_state["005930"] = OSBPositionState(
            entry_price=70000, entry_date="20260220",
            peak_price=73000, breakout_level=69000,
        )

        # 현재가 69000, 손절/트레일링 해당 X
        ts.get_current_stock_price.return_value = ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value, msg1="OK",
            data={"output": {
                "stck_prpr": "69000",
                "acml_vol": "500000",  # 대량
                "hts_kor_isnm": "삼성전자",
            }}
        )

        # 시간 손절: 진입일과 오늘 같으면 통과 → entry_date를 오늘로
        strategy._position_state["005930"].entry_date = "20260225"

        # 10일 MA = 약 70000 → 현재가 69000 < MA
        ohlcv_for_ma = [{"date": f"202602{15+i:02d}", "close": 70000, "volume": 100000} for i in range(20)]
        # 20일 평균 거래량 = 100000, 환산 거래량 = 500000 / (1/6.5) ≈ 3250000 > 100000
        ohlcv_for_ma_20 = [{"date": f"202602{5+i:02d}", "close": 70000, "volume": 100000, "high": 70100, "low": 69900, "open": 70000} for i in range(20)]

        call_count = [0]
        async def mock_ohlcv(code, limit=100):
            call_count[0] += 1
            if limit == 10:
                return ohlcv_for_ma[-10:]
            return ohlcv_for_ma_20

        ts.get_recent_daily_ohlcv.side_effect = mock_ohlcv

        holdings = [{"code": "005930", "buy_price": 70000, "name": "삼성전자"}]
        signals = await strategy.check_exits(holdings)

        self.assertEqual(len(signals), 1)
        self.assertIn("추세이탈", signals[0].reason)

    async def test_no_exit_when_healthy(self):
        """정상 보유 중이면 매도 시그널 없음."""
        strategy, ts, *_ = self._make_strategy()

        strategy._position_state["005930"] = OSBPositionState(
            entry_price=70000, entry_date="20260225",
            peak_price=72000, breakout_level=69000,
        )

        # 현재가 71500: 손절X, 트레일링X
        ts.get_current_stock_price.return_value = ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value, msg1="OK",
            data={"output": {
                "stck_prpr": "71500", "acml_vol": "80000",
                "hts_kor_isnm": "삼성전자",
            }}
        )

        # 시간손절: entry_date == today → skip
        # 추세이탈: 10MA = ~71000, 현재가 71500 > MA → skip
        ohlcv_for_ma = [{"date": f"202602{15+i:02d}", "close": 71000, "volume": 100000} for i in range(10)]
        ts.get_recent_daily_ohlcv.return_value = ohlcv_for_ma

        holdings = [{"code": "005930", "buy_price": 70000, "name": "삼성전자"}]
        signals = await strategy.check_exits(holdings)
        self.assertEqual(len(signals), 0)

    # ── 상태 저장/복원 ──

    def test_state_save_and_load(self):
        """포지션 상태 JSON 저장 및 복원."""
        strategy, *_ = self._make_strategy()
        test_file = "data/osb_test_state.json"
        strategy.STATE_FILE = test_file

        try:
            strategy._position_state["005930"] = OSBPositionState(
                entry_price=70000, entry_date="20260225",
                peak_price=72000, breakout_level=69000,
            )
            strategy._save_state()

            # 새 인스턴스로 복원
            strategy2, *_ = self._make_strategy()
            strategy2.STATE_FILE = test_file
            strategy2._load_state()

            self.assertIn("005930", strategy2._position_state)
            state = strategy2._position_state["005930"]
            self.assertEqual(state.entry_price, 70000)
            self.assertEqual(state.peak_price, 72000)
        finally:
            if os.path.exists(test_file):
                os.remove(test_file)

    # ── BB 폭 추출 ──

    def test_extract_bb_widths(self):
        """BB response에서 밴드폭 정확히 추출."""
        strategy, *_ = self._make_strategy()

        bb_data = [
            ResBollingerBand(code="T", date="20260101", close=100, middle=100, upper=110, lower=90),
            ResBollingerBand(code="T", date="20260102", close=101, middle=101, upper=115, lower=85),
            ResBollingerBand(code="T", date="20260103", close=102, middle=None, upper=None, lower=None),
        ]
        resp = ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data=bb_data)
        widths = strategy._extract_bb_widths(resp)

        self.assertEqual(len(widths), 2)  # None은 제외
        self.assertAlmostEqual(widths[0], 20.0)
        self.assertAlmostEqual(widths[1], 30.0)

    # ── 수량 계산 ──

    def test_calculate_qty_fixed(self):
        """use_fixed_qty=True면 항상 1."""
        strategy, *_ = self._make_strategy()
        self.assertEqual(strategy._calculate_qty(70000), 1)

    def test_calculate_qty_budget(self):
        """use_fixed_qty=False일 때 포트폴리오 비중 기반 계산."""
        strategy, *_ = self._make_strategy(use_fixed_qty=False)
        # budget = 10,000,000 * 5% = 500,000
        # qty = 500,000 / 50,000 = 10
        self.assertEqual(strategy._calculate_qty(50000), 10)


    # ════════════════════════════════════════════════════════
    # V2 스코어링 테스트
    # ════════════════════════════════════════════════════════

    def test_rs_score_top_10_percent(self):
        """RS 상위 10% 종목에 30점 부여."""
        strategy, *_ = self._make_strategy()

        # 20개 종목: rs_return_3m = 1~20
        items = []
        for i in range(20):
            items.append(OSBWatchlistItem(
                code=f"{i:06d}", name=f"종목{i}", market="KOSDAQ",
                high_20d=10000, ma_20d=9000, ma_50d=8000,
                avg_vol_20d=100000, bb_width_min_20d=100, prev_bb_width=110,
                w52_hgpr=11000, avg_trading_value_5d=10e9,
                rs_return_3m=float(i + 1),
            ))

        strategy._compute_rs_scores(items)

        # 상위 10% = 상위 2개 (rs_return_3m = 19, 20)
        scored = [it for it in items if it.rs_score > 0]
        self.assertGreaterEqual(len(scored), 2)
        # 최상위는 반드시 30점
        top_item = max(items, key=lambda x: x.rs_return_3m)
        self.assertEqual(top_item.rs_score, 30.0)
        # 최하위는 0점
        bottom_item = min(items, key=lambda x: x.rs_return_3m)
        self.assertEqual(bottom_item.rs_score, 0.0)

    def test_rs_score_empty_items(self):
        """빈 리스트에서 에러 없이 동작."""
        strategy, *_ = self._make_strategy()
        strategy._compute_rs_scores([])  # 에러 없어야 함

    async def test_profit_growth_score_above_25pct(self):
        """영업이익 25%↑ → 20점 부여."""
        strategy, ts, *_ = self._make_strategy()

        ts.get_financial_ratio.return_value = ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value, msg1="OK",
            data={"output": [{"bsop_prti_icdc": "30.5"}]},
        )

        item = OSBWatchlistItem(
            code="005930", name="삼성전자", market="KOSDAQ",
            high_20d=70000, ma_20d=65000, ma_50d=60000,
            avg_vol_20d=100000, bb_width_min_20d=1000, prev_bb_width=1100,
            w52_hgpr=75000, avg_trading_value_5d=20e9,
        )

        await strategy._fetch_profit_growth(item)

        self.assertEqual(item.profit_growth_score, 20.0)

    async def test_profit_growth_below_threshold(self):
        """영업이익 25% 미만 → 0점."""
        strategy, ts, *_ = self._make_strategy()

        ts.get_financial_ratio.return_value = ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value, msg1="OK",
            data={"output": [{"bsop_prti_icdc": "15.0"}]},
        )

        item = OSBWatchlistItem(
            code="005930", name="삼성전자", market="KOSDAQ",
            high_20d=70000, ma_20d=65000, ma_50d=60000,
            avg_vol_20d=100000, bb_width_min_20d=1000, prev_bb_width=1100,
            w52_hgpr=75000, avg_trading_value_5d=20e9,
        )

        await strategy._fetch_profit_growth(item)

        self.assertEqual(item.profit_growth_score, 0.0)

    async def test_profit_growth_api_failure_graceful(self):
        """API 실패 시 0점 (전략 중단 안됨)."""
        strategy, ts, *_ = self._make_strategy()

        ts.get_financial_ratio.return_value = ResCommonResponse(
            rt_cd=ErrorCode.API_ERROR.value, msg1="서버 오류", data=None,
        )

        item = OSBWatchlistItem(
            code="005930", name="삼성전자", market="KOSDAQ",
            high_20d=70000, ma_20d=65000, ma_50d=60000,
            avg_vol_20d=100000, bb_width_min_20d=1000, prev_bb_width=1100,
            w52_hgpr=75000, avg_trading_value_5d=20e9,
        )

        await strategy._fetch_profit_growth(item)  # 에러 없어야 함
        self.assertEqual(item.profit_growth_score, 0.0)

    async def test_profit_growth_exception_graceful(self):
        """API 호출 중 예외 발생 시에도 0점 (전략 중단 안됨)."""
        strategy, ts, *_ = self._make_strategy()

        ts.get_financial_ratio.side_effect = Exception("네트워크 오류")

        item = OSBWatchlistItem(
            code="005930", name="삼성전자", market="KOSDAQ",
            high_20d=70000, ma_20d=65000, ma_50d=60000,
            avg_vol_20d=100000, bb_width_min_20d=1000, prev_bb_width=1100,
            w52_hgpr=75000, avg_trading_value_5d=20e9,
        )

        await strategy._fetch_profit_growth(item)  # 에러 없어야 함
        self.assertEqual(item.profit_growth_score, 0.0)

    def test_watchlist_sorted_by_total_score(self):
        """total_score 내림차순 정렬 확인."""
        strategy, *_ = self._make_strategy()

        items = []
        scores = [0, 30, 50, 20]
        for i, score in enumerate(scores):
            item = OSBWatchlistItem(
                code=f"{i:06d}", name=f"종목{i}", market="KOSDAQ",
                high_20d=10000, ma_20d=9000, ma_50d=8000,
                avg_vol_20d=100000, bb_width_min_20d=100, prev_bb_width=110,
                w52_hgpr=11000, avg_trading_value_5d=10e9,
                total_score=float(score),
            )
            items.append(item)

        items.sort(
            key=lambda x: (x.total_score, strategy._calc_turnover_ratio(x)),
            reverse=True,
        )

        self.assertEqual(items[0].total_score, 50.0)
        self.assertEqual(items[1].total_score, 30.0)
        self.assertEqual(items[2].total_score, 20.0)
        self.assertEqual(items[3].total_score, 0.0)

    def test_watchlist_tiebreaker_by_turnover(self):
        """동점 시 회전율 높은 종목이 앞에."""
        strategy, *_ = self._make_strategy()

        # 동점(30점) 2개, 회전율이 다름
        item_low_turnover = OSBWatchlistItem(
            code="000001", name="저회전율", market="KOSDAQ",
            high_20d=10000, ma_20d=9000, ma_50d=8000,
            avg_vol_20d=100000, bb_width_min_20d=100, prev_bb_width=110,
            w52_hgpr=11000, avg_trading_value_5d=5e9,
            market_cap=500e9,  # 회전율: 5e9/500e9 = 0.01
            total_score=30.0,
        )
        item_high_turnover = OSBWatchlistItem(
            code="000002", name="고회전율", market="KOSDAQ",
            high_20d=10000, ma_20d=9000, ma_50d=8000,
            avg_vol_20d=100000, bb_width_min_20d=100, prev_bb_width=110,
            w52_hgpr=11000, avg_trading_value_5d=50e9,
            market_cap=500e9,  # 회전율: 50e9/500e9 = 0.1
            total_score=30.0,
        )

        items = [item_low_turnover, item_high_turnover]
        items.sort(
            key=lambda x: (x.total_score, strategy._calc_turnover_ratio(x)),
            reverse=True,
        )

        self.assertEqual(items[0].code, "000002")  # 고회전율이 앞
        self.assertEqual(items[1].code, "000001")

    def test_compute_total_scores(self):
        """합산 점수 계산 확인."""
        strategy, *_ = self._make_strategy()

        items = [
            OSBWatchlistItem(
                code="000001", name="A", market="KOSDAQ",
                high_20d=10000, ma_20d=9000, ma_50d=8000,
                avg_vol_20d=100000, bb_width_min_20d=100, prev_bb_width=110,
                w52_hgpr=11000, avg_trading_value_5d=10e9,
                rs_score=30.0, profit_growth_score=20.0,
            ),
            OSBWatchlistItem(
                code="000002", name="B", market="KOSDAQ",
                high_20d=10000, ma_20d=9000, ma_50d=8000,
                avg_vol_20d=100000, bb_width_min_20d=100, prev_bb_width=110,
                w52_hgpr=11000, avg_trading_value_5d=10e9,
                rs_score=30.0, profit_growth_score=0.0,
            ),
        ]

        strategy._compute_total_scores(items)

        self.assertEqual(items[0].total_score, 50.0)
        self.assertEqual(items[1].total_score, 30.0)

    def test_extract_op_profit_growth_from_output_list(self):
        """재무 API 응답에서 영업이익 증가율 추출 (output 리스트)."""
        strategy, *_ = self._make_strategy()

        data = {"output": [{"bsop_prti_icdc": "35.2"}]}
        result = strategy._extract_op_profit_growth(data)
        self.assertAlmostEqual(result, 35.2)

    def test_extract_op_profit_growth_missing_field(self):
        """필드 없으면 0.0 반환."""
        strategy, *_ = self._make_strategy()

        data = {"output": [{"some_other_field": "10"}]}
        result = strategy._extract_op_profit_growth(data)
        self.assertEqual(result, 0.0)

    def test_extract_op_profit_growth_from_flat_dict(self):
        """flat dict에서도 추출 가능."""
        strategy, *_ = self._make_strategy()

        data = {"bsop_prti_icdc": "28.5"}
        result = strategy._extract_op_profit_growth(data)
        self.assertAlmostEqual(result, 28.5)

    async def test_profit_growth_tps_chunking(self):
        """TPS 청크 호출 검증: api_chunk_size=2 → 4개 종목이면 2회 청크."""
        strategy, ts, *_ = self._make_strategy(api_chunk_size=2)

        ts.get_financial_ratio.return_value = ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value, msg1="OK",
            data={"output": [{"bsop_prti_icdc": "30"}]},
        )

        items = []
        for i in range(4):
            items.append(OSBWatchlistItem(
                code=f"{i:06d}", name=f"종목{i}", market="KOSDAQ",
                high_20d=10000, ma_20d=9000, ma_50d=8000,
                avg_vol_20d=100000, bb_width_min_20d=100, prev_bb_width=110,
                w52_hgpr=11000, avg_trading_value_5d=10e9,
            ))

        await strategy._compute_profit_growth_scores(items)

        # 4개 종목 모두 호출됨
        self.assertEqual(ts.get_financial_ratio.call_count, 4)
        # 모든 종목에 20점 부여
        for item in items:
            self.assertEqual(item.profit_growth_score, 20.0)


if __name__ == "__main__":
    unittest.main()
