# tests/unit_test/test_notification_manager.py

import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock
from datetime import datetime

from managers.notification_manager import NotificationManager, NotificationEvent

@pytest.fixture
def mock_time_manager():
    """TimeManager의 Mock 객체를 생성합니다."""
    tm = MagicMock()
    tm.get_current_kst_time.return_value = datetime(2025, 1, 1, 10, 0, 0)
    return tm

@pytest.fixture
def manager(mock_time_manager):
    """테스트용 NotificationManager 인스턴스를 생성합니다."""
    return NotificationManager(time_manager=mock_time_manager)

# --- NotificationEvent Tests ---

def test_notification_event_to_dict():
    """NotificationEvent.to_dict가 정상적으로 dict를 반환하는지 테스트합니다."""
    event = NotificationEvent(
        id="123",
        timestamp="2025-01-01T10:00:00",
        category="TEST",
        level="info",
        title="Test Event",
        message="This is a test.",
        metadata={"extra": "data"}
    )
    event_dict = event.to_dict()
    assert event_dict["id"] == "123"
    assert event_dict["category"] == "TEST"
    assert event_dict["metadata"] == {"extra": "data"}

# --- NotificationManager Tests ---

def test_init(manager):
    """NotificationManager 초기화 테스트"""
    assert manager._history == []
    assert manager._subscriber_queues == []
    assert manager._external_handlers == []

@pytest.mark.asyncio
async def test_emit_basic(manager):
    """emit 메서드의 기본 동작(이벤트 생성, 히스토리 저장, 반환)을 테스트합니다."""
    event = await manager.emit("TRADE", "info", "매수", "삼성전자 1주 매수")
    
    assert isinstance(event, NotificationEvent)
    assert event.category == "TRADE"
    assert event.message == "삼성전자 1주 매수"
    assert len(manager._history) == 1
    assert manager._history[0] == event

@pytest.mark.asyncio
async def test_emit_sends_to_subscribers(manager):
    """emit 시 구독자 큐에 데이터가 전송되는지 테스트합니다."""
    queue1 = manager.create_subscriber_queue()
    queue2 = manager.create_subscriber_queue()
    
    event = await manager.emit("SYSTEM", "warning", "API 지연", "응답 시간 5초 초과")
    
    # 큐에서 데이터 확인
    data1 = await queue1.get()
    data2 = await queue2.get()
    
    assert data1["id"] == event.id
    assert data1["message"] == "응답 시간 5초 초과"
    assert data2["id"] == event.id

@pytest.mark.asyncio
async def test_emit_handles_full_queue(manager):
    """emit 시 큐가 가득 찼을 때 예외를 처리하고 넘어가는지 테스트합니다."""
    # maxsize=1인 큐 생성 후 데이터 채우기
    queue = asyncio.Queue(maxsize=1)
    await queue.put("dummy")
    
    manager._subscriber_queues.append(queue)
    
    # 큐가 가득 찬 상태에서 emit 호출. asyncio.QueueFull 예외가 발생하지 않아야 함.
    try:
        await manager.emit("SYSTEM", "error", "에러", "큐 Full 테스트")
    except asyncio.QueueFull:
        pytest.fail("asyncio.QueueFull 예외가 처리되지 않았습니다.")
        
    # 큐에 추가 데이터가 들어가지 않았는지 확인
    assert queue.full()
    assert await queue.get() == "dummy" # 기존 데이터만 있어야 함

@pytest.mark.asyncio
async def test_emit_calls_external_handlers(manager):
    """emit 시 등록된 외부 핸들러가 호출되는지 테스트합니다."""
    handler1 = AsyncMock()
    handler2 = AsyncMock()
    
    manager.register_external_handler(handler1)
    manager.register_external_handler(handler2)
    
    event = await manager.emit("API", "info", "응답", "정상")
    
    handler1.assert_awaited_once_with(event)
    handler2.assert_awaited_once_with(event)

