import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from io import StringIO
import builtins
from unittest.mock import call, ANY
from services.market_calendar_service import MarketCalendarService
from common.types import ResCommonResponse, ErrorCode, Exchange, OrderContext, OrderState, OrderExecutionReport, OrderSide
from services.notification_service import NotificationCategory, NotificationLevel
from services.order_execution_service import OrderExecutionService

# 테스트를 위한 MockLogger
class MockLogger:
    def __init__(self):
        self.info = MagicMock()
        self.debug = MagicMock()
        self.warning = MagicMock()
        self.error = MagicMock()
        self.critical = MagicMock()
        self.exception = MagicMock()

@pytest.fixture
def mock_broker_api_wrapper():
    """BrokerAPIWrapper의 AsyncMock 인스턴스를 제공하는 픽스처."""
    mock = AsyncMock()
    # place_stock_order의 기본 반환값 설정
    mock.place_stock_order.return_value = ResCommonResponse(rt_cd="0", msg1="주문 성공", data=None)
    mock.cancel_stock_order.return_value = ResCommonResponse(rt_cd="0", msg1="취소 요청 성공", data=None)
    mock.env = MagicMock(is_paper_trading=True)
    return mock

@pytest.fixture
def mock_logger():
    """MockLogger 인스턴스를 제공하는 픽스처."""
    return MockLogger()

@pytest.fixture
def mock_market_clock():
    """MarketClock의 MagicMock 인스턴스를 제공하는 픽스처."""
    mock = MagicMock()
    mock.is_market_operating_hours.return_value = True # 기본값 설정
    mock.async_sleep = AsyncMock()
    mock.get_current_kst_time.return_value = datetime(2026, 4, 24, 9, 0, 0)
    return mock

@pytest.fixture
def mock_market_calendar_service():
    """MarketCalendarService의 AsyncMock 인스턴스를 제공하는 픽스처."""
    mock = AsyncMock(spec_set=MarketCalendarService)
    mock.is_market_open_now.return_value = True # 기본값 설정
    return mock

@pytest.fixture
def mock_notification_service():
    mock = AsyncMock()
    mock.emit = AsyncMock()
    return mock

@pytest.fixture
def handler(mock_broker_api_wrapper, mock_logger, mock_market_clock, mock_market_calendar_service, mock_notification_service):
    """TransactionHandlers 인스턴스를 제공하는 픽스처."""
    handler_instance = OrderExecutionService(
        broker_api_wrapper=mock_broker_api_wrapper,
        logger=mock_logger,
        market_clock=mock_market_clock,
        market_calendar_service=mock_market_calendar_service,
        notification_service=mock_notification_service,
    )
    return handler_instance

# --- Pytest 스타일 테스트 케이스 ---

@pytest.mark.asyncio
async def test_handle_buy_stock_success(handler, mock_broker_api_wrapper, mock_logger):
    """handle_buy_stock 매수 성공 시나리오 테스트."""
    stock_code_input = "005930"
    qty_input = "10"
    price_input = "70000"

    mock_broker_api_wrapper.place_stock_order.return_value = ResCommonResponse(
        rt_cd="0",
        msg1="주문 성공",
        data=None
    )
    await handler.handle_buy_stock(stock_code_input, qty_input, price_input)

    mock_broker_api_wrapper.place_stock_order.assert_awaited_once_with(stock_code_input, int(price_input), int(qty_input), is_buy=True, exchange=Exchange.KRX)

    mock_logger.info.assert_called()
    info_msgs = [str(call.args[0]) for call in mock_logger.info.call_args_list]
    assert any("주식 매수 주문 성공" in m and f"종목={stock_code_input}" in m for m in info_msgs)


@pytest.mark.asyncio
async def test_handle_buy_stock_market_closed(handler, mock_broker_api_wrapper, mock_market_calendar_service, mock_logger):
    """handle_buy_stock 시장 마감 시 매수 실패 테스트."""
    stock_code_input = "005930"
    qty_input = "10"
    price_input = "70000"

    mock_market_calendar_service.is_market_open_now.return_value = False

    await handler.handle_buy_stock(stock_code_input, qty_input, price_input)

    mock_logger.warning.assert_called_once_with("시장이 닫혀 있어 매수 주문을 제출하지 못했습니다.")
    mock_broker_api_wrapper.place_stock_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_buy_stock_invalid_input(handler, mock_broker_api_wrapper, mock_logger):
    """handle_buy_stock 유효하지 않은 입력 시 매수 실패 테스트."""
    stock_code_input = "005930"
    qty_input = "abc" # 잘못된 수량 입력
    price_input = "70000"

    await handler.handle_buy_stock(stock_code_input, qty_input, price_input)

    mock_logger.warning.assert_called_once()
    mock_broker_api_wrapper.place_stock_order.assert_not_awaited()

@pytest.mark.asyncio
async def test_handle_buy_stock_place_order_delegation_failure(handler, mock_broker_api_wrapper, mock_logger):
    """handle_buy_stock 주문 위임 실패 시 테스트."""
    stock_code_input = "005930"
    qty_input = "10"
    price_input = "70000"

    mock_broker_api_wrapper.place_stock_order.return_value = ResCommonResponse(
        rt_cd="1",
        msg1="주문 실패",
        data=None
    )
    await handler.handle_buy_stock(stock_code_input, qty_input, price_input)

    mock_broker_api_wrapper.place_stock_order.assert_awaited_once_with("005930", 70000, 10, is_buy=True, exchange=Exchange.KRX)
    logged_msg = mock_logger.error.call_args[0][0]
    assert "매수 주문 실패" in logged_msg
    assert "005930" in logged_msg

@pytest.mark.asyncio
async def test_handle_sell_stock_success(handler, mock_broker_api_wrapper):
    """handle_sell_stock 매도 성공 시나리오 테스트."""
    stock_code_input = "005930"
    qty_input = "5"
    price_input = "60000"

    mock_broker_api_wrapper.place_stock_order.return_value = ResCommonResponse(
        rt_cd="0",
        msg1="매도 성공",
        data=None  # 실제 주문 결과 데이터가 있다면 여기에 넣기
    )
    await handler.handle_sell_stock(stock_code_input, qty_input, price_input)

    mock_broker_api_wrapper.place_stock_order.assert_awaited_once_with("005930", 60000, 5, is_buy=False, exchange=Exchange.KRX)


@pytest.mark.asyncio
async def test_handle_sell_stock_market_closed(handler, mock_broker_api_wrapper, mock_market_calendar_service, mock_logger):
    """handle_sell_stock 시장 마감 시 매도 실패 테스트."""
    stock_code_input = "005930"
    qty_input = "5"
    price_input = "60000"

    mock_market_calendar_service.is_market_open_now.return_value = False

    await handler.handle_sell_stock(stock_code_input, qty_input, price_input)

    mock_logger.warning.assert_called_once_with("시장이 닫혀 있어 매도 주문을 제출하지 못했습니다.")
    mock_broker_api_wrapper.place_stock_order.assert_not_awaited()

@pytest.mark.asyncio
async def test_handle_sell_stock_invalid_input(handler, mock_broker_api_wrapper, mock_logger):
    """handle_sell_stock 유효하지 않은 입력 시 매도 실패 테스트."""
    stock_code_input = "005930"
    qty_input = "xyz"
    price_input = "60000"

    await handler.handle_sell_stock(stock_code_input, qty_input, price_input)

    mock_logger.warning.assert_called_once()
    mock_broker_api_wrapper.place_stock_order.assert_not_awaited()

@pytest.mark.asyncio
async def test_handle_sell_stock_place_order_delegation_failure(handler, mock_broker_api_wrapper, mock_logger):
    """handle_sell_stock 주문 위임 실패 시 테스트."""
    stock_code_input = "005930"
    qty_input = "5"
    price_input = "60000"

    mock_broker_api_wrapper.place_stock_order.return_value = ResCommonResponse(
        rt_cd="1",
        msg1="주문 실패",
        data=None
    )

    await handler.handle_sell_stock(stock_code_input, qty_input, price_input)

    from common.types import Exchange
    mock_broker_api_wrapper.place_stock_order.assert_awaited_once_with("005930", 60000, 5, is_buy=False, exchange=Exchange.KRX)
    logged_msg = mock_logger.error.call_args[0][0]
    assert "매도 주문 실패" in logged_msg
    assert "005930" in logged_msg

@pytest.mark.asyncio
async def test_handle_place_buy_order_success(handler, mock_broker_api_wrapper, mock_logger):
    """handle_place_buy_order 매수 주문 실행 성공 테스트."""
    mock_broker_api_wrapper.place_stock_order.return_value = ResCommonResponse(
        rt_cd="0",
        msg1="주문 성공",
        data=None  # 실제 주문 결과 데이터가 있다면 여기에 넣기
    )
    result = await handler.handle_place_buy_order("005930", 70000, 10)

    mock_broker_api_wrapper.place_stock_order.assert_awaited_once_with(
        "005930", 70000, 10, is_buy=True, exchange=Exchange.KRX
    )
    mock_logger.info.assert_called()
    assert result.rt_cd == "0"
    assert result.msg1 == "주문 성공"

@pytest.mark.asyncio
async def test_handle_place_buy_order_trading_service_failure(handler, mock_broker_api_wrapper, mock_logger):
    """handle_place_buy_order 매수 주문 실행 실패 테스트."""
    mock_broker_api_wrapper.place_stock_order.return_value = ResCommonResponse(
        rt_cd="1",
        msg1="잔고 부족",
        data=None  # 실제 주문 결과 데이터가 있다면 여기에 넣기
    )
    result = await handler.handle_place_buy_order("005930", 70000, 10)

    mock_logger.error.assert_called_once()
    assert result.rt_cd == "1"
    assert result.msg1 == "잔고 부족"

@pytest.mark.asyncio
async def test_handle_place_sell_order_success(handler, mock_broker_api_wrapper, mock_logger):
    """handle_place_sell_order 매도 주문 실행 성공 테스트."""
    mock_broker_api_wrapper.place_stock_order.return_value = ResCommonResponse(
        rt_cd="0",
        msg1="매도 성공",
        data=None  # 실제 주문 결과 데이터가 있다면 여기에 넣기
    )
    result = await handler.handle_place_sell_order("005930", 60000, 5)

    mock_broker_api_wrapper.place_stock_order.assert_awaited_once_with(
        "005930", 60000, 5, is_buy=False, exchange=Exchange.KRX
    )
    mock_logger.info.assert_called()
    assert result.rt_cd == "0"
    assert result.msg1 == "매도 성공"

@pytest.mark.asyncio
async def test_handle_place_sell_order_trading_service_failure(handler, mock_broker_api_wrapper, mock_logger):
    """handle_place_sell_order 매도 주문 실행 실패 테스트."""
    mock_broker_api_wrapper.place_stock_order.return_value = ResCommonResponse(
        rt_cd="1",
        msg1="수량 부족",
        data=None
    )

    result = await handler.handle_place_sell_order("005930", 60000, 5)

    mock_logger.error.assert_called_once()
    assert result.rt_cd == "1"
    assert result.msg1 == "수량 부족"

@pytest.mark.asyncio # This test is not directly related to the market open check, but it's good to ensure it still works.
async def test_handle_realtime_price_quote_stream_success(handler, mock_broker_api_wrapper, mock_logger, mock_market_calendar_service):
    """실시간 스트림이 성공적으로 연결, 구독, 종료되는지 테스트합니다."""
    mock_broker_api_wrapper.connect_websocket.return_value = True
    mock_broker_api_wrapper.subscribe_realtime_price.return_value = True
    mock_broker_api_wrapper.subscribe_realtime_quote.return_value = True
    mock_broker_api_wrapper.unsubscribe_realtime_price.return_value = True # This was missing in the original mock setup
    mock_broker_api_wrapper.unsubscribe_realtime_quote.return_value = True
    mock_broker_api_wrapper.disconnect_websocket.return_value = True

    with patch('asyncio.to_thread', new_callable=AsyncMock) as mock_to_thread:
        mock_to_thread.return_value = None

        await handler.handle_realtime_price_quote_stream("005930")

        mock_broker_api_wrapper.connect_websocket.assert_awaited_once_with(
            on_message_callback=ANY
        )
        mock_broker_api_wrapper.subscribe_realtime_price.assert_awaited_once_with("005930")
        mock_broker_api_wrapper.subscribe_realtime_quote.assert_awaited_once_with("005930")
        mock_broker_api_wrapper.unsubscribe_realtime_price.assert_awaited_once_with("005930")
        mock_broker_api_wrapper.unsubscribe_realtime_quote.assert_awaited_once_with("005930")
        mock_broker_api_wrapper.disconnect_websocket.assert_awaited_once()
        mock_logger.info.assert_any_call(f"실시간 주식 스트림 종료: 종목=005930")

@pytest.mark.asyncio
async def test_handle_realtime_price_quote_stream_connection_failure(handler, mock_broker_api_wrapper, mock_logger, mock_market_calendar_service):
    """웹소켓 연결에 실패했을 때 스트림이 시작되지 않고 오류 메시지가 출력되는지 테스트합니다."""
    mock_broker_api_wrapper.connect_websocket.return_value = False

    await handler.handle_realtime_price_quote_stream("005930")

    mock_broker_api_wrapper.connect_websocket.assert_awaited_once_with(
        on_message_callback=ANY
    )
    mock_broker_api_wrapper.subscribe_realtime_price.assert_not_awaited()
    mock_logger.error.assert_called_once_with("실시간 웹소켓 연결 실패.")

