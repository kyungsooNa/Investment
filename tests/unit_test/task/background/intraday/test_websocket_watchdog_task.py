"""
WebSocketWatchdogTask 단위 테스트.
프로그램매매 워치독/복원/재연결 및 태스크 라이프사이클 검증.
"""
import asyncio
import time
import pytest
from unittest.mock import MagicMock, AsyncMock, patch, ANY, call  # <- call 추가
from task.background.intraday.websocket_watchdog_task import WebSocketWatchdogTask
from services.market_calendar_service import MarketCalendarService
from interfaces.schedulable_task import TaskState
from repositories.streaming_stock_repo import StreamingType


# ── 공통 픽스처 ──────────────────────────────────────────────────────────────

def make_sleep_side_effect(cancel_after_calls=1):
    """
    지정된 횟수만큼은 정상적으로 반환하고, 그 이후에는 CancelledError를 발생시켜mock_price_subscription_service
    무한 루프를 빠져나오게 하는 asyncio.sleep 모킹용 side_effect 생성 헬퍼.
    """
    async def side_effect(*args, **kwargs):
        if side_effect.counter < cancel_after_calls:
            side_effect.counter += 1
            return
        raise asyncio.CancelledError
    side_effect.counter = 0
    return side_effect

def _make_streaming_stock_repo(pt_desired=None):
    """StreamingStockRepo mock 생성 헬퍼."""
    repo = MagicMock()
    repo.get_desired = MagicMock(return_value=set(pt_desired or []))
    repo.get_active = MagicMock(return_value=set())
    repo.clear_active = AsyncMock()
    repo.mark_active = AsyncMock()
    repo.mark_inactive = AsyncMock()
    repo.mark_desired = AsyncMock()
    repo.unmark_desired = AsyncMock()
    return repo


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

    program_trading_stream_service = MagicMock()
    program_trading_stream_service.last_data_ts = 0.0
    program_trading_stream_service.shutdown = AsyncMock()

    market_calendar_service = AsyncMock(spec=MarketCalendarService)
    market_calendar_service.is_market_open_now = AsyncMock(return_value=False)

    logger = MagicMock()

    streaming_stock_repo = _make_streaming_stock_repo()

    return streaming_service, program_trading_stream_service, market_calendar_service, logger, streaming_stock_repo


@pytest.fixture
def watchdog_task(mock_deps, mock_streaming_logger):
    streaming_service, program_trading_stream_service, market_calendar_service, logger, streaming_stock_repo = mock_deps
    return WebSocketWatchdogTask(
        streaming_service=streaming_service,
        program_trading_stream_service=program_trading_stream_service,
        market_calendar_service=market_calendar_service,
        logger=logger,
        streaming_stock_repo=streaming_stock_repo,
        streaming_logger=mock_streaming_logger,
    )


@pytest.fixture
def mock_price_subscription_service():
    svc = MagicMock()
    svc.clear_active_state = MagicMock()
    svc._rebalance = AsyncMock()
    svc._refs = {"005930": 1, "000660": 1}
    # 속성명 수정: _active_codes -> _active_codes_price
    svc._active_codes_price = {"005930", "000660"} 
    return svc


@pytest.fixture
def mock_streaming_logger():
    logger = MagicMock()
    logger.log_watchdog_check = MagicMock()
    logger.log_subscription_recovery_start = MagicMock()
    logger.log_pt_subscribe = MagicMock()
    logger.log_price_subscribe = MagicMock()
    logger.log_pt_unsubscribe = MagicMock()
    logger.log_price_unsubscribe = MagicMock()
    logger.log_subscription_recovery_done = MagicMock()
    logger.log_restore = MagicMock()
    logger.log_reconnect = MagicMock()
    return logger


# ── _restore_all_subscriptions 테스트 ────────────────────────────────────────