@pytest.mark.asyncio
async def test_emit_handles_handler_exception(manager):
    """외부 핸들러에서 예외 발생 시 처리하고 계속 진행하는지 테스트합니다."""
    handler_ok = AsyncMock()
    handler_fail = AsyncMock(side_effect=Exception("Handler failed"))
    
    manager.register_external_handler(handler_fail)
    manager.register_external_handler(handler_ok)
    
    # 예외가 전파되지 않아야 함
    try:
        await manager.emit("SYSTEM", "critical", "장애", "DB 연결 실패")
    except Exception:
        pytest.fail("외부 핸들러의 예외가 처리되지 않았습니다.")
        
    # 실패한 핸들러와 성공한 핸들러 모두 호출 시도되었는지 확인
    handler_fail.assert_awaited_once()
    handler_ok.assert_awaited_once()

@pytest.mark.asyncio
async def test_emit_history_trimming(manager):
    """MAX_HISTORY 초과 시 가장 오래된 알림이 제거되는지 테스트합니다."""
    manager.MAX_HISTORY = 3
    
    event1 = await manager.emit("TEST", "info", "1", "1")
    await manager.emit("TEST", "info", "2", "2")
    await manager.emit("TEST", "info", "3", "3")
    event4 = await manager.emit("TEST", "info", "4", "4")
    
    assert len(manager._history) == 3
    assert manager._history[0] != event1 # 첫 번째 이벤트는 제거됨
    assert manager._history[0].title == "2"
    assert manager._history[2] == event4 # 마지막 이벤트는 존재함

def test_subscriber_queue_management(manager):
    """구독자 큐 생성 및 제거 테스트"""
    assert len(manager._subscriber_queues) == 0
    
    queue1 = manager.create_subscriber_queue()
    assert len(manager._subscriber_queues) == 1
    assert queue1 in manager._subscriber_queues
    
    queue2 = manager.create_subscriber_queue()
    assert len(manager._subscriber_queues) == 2
    
    manager.remove_subscriber_queue(queue1)
    assert len(manager._subscriber_queues) == 1
    assert queue1 not in manager._subscriber_queues
    
    # 존재하지 않는 큐 제거 시도 (에러 없어야 함)
    manager.remove_subscriber_queue(queue1)
    assert len(manager._subscriber_queues) == 1

@pytest.mark.asyncio
async def test_get_recent(manager):
    """get_recent 메서드 기능 테스트 (count, category, 정렬)"""
    # 5개의 이벤트 생성
    event1 = await manager.emit("TRADE", "info", "T1", "msg")
    await manager.emit("SYSTEM", "info", "S1", "msg")
    await manager.emit("TRADE", "info", "T2", "msg")
    await manager.emit("API", "info", "A1", "msg")
    event5 = await manager.emit("TRADE", "info", "T3", "msg") # 가장 최신
    
    # 1. 기본 호출 (count=50, category=None) -> 5개 모두, 최신순
    recent = manager.get_recent()
    assert len(recent) == 5
    assert recent[0]["id"] == event5.id
    assert recent[4]["id"] == event1.id
    
    # 2. count 제한
    recent_2 = manager.get_recent(count=2)
    assert len(recent_2) == 2
    assert recent_2[0]["id"] == event5.id
    assert recent_2[1]["title"] == "A1"
    
    # 3. category 필터링
    recent_trade = manager.get_recent(category="TRADE")
    assert len(recent_trade) == 3
    assert recent_trade[0]["title"] == "T3"
    assert recent_trade[1]["title"] == "T2"
    assert recent_trade[2]["title"] == "T1"
    
    # 4. category + count
    recent_trade_1 = manager.get_recent(count=1, category="TRADE")
    assert len(recent_trade_1) == 1
    assert recent_trade_1[0]["title"] == "T3"

def test_register_external_handler(manager):
    """외부 핸들러 등록 테스트"""
    handler = AsyncMock()
    assert len(manager._external_handlers) == 0
    
    manager.register_external_handler(handler)
    assert len(manager._external_handlers) == 1
    assert manager._external_handlers[0] == handler