@pytest.mark.asyncio
async def test_handle_realtime_price_quote_stream_keyboard_interrupt(handler, mock_broker_api_wrapper, mock_logger, mock_market_calendar_service):
    """스트림 수신 중 KeyboardInterrupt 발생 시 정상적으로 종료되는지 테스트합니다."""
    mock_broker_api_wrapper.connect_websocket.return_value = True
    mock_broker_api_wrapper.subscribe_realtime_price.return_value = True
    mock_broker_api_wrapper.subscribe_realtime_quote.return_value = True
    mock_broker_api_wrapper.unsubscribe_realtime_price.return_value = True
    mock_broker_api_wrapper.unsubscribe_realtime_quote.return_value = True
    mock_broker_api_wrapper.disconnect_websocket.return_value = True

    with patch('asyncio.to_thread', new_callable=AsyncMock) as mock_to_thread:
        mock_to_thread.side_effect = KeyboardInterrupt

        await handler.handle_realtime_price_quote_stream("005930")

        mock_logger.info.assert_has_calls([
            call("실시간 구독 중단 (KeyboardInterrupt)."),
            call(f"실시간 주식 스트림 종료: 종목=005930")
        ])
        mock_broker_api_wrapper.unsubscribe_realtime_price.assert_awaited_once()
        mock_broker_api_wrapper.disconnect_websocket.assert_awaited_once()

@pytest.mark.asyncio # This test is directly related to the market open check.
async def test_handle_buy_order_when_market_closed(handler, mock_market_calendar_service, mock_logger, mock_broker_api_wrapper):
    """시장이 닫혀 있을 때 매수 주문이 제출되지 않는지 테스트합니다."""
    mock_market_calendar_service.is_market_open_now.return_value = False

    await handler.handle_place_buy_order("005930", 70000, 10)

    mock_logger.warning.assert_called_once_with("시장이 닫혀 있어 매수 주문을 제출하지 못했습니다.") # Ensure this is called once
    mock_broker_api_wrapper.place_stock_order.assert_not_awaited()

# --- 콜백 함수 내부 로직 검증 테스트 ---

@pytest.mark.asyncio
async def test_realtime_data_display_callback_logic(handler, mock_broker_api_wrapper, mock_logger):
    """
    realtime_data_display_callback 함수의 내부 로직을 다양한 데이터 타입으로 검증합니다.
    """
    mock_broker_api_wrapper.connect_websocket.return_value = True
    mock_broker_api_wrapper.subscribe_realtime_price.return_value = True
    mock_broker_api_wrapper.subscribe_realtime_quote.return_value = True
    mock_broker_api_wrapper.unsubscribe_realtime_price.return_value = True
    mock_broker_api_wrapper.unsubscribe_realtime_quote.return_value = True
    mock_broker_api_wrapper.disconnect_websocket.return_value = True

    with patch('asyncio.to_thread', new_callable=AsyncMock) as mock_to_thread:
        mock_to_thread.return_value = None
        await handler.handle_realtime_price_quote_stream("005930")

    realtime_callback = mock_broker_api_wrapper.connect_websocket.call_args.kwargs['on_message_callback']

    # --- 테스트 시나리오 1: realtime_price (주식 체결) 데이터 ---
    mock_logger.info.reset_mock()
    mock_logger.debug.reset_mock()

    price_data = {
        'type': 'realtime_price',
        'data': {
            'STCK_PRPR': '75000',
            'ACML_VOL': '123456',
            'STCK_CNTG_HOUR': '100000',
            'PRDY_VRSS': '500',
            'PRDY_VRSS_SIGN': '2',
            'PRDY_CTRT': '0.67'
        }
    }
    realtime_callback(price_data)
    mock_logger.info.assert_not_called()
    mock_logger.debug.assert_called_once()


    # --- 테스트 시나리오 2: realtime_quote (주식 호가) 데이터 ---
    mock_logger.info.reset_mock()
    mock_logger.debug.reset_mock()

    quote_data = {
        'type': 'realtime_quote',
        'data': {
            '매도호가1': '75050',
            '매수호가1': '74950',
            '영업시간': '100001'
        }
    }
    realtime_callback(quote_data)
    mock_logger.info.assert_not_called()
    mock_logger.debug.assert_called_once()


    # --- 테스트 시나리오 3: signing_notice (체결 통보) 데이터 ---
    mock_logger.info.reset_mock()
    mock_logger.debug.reset_mock()

    signing_notice_data = {
        'type': 'signing_notice',
        'data': {
            '주문번호': '1234567890',
            '체결수량': '10',
            '체결단가': '75000',
            '주식체결시간': '100002'
        }
    }
    realtime_callback(signing_notice_data)
    mock_logger.info.assert_not_called()
    mock_logger.debug.assert_called_once()


    # --- 테스트 시나리오 4: 처리되지 않은 메시지 (unknown type) ---
    mock_logger.info.reset_mock()
    mock_logger.debug.reset_mock()

    unknown_data = {
        'type': 'unknown_type',
        'tr_id': 'UNKNOWN001',
        'data': {'some_key': 'some_value'}
    }
    realtime_callback(unknown_data)
    mock_logger.debug.assert_called_once_with(f"처리되지 않은 실시간 메시지: UNKNOWN001 - {{'type': 'unknown_type', 'tr_id': 'UNKNOWN001', 'data': {{'some_key': 'some_value'}}}}") # 데이터 전체를 포함하도록 수정

    # --- 테스트 시나리오 5: 데이터가 dict가 아닌 경우 ---
    mock_logger.info.reset_mock()
    mock_logger.debug.reset_mock()

    non_dict_data = "invalid_string_data"
    realtime_callback(non_dict_data)
    mock_logger.debug.assert_not_called()


# --- 주문 재시도 (_retry_order) 테스트 ---

@pytest.mark.asyncio
async def test_retry_order_success_on_first_attempt(handler, mock_broker_api_wrapper):
    """첫 시도에서 성공하면 재시도 없이 즉시 반환."""
    mock_broker_api_wrapper.place_stock_order.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="주문 성공", data=None
    )
    result = await handler._retry_order(
        lambda c, p, q: handler._execute_order_via_broker(c, p, q, is_buy=True), "005930", 70000, 10
    )
    assert result.rt_cd == ErrorCode.SUCCESS.value
    assert mock_broker_api_wrapper.place_stock_order.await_count == 1


@pytest.mark.asyncio
async def test_retry_order_retriable_error_then_success(handler, mock_broker_api_wrapper, mock_market_clock):
    """NETWORK_ERROR 후 재시도에서 성공."""
    mock_broker_api_wrapper.place_stock_order.side_effect = [
        ResCommonResponse(rt_cd=ErrorCode.NETWORK_ERROR.value, msg1="네트워크 오류", data=None),
        ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="주문 성공", data=None),
    ]
    result = await handler._retry_order(
        lambda c, p, q: handler._execute_order_via_broker(c, p, q, is_buy=True), "005930", 70000, 10
    )
    assert result.rt_cd == ErrorCode.SUCCESS.value
    assert mock_broker_api_wrapper.place_stock_order.await_count == 2
    mock_market_clock.async_sleep.assert_awaited_once()


@pytest.mark.asyncio
async def test_retry_order_retriable_error_exhausted(handler, mock_broker_api_wrapper, mock_market_clock):
    """재시도 가능한 오류가 계속 발생하면 마지막 실패 결과 반환."""
    mock_broker_api_wrapper.place_stock_order.return_value = ResCommonResponse(
        rt_cd=ErrorCode.RETRY_LIMIT.value, msg1="재시도 한도 초과", data=None
    )
    result = await handler._retry_order(
        lambda c, p, q: handler._execute_order_via_broker(c, p, q, is_buy=True), "005930", 70000, 10
    )
    assert result.rt_cd == ErrorCode.RETRY_LIMIT.value
    assert mock_broker_api_wrapper.place_stock_order.await_count == 3  # _ORDER_MAX_RETRIES=3


@pytest.mark.asyncio
async def test_retry_order_non_retriable_error_no_retry(handler, mock_broker_api_wrapper, mock_market_clock):
    """API_ERROR 같은 비재시도 오류는 즉시 반환 (재시도 안 함)."""
    mock_broker_api_wrapper.place_stock_order.return_value = ResCommonResponse(
        rt_cd=ErrorCode.API_ERROR.value, msg1="잔고 부족", data=None
    )
    result = await handler._retry_order(
        lambda c, p, q: handler._execute_order_via_broker(c, p, q, is_buy=True), "005930", 70000, 10
    )
    assert result.rt_cd == ErrorCode.API_ERROR.value
    assert mock_broker_api_wrapper.place_stock_order.await_count == 1
    mock_market_clock.async_sleep.assert_not_awaited()


@pytest.mark.asyncio
async def test_retry_order_integrates_with_handle_place_buy_order(handler, mock_broker_api_wrapper, mock_market_clock):
    """handle_place_buy_order가 _retry_order를 통해 재시도하는지 확인."""
    mock_broker_api_wrapper.place_stock_order.side_effect = [
        ResCommonResponse(rt_cd=ErrorCode.NETWORK_ERROR.value, msg1="네트워크 오류", data=None),
        ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="주문 성공", data=None),
    ]
    result = await handler.handle_place_buy_order("005930", 70000, 10)
    assert result.rt_cd == ErrorCode.SUCCESS.value
    assert mock_broker_api_wrapper.place_stock_order.await_count == 2


@pytest.mark.asyncio
async def test_retry_order_integrates_with_handle_place_sell_order(handler, mock_broker_api_wrapper, mock_market_clock):
    """handle_place_sell_order가 _retry_order를 통해 재시도하는지 확인."""
    mock_broker_api_wrapper.place_stock_order.side_effect = [
        ResCommonResponse(rt_cd=ErrorCode.NETWORK_ERROR.value, msg1="네트워크 오류", data=None),
        ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="매도 성공", data=None),
    ]
    result = await handler.handle_place_sell_order("005930", 60000, 5)
    assert result.rt_cd == ErrorCode.SUCCESS.value
    assert mock_broker_api_wrapper.place_stock_order.await_count == 2


# --- sell_all_stocks 테스트 ---

@pytest.mark.asyncio
async def test_sell_all_stocks_market_closed(handler, mock_market_calendar_service, mock_logger):
    """sell_all_stocks 시장 마감 시 일괄 매도 실패 테스트."""
    mock_market_calendar_service.is_market_open_now.return_value = False
    result = await handler.sell_all_stocks()
    assert result.rt_cd == ErrorCode.MARKET_CLOSED.value
    mock_logger.warning.assert_called_once_with("시장이 닫혀 있어 매도 주문을 제출하지 못했습니다.")

@pytest.mark.asyncio
async def test_sell_all_stocks_balance_failed(handler, mock_broker_api_wrapper, mock_logger):
    """sell_all_stocks 잔고 조회 실패 시나리오 테스트."""
    mock_broker_api_wrapper.get_account_balance.return_value = ResCommonResponse(
        rt_cd="1", msg1="잔고 조회 에러", data=None
    )
    result = await handler.sell_all_stocks()
    assert "error" in result
    assert "잔고 조회에 실패했습니다: 잔고 조회 에러" in result["error"]
    mock_logger.error.assert_called_with("잔고 조회 실패: 잔고 조회 에러")

@pytest.mark.asyncio
async def test_sell_all_stocks_no_holdings(handler, mock_broker_api_wrapper, mock_logger):
    """sell_all_stocks 보유 주식이 없을 때의 테스트."""
    mock_broker_api_wrapper.get_account_balance.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output1": []}
    )
    result = await handler.sell_all_stocks()
    assert result.get("message") == "보유 중인 주식이 없습니다."
    assert result.get("results") == []
    mock_logger.info.assert_any_call("매도할 보유 주식이 없습니다.")

@pytest.mark.asyncio
async def test_sell_all_stocks_success(handler, mock_broker_api_wrapper, mock_logger):
    """sell_all_stocks 일괄 매도 성공 및 부분 실패 시나리오 테스트."""
    mock_broker_api_wrapper.get_account_balance.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={
            "output1": [
                {"pdno": "005930", "hldg_qty": "10"},
                {"pdno": "000660", "hldg_qty": "5"},
                {"pdno": "035420", "hldg_qty": "0"}  # 수량 0이므로 매도 대상에서 제외되어야 함
            ]
        }
    )
    # 삼성전자는 성공, SK하이닉스는 API 실패 반환을 시뮬레이션
    mock_broker_api_wrapper.place_stock_order.side_effect = [
        ResCommonResponse(rt_cd="0", msg1="매도 성공", data=None),
        ResCommonResponse(rt_cd="1", msg1="매도 실패", data=None)
    ]

    result = await handler.sell_all_stocks()

    assert result.get("message") == "일괄 매도가 완료되었습니다."
    assert len(result.get("results")) == 2

    res1 = result["results"][0]
    assert res1["stock_code"] == "005930"
    assert res1["success"] is True

    res2 = result["results"][1]
    assert res2["stock_code"] == "000660"
    assert res2["success"] is False
    assert res2["message"] == "매도 실패"

    assert mock_broker_api_wrapper.place_stock_order.await_count == 2
    mock_broker_api_wrapper.place_stock_order.assert_any_await("005930", 0, 10, is_buy=False, exchange=Exchange.KRX)
    mock_broker_api_wrapper.place_stock_order.assert_any_await("000660", 0, 5, is_buy=False, exchange=Exchange.KRX)


