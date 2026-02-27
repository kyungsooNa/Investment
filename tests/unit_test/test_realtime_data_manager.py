import pytest
import asyncio
import json
import os
from unittest.mock import MagicMock, patch, mock_open, AsyncMock
from datetime import datetime, timedelta
from managers.realtime_data_manager import RealtimeDataManager

@pytest.fixture
def manager():
    # 로거 모킹
    mock_logger = MagicMock()
    # 초기화 시 _load_pt_history가 호출되므로 파일 시스템 접근을 막기 위해 patch
    with patch("os.path.exists", return_value=False):
        mgr = RealtimeDataManager(logger=mock_logger)
    return mgr

@pytest.mark.asyncio
async def test_on_data_received_stores_and_broadcasts(manager):
    """데이터 수신 시 메모리/버퍼 저장 및 큐 브로드캐스트 테스트"""
    # Arrange
    test_data = {"유가증권단축종목코드": "005930", "price": 100}
    queue = manager.create_subscriber_queue()
    
    # Act
    manager.on_data_received(test_data)
    
    # Assert
    # 1. 메모리 저장 확인
    assert "005930" in manager._pt_history
    assert manager._pt_history["005930"][0] == test_data
    
    # 2. 버퍼 저장 확인
    assert len(manager._pt_history_buffer) == 1
    assert manager._pt_history_buffer[0] == test_data
    
    # 3. 큐 브로드캐스트 확인
    assert queue.qsize() == 1
    item = await queue.get()
    assert item == test_data

@pytest.mark.asyncio
async def test_on_data_received_queue_full_behavior(manager):
    """큐가 가득 찼을 때 오래된 데이터를 버리고 새 데이터를 넣는지 테스트"""
    # Arrange
    queue = manager.create_subscriber_queue()
    # 테스트를 위해 큐 사이즈를 작게 조작
    queue._maxsize = 2
    
    # 큐 가득 채우기
    queue.put_nowait("old_1")
    queue.put_nowait("old_2")
    
    test_data = {"유가증권단축종목코드": "005930", "new": True}
    
    # Act
    manager.on_data_received(test_data)
    
    # Assert
    # 큐가 꽉 찼을 때 가장 오래된 것("old_1")을 버리고 새 것을 넣어야 함
    # 현재 큐 상태 예상: "old_2", test_data
    assert queue.qsize() == 2
    item1 = await queue.get()
    item2 = await queue.get()
    assert item1 == "old_2"
    assert item2 == test_data

def test_flush_pt_history(manager):
    """버퍼 데이터를 파일에 저장하고 버퍼를 비우는지 테스트"""
    # Arrange
    test_data = {"유가증권단축종목코드": "005930", "price": 100}
    manager._pt_history_buffer.append(test_data)
    
    with patch("os.makedirs") as mock_makedirs, \
         patch("builtins.open", mock_open()) as mock_file:
        
        # Act
        manager._flush_pt_history()
        
        # Assert
        # 버퍼가 비워졌는지 확인
        assert len(manager._pt_history_buffer) == 0
        
        # 파일 쓰기 확인
        mock_makedirs.assert_called_once()
        mock_file.assert_called_once()
        handle = mock_file()
        handle.write.assert_called()
        
        # JSONL 형식으로 쓰였는지 확인
        args, _ = handle.write.call_args
        assert json.dumps(test_data, ensure_ascii=False) in args[0]

def test_cleanup_old_pt_history(manager):
    """오래된 히스토리 파일 삭제 테스트"""
    # Arrange
    today = datetime.now()
    # 31일 전 날짜 (삭제 대상)
    old_date = today - timedelta(days=31)
    old_filename = f"pt_history_{old_date.strftime('%Y%m%d')}.jsonl"
    # 1일 전 날짜 (유지 대상)
    recent_date = today - timedelta(days=1)
    recent_filename = f"pt_history_{recent_date.strftime('%Y%m%d')}.jsonl"
    
    with patch("os.path.exists", return_value=True), \
         patch("os.listdir", return_value=[old_filename, recent_filename, "other.txt"]), \
         patch("os.remove") as mock_remove:
        
        # Act
        manager._cleanup_old_pt_history(retention_days=30)
        
        # Assert
        # 오래된 파일만 삭제 호출되었는지 확인
        mock_remove.assert_called_once()
        args, _ = mock_remove.call_args
        assert old_filename in args[0]

def test_subscription_management(manager):
    """구독 상태 관리 기능 테스트"""
    code = "005930"
    
    assert not manager.is_subscribed(code)
    
    manager.add_subscribed_code(code)
    assert manager.is_subscribed(code)
    assert code in manager.get_subscribed_codes()
    
    manager.remove_subscribed_code(code)
    assert not manager.is_subscribed(code)
    
    manager.add_subscribed_code("000660")
    manager.clear_subscribed_codes()
    assert len(manager.get_subscribed_codes()) == 0

def test_load_pt_history(manager):
    """파일에서 히스토리 로드 테스트"""
    # Arrange
    mock_json_line = '{"유가증권단축종목코드": "005930", "price": 100}\n'
    
    with patch("os.path.exists", return_value=True), \
         patch("builtins.open", mock_open(read_data=mock_json_line)):
        
        # Act
        manager._load_pt_history()
        
        # Assert
        assert "005930" in manager._pt_history
        assert len(manager._pt_history["005930"]) == 1
        assert manager._pt_history["005930"][0]["price"] == 100
        
        # 로그 호출 확인
        manager.logger.info.assert_called_with("기존 히스토리 파일에서 1건의 데이터를 복구했습니다.")

