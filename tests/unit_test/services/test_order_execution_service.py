import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from io import StringIO
import builtins
from unittest.mock import call, ANY
from services.market_calendar_service import MarketCalendarService
from common.types import ResCommonResponse, ErrorCode
from services.order_execution_service import OrderExecutionService

# 테스트를 위한 MockLogger
class MockLogger:
    def __init__(self):
        self.info = MagicMock()
        self.debug = MagicMock()
        self.warning = MagicMock()
        self.error = MagicMock()
        self.critical = MagicMock()

@pytest.fixture
def mock_broker_api_wrapper():
    """BrokerAPIWrapper의 AsyncMock 인스턴스를 제공하는 픽스처."""
    mock = AsyncMock()
    # place_stock_order의 기본 반환값 설정
    mock.place_stock_order.return_value = ResCommonResponse(rt_cd="0", msg1="주문 성공", data=None)
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
    return mock

@pytest.fixture
def mock_market_calendar_service():
    """MarketCalendarService의 AsyncMock 인스턴스를 제공하는 픽스처."""
    mock = AsyncMock(spec_set=MarketCalendarService)
    mock.is_market_open_now.return_value = True # 기본값 설정
    return mock

@pytest.fixture
def handler(mock_broker_api_wrapper, mock_logger, mock_market_clock, mock_market_calendar_service):
    """TransactionHandlers 인스턴스를 제공하는 픽스처."""
    handler_instance = OrderExecutionService(
        broker_api_wrapper=mock_broker_api_wrapper,
        logger=mock_logger,
        market_clock=mock_market_clock,
        market_calendar_service=mock_market_calendar_service
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

    from common.types import Exchange
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

    from common.types import Exchange
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

    from common.types import Exchange
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

    from common.types import Exchange
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

    from common.types import Exchange
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
    assert mock_broker_api_wrapper.place_stock_order.await_count == 5


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

    from common.types import Exchange
    assert mock_broker_api_wrapper.place_stock_order.await_count == 2
    mock_broker_api_wrapper.place_stock_order.assert_any_await("005930", 0, 10, is_buy=False, exchange=Exchange.KRX)
    mock_broker_api_wrapper.place_stock_order.assert_any_await("000660", 0, 5, is_buy=False, exchange=Exchange.KRX)
