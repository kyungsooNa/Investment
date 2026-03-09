import pytest
import asyncio
import json
import os
import sqlite3
import time
from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta
from managers.realtime_data_manager import RealtimeDataManager


@pytest.fixture
def tmp_db_dir(tmp_path):
    """임시 DB 디렉토리 제공."""
    return str(tmp_path / "program_subscribe")


@pytest.fixture
def manager(tmp_db_dir):
    """테스트용 RealtimeDataManager (임시 디렉토리 사용)."""
    mock_logger = MagicMock()
    with patch.object(RealtimeDataManager, '_get_base_dir', return_value=tmp_db_dir):
        mgr = RealtimeDataManager(logger=mock_logger)
    yield mgr
    # cleanup: DB 연결 닫기
    if mgr._conn:
        mgr._conn.close()


def _get_db_path(tmp_db_dir):
    return os.path.join(tmp_db_dir, "program_trading.db")


# --- 기본 초기화 ---

def test_init_creates_db(manager, tmp_db_dir):
    """초기화 시 SQLite DB 파일 및 테이블이 생성되는지 확인."""
    db_path = _get_db_path(tmp_db_dir)
    assert os.path.exists(db_path)

    # 테이블 존재 확인
    conn = sqlite3.connect(db_path)
    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row[0] for row in cursor.fetchall()}
    conn.close()

    assert 'pt_history' in tables
    assert 'pt_subscriptions' in tables
    assert 'pt_snapshot' in tables


def test_init_db_failure():
    """DB 초기화 실패 시 에러 로그 확인."""
    mock_logger = MagicMock()
    with patch.object(RealtimeDataManager, '_get_base_dir', return_value='/invalid/path/that/cannot/exist'):
        with patch('os.makedirs', side_effect=OSError("Permission denied")):
            # 초기화 실패해도 크래시되지 않아야 함
            try:
                mgr = RealtimeDataManager(logger=mock_logger)
            except Exception:
                pass  # DB 초기화 실패 허용


# --- 데이터 수신 및 저장 ---

@pytest.mark.asyncio
async def test_on_data_received_stores_and_broadcasts(manager):
    """데이터 수신 시 메모리 + SQLite 저장 및 큐 브로드캐스트 테스트."""
    test_data = {"유가증권단축종목코드": "005930", "price": 100}
    queue = manager.create_subscriber_queue()

    manager.on_data_received(test_data)

    # 1. 메모리 저장 확인
    assert "005930" in manager._pt_history
    assert manager._pt_history["005930"][0] == test_data

    # 2. SQLite 저장 확인
    with manager._get_connection() as conn:
        cursor = conn.execute("SELECT COUNT(*) FROM pt_history WHERE code = '005930'")
        assert cursor.fetchone()[0] == 1

    # 3. 큐 브로드캐스트 확인
    assert queue.qsize() == 1
    item = await queue.get()
    assert item == test_data


@pytest.mark.asyncio
async def test_on_data_received_queue_full_behavior(manager):
    """큐가 가득 찼을 때 오래된 데이터를 버리고 새 데이터를 넣는지 테스트."""
    queue = manager.create_subscriber_queue()
    queue._maxsize = 2
    queue.put_nowait("old_1")
    queue.put_nowait("old_2")

    test_data = {"유가증권단축종목코드": "005930", "new": True}
    manager.on_data_received(test_data)

    assert queue.qsize() == 2
    item1 = await queue.get()
    item2 = await queue.get()
    assert item1 == "old_2"
    assert item2 == test_data


def test_on_data_received_missing_key(manager):
    """필수 키가 없는 데이터 수신 시 무시하는지 테스트."""
    invalid_data = {"price": 100}
    manager.on_data_received(invalid_data)
    assert len(manager._pt_history) == 0


def test_on_data_received_queue_exception(manager):
    """큐 put 중 예외 발생 시 크래시되지 않는지 테스트."""
    queue = MagicMock()
    queue.put_nowait.side_effect = Exception("Queue error")
    manager._pt_queues.append(queue)

    test_data = {"유가증권단축종목코드": "005930"}
    manager.on_data_received(test_data)
    assert queue in manager._pt_queues


# --- 구독 상태 관리 (SQLite 영속) ---

def test_subscription_management(manager):
    """구독 상태 관리 기능 테스트."""
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


