import asyncio
from typing import Callable, Optional

from common.types import ErrorCode, Exchange, OrderState, ResCommonResponse
from core.retry_queue.retry_classifier import (
    RequestOutcome,
    classify,
    is_non_retriable_business_error,
)


class BrokerOrderSubmitter:
    """주문 브로커 호출 + 재시도 정책 책임자.

    OrderExecutionService에서 Phase 2(plan: 3-3-playful-walrus.md)로 분리.
    Phase 3에서 OrderStateMachine이 분리되기 전까지, FSM 상태 전이는 호출자가
    주입한 콜백(state_provider, transition_fn, extract_broker_order_no_fn)으로 위임받는다.
    """

    def __init__(
        self,
        broker_api_wrapper,
        logger,
        *,
        kill_switch=None,
        market_clock=None,
        state_provider: Optional[Callable[[], dict]] = None,
        transition_fn: Optional[Callable[..., object]] = None,
        extract_broker_order_no_fn: Optional[Callable[[ResCommonResponse], Optional[str]]] = None,
        max_retries: int = 3,
        retry_delay_sec: int = 3,
    ) -> None:
        self._broker_api_wrapper = broker_api_wrapper
        self.logger = logger
        self._kill_switch = kill_switch
        self._market_clock = market_clock
        self._state_provider = state_provider
        self._transition_fn = transition_fn
        self._extract_broker_order_no_fn = extract_broker_order_no_fn
        self._max_retries = max_retries
        self._retry_delay_sec = retry_delay_sec

    @property
    def max_retries(self) -> int:
        return self._max_retries

    @property
    def retry_delay_sec(self) -> int:
        return self._retry_delay_sec

    async def submit_with_retry(
        self,
        stock_code,
        price,
        qty,
        *,
        is_buy: bool,
        exchange: Exchange = Exchange.KRX,
        order_key: Optional[str] = None,
    ) -> ResCommonResponse:
        """재시도 가능한 오류에 대해 주문 API를 재시도.
        - FAIL (비즈니스 거부): 즉시 REJECTED, 재시도 없음.
        - RETRY (일시적 오류): 지수 백오프, 최대 self._max_retries 회.
        """
        last_result: Optional[ResCommonResponse] = None
        for attempt in range(1, self._max_retries + 1):
            result: ResCommonResponse = await self._execute_via_broker(
                stock_code, price, qty, is_buy=is_buy, exchange=exchange
            )
            if result and result.rt_cd == ErrorCode.SUCCESS.value:
                if order_key and self._is_order_key_active(order_key):
                    broker_no = (
                        self._extract_broker_order_no_fn(result)
                        if self._extract_broker_order_no_fn
                        else None
                    )
                    self._transition_fn(
                        order_key,
                        OrderState.SUBMITTED,
                        attempt_count=attempt,
                        broker_order_no=broker_no,
                    )
                return result
            last_result = result

            outcome = classify(result)

            if outcome == RequestOutcome.FAIL:
                self.logger.warning(
                    f"주문 비즈니스 거부 (재시도 없음) — {stock_code}, "
                    f"사유: {result.msg1 if result else '응답 없음'}"
                )
                if order_key and self._is_order_key_active(order_key):
                    self._transition_fn(
                        order_key,
                        OrderState.REJECTED,
                        attempt_count=attempt,
                        error_code=result.rt_cd if result else None,
                        error_message=result.msg1 if result else "응답 없음",
                    )
                break

            if order_key and self._is_order_key_active(order_key):
                current_state = (
                    OrderState.PENDING_SUBMIT
                    if attempt < self._max_retries
                    else OrderState.REJECTED
                )
                self._transition_fn(
                    order_key,
                    current_state,
                    attempt_count=attempt,
                    error_code=result.rt_cd if result else None,
                    error_message=result.msg1 if result else "응답 없음",
                )

            if attempt < self._max_retries:
                self.logger.warning(
                    f"주문 재시도 {attempt}/{self._max_retries}: "
                    f"{stock_code}, 사유: {result.msg1 if result else '응답 없음'}"
                )
                delay = self._retry_delay_sec * attempt
                if self._market_clock is not None:
                    await self._market_clock.async_sleep(delay)
                else:
                    await asyncio.sleep(delay)
                continue
            break
        return last_result

    async def _execute_via_broker(
        self,
        stock_code,
        price,
        qty,
        *,
        is_buy: bool,
        exchange: Exchange = Exchange.KRX,
    ) -> ResCommonResponse:
        action_str = "매수" if is_buy else "매도"
        self.logger.info(
            f"OrderExecutionService - 주식 {action_str} 주문 요청 - "
            f"종목: {stock_code}, 수량: {qty}, 가격: {price}"
        )
        try:
            result = await self._broker_api_wrapper.place_stock_order(
                stock_code, price, qty, is_buy=is_buy, exchange=exchange
            )
            if self._kill_switch:
                if result and result.rt_cd == ErrorCode.SUCCESS.value:
                    await self._kill_switch.record_api_success()
                elif result and is_non_retriable_business_error(result):
                    self.logger.warning(
                        f"KillSwitch API 오류 카운트 제외: {action_str} 비즈니스 거부 - {result.msg1}"
                    )
                else:
                    rt = result.rt_cd if result else "no_response"
                    await self._kill_switch.record_api_failure(rt)
            return result
        except Exception as e:
            self.logger.exception(f"{action_str} 주문 중 오류 발생: {str(e)}")
            if self._kill_switch:
                await self._kill_switch.record_api_failure(str(e))
            return ResCommonResponse(
                rt_cd=ErrorCode.UNKNOWN_ERROR.value,
                msg1=f"{action_str} 주문 처리 중 예외 발생: {str(e)}",
                data=None,
            )

    def _is_order_key_active(self, order_key: str) -> bool:
        if not self._state_provider or not self._transition_fn:
            return False
        states = self._state_provider()
        return order_key in states