@pytest.mark.asyncio
async def test_restore_program_trading_success(watchdog_task, mock_deps):
    """_restore_all_subscriptions: 모든 PT 종목 구독 복원 성공 케이스."""
    svc = watchdog_task
    codes = ["005930", "000660"]
    svc._streaming_stock_repo.get_desired.return_value = set(codes)

    with patch("task.background.intraday.websocket_watchdog_task.asyncio.sleep", new_callable=AsyncMock):
        await svc._restore_all_subscriptions()

    assert svc._streaming_service.connect_websocket.call_count == 2
    assert svc._streaming_service.subscribe_program_trading.call_count == 2
    assert svc._streaming_service.subscribe_realtime_price.call_count == 2
    svc._streaming_logger.log_subscription_recovery_done.assert_called_once()


@pytest.mark.asyncio
async def test_restore_program_trading_partial_failure(watchdog_task, mock_deps):
    """_restore_all_subscriptions: 연결 실패 종목은 desired에서 제거, 성공 종목은 active로 등록."""
    svc = watchdog_task
    # "000660"만 connect 실패, "005930"과 "035720"은 성공
    connect_results = iter([True, False, True])

    async def connect_side_effect(callback=None):
        return next(connect_results)

    svc._streaming_service.connect_websocket = AsyncMock(side_effect=connect_side_effect)

    # 구독 종목 2개 (성공하는 것 1개, 실패하는 것 1개)
    svc._streaming_stock_repo.get_desired.return_value = {"005930", "000660", "035720"}

    with patch("task.background.intraday.websocket_watchdog_task.asyncio.sleep", new_callable=AsyncMock):
        await svc._restore_all_subscriptions()

    assert svc._streaming_service.connect_websocket.await_count == 3

    # connect 실패한 종목은 streaming_logger + unmark_desired 호출 확인
    from repositories.streaming_stock_repo import StreamingType
    unmark_calls = {call.args[0] for call in svc._streaming_stock_repo.unmark_desired.call_args_list}
    assert len(unmark_calls) == 1  # connect 실패 1종목
    failed_code = next(iter(unmark_calls))
    svc._streaming_logger.log_pt_restore_connect_failed.assert_called_once_with(failed_code)


# ── _streaming_watchdog 테스트 ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_streaming_watchdog_market_closed(watchdog_task, mock_deps):
    """_streaming_watchdog: 장 마감 시 웹소켓 연결 종료 검증."""
    svc = watchdog_task
    svc.mcs.is_market_open_now.return_value = False
    svc._streaming_stock_repo.get_desired.return_value = {"005930"}
    svc._streaming_service.broker.is_websocket_receive_alive.return_value = True
    svc._streaming_service.disconnect_websocket = AsyncMock()

    with patch("task.background.intraday.websocket_watchdog_task.asyncio.sleep", side_effect=make_sleep_side_effect(1)):
        try:
            await svc._streaming_watchdog()
        except asyncio.CancelledError:
            pass

    svc._streaming_service.disconnect_websocket.assert_awaited_once()
    svc._streaming_logger.log_market_closed_disconnect.assert_called_once()


@pytest.mark.asyncio
async def test_streaming_watchdog_data_gap(watchdog_task, mock_deps):
    """_streaming_watchdog: PT 데이터 미수신 감지 및 재연결 시도 검증."""
    svc = watchdog_task
    svc.mcs.is_market_open_now.return_value = True
    svc._streaming_stock_repo.get_desired.return_value = {"005930"}
    svc._program_trading_stream_service.last_data_ts = time.time() - 310  # 임계값 300초 초과
    svc._streaming_service.broker.is_websocket_receive_alive.return_value = True

    svc.force_reconnect = AsyncMock()

    with patch("task.background.intraday.websocket_watchdog_task.asyncio.sleep", side_effect=make_sleep_side_effect(1)):
        try:
            await svc._streaming_watchdog()
        except asyncio.CancelledError:
            pass

    svc.force_reconnect.assert_called_once()
    svc._streaming_logger.log_pt_data_gap.assert_called_once()
    data_gap_arg = svc._streaming_logger.log_pt_data_gap.call_args[0][0]
    assert data_gap_arg > 300