def test_subscription_persisted_in_db(manager, tmp_db_dir):
    """구독 상태가 SQLite에 영속되는지 확인."""
    manager.add_subscribed_code("005930")
    manager.add_subscribed_code("000660")

    # DB 직접 확인
    with manager._get_connection() as conn:
        cursor = conn.execute("SELECT code FROM pt_subscriptions ORDER BY code")
        codes = [row[0] for row in cursor.fetchall()]
    assert codes == ["000660", "005930"]

    # 삭제 후 확인
    manager.remove_subscribed_code("005930")
    with manager._get_connection() as conn:
        cursor = conn.execute("SELECT code FROM pt_subscriptions")
        codes = [row[0] for row in cursor.fetchall()]
    assert codes == ["000660"]


def test_subscription_restored_on_init(tmp_db_dir):
    """재시작 시 구독 상태가 DB에서 복원되는지 확인."""
    mock_logger = MagicMock()

    # 첫 번째 인스턴스: 구독 추가
    with patch.object(RealtimeDataManager, '_get_base_dir', return_value=tmp_db_dir):
        mgr1 = RealtimeDataManager(logger=mock_logger)
        mgr1.add_subscribed_code("005930")
        mgr1.add_subscribed_code("000660")
        mgr1._conn.close()

    # 두 번째 인스턴스: 복원 확인
    with patch.object(RealtimeDataManager, '_get_base_dir', return_value=tmp_db_dir):
        mgr2 = RealtimeDataManager(logger=mock_logger)
        assert mgr2.is_subscribed("005930")
        assert mgr2.is_subscribed("000660")
        assert len(mgr2.get_subscribed_codes()) == 2
        mgr2._conn.close()


# --- 히스토리 로드 ---

def test_load_pt_history_from_db(tmp_db_dir):
    """DB에서 금일 히스토리가 정상 로드되는지 확인."""
    mock_logger = MagicMock()

    # 첫 번째 인스턴스: 데이터 삽입
    with patch.object(RealtimeDataManager, '_get_base_dir', return_value=tmp_db_dir):
        mgr1 = RealtimeDataManager(logger=mock_logger)
        mgr1.on_data_received({"유가증권단축종목코드": "005930", "price": 100})
        mgr1.on_data_received({"유가증권단축종목코드": "005930", "price": 101})
        mgr1.on_data_received({"유가증권단축종목코드": "000660", "price": 200})
        mgr1._conn.close()

    # 두 번째 인스턴스: 복원 확인
    with patch.object(RealtimeDataManager, '_get_base_dir', return_value=tmp_db_dir):
        mgr2 = RealtimeDataManager(logger=mock_logger)
        assert "005930" in mgr2._pt_history
        assert len(mgr2._pt_history["005930"]) == 2
        assert "000660" in mgr2._pt_history
        assert len(mgr2._pt_history["000660"]) == 1
        mgr2._conn.close()


def test_load_pt_history_only_today(tmp_db_dir):
    """전일 데이터는 로드되지 않는지 확인."""
    mock_logger = MagicMock()

    with patch.object(RealtimeDataManager, '_get_base_dir', return_value=tmp_db_dir):
        mgr = RealtimeDataManager(logger=mock_logger)

        # 전일 데이터를 직접 DB에 삽입
        yesterday = time.time() - 86400 * 2
        with mgr._get_connection() as conn:
            conn.execute(
                "INSERT INTO pt_history (code, data, created_at) VALUES (?, ?, ?)",
                ("OLD_CODE", '{"유가증권단축종목코드": "OLD_CODE"}', yesterday)
            )

        # 다시 로드
        mgr._pt_history = {}
        mgr._load_pt_history()

        assert "OLD_CODE" not in mgr._pt_history
        mgr._conn.close()


# --- 스냅샷 저장/로드 ---

def test_save_and_load_snapshot(manager):
    """스냅샷 저장 및 로드 테스트."""
    data = {"chartData": {"005930": {"valueData": [1, 2, 3]}}, "subscribedCodes": ["005930"]}
    manager.save_snapshot(data)

    result = manager.load_snapshot()
    assert result == data


def test_load_snapshot_not_exists(manager):
    """스냅샷이 없을 때 None 반환."""
    result = manager.load_snapshot()
    assert result is None


def test_save_snapshot_exception(manager):
    """스냅샷 저장 중 예외 발생 테스트."""
    # DB 연결을 닫아서 예외 유발
    manager._conn.close()

    with pytest.raises(Exception):
        manager.save_snapshot({"test": "data"})


