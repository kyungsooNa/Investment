"""
WebSocketWatchdogTask 단위 테스트.
프로그램매매 워치독/복원/재연결 및 태스크 라이프사이클 검증.
"""
import asyncio
import time
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from task.background.intraday.websocket_watchdog_task import WebSocketWatchdogTask
from services.market_calendar_service import MarketCalendarService
from interfaces.schedulable_task import TaskState

@pytest.fixture
def mock_deps():
    streaming_service = MagicMock()
    streaming_service.connect_websocket = AsyncMock(return_value=True)
    streaming_service.subscribe_program_trading = AsyncMock()
    streaming_service.subscribe_realtime_price = AsyncMock()
    streaming_service.disconnect_websocket = AsyncMock()
    streaming_service.broker = MagicMock()
    streaming_service.broker.disconnect_websocket = AsyncMock()
    streaming_service.broker.is_websocket_receive_alive = MagicMock(return_value=True)

    realtime_data_service = MagicMock()
    realtime_data_service.get_subscribed_codes.return_value = []
    realtime_data_service.last_data_ts = 0.0
    realtime_data_service.shutdown = AsyncMock()

    market_calendar_service = AsyncMock(spec=MarketCalendarService)
    market_calendar_service.is_market_open_now = AsyncMock(return_value=False)

    logger = MagicMock()

    return streaming_service, realtime_data_service, market_calendar_service, logger


@pytest.fixture
def watchdog_task(mock_deps):
    streaming_service, realtime_data_service, market_calendar_service, logger = mock_deps
    return WebSocketWatchdogTask(
        streaming_service=streaming_service,
        realtime_data_service=realtime_data_service,
        market_calendar_service=market_calendar_service,
        logger=logger,
    )


@pytest.mark.asyncio
async def test_restore_program_trading_success(watchdog_task, mock_deps):
    """_restore_program_trading: 모든 종목 구독 복원 성공 케이스."""
    svc = watchdog_task
    svc._realtime_callback = MagicMock()
    codes = ["005930", "000660"]

    await svc._restore_program_trading(codes)

    assert svc._streaming_service.connect_websocket.call_count == 2
    assert svc._streaming_service.subscribe_program_trading.call_count == 2
    assert svc._streaming_service.subscribe_realtime_price.call_count == 2
    svc._logger.info.assert_any_call("프로그램매매 구독 복원 완료: 2/2개 종목")


@pytest.mark.asyncio
async def test_restore_program_trading_partial_failure(watchdog_task, mock_deps):
    """_restore_program_trading: 일부 종목 복원 실패 시에도 계속 진행하는지 검증."""
    svc = watchdog_task
    svc._realtime_callback = MagicMock()

    # 005930: connect fails, 000660: subscribe fails, 035720: success
    async def connect_side_effect(callback):
        return svc._streaming_service.connect_websocket.await_count != 1
    async def subscribe_side_effect(code):
        if code == "000660": raise Exception("Subscription failed")

    svc._streaming_service.connect_websocket = AsyncMock(side_effect=connect_side_effect)
    svc._streaming_service.subscribe_program_trading = AsyncMock(side_effect=subscribe_side_effect)
    svc._streaming_service.subscribe_realtime_price = AsyncMock()

    await svc._restore_program_trading(["005930", "000660", "035720"])

    assert svc._streaming_service.connect_websocket.await_count == 3
    assert svc._streaming_service.subscribe_program_trading.await_count == 2
    svc._streaming_service.subscribe_realtime_price.assert_awaited_once_with("035720")
    svc._logger.warning.assert_any_call("프로그램매매 복원 실패 (WebSocket 연결 불가): 005930")
    svc._logger.warning.assert_any_call("복원에 실패한 구독 종목을 상태에서 제거합니다: ['005930', '000660']")
    svc._logger.error.assert_called_with("프로그램매매 복원 중 오류 (000660): Subscription failed")


