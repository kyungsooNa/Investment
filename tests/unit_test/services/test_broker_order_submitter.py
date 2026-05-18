from unittest.mock import AsyncMock, MagicMock

import pytest

from common.types import ErrorCode, Exchange, OrderContext, OrderSide, OrderState, ResCommonResponse
from services.broker_order_submitter import BrokerOrderSubmitter


class _Logger:
    def __init__(self):
        self.info = MagicMock()
        self.warning = MagicMock()
        self.error = MagicMock()
        self.exception = MagicMock()
        self.debug = MagicMock()


@pytest.fixture
def mock_broker():
    mock = AsyncMock()
    mock.place_stock_order.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="주문 성공", data={"ordno": "B0001"}
    )
    return mock


@pytest.fixture
def mock_market_clock():
    mock = MagicMock()
    mock.async_sleep = AsyncMock()
    return mock


@pytest.fixture
def mock_kill_switch():
    mock = AsyncMock()
    mock.record_api_success = AsyncMock()
    mock.record_api_failure = AsyncMock()
    return mock


def _make_submitter(
    *,
    broker,
    market_clock=None,
    kill_switch=None,
    states: dict | None = None,
    transition: MagicMock | None = None,
    extract_no_fn=None,
) -> BrokerOrderSubmitter:
    states = states if states is not None else {}
    return BrokerOrderSubmitter(
        broker_api_wrapper=broker,
        logger=_Logger(),
        kill_switch=kill_switch,
        market_clock=market_clock,
        state_provider=(lambda: states),
        transition_fn=transition,
        extract_broker_order_no_fn=extract_no_fn or (lambda r: (r.data or {}).get("ordno") if r else None),
        max_retries=3,
        retry_delay_sec=3,
    )


# ── submit_with_retry: 성공 케이스 ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_submit_success_on_first_attempt_no_retry(mock_broker, mock_market_clock):
    submitter = _make_submitter(broker=mock_broker, market_clock=mock_market_clock)

    result = await submitter.submit_with_retry(
        "005930", 70000, 10, is_buy=True, exchange=Exchange.KRX
    )

    assert result.rt_cd == ErrorCode.SUCCESS.value
    assert mock_broker.place_stock_order.await_count == 1
    mock_market_clock.async_sleep.assert_not_awaited()


@pytest.mark.asyncio
async def test_submit_transitions_to_submitted_on_success_with_order_key(mock_broker, mock_market_clock):
    transition = MagicMock()
    submitter = _make_submitter(
        broker=mock_broker,
        market_clock=mock_market_clock,
        states={"K1": object()},
        transition=transition,
    )

    await submitter.submit_with_retry(
        "005930", 70000, 10, is_buy=True, exchange=Exchange.KRX, order_key="K1",
    )

    transition.assert_called_once()
    args, kwargs = transition.call_args
    assert args[0] == "K1"
    assert args[1] == OrderState.SUBMITTED
    assert kwargs["attempt_count"] == 1
    assert kwargs["broker_order_no"] == "B0001"


# ── submit_with_retry: 재시도 케이스 ───────────────────────────────────────

@pytest.mark.asyncio
async def test_submit_retries_on_transient_error_then_succeeds(mock_broker, mock_market_clock):
    mock_broker.place_stock_order.side_effect = [
        ResCommonResponse(rt_cd=ErrorCode.NETWORK_ERROR.value, msg1="네트워크 오류", data=None),
        ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="주문 성공", data={"ordno": "B0002"}),
    ]
    submitter = _make_submitter(broker=mock_broker, market_clock=mock_market_clock)

    result = await submitter.submit_with_retry(
        "005930", 70000, 10, is_buy=True, exchange=Exchange.KRX
    )

    assert result.rt_cd == ErrorCode.SUCCESS.value
    assert mock_broker.place_stock_order.await_count == 2
    mock_market_clock.async_sleep.assert_awaited_once()


