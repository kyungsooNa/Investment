# tests/unit_test/test_program_buy_follow_strategy.py
import unittest
from unittest.mock import MagicMock, AsyncMock
from common.types import ErrorCode, ResCommonResponse, ResStockFullInfoApiOutput
from strategies.program_buy_follow_strategy import ProgramBuyFollowStrategy, ProgramBuyFollowConfig
from typing import Optional


class TestProgramBuyFollowStrategy(unittest.IsolatedAsyncioTestCase):

    def _make_strategy(self, **config_kwargs):
        sqs = MagicMock()
        sqs.get_top_trading_value_stocks = AsyncMock()
        sqs.get_current_price = AsyncMock()
        tm = MagicMock()
        from datetime import datetime
        import pytz
        kst = pytz.timezone("Asia/Seoul")
        tm.get_current_kst_time.return_value = kst.localize(datetime(2025, 1, 1, 10, 0))

        config = ProgramBuyFollowConfig(**config_kwargs)
        strategy = ProgramBuyFollowStrategy(
            stock_query_service=sqs,
            time_manager=tm,
            config=config,
        )
        strategy._logger = MagicMock()
        return strategy, sqs, tm

    def test_name(self):
        """전략 이름 테스트."""
        strategy, _, _ = self._make_strategy()
        self.assertEqual(strategy.name, "프로그램매수추종")

    async def test_scan_filters_and_sorts_by_program_buy_ratio(self):
        """프로그램 순매수 비중(%)으로 필터링 및 정렬 테스트."""
        # min_program_buy_ratio 기본값 5.0%
        strategy, sqs, _ = self._make_strategy()

        sqs.get_top_trading_value_stocks.return_value = ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value,
            msg1="OK",
            data=[
                {"mksc_shrn_iscd": "A", "hts_kor_isnm": "StockA"}, # Ratio 10.0% -> Pass
                {"mksc_shrn_iscd": "B", "hts_kor_isnm": "StockB"}, # Ratio 4.0% -> Fail
                {"mksc_shrn_iscd": "C", "hts_kor_isnm": "StockC"}, # Ratio 6.67% -> Pass
                {"mksc_shrn_iscd": "D", "hts_kor_isnm": "StockD"}, # Net Sell -> Fail
            ]
        )

        async def mock_full_price(code, *args, **kwargs):
            responses = {
                "A": {"output": {"stck_prpr": "50000", "pgtr_ntby_qty": "10000", "acml_tr_pbmn": "5000000000"}},
                "B": {"output": {"stck_prpr": "200000", "pgtr_ntby_qty": "5000", "acml_tr_pbmn": "25000000000"}},
                "C": {"output": {"stck_prpr": "10000", "pgtr_ntby_qty": "20000", "acml_tr_pbmn": "3000000000"}},
                "D": {"output": {"stck_prpr": "10000", "pgtr_ntby_qty": "-100", "acml_tr_pbmn": "1000000000"}},
            }
            return ResCommonResponse(
                rt_cd=ErrorCode.SUCCESS.value, msg1="OK",
                data=responses.get(code, {})
            )
        sqs.get_current_price.side_effect = mock_full_price

        signals = await strategy.scan()

        # A(10%), C(6.67%) 통과. B(4%), D(매도) 탈락.
        self.assertEqual(len(signals), 2)
        
        # 정렬 순서: A > C
        self.assertEqual(signals[0].code, "A")
        self.assertEqual(signals[1].code, "C")

    async def test_scan_empty_on_api_failure(self):
        """거래대금 API 실패 시 빈 리스트 반환."""
        strategy, sqs, _ = self._make_strategy()
        sqs.get_top_trading_value_stocks.return_value = ResCommonResponse(
            rt_cd=ErrorCode.API_ERROR.value, msg1="Error", data=None
        )

        signals = await strategy.scan()
        self.assertEqual(len(signals), 0)

    async def test_check_exits_trailing_stop(self):
        """고가 대비 설정 비율(-8%) 이상 하락 시 익절(트레일링) SELL 신호 테스트."""
        strategy, sqs, tm = self._make_strategy(trailing_stop_pct=8.0)

        import pytz
        from datetime import datetime
        kst = pytz.timezone("Asia/Seoul")
        now = kst.localize(datetime(2026, 2, 20, 10, 0))
        close = kst.localize(datetime(2026, 2, 20, 15, 30))
        tm.get_current_kst_time.return_value = now
        tm.get_market_close_time.return_value = close

        # 고가 80000원, 현재가 73600원 -> -8% 하락
        sqs.get_current_price.return_value = ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value, msg1="OK",
            data={"output": {
                "stck_prpr": "73600",
                "stck_hgpr": "80000",  # 고가
                "pgtr_ntby_qty": "1000",
                "bstp_kor_isnm": "전기전자"
            }}
        )

        holdings = [{"code": "005930", "buy_price": 70000}]
        signals = await strategy.check_exits(holdings)

        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].action, "SELL")
        self.assertIn("익절(트레일링)", signals[0].reason)

    async def test_check_exits_program_reversal(self):
        """프로그램 매도 전환 시 SELL 시그널 테스트."""
        strategy, sqs, tm = self._make_strategy()

        import pytz
        from datetime import datetime
        kst = pytz.timezone("Asia/Seoul")
        now = kst.localize(datetime(2026, 2, 20, 11, 0))
        close = kst.localize(datetime(2026, 2, 20, 15, 30))
        tm.get_current_kst_time.return_value = now
        tm.get_market_close_time.return_value = close

        sqs.get_current_price.return_value = ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value, msg1="OK",
            data={"output": {"stck_prpr": "71000", "stck_hgpr": "72000", "pgtr_ntby_qty": "-500", "bstp_kor_isnm": "전기전자"}}
        )

        holdings = [{"code": "005930", "buy_price": 70000}]
        signals = await strategy.check_exits(holdings)

        self.assertEqual(len(signals), 1)
        self.assertIn("프로그램매도전환", signals[0].reason)

    async def test_check_exits_stop_loss(self):
        """매수가 대비 -3% 이하이면 손절 SELL 시그널 테스트."""
        strategy, sqs, tm = self._make_strategy(stop_loss_pct=-3.0)

        import pytz
        from datetime import datetime
        kst = pytz.timezone("Asia/Seoul")
        now = kst.localize(datetime(2026, 2, 20, 10, 0))
        close = kst.localize(datetime(2026, 2, 20, 15, 30))
        tm.get_current_kst_time.return_value = now
        tm.get_market_close_time.return_value = close

        sqs.get_current_price.return_value = ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value, msg1="OK",
            data={"output": {"stck_prpr": "67800", "stck_hgpr": "70000", "pgtr_ntby_qty": "100", "bstp_kor_isnm": "전기전자"}}
        )

        holdings = [{"code": "005930", "buy_price": 70000}]
        signals = await strategy.check_exits(holdings)

        self.assertEqual(len(signals), 1)
        self.assertIn("손절", signals[0].reason)

    async def test_scan_various_skip_conditions(self):
        """scan() 내부의 다양한 예외/skip 조건 테스트 (커버리지 향상)."""
        strategy, sqs, _ = self._make_strategy()

        # 1. 거래대금 상위 목록 (여러 케이스 포함)
        sqs.get_top_trading_value_stocks.return_value = ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value, msg1="OK",
            data=[
                {"mksc_shrn_iscd": "", "hts_kor_isnm": "코드없음"},  # Skip: Code missing
                {"mksc_shrn_iscd": "ERR001", "hts_kor_isnm": "API에러"},  # Skip: Detail API Error
                {"mksc_shrn_iscd": "NOOUT", "hts_kor_isnm": "Output없음"},  # Skip: No Output
                {"mksc_shrn_iscd": "ZERO", "hts_kor_isnm": "가격0"},  # Skip: Price 0
                {"mksc_shrn_iscd": "EXCEPT", "hts_kor_isnm": "예외발생"},  # Skip: Exception
                {"mksc_shrn_iscd": "005930", "hts_kor_isnm": "정상"},  # Success
            ]
        )

        async def mock_detail(code, *args, **kwargs):
            if code == "ERR001":
                return ResCommonResponse(rt_cd=ErrorCode.API_ERROR.value, msg1="Fail")
            if code == "NOOUT":
                return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data={})  # No output key
            if code == "ZERO":
                return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data={"output": {"stck_prpr": "0", "pgtr_ntby_qty": "100"}})
            if code == "EXCEPT":
                raise ValueError("Test Exception")
            if code == "005930":
                return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data={"output": {"stck_prpr": "1000", "pgtr_ntby_qty": "100", "acml_tr_pbmn": "10000"}})
            return None

        sqs.get_current_price.side_effect = mock_detail

        signals = await strategy.scan()

        # 정상인 1개만 잡혀야 함
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].code, "005930")

    async def test_check_exits_time_exit(self):
        """장 마감 15분 전 시간 청산 테스트."""
        strategy, sqs, tm = self._make_strategy()

        import pytz
        from datetime import datetime, timedelta
        kst = pytz.timezone("Asia/Seoul")
        # 마감 10분 전 설정
        close = kst.localize(datetime(2026, 2, 20, 15, 30))
        now = close - timedelta(minutes=10)

        tm.get_current_kst_time.return_value = now
        tm.get_market_close_time.return_value = close

        sqs.get_current_price.return_value = ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value, msg1="OK",
            data={"output": {"stck_prpr": "70000", "stck_hgpr": "71000", "pgtr_ntby_qty": "100", "bstp_kor_isnm": "삼성전자"}}
        )

        holdings = [{"code": "005930", "buy_price": 70000}]
        signals = await strategy.check_exits(holdings)

        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].action, "SELL")
        self.assertIn("시간청산", signals[0].reason)

    async def test_check_exits_hold_and_skips(self):
        """청산 조건 미달(Hold) 및 잘못된 데이터 Skip 테스트."""
        strategy, sqs, tm = self._make_strategy()

        import pytz
        from datetime import datetime
        kst = pytz.timezone("Asia/Seoul")
        now = kst.localize(datetime(2026, 2, 20, 10, 0))
        close = kst.localize(datetime(2026, 2, 20, 15, 30))
        tm.get_current_kst_time.return_value = now
        tm.get_market_close_time.return_value = close

        async def mock_price(code):
            if code == "APIERR":
                return ResCommonResponse(rt_cd="999", msg1="Err")
            if code == "NOOUT":
                return ResCommonResponse(rt_cd="0", msg1="OK", data={})
            if code == "ZERO":
                return ResCommonResponse(rt_cd="0", msg1="OK", data={"output": {"stck_prpr": "0", "stck_hgpr": "0"}})
            if code == "EXCEPT":
                raise RuntimeError("Crash")
            # Normal Hold Case
            return ResCommonResponse(rt_cd="0", msg1="OK", data={"output": {
                "stck_prpr": "70500", "stck_hgpr": "71000", "pgtr_ntby_qty": "1000", "bstp_kor_isnm": "삼성전자"
            }})

        sqs.get_current_price.side_effect = mock_price

        holdings = [
            {"code": "", "buy_price": 100},  # Skip: No code
            {"code": "APIERR", "buy_price": 100},  # Skip: API Fail
            {"code": "NOOUT", "buy_price": 100},  # Skip: No Output
            {"code": "ZERO", "buy_price": 100},  # Skip: Price 0
            {"code": "EXCEPT", "buy_price": 100},  # Skip: Exception
            {"code": "005930", "buy_price": 70000},  # Hold: Price up slightly, program buy positive
        ]

        signals = await strategy.check_exits(holdings)

        # 아무 신호도 없어야 함 (모두 Skip되거나 Hold)
        self.assertEqual(len(signals), 0)

    def test_utils_coverage(self):
        """내부 유틸 메서드 커버리지 (객체 접근, 예외 처리 등)."""
        strategy, _, _ = self._make_strategy()

        # 1. _get_int_field with object
        class DummyOutput:
            def __init__(self):
                self.val = "123"
                self.bad = "abc"

        obj = DummyOutput()
        self.assertEqual(strategy._get_int_field(obj, "val"), 123)
        self.assertEqual(strategy._get_int_field(obj, "bad"), 0)  # ValueError -> 0
        self.assertEqual(strategy._get_int_field(obj, "none"), 0)  # AttributeError -> 0 (getattr default)

        # 2. _get_str_field with object
        class DummyOutputStr:
            def __init__(self):
                self.name = "Test"

        obj_str = DummyOutputStr()
        self.assertEqual(strategy._get_str_field(obj_str, "name"), "Test")
        self.assertEqual(strategy._get_str_field(obj_str, "empty"), "")

        # 3. _extract_output
        from dataclasses import dataclass

        @dataclass
        class DummyData:
            field: str

        # Case A: data is a dict with 'output'
        resp_dict = ResCommonResponse(rt_cd="0", msg1="OK", data={"output": {"a": 1}})
        self.assertEqual(strategy._extract_output(resp_dict), {"a": 1})

        # Case B: data is a dataclass
        dummy_obj = DummyData(field="test")
        resp_obj = ResCommonResponse(rt_cd="0", msg1="OK", data=dummy_obj)
        self.assertEqual(strategy._extract_output(resp_obj), dummy_obj)

        # Case C: data is None
        resp_none = ResCommonResponse(rt_cd="0", msg1="OK", data=None)
        self.assertIsNone(strategy._extract_output(resp_none))

    async def test_scan_reentry_disallowed(self):
        """allow_reentry=False일 때 당일 재진입 방지 테스트."""
        strategy, sqs, _ = self._make_strategy(allow_reentry=False)

        sqs.get_top_trading_value_stocks.return_value = ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value, msg1="OK",
            data=[{"mksc_shrn_iscd": "005930", "hts_kor_isnm": "삼성전자"}]
        )
        sqs.get_current_price.return_value = ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value, msg1="OK",
            data={"output": {"stck_prpr": "1000", "pgtr_ntby_qty": "100", "acml_tr_pbmn": "10000"}} # Ratio 100%
        )

        # 첫 번째 스캔: 매수 신호 발생
        signals1 = await strategy.scan()
        self.assertEqual(len(signals1), 1)
        self.assertIn("005930", strategy._bought_today)

        # 두 번째 스캔: 이미 매수했으므로 신호 없음
        signals2 = await strategy.scan()
        self.assertEqual(len(signals2), 0)

    async def test_scan_reentry_reset_on_new_day(self):
        """날짜 변경 시 재진입 목록(_bought_today) 초기화 테스트."""
        strategy, sqs, tm = self._make_strategy(allow_reentry=False)
        
        from datetime import datetime
        import pytz
        kst = pytz.timezone("Asia/Seoul")

        sqs.get_top_trading_value_stocks.return_value = ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value, msg1="OK",
            data=[{"mksc_shrn_iscd": "005930", "hts_kor_isnm": "삼성전자"}]
        )
        sqs.get_current_price.return_value = ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value, msg1="OK",
            data={"output": {"stck_prpr": "1000", "pgtr_ntby_qty": "100", "acml_tr_pbmn": "10000"}}
        )

        # Day 1
        tm.get_current_kst_time.return_value = kst.localize(datetime(2025, 1, 1, 11, 0))
        signals1 = await strategy.scan()
        self.assertEqual(len(signals1), 1)
        self.assertIn("005930", strategy._bought_today)

        # Day 2
        tm.get_current_kst_time.return_value = kst.localize(datetime(2025, 1, 2, 11, 0))
        signals2 = await strategy.scan()
        self.assertEqual(len(signals2), 1) # 목록 초기화되어 다시 매수
        self.assertIn("005930", strategy._bought_today)


if __name__ == "__main__":
    unittest.main()
