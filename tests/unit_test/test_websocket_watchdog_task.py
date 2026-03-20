"""
WebSocketWatchdogTask 단위 테스트.
프로그램매매 워치독/복원/재연결 및 태스크 라이프사이클 검증.
"""
import asyncio
import time
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from task.background.websocket_watchdog_task import WebSocketWatchdogTask
from core.market_calendar import MarketCalendar


@pytest.fixture
def mock_deps():
    trading_service = MagicMock()
    trading_service.disconnect_websocket = AsyncMock()
    trading_service.is_websocket_receive_alive = MagicMock(return_value=True)

    stock_query_service = MagicMock()
    stock_query_service.connect_websocket = AsyncMock(return_value=True)
    stock_query_service.subscribe_program_trading = AsyncMock()
    stock_query_service.subscribe_realtime_price = AsyncMock()
    stock_query_service.trading_service = trading_service

    realtime_data_manager = MagicMock()
    realtime_data_manager.get_subscribed_codes.return_value = []
    realtime_data_manager.last_data_ts = 0.0
    realtime_data_manager.shutdown = AsyncMock()

    market_date_manager = AsyncMock(spec=MarketCalendar)
    market_date_manager.is_market_open_now = AsyncMock(return_value=False)

    logger = MagicMock()

    return stock_query_service, trading_service, realtime_data_manager, market_date_manager, logger


@pytest.fixture
def watchdog_task(mock_deps):
    stock_query_service, trading_service, realtime_data_manager, market_date_manager, logger = mock_deps
    return WebSocketWatchdogTask(
        stock_query_service=stock_query_service,
        trading_service=trading_service,
        realtime_data_manager=realtime_data_manager,
        market_date_manager=market_date_manager,
        logger=logger,
    )


@pytest.mark.asyncio
async def test_restore_program_trading_success(watchdog_task, mock_deps):
    """_restore_program_trading: 모든 종목 구독 복원 성공 케이스."""
    svc = watchdog_task
    svc._realtime_callback = MagicMock()
    codes = ["005930", "000660"]

    await svc._restore_program_trading(codes)

    assert svc._stock_query_service.connect_websocket.call_count == 2
    assert svc._stock_query_service.subscribe_program_trading.call_count == 2
    assert svc._stock_query_service.subscribe_realtime_price.call_count == 2
    svc._logger.info.assert_any_call("프로그램매매 구독 복원 완료: 2/2개 종목")


@pytest.mark.asyncio
async def test_restore_program_trading_partial_failure(watchdog_task, mock_deps):
    """_restore_program_trading: 일부 종목 복원 실패 시에도 계속 진행하는지 검증."""
    svc = watchdog_task
    svc._realtime_callback = MagicMock()

    # 005930: connect fails, 000660: subscribe fails, 035720: success
    async def connect_side_effect(callback):
        return svc._stock_query_service.connect_websocket.await_count != 1
    async def subscribe_side_effect(code):
        if code == "000660": raise Exception("Subscription failed")

    svc._stock_query_service.connect_websocket = AsyncMock(side_effect=connect_side_effect)
    svc._stock_query_service.subscribe_program_trading = AsyncMock(side_effect=subscribe_side_effect)
    svc._stock_query_service.subscribe_realtime_price = AsyncMock()

    await svc._restore_program_trading(["005930", "000660", "035720"])

    assert svc._stock_query_service.connect_websocket.await_count == 3
    assert svc._stock_query_service.subscribe_program_trading.await_count == 2
    svc._stock_query_service.subscribe_realtime_price.assert_awaited_once_with("035720")
    svc._logger.warning.assert_any_call("프로그램매매 복원 실패 (WebSocket 연결 불가): 005930")
    svc._logger.warning.assert_any_call("복원에 실패한 구독 종목을 상태에서 제거합니다: ['005930', '000660']")
    svc._logger.error.assert_called_with("프로그램매매 복원 중 오류 (000660): Subscription failed")