@pytest.mark.asyncio
async def test_streaming_watchdog_no_reconnect_when_never_received(watchdog_task, mock_deps):
    """_streaming_watchdog: 데이터를 한 번도 받지 않았을 때(last_data_ts=0) 재연결하지 않음."""
    svc = watchdog_task
    svc.mcs.is_market_open_now.return_value = True
    svc._streaming_stock_repo.get_desired.return_value = {"005930"}
    svc._program_trading_stream_service.last_data_ts = 0.0
    svc._streaming_service.broker.is_websocket_receive_alive.return_value = True

    svc.force_reconnect = AsyncMock()

    with patch("task.background.intraday.websocket_watchdog_task.asyncio.sleep", side_effect=make_sleep_side_effect(1)):
        try:
            await svc._streaming_watchdog()
        except asyncio.CancelledError:
            pass

    svc.force_reconnect.assert_not_called()


@pytest.mark.asyncio
async def test_streaming_watchdog_skips_when_no_repo(watchdog_task):
    """_streaming_stock_repo가 없으면 워치독이 아무것도 하지 않는다."""
    svc = watchdog_task
    svc._streaming_stock_repo = None
    svc.force_reconnect = AsyncMock()

    with patch("task.background.intraday.websocket_watchdog_task.asyncio.sleep", side_effect=make_sleep_side_effect(1)):
        try:
            await svc._streaming_watchdog()
        except asyncio.CancelledError:
            pass

    svc.force_reconnect.assert_not_called()


# ── force_reconnect 테스트 ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_force_reconnect_program_trading(watchdog_task, mock_deps):
    """force_reconnect_program_trading: 연결 종료 후 재구독 검증."""
    svc = watchdog_task
    svc._streaming_stock_repo.get_desired.return_value = {"005930", "000660"}

    with patch("task.background.intraday.websocket_watchdog_task.asyncio.sleep", new_callable=AsyncMock):
        await svc.force_reconnect_program_trading()

    svc._streaming_service.disconnect_websocket.assert_awaited_once()
    assert svc._streaming_service.connect_websocket.call_count == 2
    assert svc._streaming_service.subscribe_program_trading.call_count == 2
    assert svc._streaming_service.subscribe_realtime_price.call_count == 2
    svc._streaming_logger.log_force_reconnect_done.assert_called_once_with("manual")


@pytest.mark.asyncio
async def test_force_reconnect_calls_connect_without_callback(watchdog_task, mock_deps):
    """force_reconnect: connect_websocket()을 콜백 인자 없이 호출하는지 검증."""
    svc = watchdog_task
    svc._streaming_stock_repo.get_desired.return_value = {"005930"}

    with patch("task.background.intraday.websocket_watchdog_task.asyncio.sleep", new_callable=AsyncMock):
        await svc.force_reconnect_program_trading()

    svc._streaming_service.connect_websocket.assert_awaited_once_with()


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
    svc._program_trading_stream_service.shutdown.assert_awaited_once()

def test_state_property(watchdog_task):
    """state 프로퍼티 검증."""
    assert watchdog_task.state == TaskState.IDLE

@pytest.mark.asyncio
async def test_start_and_already_running(watchdog_task):
    """start 메서드 호출 및 이미 실행 중일 때 조기 리턴 검증."""
    svc = watchdog_task

    await svc.start()

    assert svc.state == TaskState.RUNNING
    svc._program_trading_stream_service.start_background_tasks.assert_called_once()
    assert len(svc._tasks) == 2  # _restore_all_subscriptions, _streaming_watchdog

    # 이미 RUNNING 상태일 때 start() 재호출 시 아무 작업도 하지 않음
    await svc.start()
    assert len(svc._tasks) == 2

    # 무한 루프 태스크 취소를 위해 반드시 stop() 호출 (hang 방지)
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


@pytest.mark.asyncio
async def test_force_reconnect_program_trading_early_returns(watchdog_task):
    """streaming_stock_repo가 없거나 구독 종목이 없을 때 조기 리턴 검증."""
    svc = watchdog_task

    # streaming_stock_repo가 없을 때
    svc._streaming_stock_repo = None
    await svc.force_reconnect_program_trading()
    svc._streaming_service.disconnect_websocket.assert_not_called()

    # streaming_stock_repo가 있지만 desired가 없을 때
    svc._streaming_stock_repo = _make_streaming_stock_repo(pt_desired=[])
    await svc.force_reconnect_program_trading()
    svc._streaming_service.disconnect_websocket.assert_not_called()


