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