@pytest.mark.asyncio
async def test_program_trading_watchdog_market_closed(watchdog_task, mock_deps):
    """_program_trading_watchdog: 장 마감 시 웹소켓 연결 종료 검증."""
    svc = watchdog_task
    svc.mcs.is_market_open_now.return_value = False
    svc._realtime_data_service.get_subscribed_codes.return_value = ["005930"]
    svc._streaming_service.broker.is_websocket_receive_alive.return_value = True
    svc._streaming_service.disconnect_websocket = AsyncMock()

    async def sleep_side_effect(seconds):
        if sleep_side_effect.counter == 0:
            sleep_side_effect.counter += 1
            return
        raise asyncio.CancelledError
    sleep_side_effect.counter = 0

    with patch("task.background.intraday.websocket_watchdog_task.asyncio.sleep", side_effect=sleep_side_effect):
        try:
            await svc._program_trading_watchdog()
        except asyncio.CancelledError:
            pass

    svc._streaming_service.disconnect_websocket.assert_awaited_once()
    svc._logger.info.assert_any_call("[워치독] 장 마감 시간이므로 웹소켓 연결을 종료합니다.")


@pytest.mark.asyncio
async def test_program_trading_watchdog_data_gap(watchdog_task, mock_deps):
    """_program_trading_watchdog: 데이터 미수신 감지 및 재연결 시도 검증."""
    svc = watchdog_task
    svc.mcs.is_market_open_now.return_value = True
    svc._realtime_data_service.get_subscribed_codes.return_value = ["005930"]
    svc._realtime_data_service.last_data_ts = time.time() - 130  # 임계값 120초 초과
    svc._streaming_service.broker.is_websocket_receive_alive.return_value = True  # Zombie 상태

    svc.force_reconnect_program_trading = AsyncMock()

    async def sleep_side_effect(seconds):
        if sleep_side_effect.counter == 0:
            sleep_side_effect.counter += 1
            return
        raise asyncio.CancelledError
    sleep_side_effect.counter = 0

    with patch("task.background.intraday.websocket_watchdog_task.asyncio.sleep", side_effect=sleep_side_effect):
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
    svc._realtime_data_service.get_subscribed_codes.return_value = ["005930", "000660"]

    await svc.force_reconnect_program_trading()

    svc._streaming_service.disconnect_websocket.assert_awaited_once()
    assert svc._streaming_service.connect_websocket.call_count == 2
    assert svc._streaming_service.subscribe_program_trading.call_count == 2
    assert svc._streaming_service.subscribe_realtime_price.call_count == 2
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

    with patch("task.background.intraday.websocket_watchdog_task.asyncio.gather", new_callable=AsyncMock):
        await svc.stop()

    mock_task1.cancel.assert_called_once()
    mock_task2.cancel.assert_not_called()
    assert len(svc._tasks) == 0
    svc._realtime_data_service.shutdown.assert_awaited_once()

def test_state_property(watchdog_task):
    """state 프로퍼티 검증."""
    assert watchdog_task.state == TaskState.IDLE

@pytest.mark.asyncio
async def test_start_and_already_running(watchdog_task, mock_deps):
    """start 메서드 호출 및 이미 실행 중일 때 조기 리턴 검증."""
    svc = watchdog_task
    svc._realtime_data_service.get_subscribed_codes.return_value = ["005930"]

    await svc.start()

    assert svc.state == TaskState.RUNNING
    svc._realtime_data_service.start_background_tasks.assert_called_once()
    assert len(svc._tasks) == 2  # _restore_program_trading, _program_trading_watchdog

    # 이미 RUNNING 상태일 때 start() 재호출 시 아무 작업도 하지 않음
    await svc.start()
    assert len(svc._tasks) == 2


@pytest.mark.asyncio
async def test_suspend_and_resume(watchdog_task):
    """suspend와 resume 메서드의 상태 전환 검증."""
    svc = watchdog_task

    # RUNNING 상태가 아닐 때 suspend 무시
    await svc.suspend()
    assert svc.state == TaskState.IDLE

    # RUNNING 상태에서 suspend -> SUSPENDED
    svc._state = TaskState.RUNNING
    await svc.suspend()
    assert svc.state == TaskState.SUSPENDED

    # SUSPENDED 상태에서 resume -> RUNNING
    await svc.resume()
    assert svc.state == TaskState.RUNNING

    # SUSPENDED 상태가 아닐 때 resume 무시
    svc._state = TaskState.IDLE
    await svc.resume()
    assert svc.state == TaskState.IDLE