@pytest.mark.asyncio
async def test_handle_place_buy_order_leaves_submitted_state_until_resolved(handler, mock_broker_api_wrapper):
    mock_broker_api_wrapper.place_stock_order.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value,
        msg1="주문 성공",
        data={"ordno": "A0001"},
    )

    result = await handler.handle_place_buy_order(
        "005930", 70000, 10, finalize_immediately=False, source="strategy:test"
    )

    assert result.rt_cd == ErrorCode.SUCCESS.value
    context = handler.get_order_context("005930", is_buy=True)
    assert context is not None
    assert context.state == OrderState.SUBMITTED
    assert context.broker_order_no == "A0001"
    assert context.source == "strategy:test"

    resolved = await handler.resolve_submitted_order(
        "005930", True, exchange=Exchange.KRX, final_state=OrderState.FILLED, filled_qty=10
    )
    assert resolved is not None
    assert resolved.state == OrderState.FILLED
    assert resolved.remaining_qty == 0


@pytest.mark.asyncio
async def test_handle_place_buy_order_registers_fast_poll_window(
    handler,
    mock_broker_api_wrapper,
    mock_market_clock,
):
    now = datetime(2026, 4, 23, 10, 0, 0)
    mock_market_clock.get_current_kst_time.return_value = now
    mock_broker_api_wrapper.place_stock_order.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value,
        msg1="주문 성공",
        data={"ordno": "A0001"},
    )

    await handler.handle_place_buy_order(
        "005930", 70000, 10, finalize_immediately=False, source="strategy:test"
    )

    assert handler.get_active_order_poll_interval_sec(now) == 5
    assert handler.get_active_order_poll_interval_sec(now + timedelta(seconds=61)) == 15


@pytest.mark.asyncio
async def test_active_order_poll_interval_returns_none_when_terminal(
    handler,
    mock_broker_api_wrapper,
    mock_market_clock,
):
    now = datetime(2026, 4, 23, 10, 0, 0)
    mock_market_clock.get_current_kst_time.return_value = now
    mock_broker_api_wrapper.place_stock_order.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value,
        msg1="주문 성공",
        data={"ordno": "A0001"},
    )

    await handler.handle_place_buy_order(
        "005930", 70000, 10, finalize_immediately=False, source="strategy:test"
    )
    await handler.resolve_submitted_order(
        "005930", True, exchange=Exchange.KRX, final_state=OrderState.FILLED, filled_qty=10
    )

    assert handler.get_active_order_poll_interval_sec(now + timedelta(seconds=1)) is None


@pytest.mark.asyncio
async def test_handle_place_buy_order_blocks_duplicate_while_submitted(handler, mock_broker_api_wrapper):
    mock_broker_api_wrapper.place_stock_order.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value,
        msg1="주문 성공",
        data={"ordno": "A0001"},
    )

    first = await handler.handle_place_buy_order(
        "005930", 70000, 10, finalize_immediately=False, source="strategy:first"
    )
    second = await handler.handle_place_buy_order(
        "005930", 70000, 10, finalize_immediately=False, source="strategy:second"
    )

    assert first.rt_cd == ErrorCode.SUCCESS.value
    assert second.rt_cd == ErrorCode.RETRY_LIMIT.value
    assert "진행 중인 주문" in second.msg1
    assert mock_broker_api_wrapper.place_stock_order.await_count == 1


@pytest.mark.asyncio
async def test_handle_place_buy_order_rejects_after_retry_exhaustion(handler, mock_broker_api_wrapper):
    mock_broker_api_wrapper.place_stock_order.return_value = ResCommonResponse(
        rt_cd=ErrorCode.RETRY_LIMIT.value,
        msg1="재시도 한도 초과",
        data=None,
    )

    result = await handler.handle_place_buy_order(
        "005930", 70000, 10, finalize_immediately=False
    )

    assert result.rt_cd == ErrorCode.RETRY_LIMIT.value
    context = handler.get_order_context("005930", is_buy=True)
    assert context is not None
    assert context.state == OrderState.REJECTED
    assert context.attempt_count == handler._ORDER_MAX_RETRIES
    assert handler.has_active_order("005930") is False


@pytest.mark.asyncio
async def test_order_partial_fill_recomputes_remaining_qty(handler, mock_broker_api_wrapper):
    mock_broker_api_wrapper.place_stock_order.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value,
        msg1="주문 성공",
        data={"ordno": "A0001"},
    )
    await handler.handle_place_buy_order(
        "005930", 70000, 10, finalize_immediately=False
    )

    partial = await handler.mark_order_partial_filled("005930", True, 4)
    assert partial is not None
    assert partial.state == OrderState.PARTIAL_FILLED
    assert partial.filled_qty == 4
    assert partial.remaining_qty == 6

    filled = await handler.resolve_submitted_order(
        "005930", True, final_state=OrderState.FILLED, filled_qty=10
    )
    assert filled is not None
    assert filled.remaining_qty == 0


@pytest.mark.asyncio
async def test_next_order_allowed_after_previous_order_filled(handler, mock_broker_api_wrapper):
    mock_broker_api_wrapper.place_stock_order.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value,
        msg1="주문 성공",
        data={"ordno": "A0001"},
    )
    await handler.handle_place_buy_order(
        "005930", 70000, 10, finalize_immediately=False
    )
    await handler.resolve_submitted_order("005930", True, final_state=OrderState.FILLED)

    second = await handler.handle_place_buy_order(
        "005930", 70100, 5, finalize_immediately=False
    )

    assert second.rt_cd == ErrorCode.SUCCESS.value
    assert mock_broker_api_wrapper.place_stock_order.await_count == 2


@pytest.mark.asyncio
async def test_signing_notice_accumulates_partial_fills_to_filled(handler, mock_broker_api_wrapper):
    mock_broker_api_wrapper.place_stock_order.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value,
        msg1="주문 성공",
        data={"ordno": "A0001"},
    )
    await handler.handle_place_buy_order(
        "005930", 70000, 10, finalize_immediately=False
    )

    first = await handler.handle_signing_notice({
        "ODER_NO": "A0001",
        "STCK_SHRN_ISCD": "005930",
        "SELN_BYOV_CLS": "02",
        "CNTG_QTY": "4",
        "CNTG_UNPR": "70000",
        "STCK_CNTG_HOUR": "101500",
        "RFUS_YN": "N",
        "CNTG_YN": "2",
        "ACPT_YN": "Y",
        "ODER_QTY": "10",
    }, tr_id="H0STCNI0")
    assert first.state == OrderState.PARTIAL_FILLED
    assert first.filled_qty == 4
    assert first.remaining_qty == 6

    second = await handler.handle_signing_notice({
        "ODER_NO": "A0001",
        "STCK_SHRN_ISCD": "005930",
        "SELN_BYOV_CLS": "02",
        "CNTG_QTY": "6",
        "CNTG_UNPR": "70100",
        "STCK_CNTG_HOUR": "101700",
        "RFUS_YN": "N",
        "CNTG_YN": "2",
        "ACPT_YN": "Y",
        "ODER_QTY": "10",
    }, tr_id="H0STCNI0")
    assert second.state == OrderState.FILLED
    assert second.filled_qty == 10
    assert second.remaining_qty == 0


@pytest.mark.asyncio
async def test_signing_notice_duplicate_event_is_idempotent(handler, mock_broker_api_wrapper):
    mock_broker_api_wrapper.place_stock_order.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value,
        msg1="주문 성공",
        data={"ordno": "A0001"},
    )
    await handler.handle_place_buy_order(
        "005930", 70000, 10, finalize_immediately=False
    )
    notice = {
        "ODER_NO": "A0001",
        "STCK_SHRN_ISCD": "005930",
        "SELN_BYOV_CLS": "02",
        "CNTG_QTY": "4",
        "CNTG_UNPR": "70000",
        "STCK_CNTG_HOUR": "101500",
        "RFUS_YN": "N",
        "CNTG_YN": "2",
        "ACPT_YN": "Y",
        "ODER_QTY": "10",
    }

    await handler.handle_signing_notice(notice, tr_id="H0STCNI0")
    duplicate = await handler.handle_signing_notice(notice, tr_id="H0STCNI0")

    assert duplicate.state == OrderState.PARTIAL_FILLED
    assert duplicate.filled_qty == 4
    assert duplicate.remaining_qty == 6


@pytest.mark.asyncio
async def test_processed_execution_events_are_bounded(handler, mock_broker_api_wrapper):
    handler._processed_execution_event_limit = 2
    mock_broker_api_wrapper.place_stock_order.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value,
        msg1="주문 성공",
        data={"ordno": "A0001"},
    )
    await handler.handle_place_buy_order(
        "005930", 70000, 10, finalize_immediately=False
    )

    for event_time in ("101500", "101501", "101502"):
        await handler.handle_signing_notice({
            "ODER_NO": "A0001",
            "STCK_SHRN_ISCD": "005930",
            "SELN_BYOV_CLS": "02",
            "CNTG_QTY": "1",
            "CNTG_UNPR": "70000",
            "STCK_CNTG_HOUR": event_time,
            "RFUS_YN": "N",
            "CNTG_YN": "2",
            "ACPT_YN": "Y",
            "ODER_QTY": "10",
        }, tr_id="H0STCNI0")

    assert len(handler._processed_execution_events) == 2
    assert "websocket:H0STCNI0:A0001:101500:PARTIAL_FILLED:1:70000:None" not in handler._processed_execution_events
    assert "websocket:H0STCNI0:A0001:101501:PARTIAL_FILLED:1:70000:None" in handler._processed_execution_events
    assert "websocket:H0STCNI0:A0001:101502:PARTIAL_FILLED:1:70000:None" in handler._processed_execution_events


@pytest.mark.asyncio
async def test_late_acceptance_notice_after_partial_fill_is_noop(handler, mock_broker_api_wrapper):
    mock_broker_api_wrapper.place_stock_order.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value,
        msg1="주문 성공",
        data={"ordno": "A0001"},
    )
    await handler.handle_place_buy_order(
        "005930", 70000, 10, finalize_immediately=False
    )

    partial = await handler.handle_signing_notice({
        "ODER_NO": "A0001",
        "STCK_SHRN_ISCD": "005930",
        "SELN_BYOV_CLS": "02",
        "CNTG_QTY": "4",
        "CNTG_UNPR": "70000",
        "STCK_CNTG_HOUR": "101500",
        "RFUS_YN": "N",
        "CNTG_YN": "2",
        "ACPT_YN": "Y",
        "ODER_QTY": "10",
    }, tr_id="H0STCNI0")
    late_acceptance = await handler.handle_signing_notice({
        "ODER_NO": "A0001",
        "STCK_SHRN_ISCD": "005930",
        "SELN_BYOV_CLS": "02",
        "CNTG_QTY": "0",
        "CNTG_UNPR": "0",
        "STCK_CNTG_HOUR": "101400",
        "RFUS_YN": "N",
        "CNTG_YN": "1",
        "ACPT_YN": "Y",
        "ODER_QTY": "10",
    }, tr_id="H0STCNI0")

    assert partial.state == OrderState.PARTIAL_FILLED
    assert late_acceptance.state == OrderState.PARTIAL_FILLED
    assert late_acceptance.filled_qty == 4
    assert late_acceptance.remaining_qty == 6


@pytest.mark.asyncio
async def test_signing_notice_rejects_submitted_order(handler, mock_broker_api_wrapper):
    mock_broker_api_wrapper.place_stock_order.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value,
        msg1="주문 성공",
        data={"ordno": "A0001"},
    )
    await handler.handle_place_buy_order(
        "005930", 70000, 10, finalize_immediately=False
    )

    rejected = await handler.handle_signing_notice({
        "ODER_NO": "A0001",
        "STCK_SHRN_ISCD": "005930",
        "SELN_BYOV_CLS": "02",
        "CNTG_QTY": "0",
        "STCK_CNTG_HOUR": "101500",
        "RFUS_YN": "Y",
        "CNTG_YN": "1",
        "ACPT_YN": "N",
        "ODER_QTY": "10",
    }, tr_id="H0STCNI0")

    assert rejected.state == OrderState.REJECTED
    assert handler.has_active_order("005930") is False


@pytest.mark.asyncio
async def test_poll_active_orders_once_applies_order_query_result(handler, mock_broker_api_wrapper):
    mock_broker_api_wrapper.place_stock_order.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value,
        msg1="주문 성공",
        data={"ordno": "A0001"},
    )
    mock_broker_api_wrapper.inquire_daily_ccld.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value,
        msg1="정상",
        data={
            "output1": [
                {
                    "odno": "A0001",
                    "pdno": "005930",
                    "sll_buy_dvsn_cd": "02",
                    "ord_qty": "10",
                    "tot_ccld_qty": "10",
                    "rmn_qty": "0",
                    "avg_prvs": "70000",
                    "ord_dt": "20260423",
                    "ord_tmd": "101500",
                }
            ]
        },
    )
    await handler.handle_place_buy_order(
        "005930", 70000, 10, finalize_immediately=False
    )

    applied_count = await handler.poll_active_orders_once(
        start_date="20260423",
        end_date="20260423",
    )

    context = handler.get_order_context("005930", is_buy=True)
    assert applied_count == 1
    assert context.state == OrderState.FILLED
    assert context.filled_qty == 10
    mock_broker_api_wrapper.inquire_daily_ccld.assert_awaited_once_with(
        start_date="20260423",
        end_date="20260423",
        side_code="02",
        stock_code="005930",
        ccld_dvsn="00",
        order_no="A0001",
        exchange=Exchange.KRX,
    )


