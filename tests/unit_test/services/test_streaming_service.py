import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from services.streaming_service import StreamingService
from common.types import ResCommonResponse, ErrorCode

@pytest.fixture
def mock_broker():
    broker = AsyncMock()
    broker.connect_websocket = AsyncMock()
    broker.disconnect_websocket = AsyncMock()
    broker.subscribe_program_trading = AsyncMock()
    broker.unsubscribe_program_trading = AsyncMock()
    broker.subscribe_realtime_price = AsyncMock()
    broker.unsubscribe_realtime_price = AsyncMock()
    broker.subscribe_realtime_quote = AsyncMock()
    broker.unsubscribe_realtime_quote = AsyncMock()
    broker.subscribe_order_notice = AsyncMock(return_value=True)
    broker.unsubscribe_order_notice = AsyncMock(return_value=True)
    broker.subscribe_unified_price = AsyncMock()
    broker.unsubscribe_unified_price = AsyncMock()
    broker.get_program_trade_by_stock_daily = AsyncMock()
    return broker

@pytest.fixture
def mock_logger():
    return MagicMock()

@pytest.fixture
def mock_market_clock():
    clock = MagicMock()
    clock.async_sleep = AsyncMock()
    return clock

@pytest.fixture
def mock_market_data_service():
    service = MagicMock()
    service._stock_repo = MagicMock()
    service._stock_repo.update_realtime_data = MagicMock()
    return service

@pytest.fixture
def streaming_service(mock_broker, mock_logger, mock_market_clock, mock_market_data_service):
    return StreamingService(
        broker_api_wrapper=mock_broker,
        logger=mock_logger,
        market_clock=mock_market_clock,
        market_data_service=mock_market_data_service
    )


@pytest.fixture
def mock_streaming_logger():
    return MagicMock()

def test_init_with_price_stream_service(mock_broker, mock_logger, mock_market_clock, mock_market_data_service):
    """초기화 시 price_stream_service를 전달하면 자동으로 핸들러가 등록되는지 테스트"""
    mock_price_stream = MagicMock()
    service = StreamingService(
        broker_api_wrapper=mock_broker,
        logger=mock_logger,
        market_clock=mock_market_clock,
        market_data_service=mock_market_data_service,
        price_stream_service=mock_price_stream
    )
    assert service._price_stream_service == mock_price_stream
    assert mock_price_stream.on_price_tick in service._handlers.get('realtime_price', [])

@pytest.mark.asyncio
async def test_connect_disconnect_websocket(streaming_service, mock_broker):
    """WebSocket 연결 및 해제 위임 테스트"""
    await streaming_service.connect_websocket()
    mock_broker.connect_websocket.assert_awaited_once()

    await streaming_service.disconnect_websocket()
    mock_broker.disconnect_websocket.assert_awaited_once()


@pytest.mark.asyncio
async def test_connect_disconnect_websocket_logs_streaming_events(
    mock_broker, mock_logger, mock_market_clock, mock_market_data_service, mock_streaming_logger
):
    """streaming_logger가 있으면 connect/disconnect 이벤트를 기록한다."""
    mock_broker.connect_websocket.return_value = True
    service = StreamingService(
        broker_api_wrapper=mock_broker,
        logger=mock_logger,
        market_clock=mock_market_clock,
        market_data_service=mock_market_data_service,
        streaming_logger=mock_streaming_logger,
    )

    await service.connect_websocket()
    await service.disconnect_websocket()

    mock_streaming_logger.log_connect.assert_called_once()
    mock_streaming_logger.log_disconnect.assert_called_once()


@pytest.mark.asyncio
async def test_connect_websocket_stores_callback(streaming_service, mock_broker):
    """connect_websocket: 콜백이 내부에 저장되어 재연결 시 유지되는지 검증"""
    callback = MagicMock()

    # 첫 호출: 콜백 전달 → 내부 저장
    await streaming_service.connect_websocket(callback)
    assert streaming_service._callback is callback
    mock_broker.connect_websocket.assert_awaited_with(callback)

    mock_broker.connect_websocket.reset_mock()

    # 두 번째 호출: 콜백 없이 호출 → 저장된 콜백 사용
    await streaming_service.connect_websocket()
    mock_broker.connect_websocket.assert_awaited_with(callback)


