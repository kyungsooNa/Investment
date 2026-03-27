"""
WebSocketWatchdogTask 단위 테스트.
WebSocket 연결 감시/복원 및 태스크 라이프사이클 검증.
"""
import asyncio
import time
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from task.background.intraday.websocket_watchdog_task import WebSocketWatchdogTask
from services.market_calendar_service import MarketCalendarService
from interfaces.schedulable_task import TaskState


@pytest.fixture
def mock_subscription_service():
    svc = MagicMock()
    svc._pt_codes = set()
    svc._active_codes = set()
    svc.get_program_trading_codes = MagicMock(return_value=[])
    svc.has_program_trading_subscriptions = MagicMock(return_value=False)
    svc.restore_all_subscriptions = AsyncMock()
    return svc


@pytest.fixture
def mock_deps(mock_subscription_service):
    streaming_service = MagicMock()
    streaming_service.connect_websocket = AsyncMock(return_value=True)
    streaming_service.disconnect_websocket = AsyncMock()
    streaming_service.broker = MagicMock()
    streaming_service.broker.disconnect_websocket = AsyncMock()
    streaming_service.broker.is_websocket_receive_alive = MagicMock(return_value=True)
    streaming_service.broker.is_websocket_connected = MagicMock(return_value=True)
    streaming_service._latest_prices = {}

    realtime_data_service = MagicMock()
    realtime_data_service.last_data_ts = 0.0
    realtime_data_service.shutdown = AsyncMock()
    realtime_data_service.start_background_tasks = MagicMock()

    market_calendar_service = AsyncMock(spec=MarketCalendarService)
    market_calendar_service.is_market_open_now = AsyncMock(return_value=False)

    logger = MagicMock()

    return streaming_service, realtime_data_service, market_calendar_service, logger


@pytest.fixture
def watchdog_task(mock_deps, mock_subscription_service):
    streaming_service, realtime_data_service, market_calendar_service, logger = mock_deps
    return WebSocketWatchdogTask(
        streaming_service=streaming_service,
        realtime_data_service=realtime_data_service,
        subscription_service=mock_subscription_service,
        market_calendar_service=market_calendar_service,
        logger=logger,
    )


# ── 구독 복원 ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_restore_subscriptions_calls_restore_all(watchdog_task, mock_subscription_service):
    """_restore_subscriptions: connect 후 restore_all_subscriptions 호출 검증."""
    svc = watchdog_task
    svc._realtime_callback = MagicMock()

    await svc._restore_subscriptions()

    svc._streaming_service.connect_websocket.assert_awaited_once()
    mock_subscription_service.restore_all_subscriptions.assert_awaited_once()


@pytest.mark.asyncio
async def test_restore_subscriptions_skips_on_connect_fail(watchdog_task, mock_subscription_service):
    """_restore_subscriptions: WebSocket 연결 실패 시 복원 스킵."""
    svc = watchdog_task
    svc._realtime_callback = MagicMock()
    svc._streaming_service.connect_websocket = AsyncMock(return_value=False)

    await svc._restore_subscriptions()

    mock_subscription_service.restore_all_subscriptions.assert_not_awaited()


# ── WebSocket 워치독 ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_websocket_watchdog_market_closed(watchdog_task, mock_subscription_service):
    """_websocket_watchdog: 장 마감 시 웹소켓 연결 종료 검증."""
    svc = watchdog_task
    svc.mcs.is_market_open_now.return_value = False
    mock_subscription_service.has_program_trading_subscriptions.return_value = True
    mock_subscription_service._active_codes = {"005930"}
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
            await svc._websocket_watchdog()
        except asyncio.CancelledError:
            pass

    svc._streaming_service.disconnect_websocket.assert_awaited_once()
    svc._logger.info.assert_any_call("[워치독] 장 마감 시간이므로 웹소켓 연결을 종료합니다.")


@pytest.mark.asyncio
async def test_websocket_watchdog_data_gap(watchdog_task, mock_subscription_service):
    """_websocket_watchdog: PT 데이터 미수신 감지 및 재연결 시도 검증."""
    svc = watchdog_task
    svc.mcs.is_market_open_now.return_value = True
    mock_subscription_service.has_program_trading_subscriptions.return_value = True
    mock_subscription_service._active_codes = set()
    svc._realtime_data_service.last_data_ts = time.time() - 130  # 임계값 120초 초과
    svc._streaming_service.broker.is_websocket_receive_alive.return_value = True
    svc._streaming_service.broker.is_websocket_connected.return_value = True

    svc.force_reconnect_all = AsyncMock()

    async def sleep_side_effect(seconds):
        if sleep_side_effect.counter == 0:
            sleep_side_effect.counter += 1
            return
        raise asyncio.CancelledError
    sleep_side_effect.counter = 0

    with patch("task.background.intraday.websocket_watchdog_task.asyncio.sleep", side_effect=sleep_side_effect):
        try:
            await svc._websocket_watchdog()
        except asyncio.CancelledError:
            pass

    svc.force_reconnect_all.assert_called_once()
    args, _ = svc._logger.warning.call_args
    assert "데이터 미수신" in args[0]
    assert "재연결을 시도합니다" in args[0]


