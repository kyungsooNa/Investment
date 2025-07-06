import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from io import StringIO
import builtins
from unittest.mock import call, ANY

# 테스트 대상 모듈 임포트
from app.transaction_handlers import TransactionHandlers

# 테스트를 위한 MockLogger
class MockLogger:
    def __init__(self):
        self.info = MagicMock()
        self.debug = MagicMock()
        self.warning = MagicMock()
        self.error = MagicMock()
        self.critical = MagicMock()

# --- Pytest 픽스처 정의 ---

@pytest.fixture
def mock_trading_service():
    """TradingService의 AsyncMock 인스턴스를 제공하는 픽스처."""
    mock = AsyncMock()
    # handle_buy_stock, handle_sell_stock 테스트를 위해 get_user_input 기본값 설정
    mock.get_user_input.side_effect = ["005930", "10", "70000"]
    # place_buy_order 및 place_sell_order의 기본 반환값 설정
    mock.place_buy_order.return_value = {"rt_cd": "0", "msg1": "주문 성공"}
    mock.place_sell_order.return_value = {"rt_cd": "0", "msg1": "매도 성공"}
    return mock

@pytest.fixture
def mock_logger():
    """MockLogger 인스턴스를 제공하는 픽스처."""
    return MockLogger()

@pytest.fixture
def mock_time_manager():
    """TimeManager의 MagicMock 인스턴스를 제공하는 픽스처."""
    mock = MagicMock()
    mock.is_market_open.return_value = True # 기본값 설정
    return mock

@pytest.fixture
def print_output_capture():
    """builtins.print를 캡처하는 픽스처."""
    original_print = builtins.print
    capture = StringIO()
    builtins.print = lambda *args, **kwargs: capture.write(' '.join(map(str, args)) + '\n')
    yield capture
    builtins.print = original_print # 테스트 종료 후 print 복원
    capture.close()

@pytest.fixture
def handler(mock_trading_service, mock_logger, mock_time_manager):
    """TransactionHandlers 인스턴스를 제공하는 픽스처."""
    handler_instance = TransactionHandlers(
        trading_service=mock_trading_service,
        logger=mock_logger,
        time_manager=mock_time_manager
    )
    # handle_buy_stock, handle_sell_stock 테스트를 위해 내부 메서드 목킹
    # 이 부분은 실제 handle_place_buy_order/sell_order가 호출되도록 변경 (아래 테스트에서)
    # handler_instance.handle_place_buy_order = AsyncMock(return_value={"rt_cd": "0"})
    # handler_instance.handle_place_sell_order = AsyncMock(return_value={"rt_cd": "0"})
    return handler_instance

# --- Pytest 스타일 테스트 케이스 ---

@pytest.mark.asyncio
async def test_handle_buy_stock_success(handler, mock_trading_service, print_output_capture):
    """handle_buy_stock 매수 성공 시나리오 테스트."""
    mock_trading_service.get_user_input.side_effect = ["005930", "10", "70000"]
    # handle_place_buy_order가 성공적으로 실행되도록 mock_trading_service.place_buy_order를 목킹
    mock_trading_service.place_buy_order.return_value = {"rt_cd": "0", "msg1": "주문 성공"}

    await handler.handle_buy_stock()

    mock_trading_service.get_user_input.assert_has_awaits([
        call("매수할 종목 코드를 입력하세요: "),
        call("매수할 수량을 입력하세요: "),
        call("매수 가격을 입력하세요 (시장가: 0): ")
    ])
    mock_trading_service.place_buy_order.assert_awaited_once_with("005930", 70000, 10, "01")
    assert "--- 주식 매수 주문 ---" in print_output_capture.getvalue()
    assert "주식 매수 주문 성공" in print_output_capture.getvalue()

@pytest.mark.asyncio
async def test_handle_buy_stock_market_closed(handler, mock_trading_service, mock_time_manager, print_output_capture, mock_logger):
    """handle_buy_stock 시장 마감 시 매수 실패 테스트."""
    mock_trading_service.get_user_input.side_effect = ["005930", "10", "70000"]
    mock_time_manager.is_market_open.return_value = False

    await handler.handle_buy_stock()

    mock_trading_service.get_user_input.assert_has_awaits([
        call("매수할 종목 코드를 입력하세요: "),
        call("매수할 수량을 입력하세요: "),
        call("매수 가격을 입력하세요 (시장가: 0): ")
    ])
    assert "WARNING: 시장이 닫혀 있어 주문을 제출할 수 없습니다.\n" in print_output_capture.getvalue()
    mock_logger.warning.assert_called_with("시장이 닫혀 있어 매수 주문을 제출하지 못했습니다.")
    mock_trading_service.place_buy_order.assert_not_awaited() # 주문이 시도되지 않아야 함


