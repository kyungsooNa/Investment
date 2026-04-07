import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from view.web.web_app_initializer import WebAppContext
from repositories.streaming_stock_repo import StreamingType


@pytest.fixture
def mock_ctx():
    """WebAppContext 픽스처 — streaming_stock_repo 기반 PT 구독 상태 관리."""
    app_context = MagicMock()
    app_context.env = MagicMock()

    with patch('view.web.web_app_initializer.ProgramTradingStreamService') as MockRDM, \
         patch('view.web.web_app_initializer.web_api'), \
         patch('view.web.web_app_initializer.StockRepository'), \
         patch('view.web.web_app_initializer.StockCodeRepository'), \
         patch('view.web.web_app_initializer.VirtualTradeService'), \
         patch('view.web.web_app_initializer.Logger'):
        ctx = WebAppContext(app_context)
        ctx.logger = MagicMock()
        ctx.stock_query_service = AsyncMock()
        ctx.streaming_service = MagicMock()
        ctx.streaming_service.connect_websocket = AsyncMock(return_value=True)
        ctx.streaming_service.subscribe_program_trading = AsyncMock(return_value=True)
        ctx.streaming_service.subscribe_realtime_price = AsyncMock(return_value=True)
        ctx.streaming_service.unsubscribe_program_trading = AsyncMock()
        ctx.streaming_service.unsubscribe_realtime_price = AsyncMock()
        ctx.broker = MagicMock()
        ctx.broker.is_websocket_receive_alive = MagicMock(return_value=True)
        ctx.program_trading_stream_service = MockRDM.return_value

        # WebSocketWatchdogTask mock
        ctx.websocket_watchdog_task = MagicMock()
        ctx.websocket_watchdog_task.force_reconnect_program_trading = AsyncMock()

        # PerformanceProfiler mock
        ctx.pm = MagicMock()
        ctx.pm.start_timer.return_value = 0.0

        # StreamingStockRepo mock — PT 구독 SSOT
        ctx.streaming_stock_repo = MagicMock()
        ctx.streaming_stock_repo.get_desired = MagicMock(return_value=set())
        ctx.streaming_stock_repo.mark_desired = AsyncMock()
        ctx.streaming_stock_repo.unmark_desired = AsyncMock()
        ctx.streaming_stock_repo.mark_active = AsyncMock()
        ctx.streaming_stock_repo.mark_inactive = AsyncMock()

        yield ctx


@pytest.mark.asyncio
async def test_start_program_trading_success(mock_ctx):
    """신규 구독 성공 케이스: 구독 API 호출 및 streaming_stock_repo 상태 등록 확인"""
    code = "005930"
    mock_ctx.streaming_stock_repo.get_desired.return_value = set()  # 미구독 상태

    result = await mock_ctx.start_program_trading(code)

    assert result is True
    mock_ctx.streaming_service.subscribe_program_trading.assert_called_once_with(code)
    mock_ctx.streaming_service.subscribe_realtime_price.assert_called_once_with(code)
    mock_ctx.streaming_stock_repo.mark_desired.assert_called_once_with(code, StreamingType.PROGRAM_TRADING)
    mock_ctx.streaming_stock_repo.mark_active.assert_called_once_with(code, StreamingType.PROGRAM_TRADING)


@pytest.mark.asyncio
async def test_start_program_trading_partial_fail(mock_ctx):
    """구독 하나만 성공하고 하나는 실패 시 롤백 및 실패 반환 확인"""
    code = "005930"
    mock_ctx.streaming_stock_repo.get_desired.return_value = set()
    mock_ctx.streaming_service.subscribe_program_trading.return_value = True
    mock_ctx.streaming_service.subscribe_realtime_price.return_value = False

    result = await mock_ctx.start_program_trading(code)

    assert result is False
    mock_ctx.streaming_stock_repo.mark_desired.assert_not_called()
    # 성공했던 PT 구독 해지 롤백 확인
    mock_ctx.streaming_service.unsubscribe_program_trading.assert_called_once_with(code)


@pytest.mark.asyncio
async def test_start_program_trading_already_subscribed_alive(mock_ctx):
    """이미 desired에 있고 연결 살아있으면 스킵"""
    code = "005930"
    mock_ctx.streaming_stock_repo.get_desired.return_value = {code}  # 이미 구독 중
    mock_ctx.broker.is_websocket_receive_alive.return_value = True

    result = await mock_ctx.start_program_trading(code)

    assert result is True
    mock_ctx.streaming_service.connect_websocket.assert_not_called()


@pytest.mark.asyncio
async def test_start_program_trading_dead_reconnect_success(mock_ctx):
    """desired에 있고 연결이 죽어있어서 재연결 시도 후 여전히 desired에 있으면 성공"""
    code = "005930"
    mock_ctx.streaming_stock_repo.get_desired.return_value = {code}
    mock_ctx.broker.is_websocket_receive_alive.return_value = False

    result = await mock_ctx.start_program_trading(code)

    assert result is True
    mock_ctx.websocket_watchdog_task.force_reconnect_program_trading.assert_called_once()


@pytest.mark.asyncio
async def test_start_program_trading_dead_reconnect_fail_retry_success(mock_ctx):
    """재연결 시도 후 desired에서 제거됨 → 신규 구독 재시도 성공"""
    code = "005930"
    mock_ctx.broker.is_websocket_receive_alive.return_value = False

    # 처음: desired에 있음 → 재연결 후: desired에서 제거됨
    mock_ctx.streaming_stock_repo.get_desired.side_effect = [
        {code},   # 1차 체크: 이미 구독 중 판단
        set(),    # 재연결 후 체크: 실패로 제거됨
    ]

    result = await mock_ctx.start_program_trading(code)

    assert result is True
    # 재연결 후 신규 구독 로직 실행 확인
    mock_ctx.streaming_service.connect_websocket.assert_called_once()
    mock_ctx.streaming_stock_repo.mark_desired.assert_called_once_with(code, StreamingType.PROGRAM_TRADING)