@pytest.mark.asyncio
async def test_connect_websocket_logs_order_notice_subscription_failure(
    mock_broker, mock_logger, mock_market_clock, mock_market_data_service
):
    mock_broker.connect_websocket.return_value = True
    mock_broker.subscribe_order_notice.side_effect = RuntimeError("notice down")
    service = StreamingService(
        broker_api_wrapper=mock_broker,
        logger=mock_logger,
        market_clock=mock_market_clock,
        market_data_service=mock_market_data_service,
    )

    assert await service.connect_websocket() is True

    mock_logger.warning.assert_called_once()


@pytest.mark.asyncio
async def test_connect_websocket_no_callback(streaming_service, mock_broker):
    """connect_websocket: 콜백 없이 호출 시 None이 전달됨"""
    await streaming_service.connect_websocket()
    mock_broker.connect_websocket.assert_awaited_with(None)

@pytest.mark.asyncio
async def test_subscribe_unsubscribe_program_trading(streaming_service, mock_broker):
    """프로그램매매 실시간 구독 및 해지 위임 테스트"""
    await streaming_service.subscribe_program_trading("005930")
    mock_broker.subscribe_program_trading.assert_awaited_once_with("005930")

    await streaming_service.unsubscribe_program_trading("005930")
    mock_broker.unsubscribe_program_trading.assert_awaited_once_with("005930")

@pytest.mark.asyncio
async def test_subscribe_unsubscribe_realtime_price(streaming_service, mock_broker):
    """실시간 체결가 구독 및 해지 위임 테스트"""
    await streaming_service.subscribe_realtime_price("005930")
    mock_broker.subscribe_realtime_price.assert_awaited_once_with("005930")

    await streaming_service.unsubscribe_realtime_price("005930")
    mock_broker.unsubscribe_realtime_price.assert_awaited_once_with("005930")

@pytest.mark.asyncio
async def test_subscribe_unsubscribe_unified_price(streaming_service, mock_broker):
    """통합 체결가 구독 및 해지 위임 테스트"""
    await streaming_service.subscribe_unified_price("005930")
    mock_broker.subscribe_unified_price.assert_awaited_once_with("005930")

    await streaming_service.unsubscribe_unified_price("005930")
    mock_broker.unsubscribe_unified_price.assert_awaited_once_with("005930")

@pytest.mark.asyncio
async def test_handle_program_trading_stream(streaming_service, mock_broker, mock_market_clock):
    """고수준 스트림 핸들러: 프로그램매매 스트리밍 처리 (duration 0으로 빠르게 통과)"""
    await streaming_service.handle_program_trading_stream("005930", duration=0)

    mock_broker.connect_websocket.assert_awaited_once()
    mock_broker.subscribe_program_trading.assert_awaited_once_with("005930")
    mock_market_clock.async_sleep.assert_awaited_once_with(0)
    mock_broker.unsubscribe_program_trading.assert_awaited_once_with("005930")
    mock_broker.disconnect_websocket.assert_awaited_once()

@pytest.mark.asyncio
async def test_handle_program_trading_stream_exception(streaming_service, mock_broker, mock_market_clock):
    """프로그램매매 스트리밍 중 예외 발생 시 finally 블록이 동작하는지 검증"""
    mock_market_clock.async_sleep.side_effect = Exception("Sleep Error")

    with pytest.raises(Exception, match="Sleep Error"):
        await streaming_service.handle_program_trading_stream("005930", duration=10)

    mock_broker.connect_websocket.assert_awaited_once()
    mock_broker.unsubscribe_program_trading.assert_awaited_once_with("005930")
    mock_broker.disconnect_websocket.assert_awaited_once()