@pytest.mark.asyncio
async def test_shutdown(manager):
    """종료 시 태스크 취소 및 플러시 테스트"""
    # Arrange
    mock_task = asyncio.Future()
    mock_task.set_result(None)
    mock_task.cancel = MagicMock()
    manager._flush_task = mock_task
    
    # flush 호출 확인을 위해 버퍼에 데이터 추가
    manager._pt_history_buffer.append({"data": 1})
    
    with patch("os.makedirs"), patch("builtins.open", mock_open()):
        # Act
        await manager.shutdown()
        
        # Assert
        # 태스크 취소 확인
        mock_task.cancel.assert_called_once()
        # 마지막 플러시 확인 (버퍼가 비워졌는지)
        assert len(manager._pt_history_buffer) == 0

def test_save_snapshot(manager):
    """스냅샷 저장 테스트"""
    data = {"test": "data"}
    with patch("os.makedirs") as mock_makedirs, \
         patch("builtins.open", mock_open()) as mock_file:
        
        manager.save_snapshot(data)
        
        mock_makedirs.assert_called_once()
        mock_file.assert_called_once()
        handle = mock_file()
        # json.dump가 호출되었는지 확인 (write 호출 확인)
        handle.write.assert_called()

def test_load_snapshot(manager):
    """스냅샷 로드 테스트"""
    data = {"test": "data"}
    json_str = json.dumps(data)
    
    with patch("os.path.exists", return_value=True), \
         patch("builtins.open", mock_open(read_data=json_str)):
        
        result = manager.load_snapshot()
        assert result == data

def test_load_snapshot_not_exists(manager):
    """스냅샷 파일 없을 때 로드 테스트"""
    with patch("os.path.exists", return_value=False):
        result = manager.load_snapshot()
        assert result is None

@pytest.mark.asyncio
async def test_periodic_flush_loop(manager):
    """주기적 플러시 루프 테스트 (시간 지연 없이)"""
    # Arrange
    manager._flush_pt_history = MagicMock()
    
    # asyncio.sleep을 모킹하여 실제 대기 없이 즉시 리턴하거나 예외를 발생시키도록 설정
    # side_effect: [첫번째 호출 결과, 두번째 호출 결과, ...]
    # 1, 2번째는 즉시 리턴(None) -> flush 실행됨
    # 3번째는 CancelledError 발생 -> 루프 종료
    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        mock_sleep.side_effect = [None, None, asyncio.CancelledError]
        
        # Act
        try:
            await manager._periodic_flush_loop()
        except asyncio.CancelledError:
            pass
            
        # Assert
        # sleep이 3번 호출되었는지 확인 (루프가 돌았는지)
        assert mock_sleep.call_count == 3
        # flush가 2번 호출되었는지 확인 (sleep 후 호출되므로 2번 성공)
        assert manager._flush_pt_history.call_count == 2

def test_load_pt_history_corrupted_json(manager):
    """손상된 JSON 라인이 포함된 파일 로드 테스트 (커버리지 향상)"""
    # 정상 라인 + 손상된 라인
    mock_data = '{"유가증권단축종목코드": "005930", "price": 100}\n{invalid_json}\n'
    
    with patch("os.path.exists", return_value=True), \
         patch("builtins.open", mock_open(read_data=mock_data)):
        
        manager._load_pt_history()
        
        # 정상 데이터는 로드되어야 함
        assert "005930" in manager._pt_history
        assert len(manager._pt_history["005930"]) == 1

def test_flush_pt_history_exception(manager):
    """플러시 중 파일 쓰기 예외 발생 테스트 (커버리지 향상)"""
    manager._pt_history_buffer.append({"data": 1})
    
    with patch("os.makedirs"), \
         patch("builtins.open", side_effect=IOError("Disk full")):
        
        # 예외가 발생해도 크래시되지 않아야 함
        manager._flush_pt_history()
        
        # 에러 로그 확인
        manager.logger.error.assert_called()

def test_cleanup_old_pt_history_exception(manager):
    """오래된 파일 삭제 중 예외 발생 테스트 (커버리지 향상)"""
    today = datetime.now()
    old_date = today - timedelta(days=31)
    old_filename = f"pt_history_{old_date.strftime('%Y%m%d')}.jsonl"
    
    with patch("os.path.exists", return_value=True), \
         patch("os.listdir", return_value=[old_filename]), \
         patch("os.remove", side_effect=OSError("Permission denied")):
        
        manager._cleanup_old_pt_history(retention_days=30)
        
        # 에러 로그 확인
        manager.logger.error.assert_called()

def test_on_data_received_missing_key(manager):
    """필수 키가 없는 데이터 수신 시 무시하는지 테스트 (커버리지 향상)"""
    # "유가증권단축종목코드" 키가 없는 데이터
    invalid_data = {"price": 100}
    
    manager.on_data_received(invalid_data)
    
    # 저장되지 않아야 함
    assert len(manager._pt_history) == 0
    assert len(manager._pt_history_buffer) == 0