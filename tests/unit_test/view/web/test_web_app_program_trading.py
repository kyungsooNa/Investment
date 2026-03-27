import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from view.web.web_app_initializer import WebAppContext

@pytest.fixture
def mock_ctx():
    # Mocking dependencies
    app_context = MagicMock()
    app_context.env = MagicMock()

    # [수정] WebAppContext 초기화 시 외부 의존성(파일I/O, 네트워크) 격리
    # StockCodeRepository: DB 없을 때 save_stock_code_list(FinanceDataReader/pykrx) → 실제 HTTP → xdist 병렬 시 429
    with patch('view.web.web_app_initializer.ProgramTradingStreamService') as MockRDM, \
         patch('view.web.web_app_initializer.web_api') as MockWebApi, \
         patch('view.web.web_app_initializer.StockRepository') as MockMapper, \
         patch('view.web.web_app_initializer.StockCodeRepository') as MockSCR, \
         patch('view.web.web_app_initializer.VirtualTradeService') as MockVTM, \
         patch('view.web.web_app_initializer.Logger') as MockLogger:
        ctx = WebAppContext(app_context)
        ctx.logger = MagicMock()
        ctx.stock_query_service = AsyncMock()
        ctx.streaming_service = AsyncMock()
        ctx.broker = MagicMock()
        ctx.broker.is_websocket_receive_alive = MagicMock(return_value=True)
        ctx.realtime_data_service = MockRDM.return_value

        # subscription_service mock
        ctx.subscription_service = MagicMock()
        ctx.subscription_service._pt_codes = set()
        ctx.subscription_service.add_program_trading = AsyncMock(return_value=True)
        ctx.subscription_service.remove_program_trading = AsyncMock()
        ctx.subscription_service.get_program_trading_codes = MagicMock(return_value=[])

        # WebSocketWatchdogTask mock (force_reconnect 등 위임용)
        ctx.websocket_watchdog_task = MagicMock()
        ctx.websocket_watchdog_task.force_reconnect_all = AsyncMock()

        # PerformanceProfiler mock
        ctx.pm = MagicMock()
        ctx.pm.start_timer.return_value = 0.0

        # Default behaviors
        ctx.streaming_service.connect_websocket = AsyncMock(return_value=True)
        ctx.streaming_service.subscribe_program_trading = AsyncMock(return_value=True)
        ctx.streaming_service.subscribe_realtime_price = AsyncMock(return_value=True)

        yield ctx

@pytest.mark.asyncio
async def test_start_program_trading_success(mock_ctx):
    """신규 구독 성공 케이스: subscription_service.add_program_trading 호출 확인"""
    code = "005930"
    result = await mock_ctx.start_program_trading(code)

    assert result is True
    mock_ctx.subscription_service.add_program_trading.assert_awaited_once_with(code)
    mock_ctx.streaming_service.connect_websocket.assert_awaited_once()

@pytest.mark.asyncio
async def test_start_program_trading_already_subscribed_alive(mock_ctx):
    """이미 구독 중이고 연결 살아있으면 스킵"""
    code = "005930"
    mock_ctx.subscription_service._pt_codes = {code}
    mock_ctx.broker.is_websocket_receive_alive.return_value = True

    result = await mock_ctx.start_program_trading(code)

    assert result is True
    mock_ctx.streaming_service.connect_websocket.assert_not_awaited()

@pytest.mark.asyncio
async def test_start_program_trading_dead_reconnect(mock_ctx):
    """구독 중이나 수신 태스크 죽어있어서 force_reconnect_all 호출"""
    code = "005930"
    mock_ctx.subscription_service._pt_codes = {code}
    mock_ctx.broker.is_websocket_receive_alive.return_value = False

    result = await mock_ctx.start_program_trading(code)

    assert result is True
    mock_ctx.websocket_watchdog_task.force_reconnect_all.assert_awaited_once()

@pytest.mark.asyncio
async def test_start_program_trading_connect_fail(mock_ctx):
    """WebSocket 연결 실패 시 False 반환"""
    code = "005930"
    mock_ctx.streaming_service.connect_websocket = AsyncMock(return_value=False)

    result = await mock_ctx.start_program_trading(code)

    assert result is False
    mock_ctx.subscription_service.add_program_trading.assert_not_awaited()