@pytest.mark.asyncio
async def test_handle_realtime_stream(streaming_service, mock_broker, mock_market_clock):
    """고수준 스트림 핸들러: 실시간 스트림 다중 종목/필드 구독 및 해지 처리"""
    # duration=0 을 넘겨서 while 루프를 한 번도 돌지 않도록 빠르게 통과
    await streaming_service.handle_realtime_stream(
        stock_codes=["005930", "000660"],
        fields=["price", "quote"],
        duration=0
    )

    mock_broker.connect_websocket.assert_awaited_once()
    assert mock_broker.subscribe_realtime_price.await_count == 2
    assert mock_broker.subscribe_realtime_quote.await_count == 2

    assert mock_broker.unsubscribe_realtime_price.await_count == 2
    assert mock_broker.unsubscribe_realtime_quote.await_count == 2
    mock_broker.disconnect_websocket.assert_awaited_once()

@pytest.mark.asyncio
async def test_handle_realtime_stream_exception(streaming_service, mock_broker):
    """실시간 스트림 핸들러 수행 중 오류 발생 시 정상적인 리소스 정리(finally) 테스트"""
    mock_broker.subscribe_realtime_price.side_effect = Exception("Test Error")

    await streaming_service.handle_realtime_stream(
        stock_codes=["005930"],
        fields=["price"],
        duration=0
    )

    streaming_service.logger.exception.assert_called_once()
    mock_broker.unsubscribe_realtime_price.assert_awaited_once_with("005930")
    mock_broker.disconnect_websocket.assert_awaited_once()

@pytest.mark.asyncio
async def test_handle_realtime_stream_only_quote(streaming_service, mock_broker):
    """고수준 스트림 핸들러: price 필드 없이 quote만 구독하는 경우"""
    await streaming_service.handle_realtime_stream(
        stock_codes=["005930"],
        fields=["quote"],
        duration=0
    )

    mock_broker.subscribe_realtime_price.assert_not_awaited()
    mock_broker.subscribe_realtime_quote.assert_awaited_once_with("005930")
    mock_broker.unsubscribe_realtime_price.assert_not_awaited()
    mock_broker.unsubscribe_realtime_quote.assert_awaited_once_with("005930")

# ── Observer 패턴 테스트 ──────────────────────────────────────────

def test_register_handler_called_with_inner_data(streaming_service):
    """register_handler: 등록 핸들러가 inner data dict으로 호출되는지 확인"""
    handler = MagicMock()
    streaming_service.register_handler('realtime_price', handler)

    inner = {
        '유가증권단축종목코드': '005930',
        '주식현재가': '70000',
        '전일대비': '1000',
        '전일대비율': '1.45',
        '전일대비부호': '2',
        '누적거래량': '1000000',
        '주식체결시간': '100000',
    }
    streaming_service.dispatch_realtime_message({'type': 'realtime_price', 'data': inner})

    handler.assert_called_once_with(inner)


def test_register_multiple_handlers_all_called(streaming_service):
    """register_handler: 동일 타입에 복수 핸들러 등록 시 모두 호출됨"""
    h1 = MagicMock()
    h2 = MagicMock()
    streaming_service.register_handler('realtime_price', h1)
    streaming_service.register_handler('realtime_price', h2)

    inner = {'유가증권단축종목코드': '005930', '주식현재가': '70000'}
    streaming_service.dispatch_realtime_message({'type': 'realtime_price', 'data': inner})

    h1.assert_called_once_with(inner)
    h2.assert_called_once_with(inner)