@pytest.mark.asyncio
async def test_force_reconnect_program_trading_errors(watchdog_task, mock_deps):
    """연결 종료 오류 및 구독 실패 시나리오 검증."""
    svc = watchdog_task
    svc._streaming_stock_repo.get_desired.return_value = {"005930"}

    # disconnect_websocket에서 오류 발생 (무시하고 계속 진행되어야 함)
    svc._streaming_service.disconnect_websocket = AsyncMock(side_effect=Exception("Disconnect Error"))
    # connect 실패 → 005930이 unmark_desired 처리
    svc._streaming_service.connect_websocket = AsyncMock(return_value=False)

    with patch("task.background.intraday.websocket_watchdog_task.asyncio.sleep", new_callable=AsyncMock):
        await svc.force_reconnect_program_trading()

    svc._streaming_logger.log_force_reconnect_disconnect_error.assert_called_once_with("Disconnect Error")
    svc._streaming_logger.log_pt_restore_connect_failed.assert_called_once_with("005930")
    # 실패 종목이 unmark_desired 호출되어야 함
    svc._streaming_stock_repo.unmark_desired.assert_called_once()
    assert svc._streaming_stock_repo.unmark_desired.call_args.args[0] == "005930"


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
    watchdog_task._streaming_stock_repo.get_desired.return_value = {"005930", "000660"}

    p = watchdog_task.get_progress()

    assert p["subscribed_codes"] == 2


def test_get_progress_data_gap_calculated(watchdog_task):
    """last_data_ts가 설정되면 data_gap_sec을 계산한다."""
    watchdog_task._program_trading_stream_service.last_data_ts = time.time() - 30.0

    p = watchdog_task.get_progress()

    assert p["data_gap_sec"] is not None
    assert 28.0 <= p["data_gap_sec"] <= 33.0  # 30초 ± 여유


def test_get_progress_no_data_ts_returns_none_gap(watchdog_task):
    """last_data_ts=0이면 data_gap_sec은 None."""
    watchdog_task._program_trading_stream_service.last_data_ts = 0.0

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
async def test_streaming_watchdog_sets_market_open_false(watchdog_task):
    """장 마감 감지 후 _market_open이 False로 설정된다."""
    svc = watchdog_task
    svc.mcs.is_market_open_now.return_value = False
    svc._streaming_stock_repo.get_desired.return_value = {"005930"}
    svc._streaming_service.broker.is_websocket_receive_alive.return_value = False

    with patch("task.background.intraday.websocket_watchdog_task.asyncio.sleep", side_effect=make_sleep_side_effect(1)):
        try:
            await svc._streaming_watchdog()
        except asyncio.CancelledError:
            pass

    assert svc._market_open is False


@pytest.mark.asyncio
async def test_streaming_watchdog_sets_market_open_true(watchdog_task):
    """장 중 감지 후 _market_open이 True로 설정된다."""
    svc = watchdog_task
    svc.mcs.is_market_open_now.return_value = True
    svc._streaming_stock_repo.get_desired.return_value = {"005930"}
    svc._program_trading_stream_service.last_data_ts = time.time() - 1.0  # 최근 수신
    svc._streaming_service.broker.is_websocket_receive_alive.return_value = True
    svc.force_reconnect = AsyncMock()

    with patch("task.background.intraday.websocket_watchdog_task.asyncio.sleep", side_effect=make_sleep_side_effect(1)):
        try:
            await svc._streaming_watchdog()
        except asyncio.CancelledError:
            pass

    assert svc._market_open is True


# ── 추가 Coverage 확보용 테스트 ──────────────────────────────────────────────

def test_init_without_performance_profiler():
    """performance_profiler가 없을 때 기본 활성화 안 된 프로파일러 생성"""
    task = WebSocketWatchdogTask()
    assert task.pm is not None
    assert task.pm.enabled is False


