import pytest
from unittest.mock import MagicMock, AsyncMock
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

@pytest.mark.asyncio
async def test_connect_disconnect_websocket(streaming_service, mock_broker):
    """WebSocket 연결 및 해제 위임 테스트"""
    await streaming_service.connect_websocket()
    mock_broker.connect_websocket.assert_awaited_once()

    await streaming_service.disconnect_websocket()
    mock_broker.disconnect_websocket.assert_awaited_once()

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
async def test_handle_program_trading_stream(streaming_service, mock_broker, mock_market_clock):
    """고수준 스트림 핸들러: 프로그램매매 스트리밍 처리 (duration 0으로 빠르게 통과)"""
    await streaming_service.handle_program_trading_stream("005930", duration=0)
    
    mock_broker.connect_websocket.assert_awaited_once()
    mock_broker.subscribe_program_trading.assert_awaited_once_with("005930")
    mock_market_clock.async_sleep.assert_awaited_once_with(0)
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

def test_dispatch_realtime_message_realtime_price(streaming_service, mock_market_data_service, capsys):
    """메시지 디스패치: 실시간 체결가 수신 시 메모리 캐시 갱신 및 콘솔 출력 확인"""
    data = {
        'type': 'realtime_price',
        'data': {
            '유가증권단축종목코드': '005930',
            '주식현재가': '70000',
            '전일대비': '1000',
            '전일대비율': '1.45',
            '전일대비부호': '2',
            '누적거래량': '1000000',
            '주식체결시간': '100000'
        }
    }
    
    streaming_service.dispatch_realtime_message(data)
    
    cached = streaming_service.get_cached_realtime_price("005930")
    assert cached is not None
    assert cached["price"] == "70000"
    assert cached["change"] == "1000"
    assert "received_at" in cached
    assert isinstance(cached["received_at"], float)

    mock_market_data_service._stock_repo.update_realtime_data.assert_called_once_with("005930", 70000.0, 1000000)
    
    captured = capsys.readouterr()
    assert "[실시간 체결 - 100000]" in captured.out
    assert "현재가 70000원" in captured.out

def test_dispatch_realtime_message_realtime_quote(streaming_service, capsys):
    """메시지 디스패치: 실시간 호가 수신 시 콘솔 출력 확인"""
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
    captured = capsys.readouterr()
    assert "[실시간 호가 - 100000]" in captured.out
    assert "매도1호가: 70100" in captured.out

def test_dispatch_realtime_message_unknown_type(streaming_service):
    """메시지 디스패치: 알 수 없는 타입의 메시지 수신 시 로깅 처리"""
    data = {
        'type': 'unknown_type',
        'tr_id': 'UNKNOWN',
        'data': {}
    }
    
    streaming_service.dispatch_realtime_message(data)
    assert streaming_service.logger.debug.call_count == 2

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