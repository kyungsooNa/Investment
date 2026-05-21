import unittest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytz

from common.types import ErrorCode, ResCommonResponse
from services.stock_query_service import StockQueryService
from strategies.larry_williams_vbo_strategy import (
    LarryWilliamsVBOConfig,
    LarryWilliamsVBOStrategy,
)

KST = pytz.timezone("Asia/Seoul")


def _kst(h: int, m: int, date: str = "2026-01-15") -> datetime:
    y, mo, d = (int(x) for x in date.split("-"))
    return KST.localize(datetime(y, mo, d, h, m))


def _price_resp(current: int, open_price: int,
                pgtr_ntby_qty: int = 500000,
                acml_tr_pbmn: int = 50_000_000_000) -> ResCommonResponse:
    """현재가 API mock 응답 헬퍼."""
    return ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value,
        msg1="OK",
        data={
            "price": str(current),
            "stck_prpr": str(current),
            "open": str(open_price),
            "pgtr_ntby_qty": str(pgtr_ntby_qty),   # 프로그램 순매수 수량
            "acml_tr_pbmn": str(acml_tr_pbmn),     # 누적 거래대금
        },
    )


def _ohlcv_resp(high: int, low: int, close: int = 0, volume: int = 10_000_000) -> ResCommonResponse:
    """일봉 API mock 응답 헬퍼 (limit=5 가정)."""
    close = close or (high + low) // 2
    row = {"date": "20260114", "open": low + 100, "high": high, "low": low, "close": close, "volume": volume}
    return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data=[row] * 5)


def _conclusion_resp(tday_rltv: float) -> ResCommonResponse:
    """체결강도 API mock 응답 헬퍼."""
    return ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value,
        msg1="OK",
        data={"output": [{"tday_rltv": str(tday_rltv)}]},
    )