@pytest.mark.asyncio
async def test_force_reconnect_program_trading_early_returns(watchdog_task):
    """데이터 서비스가 없거나 구독 종목이 없을 때 조기 리턴 검증."""
    svc = watchdog_task
    
    # 데이터 서비스가 없을 때
    svc._realtime_data_service = None
    await svc.force_reconnect_program_trading()
    svc._streaming_service.disconnect_websocket.assert_not_called()
    
    # 구독 종목이 없을 때
    svc._realtime_data_service = MagicMock()
    svc._realtime_data_service.get_subscribed_codes.return_value = []
    await svc.force_reconnect_program_trading()
    svc._streaming_service.disconnect_websocket.assert_not_called()


# ── get_progress() 테스트 ────────────────────────────────────────────────────


def test_get_progress_initial_state(watchdog_task):
    """초기 상태: market_open=None, subscribed_codes=0, data_gap_sec=None."""
    p = watchdog_task.get_progress()

    assert p["running"] is False
    assert p["subscribed_codes"] == 0
    assert p["data_gap_sec"] is None
    assert p["market_open"] is None


def test_get_progress_with_subscriptions(watchdog_task):
    """구독 종목이 있으면 subscribed_codes에 개수가 반영된다."""
    watchdog_task._realtime_data_service.get_subscribed_codes.return_value = ["005930", "000660"]

    p = watchdog_task.get_progress()

    assert p["subscribed_codes"] == 2


def test_get_progress_data_gap_calculated(watchdog_task):
    """last_data_ts가 설정되면 data_gap_sec을 계산한다."""
    watchdog_task._realtime_data_service.last_data_ts = time.time() - 30.0

    p = watchdog_task.get_progress()

    assert p["data_gap_sec"] is not None
    assert 28.0 <= p["data_gap_sec"] <= 33.0  # 30초 ± 여유


def test_get_progress_no_data_ts_returns_none_gap(watchdog_task):
    """last_data_ts=0이면 data_gap_sec은 None."""
    watchdog_task._realtime_data_service.last_data_ts = 0.0

    p = watchdog_task.get_progress()

    assert p["data_gap_sec"] is None


def test_get_progress_reflects_market_open_true(watchdog_task):
    """_market_open=True면 get_progress()에 market_open=True 반영."""
    watchdog_task._market_open = True

    assert watchdog_task.get_progress()["market_open"] is True


def test_get_progress_reflects_market_open_false(watchdog_task):
    """_market_open=False면 get_progress()에 market_open=False 반영."""
    watchdog_task._market_open = False

    assert watchdog_task.get_progress()["market_open"] is False


@pytest.mark.asyncio
async def test_program_trading_watchdog_sets_market_open_false(watchdog_task):
    """장 마감 감지 후 _market_open이 False로 설정된다."""
    svc = watchdog_task
    svc.mcs.is_market_open_now.return_value = False
    svc._realtime_data_service.get_subscribed_codes.return_value = ["005930"]
    svc._streaming_service.broker.is_websocket_receive_alive.return_value = False

    async def sleep_side_effect(seconds):
        if sleep_side_effect.counter == 0:
            sleep_side_effect.counter += 1
            return
        raise asyncio.CancelledError
    sleep_side_effect.counter = 0

    with patch("task.background.intraday.websocket_watchdog_task.asyncio.sleep", side_effect=sleep_side_effect):
        try:
            await svc._program_trading_watchdog()
        except asyncio.CancelledError:
            pass

    assert svc._market_open is False


@pytest.mark.asyncio
async def test_program_trading_watchdog_sets_market_open_true(watchdog_task):
    """장 중 감지 후 _market_open이 True로 설정된다."""
    svc = watchdog_task
    svc.mcs.is_market_open_now.return_value = True
    svc._realtime_data_service.get_subscribed_codes.return_value = ["005930"]
    svc._realtime_data_service.last_data_ts = time.time() - 1.0  # 최근 수신
    svc._streaming_service.broker.is_websocket_receive_alive.return_value = True
    svc.force_reconnect_program_trading = AsyncMock()

    async def sleep_side_effect(seconds):
        if sleep_side_effect.counter == 0:
            sleep_side_effect.counter += 1
            return
        raise asyncio.CancelledError
    sleep_side_effect.counter = 0

    with patch("task.background.intraday.websocket_watchdog_task.asyncio.sleep", side_effect=sleep_side_effect):
        try:
            await svc._program_trading_watchdog()
        except asyncio.CancelledError:
            pass

    assert svc._market_open is True