@pytest.mark.asyncio
async def test_restore_all_subscriptions_with_price_service(watchdog_task, mock_price_subscription_service, mock_streaming_logger):
    """H0UNCNT0(PriceSubscriptionService) 복원 로직이 정상 동작하는지 검증."""
    watchdog_task._price_subscription_service = mock_price_subscription_service
    watchdog_task._streaming_logger = mock_streaming_logger
    watchdog_task._streaming_stock_repo.get_desired.return_value = set()  # PT 구독 없음

    await watchdog_task._restore_all_subscriptions()

    mock_price_subscription_service.clear_active_state.assert_called_once()
    watchdog_task._streaming_stock_repo.clear_active.assert_awaited_with(StreamingType.UNIFIED_PRICE)
    mock_price_subscription_service._rebalance.assert_awaited_once()
    
    # PT가 없으므로 PT 관련 로그는 안 찍힘
    mock_streaming_logger.log_restore.assert_called_once()


@pytest.mark.asyncio
async def test_streaming_watchdog_receive_task_dead(watchdog_task):
    """수신 태스크가 죽었을 때(receive_alive=False) 재연결을 시도하는지 검증."""
    svc = watchdog_task
    svc.mcs.is_market_open_now.return_value = True
    svc._streaming_stock_repo.get_desired.return_value = {"005930"}
    svc._streaming_service.broker.is_websocket_receive_alive.return_value = False
    svc._intentionally_disconnected = False

    svc.force_reconnect = AsyncMock()

    with patch("task.background.intraday.websocket_watchdog_task.asyncio.sleep", side_effect=make_sleep_side_effect(1)):
        try:
            await svc._streaming_watchdog()
        except asyncio.CancelledError:
            pass

    svc.force_reconnect.assert_called_once_with(trigger="receive_task_dead")


@pytest.mark.asyncio
async def test_streaming_watchdog_market_open_reconnect(watchdog_task):
    """장 시작으로 인해 신규 재연결을 수립하는지 검증."""
    svc = watchdog_task
    svc.mcs.is_market_open_now.return_value = True
    svc._streaming_stock_repo.get_desired.return_value = {"005930"}
    svc._streaming_service.broker.is_websocket_receive_alive.return_value = False
    svc._intentionally_disconnected = True  # 장 마감으로 끊었던 상태

    svc.force_reconnect = AsyncMock()

    with patch("task.background.intraday.websocket_watchdog_task.asyncio.sleep", side_effect=make_sleep_side_effect(1)):
        try:
            await svc._streaming_watchdog()
        except asyncio.CancelledError:
            pass

    svc.force_reconnect.assert_called_once_with(trigger="market_open")
    assert svc._intentionally_disconnected is False


@pytest.mark.asyncio
async def test_streaming_watchdog_no_realtime_service(watchdog_task):
    """_program_trading_stream_service가 None이어도 구독이 없으면 워치독 루프가 스킵되는지 검증."""
    svc = watchdog_task
    svc._program_trading_stream_service = None
    # get_desired 기본값이 set()이므로 pt_codes=[], has_price_subs=False → 스킵
    svc.force_reconnect = AsyncMock()

    with patch("task.background.intraday.websocket_watchdog_task.asyncio.sleep", side_effect=make_sleep_side_effect(1)):
        try:
            await svc._streaming_watchdog()
        except asyncio.CancelledError:
            pass

    svc.force_reconnect.assert_not_called()


@pytest.mark.asyncio
async def test_streaming_watchdog_no_desired_codes(watchdog_task):
    """PT 구독도 없고 체결가 구독도 없을 때 워치독 루프가 스킵되는지 검증."""
    svc = watchdog_task
    svc.mcs.is_market_open_now.return_value = True
    svc._streaming_stock_repo.get_desired.return_value = set()
    svc._price_subscription_service = None
    svc.force_reconnect = AsyncMock()

    with patch("task.background.intraday.websocket_watchdog_task.asyncio.sleep", side_effect=make_sleep_side_effect(1)):
        try:
            await svc._streaming_watchdog()
        except asyncio.CancelledError:
            pass

    svc.force_reconnect.assert_not_called()


