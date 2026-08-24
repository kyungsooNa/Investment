"""StreamingService 의 브로커 위임·구독 복구 경로 테스트.

대부분의 구독 메서드는 `BrokerAPIWrapper` 로 그대로 넘기는 얇은 층이라 표로
고정하고, 체결통보 구독 후 수신 태스크가 죽는 복구 시나리오와 장운영정보/
지수선물 모니터 일괄 구독처럼 판단이 들어간 경로는 개별 테스트로 다룬다.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.streaming_service import StreamingService


def _build(**kwargs):
    broker = AsyncMock()
    broker.is_websocket_receive_alive = MagicMock(return_value=True)
    broker.get_subscription_ledger = MagicMock(return_value={"005930": "active"})
    svc = StreamingService(broker, MagicMock(), MagicMock(), **kwargs)
    return svc, broker


# (서비스 메서드, 브로커 메서드, 인자)
ASYNC_DELEGATIONS = [
    ("unsubscribe_program_trading", "unsubscribe_program_trading", ("005930",)),
    ("subscribe_market_status", "subscribe_market_status", ("005930",)),
    ("unsubscribe_market_status", "unsubscribe_market_status", ("005930",)),
    ("subscribe_index_futures_contract", "subscribe_index_futures_contract", ("101W09",)),
    ("unsubscribe_index_futures_contract", "unsubscribe_index_futures_contract", ("101W09",)),
    ("subscribe_order_notice", "subscribe_order_notice", ()),
    ("unsubscribe_order_notice", "unsubscribe_order_notice", ()),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("method, target, args", ASYNC_DELEGATIONS)
async def test_async_methods_delegate_to_the_broker(method, target, args):
    svc, broker = _build()
    sentinel = object()
    setattr(broker, target, AsyncMock(return_value=sentinel))

    assert await getattr(svc, method)(*args) is sentinel
    getattr(broker, target).assert_awaited_once_with(*args)


def test_subscription_ledger_is_delegated():
    svc, broker = _build()

    assert svc.get_subscription_ledger() == {"005930": "active"}
    broker.get_subscription_ledger.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method, target, code",
    [
        ("wait_program_trading_ack", "wait_program_trading_ack", "005930"),
        ("wait_market_status_ack", "wait_market_status_ack", "005930"),
        ("wait_index_futures_contract_ack", "wait_index_futures_contract_ack", "101W09"),
        ("wait_unified_price_ack", "wait_unified_price_ack", "005930"),
    ],
)
async def test_ack_waiters_forward_code_and_timeout(method, target, code):
    svc, broker = _build()
    setattr(broker, target, AsyncMock(return_value=False))

    assert await getattr(svc, method)(code, 2.0) is False
    getattr(broker, target).assert_awaited_once_with(code, 2.0)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method, target",
    [
        ("wait_program_trading_ack", "wait_program_trading_ack"),
        ("wait_market_status_ack", "wait_market_status_ack"),
        ("wait_index_futures_contract_ack", "wait_index_futures_contract_ack"),
        ("wait_unified_price_ack", "wait_unified_price_ack"),
    ],
)
async def test_ack_waiters_assume_success_when_the_broker_lacks_the_method(method, target):
    """구버전/모킹 브로커에서는 전송 성공을 ACK 로 간주해 기존 동작을 유지한다."""
    svc, broker = _build()
    setattr(broker, target, None)

    assert await getattr(svc, method)("005930") is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exchange, expected_target",
    [("NXT", "subscribe_nxt_price"), ("KRX", "subscribe_realtime_price")],
)
async def test_exchange_price_subscription_picks_the_matching_stream(exchange, expected_target):
    svc, broker = _build()

    await svc.subscribe_exchange_price("005930", exchange)

    getattr(broker, expected_target).assert_awaited_once_with("005930")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exchange, expected_target",
    [("NXT", "unsubscribe_nxt_price"), ("KRX", "unsubscribe_realtime_price")],
)
async def test_exchange_price_unsubscription_picks_the_matching_stream(exchange, expected_target):
    svc, broker = _build()

    await svc.unsubscribe_exchange_price("005930", exchange)

    getattr(broker, expected_target).assert_awaited_once_with("005930")


# --- 수신 태스크 생존 확인 ----------------------------------------------------

def test_receive_alive_probe_is_false_when_the_broker_lacks_the_method():
    svc, broker = _build()
    broker.is_websocket_receive_alive = None

    assert svc._is_websocket_receive_alive() is False


def test_receive_alive_probe_swallows_broker_errors():
    svc, broker = _build()
    broker.is_websocket_receive_alive = MagicMock(side_effect=RuntimeError("closed"))

    assert svc._is_websocket_receive_alive() is False


# --- 체결통보 구독 안전장치 ---------------------------------------------------

@pytest.mark.asyncio
async def test_order_notice_subscription_is_skipped_once_disabled():
    svc, broker = _build()
    svc._order_notice_auto_subscribe_disabled = True

    assert await svc._subscribe_order_notice_safely(True) is True
    broker.subscribe_order_notice.assert_not_awaited()


@pytest.mark.asyncio
async def test_order_notice_subscription_failure_does_not_abort_the_connection():
    svc, broker = _build()
    broker.subscribe_order_notice = AsyncMock(side_effect=RuntimeError("거부"))

    assert await svc._subscribe_order_notice_safely(True) is True
    svc.logger.warning.assert_called_once()


@pytest.mark.asyncio
async def test_order_notice_health_check_is_skipped_when_receive_was_already_dead():
    svc, broker = _build()

    assert await svc._subscribe_order_notice_safely(False) is True
    assert svc._order_notice_auto_subscribe_disabled is False


@pytest.mark.asyncio
async def test_order_notice_subscription_that_kills_receive_disables_itself_and_reconnects():
    svc, broker = _build()
    streaming_logger = MagicMock()
    svc._streaming_logger = streaming_logger
    # 구독 직후 수신 태스크가 죽은 상태로 관측된다.
    broker.is_websocket_receive_alive = MagicMock(return_value=False)
    broker.connect_websocket = AsyncMock(return_value=True)

    assert await svc._subscribe_order_notice_safely(True) is True

    assert svc._order_notice_auto_subscribe_disabled is True
    broker.disconnect_websocket.assert_awaited_once()
    broker.connect_websocket.assert_awaited_once_with(svc._callback)
    streaming_logger.log_connect.assert_called_once()


@pytest.mark.asyncio
async def test_disconnect_failure_during_recovery_is_logged_and_reconnect_still_runs():
    svc, broker = _build()
    broker.is_websocket_receive_alive = MagicMock(return_value=False)
    broker.disconnect_websocket = AsyncMock(side_effect=RuntimeError("이미 닫힘"))
    broker.connect_websocket = AsyncMock(return_value=False)

    assert await svc._subscribe_order_notice_safely(True) is False
    assert svc.logger.warning.call_count == 2


# --- 모니터 코드 일괄 구독 ----------------------------------------------------

@pytest.mark.asyncio
async def test_market_status_monitors_warn_when_the_ack_never_arrives():
    svc, broker = _build(market_status_monitor_codes=["005930"])
    broker.subscribe_market_status = AsyncMock(return_value=True)
    broker.wait_market_status_ack = AsyncMock(return_value=False)

    await svc._subscribe_market_status_monitors()

    svc.logger.warning.assert_called_once()
    assert "ACK 미확정" in svc.logger.warning.call_args.args[0]


@pytest.mark.asyncio
async def test_market_status_monitor_failure_is_isolated_per_code():
    svc, broker = _build(market_status_monitor_codes=["005930", "000660"])
    broker.subscribe_market_status = AsyncMock(side_effect=[RuntimeError("거부"), True])
    broker.wait_market_status_ack = AsyncMock(return_value=True)

    await svc._subscribe_market_status_monitors()

    assert broker.subscribe_market_status.await_count == 2
    svc.logger.warning.assert_called_once()


@pytest.mark.asyncio
async def test_futures_sidecar_monitors_warn_when_the_ack_never_arrives():
    svc, broker = _build(futures_sidecar_monitor_codes=["101W09"])
    broker.subscribe_index_futures_contract = AsyncMock(return_value=True)
    broker.wait_index_futures_contract_ack = AsyncMock(return_value=False)

    await svc._subscribe_futures_sidecar_monitors()

    svc.logger.warning.assert_called_once()
    assert "ACK 미확정" in svc.logger.warning.call_args.args[0]


@pytest.mark.asyncio
async def test_futures_sidecar_monitor_failure_is_isolated_per_code():
    svc, broker = _build(futures_sidecar_monitor_codes=["101W09", "201W09"])
    broker.subscribe_index_futures_contract = AsyncMock(
        side_effect=[RuntimeError("거부"), True]
    )
    broker.wait_index_futures_contract_ack = AsyncMock(return_value=True)

    await svc._subscribe_futures_sidecar_monitors()

    assert broker.subscribe_index_futures_contract.await_count == 2
    svc.logger.warning.assert_called_once()


# --- 통합 체결가 구독과 구독 상태 연동 ----------------------------------------

@pytest.mark.asyncio
async def test_unified_price_subscription_marks_the_request_on_the_price_stream():
    price_stream = MagicMock()
    price_stream.on_price_tick = MagicMock()
    svc, broker = _build(price_stream_service=price_stream)
    broker.subscribe_unified_price = AsyncMock(return_value=True)

    assert await svc.subscribe_unified_price("005930") is True
    price_stream.mark_subscription_requested.assert_called_once_with("005930")


@pytest.mark.asyncio
async def test_unified_price_unsubscription_clears_the_price_stream_state():
    price_stream = MagicMock()
    price_stream.on_price_tick = MagicMock()
    svc, broker = _build(price_stream_service=price_stream)
    broker.unsubscribe_unified_price = AsyncMock(return_value=True)

    assert await svc.unsubscribe_unified_price("005930") is True
    price_stream.clear_subscription_state.assert_called_once_with("005930")


@pytest.mark.asyncio
async def test_failed_unified_price_unsubscription_keeps_the_price_stream_state():
    price_stream = MagicMock()
    price_stream.on_price_tick = MagicMock()
    svc, broker = _build(price_stream_service=price_stream)
    broker.unsubscribe_unified_price = AsyncMock(return_value=False)

    assert await svc.unsubscribe_unified_price("005930") is False
    price_stream.clear_subscription_state.assert_not_called()