@pytest.mark.asyncio
async def test_execution_confirmed_buy_persists_virtual_trade(mock_broker_api_wrapper, mock_logger, mock_market_clock, mock_market_calendar_service):
    virtual_trade_service = AsyncMock()
    handler = OrderExecutionService(
        broker_api_wrapper=mock_broker_api_wrapper,
        logger=mock_logger,
        market_clock=mock_market_clock,
        market_calendar_service=mock_market_calendar_service,
        virtual_trade_service=virtual_trade_service,
    )
    mock_broker_api_wrapper.place_stock_order.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value,
        msg1="주문 성공",
        data={"ordno": "A0001"},
    )
    await handler.handle_place_buy_order(
        "005930", 70000, 10, source="strategy:모멘텀", finalize_immediately=False
    )

    filled = await handler.handle_signing_notice({
        "ODER_NO": "A0001",
        "STCK_SHRN_ISCD": "005930",
        "SELN_BYOV_CLS": "02",
        "CNTG_QTY": "10",
        "CNTG_UNPR": "70100",
        "STCK_CNTG_HOUR": "101500",
        "RFUS_YN": "N",
        "CNTG_YN": "2",
        "ACPT_YN": "Y",
        "ODER_QTY": "10",
    }, tr_id="H0STCNI0")

    assert filled.state == OrderState.FILLED
    assert filled.virtual_recorded_qty == 10
    virtual_trade_service.log_buy_async.assert_awaited_once_with("모멘텀", "005930", 70100, 10)


@pytest.mark.asyncio
async def test_duplicate_execution_notice_does_not_duplicate_virtual_trade(mock_broker_api_wrapper, mock_logger, mock_market_clock, mock_market_calendar_service):
    virtual_trade_service = AsyncMock()
    handler = OrderExecutionService(
        broker_api_wrapper=mock_broker_api_wrapper,
        logger=mock_logger,
        market_clock=mock_market_clock,
        market_calendar_service=mock_market_calendar_service,
        virtual_trade_service=virtual_trade_service,
    )
    mock_broker_api_wrapper.place_stock_order.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value,
        msg1="주문 성공",
        data={"ordno": "A0001"},
    )
    await handler.handle_place_buy_order(
        "005930", 70000, 10, source="strategy:모멘텀", finalize_immediately=False
    )
    notice = {
        "ODER_NO": "A0001",
        "STCK_SHRN_ISCD": "005930",
        "SELN_BYOV_CLS": "02",
        "CNTG_QTY": "10",
        "CNTG_UNPR": "70100",
        "STCK_CNTG_HOUR": "101500",
        "RFUS_YN": "N",
        "CNTG_YN": "2",
        "ACPT_YN": "Y",
        "ODER_QTY": "10",
    }

    await handler.handle_signing_notice(notice, tr_id="H0STCNI0")
    await handler.handle_signing_notice(notice, tr_id="H0STCNI0")

    virtual_trade_service.log_buy_async.assert_awaited_once_with("모멘텀", "005930", 70100, 10)


@pytest.mark.asyncio
async def test_execution_confirmed_sell_persists_strategy_virtual_trade(mock_broker_api_wrapper, mock_logger, mock_market_clock, mock_market_calendar_service):
    virtual_trade_service = AsyncMock()
    handler = OrderExecutionService(
        broker_api_wrapper=mock_broker_api_wrapper,
        logger=mock_logger,
        market_clock=mock_market_clock,
        market_calendar_service=mock_market_calendar_service,
        virtual_trade_service=virtual_trade_service,
    )
    mock_broker_api_wrapper.place_stock_order.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value,
        msg1="주문 성공",
        data={"ordno": "S0001"},
    )
    await handler.handle_place_sell_order(
        "005930", 71000, 5, source="strategy:모멘텀", finalize_immediately=False
    )

    filled = await handler.apply_execution_report(OrderExecutionReport(
        broker_order_no="S0001",
        stock_code="005930",
        side=OrderSide.SELL,
        event_state=OrderState.FILLED,
        order_qty=5,
        fill_qty=5,
        fill_price=71100,
        cumulative_filled_qty=5,
        remaining_qty=0,
        event_time="101500",
    ))

    assert filled.state == OrderState.FILLED
    assert filled.virtual_recorded_qty == 5
    virtual_trade_service.log_sell_by_strategy_async.assert_awaited_once_with("모멘텀", "005930", 71100, 5)


@pytest.mark.asyncio
async def test_partial_fill_then_cancel_persists_confirmed_partial_qty(mock_broker_api_wrapper, mock_logger, mock_market_clock, mock_market_calendar_service):
    virtual_trade_service = AsyncMock()
    handler = OrderExecutionService(
        broker_api_wrapper=mock_broker_api_wrapper,
        logger=mock_logger,
        market_clock=mock_market_clock,
        market_calendar_service=mock_market_calendar_service,
        virtual_trade_service=virtual_trade_service,
    )
    mock_broker_api_wrapper.place_stock_order.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value,
        msg1="주문 성공",
        data={"ordno": "A0001"},
    )
    await handler.handle_place_buy_order(
        "005930", 70000, 10, source="manual:수동매매", finalize_immediately=False
    )

    partial = await handler.apply_execution_report(OrderExecutionReport(
        broker_order_no="A0001",
        stock_code="005930",
        side=OrderSide.BUY,
        event_state=OrderState.PARTIAL_FILLED,
        order_qty=10,
        fill_qty=4,
        fill_price=70100,
        cumulative_filled_qty=4,
        remaining_qty=6,
        event_time="101500",
    ))
    assert partial.state == OrderState.PARTIAL_FILLED
    virtual_trade_service.log_buy_async.assert_not_awaited()

    canceled = await handler.apply_execution_report(OrderExecutionReport(
        broker_order_no="A0001",
        stock_code="005930",
        side=OrderSide.BUY,
        event_state=OrderState.CANCELED,
        fill_qty=0,
        fill_price=70200,
        cumulative_filled_qty=4,
        remaining_qty=6,
        event_time="101700",
    ))

    assert canceled.state == OrderState.CANCELED
    assert canceled.virtual_recorded_qty == 4
    virtual_trade_service.log_buy_async.assert_awaited_once_with("수동매매", "005930", 70200, 4)


def _seed_order_context(
    handler,
    *,
    state=OrderState.SUBMITTED,
    broker_order_no="A0001",
    remaining_qty=10,
    created_at=None,
    state_entered_at=None,
    last_stuck_alert_at=None,
    last_stuck_alert_level="",
):
    order_key = handler._make_order_key("005930", OrderSide.BUY, Exchange.KRX)
    return handler._set_order_context(OrderContext(
        order_key=order_key,
        stock_code="005930",
        side=OrderSide.BUY,
        state=state,
        exchange=Exchange.KRX,
        price=70000,
        qty=10,
        filled_qty=10 - remaining_qty,
        remaining_qty=remaining_qty,
        broker_order_no=broker_order_no,
        source="strategy:test",
        created_at=created_at,
        state_entered_at=state_entered_at,
        last_stuck_alert_at=last_stuck_alert_at,
        last_stuck_alert_level=last_stuck_alert_level,
    ))


@pytest.mark.asyncio
async def test_cancel_order_requests_broker_with_remaining_qty(
    mock_broker_api_wrapper,
    mock_logger,
    mock_market_clock,
    mock_market_calendar_service,
):
    handler = OrderExecutionService(
        mock_broker_api_wrapper,
        mock_logger,
        mock_market_clock,
        market_calendar_service=mock_market_calendar_service,
    )
    context = _seed_order_context(
        handler,
        state=OrderState.PARTIAL_FILLED,
        broker_order_no="A0001",
        remaining_qty=6,
    )

    result = await handler.cancel_order(broker_order_no="A0001")

    assert result.rt_cd == ErrorCode.SUCCESS.value
    mock_broker_api_wrapper.cancel_stock_order.assert_awaited_once_with(
        broker_order_no="A0001",
        order_qty=6,
        order_price=0,
        order_orgno="06010",
        order_dvsn="00",
        qty_all_ord_yn="Y",
        exchange=Exchange.KRX,
    )
    assert handler._order_states[context.order_key].state == OrderState.PARTIAL_FILLED


@pytest.mark.asyncio
async def test_cancel_order_requires_active_context(
    mock_broker_api_wrapper,
    mock_logger,
    mock_market_clock,
    mock_market_calendar_service,
):
    handler = OrderExecutionService(
        mock_broker_api_wrapper,
        mock_logger,
        mock_market_clock,
        market_calendar_service=mock_market_calendar_service,
    )

    result = await handler.cancel_order(broker_order_no="A0001")

    assert result.rt_cd == ErrorCode.INVALID_INPUT.value
    mock_broker_api_wrapper.cancel_stock_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancel_order_requires_broker_order_no(
    mock_broker_api_wrapper,
    mock_logger,
    mock_market_clock,
    mock_market_calendar_service,
):
    handler = OrderExecutionService(
        mock_broker_api_wrapper,
        mock_logger,
        mock_market_clock,
        market_calendar_service=mock_market_calendar_service,
    )
    _seed_order_context(handler, broker_order_no="", remaining_qty=10)

    result = await handler.cancel_order(stock_code="005930", is_buy=True)

    assert result.rt_cd == ErrorCode.INVALID_INPUT.value
    mock_broker_api_wrapper.cancel_stock_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancel_order_rejects_terminal_context(
    mock_broker_api_wrapper,
    mock_logger,
    mock_market_clock,
    mock_market_calendar_service,
):
    handler = OrderExecutionService(
        mock_broker_api_wrapper,
        mock_logger,
        mock_market_clock,
        market_calendar_service=mock_market_calendar_service,
    )
    _seed_order_context(handler, state=OrderState.FILLED, broker_order_no="A0001", remaining_qty=0)

    result = await handler.cancel_order(broker_order_no="A0001")

    assert result.rt_cd == ErrorCode.INVALID_INPUT.value
    mock_broker_api_wrapper.cancel_stock_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancel_order_returns_broker_failure_without_forcing_state(
    mock_broker_api_wrapper,
    mock_logger,
    mock_market_clock,
    mock_market_calendar_service,
):
    mock_broker_api_wrapper.cancel_stock_order.return_value = ResCommonResponse(
        rt_cd=ErrorCode.API_ERROR.value,
        msg1="취소 실패",
        data=None,
    )
    handler = OrderExecutionService(
        mock_broker_api_wrapper,
        mock_logger,
        mock_market_clock,
        market_calendar_service=mock_market_calendar_service,
    )
    context = _seed_order_context(handler, state=OrderState.SUBMITTED, broker_order_no="A0001", remaining_qty=10)

    result = await handler.cancel_order(broker_order_no="A0001")

    assert result.rt_cd == ErrorCode.API_ERROR.value
    assert handler._order_states[context.order_key].state == OrderState.SUBMITTED


@pytest.mark.asyncio
async def test_check_stuck_orders_once_warns_once_in_paper_mode(
    mock_broker_api_wrapper,
    mock_logger,
    mock_market_clock,
    mock_market_calendar_service,
    mock_notification_service,
):
    created_at = datetime(2026, 4, 24, 9, 0, 0)
    mock_market_clock.get_current_kst_time.return_value = created_at
    handler = OrderExecutionService(
        mock_broker_api_wrapper,
        mock_logger,
        mock_market_clock,
        notification_service=mock_notification_service,
        market_calendar_service=mock_market_calendar_service,
    )
    context = _seed_order_context(
        handler,
        state=OrderState.SUBMITTED,
        broker_order_no="A0001",
        remaining_qty=10,
        created_at=created_at,
        state_entered_at=created_at,
    )

    first_count = await handler.check_stuck_orders_once(created_at + timedelta(seconds=61))
    second_count = await handler.check_stuck_orders_once(created_at + timedelta(seconds=120))

    assert first_count == 1
    assert second_count == 0
    mock_logger.warning.assert_called_once()
    logged_message = mock_logger.warning.call_args.args[0]
    assert "order_key=" in logged_message
    assert "broker_order_no=A0001" in logged_message
    assert "age=61s" in logged_message
    mock_notification_service.emit.assert_awaited_once()
    emit_args = mock_notification_service.emit.await_args.args
    assert emit_args[0] == NotificationCategory.TRADE
    assert emit_args[1] == NotificationLevel.WARNING
    updated = handler._order_states[context.order_key]
    assert updated.last_stuck_alert_level == NotificationLevel.WARNING.value
    assert updated.last_stuck_alert_at == created_at + timedelta(seconds=61)