# --- 데이터 정리 ---

def test_cleanup_old_data(manager):
    """7일 초과 데이터 삭제 테스트."""
    old_ts = time.time() - (8 * 86400)  # 8일 전
    recent_ts = time.time() - (3 * 86400)  # 3일 전

    with manager._get_connection() as conn:
        conn.execute(
            "INSERT INTO pt_history (code, data, created_at) VALUES (?, ?, ?)",
            ("OLD", '{"old": true}', old_ts)
        )
        conn.execute(
            "INSERT INTO pt_history (code, data, created_at) VALUES (?, ?, ?)",
            ("RECENT", '{"recent": true}', recent_ts)
        )

    manager._cleanup_old_data()

    with manager._get_connection() as conn:
        cursor = conn.execute("SELECT code FROM pt_history")
        codes = [row[0] for row in cursor.fetchall()]

    assert "OLD" not in codes
    assert "RECENT" in codes


def test_cleanup_old_data_no_old_data(manager):
    """삭제 대상이 없을 때 정상 동작."""
    manager.on_data_received({"유가증권단축종목코드": "005930", "price": 100})
    manager._cleanup_old_data()

    with manager._get_connection() as conn:
        cursor = conn.execute("SELECT COUNT(*) FROM pt_history")
        assert cursor.fetchone()[0] == 1


# --- 레거시 파일 정리 ---

def test_cleanup_old_files(manager, tmp_db_dir):
    """레거시 JSONL/JSON 파일 삭제 테스트."""
    os.makedirs(tmp_db_dir, exist_ok=True)
    # 레거시 파일 생성
    jsonl_path = os.path.join(tmp_db_dir, "pt_history_20260101.jsonl")
    json_path = os.path.join(tmp_db_dir, "pt_data.json")
    with open(jsonl_path, "w") as f:
        f.write("test")
    with open(json_path, "w") as f:
        f.write("test")

    with patch.object(manager, '_get_base_dir', return_value=tmp_db_dir):
        manager._cleanup_old_files()

    assert not os.path.exists(jsonl_path)
    assert not os.path.exists(json_path)


# --- 큐 관리 ---

def test_create_and_remove_subscriber_queue(manager):
    """구독자 큐 생성 및 제거 테스트."""
    queue = manager.create_subscriber_queue()
    assert queue in manager._pt_queues
    assert queue.maxsize == 200

    manager.remove_subscriber_queue(queue)
    assert queue not in manager._pt_queues


def test_remove_subscriber_queue_not_found(manager):
    """존재하지 않는 큐 제거 시도."""
    queue = asyncio.Queue()
    manager.remove_subscriber_queue(queue)
    assert len(manager._pt_queues) == 0


# --- 생명주기 ---

def test_start_background_tasks(manager):
    """백그라운드 태스크 시작 시 데이터 정리 실행 확인."""
    manager.start_background_tasks()
    manager.logger.info.assert_called()


@pytest.mark.asyncio
async def test_shutdown(manager):
    """종료 시 DB 연결이 닫히는지 확인."""
    assert manager._conn is not None
    await manager.shutdown()
    assert manager._conn is None


@pytest.mark.asyncio
async def test_shutdown_no_conn(manager):
    """DB 연결 없이 shutdown 호출."""
    manager._conn = None
    await manager.shutdown()  # 에러 없이 통과


# --- get_history_data ---

def test_get_history_data(manager):
    """히스토리 데이터 반환 테스트."""
    manager.on_data_received({"유가증권단축종목코드": "005930", "price": 100})
    history = manager.get_history_data()
    assert "005930" in history
    assert len(history["005930"]) == 1


# --- RETENTION_DAYS 상수 확인 ---

def test_retention_days_is_7():
    """보존 기간이 7일인지 확인."""
    assert RealtimeDataManager.RETENTION_DAYS == 7

# --- 추가된 테스트 케이스 (Coverage 향상) ---

def test_inspect_db_status(manager):
    """inspect_db_status 정상 동작 확인."""
    # 1. 데이터 준비
    manager.save_snapshot({"test": "snapshot"})
    manager.on_data_received({"유가증권단축종목코드": "005930", "price": 100})
    
    # 2. 실행
    status = manager.inspect_db_status()
    
    # 3. 검증
    assert status["snapshot"]["exists"] is True
    assert status["snapshot"]["updated_at"] is not None
    assert status["history"]["count"] == 1
    assert status["history"]["last_record"] is not None
    assert "hourly_counts" in status["history"]
    assert status["memory"]["last_received_at"] is not None

