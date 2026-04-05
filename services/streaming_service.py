"""
WebSocket 스트리밍 관련 기능을 담당하는 서비스.

역할:
  - WebSocket 연결/해제 수명주기 관리 (connect, disconnect)
  - 실시간 데이터 구독/해지 (subscribe/unsubscribe)
  - 수신 메시지 dispatch 및 최신가 메모리 캐시 유지
  - 프로그램매매 히스토리 조회 (REST)

ProgramTradingStreamService와의 역할 구분:
  - StreamingService           : WebSocket 연결·구독·메시지 처리 (프로토콜 레이어)
  - ProgramTradingStreamService: 프로그램매매 데이터의 저장·버퍼링·SSE 배포 (데이터 레이어)
"""
from __future__ import annotations

import time
from typing import Optional, Dict, TYPE_CHECKING

from common.types import ResCommonResponse, ErrorCode

if TYPE_CHECKING:
    from brokers.broker_api_wrapper import BrokerAPIWrapper
    from services.market_data_service import MarketDataService
    from services.price_stream_service import PriceStreamService
    from core.logger import StreamingEventLogger


class StreamingService:
    """
    WebSocket 연결·구독·메시지 dispatch를 담당하는 서비스.
    BrokerAPIWrapper를 통해 실제 WebSocket API에 위임한다.
    """

    def __init__(
        self,
        broker_api_wrapper: "BrokerAPIWrapper",
        logger,
        market_clock,
        market_data_service: Optional["MarketDataService"] = None,
        streaming_logger: Optional["StreamingEventLogger"] = None,
        price_stream_service: Optional["PriceStreamService"] = None,
    ):
        self.broker = broker_api_wrapper
        self.logger = logger
        self.market_clock = market_clock
        self.market_data_service = market_data_service
        self._streaming_logger = streaming_logger
        self._price_stream_service = price_stream_service
        self._latest_prices: Dict[str, dict | str] = {}
        self._last_console_print_time: float = 0.0
        self._PRINT_THROTTLE_SEC: float = 0.5
        self._callback = None  # 재연결 시 콜백 유실 방지용 저장

    # ── 의존성 주입 ───────────────────────────────────────────────

    def set_price_stream_service(self, svc: "PriceStreamService") -> None:
        """PriceStreamService를 사후 주입한다 (선택적 — 없으면 기존 동작 유지)."""
        self._price_stream_service = svc

    # ── 연결 수명주기 ──────────────────────────────────────────────

    async def connect_websocket(self, callback=None):
        """WebSocket 연결 (BrokerAPIWrapper 위임).

        callback이 전달되면 내부에 저장하여 이후 재연결 시에도 동일한 콜백을 사용한다.
        callback이 None이면 기존에 저장된 콜백을 사용한다.
        """
        if callback is not None:
            self._callback = callback
        result = await self.broker.connect_websocket(self._callback)
        if result and self._streaming_logger:
            self._streaming_logger.log_connect()
        return result

    async def disconnect_websocket(self):
        """WebSocket 연결 해제 (BrokerAPIWrapper 위임)."""
        result = await self.broker.disconnect_websocket()
        if self._streaming_logger:
            self._streaming_logger.log_disconnect()
        return result

    # ── 구독 / 해지 ───────────────────────────────────────────────

    async def subscribe_program_trading(self, code: str):
        """프로그램매매 실시간 구독 (BrokerAPIWrapper 위임)."""
        return await self.broker.subscribe_program_trading(code)

    async def unsubscribe_program_trading(self, code: str):
        """프로그램매매 구독 해지 (BrokerAPIWrapper 위임)."""
        return await self.broker.unsubscribe_program_trading(code)

    async def subscribe_realtime_price(self, code: str):
        """실시간 체결가 구독 (BrokerAPIWrapper 위임)."""
        return await self.broker.subscribe_realtime_price(code)

    async def unsubscribe_realtime_price(self, code: str):
        """실시간 체결가 구독 해지 (BrokerAPIWrapper 위임)."""
        return await self.broker.unsubscribe_realtime_price(code)

    async def subscribe_unified_price(self, code: str) -> bool:
        """실시간 통합 체결가(H0UNCNT0) 구독 — PriceSubscriptionService 전용."""
        return await self.broker.subscribe_unified_price(code)

    async def unsubscribe_unified_price(self, code: str) -> bool:
        """실시간 통합 체결가(H0UNCNT0) 구독 해지 — PriceSubscriptionService 전용."""
        return await self.broker.unsubscribe_unified_price(code)

    # ── 고수준 스트림 핸들러 ──────────────────────────────────────

    async def handle_program_trading_stream(self, stock_code: str, duration: int = 60) -> None:
        """
        프로그램매매(H0STPGM0) 구독 → duration초 수신 → 해지.
        CLI 등 단발성 스트리밍 용도.
        """
        await self.connect_websocket()
        await self.subscribe_program_trading(stock_code)
        try:
            await self.market_clock.async_sleep(duration)
        finally:
            await self.unsubscribe_program_trading(stock_code)
            await self.disconnect_websocket()

    async def handle_realtime_stream(
        self,
        stock_codes: list[str],
        fields: list[str],
        duration: int = 30,
    ) -> None:
        """실시간 스트림 구독 및 처리 (price / quote 필드 지원)."""
        self.logger.info(
            f"StreamingService - 실시간 스트림 요청: 종목={stock_codes}, 필드={fields}, 시간={duration}s"
        )
        try:
            await self.connect_websocket()
            for code in stock_codes:
                if "price" in fields:
                    await self.subscribe_realtime_price(code)
                if "quote" in fields:
                    await self.broker.subscribe_realtime_quote(code)

            from datetime import datetime, timedelta
            start_time = datetime.now()
            while (datetime.now() - start_time) < timedelta(seconds=duration):
                await self.market_clock.async_sleep(1)
        except Exception as e:
            self.logger.exception(f"실시간 스트림 처리 중 오류 발생: {str(e)}")
        finally:
            for code in stock_codes:
                if "price" in fields:
                    await self.unsubscribe_realtime_price(code)
                if "quote" in fields:
                    await self.broker.unsubscribe_realtime_quote(code)
            await self.disconnect_websocket()
            self.logger.info("실시간 스트림 종료")

    # ── 메시지 dispatch 및 캐시 ───────────────────────────────────

    def dispatch_realtime_message(self, data: dict) -> None:
        """실시간 WebSocket 메시지를 파싱하여 내부 최신가 캐시를 갱신한다."""
        self.logger.debug(
            f"실시간 데이터 수신: Type={data.get('type')}, TR_ID={data.get('tr_id')}, Data={data.get('data')}"
        )

        if data.get('type') == 'realtime_price':
            realtime_data = data.get('data', {})
            stock_code = realtime_data.get('유가증권단축종목코드')
            current_price = realtime_data.get('주식현재가')

            if self._price_stream_service:
                # PriceStreamService에 위임: 캐시 갱신 + StockRepository 업데이트
                self._price_stream_service.on_price_tick(realtime_data)
            elif stock_code and current_price:
                # PriceStreamService 미설정 시 기존 동작 유지 (하위 호환)
                self._latest_prices[stock_code] = {
                    "price": current_price,
                    "change": realtime_data.get('전일대비', '0'),
                    "rate": realtime_data.get('전일대비율', '0.00'),
                    "sign": realtime_data.get('전일대비부호', '3'),
                    "received_at": time.time(),
                }

                # StockRepository 실시간 틱 캐시 즉시 반영
                if (
                    self.market_data_service is not None
                    and hasattr(self.market_data_service, '_stock_repo')
                    and self.market_data_service._stock_repo
                ):
                    try:
                        cum_vol = realtime_data.get('누적거래량', '0')
                        vol_int = int(cum_vol) if cum_vol and cum_vol != 'N/A' else 0
                        self.market_data_service._stock_repo.update_realtime_data(
                            stock_code, float(current_price), vol_int
                        )
                    except Exception as e:
                        self.logger.warning(f"StockRepository 실시간 틱 캐시 갱신 실패: {e}")

            # 콘솔 출력 (0.5초 스로틀링 — 이벤트 루프 blocking 최소화)
            now = time.monotonic()
            if now - self._last_console_print_time >= self._PRINT_THROTTLE_SEC:
                self._last_console_print_time = now
                change = realtime_data.get('전일대비', 'N/A')
                change_sign = realtime_data.get('전일대비부호', 'N/A')
                change_rate = realtime_data.get('전일대비율', 'N/A')
                cumulative_volume = realtime_data.get('누적거래량', 'N/A')
                trade_time = realtime_data.get('주식체결시간', 'N/A')
                display_message = (
                    f"\r[실시간 체결 - {trade_time}] 종목: {stock_code}: 현재가 {current_price}원, "
                    f"전일대비: {change_sign}{change} ({change_rate}%), 누적량: {cumulative_volume}"
                )
                self.logger.debug(f"\r{display_message}{' ' * (80 - len(display_message))}", end="")

        elif data.get('type') == 'realtime_quote':
            quote_data = data.get('data', {})
            stock_code = quote_data.get('유가증권단축종목코드', 'N/A')
            askp1 = quote_data.get('매도호가1', 'N/A')
            bidp1 = quote_data.get('매수호가1', 'N/A')
            trade_time = quote_data.get('영업시간', 'N/A')
            now = time.monotonic()
            if now - self._last_console_print_time >= self._PRINT_THROTTLE_SEC:
                self._last_console_print_time = now
                display_message = (
                    f"[실시간 호가 - {trade_time}] 종목: {stock_code}: 매도1호가: {askp1}, 매수1호가: {bidp1}"
                )
                self.logger.debug(f"\r{display_message}{' ' * (80 - len(display_message))}", end="")

        elif data.get('type') == 'signing_notice':
            notice_data = data.get('data', {})
            order_num = notice_data.get('주문번호', 'N/A')
            trade_qty = notice_data.get('체결수량', 'N/A')
            trade_price = notice_data.get('체결단가', 'N/A')
            trade_time = notice_data.get('주식체결시간', 'N/A')
            self.logger.debug(
                f"\n[체결통보] 주문: {order_num}, 수량: {trade_qty}, "
                f"단가: {trade_price}, 시간: {trade_time}"
            )

        elif data.get('type') == 'realtime_program_trading':
            d = data.get('data', {})
            t = d.get('주식체결시간', 'N/A')
            ntby = d.get('순매수거래대금', '0')
            now = time.monotonic()
            if now - self._last_console_print_time >= self._PRINT_THROTTLE_SEC:
                self._last_console_print_time = now
                msg = f"[프로그램매매 - {t}] 순매수거래대금: {ntby}"
                self.logger.debug(f"\r{msg}{' ' * max(0, 80 - len(msg))}", end="")

        else:
            self.logger.debug(
                f"처리되지 않은 실시간 메시지: {data.get('tr_id')} - {data}"
            )

    def get_cached_realtime_price(self, code: str) -> Optional[Dict | str]:
        """메모리 캐시에서 실시간 최신가 정보를 반환한다."""
        if self._price_stream_service:
            return self._price_stream_service.get_cached_price(code)
        return self._latest_prices.get(code)

    # ── REST 조회 ─────────────────────────────────────────────────

    async def handle_get_program_trading_history(self, code: str) -> ResCommonResponse:
        """종목별 프로그램매매 추이 히스토리 조회 (REST, 실전 전용)."""
        self.logger.info(f"StreamingService - 프로그램매매 히스토리 조회: {code}")
        try:
            return await self.broker.get_program_trade_by_stock_daily(code)
        except Exception as e:
            self.logger.error(f"프로그램매매 히스토리 조회 실패 ({code}): {e}")
            return ResCommonResponse(
                rt_cd=ErrorCode.API_ERROR.value,
                msg1=str(e),
                data=None,
            )
