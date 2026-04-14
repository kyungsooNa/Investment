"""
WebSocket 스트리밍 관련 기능을 담당하는 서비스.

역할:
  - WebSocket 연결/해제 수명주기 관리 (connect, disconnect)
  - 실시간 데이터 구독/해지 (subscribe/unsubscribe)
  - 수신 메시지 dispatch 및 등록된 핸들러 호출
  - 프로그램매매 히스토리 조회 (REST)

ProgramTradingStreamService와의 역할 구분:
  - StreamingService           : WebSocket 연결·구독·메시지 처리 (프로토콜 레이어)
  - ProgramTradingStreamService: 프로그램매매 데이터의 저장·버퍼링·SSE 배포 (데이터 레이어)

Observer 패턴:
  - register_handler(data_type, callback) 으로 데이터 타입별 핸들러 등록
  - dispatch_realtime_message() 가 각 핸들러를 독립 try-except 으로 호출하여
    한 컨슈머의 장애가 다른 컨슈머에 전파되지 않도록 격리
"""
from __future__ import annotations

import time
from typing import Callable, Dict, List, Optional, TYPE_CHECKING
import asyncio

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
        self._price_stream_service: Optional["PriceStreamService"] = None
        self._last_console_print_time: float = 0.0
        self._PRINT_THROTTLE_SEC: float = 0.5
        self._callback = None  # 재연결 시 콜백 유실 방지용 저장

        # Observer 레지스트리: data_type → [handler, ...]
        self._handlers: Dict[str, List[Callable[[dict], None]]] = {}

        if price_stream_service is not None:
            self.set_price_stream_service(price_stream_service)

    # ── 의존성 주입 ───────────────────────────────────────────────

    def register_handler(self, data_type: str, handler: Callable[[dict], None]) -> None:
        """
        데이터 타입별 핸들러를 등록한다.

        동일 타입에 여러 핸들러를 등록할 수 있으며,
        dispatch_realtime_message() 호출 시 각 핸들러는 독립 try-except 블록으로
        실행되어 한 핸들러의 예외가 나머지에 전파되지 않는다.

        Args:
            data_type: 'realtime_price', 'realtime_quote', 'signing_notice',
                       'realtime_program_trading' 등 data['type'] 값
            handler:   inner data dict(data['data'])를 인자로 받는 callable
        """
        self._handlers.setdefault(data_type, []).append(handler)

    def set_price_stream_service(self, svc: "PriceStreamService") -> None:
        """PriceStreamService를 사후 주입한다 (선택적 — 없으면 핸들러 미등록).

        이전에 등록된 PriceStreamService 핸들러가 있으면 먼저 제거한 후 새로 등록한다.
        """
        if self._price_stream_service is not None:
            handlers = self._handlers.get('realtime_price', [])
            if self._price_stream_service.on_price_tick in handlers:
                handlers.remove(self._price_stream_service.on_price_tick)

        self._price_stream_service = svc
        self.register_handler('realtime_price', svc.on_price_tick)

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

    # ── 메시지 dispatch ───────────────────────────────────────────

    def dispatch_realtime_message(self, data: dict) -> None:
        """실시간 WebSocket 메시지를 파싱하여 등록된 핸들러를 호출한다.

        각 핸들러는 독립 try-except 블록으로 실행되어 한 핸들러의 예외가
        다른 핸들러에 전파되지 않는다 (Observer 격리 원칙).

        핸들러는 inner data dict (data['data']) 를 인자로 받는다.
        """
        data_type = data.get('type', '')
        inner = data.get('data', {})

        self.logger.debug(
            f"실시간 데이터 수신: Type={data_type}, TR_ID={data.get('tr_id')}, Data={inner}"
        )

        # ── 등록된 핸들러 호출 (Observer 격리) ──────────────────────
        for handler in self._handlers.get(data_type, []):
            try:
                if asyncio.iscoroutinefunction(handler):
                    # 코루틴 핸들러는 백그라운드 태스크로 분리
                    loop = asyncio.get_running_loop()
                    task = loop.create_task(handler(inner))
                    # 예외 로깅을 위해 콜백 추가
                    def _log_done(t: asyncio.Task, h=handler):
                        try:
                            exc = t.exception()
                            if exc:
                                self.logger.error(f"[{data_type}] 핸들러 예외 ({h!r}): {exc}", exc_info=True)
                        except asyncio.CancelledError:
                            pass
                    task.add_done_callback(_log_done)
                else:
                    # 동기(또는 블로킹) 핸들러는 스레드 풀로 오프로드
                    try:
                        loop = asyncio.get_running_loop()
                        loop.run_in_executor(None, handler, inner)
                    except RuntimeError:
                        # 이벤트 루프가 없으면 동기 호출
                        handler(inner)
            except Exception as e:
                self.logger.error(
                    f"[{data_type}] 핸들러 오류 ({handler!r}): {e}", exc_info=True
                )

        # ── 내장 콘솔/디버그 출력 (스로틀링) ──────────────────────
        now = time.monotonic()

        def _throttle_allowed(dtype: str) -> bool:
            """Support both old float and new per-type dict for last print times."""
            last = self._last_console_print_time
            try:
                # dict mode
                if isinstance(last, dict):
                    last_time = last.get(dtype, 0.0)
                    if now - last_time >= self._PRINT_THROTTLE_SEC:
                        last[dtype] = now
                        return True
                    return False
                # float mode (backward compatibility)
                if now - float(last) >= self._PRINT_THROTTLE_SEC:
                    # convert to dict for future per-type use
                    self._last_console_print_time = {dtype: now}
                    return True
                return False
            except Exception:
                # fallback: allow
                try:
                    self._last_console_print_time = {dtype: now}
                except Exception:
                    self._last_console_print_time = now
                return True

        if data_type == 'realtime_price':
            if _throttle_allowed('realtime_price'):
                stock_code = inner.get('유가증권단축종목코드')
                current_price = inner.get('주식현재가')
                change = inner.get('전일대비', 'N/A')
                change_sign = inner.get('전일대비부호', 'N/A')
                change_rate = inner.get('전일대비율', 'N/A')
                cumulative_volume = inner.get('누적거래량', 'N/A')
                trade_time = inner.get('주식체결시간', 'N/A')
                display_message = (
                    f"\r[실시간 체결 - {trade_time}] 종목: {stock_code}: 현재가 {current_price}원, "
                    f"전일대비: {change_sign}{change} ({change_rate}%), 누적량: {cumulative_volume}"
                )
                self.logger.debug(f"\r{display_message}{' ' * (80 - len(display_message))}")

        elif data_type == 'realtime_quote':
            if _throttle_allowed('realtime_quote'):
                stock_code = inner.get('유가증권단축종목코드', 'N/A')
                askp1 = inner.get('매도호가1', 'N/A')
                bidp1 = inner.get('매수호가1', 'N/A')
                trade_time = inner.get('영업시간', 'N/A')
                display_message = (
                    f"[실시간 호가 - {trade_time}] 종목: {stock_code}: 매도1호가: {askp1}, 매수1호가: {bidp1}"
                )
                self.logger.debug(f"\r{display_message}{' ' * (80 - len(display_message))}")

        elif data_type == 'signing_notice':
            order_num = inner.get('주문번호', 'N/A')
            trade_qty = inner.get('체결수량', 'N/A')
            trade_price = inner.get('체결단가', 'N/A')
            trade_time = inner.get('주식체결시간', 'N/A')
            self.logger.debug(
                f"\n[체결통보] 주문: {order_num}, 수량: {trade_qty}, "
                f"단가: {trade_price}, 시간: {trade_time}"
            )

        elif data_type == 'realtime_program_trading':
            if _throttle_allowed('realtime_program_trading'):
                t = inner.get('주식체결시간', 'N/A')
                ntby = inner.get('순매수거래대금', '0')
                msg = f"[프로그램매매 - {t}] 순매수거래대금: {ntby}"
                self.logger.debug(f"\r{msg}{' ' * max(0, 80 - len(msg))}", end="")

        else:
            self.logger.debug(
                f"처리되지 않은 실시간 메시지: {data.get('tr_id')} - {data}"
            )

    def get_cached_realtime_price(self, code: str) -> Optional[Dict]:
        """메모리 캐시에서 실시간 최신가 정보를 반환한다.

        PriceStreamService가 주입되지 않은 경우 None을 반환한다.
        """
        if self._price_stream_service:
            return self._price_stream_service.get_cached_price(code)
        return None

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