@pytest.mark.asyncio
async def test_check_stuck_orders_once_escalates_to_critical_in_real_mode(
    mock_broker_api_wrapper,
    mock_logger,
    mock_market_clock,
    mock_market_calendar_service,
    mock_notification_service,
):
    created_at = datetime(2026, 4, 24, 9, 0, 0)
    mock_broker_api_wrapper.env.is_paper_trading = False
    mock_market_clock.get_current_kst_time.return_value = created_at
    handler = OrderExecutionService(
        mock_broker_api_wrapper,
        mock_logger,
        mock_market_clock,
        notification_service=mock_notification_service,
        market_calendar_service=mock_market_calendar_service,
    )
    context = _seed_order_context(
        handler,
        state=OrderState.SUBMITTED,
        broker_order_no="A0001",
        remaining_qty=10,
        created_at=created_at,
        state_entered_at=created_at,
    )

    warning_count = await handler.check_stuck_orders_once(created_at + timedelta(seconds=61))
    critical_count = await handler.check_stuck_orders_once(created_at + timedelta(seconds=181))

    assert warning_count == 1
    assert critical_count == 1
    # WARNING: stuck detected(61s) + polling failure + polling ambiguous → 최소 1회
    assert mock_logger.warning.call_count >= 1
    mock_logger.critical.assert_called_once()
    emit_calls = mock_notification_service.emit.await_args_list
    assert emit_calls[0].args[1] == NotificationLevel.WARNING
    assert emit_calls[1].args[1] == NotificationLevel.CRITICAL
    updated = handler._order_states[context.order_key]
    assert updated.last_stuck_alert_level == NotificationLevel.CRITICAL.value


@pytest.mark.asyncio
async def test_state_transition_resets_stuck_order_alert_level(
    mock_broker_api_wrapper,
    mock_logger,
    mock_market_clock,
    mock_market_calendar_service,
):
    created_at = datetime(2026, 4, 24, 9, 0, 0)
    mock_market_clock.get_current_kst_time.return_value = created_at
    handler = OrderExecutionService(
        mock_broker_api_wrapper,
        mock_logger,
        mock_market_clock,
        market_calendar_service=mock_market_calendar_service,
    )
    _seed_order_context(
        handler,
        state=OrderState.SUBMITTED,
        broker_order_no="A0001",
        remaining_qty=10,
        created_at=created_at,
        state_entered_at=created_at,
        last_stuck_alert_at=created_at + timedelta(seconds=61),
        last_stuck_alert_level=NotificationLevel.WARNING.value,
    )

    mock_market_clock.get_current_kst_time.return_value = created_at + timedelta(seconds=90)
    updated = await handler.apply_execution_report(OrderExecutionReport(
        broker_order_no="A0001",
        stock_code="005930",
        side=OrderSide.BUY,
        event_state=OrderState.PARTIAL_FILLED,
        order_qty=10,
        fill_qty=4,
        fill_price=70100,
        cumulative_filled_qty=4,
        remaining_qty=6,
        event_time="090130",
    ))

    assert updated.state == OrderState.PARTIAL_FILLED
    assert updated.last_stuck_alert_level == ""
    assert updated.last_stuck_alert_at is None


# ── Kill Switch 차단 테스트 ──────────────────────────────────────────────────

@pytest.fixture
def mock_kill_switch():
    ks = AsyncMock()
    ks.check_orders_allowed = AsyncMock(return_value=(True, None))
    return ks


@pytest.fixture
def handler_with_ks(
    mock_broker_api_wrapper,
    mock_logger,
    mock_market_clock,
    mock_market_calendar_service,
    mock_notification_service,
    mock_kill_switch,
):
    return OrderExecutionService(
        broker_api_wrapper=mock_broker_api_wrapper,
        logger=mock_logger,
        market_clock=mock_market_clock,
        market_calendar_service=mock_market_calendar_service,
        notification_service=mock_notification_service,
        kill_switch_service=mock_kill_switch,
    )


@pytest.mark.asyncio
async def test_handle_place_buy_order_blocked_by_kill_switch(
    handler_with_ks, mock_kill_switch, mock_broker_api_wrapper
):
    """Kill Switch 트립 시 매수 주문이 KILL_SWITCH_BLOCKED 코드로 즉시 차단되는지 테스트."""
    mock_kill_switch.check_orders_allowed.return_value = (False, "일 손실 한도 초과")

    result = await handler_with_ks.handle_place_buy_order("005930", 70000, 10)

    assert result.rt_cd == ErrorCode.KILL_SWITCH_BLOCKED.value
    assert "일 손실 한도 초과" in result.msg1
    mock_broker_api_wrapper.place_stock_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_place_sell_order_blocked_by_kill_switch(
    handler_with_ks, mock_kill_switch, mock_broker_api_wrapper
):
    """Kill Switch 트립 시 매도 주문이 KILL_SWITCH_BLOCKED 코드로 즉시 차단되는지 테스트."""
    mock_kill_switch.check_orders_allowed.return_value = (False, "연속 손실 한도 초과")

    result = await handler_with_ks.handle_place_sell_order("005930", 70000, 10)

    assert result.rt_cd == ErrorCode.KILL_SWITCH_BLOCKED.value
    assert "연속 손실 한도 초과" in result.msg1
    mock_broker_api_wrapper.place_stock_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_place_buy_order_allowed_when_kill_switch_ok(
    handler_with_ks, mock_kill_switch, mock_broker_api_wrapper
):
    """Kill Switch 정상 상태에서는 주문이 차단되지 않고 정상 처리되는지 테스트."""
    mock_kill_switch.check_orders_allowed.return_value = (True, None)
    mock_broker_api_wrapper.place_stock_order.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="주문 성공", data={"ordno": "A0001"}
    )

    result = await handler_with_ks.handle_place_buy_order("005930", 70000, 10)

    assert result.rt_cd == ErrorCode.SUCCESS.value
    mock_broker_api_wrapper.place_stock_order.assert_awaited_once()


def test_order_execution_service_small_helpers_cover_edge_cases(handler):
    class OrderData:
        ordno = "OBJ001"

    context = OrderContext(
        order_key=handler._make_order_key("005930", OrderSide.BUY, Exchange.KRX),
        stock_code="005930",
        side=OrderSide.BUY,
        state=OrderState.SUBMITTED,
        exchange=Exchange.KRX,
        price=70000,
        qty=10,
        broker_order_no="A0001",
    )

    assert handler.has_active_order("005930") is False
    handler._set_order_context(context)
    assert handler.has_active_order("005930") is True
    assert handler._extract_broker_order_no(ResCommonResponse(rt_cd="0", msg1="OK", data=OrderData())) == "OBJ001"
    assert handler._extract_broker_order_no(ResCommonResponse(rt_cd="0", msg1="OK", data={"order_no": "DICT001"})) == "DICT001"
    assert handler._extract_broker_order_no(ResCommonResponse(rt_cd="0", msg1="OK", data="unexpected")) is None
    assert handler._strategy_name_from_source("") == ("수동매매", False)
    assert handler._strategy_name_from_source("web") == ("수동매매", False)
    assert handler._strategy_name_from_source("scanner") == ("scanner", False)
    assert handler._mark_execution_event_seen("E1") is True
    handler._processed_execution_event_limit = 0
    assert handler._mark_execution_event_seen("E2") is True
    assert handler._processed_execution_events == {}


def test_order_query_row_helpers_cover_non_dict_and_mismatch(handler):
    context = OrderContext(
        order_key=handler._make_order_key("005930", OrderSide.BUY, Exchange.KRX),
        stock_code="005930",
        side=OrderSide.BUY,
        state=OrderState.SUBMITTED,
        exchange=Exchange.KRX,
        price=70000,
        qty=10,
        broker_order_no="A0001",
    )

    assert OrderExecutionService._extract_order_query_rows([{"odno": "A0001"}, "skip"]) == [{"odno": "A0001"}]
    assert OrderExecutionService._extract_order_query_rows("bad") == []
    assert OrderExecutionService._extract_order_query_rows({"output": {"odno": "A0001"}}) == [{"odno": "A0001"}]
    assert OrderExecutionService._query_row_matches_context({"odno": "B0001", "pdno": "005930"}, context) is False
    assert OrderExecutionService._query_row_matches_context({"odno": "A0001", "pdno": "000660"}, context) is False


@pytest.mark.asyncio
async def test_apply_execution_report_ignores_invalid_or_unknown_report(handler, mock_logger):
    missing_identifiers = OrderExecutionReport(
        broker_order_no="",
        stock_code="",
        event_state=OrderState.FILLED,
        fill_qty=1,
    )
    unknown_context = OrderExecutionReport(
        broker_order_no="UNKNOWN",
        stock_code="005930",
        side=OrderSide.BUY,
        event_state=OrderState.FILLED,
        fill_qty=1,
    )

    assert await handler.apply_execution_report(missing_identifiers) is None
    assert await handler.apply_execution_report(unknown_context) is None
    assert mock_logger.warning.call_count == 2


@pytest.mark.asyncio
async def test_execution_report_without_fill_moves_pending_to_submitted(handler):
    order_key = handler._make_order_key("005930", OrderSide.BUY, Exchange.KRX)
    handler._set_order_context(OrderContext(
        order_key=order_key,
        stock_code="005930",
        side=OrderSide.BUY,
        state=OrderState.PENDING_SUBMIT,
        exchange=Exchange.KRX,
        price=70000,
        qty=10,
    ))

    updated = await handler.apply_execution_report(OrderExecutionReport(
        broker_order_no="A0001",
        stock_code="005930",
        side=OrderSide.BUY,
        event_state=OrderState.SUBMITTED,
        fill_qty=0,
        remaining_qty=10,
    ))

    assert updated.state == OrderState.SUBMITTED
    assert updated.broker_order_no == "A0001"


@pytest.mark.asyncio
async def test_manual_sell_terminal_report_uses_plain_virtual_sell_and_records_kill_switch(
    mock_broker_api_wrapper,
    mock_logger,
    mock_market_clock,
    mock_market_calendar_service,
):
    virtual_trade_service = AsyncMock()
    kill_switch = AsyncMock()
    handler = OrderExecutionService(
        broker_api_wrapper=mock_broker_api_wrapper,
        logger=mock_logger,
        market_clock=mock_market_clock,
        market_calendar_service=mock_market_calendar_service,
        virtual_trade_service=virtual_trade_service,
        kill_switch_service=kill_switch,
    )
    kill_switch.check_orders_allowed = AsyncMock(return_value=(True, None))
    mock_broker_api_wrapper.place_stock_order.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data={"ordno": "S0001"}
    )
    await handler.handle_place_sell_order(
        "005930", 70000, 3, source="manual:web", finalize_immediately=False
    )

    filled = await handler.apply_execution_report(OrderExecutionReport(
        broker_order_no="S0001",
        stock_code="005930",
        side=OrderSide.SELL,
        event_state=OrderState.FILLED,
        order_qty=3,
        fill_qty=3,
        fill_price=69900,
        cumulative_filled_qty=3,
        remaining_qty=0,
    ))

    assert filled.virtual_recorded_qty == 3
    virtual_trade_service.log_sell_async.assert_awaited_once_with("005930", 69900, 3)
    kill_switch.record_fill_event.assert_awaited_once_with(70000, 69900, "005930", 3)


@pytest.mark.asyncio
async def test_virtual_trade_persist_failure_keeps_unrecorded_context(
    mock_broker_api_wrapper,
    mock_logger,
    mock_market_clock,
    mock_market_calendar_service,
):
    virtual_trade_service = AsyncMock()
    virtual_trade_service.log_sell_async.side_effect = RuntimeError("journal locked")
    handler = OrderExecutionService(
        broker_api_wrapper=mock_broker_api_wrapper,
        logger=mock_logger,
        market_clock=mock_market_clock,
        market_calendar_service=mock_market_calendar_service,
        virtual_trade_service=virtual_trade_service,
    )
    mock_broker_api_wrapper.place_stock_order.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data={"ordno": "S0001"}
    )
    await handler.handle_place_sell_order(
        "005930", 70000, 3, source="manual:web", finalize_immediately=False
    )

    filled = await handler.apply_execution_report(OrderExecutionReport(
        broker_order_no="S0001",
        stock_code="005930",
        side=OrderSide.SELL,
        event_state=OrderState.FILLED,
        order_qty=3,
        fill_qty=3,
        fill_price=69900,
        cumulative_filled_qty=3,
        remaining_qty=0,
    ))

    assert filled.virtual_recorded_qty == 0
    mock_logger.warning.assert_called()


@pytest.mark.asyncio
async def test_cancel_order_rejects_mismatched_broker_order_no(handler, mock_broker_api_wrapper):
    _seed_order_context(
        handler,
        state=OrderState.SUBMITTED,
        broker_order_no="A0001",
        remaining_qty=10,
    )

    result = await handler.cancel_order(
        stock_code="005930",
        is_buy=True,
        broker_order_no="B0001",
    )

    assert result.rt_cd == ErrorCode.INVALID_INPUT.value
    mock_broker_api_wrapper.cancel_stock_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancel_order_rejects_context_without_remaining_qty(handler, mock_broker_api_wrapper):
    _seed_order_context(
        handler,
        state=OrderState.PARTIAL_FILLED,
        broker_order_no="A0001",
        remaining_qty=0,
    )

    result = await handler.cancel_order(broker_order_no="A0001")

    assert result.rt_cd == ErrorCode.INVALID_INPUT.value
    mock_broker_api_wrapper.cancel_stock_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancel_order_returns_unknown_error_when_broker_raises(handler, mock_broker_api_wrapper, mock_logger):
    _seed_order_context(
        handler,
        state=OrderState.SUBMITTED,
        broker_order_no="A0001",
        remaining_qty=10,
    )
    mock_broker_api_wrapper.cancel_stock_order.side_effect = RuntimeError("cancel timeout")

    result = await handler.cancel_order(broker_order_no="A0001")

    assert result.rt_cd == ErrorCode.UNKNOWN_ERROR.value
    assert "cancel timeout" in result.msg1
    mock_logger.exception.assert_called_once()