@pytest.mark.asyncio
async def test_websocket_watchdog_sets_market_open_false(watchdog_task, mock_subscription_service):
    """장 마감 감지 후 _market_open이 False로 설정된다."""
    svc = watchdog_task
    svc.mcs.is_market_open_now.return_value = False
    mock_subscription_service.has_program_trading_subscriptions.return_value = True
    mock_subscription_service._active_codes = {"005930"}
    svc._streaming_service.broker.is_websocket_receive_alive.return_value = False

    async def sleep_side_effect(seconds):
        if sleep_side_effect.counter == 0:
            sleep_side_effect.counter += 1
            return
        raise asyncio.CancelledError
    sleep_side_effect.counter = 0

    with patch("task.background.intraday.websocket_watchdog_task.asyncio.sleep", side_effect=sleep_side_effect):
        try:
            await svc._websocket_watchdog()
        except asyncio.CancelledError:
            pass

    assert svc._market_open is False


@pytest.mark.asyncio
async def test_websocket_watchdog_sets_market_open_true(watchdog_task, mock_subscription_service):
    """장 중 감지 후 _market_open이 True로 설정된다."""
    svc = watchdog_task
    svc.mcs.is_market_open_now.return_value = True
    mock_subscription_service.has_program_trading_subscriptions.return_value = True
    mock_subscription_service._active_codes = set()
    svc._realtime_data_service.last_data_ts = time.time() - 1.0  # 최근 수신
    svc._streaming_service.broker.is_websocket_receive_alive.return_value = True
    svc._streaming_service.broker.is_websocket_connected.return_value = True
    svc.force_reconnect_all = AsyncMock()

    async def sleep_side_effect(seconds):
        if sleep_side_effect.counter == 0:
            sleep_side_effect.counter += 1
            return
        raise asyncio.CancelledError
    sleep_side_effect.counter = 0

    with patch("task.background.intraday.websocket_watchdog_task.asyncio.sleep", side_effect=sleep_side_effect):
        try:
            await svc._websocket_watchdog()
        except asyncio.CancelledError:
            pass

    assert svc._market_open is True


# ── force_reconnect_all ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_force_reconnect_all_success(watchdog_task, mock_subscription_service):
    """force_reconnect_all: 연결 종료 후 재연결 + restore_all_subscriptions 호출 검증."""
    svc = watchdog_task
    svc._realtime_callback = MagicMock()
    mock_subscription_service.has_program_trading_subscriptions.return_value = True
    mock_subscription_service.get_program_trading_codes.return_value = ["005930"]

    await svc.force_reconnect_all()

    svc._streaming_service.disconnect_websocket.assert_awaited_once()
    svc._streaming_service.connect_websocket.assert_awaited_once()
    mock_subscription_service.restore_all_subscriptions.assert_awaited_once()


@pytest.mark.asyncio
async def test_force_reconnect_all_early_return_no_subscriptions(watchdog_task, mock_subscription_service):
    """구독 종목 없을 때 force_reconnect_all은 조기 리턴해야 한다."""
    svc = watchdog_task
    mock_subscription_service.has_program_trading_subscriptions.return_value = False
    mock_subscription_service._active_codes = set()

    await svc.force_reconnect_all()

    svc._streaming_service.disconnect_websocket.assert_not_awaited()


@pytest.mark.asyncio
async def test_force_reconnect_all_connect_fail(watchdog_task, mock_subscription_service):
    """재연결 실패 시 restore_all_subscriptions를 호출하지 않아야 한다."""
    svc = watchdog_task
    svc._realtime_callback = MagicMock()
    mock_subscription_service.has_program_trading_subscriptions.return_value = True
    mock_subscription_service.get_program_trading_codes.return_value = ["005930"]
    svc._streaming_service.connect_websocket = AsyncMock(return_value=False)

    await svc.force_reconnect_all()

    mock_subscription_service.restore_all_subscriptions.assert_not_awaited()


@pytest.mark.asyncio
async def test_force_reconnect_all_disconnect_error_ignored(watchdog_task, mock_subscription_service):
    """disconnect 오류는 무시하고 재연결을 계속 시도해야 한다."""
    svc = watchdog_task
    svc._realtime_callback = MagicMock()
    mock_subscription_service.has_program_trading_subscriptions.return_value = True
    mock_subscription_service.get_program_trading_codes.return_value = ["005930"]
    svc._streaming_service.disconnect_websocket = AsyncMock(side_effect=Exception("Disconnect Error"))

    await svc.force_reconnect_all()

    svc._logger.warning.assert_any_call(
        "[워치독] 기존 연결 종료 중 오류 (무시): Disconnect Error"
    )
    # 오류 무시 후 connect 시도
    svc._streaming_service.connect_websocket.assert_awaited_once()