@pytest.mark.asyncio
async def test_streaming_watchdog_price_only_subscription_proceeds(watchdog_task, mock_price_subscription_service):
    """PT 구독이 없어도 실시간 체결가 구독이 있으면 워치독이 receive_alive를 감시한다."""
    svc = watchdog_task
    svc._streaming_stock_repo.get_desired.return_value = set()  # PT 없음
    svc._price_subscription_service = mock_price_subscription_service  # 체결가 구독 있음
    svc.mcs.is_market_open_now.return_value = True
    svc._streaming_service.broker.is_websocket_receive_alive.return_value = False
    svc._intentionally_disconnected = False

    svc.force_reconnect = AsyncMock()

    with patch("task.background.intraday.websocket_watchdog_task.asyncio.sleep", side_effect=make_sleep_side_effect(1)):
        try:
            await svc._streaming_watchdog()
        except asyncio.CancelledError:
            pass

    # PT 없이 체결가만 있어도 receive_task_dead로 재연결해야 함
    svc.force_reconnect.assert_called_once_with(trigger="receive_task_dead")


@pytest.mark.asyncio
async def test_streaming_watchdog_price_only_no_data_gap_check(watchdog_task, mock_price_subscription_service):
    """PT 구독 없이 체결가만 구독 중일 때 data_gap 기반 재연결은 하지 않는다."""
    svc = watchdog_task
    svc._streaming_stock_repo.get_desired.return_value = set()  # PT 없음
    svc._price_subscription_service = mock_price_subscription_service  # 체결가 구독 있음
    svc.mcs.is_market_open_now.return_value = True
    svc._streaming_service.broker.is_websocket_receive_alive.return_value = True  # 연결 살아있음
    svc._program_trading_stream_service.last_data_ts = time.time() - 310  # PT 타임스탬프가 오래됐어도

    svc.force_reconnect = AsyncMock()

    with patch("task.background.intraday.websocket_watchdog_task.asyncio.sleep", side_effect=make_sleep_side_effect(1)):
        try:
            await svc._streaming_watchdog()
        except asyncio.CancelledError:
            pass

    # PT 종목이 없으므로 data_gap 체크 안 함 → 재연결 없음
    svc.force_reconnect.assert_not_called()


@pytest.mark.asyncio
async def test_force_reconnect_with_price_subs(watchdog_task, mock_price_subscription_service):
    """PT 종목이 없어도 Price Subscription이 있으면 강제 재연결을 수행하는지 검증."""
    svc = watchdog_task
    svc._streaming_stock_repo.get_desired.return_value = set()
    svc._price_subscription_service = mock_price_subscription_service
    
    svc._restore_all_subscriptions = AsyncMock()

    await svc.force_reconnect(trigger="manual")

    svc._streaming_service.disconnect_websocket.assert_awaited_once()
    svc._restore_all_subscriptions.assert_awaited_once()


@pytest.mark.asyncio
async def test_force_reconnect_returns_when_no_subs(watchdog_task):
    """구독 종목이 아예 없을 때 강제 재연결이 조기 리턴되는지 검증."""
    svc = watchdog_task
    svc._streaming_stock_repo.get_desired.return_value = set()
    svc._price_subscription_service = None
    
    svc._restore_all_subscriptions = AsyncMock()

    await svc.force_reconnect(trigger="manual")

    svc._streaming_service.disconnect_websocket.assert_not_called()
    svc._restore_all_subscriptions.assert_not_called()


@pytest.mark.asyncio
async def test_streaming_logger_calls_on_restore_and_reconnect(watchdog_task, mock_streaming_logger):
    """복원 및 재연결 과정에서 streaming_logger 메서드들이 제대로 호출되는지 검증."""
    svc = watchdog_task
    svc._streaming_logger = mock_streaming_logger
    svc._streaming_stock_repo.get_desired.return_value = {"005930"}

    with patch("task.background.intraday.websocket_watchdog_task.asyncio.sleep", new_callable=AsyncMock):
        await svc.force_reconnect(trigger="test_trigger")

    mock_streaming_logger.log_reconnect.assert_called_once()
    mock_streaming_logger.log_subscription_recovery_start.assert_called_once()
    mock_streaming_logger.log_pt_subscribe.assert_called_once()
    mock_streaming_logger.log_price_subscribe.assert_called_once()
    mock_streaming_logger.log_subscription_recovery_done.assert_called_once()
    mock_streaming_logger.log_restore.assert_called_once()


