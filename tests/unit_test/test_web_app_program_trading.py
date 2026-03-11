import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from view.web.web_app_initializer import WebAppContext

@pytest.fixture
def mock_ctx():
    # Mocking dependencies
    app_context = MagicMock()
    app_context.env = MagicMock()
    
    # [수정] WebAppContext 초기화 시 web_api 의존성 격리를 위해 추가 patch
    with patch('view.web.web_app_initializer.RealtimeDataManager') as MockRDM, \
         patch('view.web.web_app_initializer.web_api') as MockWebApi:
        ctx = WebAppContext(app_context)
        ctx.logger = MagicMock()
        ctx.stock_query_service = AsyncMock()
        ctx.trading_service = MagicMock()
        ctx.realtime_data_manager = MockRDM.return_value
        
        # Default behaviors
        ctx.realtime_data_manager.is_subscribed.return_value = False
        ctx.stock_query_service.connect_websocket.return_value = True
        ctx.stock_query_service.subscribe_program_trading.return_value = True
        ctx.stock_query_service.subscribe_realtime_price.return_value = True
        
        yield ctx

@pytest.mark.asyncio
async def test_start_program_trading_success(mock_ctx):
    """신규 구독 성공 케이스: 모든 구독 API 호출 및 상태 등록 확인"""
    code = "005930"
    result = await mock_ctx.start_program_trading(code)
    
    assert result is True
    mock_ctx.realtime_data_manager.add_subscribed_code.assert_called_once_with(code)
    mock_ctx.stock_query_service.subscribe_program_trading.assert_called_once_with(code)
    mock_ctx.stock_query_service.subscribe_realtime_price.assert_called_once_with(code)

@pytest.mark.asyncio
async def test_start_program_trading_partial_fail(mock_ctx):
    """구독 하나만 성공하고 하나는 실패 시 롤백 및 실패 반환 확인"""
    code = "005930"
    # PT 구독 성공, 가격 구독 실패 설정
    mock_ctx.stock_query_service.subscribe_program_trading.return_value = True
    mock_ctx.stock_query_service.subscribe_realtime_price.return_value = False
    
    result = await mock_ctx.start_program_trading(code)
    
    assert result is False
    mock_ctx.realtime_data_manager.add_subscribed_code.assert_not_called()
    # 성공했던 PT 구독 해지 요청(롤백) 확인
    mock_ctx.stock_query_service.unsubscribe_program_trading.assert_called_once_with(code)

@pytest.mark.asyncio
async def test_start_program_trading_already_subscribed_alive(mock_ctx):
    """이미 구독 중이고 연결 살아있으면 스킵"""
    code = "005930"
    mock_ctx.realtime_data_manager.is_subscribed.return_value = True
    mock_ctx.trading_service.is_websocket_receive_alive.return_value = True
    
    result = await mock_ctx.start_program_trading(code)
    
    assert result is True
    mock_ctx.stock_query_service.connect_websocket.assert_not_called()

@pytest.mark.asyncio
async def test_start_program_trading_dead_reconnect_success(mock_ctx):
    """구독 중이나 죽어있어서 재연결 시도 후 성공"""
    code = "005930"
    mock_ctx.realtime_data_manager.is_subscribed.return_value = True
    mock_ctx.trading_service.is_websocket_receive_alive.return_value = False
    
    # 재연결 로직은 복잡하므로 여기서는 메서드 호출 여부만 모킹
    mock_ctx._force_reconnect_program_trading = AsyncMock()
    
    result = await mock_ctx.start_program_trading(code)
    
    assert result is True
    mock_ctx._force_reconnect_program_trading.assert_called_once()

@pytest.mark.asyncio
async def test_start_program_trading_dead_reconnect_fail_retry_success(mock_ctx):
    """재연결 시도 -> 실패하여 상태 제거됨 -> 신규 구독 시도 -> 성공"""
    code = "005930"
    # 1. 처음 체크 시 True, 2. 재연결 후 체크 시 False (실패 가정)
    mock_ctx.realtime_data_manager.is_subscribed.side_effect = [True, False, False]
    mock_ctx.trading_service.is_websocket_receive_alive.return_value = False
    mock_ctx._force_reconnect_program_trading = AsyncMock()
    
    result = await mock_ctx.start_program_trading(code)
    
    assert result is True
    # 재연결 시도 후 상태가 False가 되어 아래의 신규 구독 로직(connect_websocket 등)이 호출되어야 함
    mock_ctx.stock_query_service.connect_websocket.assert_called_once()
    mock_ctx.realtime_data_manager.add_subscribed_code.assert_called_once_with(code)