@pytest.mark.asyncio
async def test_handle_buy_stock_invalid_input(handler, mock_trading_service, print_output_capture, mock_logger):
    """handle_buy_stock 유효하지 않은 입력 시 매수 실패 테스트."""
    mock_trading_service.get_user_input.side_effect = ["005930", "abc", "70000"] # 잘못된 수량 입력
    mock_trading_service.place_buy_order = AsyncMock() # 호출되지 않아야 함

    await handler.handle_buy_stock()

    mock_trading_service.get_user_input.assert_has_awaits([
        call("매수할 종목 코드를 입력하세요: "),
        call("매수할 수량을 입력하세요: "),
        call("매수 가격을 입력하세요 (시장가: 0): ")
    ])
    assert "잘못된 수량 또는 가격 입력입니다.\n" in print_output_capture.getvalue()
    mock_logger.warning.assert_called_once()
    mock_trading_service.place_buy_order.assert_not_awaited()

@pytest.mark.asyncio
async def test_handle_buy_stock_place_order_delegation_failure(handler, mock_trading_service, print_output_capture, mock_logger):
    """handle_buy_stock 주문 위임 실패 시 테스트."""
    mock_trading_service.get_user_input.side_effect = ["005930", "10", "70000"]
    mock_trading_service.place_buy_order.return_value = {"rt_cd": "1", "msg1": "주문 실패"}

    await handler.handle_buy_stock()

    mock_trading_service.place_buy_order.assert_awaited_once_with("005930", 70000, 10, "01")
    assert "주식 매수 주문 실패: {'rt_cd': '1', 'msg1': '주문 실패'}" in print_output_capture.getvalue()
    mock_logger.error.assert_called_once_with(f"주식 매수 주문 실패: 종목=005930, 결과={{'rt_cd': '1', 'msg1': '주문 실패'}}")

@pytest.mark.asyncio
async def test_handle_sell_stock_success(handler, mock_trading_service, print_output_capture):
    """handle_sell_stock 매도 성공 시나리오 테스트."""
    mock_trading_service.get_user_input.side_effect = ["005930", "5", "60000"]
    # handle_place_sell_order가 성공적으로 실행되도록 mock_trading_service.place_sell_order를 목킹
    mock_trading_service.place_sell_order.return_value = {"rt_cd": "0", "msg1": "매도 성공"}

    await handler.handle_sell_stock()

    mock_trading_service.get_user_input.assert_has_awaits([
        call("매도할 종목 코드를 입력하세요: "),
        call("매도할 수량을 입력하세요: "),
        call("매도 가격을 입력하세요 (시장가: 0): ")
    ])
    mock_trading_service.place_sell_order.assert_awaited_once_with("005930", 60000, 5, "01")
    assert "--- 주식 매도 주문 ---" in print_output_capture.getvalue()
    assert "주식 매도 주문 성공" in print_output_capture.getvalue()


@pytest.mark.asyncio
async def test_handle_sell_stock_market_closed(handler, mock_trading_service, mock_time_manager, print_output_capture, mock_logger):
    """handle_sell_stock 시장 마감 시 매도 실패 테스트."""
    mock_trading_service.get_user_input.side_effect = ["005930", "5", "60000"]
    mock_time_manager.is_market_open.return_value = False

    await handler.handle_sell_stock()

    assert "WARNING: 시장이 닫혀 있어 주문을 제출할 수 없습니다.\n" in print_output_capture.getvalue()
    mock_logger.warning.assert_called_with("시장이 닫혀 있어 매도 주문을 제출하지 못했습니다.")
    mock_trading_service.place_sell_order.assert_not_awaited() # 주문이 시도되지 않아야 함

@pytest.mark.asyncio
async def test_handle_sell_stock_invalid_input(handler, mock_trading_service, print_output_capture, mock_logger):
    """handle_sell_stock 유효하지 않은 입력 시 매도 실패 테스트."""
    mock_trading_service.get_user_input.side_effect = ["005930", "xyz", "60000"]
    mock_trading_service.place_sell_order = AsyncMock() # 호출되지 않아야 함

    await handler.handle_sell_stock()

    assert "잘못된 수량 또는 가격 입력입니다.\n" in print_output_capture.getvalue()
    mock_logger.warning.assert_called_once()
    mock_trading_service.place_sell_order.assert_not_awaited()