@pytest.mark.asyncio
async def test_streaming_watchdog_watchdog_log(watchdog_task, mock_streaming_logger):
    """워치독 주기적 실행 시 log_watchdog_check가 호출되는지 확인."""
    svc = watchdog_task
    svc._streaming_logger = mock_streaming_logger
    svc.mcs.is_market_open_now.return_value = True
    svc._streaming_stock_repo.get_desired.return_value = {"005930"}
    svc._program_trading_stream_service.last_data_ts = time.time() - 100
    svc._streaming_service.broker.is_websocket_receive_alive.return_value = True

    svc.force_reconnect = AsyncMock()

    with patch("task.background.intraday.websocket_watchdog_task.asyncio.sleep", side_effect=make_sleep_side_effect(1)):
        try:
            await svc._streaming_watchdog()
        except asyncio.CancelledError:
            pass

    mock_streaming_logger.log_watchdog_check.assert_called_once()

@pytest.mark.asyncio
async def test_restore_accounts_for_pt_slots_before_rebalance(watchdog_task, mock_price_subscription_service):
    """
    PT 복원이 완료되어 Repo에 active로 기록된 후, 
    최종적으로 PriceService의 _rebalance가 호출되는지 순서와 상태를 검증.
    """
    svc = watchdog_task
    svc._price_subscription_service = mock_price_subscription_service
    pt_codes = ["005930"]
    svc._streaming_stock_repo.get_desired.return_value = set(pt_codes)
    
    # mark_active가 호출될 때 repo의 상태가 변하는지 추적하기 위한 setup
    # (실제 로직 순서: PT subscribe -> mark_active -> ... -> Price _rebalance)
    
    with patch("task.background.intraday.websocket_watchdog_task.asyncio.sleep", new_callable=AsyncMock):
        await svc._restore_all_subscriptions()

    # 1. PT 종목이 성공적으로 Repo에 Active로 등록되었는가?
    svc._streaming_stock_repo.mark_active.assert_any_call("005930", StreamingType.PROGRAM_TRADING)
    
    # 2. Price Service의 rebalance가 호출되었는가?
    mock_price_subscription_service._rebalance.assert_awaited_once()
    
    # 3. [핵심] rebalance 호출 시점에 이미 PT 슬롯이 점유되어 있어야 함
    # (PriceSubscriptionService 내부에서 _calculate_used_slots 호출 시 StreamingStockRepo를 참조하므로)
    assert svc._streaming_stock_repo.clear_active.call_count >= 2 # PT 초기화 + Price 초기화

@pytest.mark.asyncio
async def test_restore_sequence_accounts_for_pt_slots(watchdog_task, mock_price_subscription_service):
    """
    PT 복원(mark_active)이 완료된 후 Price 서비스의 _rebalance가 호출되는지 순서 검증.
    이 순서가 보장되어야 _rebalance 내에서 PT가 점유한 슬롯을 제외하고 계산할 수 있음.
    """
    svc = watchdog_task
    svc._price_subscription_service = mock_price_subscription_service

    # 1. PT 종목 1개 설정
    pt_codes = ["005930"]
    svc._streaming_stock_repo.get_desired.return_value = set(pt_codes)

    # 2. 호출 순서 추적을 위해 상위 Mock 생성
    # (주의: 실제 객체의 메서드를 Mock으로 교체하여 추적)
    with patch.object(svc._streaming_stock_repo, 'mark_active', new_callable=AsyncMock) as mock_mark, \
         patch.object(mock_price_subscription_service, '_rebalance', new_callable=AsyncMock) as mock_rebal, \
         patch("task.background.intraday.websocket_watchdog_task.asyncio.sleep", new_callable=AsyncMock):

        # 순서 기록용 매니저
        manager = MagicMock()
        manager.attach_mock(mock_mark, 'mark_active')
        manager.attach_mock(mock_rebal, 'rebalance')

        await svc._restore_all_subscriptions()

        # 3. 검증: PT 마킹이 먼저 발생하고, 그 다음 rebalance가 호출되어야 함
        expected_calls = [
            call.mark_active("005930", StreamingType.PROGRAM_TRADING),
            call.rebalance()
        ]
        manager.assert_has_calls(expected_calls, any_order=False)


