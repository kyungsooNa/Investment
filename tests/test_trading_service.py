import pytest
import unittest
from unittest.mock import AsyncMock, MagicMock
from services.trading_service import TradingService


class TestDefaultRealtimeMessageHandler(unittest.TestCase):
    def setUp(self):
        self.mock_logger = MagicMock()
        self.mock_api_client = MagicMock()
        self.mock_env = MagicMock()
        self.service = TradingService(api_client=self.mock_api_client, env=self.mock_env, logger=self.mock_logger)

    def test_handle_realtime_price(self):
        data = {
            "type": "realtime_price",
            "tr_id": "H0STCNT0",
            "data": {
                "MKSC_SHRN_ISCD": "005930",
                "STCK_PRPR": "80000",
                "PRDY_VRSS": "+500",
                "PRDY_VRSS_SIGN": "2",
                "PRDY_CTRT": "0.6",
                "ACML_VOL": "120000",
                "STCK_CNTG_HOUR": "093015"
            }
        }

        self.service._default_realtime_message_handler(data)
        self.mock_logger.info.assert_any_call(
            "실시간 데이터 수신: Type=realtime_price, TR_ID=H0STCNT0, Data={'MKSC_SHRN_ISCD': '005930', 'STCK_PRPR': '80000', "
            "'PRDY_VRSS': '+500', 'PRDY_VRSS_SIGN': '2', 'PRDY_CTRT': '0.6', 'ACML_VOL': '120000', 'STCK_CNTG_HOUR': '093015'}"
        )

    def test_handle_realtime_quote(self):
        data = {
            "type": "realtime_quote",
            "tr_id": "H0STASP0",
            "data": {
                "유가증권단축종목코드": "005930",
                "매도호가1": "80100",
                "매수호가1": "79900",
                "영업시간": "093030"
            }
        }

        self.service._default_realtime_message_handler(data)
        self.mock_logger.info.assert_any_call("실시간 호가 데이터: 005930 매도1=80100, 매수1=79900")

    def test_handle_signing_notice(self):
        data = {
            "type": "signing_notice",
            "tr_id": "H0TR0002",
            "data": {
                "주문번호": "A123456",
                "체결수량": "10",
                "체결단가": "80000",
                "체결시간": "093045"
            }
        }

        self.service._default_realtime_message_handler(data)
        self.mock_logger.info.assert_any_call("체결통보: 주문=A123456, 수량=10, 단가=80000")

    def test_handle_unknown_type(self):
        data = {
            "type": "unknown_type",
            "tr_id": "X0000001",
            "data": {}
        }

        self.service._default_realtime_message_handler(data)
        self.mock_logger.debug.assert_called_once_with("처리되지 않은 실시간 메시지: X0000001 - {'type': 'unknown_type', 'tr_id': 'X0000001', 'data': {}}")