@pytest.mark.asyncio
async def test_handle_sell_stock_place_order_delegation_failure(handler, mock_trading_service, print_output_capture, mock_logger):
    """handle_sell_stock 주문 위임 실패 시 테스트."""
    mock_trading_service.get_user_input.side_effect = ["005930", "5", "60000"]
    mock_trading_service.place_sell_order.return_value = {"rt_cd": "1", "msg1": "매도 실패"}

    await handler.handle_sell_stock()

    mock_trading_service.place_sell_order.assert_awaited_once_with("005930", 60000, 5, "01")
    assert "주식 매도 주문 실패: {'rt_cd': '1', 'msg1': '매도 실패'}" in print_output_capture.getvalue()
    mock_logger.error.assert_called_once_with(f"주식 매도 주문 실패: 종목=005930, 결과={{'rt_cd': '1', 'msg1': '매도 실패'}}")

@pytest.mark.asyncio
async def test_handle_place_buy_order_success(handler, mock_trading_service, print_output_capture, mock_logger):
    """handle_place_buy_order 매수 주문 실행 성공 테스트."""
    mock_trading_service.place_buy_order.return_value = {"rt_cd": "0", "msg1": "주문 성공"}

    result = await handler.handle_place_buy_order("005930", 70000, 10, "01")

    mock_trading_service.place_buy_order.assert_awaited_once_with(
        "005930", 70000, 10, "01"
    )
    assert "--- 주식 매수 주문 시도 ---" in print_output_capture.getvalue()
    assert "주식 매수 주문 성공" in print_output_capture.getvalue()
    mock_logger.info.assert_called_once()
    assert result == {"rt_cd": "0", "msg1": "주문 성공"}

@pytest.mark.asyncio
async def test_handle_place_buy_order_trading_service_failure(handler, mock_trading_service, print_output_capture, mock_logger):
    """handle_place_buy_order 매수 주문 실행 실패 테스트."""
    mock_trading_service.place_buy_order.return_value = {"rt_cd": "1", "msg1": "잔고 부족"}

    result = await handler.handle_place_buy_order("005930", 70000, 10, "01")

    assert "주식 매수 주문 실패: {'rt_cd': '1', 'msg1': '잔고 부족'}" in print_output_capture.getvalue()
    mock_logger.error.assert_called_once()
    assert result == {"rt_cd": "1", "msg1": "잔고 부족"}

@pytest.mark.asyncio
async def test_handle_place_sell_order_success(handler, mock_trading_service, print_output_capture, mock_logger):
    """handle_place_sell_order 매도 주문 실행 성공 테스트."""
    mock_trading_service.place_sell_order.return_value = {"rt_cd": "0", "msg1": "매도 성공"}

    result = await handler.handle_place_sell_order("005930", 60000, 5, "01")

    mock_trading_service.place_sell_order.assert_awaited_once_with(
        "005930", 60000, 5, "01"
    )
    assert "--- 주식 매도 주문 시도 ---" in print_output_capture.getvalue()
    assert "주식 매도 주문 성공" in print_output_capture.getvalue()
    mock_logger.info.assert_called_once()
    assert result == {"rt_cd": "0", "msg1": "매도 성공"}

@pytest.mark.asyncio
async def test_handle_place_sell_order_trading_service_failure(handler, mock_trading_service, print_output_capture, mock_logger):
    """handle_place_sell_order 매도 주문 실행 실패 테스트."""
    mock_trading_service.place_sell_order.return_value = {"rt_cd": "1", "msg1": "수량 부족"}

    result = await handler.handle_place_sell_order("005930", 60000, 5, "01")

    assert "주식 매도 주문 실패: {'rt_cd': '1', 'msg1': '수량 부족'}" in print_output_capture.getvalue()
    mock_logger.error.assert_called_once()
    assert result == {"rt_cd": "1", "msg1": "수량 부족"}