@pytest.mark.asyncio
async def test_poll_active_orders_once_returns_zero_without_contexts(handler, mock_broker_api_wrapper):
    assert await handler.poll_active_orders_once() == 0
    mock_broker_api_wrapper.inquire_daily_ccld.assert_not_awaited()


@pytest.mark.asyncio
async def test_poll_single_order_context_ignores_failed_and_unmatched_rows(
    handler,
    mock_broker_api_wrapper,
    mock_logger,
):
    context = _seed_order_context(
        handler,
        state=OrderState.SUBMITTED,
        broker_order_no="A0001",
        remaining_qty=10,
    )
    mock_broker_api_wrapper.inquire_daily_ccld.return_value = ResCommonResponse(
        rt_cd=ErrorCode.API_ERROR.value,
        msg1="query failed",
        data=None,
    )

    failed_count = await handler._poll_single_order_context(context, "20260424", "20260424")

    mock_broker_api_wrapper.inquire_daily_ccld.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value,
        msg1="OK",
        data={"output1": [{"odno": "OTHER", "pdno": "005930"}]},
    )
    unmatched_count = await handler._poll_single_order_context(context, "20260424", "20260424")

    assert failed_count == 0
    assert unmatched_count == 0
    mock_logger.warning.assert_called()


@pytest.mark.asyncio
async def test_resolve_and_mark_rejected_ignore_missing_or_terminal_context(handler):
    assert await handler.resolve_submitted_order("005930", True) is None
    assert await handler.mark_order_rejected("005930", True, error_message="missing") is None

    _seed_order_context(handler, state=OrderState.FILLED, broker_order_no="A0001", remaining_qty=0)

    assert (await handler.resolve_submitted_order("005930", True)).state == OrderState.FILLED
    assert (await handler.mark_order_rejected("005930", True, error_message="done")).state == OrderState.FILLED


@pytest.mark.asyncio
async def test_execute_order_via_broker_records_kill_switch_failure_and_exception(
    handler_with_ks,
    mock_broker_api_wrapper,
    mock_kill_switch,
):
    mock_broker_api_wrapper.place_stock_order.return_value = ResCommonResponse(
        rt_cd=ErrorCode.API_ERROR.value, msg1="API error", data=None
    )

    result = await handler_with_ks._execute_order_via_broker("005930", 70000, 10, is_buy=True)

    assert result.rt_cd == ErrorCode.API_ERROR.value
    mock_kill_switch.record_api_failure.assert_awaited_with(ErrorCode.API_ERROR.value)

    mock_broker_api_wrapper.place_stock_order.side_effect = RuntimeError("network down")

    exception_result = await handler_with_ks._execute_order_via_broker("005930", 70000, 10, is_buy=True)

    assert exception_result.rt_cd == ErrorCode.UNKNOWN_ERROR.value
    mock_kill_switch.record_api_failure.assert_awaited_with("network down")


@pytest.mark.asyncio
async def test_market_clock_fallback_blocks_orders_when_calendar_service_missing(
    mock_broker_api_wrapper,
    mock_logger,
    mock_market_clock,
):
    mock_market_clock.is_market_operating_hours.return_value = False
    handler = OrderExecutionService(
        broker_api_wrapper=mock_broker_api_wrapper,
        logger=mock_logger,
        market_clock=mock_market_clock,
        market_calendar_service=None,
    )

    buy_result = await handler.handle_place_buy_order("005930", 70000, 10)
    sell_result = await handler.handle_place_sell_order("005930", 70000, 10)
    sell_all_result = await handler.sell_all_stocks()

    assert buy_result.rt_cd == ErrorCode.MARKET_CLOSED.value
    assert sell_result.rt_cd == ErrorCode.MARKET_CLOSED.value
    assert sell_all_result.rt_cd == ErrorCode.MARKET_CLOSED.value
    mock_broker_api_wrapper.place_stock_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_successful_orders_manage_price_subscription_tasks(
    mock_broker_api_wrapper,
    mock_logger,
    mock_market_clock,
    mock_market_calendar_service,
):
    price_sub_svc = AsyncMock()
    handler = OrderExecutionService(
        broker_api_wrapper=mock_broker_api_wrapper,
        logger=mock_logger,
        market_clock=mock_market_clock,
        market_calendar_service=mock_market_calendar_service,
        price_subscription_service=price_sub_svc,
    )
    mock_broker_api_wrapper.place_stock_order.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data={"ordno": "A0001"}
    )

    with patch("asyncio.create_task") as mock_create_task:
        await handler.handle_place_buy_order("005930", 70000, 10)
        await handler.handle_place_sell_order("005930", 70000, 10)

    assert mock_create_task.call_count == 2


@pytest.mark.asyncio
async def test_order_failure_records_virtual_trade_failure(
    mock_broker_api_wrapper,
    mock_logger,
    mock_market_clock,
    mock_market_calendar_service,
):
    virtual_trade_service = AsyncMock()
    handler = OrderExecutionService(
        broker_api_wrapper=mock_broker_api_wrapper,
        logger=mock_logger,
        market_clock=mock_market_clock,
        market_calendar_service=mock_market_calendar_service,
        virtual_trade_service=virtual_trade_service,
    )
    mock_broker_api_wrapper.place_stock_order.return_value = ResCommonResponse(
        rt_cd=ErrorCode.API_ERROR.value, msg1="API rejected", data=None
    )

    await handler.handle_place_buy_order("005930", 70000, 10)
    await handler.handle_place_sell_order("005930", 70000, 10)

    virtual_trade_service.log_order_failure_async.assert_has_awaits([
        call("BUY", "005930", 70000, 10, "API rejected"),
        call("SELL", "005930", 70000, 10, "API rejected"),
    ])


@pytest.mark.asyncio
async def test_sell_all_stocks_returns_no_valid_holdings(handler, mock_broker_api_wrapper):
    mock_broker_api_wrapper.get_account_balance.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value,
        msg1="OK",
        data={"output1": [{"pdno": "005930", "hldg_qty": "0"}, {"pdno": "", "hldg_qty": "10"}]},
    )

    result = await handler.sell_all_stocks()

    assert result["results"] == []
    mock_broker_api_wrapper.place_stock_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_sell_all_stocks_captures_per_order_exception(handler, mock_broker_api_wrapper):
    mock_broker_api_wrapper.get_account_balance.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value,
        msg1="OK",
        data={"output1": [{"pdno": "005930", "hldg_qty": "10"}]},
    )

    with patch.object(handler, "handle_place_sell_order", new=AsyncMock(side_effect=RuntimeError("order boom"))):
        result = await handler.sell_all_stocks()

    assert result["results"] == [
        {"stock_code": "005930", "success": False, "message": "order boom"}
    ]


@pytest.mark.asyncio
async def test_sell_all_stocks_captures_unexpected_outer_exception(handler, mock_broker_api_wrapper, mock_logger):
    mock_broker_api_wrapper.get_account_balance.side_effect = RuntimeError("balance boom")

    result = await handler.sell_all_stocks()

    assert "balance boom" in result["error"]
    mock_logger.critical.assert_called_once()


# ── PR1: _resolve_finalize + real mode 즉시 체결 확정 제거 테스트 ─────────────

@pytest.fixture
def mock_broker_api_wrapper_real(mock_broker_api_wrapper):
    """실전 모드(is_paper_trading=False) BrokerAPIWrapper 픽스처."""
    mock_broker_api_wrapper.env.is_paper_trading = False
    return mock_broker_api_wrapper


@pytest.fixture
def handler_real(mock_broker_api_wrapper_real, mock_logger, mock_market_clock, mock_market_calendar_service):
    """실전 모드 OrderExecutionService 픽스처."""
    return OrderExecutionService(
        broker_api_wrapper=mock_broker_api_wrapper_real,
        logger=mock_logger,
        market_clock=mock_market_clock,
        market_calendar_service=mock_market_calendar_service,
    )


# _resolve_finalize

def test_resolve_finalize_paper_mode_true(handler):
    assert handler._resolve_finalize(True) is True


def test_resolve_finalize_paper_mode_false(handler):
    assert handler._resolve_finalize(False) is False


def test_resolve_finalize_real_mode_forced_false(handler_real):
    assert handler_real._resolve_finalize(True) is False


def test_resolve_finalize_real_mode_false_stays_false(handler_real):
    assert handler_real._resolve_finalize(False) is False


def test_resolve_finalize_real_mode_logs_warning_when_true_requested(handler_real, mock_logger):
    handler_real._resolve_finalize(True)
    mock_logger.warning.assert_called_once()
    assert "finalize_immediately=True" in mock_logger.warning.call_args[0][0]


def test_resolve_finalize_real_mode_no_warning_when_false_requested(handler_real, mock_logger):
    handler_real._resolve_finalize(False)
    # warning 중 finalize 관련만 없어야 한다 (다른 warning이 끼어들지 않도록 필터)
    finalize_warnings = [
        c for c in mock_logger.warning.call_args_list
        if "finalize_immediately" in str(c)
    ]
    assert len(finalize_warnings) == 0


# real mode 즉시 FILLED 제거

@pytest.mark.asyncio
async def test_real_mode_buy_order_stays_submitted_even_with_finalize_true(
    handler_real, mock_broker_api_wrapper_real
):
    mock_broker_api_wrapper_real.place_stock_order.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="주문 성공", data={"ordno": "A0001"}
    )
    result = await handler_real.handle_place_buy_order(
        "005930", 70000, 10, finalize_immediately=True
    )
    assert result.rt_cd == ErrorCode.SUCCESS.value
    ctx = handler_real.get_order_context("005930", is_buy=True)
    assert ctx.state == OrderState.SUBMITTED


@pytest.mark.asyncio
async def test_real_mode_sell_order_stays_submitted_even_with_finalize_true(
    handler_real, mock_broker_api_wrapper_real
):
    mock_broker_api_wrapper_real.place_stock_order.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="매도 성공", data={"ordno": "S0001"}
    )
    result = await handler_real.handle_place_sell_order(
        "005930", 70000, 5, finalize_immediately=True
    )
    assert result.rt_cd == ErrorCode.SUCCESS.value
    ctx = handler_real.get_order_context("005930", is_buy=False)
    assert ctx.state == OrderState.SUBMITTED


@pytest.mark.asyncio
async def test_paper_mode_buy_order_transitions_to_filled_immediately(handler, mock_broker_api_wrapper):
    mock_broker_api_wrapper.place_stock_order.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="주문 성공", data={"ordno": "A0001"}
    )
    result = await handler.handle_place_buy_order(
        "005930", 70000, 10, finalize_immediately=True
    )
    assert result.rt_cd == ErrorCode.SUCCESS.value
    ctx = handler.get_order_context("005930", is_buy=True)
    assert ctx.state == OrderState.FILLED


# ── PR1: _safe_transition_order_context 테스트 ────────────────────────────────

def test_safe_transition_noop_on_invalid_transition(handler):
    ctx = _seed_order_context(handler, state=OrderState.FILLED, broker_order_no="A0001", remaining_qty=0)

    result = handler._safe_transition_order_context(ctx.order_key, OrderState.SUBMITTED)

    assert result is not None
    assert result.state == OrderState.FILLED


def test_safe_transition_increments_mismatch_count(handler):
    ctx = _seed_order_context(handler, state=OrderState.FILLED, broker_order_no="A0001", remaining_qty=0)
    assert handler._reconcile_mismatch_count == 0

    handler._safe_transition_order_context(ctx.order_key, OrderState.SUBMITTED)

    assert handler._reconcile_mismatch_count == 1


def test_safe_transition_logs_warning_on_invalid(handler, mock_logger):
    ctx = _seed_order_context(handler, state=OrderState.REJECTED, broker_order_no="A0001", remaining_qty=0)
    mock_logger.warning.reset_mock()

    handler._safe_transition_order_context(ctx.order_key, OrderState.FILLED)

    finalize_warnings = [c for c in mock_logger.warning.call_args_list if "외부 이벤트 상태 전이 실패" in str(c)]
    assert len(finalize_warnings) == 1
    assert "no-op" in finalize_warnings[0][0][0]


def test_safe_transition_succeeds_on_valid_transition(handler):
    ctx = _seed_order_context(handler, state=OrderState.SUBMITTED, broker_order_no="A0001", remaining_qty=10)

    result = handler._safe_transition_order_context(ctx.order_key, OrderState.FILLED, filled_qty=10)

    assert result is not None
    assert result.state == OrderState.FILLED
    assert handler._reconcile_mismatch_count == 0


def test_safe_transition_noop_on_missing_order_key(handler):
    result = handler._safe_transition_order_context("nonexistent:key", OrderState.FILLED)

    assert result is None
    assert handler._reconcile_mismatch_count == 1


def test_internal_transition_raises_on_invalid(handler):
    ctx = _seed_order_context(handler, state=OrderState.FILLED, broker_order_no="A0001", remaining_qty=0)

    with pytest.raises((ValueError, Exception)):
        handler._transition_order_context(ctx.order_key, OrderState.SUBMITTED)


# ── PR2: stuck order CRITICAL 시 broker polling 상태 보정 테스트 ───────────────