def test_handler_exception_does_not_affect_other_handlers(streaming_service):
    """handler 격리: 첫 번째 핸들러 예외가 두 번째 핸들러 호출을 막지 않는다"""
    failing_handler = MagicMock(side_effect=RuntimeError("handler crash"))
    good_handler = MagicMock()

    streaming_service.register_handler('realtime_price', failing_handler)
    streaming_service.register_handler('realtime_price', good_handler)

    inner = {'유가증권단축종목코드': '005930', '주식현재가': '70000'}
    streaming_service.dispatch_realtime_message({'type': 'realtime_price', 'data': inner})

    failing_handler.assert_called_once()
    good_handler.assert_called_once_with(inner)
    streaming_service.logger.error.assert_called_once()


@pytest.mark.asyncio
async def test_async_handler_is_scheduled_with_inner_data(streaming_service):
    """코루틴 핸들러는 background task로 스케줄되고 inner data를 전달받는다."""
    called = []

    async def async_handler(inner):
        called.append(inner)

    streaming_service.register_handler('realtime_price', async_handler)
    inner = {'유가증권단축종목코드': '005930', '주식현재가': '70000'}
    current_loop = asyncio.get_running_loop()
    tasks = []
    fake_loop = MagicMock()

    def _create_task(coro):
        task = current_loop.create_task(coro)
        tasks.append(task)
        return task

    fake_loop.create_task.side_effect = _create_task

    with patch('services.streaming_service.asyncio.iscoroutinefunction', return_value=True), \
         patch('services.streaming_service.asyncio.get_running_loop', return_value=fake_loop):
        streaming_service.dispatch_realtime_message({'type': 'realtime_price', 'data': inner})
    await asyncio.gather(*tasks)

    assert called == [inner]


@pytest.mark.asyncio
async def test_async_handler_exception_is_logged(streaming_service):
    """코루틴 핸들러 내부 예외는 done callback에서 로깅된다."""
    async def failing_async_handler(_inner):
        raise RuntimeError("async crash")

    streaming_service.register_handler('realtime_price', failing_async_handler)
    inner = {'유가증권단축종목코드': '005930', '주식현재가': '70000'}
    current_loop = asyncio.get_running_loop()
    tasks = []
    fake_loop = MagicMock()

    def _create_task(coro):
        task = current_loop.create_task(coro)
        tasks.append(task)
        return task

    fake_loop.create_task.side_effect = _create_task

    with patch('services.streaming_service.asyncio.iscoroutinefunction', return_value=True), \
         patch('services.streaming_service.asyncio.get_running_loop', return_value=fake_loop):
        streaming_service.dispatch_realtime_message({'type': 'realtime_price', 'data': inner})
    await asyncio.gather(*tasks, return_exceptions=True)

    assert streaming_service.logger.error.call_count >= 1


def test_handler_without_running_loop_falls_back_to_sync_call(streaming_service):
    """이벤트 루프가 없으면 동기 핸들러를 직접 호출한다."""
    handler = MagicMock()
    streaming_service.register_handler('realtime_price', handler)
    inner = {'유가증권단축종목코드': '005930'}

    with patch('services.streaming_service.asyncio.get_running_loop', side_effect=RuntimeError):
        streaming_service.dispatch_realtime_message({'type': 'realtime_price', 'data': inner})

    handler.assert_called_once_with(inner)


def test_signing_notice_data_quality_failure_logs_warning(streaming_service):
    quality = MagicMock(ok=False, reason="bad_notice", metadata={"x": 1})
    dq = MagicMock()
    dq.validate_execution_report.return_value = quality
    streaming_service._data_quality_service = dq

    streaming_service.dispatch_realtime_message({"type": "signing_notice", "data": {"주문번호": "1"}})

    dq.validate_execution_report.assert_called_once()
    streaming_service.logger.warning.assert_called_once()


def test_set_price_stream_service_registers_handler(streaming_service):
    """set_price_stream_service: on_price_tick이 realtime_price 핸들러로 등록됨"""
    mock_svc = MagicMock()
    streaming_service.set_price_stream_service(mock_svc)

    inner = {'유가증권단축종목코드': '005930', '주식현재가': '70000'}
    streaming_service.dispatch_realtime_message({'type': 'realtime_price', 'data': inner})

    mock_svc.on_price_tick.assert_called_once_with(inner)