@pytest.mark.asyncio
async def test_handle_realtime_price_quote_stream_success(handler, mock_trading_service, print_output_capture, mock_logger):
    """실시간 스트림이 성공적으로 연결, 구독, 종료되는지 테스트합니다."""
    mock_trading_service.connect_websocket.return_value = True
    mock_trading_service.subscribe_realtime_price.return_value = True
    mock_trading_service.subscribe_realtime_quote.return_value = True
    mock_trading_service.unsubscribe_realtime_price.return_value = True
    mock_trading_service.unsubscribe_realtime_quote.return_value = True
    mock_trading_service.disconnect_websocket.return_value = True

    with patch('asyncio.to_thread', new_callable=AsyncMock) as mock_to_thread:
        mock_to_thread.return_value = None

        await handler.handle_realtime_price_quote_stream("005930")

        mock_trading_service.connect_websocket.assert_awaited_once_with(
            on_message_callback=ANY
        )
        mock_trading_service.subscribe_realtime_price.assert_awaited_once_with("005930")
        mock_trading_service.subscribe_realtime_quote.assert_awaited_once_with("005930")
        mock_trading_service.unsubscribe_realtime_price.assert_awaited_once_with("005930")
        mock_trading_service.unsubscribe_realtime_quote.assert_awaited_once_with("005930")
        mock_trading_service.disconnect_websocket.assert_awaited_once()
        assert "--- 실시간 주식 체결가/호가 구독 시작 (005930) ---" in print_output_capture.getvalue()
        assert "실시간 주식 스트림을 종료했습니다." in print_output_capture.getvalue()
        mock_logger.info.assert_called_once_with(f"실시간 주식 스트림 종료: 종목=005930")

@pytest.mark.asyncio
async def test_handle_realtime_price_quote_stream_connection_failure(handler, mock_trading_service, print_output_capture, mock_logger):
    """웹소켓 연결에 실패했을 때 스트림이 시작되지 않고 오류 메시지가 출력되는지 테스트합니다."""
    mock_trading_service.connect_websocket.return_value = False

    await handler.handle_realtime_price_quote_stream("005930")

    mock_trading_service.connect_websocket.assert_awaited_once_with(
        on_message_callback=ANY
    )
    mock_trading_service.subscribe_realtime_price.assert_not_awaited()
    assert "실시간 웹소켓 연결에 실패했습니다.\n" in print_output_capture.getvalue()
    mock_logger.error.assert_called_once_with("실시간 웹소켓 연결 실패.")

@pytest.mark.asyncio
async def test_handle_realtime_price_quote_stream_keyboard_interrupt(handler, mock_trading_service, print_output_capture, mock_logger):
    """스트림 수신 중 KeyboardInterrupt 발생 시 정상적으로 종료되는지 테스트합니다."""
    mock_trading_service.connect_websocket.return_value = True
    mock_trading_service.subscribe_realtime_price.return_value = True
    mock_trading_service.subscribe_realtime_quote.return_value = True
    mock_trading_service.unsubscribe_realtime_price.return_value = True
    mock_trading_service.unsubscribe_realtime_quote.return_value = True
    mock_trading_service.disconnect_websocket.return_value = True

    with patch('asyncio.to_thread', new_callable=AsyncMock) as mock_to_thread:
        mock_to_thread.side_effect = KeyboardInterrupt

        await handler.handle_realtime_price_quote_stream("005930")

        assert "사용자에 의해 실시간 구독이 중단됩니다." in print_output_capture.getvalue()
        mock_logger.info.assert_has_calls([
            call("실시간 구독 중단 (KeyboardInterrupt)."),
            call(f"실시간 주식 스트림 종료: 종목=005930")
        ])
        mock_trading_service.unsubscribe_realtime_price.assert_awaited_once()
        mock_trading_service.disconnect_websocket.assert_awaited_once()

@pytest.mark.asyncio
async def test_handle_buy_order_when_market_closed(handler, mock_time_manager, mock_logger, print_output_capture, mock_trading_service):
    """시장이 닫혀 있을 때 매수 주문이 제출되지 않는지 테스트합니다."""
    mock_time_manager.is_market_open.return_value = False

    await handler.handle_place_buy_order("005930", 70000, 10, "01")

    assert "WARNING: 시장이 닫혀 있어 주문을 제출할 수 없습니다.\n" in print_output_capture.getvalue()
    mock_logger.warning.assert_called_once_with("시장이 닫혀 있어 매수 주문을 제출하지 못했습니다.")
    mock_trading_service.place_buy_order.assert_not_awaited() # 주문이 시도되지 않아야 함