class TestLarryWilliamsVBOStrategy(unittest.IsolatedAsyncioTestCase):

    def _make_strategy(self, now_time: datetime = None, **cfg_kwargs) -> tuple:
        sqs = MagicMock(spec=StockQueryService)
        sqs.get_top_trading_value_stocks = AsyncMock()
        sqs.get_recent_daily_ohlcv = AsyncMock()
        sqs.handle_get_current_stock_price = AsyncMock()
        sqs.get_stock_conclusion = AsyncMock()

        tm = MagicMock()
        tm.get_current_kst_time.return_value = now_time or _kst(10, 0)

        cfg_values = {"k_value": 0.5, "min_market_cap": 0, "min_5d_trading_value": 0,
                      "confidence_threshold": 120.0, "program_buy_ratio": 0.10, "stop_loss_pct": -3.0}
        cfg_values.update(cfg_kwargs)
        config = LarryWilliamsVBOConfig(**cfg_values)

        strategy = LarryWilliamsVBOStrategy(
            stock_query_service=sqs,
            market_clock=tm,
            config=config,
            logger=MagicMock(),
        )
        return strategy, sqs, tm

    def _pool_b(self, code: str = "005930", name: str = "삼성전자",
                stck_avls: str = "500000000000") -> ResCommonResponse:
        return ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value, msg1="OK",
            data=[{"mksc_shrn_iscd": code, "hts_kor_isnm": name, "stck_avls": stck_avls}],
        )

    # ── name ──────────────────────────────────────────────────────────

    def test_name(self):
        strategy, _, _ = self._make_strategy()
        self.assertEqual(strategy.name, "래리윌리엄스VBO")

    # ── 진입 시간대 가드 ────────────────────────────────────────────────

    async def test_scan_skipped_before_entry_start(self):
        """09:10 이전은 신호 없음."""
        strategy, sqs, _ = self._make_strategy(now_time=_kst(9, 5))
        signals = await strategy.scan()
        self.assertEqual(signals, [])
        sqs.get_top_trading_value_stocks.assert_not_called()

    async def test_scan_skipped_after_entry_cutoff(self):
        """14:00 이후는 신호 없음."""
        strategy, sqs, _ = self._make_strategy(now_time=_kst(14, 1))
        signals = await strategy.scan()
        self.assertEqual(signals, [])
        sqs.get_top_trading_value_stocks.assert_not_called()

    # ── Pool B 실패 ─────────────────────────────────────────────────

    async def test_scan_empty_on_pool_b_failure(self):
        """Pool B API 실패 시 빈 리스트 반환."""
        strategy, sqs, _ = self._make_strategy()
        sqs.get_top_trading_value_stocks.return_value = ResCommonResponse(
            rt_cd=ErrorCode.API_ERROR.value, msg1="err", data=None
        )
        signals = await strategy.scan()
        self.assertEqual(signals, [])

    # ── Target 돌파 → BUY 신호 ────────────────────────────────────────

    async def test_scan_generates_buy_signal_on_breakout(self):
        """Target 돌파 + 체결강도 120%+ + 프로그램 순매수 10%+ → BUY 신호."""
        strategy, sqs, _ = self._make_strategy(k_value=0.5)
        sqs.get_top_trading_value_stocks.return_value = self._pool_b()
        strategy._load_pool_b = AsyncMock(return_value=[{
            "code": "005930", "name": "삼성전자", "market": "",
            "market_cap": 500_000_000_000, "avg_5d_tv": 50_000_000_000,
        }])
        # Range=2000, K=0.5 → Target = 70000 + 1000 = 71000
        # current=72000 > target=71000 → 돌파
        sqs.get_recent_daily_ohlcv.return_value = _ohlcv_resp(high=72000, low=70000)
        sqs.handle_get_current_stock_price.return_value = _price_resp(
            current=72000, open_price=70000,
            pgtr_ntby_qty=700_000,          # 700000주 × 72000원 = 50.4억
            acml_tr_pbmn=50_000_000_000,   # 500억 → 비율 10.08% > 10%
        )
        sqs.get_stock_conclusion.return_value = _conclusion_resp(130.0)

        signals = await strategy.scan()

        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].code, "005930")
        self.assertEqual(signals[0].action, "BUY")
        self.assertIn("VBO돌파", signals[0].reason)
        sqs.handle_get_current_stock_price.assert_awaited_once_with(
            "005930", caller=strategy.name
        )

    # ── Target 미달 → 거절 ────────────────────────────────────────────

    async def test_scan_rejects_below_target(self):
        """현재가 < Target이면 BUY 신호 없음."""
        strategy, sqs, _ = self._make_strategy(k_value=0.5)
        sqs.get_top_trading_value_stocks.return_value = self._pool_b()
        # Range=2000, Target=71000, current=70500 (미달)
        sqs.get_recent_daily_ohlcv.return_value = _ohlcv_resp(high=72000, low=70000)
        sqs.handle_get_current_stock_price.return_value = _price_resp(
            current=70500, open_price=70000
        )

        signals = await strategy.scan()
        self.assertEqual(signals, [])

    # ── 체결강도 필터 ────────────────────────────────────────────────

    async def test_scan_rejects_low_execution_strength(self):
        """체결강도 120% 미만이면 BUY 신호 없음."""
        strategy, sqs, _ = self._make_strategy(k_value=0.5, confidence_threshold=120.0)
        sqs.get_top_trading_value_stocks.return_value = self._pool_b()
        sqs.get_recent_daily_ohlcv.return_value = _ohlcv_resp(high=72000, low=70000)
        sqs.handle_get_current_stock_price.return_value = _price_resp(
            current=72000, open_price=70000,
            pgtr_ntby_qty=700_000, acml_tr_pbmn=50_000_000_000,
        )
        sqs.get_stock_conclusion.return_value = _conclusion_resp(110.0)  # 110% < 120%

        signals = await strategy.scan()
        self.assertEqual(signals, [])

    # ── 프로그램 순매수 필터 ──────────────────────────────────────────

    async def test_scan_rejects_negative_program_buy(self):
        """프로그램 순매수 음수이면 BUY 신호 없음."""
        strategy, sqs, _ = self._make_strategy(k_value=0.5)
        sqs.get_top_trading_value_stocks.return_value = self._pool_b()
        sqs.get_recent_daily_ohlcv.return_value = _ohlcv_resp(high=72000, low=70000)
        sqs.handle_get_current_stock_price.return_value = _price_resp(
            current=72000, open_price=70000,
            pgtr_ntby_qty=-100_000,         # 음수 → 거절
            acml_tr_pbmn=50_000_000_000,
        )
        sqs.get_stock_conclusion.return_value = _conclusion_resp(130.0)

        signals = await strategy.scan()
        self.assertEqual(signals, [])

    async def test_scan_rejects_low_program_buy_ratio(self):
        """프로그램 순매수 비율 < 10%이면 BUY 신호 없음."""
        strategy, sqs, _ = self._make_strategy(k_value=0.5, program_buy_ratio=0.10)
        sqs.get_top_trading_value_stocks.return_value = self._pool_b()
        sqs.get_recent_daily_ohlcv.return_value = _ohlcv_resp(high=72000, low=70000)
        sqs.handle_get_current_stock_price.return_value = _price_resp(
            current=72000, open_price=70000,
            pgtr_ntby_qty=50_000,           # 50000주 × 72000원 = 36억 / 500억 = 7.2%
            acml_tr_pbmn=50_000_000_000,
        )
        sqs.get_stock_conclusion.return_value = _conclusion_resp(130.0)

        signals = await strategy.scan()
        self.assertEqual(signals, [])

    # ── Range 미확보 → 거절 ───────────────────────────────────────────

    async def test_scan_rejects_when_range_unavailable(self):
        """일봉 API 실패로 Range 미확보 시 BUY 신호 없음."""
        strategy, sqs, _ = self._make_strategy()
        sqs.get_top_trading_value_stocks.return_value = self._pool_b()
        sqs.get_recent_daily_ohlcv.return_value = ResCommonResponse(
            rt_cd=ErrorCode.API_ERROR.value, msg1="err", data=[]
        )
        sqs.handle_get_current_stock_price.return_value = _price_resp(
            current=72000, open_price=70000
        )

        signals = await strategy.scan()
        self.assertEqual(signals, [])

    # ── 당일 재진입 금지 ──────────────────────────────────────────────

    async def test_scan_no_reentry_same_day(self):
        """allow_reentry=False: 당일 동일 종목 두 번째 신호 차단."""
        strategy, sqs, _ = self._make_strategy(k_value=0.5)
        sqs.get_top_trading_value_stocks.return_value = self._pool_b()
        strategy._load_pool_b = AsyncMock(return_value=[{
            "code": "005930", "name": "삼성전자", "market": "",
            "market_cap": 500_000_000_000, "avg_5d_tv": 50_000_000_000,
        }])
        sqs.get_recent_daily_ohlcv.return_value = _ohlcv_resp(high=72000, low=70000)
        sqs.handle_get_current_stock_price.return_value = _price_resp(
            current=72000, open_price=70000,
            pgtr_ntby_qty=700_000, acml_tr_pbmn=50_000_000_000,
        )
        sqs.get_stock_conclusion.return_value = _conclusion_resp(130.0)

        first = await strategy.scan()
        self.assertEqual(len(first), 1)

        second = await strategy.scan()  # 같은 날
        self.assertEqual(len(second), 0)

    # ── check_exits: 오버나이트 방어 ─────────────────────────────────

    async def test_check_exits_overnight_guard(self):
        """매수일 ≠ 오늘이면 즉시 청산 신호."""
        strategy, sqs, tm = self._make_strategy(now_time=_kst(10, 0, date="2026-01-15"))
        holdings = [{"code": "005930", "name": "삼성전자", "buy_price": 70000,
                     "buy_date": "20260114", "qty": 1}]  # 전일 매수

        signals = await strategy.check_exits(holdings)

        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].action, "SELL")
        self.assertIn("오버나이트방어", signals[0].reason)
        sqs.handle_get_current_stock_price.assert_not_called()

    async def test_check_exits_does_not_sell_same_day_timestamp_buy_date(self):
        """매수일이 타임스탬프 문자열이어도 같은 날짜면 오버나이트 청산하지 않는다."""
        strategy, sqs, _ = self._make_strategy(now_time=_kst(9, 19, date="2026-04-30"))
        sqs.handle_get_current_stock_price.return_value = _price_resp(
            current=10200, open_price=9590
        )
        holdings = [{"code": "025860", "name": "남해화학", "buy_price": 10200,
                     "buy_date": "2026-04-30 09:13:18", "qty": 1}]

        signals = await strategy.check_exits(holdings)

        self.assertEqual(signals, [])
        sqs.handle_get_current_stock_price.assert_called_once()

    async def test_check_exits_overnight_guard_normalizes_timestamp_buy_date(self):
        """전일 타임스탬프 문자열은 날짜만 비교해 오버나이트 청산한다."""
        strategy, sqs, _ = self._make_strategy(now_time=_kst(10, 0, date="2026-04-30"))
        holdings = [{"code": "005930", "name": "삼성전자", "buy_price": 70000,
                     "buy_date": "2026-04-29 14:55:01", "qty": 1}]

        signals = await strategy.check_exits(holdings)

        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].action, "SELL")
        self.assertIn("매수일(20260429)", signals[0].reason)
        sqs.handle_get_current_stock_price.assert_not_called()

    # ── check_exits: 칼손절 ───────────────────────────────────────────

    async def test_check_exits_stop_loss(self):
        """진입가 대비 -3% 이하 → 칼손절 SELL 신호."""
        strategy, sqs, tm = self._make_strategy(
            now_time=_kst(11, 0), stop_loss_pct=-3.0
        )
        today = tm.get_current_kst_time().strftime("%Y%m%d")
        # 매수가 70000, 현재가 67800 → -3.14%
        sqs.handle_get_current_stock_price.return_value = _price_resp(
            current=67800, open_price=70000
        )
        holdings = [{"code": "005930", "name": "삼성전자", "buy_price": 70000,
                     "buy_date": today, "qty": 1}]

        signals = await strategy.check_exits(holdings)

        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].action, "SELL")
        self.assertIn("칼손절", signals[0].reason)

    # ── check_exits: EOD 강제 청산 ────────────────────────────────────

    async def test_check_exits_eod_flatten(self):
        """15:20 이후 → EOD 강제 청산 SELL 신호."""
        strategy, sqs, tm = self._make_strategy(now_time=_kst(15, 20))
        today = tm.get_current_kst_time().strftime("%Y%m%d")
        # 수익권이어도 강제 청산
        sqs.handle_get_current_stock_price.return_value = _price_resp(
            current=72000, open_price=70000
        )
        holdings = [{"code": "005930", "name": "삼성전자", "buy_price": 70000,
                     "buy_date": today, "qty": 2}]

        signals = await strategy.check_exits(holdings)

        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].action, "SELL")
        self.assertEqual(signals[0].qty, 2)
        self.assertIn("EOD청산", signals[0].reason)

    # ── check_exits: 조건 미충족 → HOLD ─────────────────────────────

    async def test_check_exits_hold_when_no_condition_met(self):
        """손절/EOD 모두 미충족 → 신호 없음(HOLD)."""
        strategy, sqs, tm = self._make_strategy(now_time=_kst(11, 0), stop_loss_pct=-3.0)
        today = tm.get_current_kst_time().strftime("%Y%m%d")
        # 매수가 70000, 현재가 70500 → +0.71% (손절 아님)
        sqs.handle_get_current_stock_price.return_value = _price_resp(
            current=70500, open_price=70000
        )
        holdings = [{"code": "005930", "name": "삼성전자", "buy_price": 70000,
                     "buy_date": today, "qty": 1}]

        signals = await strategy.check_exits(holdings)
        self.assertEqual(signals, [])

    # ── check_exits: 현재가 조회 실패 → 스킵 ─────────────────────────

    async def test_check_exits_skips_on_price_api_failure(self):
        """현재가 조회 실패 시 해당 종목 스킵 (SELL 신호 없음)."""
        strategy, sqs, tm = self._make_strategy(now_time=_kst(11, 0))
        today = tm.get_current_kst_time().strftime("%Y%m%d")
        sqs.handle_get_current_stock_price.return_value = ResCommonResponse(
            rt_cd=ErrorCode.API_ERROR.value, msg1="err"
        )
        holdings = [{"code": "005930", "name": "삼성전자", "buy_price": 70000,
                     "buy_date": today, "qty": 1}]

        signals = await strategy.check_exits(holdings)
        self.assertEqual(signals, [])

    # ── universe_service 경로 ──────────────────────────────────────────

    async def test_scan_uses_universe_service_when_provided(self):
        """universe_service가 주어지면 get_watchlist()를 사용하고 fallback API는 호출하지 않는다."""
        sqs = MagicMock(spec=StockQueryService)
        sqs.get_top_trading_value_stocks = AsyncMock()
        sqs.get_recent_daily_ohlcv = AsyncMock()
        sqs.handle_get_current_stock_price = AsyncMock()
        sqs.get_stock_conclusion = AsyncMock()

        tm = MagicMock()
        tm.get_current_kst_time.return_value = _kst(10, 0)

        # OSBWatchlistItem 흉내 (MagicMock)
        item = MagicMock()
        item.code = "005930"
        item.name = "삼성전자"
        item.market = "KOSPI"
        item.market_cap = 300_000_000_000   # 3,000억 → 필터 통과
        item.avg_trading_value_5d = 200_000_000_000  # 2,000억 → 필터 통과

        universe = MagicMock()
        universe.get_watchlist = AsyncMock(return_value={"005930": item})
        universe.is_market_timing_ok = AsyncMock(return_value=True)

        config = LarryWilliamsVBOConfig(
            k_value=0.5,
            min_market_cap=200_000_000_000,
            min_5d_trading_value=100_000_000_000,
            confidence_threshold=120.0,
            program_buy_ratio=0.10,
            stop_loss_pct=-3.0,
        )
        strategy = LarryWilliamsVBOStrategy(
            stock_query_service=sqs,
            market_clock=tm,
            universe_service=universe,
            config=config,
            logger=MagicMock(),
        )

        sqs.get_recent_daily_ohlcv.return_value = _ohlcv_resp(high=72000, low=70000)
        sqs.handle_get_current_stock_price.return_value = _price_resp(
            current=72000, open_price=70000,
            pgtr_ntby_qty=700_000, acml_tr_pbmn=50_000_000_000,
        )
        sqs.get_stock_conclusion.return_value = _conclusion_resp(130.0)

        signals = await strategy.scan()

        universe.get_watchlist.assert_called_once()
        sqs.get_top_trading_value_stocks.assert_not_called()   # fallback 미사용
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].code, "005930")
        self.assertEqual(signals[0].action, "BUY")

    # ── Range 캐시: 날짜 변경 시 갱신 ────────────────────────────────

    async def test_range_cache_refreshes_on_date_change(self):
        """날짜가 바뀌면 Range 캐시를 새로 로드한다."""
        strategy, sqs, tm = self._make_strategy(now_time=_kst(10, 0, "2026-01-15"))
        sqs.get_top_trading_value_stocks.return_value = self._pool_b()
        sqs.get_recent_daily_ohlcv.return_value = _ohlcv_resp(high=72000, low=70000)
        sqs.handle_get_current_stock_price.return_value = _price_resp(
            current=72000, open_price=70000,
            pgtr_ntby_qty=700_000, acml_tr_pbmn=50_000_000_000,
        )
        sqs.get_stock_conclusion.return_value = _conclusion_resp(130.0)

        await strategy.scan()
        first_call_count = sqs.get_recent_daily_ohlcv.call_count

        # 같은 날: 캐시 재사용
        await strategy.scan()
        self.assertEqual(sqs.get_recent_daily_ohlcv.call_count, first_call_count)

        # 날짜 변경 → 캐시 갱신
        tm.get_current_kst_time.return_value = _kst(10, 0, "2026-01-16")
        await strategy.scan()
        self.assertGreater(sqs.get_recent_daily_ohlcv.call_count, first_call_count)

    async def test_scan_skips_candidate_without_code(self):
        strategy, sqs, _ = self._make_strategy()
        sqs.get_top_trading_value_stocks.return_value = ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value,
            msg1="OK",
            data=[{"hts_kor_isnm": "NO_CODE", "stck_avls": "500000000000"}],
        )

        signals = await strategy.scan()

        self.assertEqual(signals, [])
        sqs.handle_get_current_stock_price.assert_not_called()

    async def test_scan_rejects_price_api_failure_and_zero_price(self):
        strategy, sqs, _ = self._make_strategy()
        sqs.get_top_trading_value_stocks.return_value = self._pool_b()
        sqs.get_recent_daily_ohlcv.return_value = _ohlcv_resp(high=72000, low=70000)
        sqs.handle_get_current_stock_price.return_value = ResCommonResponse(
            rt_cd=ErrorCode.API_ERROR.value,
            msg1="err",
        )

        self.assertEqual(await strategy.scan(), [])

        strategy, sqs, _ = self._make_strategy()
        sqs.get_top_trading_value_stocks.return_value = self._pool_b()
        sqs.get_recent_daily_ohlcv.return_value = _ohlcv_resp(high=72000, low=70000)
        sqs.handle_get_current_stock_price.return_value = _price_resp(current=0, open_price=70000)

        self.assertEqual(await strategy.scan(), [])

    async def test_scan_continues_when_candidate_processing_raises(self):
        strategy, sqs, _ = self._make_strategy()
        sqs.get_top_trading_value_stocks.return_value = self._pool_b()
        sqs.get_recent_daily_ohlcv.return_value = _ohlcv_resp(high=72000, low=70000)
        sqs.handle_get_current_stock_price.side_effect = ValueError("boom")

        signals = await strategy.scan()

        self.assertEqual(signals, [])

    async def test_scan_rejects_validity_filter_failures(self):
        strategy, _, _ = self._make_strategy(
            min_market_cap=200_000_000_000,
            min_5d_trading_value=10_000_000_000,
        )

        self.assertFalse(strategy._passes_validity_filter(
            {"market_cap": 100_000_000_000, "avg_5d_tv": 20_000_000_000},
            {"code": "LOW_CAP"},
        ))
        self.assertFalse(strategy._passes_validity_filter(
            {"market_cap": 300_000_000_000, "avg_5d_tv": 1_000_000_000},
            {"code": "LOW_TV"},
        ))

    async def test_validity_filter_rejects_unknown_trading_value(self):
        """fallback 경로에서 avg_5d_tv 가 0 또는 누락이면 fail-closed reject."""
        strategy, _, _ = self._make_strategy(
            min_market_cap=200_000_000_000,
            min_5d_trading_value=10_000_000_000,
        )

        # avg_5d_tv == 0 (fallback 기본값)
        self.assertFalse(strategy._passes_validity_filter(
            {"market_cap": 300_000_000_000, "avg_5d_tv": 0},
            {"code": "UNKNOWN_TV_ZERO"},
        ))

        # avg_5d_tv 누락
        self.assertFalse(strategy._passes_validity_filter(
            {"market_cap": 300_000_000_000},
            {"code": "UNKNOWN_TV_MISSING"},
        ))

        # avg_5d_tv == None
        self.assertFalse(strategy._passes_validity_filter(
            {"market_cap": 300_000_000_000, "avg_5d_tv": None},
            {"code": "UNKNOWN_TV_NONE"},
        ))

    async def test_load_pool_b_returns_empty_when_universe_raises(self):
        strategy, sqs, _ = self._make_strategy()
        universe = MagicMock()
        universe.get_watchlist = AsyncMock(side_effect=RuntimeError("universe down"))
        strategy._universe = universe

        result = await strategy._load_pool_b()

        self.assertEqual(result, [])
        sqs.get_top_trading_value_stocks.assert_not_called()

    async def test_refresh_range_cache_skips_empty_rows_and_logs_exceptions(self):
        strategy, sqs, _ = self._make_strategy()
        sqs.get_recent_daily_ohlcv.side_effect = [
            ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data=[]),
            RuntimeError("ohlcv down"),
        ]

        await strategy._refresh_range_cache("20260115", ["EMPTY", "RAISE"])

        self.assertEqual(strategy._range_cache.ranges, {})
        self.assertEqual(sqs.get_recent_daily_ohlcv.call_count, 2)

    async def test_refresh_range_cache_respects_concurrency_limit(self):
        """일봉 조회는 _RANGE_CACHE_CONCURRENCY를 초과해 동시에 in-flight 되지 않는다."""
        import asyncio

        from strategies import larry_williams_vbo_strategy as vbo_mod

        strategy, sqs, _ = self._make_strategy()

        concurrency_limit = vbo_mod._RANGE_CACHE_CONCURRENCY
        in_flight = 0
        peak = 0
        gate = asyncio.Event()
        call_count = 0

        async def _tracked(code: str, limit: int = 2) -> ResCommonResponse:
            nonlocal in_flight, peak, call_count
            call_count += 1
            in_flight += 1
            peak = max(peak, in_flight)
            if in_flight >= concurrency_limit:
                gate.set()
            try:
                await gate.wait()
                return _ohlcv_resp(high=72000, low=70000)
            finally:
                in_flight -= 1

        sqs.get_recent_daily_ohlcv = _tracked

        codes = [f"C{i:04d}" for i in range(concurrency_limit * 3)]
        await strategy._refresh_range_cache("20260115", codes)

        self.assertEqual(peak, concurrency_limit)
        self.assertEqual(call_count, len(codes))
        self.assertEqual(len(strategy._range_cache.ranges), len(codes))

    async def test_refresh_range_cache_parallel_preserves_per_code_isolation(self):
        """일부 코드가 예외/실패 응답이어도 나머지 코드의 range는 정상 저장된다 (zip 순서 매핑)."""
        strategy, sqs, _ = self._make_strategy()

        good = _ohlcv_resp(high=72000, low=70000)  # range = 2000
        bad = ResCommonResponse(rt_cd=ErrorCode.API_ERROR.value, msg1="err", data=None)
        sqs.get_recent_daily_ohlcv.side_effect = [
            good,
            RuntimeError("ohlcv down"),
            bad,
            good,
        ]

        codes = ["OK1", "RAISE", "FAIL", "OK2"]
        await strategy._refresh_range_cache("20260115", codes)

        self.assertEqual(strategy._range_cache.ranges, {"OK1": 2000.0, "OK2": 2000.0})

    async def test_get_execution_strength_returns_zero_on_exception(self):
        strategy, sqs, _ = self._make_strategy()
        sqs.get_stock_conclusion.side_effect = RuntimeError("conclusion down")

        self.assertEqual(await strategy._get_execution_strength("005930"), 0.0)

    async def test_program_buy_filter_rejects_missing_values_and_bad_payload(self):
        strategy, _, _ = self._make_strategy()

        self.assertFalse(strategy._passes_program_buy_filter(
            {"pgtr_ntby_qty": "1", "stck_prpr": "0", "acml_tr_pbmn": "0"},
            {"code": "ZERO"},
        ))
        self.assertFalse(strategy._passes_program_buy_filter(
            {"pgtr_ntby_qty": "not-int", "stck_prpr": "72000", "acml_tr_pbmn": "50000000000"},
            {"code": "BAD"},
        ))

    async def test_check_exits_skips_invalid_holding_and_zero_current_price(self):
        strategy, sqs, tm = self._make_strategy(now_time=_kst(11, 0))
        today = tm.get_current_kst_time().strftime("%Y%m%d")
        sqs.handle_get_current_stock_price.return_value = _price_resp(current=0, open_price=70000)
        holdings = [
            {"code": "", "buy_price": 70000, "buy_date": today},
            {"code": "005930", "buy_price": 0, "buy_date": today},
            {"code": "000660", "buy_price": 70000, "buy_date": today},
        ]

        signals = await strategy.check_exits(holdings)

        self.assertEqual(signals, [])
        sqs.handle_get_current_stock_price.assert_called_once()

    async def test_check_exits_continues_when_holding_processing_raises(self):
        strategy, sqs, tm = self._make_strategy(now_time=_kst(11, 0))
        today = tm.get_current_kst_time().strftime("%Y%m%d")
        sqs.handle_get_current_stock_price.side_effect = ValueError("price down")
        holdings = [{"code": "005930", "buy_price": 70000, "buy_date": today, "qty": 1}]

        signals = await strategy.check_exits(holdings)

        self.assertEqual(signals, [])


if __name__ == "__main__":
    unittest.main()