def test_set_price_stream_service_replaces_previous_handler(streaming_service):
    """set_price_stream_service: 재호출 시 이전 핸들러가 제거되고 새 핸들러로 교체됨"""
    old_svc = MagicMock()
    new_svc = MagicMock()

    streaming_service.set_price_stream_service(old_svc)
    streaming_service.set_price_stream_service(new_svc)

    inner = {'유가증권단축종목코드': '005930', '주식현재가': '70000'}
    streaming_service.dispatch_realtime_message({'type': 'realtime_price', 'data': inner})

    old_svc.on_price_tick.assert_not_called()
    new_svc.on_price_tick.assert_called_once_with(inner)


def test_get_cached_realtime_price_delegates_to_price_stream_service(streaming_service):
    """get_cached_realtime_price: PriceStreamService 설정 시 위임하여 반환"""
    mock_svc = MagicMock()
    mock_svc.get_cached_price.return_value = {"price": "70000"}
    streaming_service.set_price_stream_service(mock_svc)

    result = streaming_service.get_cached_realtime_price("005930")

    mock_svc.get_cached_price.assert_called_once_with("005930")
    assert result == {"price": "70000"}


def test_get_cached_realtime_price_returns_none_without_service(streaming_service):
    """get_cached_realtime_price: PriceStreamService 미설정 시 None 반환"""
    result = streaming_service.get_cached_realtime_price("005930")
    assert result is None


def test_is_subscribed_realtime_price_false_without_repo(streaming_service):
    assert streaming_service.is_subscribed_realtime_price("005930") is False


def test_is_subscribed_realtime_price_uses_streaming_stock_repo(streaming_service):
    """통합 체결가 desired 상태를 기준으로 구독 여부를 판단한다."""
    repo = MagicMock()
    repo.get_desired.return_value = {"005930"}
    streaming_service.set_streaming_stock_repo(repo)

    assert streaming_service.is_subscribed_realtime_price("005930") is True
    assert streaming_service.is_subscribed_realtime_price("000660") is False


def test_is_subscribed_realtime_price_treats_program_trading_as_price_expected(streaming_service):
    """프로그램매매 desired만 있어도 PT 누락 판단을 위해 True를 반환한다."""
    repo = MagicMock()
    repo.get_desired.side_effect = lambda streaming_type: (
        set() if streaming_type.name == "UNIFIED_PRICE" else {"005930"}
    )
    streaming_service.set_streaming_stock_repo(repo)

    assert streaming_service.is_subscribed_realtime_price("005930") is True
    assert streaming_service.is_subscribed_realtime_price("000660") is False


@pytest.mark.asyncio
async def test_subscribe_realtime_price_marks_subscription_requested(streaming_service, mock_broker):
    """실시간 가격 구독 전 price stream service에 구독 요청 시각을 기록한다."""
    mock_price_stream = MagicMock()
    streaming_service.set_price_stream_service(mock_price_stream)

    await streaming_service.subscribe_realtime_price("005930")

    mock_price_stream.mark_subscription_requested.assert_called_once_with("005930")
    mock_broker.subscribe_realtime_price.assert_awaited_once_with("005930")


@pytest.mark.asyncio
async def test_subscribe_unified_price_marks_subscription_requested_even_on_failure(streaming_service, mock_broker):
    """브로커 구독이 실패해도 watchdog이 추적할 수 있게 요청 시각은 먼저 기록한다."""
    mock_broker.subscribe_unified_price.return_value = False
    mock_price_stream = MagicMock()
    streaming_service.set_price_stream_service(mock_price_stream)

    result = await streaming_service.subscribe_unified_price("005930")

    assert result is False
    mock_price_stream.mark_subscription_requested.assert_called_once_with("005930")
    mock_broker.subscribe_unified_price.assert_awaited_once_with("005930")