@pytest.mark.asyncio
async def test_stuck_order_critical_triggers_polling(handler_real, mock_logger):
    """CRITICAL stuck order 감지 시 _poll_single_order_context 가 호출돼야 한다."""
    now = datetime(2026, 4, 24, 10, 0, 0)
    entered_at = now - timedelta(seconds=OrderExecutionService._STUCK_ORDER_CRITICAL_SEC + 10)
    _seed_order_context(handler_real, state=OrderState.SUBMITTED, broker_order_no="C0001",
                        remaining_qty=10, state_entered_at=entered_at, created_at=entered_at)

    with patch.object(handler_real, "_poll_single_order_context", new_callable=AsyncMock) as mock_poll:
        mock_poll.return_value = 0
        await handler_real.check_stuck_orders_once(now=now)

    mock_poll.assert_awaited_once()
    call_args = mock_poll.call_args
    assert call_args.args[1] == now.strftime("%Y%m%d")  # start_date
    assert call_args.args[2] == now.strftime("%Y%m%d")  # end_date


@pytest.mark.asyncio
async def test_stuck_order_polling_clear_response_transitions_state(handler_real, mock_logger):
    """polling 이 1건 적용되면 INFO 로그가 기록되고 stuck_alert 메타 업데이트는 생략된다."""
    now = datetime(2026, 4, 24, 10, 0, 0)
    entered_at = now - timedelta(seconds=OrderExecutionService._STUCK_ORDER_CRITICAL_SEC + 10)
    ctx = _seed_order_context(handler_real, state=OrderState.SUBMITTED, broker_order_no="C0002",
                              remaining_qty=10, state_entered_at=entered_at, created_at=entered_at)

    def fake_poll(context, start_date, end_date):
        # polling 이 상태를 FILLED 로 전이시킨 것처럼 시뮬레이션
        handler_real._order_states[ctx.order_key] = handler_real._order_states[ctx.order_key].model_copy(
            update={"state": OrderState.FILLED, "filled_qty": 10, "remaining_qty": 0}
        )
        return 1

    with patch.object(handler_real, "_poll_single_order_context", side_effect=fake_poll):
        count = await handler_real.check_stuck_orders_once(now=now)

    assert count == 1
    info_calls = [str(c) for c in mock_logger.info.call_args_list]
    assert any("상태 보정 완료" in s for s in info_calls)
    # terminal 상태이므로 stuck_alert_level 메타 업데이트 시도 없음 → _order_states 에 FILLED 유지
    assert handler_real._order_states[ctx.order_key].state == OrderState.FILLED


@pytest.mark.asyncio
async def test_stuck_order_polling_ambiguous_response_no_transition(handler_real, mock_logger):
    """polling 이 0건 반환하면 WARNING 로그만 기록하고 상태는 SUBMITTED 유지된다."""
    now = datetime(2026, 4, 24, 10, 0, 0)
    entered_at = now - timedelta(seconds=OrderExecutionService._STUCK_ORDER_CRITICAL_SEC + 10)
    ctx = _seed_order_context(handler_real, state=OrderState.SUBMITTED, broker_order_no="C0003",
                              remaining_qty=10, state_entered_at=entered_at, created_at=entered_at)

    with patch.object(handler_real, "_poll_single_order_context", new_callable=AsyncMock) as mock_poll:
        mock_poll.return_value = 0
        count = await handler_real.check_stuck_orders_once(now=now)

    assert count == 1
    warning_calls = [str(c) for c in mock_logger.warning.call_args_list]
    assert any("모호" in s for s in warning_calls)
    # 상태 전이 없음 — SUBMITTED 유지
    assert handler_real._order_states[ctx.order_key].state == OrderState.SUBMITTED


@pytest.mark.asyncio
async def test_stuck_order_warning_level_does_not_trigger_polling(handler_real, mock_logger):
    """WARNING 수준 stuck order 에서는 polling 을 호출하지 않는다."""
    now = datetime(2026, 4, 24, 10, 0, 0)
    # WARNING 범위: WARNING_SEC 이상 ~ CRITICAL_SEC 미만
    entered_at = now - timedelta(seconds=OrderExecutionService._STUCK_ORDER_WARNING_SEC + 5)
    _seed_order_context(handler_real, state=OrderState.SUBMITTED, broker_order_no="W0001",
                        remaining_qty=10, state_entered_at=entered_at, created_at=entered_at)

    with patch.object(handler_real, "_poll_single_order_context", new_callable=AsyncMock) as mock_poll:
        await handler_real.check_stuck_orders_once(now=now)

    mock_poll.assert_not_awaited()


@pytest.mark.asyncio
async def test_stuck_order_paper_mode_no_polling(handler, mock_logger):
    """paper mode 에서는 CRITICAL 알림이 없으므로 polling 이 호출되지 않는다."""
    now = datetime(2026, 4, 24, 10, 0, 0)
    entered_at = now - timedelta(seconds=OrderExecutionService._STUCK_ORDER_CRITICAL_SEC + 10)
    _seed_order_context(handler, state=OrderState.SUBMITTED, broker_order_no="P0001",
                        remaining_qty=10, state_entered_at=entered_at, created_at=entered_at)

    with patch.object(handler, "_poll_single_order_context", new_callable=AsyncMock) as mock_poll:
        await handler.check_stuck_orders_once(now=now)

    mock_poll.assert_not_awaited()


# ── PR4: reconcile_orders_with_broker 테스트 ────────────────────────────────

def _make_unfilled_response(order_nos: list[str]) -> ResCommonResponse:
    rows = [{"odno": ono, "pdno": "005930"} for ono in order_nos]
    return ResCommonResponse(rt_cd="0", msg1="", data={"output1": rows})


def _make_filled_response(rows: list[dict]) -> ResCommonResponse:
    return ResCommonResponse(rt_cd="0", msg1="", data={"output1": rows})


def _make_balance_response(holdings: list[dict]) -> ResCommonResponse:
    return ResCommonResponse(rt_cd="0", msg1="", data={"output1": holdings})


def _fail_response(msg="오류") -> ResCommonResponse:
    return ResCommonResponse(rt_cd="1", msg1=msg, data=None)


def _seed_context(handler, *, stock_code="005930", side=OrderSide.BUY,
                  state=OrderState.SUBMITTED, broker_order_no="O0001",
                  qty=10, filled_qty=0) -> OrderContext:
    order_key = handler._make_order_key(stock_code, side, Exchange.KRX)
    return handler._set_order_context(OrderContext(
        order_key=order_key,
        stock_code=stock_code,
        side=side,
        state=state,
        exchange=Exchange.KRX,
        price=70000,
        qty=qty,
        filled_qty=filled_qty,
        remaining_qty=qty - filled_qty,
        broker_order_no=broker_order_no,
        source="strategy:test",
    ))


@pytest.mark.asyncio
async def test_reconcile_no_active_orders_returns_zero(handler, mock_broker_api_wrapper):
    """활성 주문이 없으면 0 을 반환하고 alarm이 설정되지 않는다."""
    mock_broker_api_wrapper.inquire_unfilled_orders.return_value = _make_unfilled_response([])
    mock_broker_api_wrapper.inquire_filled_history.return_value = _make_filled_response([])
    mock_broker_api_wrapper.get_account_balance.return_value = _make_balance_response([])

    result = await handler.reconcile_orders_with_broker()

    assert result == 0
    assert not handler._reconcile_alarm


@pytest.mark.asyncio
async def test_reconcile_order_in_unfilled_list_no_mismatch(handler, mock_broker_api_wrapper):
    """broker 미체결 목록에 주문번호가 있으면 불일치가 아니다."""
    _real_mode(mock_broker_api_wrapper)
    _seed_context(handler, broker_order_no="O0001")
    mock_broker_api_wrapper.inquire_unfilled_orders.return_value = _make_unfilled_response(["O0001"])
    mock_broker_api_wrapper.inquire_filled_history.return_value = _make_filled_response([])
    mock_broker_api_wrapper.get_account_balance.return_value = _make_balance_response([])

    result = await handler.reconcile_orders_with_broker()

    assert result == 0
    assert not handler._reconcile_alarm


@pytest.mark.asyncio
async def test_reconcile_order_in_filled_history_no_mismatch(handler, mock_broker_api_wrapper):
    """broker 체결내역에 주문번호가 있으면 불일치가 아니다."""
    _real_mode(mock_broker_api_wrapper)
    _seed_context(handler, broker_order_no="O0001")
    mock_broker_api_wrapper.inquire_unfilled_orders.return_value = _make_unfilled_response([])
    mock_broker_api_wrapper.inquire_filled_history.return_value = _make_filled_response(
        [{"odno": "O0001", "tot_ccld_qty": "10"}]
    )
    mock_broker_api_wrapper.get_account_balance.return_value = _make_balance_response([])

    result = await handler.reconcile_orders_with_broker()

    assert result == 0
    assert not handler._reconcile_alarm


@pytest.mark.asyncio
async def test_reconcile_one_mismatch_sets_alarm_no_transition(handler, mock_broker_api_wrapper, mock_logger):
    """1회 불일치: _reconcile_alarm=True + WARNING, 상태 전이 없음."""
    _real_mode(mock_broker_api_wrapper)
    ctx = _seed_context(handler, broker_order_no="O0001", state=OrderState.SUBMITTED)
    mock_broker_api_wrapper.inquire_unfilled_orders.return_value = _make_unfilled_response([])
    mock_broker_api_wrapper.inquire_filled_history.return_value = _make_filled_response([])
    mock_broker_api_wrapper.get_account_balance.return_value = _make_balance_response([])

    result = await handler.reconcile_orders_with_broker()

    assert result == 1
    assert handler._reconcile_alarm is True
    assert handler._reconcile_mismatch_count == 1
    assert handler._order_states[ctx.order_key].state == OrderState.SUBMITTED
    mock_logger.warning.assert_called()


@pytest.mark.asyncio
async def test_reconcile_two_consecutive_with_evidence_marks_canceled(handler, mock_broker_api_wrapper, mock_logger):
    """2회 연속 + 명시 근거(잔고·체결 없음) → CANCELED 추정 전이."""
    _real_mode(mock_broker_api_wrapper)
    ctx = _seed_context(handler, broker_order_no="O0001", state=OrderState.SUBMITTED)
    mock_broker_api_wrapper.inquire_unfilled_orders.return_value = _make_unfilled_response([])
    mock_broker_api_wrapper.inquire_filled_history.return_value = _make_filled_response([])
    mock_broker_api_wrapper.get_account_balance.return_value = _make_balance_response([])

    await handler.reconcile_orders_with_broker()
    assert handler._order_states[ctx.order_key].state == OrderState.SUBMITTED

    await handler.reconcile_orders_with_broker()

    assert handler._order_states[ctx.order_key].state == OrderState.CANCELED
    warning_msgs = " ".join(str(c) for c in mock_logger.warning.call_args_list)
    assert "assumed=true" in warning_msgs


@pytest.mark.asyncio
async def test_reconcile_two_consecutive_buy_with_balance_no_transition(handler, mock_broker_api_wrapper):
    """2회 연속이지만 매수 종목이 잔고에 있으면 명시 근거 부족 → 전이 없음."""
    _real_mode(mock_broker_api_wrapper)
    ctx = _seed_context(handler, broker_order_no="O0001", state=OrderState.SUBMITTED)
    mock_broker_api_wrapper.inquire_unfilled_orders.return_value = _make_unfilled_response([])
    mock_broker_api_wrapper.inquire_filled_history.return_value = _make_filled_response([])
    mock_broker_api_wrapper.get_account_balance.return_value = _make_balance_response(
        [{"pdno": "005930", "hldg_qty": "10"}]
    )

    await handler.reconcile_orders_with_broker()
    await handler.reconcile_orders_with_broker()

    assert handler._order_states[ctx.order_key].state == OrderState.SUBMITTED


@pytest.mark.asyncio
async def test_reconcile_filled_without_balance_logs_critical(handler, mock_broker_api_wrapper, mock_logger):
    """내부 FILLED인데 잔고 없음 → CRITICAL 로그 + alarm."""
    _real_mode(mock_broker_api_wrapper)
    _seed_context(handler, broker_order_no="O0001", state=OrderState.FILLED,
                  qty=10, filled_qty=10)
    mock_broker_api_wrapper.inquire_unfilled_orders.return_value = _make_unfilled_response([])
    mock_broker_api_wrapper.inquire_filled_history.return_value = _make_filled_response([])
    mock_broker_api_wrapper.get_account_balance.return_value = _make_balance_response([])

    await handler.reconcile_orders_with_broker()

    assert handler._reconcile_alarm is True
    mock_logger.critical.assert_called()
    critical_msg = mock_logger.critical.call_args[0][0]
    assert "FILLED" in critical_msg and "잔고 없음" in critical_msg


@pytest.mark.asyncio
async def test_reconcile_balance_without_context_logs_info_only(handler, mock_broker_api_wrapper, mock_logger):
    """잔고에 종목이 있지만 내부 컨텍스트 없음 → INFO만, alarm 없음."""
    _real_mode(mock_broker_api_wrapper)
    mock_broker_api_wrapper.inquire_unfilled_orders.return_value = _make_unfilled_response([])
    mock_broker_api_wrapper.inquire_filled_history.return_value = _make_filled_response([])
    mock_broker_api_wrapper.get_account_balance.return_value = _make_balance_response(
        [{"pdno": "999999", "hldg_qty": "5"}]
    )

    await handler.reconcile_orders_with_broker()

    assert not handler._reconcile_alarm
    mock_logger.info.assert_called()
    info_msg = " ".join(str(c) for c in mock_logger.info.call_args_list)
    assert "외부 주문" in info_msg