# ── 태스크 라이프사이클 ──────────────────────────────────────────────────────

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
async def test_start_and_already_running(watchdog_task, mock_subscription_service):
    """start 메서드 호출 및 이미 실행 중일 때 조기 리턴 검증."""
    svc = watchdog_task
    mock_subscription_service.has_program_trading_subscriptions.return_value = True
    mock_subscription_service._active_codes = {"005930"}

    await svc.start()

    assert svc.state == TaskState.RUNNING
    svc._realtime_data_service.start_background_tasks.assert_called_once()
    assert len(svc._tasks) == 2  # _restore_subscriptions, _websocket_watchdog

    # 이미 RUNNING 상태일 때 start() 재호출 시 아무 작업도 하지 않음
    await svc.start()
    assert len(svc._tasks) == 2

    # 🚨 무한 루프 태스크 취소를 위해 반드시 stop() 호출 (hang 방지)
    await svc.stop()


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


# ── get_progress() ───────────────────────────────────────────────────────────

def test_get_progress_initial_state(watchdog_task):
    """초기 상태: market_open=None, subscribed_codes=0, data_gap_sec=None."""
    p = watchdog_task.get_progress()

    assert p["running"] is False
    assert p["subscribed_codes"] == 0
    assert p["data_gap_sec"] is None
    assert p["market_open"] is None


def test_get_progress_subscribed_codes_is_total(watchdog_task, mock_subscription_service):
    """PT + 체결가 구독 합산 수가 subscribed_codes에 반영된다."""
    mock_subscription_service.get_program_trading_codes.return_value = ["005930", "000660"]
    mock_subscription_service._active_codes = {"035720", "051910"}

    p = watchdog_task.get_progress()

    assert p["subscribed_codes"] == 4  # PT 2 + price 2


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


def test_get_progress_uses_price_cache_when_no_pt_data(watchdog_task, mock_subscription_service):
    """PT last_data_ts=0일 때 체결가 최신가 캐시의 received_at으로 data_gap_sec을 계산한다."""
    watchdog_task._realtime_data_service.last_data_ts = 0.0
    code = "005930"
    mock_subscription_service._active_codes = {code}
    watchdog_task._streaming_service._latest_prices = {
        code: {"price": "70000", "received_at": time.time() - 20.0}
    }

    p = watchdog_task.get_progress()

    assert p["data_gap_sec"] is not None
    assert 18.0 <= p["data_gap_sec"] <= 23.0


@pytest.mark.asyncio
async def test_websocket_watchdog_price_data_gap_triggers_reconnect(watchdog_task, mock_subscription_service):
    """PT 없이 체결가만 구독 중일 때 데이터 갭 초과 시 재연결을 시도한다."""
    svc = watchdog_task
    svc.mcs.is_market_open_now.return_value = True
    mock_subscription_service.has_program_trading_subscriptions.return_value = False
    code = "035720"
    mock_subscription_service._active_codes = {code}
    # 130초 전 마지막 수신 (임계값 120초 초과)
    svc._streaming_service._latest_prices = {
        code: {"price": "50000", "received_at": time.time() - 130}
    }
    svc._streaming_service.broker.is_websocket_receive_alive.return_value = True
    svc._streaming_service.broker.is_websocket_connected.return_value = True

    svc.force_reconnect_all = AsyncMock()

    async def sleep_side_effect(seconds):
        if sleep_side_effect.counter == 0:
            sleep_side_effect.counter += 1
            return
        raise asyncio.CancelledError
    sleep_side_effect.counter = 0

    with patch("task.background.intraday.websocket_watchdog_task.asyncio.sleep", side_effect=sleep_side_effect):
        try:
            await svc._websocket_watchdog()
        except asyncio.CancelledError:
            pass

    svc.force_reconnect_all.assert_called_once()
    args, _ = svc._logger.warning.call_args
    assert "체결가 데이터 미수신" in args[0]
    assert "재연결을 시도합니다" in args[0]


@pytest.mark.asyncio
async def test_websocket_watchdog_price_data_gap_no_reconnect_if_recent(watchdog_task, mock_subscription_service):
    """체결가 데이터를 최근에 수신한 경우 재연결하지 않는다."""
    svc = watchdog_task
    svc.mcs.is_market_open_now.return_value = True
    mock_subscription_service.has_program_trading_subscriptions.return_value = False
    code = "035720"
    mock_subscription_service._active_codes = {code}
    svc._streaming_service._latest_prices = {
        code: {"price": "50000", "received_at": time.time() - 10}  # 최근 수신
    }
    svc._streaming_service.broker.is_websocket_receive_alive.return_value = True
    svc._streaming_service.broker.is_websocket_connected.return_value = True

    svc.force_reconnect_all = AsyncMock()

    async def sleep_side_effect(seconds):
        if sleep_side_effect.counter == 0:
            sleep_side_effect.counter += 1
            return
        raise asyncio.CancelledError
    sleep_side_effect.counter = 0

    with patch("task.background.intraday.websocket_watchdog_task.asyncio.sleep", side_effect=sleep_side_effect):
        try:
            await svc._websocket_watchdog()
        except asyncio.CancelledError:
            pass

    svc.force_reconnect_all.assert_not_called()
