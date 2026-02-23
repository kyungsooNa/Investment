# tests/unit_test/test_program_buy_follow_strategy.py
import unittest
from unittest.mock import MagicMock, AsyncMock
from common.types import ErrorCode, ResCommonResponse
from strategies.program_buy_follow_strategy import ProgramBuyFollowStrategy, ProgramBuyFollowConfig


class TestProgramBuyFollowStrategy(unittest.IsolatedAsyncioTestCase):

    def _make_strategy(self, take_profit=5.0, stop_loss=-3.0):
        ts = MagicMock()
        ts.get_top_trading_value_stocks = AsyncMock()
        ts.get_current_stock_price = AsyncMock()
        sqs = MagicMock()
        tm = MagicMock()

        config = ProgramBuyFollowConfig(
            take_profit_pct=take_profit,
            stop_loss_pct=stop_loss,
        )
        strategy = ProgramBuyFollowStrategy(
            trading_service=ts,
            stock_query_service=sqs,
            time_manager=tm,
            config=config,
        )
        return strategy, ts, sqs, tm

    def test_name(self):
        """전략 이름 테스트."""
        strategy, _, _, _ = self._make_strategy()
        self.assertEqual(strategy.name, "프로그램매수추종")

    async def test_scan_filters_by_program_net_buy(self):
        """pgtr_ntby_qty 양수인 종목만 BUY 시그널 생성 테스트."""
        strategy, ts, _, _ = self._make_strategy()

        ts.get_top_trading_value_stocks.return_value = ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value,
            msg1="OK",
            data=[
                {"mksc_shrn_iscd": "005930", "hts_kor_isnm": "삼성전자"},
                {"mksc_shrn_iscd": "000660", "hts_kor_isnm": "SK하이닉스"},
                {"mksc_shrn_iscd": "035420", "hts_kor_isnm": "NAVER"},
            ]
        )

        async def mock_full_price(code):
            responses = {
                "005930": {"output": {"stck_prpr": "70000", "pgtr_ntby_qty": "5000"}},
                "000660": {"output": {"stck_prpr": "120000", "pgtr_ntby_qty": "-200"}},
                "035420": {"output": {"stck_prpr": "300000", "pgtr_ntby_qty": "3000"}},
            }
            return ResCommonResponse(
                rt_cd=ErrorCode.SUCCESS.value, msg1="OK",
                data=responses.get(code, {})
            )

        ts.get_current_stock_price.side_effect = mock_full_price

        signals = await strategy.scan()

        # 000660은 pgtr_ntby_qty < 0이므로 제외, 나머지 2개
        self.assertEqual(len(signals), 2)
        codes = [s.code for s in signals]
        self.assertIn("005930", codes)
        self.assertIn("035420", codes)
        self.assertNotIn("000660", codes)

        # 5000 > 3000 이므로 005930이 먼저
        self.assertEqual(signals[0].code, "005930")
        self.assertEqual(signals[0].action, "BUY")

    async def test_scan_empty_on_api_failure(self):
        """거래대금 API 실패 시 빈 리스트 반환."""
        strategy, ts, _, _ = self._make_strategy()
        ts.get_top_trading_value_stocks.return_value = ResCommonResponse(
            rt_cd=ErrorCode.API_ERROR.value, msg1="Error", data=None
        )

        signals = await strategy.scan()
        self.assertEqual(len(signals), 0)

    async def test_check_exits_take_profit(self):
        """매수가 대비 +5% 이상이면 익절 SELL 시그널 테스트."""
        strategy, ts, _, tm = self._make_strategy(take_profit=5.0)

        import pytz
        from datetime import datetime
        kst = pytz.timezone("Asia/Seoul")
        now = kst.localize(datetime(2026, 2, 20, 10, 0))
        close = kst.localize(datetime(2026, 2, 20, 15, 30))
        tm.get_current_kst_time.return_value = now
        tm.get_market_close_time.return_value = close

        ts.get_current_stock_price.return_value = ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value, msg1="OK",
            data={"output": {"stck_prpr": "73600", "pgtr_ntby_qty": "1000", "bstp_kor_isnm": "전기전자"}}
        )

        holdings = [{"code": "005930", "buy_price": 70000}]
        signals = await strategy.check_exits(holdings)

        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].action, "SELL")
        self.assertIn("익절", signals[0].reason)

    async def test_check_exits_program_reversal(self):
        """프로그램 매도 전환 시 SELL 시그널 테스트."""
        strategy, ts, _, tm = self._make_strategy()

        import pytz
        from datetime import datetime
        kst = pytz.timezone("Asia/Seoul")
        now = kst.localize(datetime(2026, 2, 20, 11, 0))
        close = kst.localize(datetime(2026, 2, 20, 15, 30))
        tm.get_current_kst_time.return_value = now
        tm.get_market_close_time.return_value = close

        ts.get_current_stock_price.return_value = ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value, msg1="OK",
            data={"output": {"stck_prpr": "71000", "pgtr_ntby_qty": "-500", "bstp_kor_isnm": "전기전자"}}
        )

        holdings = [{"code": "005930", "buy_price": 70000}]
        signals = await strategy.check_exits(holdings)

        self.assertEqual(len(signals), 1)
        self.assertIn("프로그램매도전환", signals[0].reason)

    async def test_check_exits_stop_loss(self):
        """매수가 대비 -3% 이하이면 손절 SELL 시그널 테스트."""
        strategy, ts, _, tm = self._make_strategy(stop_loss=-3.0)

        import pytz
        from datetime import datetime
        kst = pytz.timezone("Asia/Seoul")
        now = kst.localize(datetime(2026, 2, 20, 10, 0))
        close = kst.localize(datetime(2026, 2, 20, 15, 30))
        tm.get_current_kst_time.return_value = now
        tm.get_market_close_time.return_value = close

        ts.get_current_stock_price.return_value = ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value, msg1="OK",
            data={"output": {"stck_prpr": "67800", "pgtr_ntby_qty": "100", "bstp_kor_isnm": "전기전자"}}
        )

        holdings = [{"code": "005930", "buy_price": 70000}]
        signals = await strategy.check_exits(holdings)

        self.assertEqual(len(signals), 1)
        self.assertIn("손절", signals[0].reason)


if __name__ == "__main__":
    unittest.main()
