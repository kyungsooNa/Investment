# tests/unit_test/test_volume_breakout_live_strategy.py
import unittest
from unittest.mock import MagicMock, AsyncMock
from common.types import ErrorCode, ResCommonResponse
from strategies.volume_breakout_live_strategy import VolumeBreakoutLiveStrategy
from strategies.volume_breakout_strategy import VolumeBreakoutConfig


class TestVolumeBreakoutLiveStrategy(unittest.IsolatedAsyncioTestCase):

    def _make_strategy(self, trigger_pct=10.0, trailing_stop_pct=8.0, stop_loss_pct=8.0):
        ts = MagicMock()
        ts.get_top_trading_value_stocks = AsyncMock()
        sqs = MagicMock()
        sqs.handle_get_current_stock_price = AsyncMock()
        tm = MagicMock()

        config = VolumeBreakoutConfig(
            trigger_pct=trigger_pct,
            trailing_stop_pct=trailing_stop_pct,
            stop_loss_pct=stop_loss_pct,
        )
        strategy = VolumeBreakoutLiveStrategy(
            trading_service=ts,
            stock_query_service=sqs,
            time_manager=tm,
            config=config,
        )
        return strategy, ts, sqs, tm

    def test_name(self):
        """전략 이름이 '거래량돌파'인지 테스트."""
        strategy, _, _, _ = self._make_strategy()
        self.assertEqual(strategy.name, "거래량돌파")

    async def test_scan_returns_buy_signals_when_trigger_met(self):
        """시가 대비 trigger_pct 이상 상승 종목에 BUY 시그널 생성 테스트."""
        strategy, ts, sqs, _ = self._make_strategy(trigger_pct=10.0)

        # 거래대금 상위 종목
        ts.get_top_trading_value_stocks.return_value = ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value,
            msg1="OK",
            data=[
                {"mksc_shrn_iscd": "005930", "hts_kor_isnm": "삼성전자", "acml_vol": "5000000"},
                {"mksc_shrn_iscd": "000660", "hts_kor_isnm": "SK하이닉스", "acml_vol": "3000000"},
            ]
        )

        # 현재가 응답: 삼성전자(시가 대비 +12%), SK하이닉스(시가 대비 +5%)
        async def mock_price(code):
            if code == "005930":
                return ResCommonResponse(
                    rt_cd=ErrorCode.SUCCESS.value, msg1="OK",
                    data={"price": "78400", "open": "70000"}  # +12%
                )
            return ResCommonResponse(
                rt_cd=ErrorCode.SUCCESS.value, msg1="OK",
                data={"price": "126000", "open": "120000"}  # +5%
            )

        sqs.handle_get_current_stock_price.side_effect = mock_price

        signals = await strategy.scan()

        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].code, "005930")
        self.assertEqual(signals[0].action, "BUY")
        self.assertIn("+12.0%", signals[0].reason)

    async def test_scan_returns_empty_on_api_failure(self):
        """거래대금 API 실패 시 빈 리스트 반환 테스트."""
        strategy, ts, _, _ = self._make_strategy()
        ts.get_top_trading_value_stocks.return_value = ResCommonResponse(
            rt_cd=ErrorCode.API_ERROR.value, msg1="Error", data=None
        )

        signals = await strategy.scan()
        self.assertEqual(len(signals), 0)

    async def test_check_exits_trailing_stop(self):
        """고가 대비 설정 비율(-8%) 하락 시 익절(트레일링) SELL 신호 테스트."""
        strategy, _, sqs, tm = self._make_strategy(trailing_stop_pct=8.0)

        # 장 마감까지 충분한 시간 남음
        import pytz
        from datetime import datetime
        kst = pytz.timezone("Asia/Seoul")
        now = kst.localize(datetime(2026, 2, 20, 10, 0))
        close = kst.localize(datetime(2026, 2, 20, 15, 30))
        tm.get_current_kst_time.return_value = now
        tm.get_market_close_time.return_value = close

        # 고가 80000원, 현재가 73600원 -> 고가 대비 -8%
        sqs.handle_get_current_stock_price.return_value = ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value, msg1="OK",
            data={"price": "73600", "open": "70000", "high": "80000", "code": "005930"}
        )

        holdings = [{"code": "005930", "buy_price": 72000}]
        signals = await strategy.check_exits(holdings)

        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].action, "SELL")
        self.assertIn("익절(트레일링)", signals[0].reason)

    async def test_check_exits_time_exit(self):
        """장 마감 15분 전 시간청산 SELL 시그널 테스트."""
        strategy, _, sqs, tm = self._make_strategy()

        import pytz
        from datetime import datetime
        kst = pytz.timezone("Asia/Seoul")
        now = kst.localize(datetime(2026, 2, 20, 15, 20))  # 마감 10분 전
        close = kst.localize(datetime(2026, 2, 20, 15, 30))
        tm.get_current_kst_time.return_value = now
        tm.get_market_close_time.return_value = close

        sqs.handle_get_current_stock_price.return_value = ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value, msg1="OK",
            data={"price": "78000", "open": "70000", "high": "79000", "code": "005930"}
        )

        holdings = [{"code": "005930", "buy_price": 72000}]
        signals = await strategy.check_exits(holdings)

        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].action, "SELL")
        self.assertIn("시간청산", signals[0].reason)


if __name__ == "__main__":
    unittest.main()
