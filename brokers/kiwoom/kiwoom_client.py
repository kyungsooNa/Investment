import logging
from typing import Optional, TYPE_CHECKING

from brokers.kiwoom.kiwoom_account_api import KiwoomAccountApi
from brokers.kiwoom.kiwoom_quotations_api import KiwoomQuotationsApi
from brokers.kiwoom.kiwoom_trading_api import KiwoomTradingApi
from common.types import ErrorCode, Exchange, ResCommonResponse

if TYPE_CHECKING:
    from core.logger import StreamingEventLogger


class KiwoomApiClient:
    """키움 REST/WebSocket API 통합 클라이언트 골격."""

    def __init__(self, env, logger=None, market_clock=None, market_calendar_service=None,
                 streaming_logger: Optional["StreamingEventLogger"] = None):
        self._env = env
        self._logger = logger if logger else logging.getLogger(__name__)
        self.market_clock = market_clock
        self._mcs = market_calendar_service
        self._streaming_logger = streaming_logger
        self._quotations = KiwoomQuotationsApi(env, logger=self._logger, market_clock=market_clock)
        self._account = KiwoomAccountApi(env, logger=self._logger, market_clock=market_clock)
        self._trading = KiwoomTradingApi(env, logger=self._logger, market_clock=market_clock)

    def _unsupported(self, feature: str) -> ResCommonResponse:
        return ResCommonResponse(
            rt_cd=ErrorCode.API_ERROR.value,
            msg1=f"Kiwoom API 기능 미구현: {feature}",
            data={"broker": "kiwoom", "feature": feature},
        )

    async def get_stock_info_by_code(self, stock_code: str, exchange: Exchange = Exchange.KRX) -> ResCommonResponse:
        return await self._quotations.get_stock_info_by_code(stock_code, exchange=exchange)

    async def get_current_price(self, code: str, exchange: Exchange = Exchange.KRX) -> ResCommonResponse:
        return await self._quotations.get_current_price(code, exchange=exchange)

    async def inquire_daily_itemchartprice(self, stock_code: str, start_date: str, end_date: str,
                                           fid_period_div_code: str = "D",
                                           exchange: Exchange = Exchange.KRX) -> ResCommonResponse:
        return await self._quotations.inquire_daily_itemchartprice(
            stock_code, start_date, end_date,
            fid_period_div_code=fid_period_div_code, exchange=exchange,
        )

    async def get_intraday_minutes(self, stock_code: str, tick_scope: str = "1",
                                   base_date: str = "",
                                   exchange: Exchange = Exchange.KRX) -> ResCommonResponse:
        return await self._quotations.get_intraday_minutes(
            stock_code, tick_scope=tick_scope, base_date=base_date, exchange=exchange,
        )

    async def get_account_balance(self, exchange: Exchange = Exchange.KRX) -> ResCommonResponse:
        return await self._account.get_account_balance(exchange=exchange)

    async def place_stock_order(self, stock_code, order_price, order_qty, is_buy: bool,
                                exchange: Exchange = Exchange.KRX) -> ResCommonResponse:
        return await self._trading.place_stock_order(
            stock_code, order_price, order_qty, is_buy, exchange=exchange,
        )

    async def cancel_stock_order(self, **kwargs) -> ResCommonResponse:
        return await self._trading.cancel_stock_order(**kwargs)

    def is_websocket_receive_alive(self) -> bool:
        return False

    async def connect_websocket(self, on_message_callback=None):
        return False

    async def disconnect_websocket(self):
        return True

    async def subscribe_realtime_price(self, stock_code: str):
        return False

    async def unsubscribe_realtime_price(self, stock_code: str):
        return True

    async def subscribe_realtime_quote(self, stock_code: str):
        return False

    async def unsubscribe_realtime_quote(self, stock_code: str):
        return True

    def get_subscription_ledger(self) -> dict:
        return {}
