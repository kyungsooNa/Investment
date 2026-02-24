import unittest
from unittest.mock import MagicMock, AsyncMock
from strategies.volume_breakout_strategy import VolumeBreakoutStrategy

class TestVolumeBreakoutStrategy(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.mock_sqs = MagicMock()
        self.mock_tm = MagicMock()
        self.mock_logger = MagicMock()
        
        # TimeManager mock setup
        # to_hhmmss는 입력을 그대로 반환하거나 간단한 처리를 하도록 설정
        self.mock_tm.to_hhmmss.side_effect = lambda x: str(x).zfill(6)
        self.mock_tm.get_current_kst_time.return_value.strftime.return_value = "20250101"

        self.strategy = VolumeBreakoutStrategy(
            stock_query_service=self.mock_sqs,
            time_manager=self.mock_tm,
            logger=self.mock_logger
        )

    async def test_backtest_no_data(self):
        """분봉 데이터가 없을 때"""
        self.mock_sqs.get_day_intraday_minutes_list = AsyncMock(return_value=[])
        result = await self.strategy.backtest_open_threshold_intraday("005930")
        self.assertFalse(result["ok"])
        self.assertEqual(result["message"], "분봉 데이터 없음")

    async def test_backtest_open_price_parsing_error(self):
        """시가 데이터 파싱 실패 시"""
        # 유효하지 않은 시가 데이터
        rows = [{"stck_bsop_date": "20250101", "stck_cntg_hour": "090000", "stck_oprc": "invalid"}]
        self.mock_sqs.get_day_intraday_minutes_list = AsyncMock(return_value=rows)
        
        result = await self.strategy.backtest_open_threshold_intraday("005930")
        self.assertFalse(result["ok"])
        self.assertIn("시가 파싱 실패", result["message"])

    async def test_backtest_no_trigger(self):
        """매수 트리거 조건에 도달하지 못했을 때"""
        # 시가 10000, 고가 10500 (+5%) -> 트리거(10%) 미달
        rows = [
            {"stck_bsop_date": "20250101", "stck_cntg_hour": "090000", "stck_oprc": "10000", "stck_prpr": "10000"},
            {"stck_bsop_date": "20250101", "stck_cntg_hour": "090100", "stck_prpr": "10500"},
        ]
        self.mock_sqs.get_day_intraday_minutes_list = AsyncMock(return_value=rows)
        
        result = await self.strategy.backtest_open_threshold_intraday("005930", trigger_pct=10.0)
        self.assertTrue(result["ok"])
        self.assertIn("트리거 10.0% 미발생", result["message"])
        self.assertEqual(len(result["trades"]), 0)

    async def test_backtest_trailing_stop(self):
        """트레일링 스탑 익절 테스트"""
        # 시가 10000
        # 09:01: 11000 (+10%) -> 매수 진입
        # 09:02: 12000 (+20%) -> 고가 갱신
        # 09:03: 11300 -> 고가(12000) 대비 -5.8% 하락 -> 트레일링 스탑(-5%) 조건 충족
        rows = [
            {"stck_bsop_date": "20250101", "stck_cntg_hour": "090000", "stck_oprc": "10000", "stck_prpr": "10000"},
            {"stck_bsop_date": "20250101", "stck_cntg_hour": "090100", "stck_prpr": "11000"}, # Entry
            {"stck_bsop_date": "20250101", "stck_cntg_hour": "090200", "stck_prpr": "12000"}, # New High
            {"stck_bsop_date": "20250101", "stck_cntg_hour": "090300", "stck_prpr": "11300"}, # Exit
        ]
        self.mock_sqs.get_day_intraday_minutes_list = AsyncMock(return_value=rows)
        
        result = await self.strategy.backtest_open_threshold_intraday(
            "005930", trigger_pct=10.0, trailing_stop_pct=5.0
        )
        
        self.assertTrue(result["ok"])
        trades = result["trades"]
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["outcome"], "trailing_stop")
        self.assertEqual(trades[0]["entry_px"], 11000.0)
        self.assertEqual(trades[0]["exit_px"], 11300.0)

    async def test_backtest_stop_loss(self):
        """손절 테스트"""
        # 시가 10000
        # 09:01: 11000 (+10%) -> 매수 진입
        # 09:02: 10400 -> 매수가(11000) 대비 -5.45% 하락 -> 손절(-5%) 조건 충족
        rows = [
            {"stck_bsop_date": "20250101", "stck_cntg_hour": "090000", "stck_oprc": "10000", "stck_prpr": "10000"},
            {"stck_bsop_date": "20250101", "stck_cntg_hour": "090100", "stck_prpr": "11000"}, # Entry
            {"stck_bsop_date": "20250101", "stck_cntg_hour": "090200", "stck_prpr": "10400"}, # Stop Loss
        ]
        self.mock_sqs.get_day_intraday_minutes_list = AsyncMock(return_value=rows)
        
        result = await self.strategy.backtest_open_threshold_intraday(
            "005930", trigger_pct=10.0, sl_pct=-5.0
        )
        
        self.assertTrue(result["ok"])
        trades = result["trades"]
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["outcome"], "stop_loss")
        self.assertEqual(trades[0]["exit_px"], 10400.0)

    async def test_backtest_close_exit(self):
        """장 마감 청산 테스트"""
        # 시가 10000
        # 09:01: 11000 (+10%) -> 매수 진입
        # 이후 손절/익절 조건에 닿지 않고 장 마감(15:30) 가격 11500원으로 종료
        rows = [
            {"stck_bsop_date": "20250101", "stck_cntg_hour": "090000", "stck_oprc": "10000", "stck_prpr": "10000"},
            {"stck_bsop_date": "20250101", "stck_cntg_hour": "090100", "stck_prpr": "11000"}, # Entry
            {"stck_bsop_date": "20250101", "stck_cntg_hour": "153000", "stck_prpr": "11500"}, # Close
        ]
        self.mock_sqs.get_day_intraday_minutes_list = AsyncMock(return_value=rows)
        
        result = await self.strategy.backtest_open_threshold_intraday(
            "005930", trigger_pct=10.0, trailing_stop_pct=10.0, sl_pct=-10.0
        )
        
        self.assertTrue(result["ok"])
        trades = result["trades"]
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["outcome"], "close_exit")
        self.assertEqual(trades[0]["exit_px"], 11500.0)

if __name__ == "__main__":
    unittest.main()