@pytest.mark.asyncio
class TestGetTop10MarketCapStocksWithPrices:
    def setup_method(self):
        self.mock_api_client = AsyncMock()
        self.mock_env = MagicMock()
        self.mock_time_manager = MagicMock()
        self.mock_logger = MagicMock()
        from services.trading_service import TradingService
        self.service = TradingService(
            api_client=self.mock_api_client,
            env=self.mock_env,
            logger=self.mock_logger,
            time_manager=self.mock_time_manager,
        )

    async def test_market_closed_returns_none(self):
        self.mock_time_manager.is_market_open.return_value = False
        result = await self.service.get_top_10_market_cap_stocks_with_prices()
        assert result is None
        self.mock_logger.warning.assert_any_call("시장이 닫혀 있어 시가총액 1~10위 종목 현재가 조회를 수행할 수 없습니다.")

    async def test_paper_trading_returns_error(self):
        self.mock_time_manager.is_market_open.return_value = True
        self.mock_env.is_paper_trading = True
        result = await self.service.get_top_10_market_cap_stocks_with_prices()
        assert result == {"rt_cd": "1", "msg1": "모의투자 미지원 API입니다."}

    async def test_get_top_stocks_failure(self):
        self.mock_time_manager.is_market_open.return_value = True
        self.mock_env.is_paper_trading = False
        self.service.get_top_market_cap_stocks_code = AsyncMock(return_value={"rt_cd": "1", "msg1": "API 오류"})
        result = await self.service.get_top_10_market_cap_stocks_with_prices()
        assert result is None
        self.mock_logger.error.assert_called()

    async def test_get_top_stocks_empty_list(self):
        self.mock_time_manager.is_market_open.return_value = True
        self.mock_env.is_paper_trading = False
        self.service.get_top_market_cap_stocks_code = AsyncMock(return_value={"rt_cd": "0", "output": []})
        result = await self.service.get_top_10_market_cap_stocks_with_prices()
        assert result is None
        self.mock_logger.info.assert_any_call("시가총액 상위 종목 목록을 찾을 수 없습니다.")

    async def test_successful_flow_returns_results(self):
        self.mock_time_manager.is_market_open.return_value = True
        self.mock_env.is_paper_trading = False
        top_stocks = [
            {"mksc_shrn_iscd": "005930", "hts_kor_isnm": "삼성전자", "data_rank": "1"},
            {"mksc_shrn_iscd": "000660", "hts_kor_isnm": "SK하이닉스", "data_rank": "2"},
        ]
        self.service.get_top_market_cap_stocks_code = AsyncMock(return_value={"rt_cd": "0", "output": top_stocks})
        self.service.get_current_stock_price = AsyncMock(side_effect=[
            {"rt_cd": "0", "output": {"stck_prpr": "80000"}},
            {"rt_cd": "0", "output": {"stck_prpr": "130000"}},
        ])

        result = await self.service.get_top_10_market_cap_stocks_with_prices()
        assert result == [
            {"rank": "1", "name": "삼성전자", "code": "005930", "current_price": "80000"},
            {"rank": "2", "name": "SK하이닉스", "code": "000660", "current_price": "130000"},
        ]
        assert self.service.get_current_stock_price.await_count == 2

    async def test_partial_price_failure(self):
        self.mock_time_manager.is_market_open.return_value = True
        self.mock_env.is_paper_trading = False
        top_stocks = [
            {"mksc_shrn_iscd": "005930", "hts_kor_isnm": "삼성전자", "data_rank": "1"},
            {"mksc_shrn_iscd": "000660", "hts_kor_isnm": "SK하이닉스", "data_rank": "2"},
        ]
        self.service.get_top_market_cap_stocks_code = AsyncMock(return_value={"rt_cd": "0", "output": top_stocks})
        self.service.get_current_stock_price = AsyncMock(side_effect=[
            {"rt_cd": "1", "msg1": "실패"},
            {"rt_cd": "0", "output": {"stck_prpr": "130000"}},
        ])

        result = await self.service.get_top_10_market_cap_stocks_with_prices()
        assert result == [
            {"rank": "2", "name": "SK하이닉스", "code": "000660", "current_price": "130000"}
        ]

    @pytest.mark.asyncio
    async def test_market_cap_limit_10_enforced(self):

        # ─ Setup ─
        mock_api_client = AsyncMock()
        mock_env = MagicMock()
        mock_time_manager = MagicMock()
        mock_logger = MagicMock()
        service = TradingService(
            api_client=mock_api_client,
            env=mock_env,
            logger=mock_logger,
            time_manager=mock_time_manager,
        )

        # ─ Conditions ─
        mock_time_manager.is_market_open.return_value = True
        mock_env.is_paper_trading = False

        # 11개 종목 제공
        top_stocks = [
            {"mksc_shrn_iscd": f"00000{i}", "hts_kor_isnm": f"종목{i}", "data_rank": str(i + 1)}
            for i in range(11)
        ]
        service.get_top_market_cap_stocks_code = AsyncMock(
            return_value={"rt_cd": "0", "output": top_stocks}
        )
        service.get_current_stock_price = AsyncMock(
            side_effect=[
                {"rt_cd": "0", "output": {"stck_prpr": str(10000 + i)}} for i in range(11)
            ]
        )

        # ─ Execute ─
        result = await service.get_top_10_market_cap_stocks_with_prices()

        # ─ Assert ─
        assert len(result) == 10
        assert all("current_price" in r for r in result)
        service.get_current_stock_price.assert_awaited()
        assert service.get_current_stock_price.await_count == 10  # 11개 중 10개만 처리되어야 함