# ── Bug Fix: Price-only 구독 시 connect_websocket 누락 버그 수정 검증 ──────────

@pytest.mark.asyncio
async def test_restore_price_only_calls_connect_websocket(watchdog_task, mock_price_subscription_service, mock_streaming_logger):
    """[Bug Fix] PT 구독 없이 H0UNCNT0만 있을 때 _rebalance() 전에 connect_websocket()이 호출된다.

    이전에는 PT 루프에서만 connect_websocket()을 호출했기 때문에
    PT 종목이 없을 경우 WebSocket 미연결 상태로 _rebalance()가 실행되어 구독 실패했음.
    """
    svc = watchdog_task
    svc._price_subscription_service = mock_price_subscription_service
    svc._streaming_logger = mock_streaming_logger
    svc._streaming_stock_repo.get_desired.return_value = set()  # PT 없음

    call_order = []
    svc._streaming_service.connect_websocket = AsyncMock(
        side_effect=lambda **_: call_order.append("connect") or True
    )
    mock_price_subscription_service._rebalance = AsyncMock(
        side_effect=lambda: call_order.append("rebalance")
    )

    await svc._restore_all_subscriptions()

    assert "connect" in call_order
    assert "rebalance" in call_order
    assert call_order.index("connect") < call_order.index("rebalance")


@pytest.mark.asyncio
async def test_restore_price_only_connect_failed_logs_failure(watchdog_task, mock_price_subscription_service, mock_streaming_logger):
    """[Bug Fix] H0UNCNT0 복원 시 connect_websocket()이 False를 반환하면 실패 로그를 남긴다."""
    svc = watchdog_task
    svc._price_subscription_service = mock_price_subscription_service
    svc._streaming_logger = mock_streaming_logger
    svc._streaming_stock_repo.get_desired.return_value = set()  # PT 없음
    svc._streaming_service.connect_websocket = AsyncMock(return_value=False)

    await svc._restore_all_subscriptions()

    mock_streaming_logger.log_pt_restore_connect_failed.assert_called_once_with("H0UNCNT0")


@pytest.mark.asyncio
async def test_restore_price_done_log_called_with_single_arg(watchdog_task, mock_price_subscription_service, mock_streaming_logger):
    """[Bug Fix] log_price_restore_done()이 인자 1개(active_count)만 받아야 한다.

    이전에는 log_price_restore_done(active_count, desired_count)로 인자 2개를 전달해
    'takes 2 positional arguments but 3 were given' 오류가 발생했음.
    """
    svc = watchdog_task
    svc._price_subscription_service = mock_price_subscription_service
    svc._streaming_logger = mock_streaming_logger
    svc._streaming_stock_repo.get_desired.return_value = set()  # PT 없음

    await svc._restore_all_subscriptions()

    mock_streaming_logger.log_price_restore_done.assert_called_once()
    args, kwargs = mock_streaming_logger.log_price_restore_done.call_args
    # 인자가 정확히 1개여야 함 (active_count만)
    assert len(args) == 1
    assert len(kwargs) == 0


@pytest.mark.asyncio
async def test_restore_price_done_log_reflects_active_count(watchdog_task, mock_price_subscription_service, mock_streaming_logger):
    """log_price_restore_done()에 전달되는 값이 _active_codes_price의 실제 크기와 일치한다."""
    svc = watchdog_task
    svc._price_subscription_service = mock_price_subscription_service
    svc._streaming_logger = mock_streaming_logger
    svc._streaming_stock_repo.get_desired.return_value = set()  # PT 없음
    mock_price_subscription_service._active_codes_price = {"005930", "000660"}

    await svc._restore_all_subscriptions()

    args, _ = mock_streaming_logger.log_price_restore_done.call_args
    assert args[0] == 2  # _active_codes_price 크기와 동일
    