@pytest.mark.asyncio
async def test_program_trading_watchdog_market_closed(watchdog_task, mock_deps):
    """_program_trading_watchdog: 장 마감 시 웹소켓 연결 종료 검증."""
    svc = watchdog_task
    svc.mdm.is_market_open_now.return_value = False
    svc._realtime_data_manager.get_subscribed_codes.return_value = ["005930"]
    svc._trading_service.is_websocket_receive_alive.return_value = True
    svc._trading_service.disconnect_websocket = AsyncMock()

    async def sleep_side_effect(seconds):
        if sleep_side_effect.counter == 0:
            sleep_side_effect.counter += 1
            return
        raise asyncio.CancelledError
    sleep_side_effect.counter = 0

    with patch("task.background.websocket_watchdog_task.asyncio.sleep", side_effect=sleep_side_effect):
        try:
            await svc._program_trading_watchdog()
        except asyncio.CancelledError:
            pass

    svc._trading_service.disconnect_websocket.assert_awaited_once()
    svc._logger.info.assert_any_call("[워치독] 장 마감 시간이므로 웹소켓 연결을 종료합니다.")


@pytest.mark.asyncio
async def test_program_trading_watchdog_data_gap(watchdog_task, mock_deps):
    """_program_trading_watchdog: 데이터 미수신 감지 및 재연결 시도 검증."""
    svc = watchdog_task
    svc.mdm.is_market_open_now.return_value = True
    svc._realtime_data_manager.get_subscribed_codes.return_value = ["005930"]
    svc._realtime_data_manager.last_data_ts = time.time() - 130  # 임계값 120초 초과
    svc._trading_service.is_websocket_receive_alive.return_value = True  # Zombie 상태

    svc.force_reconnect_program_trading = AsyncMock()

    async def sleep_side_effect(seconds):
        if sleep_side_effect.counter == 0:
            sleep_side_effect.counter += 1
            return
        raise asyncio.CancelledError
    sleep_side_effect.counter = 0

    with patch("task.background.websocket_watchdog_task.asyncio.sleep", side_effect=sleep_side_effect):
        try:
            await svc._program_trading_watchdog()
        except asyncio.CancelledError:
            pass

    svc.force_reconnect_program_trading.assert_called_once()
    args, _ = svc._logger.warning.call_args
    assert "데이터 미수신" in args[0]
    assert "재연결을 시도합니다" in args[0]


@pytest.mark.asyncio
async def test_force_reconnect_program_trading(watchdog_task, mock_deps):
    """force_reconnect_program_trading: 연결 종료 후 재구독 검증."""
    svc = watchdog_task
    svc._realtime_callback = MagicMock()
    svc._realtime_data_manager.get_subscribed_codes.return_value = ["005930", "000660"]

    await svc.force_reconnect_program_trading()

    svc._stock_query_service.trading_service.disconnect_websocket.assert_awaited_once()
    assert svc._stock_query_service.connect_websocket.call_count == 2
    assert svc._stock_query_service.subscribe_program_trading.call_count == 2
    assert svc._stock_query_service.subscribe_realtime_price.call_count == 2
    svc._logger.info.assert_any_call("[워치독] 강제 재연결 완료: 2/2개 종목")


@pytest.mark.asyncio
async def test_stop_cancels_all_tasks(watchdog_task):
    """stop이 모든 추적 중인 태스크를 취소하는지 검증."""
    svc = watchdog_task
    mock_task1 = MagicMock()
    mock_task1.done.return_value = False
    mock_task1.cancel = MagicMock()
    mock_task2 = MagicMock()
    mock_task2.done.return_value = True  # 이미 완료된 태스크
    mock_task2.cancel = MagicMock()

    svc._tasks = [mock_task1, mock_task2]

    with patch("task.background.websocket_watchdog_task.asyncio.gather", new_callable=AsyncMock):
        await svc.stop()

    mock_task1.cancel.assert_called_once()
    mock_task2.cancel.assert_not_called()
    assert len(svc._tasks) == 0
    svc._realtime_data_manager.shutdown.assert_awaited_once()
