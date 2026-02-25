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
        ts.get_current_stock_price = AsyncMock()
        ts.get_recent_daily_ohlcv = AsyncMock()
        sqs = MagicMock()
        indicator = MagicMock()
        indicator.get_bollinger_bands = AsyncMock()
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

        config = OneilSqueezeConfig(**config_overrides)
        strategy = OneilSqueezeBreakoutStrategy(
            trading_service=ts,
            stock_query_service=sqs,
            indicator_service=indicator,
            stock_code_mapper=mapper,
            time_manager=tm,
            config=config,
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

        ts.get_top_trading_value_stocks.return_value = ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value, msg1="OK",
            data=[
                {"mksc_shrn_iscd": "005930", "hts_kor_isnm": "삼성전자"},
                {"mksc_shrn_iscd": "000660", "hts_kor_isnm": "SK하이닉스"},
            ]
        )

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
                    data={"output": {"w52_hgpr": "55000", "stck_prpr": "52950"}},
                )
            return ResCommonResponse(
                rt_cd=ErrorCode.SUCCESS.value, msg1="OK",
                data={"output": {"w52_hgpr": "200", "stck_prpr": "105"}},
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


if __name__ == "__main__":
    unittest.main()