@pytest.mark.asyncio
async def test_unsubscribe_unified_price_clears_subscription_state(streaming_service, mock_broker):
    """통합 체결가 해지 시 price stream service의 추적 상태를 정리한다."""
    mock_price_stream = MagicMock()
    streaming_service.set_price_stream_service(mock_price_stream)

    await streaming_service.unsubscribe_unified_price("005930")

    mock_price_stream.clear_subscription_state.assert_called_once_with("005930")
    mock_broker.unsubscribe_unified_price.assert_awaited_once_with("005930")


@pytest.mark.asyncio
async def test_unsubscribe_realtime_price_clears_state_only_on_success(streaming_service, mock_broker):
    mock_price_stream = MagicMock()
    streaming_service.set_price_stream_service(mock_price_stream)

    mock_broker.unsubscribe_realtime_price.return_value = True
    assert await streaming_service.unsubscribe_realtime_price("005930") is True
    mock_price_stream.clear_subscription_state.assert_called_once_with("005930")

    mock_price_stream.clear_subscription_state.reset_mock()
    mock_broker.unsubscribe_realtime_price.return_value = False
    assert await streaming_service.unsubscribe_realtime_price("005930") is False
    mock_price_stream.clear_subscription_state.assert_not_called()


def test_dispatch_realtime_message_realtime_price_console_log(streaming_service):
    """dispatch_realtime_message: realtime_price 수신 시 콘솔 디버그 로그 출력"""
    # 스로틀 우회를 위해 마지막 출력 시각을 과거로 설정
    streaming_service._last_console_print_time = 0.0

    data = {
        'type': 'realtime_price',
        'data': {
            '유가증권단축종목코드': '005930',
            '주식현재가': '70000',
            '전일대비': '1000',
            '전일대비율': '1.45',
            '전일대비부호': '2',
            '누적거래량': '1000000',
            '주식체결시간': '100000',
        }
    }
    streaming_service.dispatch_realtime_message(data)

    debug_calls_str = str(streaming_service.logger.debug.call_args_list)
    assert "[실시간 체결 - 100000]" in debug_calls_str
    assert "현재가 70000원" in debug_calls_str


def test_dispatch_realtime_message_realtime_quote(streaming_service):
    """메시지 디스패치: 실시간 호가 수신 시 로깅 확인"""
    streaming_service._last_console_print_time = 0.0

    data = {
        'type': 'realtime_quote',
        'data': {
            '유가증권단축종목코드': '005930',
            '매도호가1': '70100',
            '매수호가1': '70000',
            '영업시간': '100000'
        }
    }

    streaming_service.dispatch_realtime_message(data)
    debug_calls_str = str(streaming_service.logger.debug.call_args_list)
    assert "[실시간 호가 - 100000]" in debug_calls_str
    assert "매도1호가: 70100" in debug_calls_str

def test_dispatch_realtime_message_program_trading(streaming_service):
    """메시지 디스패치: 프로그램매매 실시간 데이터 로깅 확인"""
    streaming_service._last_console_print_time = 0.0

    data = {
        'type': 'realtime_program_trading',
        'data': {
            '주식체결시간': '100000',
            '순매수거래대금': '5000'
        }
    }

    streaming_service.dispatch_realtime_message(data)
    
    debug_calls_str = str(streaming_service.logger.debug.call_args_list)
    assert "[프로그램매매 - 100000]" in debug_calls_str
    assert "순매수거래대금: 5000" in debug_calls_str


def test_dispatch_realtime_message_signing_notice(streaming_service):
    """메시지 디스패치: 체결통보 수신 시 상세 로그를 남긴다."""
    data = {
        'type': 'signing_notice',
        'data': {
            '주문번호': 'A001',
            '체결수량': '10',
            '체결단가': '70000',
            '주식체결시간': '100000',
        }
    }

    streaming_service.dispatch_realtime_message(data)

    debug_calls_str = str(streaming_service.logger.debug.call_args_list)
    assert "[체결통보]" in debug_calls_str
    assert "주문: A001" in debug_calls_str