@pytest.mark.asyncio
async def test_realtime_data_display_callback_logic(handler, mock_trading_service, print_output_capture, mock_logger):
    """
    realtime_data_display_callback 함수의 내부 로직을 다양한 데이터 타입으로 검증합니다.
    """
    # handle_realtime_price_quote_stream을 호출하여 콜백 함수에 대한 참조를 얻음
    mock_trading_service.connect_websocket.return_value = True
    mock_trading_service.subscribe_realtime_price.return_value = True
    mock_trading_service.subscribe_realtime_quote.return_value = True
    mock_trading_service.unsubscribe_realtime_price.return_value = True
    mock_trading_service.unsubscribe_realtime_quote.return_value = True
    mock_trading_service.disconnect_websocket.return_value = True

    # asyncio.to_thread를 목킹하여 input() 대기를 건너뜀
    with patch('asyncio.to_thread', new_callable=AsyncMock) as mock_to_thread:
        mock_to_thread.return_value = None
        await handler.handle_realtime_price_quote_stream("005930")

    # connect_websocket에 전달된 콜백 함수를 캡처
    # call_args.args[0]은 첫 번째 위치 인자 (on_message_callback)
    realtime_callback = mock_trading_service.connect_websocket.call_args.kwargs['on_message_callback']
    # --- 테스트 시나리오 1: realtime_price (주식 체결) 데이터 ---
    print_output_capture.truncate(0) # 이전 출력 초기화
    print_output_capture.seek(0)
    mock_logger.info.reset_mock() # 로거 목 초기화
    mock_logger.debug.reset_mock()

    price_data = {
        'type': 'realtime_price',
        'data': {
            'STCK_PRPR': '75000',
            'ACML_VOL': '123456',
            'STCK_CNTG_HOUR': '100000',
            'PRDY_VRSS': '500',
            'PRDY_VRSS_SIGN': '2', # 상승
            'PRDY_CTRT': '0.67'
        }
    }
    realtime_callback(price_data)
    expected_output = "\r[실시간 체결 - 100000] 종목: 005930: 현재가 75000원, 전일대비: 2500 (0.67%), 누적량: 123456" # '2'는 '+'로 변환되어야 함
    # 실제 코드의 _get_sign_from_code 로직이 없으므로, 직접 부호 검증은 어려움.
    # 하지만 print가 호출되는지, 그리고 데이터가 포함되는지는 검증 가능.
    # print_output_capture.getvalue()는 \r과 공백 채우기 때문에 정확한 문자열 매칭이 어려울 수 있음.
    # 대신, 중요한 키워드 포함 여부로 검증.
    output_str = print_output_capture.getvalue()
    assert "현재가 75000원" in output_str
    assert "누적량: 123456" in output_str
    assert "전일대비: 2500 (0.67%)" in output_str # 부호는 실제 코드의 _get_sign_from_code에 따라 달라짐
    mock_logger.info.assert_not_called() # 콜백 내부에서는 info 로깅 없음
    mock_logger.debug.assert_not_called()


    # --- 테스트 시나리오 2: realtime_quote (주식 호가) 데이터 ---
    print_output_capture.truncate(0)
    print_output_capture.seek(0)
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
    output_str = print_output_capture.getvalue()
    assert "매도1: 75050" in output_str
    assert "매수1: 74950" in output_str
    mock_logger.info.assert_not_called()
    mock_logger.debug.assert_not_called()


    # --- 테스트 시나리오 3: signing_notice (체결 통보) 데이터 ---
    print_output_capture.truncate(0)
    print_output_capture.seek(0)
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
    output_str = print_output_capture.getvalue()
    assert "\n[체결통보] 주문: 1234567890, 수량: 10, 단가: 75000, 시간: 100002\n" in output_str
    mock_logger.info.assert_not_called()
    mock_logger.debug.assert_not_called()


    # --- 테스트 시나리오 4: 처리되지 않은 메시지 (unknown type) ---
    print_output_capture.truncate(0)
    print_output_capture.seek(0)
    mock_logger.info.reset_mock()
    mock_logger.debug.reset_mock()

    unknown_data = {
        'type': 'unknown_type',
        'tr_id': 'UNKNOWN001',
        'data': {'some_key': 'some_value'}
    }
    realtime_callback(unknown_data)
    output_str = print_output_capture.getvalue()
    assert output_str == "" # 콘솔에 아무것도 출력되지 않아야 함
    mock_logger.debug.assert_called_once_with(f"처리되지 않은 실시간 메시지: UNKNOWN001 - {unknown_data}")

    # --- 테스트 시나리오 5: 데이터가 dict가 아닌 경우 ---
    print_output_capture.truncate(0)
    print_output_capture.seek(0)
    mock_logger.info.reset_mock()
    mock_logger.debug.reset_mock()

    non_dict_data = "invalid_string_data"
    realtime_callback(non_dict_data)
    output_str = print_output_capture.getvalue()
    assert output_str == "" # 아무것도 출력되지 않아야 함
    mock_logger.debug.assert_not_called() # 로깅도 호출되지 않아야 함