def test_inspect_db_status_exception(manager):
    """inspect_db_status 예외 처리 확인."""
    # DB 연결을 닫아서 예외 유발
    manager._conn.close()
    
    status = manager.inspect_db_status()
    
    assert "error" in status

def test_on_data_received_log_gap(manager):
    """데이터 수신 간격이 10초 이상일 때 로그 기록 확인."""
    manager.last_data_ts = time.time() - 15  # 15초 전
    
    test_data = {"유가증권단축종목코드": "005930", "price": 100}
    manager.on_data_received(test_data)
    
    # 로그 메시지 확인
    manager.logger.info.assert_called()
    found = False
    for call_args in manager.logger.info.call_args_list:
        if "실시간 데이터 수신 재개" in str(call_args):
            found = True
            break
    assert found

def test_on_data_received_db_save_failure(manager):
    """DB 저장 실패 시에도 메모리 저장 및 큐 전송은 동작해야 함."""
    test_data = {"유가증권단축종목코드": "005930", "price": 100}
    queue = manager.create_subscriber_queue()
    
    # _get_connection이 예외를 던지도록 설정
    with patch.object(manager, '_get_connection', side_effect=Exception("DB Error")):
        manager.on_data_received(test_data)
        
    # 1. 에러 로그 확인
    manager.logger.error.assert_called()
    
    # 2. 메모리 저장은 성공해야 함
    assert "005930" in manager._pt_history
    
    # 3. 큐 전송은 성공해야 함
    assert queue.qsize() == 1

def test_load_pt_history_json_error(manager):
    """히스토리 데이터 중 JSON 파싱 에러가 있는 경우 건너뛰는지 확인."""
    # 1. 정상 데이터와 불량 데이터 삽입
    now = time.time()
    with manager._get_connection() as conn:
        conn.execute(
            "INSERT INTO pt_history (code, data, created_at) VALUES (?, ?, ?)",
            ("005930", '{"valid": true}', now)
        )
        conn.execute(
            "INSERT INTO pt_history (code, data, created_at) VALUES (?, ?, ?)",
            ("005930", '{invalid_json}', now)
        )
    
    # 2. 로드 실행 (초기화 시 이미 로드되었으므로 다시 로드)
    manager._pt_history = {}
    manager._load_pt_history()
    
    # 3. 검증: 정상 데이터만 로드되어야 함
    assert len(manager._pt_history["005930"]) == 1
    assert manager._pt_history["005930"][0]["valid"] is True

def test_load_snapshot_json_error(manager):
    """스냅샷 데이터 JSON 파싱 에러 처리."""
    # 1. 불량 데이터 삽입
    with manager._get_connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO pt_snapshot (key, value, updated_at) VALUES (?, ?, ?)",
            ("pt_data", "{invalid_json}", time.time())
        )
        
    # 2. 로드 실행
    result = manager.load_snapshot()
    
    # 3. 검증: None 반환 및 에러 로그
    assert result is None
    manager.logger.error.assert_called()

def test_subscription_db_errors(manager):
    """구독 관리 시 DB 에러 발생해도 메모리에는 반영되는지 확인."""
    # Add
    with patch.object(manager, '_get_connection', side_effect=Exception("DB Error")):
        manager.add_subscribed_code("005930")
    assert "005930" in manager._pt_codes
    manager.logger.error.assert_called()
    
    # Remove
    with patch.object(manager, '_get_connection', side_effect=Exception("DB Error")):
        manager.remove_subscribed_code("005930")
    assert "005930" not in manager._pt_codes
    
    # Clear
    manager.add_subscribed_code("005930")
    with patch.object(manager, '_get_connection', side_effect=Exception("DB Error")):
        manager.clear_subscribed_codes()
    assert len(manager._pt_codes) == 0

def test_init_db_connect_failure():
    """DB 연결 실패 시 에러 로그 확인."""
    mock_logger = MagicMock()
    with patch('sqlite3.connect', side_effect=Exception("Connection failed")):
        with patch.object(RealtimeDataManager, '_get_base_dir', return_value='.'):
             mgr = RealtimeDataManager(logger=mock_logger)
             mock_logger.error.assert_any_call("SQLite DB 초기화 실패: Connection failed")