@pytest.mark.asyncio
async def test_reconcile_unfilled_api_failure_returns_zero(handler, mock_broker_api_wrapper, mock_logger):
    """미체결 조회 실패 시 0 반환, alarm 없음."""
    _real_mode(mock_broker_api_wrapper)
    mock_broker_api_wrapper.inquire_unfilled_orders.return_value = _fail_response("API 오류")

    result = await handler.reconcile_orders_with_broker()

    assert result == 0
    assert not handler._reconcile_alarm
    mock_logger.warning.assert_called()


@pytest.mark.asyncio
async def test_reconcile_alarm_blocks_new_order(handler_real, mock_broker_api_wrapper):
    """_reconcile_alarm=True 면 신규 주문이 차단된다."""
    handler_real._reconcile_alarm = True

    result = await handler_real.handle_place_buy_order("005930", 70000, 1)

    assert result.rt_cd != ErrorCode.SUCCESS.value
    assert "reconcile alarm" in result.msg1
    mock_broker_api_wrapper.place_stock_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_reconcile_consecutive_counter_resets_on_match(handler, mock_broker_api_wrapper):
    """불일치 후 다음 reconcile에서 broker에 나타나면 연속 카운터가 초기화된다."""
    _real_mode(mock_broker_api_wrapper)
    ctx = _seed_context(handler, broker_order_no="O0001", state=OrderState.SUBMITTED)
    mock_broker_api_wrapper.inquire_unfilled_orders.return_value = _make_unfilled_response([])
    mock_broker_api_wrapper.inquire_filled_history.return_value = _make_filled_response([])
    mock_broker_api_wrapper.get_account_balance.return_value = _make_balance_response([])

    await handler.reconcile_orders_with_broker()
    assert handler._reconcile_consecutive_mismatch_by_key.get(ctx.order_key, 0) == 1

    mock_broker_api_wrapper.inquire_unfilled_orders.return_value = _make_unfilled_response(["O0001"])
    await handler.reconcile_orders_with_broker()

    assert handler._reconcile_consecutive_mismatch_by_key.get(ctx.order_key, 0) == 0


# ── PR5: restore_state_from_broker 테스트 ────────────────────────────────

def _restore_response(rows: list[dict]) -> ResCommonResponse:
    """restore용 full row dict 리스트로 응답 생성."""
    return ResCommonResponse(rt_cd="0", msg1="", data={"output": rows})


def _restore_row(odno: str, pdno: str, sll_buy: str = "02",
                 ord_qty: str = "10", ord_unpr: str = "50000",
                 tot_ccld_qty: str = "0") -> dict:
    return {
        "odno": odno, "pdno": pdno, "sll_buy_dvsn_cd": sll_buy,
        "ord_qty": ord_qty, "ord_unpr": ord_unpr, "tot_ccld_qty": tot_ccld_qty,
    }


def _real_mode(broker_mock):
    """broker_api_wrapper.env.is_paper_trading = False 설정."""
    mock_env = MagicMock()
    mock_env.is_paper_trading = False
    broker_mock.env = mock_env


@pytest.mark.asyncio
async def test_restore_paper_mode_skips(handler, mock_broker_api_wrapper):
    """paper mode(기본값)에서는 API를 호출하지 않고 0을 반환한다."""
    count = await handler.restore_state_from_broker()
    assert count == 0
    mock_broker_api_wrapper.inquire_unfilled_orders.assert_not_awaited()


@pytest.mark.asyncio
async def test_restore_rebuilds_submitted_from_unfilled(handler, mock_broker_api_wrapper):
    """미체결 조회 응답으로 SUBMITTED 컨텍스트가 재구성된다."""
    _real_mode(mock_broker_api_wrapper)
    row = _restore_row("ORD001", "005930", sll_buy="02", ord_qty="5", ord_unpr="70000")
    mock_broker_api_wrapper.inquire_unfilled_orders.return_value = _restore_response([row])
    mock_broker_api_wrapper.inquire_filled_history.return_value = _restore_response([])

    count = await handler.restore_state_from_broker()

    assert count == 1
    order_key = handler._make_order_key("005930", OrderSide.BUY, Exchange.KRX)
    ctx = handler._order_states.get(order_key)
    assert ctx is not None
    assert ctx.state == OrderState.SUBMITTED
    assert ctx.broker_order_no == "ORD001"
    assert ctx.source == "restored"
    assert handler._order_no_index.get("ORD001") == order_key


@pytest.mark.asyncio
async def test_restore_rebuilds_filled_from_filled_history(handler, mock_broker_api_wrapper):
    """당일 체결내역으로 FILLED 컨텍스트가 재구성된다."""
    _real_mode(mock_broker_api_wrapper)
    row = _restore_row("ORD002", "000660", sll_buy="01", ord_qty="3", ord_unpr="120000", tot_ccld_qty="3")
    mock_broker_api_wrapper.inquire_unfilled_orders.return_value = _restore_response([])
    mock_broker_api_wrapper.inquire_filled_history.return_value = _restore_response([row])

    count = await handler.restore_state_from_broker()

    assert count == 1
    order_key = handler._make_order_key("000660", OrderSide.SELL, Exchange.KRX)
    ctx = handler._order_states.get(order_key)
    assert ctx is not None
    assert ctx.state == OrderState.FILLED
    assert ctx.broker_order_no == "ORD002"
    assert ctx.filled_qty == 3
    assert ctx.source == "restored"


@pytest.mark.asyncio
async def test_restore_submitted_upgraded_to_filled(handler, mock_broker_api_wrapper):
    """미체결로 SUBMITTED 복원 후 체결내역에서 동일 주문 발견 시 FILLED로 전이."""
    _real_mode(mock_broker_api_wrapper)
    unfilled_row = _restore_row("ORD003", "035720", sll_buy="02", ord_qty="2", ord_unpr="60000")
    filled_row = _restore_row("ORD003", "035720", sll_buy="02", ord_qty="2", ord_unpr="60000", tot_ccld_qty="2")
    mock_broker_api_wrapper.inquire_unfilled_orders.return_value = _restore_response([unfilled_row])
    mock_broker_api_wrapper.inquire_filled_history.return_value = _restore_response([filled_row])

    count = await handler.restore_state_from_broker()

    order_key = handler._make_order_key("035720", OrderSide.BUY, Exchange.KRX)
    ctx = handler._order_states.get(order_key)
    assert ctx is not None
    assert ctx.state == OrderState.FILLED
    assert ctx.filled_qty == 2
    assert count == 1  # SUBMITTED 복원 1건, FILLED 전이는 count 미포함


@pytest.mark.asyncio
async def test_restore_skips_existing_context(handler, mock_broker_api_wrapper):
    """이미 _order_states에 존재하는 order_key는 복원 스킵."""
    _real_mode(mock_broker_api_wrapper)
    _seed_context(handler, stock_code="005930", state=OrderState.SUBMITTED)
    row = _restore_row("ORD999", "005930", sll_buy="02")
    mock_broker_api_wrapper.inquire_unfilled_orders.return_value = _restore_response([row])
    mock_broker_api_wrapper.inquire_filled_history.return_value = _restore_response([])

    count = await handler.restore_state_from_broker()

    assert count == 0


@pytest.mark.asyncio
async def test_restore_unfilled_api_failure_logs_warning(handler, mock_broker_api_wrapper, mock_logger):
    """미체결 조회 실패 시 warning 로그 후 체결내역 조회도 계속 진행."""
    _real_mode(mock_broker_api_wrapper)
    mock_broker_api_wrapper.inquire_unfilled_orders.return_value = _fail_response("조회 실패")
    mock_broker_api_wrapper.inquire_filled_history.return_value = _restore_response([])

    count = await handler.restore_state_from_broker()

    assert count == 0
    mock_logger.warning.assert_called()
    mock_broker_api_wrapper.inquire_filled_history.assert_awaited_once()


@pytest.mark.asyncio
async def test_restore_state_rebuilds_order_indices(handler, mock_broker_api_wrapper):
    """복원된 컨텍스트는 _order_no_index에도 정확히 등록된다."""
    _real_mode(mock_broker_api_wrapper)
    rows = [
        _restore_row("ORDB001", "035420", sll_buy="02", ord_qty="1"),
        _restore_row("ORDS001", "000270", sll_buy="01", ord_qty="1"),
    ]
    mock_broker_api_wrapper.inquire_unfilled_orders.return_value = _restore_response(rows)
    mock_broker_api_wrapper.inquire_filled_history.return_value = _restore_response([])

    count = await handler.restore_state_from_broker()

    assert count == 2
    assert "ORDB001" in handler._order_no_index
    assert "ORDS001" in handler._order_no_index
    buy_key = handler._make_order_key("035420", OrderSide.BUY, Exchange.KRX)
    sell_key = handler._make_order_key("000270", OrderSide.SELL, Exchange.KRX)
    assert handler._order_no_index["ORDB001"] == buy_key
    assert handler._order_no_index["ORDS001"] == sell_key


# ── PR6: intent_id, 중복 차단, business reject, max retry 3 테스트 ──────────────

@pytest.mark.asyncio
async def test_intent_id_auto_generated_when_missing(
    handler, mock_broker_api_wrapper, mock_market_calendar_service
):
    """intent_id 미전달 시 uuid가 자동 생성되어 OrderContext에 저장된다."""
    import uuid as _uuid
    mock_broker_api_wrapper.place_stock_order.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data={"ordno": "A0001"}
    )
    result = await handler.handle_place_buy_order("005930", 70000, 10)
    assert result.rt_cd == ErrorCode.SUCCESS.value
    order_key = handler._make_order_key("005930", OrderSide.BUY, Exchange.KRX)
    ctx = handler._order_states.get(order_key)
    assert ctx is not None
    assert ctx.intent_id
    _uuid.UUID(ctx.intent_id)  # 유효한 UUID 형식


@pytest.mark.asyncio
async def test_duplicate_intent_id_blocked(
    handler, mock_broker_api_wrapper, mock_market_calendar_service
):
    """동일 intent_id + 활성(non-terminal) 상태 주문이 있으면 두 번째 주문을 즉시 거부."""
    iid = "fixed-intent-id"
    order_key = handler._make_order_key("005930", OrderSide.BUY, Exchange.KRX)
    handler._set_order_context(OrderContext(
        order_key=order_key,
        stock_code="005930",
        side=OrderSide.BUY,
        state=OrderState.SUBMITTED,
        exchange=Exchange.KRX,
        price=70000,
        qty=10,
        intent_id=iid,
    ))
    handler._intent_index[iid] = order_key

    # 동일 intent_id로 다른 종목에 주문 시도 → 차단되어야 한다
    result = await handler.handle_place_buy_order("000660", 60000, 5, intent_id=iid)

    assert result.rt_cd == ErrorCode.API_ERROR.value
    assert "duplicate intent" in result.msg1
    mock_broker_api_wrapper.place_stock_order.assert_not_awaited()


def test_intent_index_cleaned_up_on_terminal_transition(handler):
    """terminal 상태로 전이 시 _intent_index에서 intent_id가 제거된다."""
    iid = "cleanup-intent"
    order_key = handler._make_order_key("005930", OrderSide.BUY, Exchange.KRX)
    handler._set_order_context(OrderContext(
        order_key=order_key,
        stock_code="005930",
        side=OrderSide.BUY,
        state=OrderState.SUBMITTED,
        exchange=Exchange.KRX,
        price=70000,
        qty=10,
        intent_id=iid,
    ))
    handler._intent_index[iid] = order_key

    handler._transition_order_context(order_key, OrderState.FILLED, filled_qty=10)

    assert iid not in handler._intent_index


@pytest.mark.asyncio
async def test_business_reject_no_retry_marks_rejected(
    handler, mock_broker_api_wrapper, mock_market_calendar_service
):
    """잔고부족 등 비즈니스 거부 응답은 즉시 REJECTED 전이, place_stock_order 1회 호출."""
    mock_broker_api_wrapper.place_stock_order.return_value = ResCommonResponse(
        rt_cd=ErrorCode.API_ERROR.value, msg1="잔고부족으로 주문 불가", data=None
    )
    result = await handler.handle_place_buy_order("005930", 70000, 10)

    assert result.rt_cd == ErrorCode.API_ERROR.value
    mock_broker_api_wrapper.place_stock_order.assert_awaited_once()
    order_key = handler._make_order_key("005930", OrderSide.BUY, Exchange.KRX)
    ctx = handler._order_states.get(order_key)
    assert ctx is not None
    assert ctx.state == OrderState.REJECTED


@pytest.mark.asyncio
async def test_transient_api_retried_max_3(
    handler, mock_broker_api_wrapper, mock_market_clock, mock_market_calendar_service
):
    """일시적(NETWORK_ERROR) API 오류는 최대 3회 호출 후 REJECTED."""
    mock_broker_api_wrapper.place_stock_order.return_value = ResCommonResponse(
        rt_cd=ErrorCode.NETWORK_ERROR.value, msg1="네트워크 오류", data=None
    )
    result = await handler.handle_place_buy_order("005930", 70000, 10)

    assert result.rt_cd == ErrorCode.NETWORK_ERROR.value
    assert mock_broker_api_wrapper.place_stock_order.await_count == 3
    # sleep은 retry 사이에만 호출: 1→2, 2→3 총 2회
    assert mock_market_clock.async_sleep.await_count == 2
    order_key = handler._make_order_key("005930", OrderSide.BUY, Exchange.KRX)
    ctx = handler._order_states.get(order_key)
    assert ctx is not None
    assert ctx.state == OrderState.REJECTED