def test_dispatch_realtime_message_unknown_type(streaming_service):
    """메시지 디스패치: 알 수 없는 타입의 메시지 수신 시 로깅 처리"""
    data = {
        'type': 'unknown_type',
        'tr_id': 'UNKNOWN',
        'data': {}
    }

    streaming_service.dispatch_realtime_message(data)
    assert streaming_service.logger.debug.call_count == 2

def test_dispatch_realtime_message_throttling(streaming_service):
    """스로틀링 로직: 제한 시간 내 연달아 호출 시 첫 번째만 로깅됨을 검증"""
    data = {
        'type': 'realtime_price',
        'data': {'유가증권단축종목코드': '005930', '주식현재가': '70000'}
    }
    
    # time.monotonic()가 고정값을 반환하도록 패치
    with patch('time.monotonic', return_value=100.0):
        streaming_service.dispatch_realtime_message(data)
        # 1. "실시간 데이터 수신..." (무조건 출력)
        # 2. "[실시간 체결..." (스로틀 통과)
        assert streaming_service.logger.debug.call_count == 2
        
        # 동일 시간에 재호출 -> 체결 로그 스로틀링 작동 (무조건 출력 로그 1회만 증가)
        streaming_service.dispatch_realtime_message(data)
        assert streaming_service.logger.debug.call_count == 3

    # 제한 시간(0.5초)이 지난 후 호출 -> 로깅 다시 작동
    with patch('time.monotonic', return_value=101.0):
        streaming_service.dispatch_realtime_message(data)
        # 3. "실시간 데이터 수신..." (무조건 출력)
        # 4. "[실시간 체결..." (스로틀 통과)
        assert streaming_service.logger.debug.call_count == 5


def test_dispatch_realtime_message_throttling_with_dict_state(streaming_service):
    """dict 기반 per-type 스로틀 상태도 정상 동작한다."""
    streaming_service._last_console_print_time = {'realtime_price': 99.0}
    data = {
        'type': 'realtime_price',
        'data': {'유가증권단축종목코드': '005930', '주식현재가': '70000'}
    }

    with patch('time.monotonic', return_value=100.0):
        streaming_service.dispatch_realtime_message(data)

    assert streaming_service._last_console_print_time['realtime_price'] == 100.0


def test_dispatch_realtime_message_throttling_fallback_on_bad_state(streaming_service):
    """스로틀 상태가 비정상이면 fallback으로 허용하고 dict 상태로 복구한다."""
    streaming_service._last_console_print_time = object()
    data = {
        'type': 'realtime_program_trading',
        'data': {'주식체결시간': '100000', '순매수거래대금': '5000'}
    }

    with patch('time.monotonic', return_value=100.0):
        streaming_service.dispatch_realtime_message(data)

    assert streaming_service._last_console_print_time == {'realtime_program_trading': 100.0}

@pytest.mark.asyncio
async def test_handle_get_program_trading_history_success(streaming_service, mock_broker):
    """REST 조회: 프로그램매매 히스토리 정상 조회"""
    expected_resp = ResCommonResponse(rt_cd="0", msg1="정상", data=[])
    mock_broker.get_program_trade_by_stock_daily.return_value = expected_resp

    res = await streaming_service.handle_get_program_trading_history("005930")
    assert res == expected_resp
    mock_broker.get_program_trade_by_stock_daily.assert_awaited_once_with("005930")

@pytest.mark.asyncio
async def test_handle_get_program_trading_history_exception(streaming_service, mock_broker):
    """REST 조회: 프로그램매매 히스토리 조회 시 예외 발생 방어"""
    mock_broker.get_program_trade_by_stock_daily.side_effect = Exception("API 에러")

    res = await streaming_service.handle_get_program_trading_history("005930")
    assert res.rt_cd == ErrorCode.API_ERROR.value
    streaming_service.logger.error.assert_called_once()