@pytest.mark.asyncio
async def test_submit_exhausts_retries_returns_last_failure(mock_broker, mock_market_clock):
    mock_broker.place_stock_order.return_value = ResCommonResponse(
        rt_cd=ErrorCode.RETRY_LIMIT.value, msg1="재시도 한도 초과", data=None
    )
    submitter = _make_submitter(broker=mock_broker, market_clock=mock_market_clock)

    result = await submitter.submit_with_retry(
        "005930", 70000, 10, is_buy=True, exchange=Exchange.KRX
    )

    assert result.rt_cd == ErrorCode.RETRY_LIMIT.value
    assert mock_broker.place_stock_order.await_count == 3
    # 마지막 시도 직후엔 sleep 하지 않음
    assert mock_market_clock.async_sleep.await_count == 2


@pytest.mark.asyncio
async def test_submit_uses_exponential_backoff_for_retry_sleep(mock_broker, mock_market_clock):
    mock_broker.place_stock_order.return_value = ResCommonResponse(
        rt_cd=ErrorCode.NETWORK_ERROR.value, msg1="네트워크", data=None
    )
    submitter = _make_submitter(broker=mock_broker, market_clock=mock_market_clock)

    await submitter.submit_with_retry(
        "005930", 70000, 10, is_buy=True, exchange=Exchange.KRX
    )

    # attempt 1 → sleep(3*1=3), attempt 2 → sleep(3*2=6)
    assert mock_market_clock.async_sleep.await_args_list[0].args == (3,)
    assert mock_market_clock.async_sleep.await_args_list[1].args == (6,)


# ── submit_with_retry: 비즈니스 거부 (FAIL) ─────────────────────────────────

@pytest.mark.asyncio
async def test_submit_business_reject_immediate_no_retry(mock_broker, mock_market_clock):
    mock_broker.place_stock_order.return_value = ResCommonResponse(
        rt_cd=ErrorCode.API_ERROR.value, msg1="잔고 부족", data=None
    )
    submitter = _make_submitter(broker=mock_broker, market_clock=mock_market_clock)

    result = await submitter.submit_with_retry(
        "005930", 70000, 10, is_buy=True, exchange=Exchange.KRX
    )

    assert result.rt_cd == ErrorCode.API_ERROR.value
    assert mock_broker.place_stock_order.await_count == 1
    mock_market_clock.async_sleep.assert_not_awaited()


@pytest.mark.asyncio
async def test_submit_business_reject_transitions_to_rejected_with_order_key(mock_broker, mock_market_clock):
    mock_broker.place_stock_order.return_value = ResCommonResponse(
        rt_cd=ErrorCode.API_ERROR.value, msg1="잔고 부족", data=None
    )
    transition = MagicMock()
    submitter = _make_submitter(
        broker=mock_broker,
        market_clock=mock_market_clock,
        states={"K1": object()},
        transition=transition,
    )

    await submitter.submit_with_retry(
        "005930", 70000, 10, is_buy=True, exchange=Exchange.KRX, order_key="K1"
    )

    transition.assert_called_once()
    args, kwargs = transition.call_args
    assert args[1] == OrderState.REJECTED
    assert kwargs["error_code"] == ErrorCode.API_ERROR.value
    assert kwargs["error_message"] == "잔고 부족"


# ── submit_with_retry: RETRY 중 FSM 전이 ─────────────────────────────────

@pytest.mark.asyncio
async def test_submit_retry_attempts_transition_to_pending_then_rejected_on_exhaustion(
    mock_broker, mock_market_clock
):
    mock_broker.place_stock_order.return_value = ResCommonResponse(
        rt_cd=ErrorCode.NETWORK_ERROR.value, msg1="네트워크", data=None
    )
    transition = MagicMock()
    submitter = _make_submitter(
        broker=mock_broker,
        market_clock=mock_market_clock,
        states={"K1": object()},
        transition=transition,
    )

    await submitter.submit_with_retry(
        "005930", 70000, 10, is_buy=True, exchange=Exchange.KRX, order_key="K1"
    )

    # 3회 시도 → 3개의 전이 호출
    assert transition.call_count == 3
    states = [call.args[1] for call in transition.call_args_list]
    assert states == [OrderState.PENDING_SUBMIT, OrderState.PENDING_SUBMIT, OrderState.REJECTED]


# ── _execute_via_broker: KillSwitch 통합 ─────────────────────────────────

@pytest.mark.asyncio
async def test_execute_via_broker_records_kill_switch_success(mock_broker, mock_kill_switch):
    submitter = _make_submitter(broker=mock_broker, kill_switch=mock_kill_switch)
    await submitter._execute_via_broker("005930", 70000, 10, is_buy=True)
    mock_kill_switch.record_api_success.assert_awaited_once()
    mock_kill_switch.record_api_failure.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_via_broker_records_kill_switch_failure_on_non_business_error(
    mock_broker, mock_kill_switch
):
    mock_broker.place_stock_order.return_value = ResCommonResponse(
        rt_cd=ErrorCode.NETWORK_ERROR.value, msg1="네트워크", data=None
    )
    submitter = _make_submitter(broker=mock_broker, kill_switch=mock_kill_switch)

    await submitter._execute_via_broker("005930", 70000, 10, is_buy=True)

    mock_kill_switch.record_api_failure.assert_awaited_once_with(ErrorCode.NETWORK_ERROR.value)
    mock_kill_switch.record_api_success.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_via_broker_skips_kill_switch_count_on_business_reject(
    mock_broker, mock_kill_switch
):
    """비즈니스 거부(잔고부족 등 NON_RETRIABLE 패턴)는 KillSwitch API 오류로 카운트하지 않는다."""
    mock_broker.place_stock_order.return_value = ResCommonResponse(
        rt_cd=ErrorCode.API_ERROR.value, msg1="잔고부족", data=None
    )
    submitter = _make_submitter(broker=mock_broker, kill_switch=mock_kill_switch)

    await submitter._execute_via_broker("005930", 70000, 10, is_buy=True)

    mock_kill_switch.record_api_failure.assert_not_awaited()
    mock_kill_switch.record_api_success.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_via_broker_handles_broker_exception_records_kill_switch(
    mock_broker, mock_kill_switch
):
    mock_broker.place_stock_order.side_effect = RuntimeError("network down")
    submitter = _make_submitter(broker=mock_broker, kill_switch=mock_kill_switch)

    result = await submitter._execute_via_broker("005930", 70000, 10, is_buy=True)

    assert result.rt_cd == ErrorCode.UNKNOWN_ERROR.value
    assert "network down" in result.msg1
    mock_kill_switch.record_api_failure.assert_awaited_once_with("network down")


# ── state_provider / transition_fn 결합 동작 ────────────────────────────

@pytest.mark.asyncio
async def test_submit_skips_transition_when_order_key_not_in_states(mock_broker, mock_market_clock):
    """order_key가 state_provider에 없으면 전이 콜백을 호출하지 않는다."""
    transition = MagicMock()
    submitter = _make_submitter(
        broker=mock_broker,
        market_clock=mock_market_clock,
        states={},  # 비어있음
        transition=transition,
    )

    result = await submitter.submit_with_retry(
        "005930", 70000, 10, is_buy=True, exchange=Exchange.KRX, order_key="K_NOT_REGISTERED",
    )

    assert result.rt_cd == ErrorCode.SUCCESS.value
    transition.assert_not_called()


@pytest.mark.asyncio
async def test_submit_skips_transition_when_no_state_provider_configured(mock_broker, mock_market_clock):
    """state_provider/transition_fn이 None이면 FSM 갱신 시도 자체를 건너뛴다."""
    submitter = BrokerOrderSubmitter(
        broker_api_wrapper=mock_broker,
        logger=_Logger(),
        kill_switch=None,
        market_clock=mock_market_clock,
        state_provider=None,
        transition_fn=None,
        extract_broker_order_no_fn=None,
    )

    result = await submitter.submit_with_retry(
        "005930", 70000, 10, is_buy=True, exchange=Exchange.KRX, order_key="K1",
    )

    assert result.rt_cd == ErrorCode.SUCCESS.value


# ── properties ─────────────────────────────────────────────────────────────

def test_properties_expose_retry_policy():
    submitter = BrokerOrderSubmitter(
        broker_api_wrapper=MagicMock(),
        logger=_Logger(),
        max_retries=5,
        retry_delay_sec=7,
    )
    assert submitter.max_retries == 5
    assert submitter.retry_delay_sec == 7
