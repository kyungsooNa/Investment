import os
import csv
import json
import sqlite3
import pytest
from unittest.mock import MagicMock, patch

from scheduler.strategy_scheduler_store import StrategySchedulerStore
import scheduler.strategy_scheduler_store as store_module


class MockSignalRecord:
    """테스트를 위한 SignalRecord 모의 객체"""
    def __init__(self, strategy_name, code, name, action, price, qty, return_rate, reason, timestamp, api_success):
        self.strategy_name = strategy_name
        self.code = code
        self.name = name
        self.action = action
        self.price = price
        self.qty = qty
        self.return_rate = return_rate
        self.reason = reason
        self.timestamp = timestamp
        self.api_success = api_success


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test_scheduler.db")


@pytest.fixture
def mock_logger():
    return MagicMock()


@pytest.fixture
def store(db_path, mock_logger):
    store = StrategySchedulerStore(db_path=db_path, logger=mock_logger)
    yield store
    store.close()


def test_init_and_close(db_path, mock_logger):
    """DB 초기화 및 close 정상 동작 확인"""
    store = StrategySchedulerStore(db_path=db_path, logger=mock_logger)
    assert os.path.exists(db_path)
    
    # 커넥션 닫기
    store.close()
    assert store._conn is None
    
    # 이미 닫힌 상태에서 호출해도 문제없어야 함
    store.close()


def test_append_and_load_signal_history(store):
    """시그널 이력 삽입 및 최신순 로딩 테스트"""
    record1 = MockSignalRecord("S1", "001", "N1", "BUY", 100, 10, 1.5, "R1", "2025-01-01 10:00", True)
    record2 = MockSignalRecord("S2", "002", "N2", "SELL", 200, 20, -1.0, "R2", "2025-01-01 11:00", False)
    
    store.append_signal(record1)
    store.append_signal(record2)
    
    history = store.load_signal_history(limit=2)
    
    assert len(history) == 2
    # 결과는 가장 오래된 것이 먼저 오도록 역순(reversed)으로 반환됨
    assert history[0]["code"] == "001"
    assert history[0]["api_success"] is True
    assert history[1]["code"] == "002"
    assert history[1]["api_success"] is False

    # Limit 동작 테스트
    history_limit1 = store.load_signal_history(limit=1)
    assert len(history_limit1) == 1
    assert history_limit1[0]["code"] == "002"  # 가장 최근의 것


def test_save_load_clear_state(store):
    """스케줄러 상태 저장, 로드, 삭제 테스트"""
    # 초기 상태는 None
    assert store.load_state() is None
    
    # 상태 저장
    state_data = {"is_running": True, "active_count": 5}
    store.save_state(state_data)
    
    # 로드 검증
    loaded = store.load_state()
    assert loaded == state_data
    
    # 덮어쓰기 검증
    new_state_data = {"is_running": False}
    store.save_state(new_state_data)
    assert store.load_state() == new_state_data
    
    # 삭제 검증
    store.clear_state()
    assert store.load_state() is None


def test_load_state_invalid_json(store):
    """상태 데이터가 잘못된 JSON 문자열일 경우 None 반환 확인"""
    with store._lock:
        store._conn.execute(
            "INSERT OR REPLACE INTO scheduler_state (key, value) VALUES ('state', ?)",
            ("{ invalid json }",)
        )
        store._conn.commit()
        
    assert store.load_state() is None


def test_save_and_load_keyed_value(store):
    """임의 키 문자열 저장/조회 및 미존재 키 None 반환 확인"""
    assert store.load_keyed("missing") is None

    store.save_keyed("last_run", "2026-04-22 09:00:00")

    assert store.load_keyed("last_run") == "2026-04-22 09:00:00"


def test_migrate_csv_success(tmp_path, mock_logger, monkeypatch):
    """CSV 레거시 파일 마이그레이션 성공 테스트"""
    csv_path = tmp_path / "signal_history.csv"
    monkeypatch.setattr(store_module, "_LEGACY_SIGNAL_CSV", str(csv_path))
    
    # 임시 CSV 파일 생성
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "strategy_name", "code", "name", "action", "price", "qty",
            "return_rate", "reason", "timestamp", "api_success"
        ])
        writer.writeheader()
        writer.writerow({
            "strategy_name": "MigrateStrat", "code": "005930", "name": "삼성전자",
            "action": "BUY", "price": "70000", "qty": "10", "return_rate": "1.5",
            "reason": "Test", "timestamp": "2025-01-01", "api_success": "True"
        })
        # 파싱 오류 대처 케이스 (qty 부재, return_rate 오류, api_success False 등)
        writer.writerow({
            "strategy_name": "MigrateStrat2", "code": "000660", "name": "SK하이닉스",
            "action": "SELL", "price": "100000", "qty": "", "return_rate": "invalid_float",
            "reason": "", "timestamp": "2025-01-02", "api_success": "False"
        })

    db_path = str(tmp_path / "test_mig.db")
    store = StrategySchedulerStore(db_path=db_path, logger=mock_logger)

    history = store.load_signal_history()
    assert len(history) == 2
    
    # 정상 레코드 확인
    assert history[0]["code"] == "005930"
    assert history[0]["qty"] == 10
    assert history[0]["return_rate"] == 1.5
    assert history[0]["api_success"] is True
    
    # 파싱 예외 복구 레코드 확인
    assert history[1]["code"] == "000660"
    assert history[1]["qty"] == 1  # qty가 빈 값일 경우 기본값 1
    assert history[1]["return_rate"] is None  # 잘못된 float일 경우 None
    assert history[1]["api_success"] is False

    # 마이그레이션 후 파일 이름 변경 확인
    assert not os.path.exists(str(csv_path))
    assert os.path.exists(str(csv_path) + ".migrated")


def test_migrate_csv_logs_warning_for_invalid_row(tmp_path, mock_logger, monkeypatch):
    """개별 CSV 행 삽입 실패 시 warning만 남기고 나머지 처리는 계속한다."""
    csv_path = tmp_path / "signal_history.csv"
    monkeypatch.setattr(store_module, "_LEGACY_SIGNAL_CSV", str(csv_path))

    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "strategy_name", "code", "name", "action", "price", "qty",
            "return_rate", "reason", "timestamp", "api_success"
        ])
        writer.writeheader()
        writer.writerow({
            "strategy_name": "Broken", "code": "005930", "name": "삼성전자",
            "action": "BUY", "price": "invalid_price", "qty": "2", "return_rate": "",
            "reason": "bad", "timestamp": "2025-01-01", "api_success": "True"
        })

    StrategySchedulerStore(db_path=str(tmp_path / "warn.db"), logger=mock_logger)

    mock_logger.warning.assert_called_once()
    assert os.path.exists(str(csv_path) + ".migrated")


def test_migrate_csv_logs_error_on_file_read_failure(tmp_path, mock_logger, monkeypatch):
    """CSV 파일 자체를 읽지 못하면 error를 로깅한다."""
    csv_path = tmp_path / "signal_history.csv"
    monkeypatch.setattr(store_module, "_LEGACY_SIGNAL_CSV", str(csv_path))

    store = StrategySchedulerStore(db_path=str(tmp_path / "csv_fail.db"), logger=mock_logger)
    csv_path.write_text("dummy", encoding="utf-8")

    with patch("builtins.open", side_effect=OSError("read fail")):
        store._migrate_csv()

    mock_logger.error.assert_called()


def test_migrate_json_success(tmp_path, mock_logger, monkeypatch):
    """JSON 레거시 상태 파일 마이그레이션 성공 테스트"""
    json_path = tmp_path / "scheduler_state.json"
    monkeypatch.setattr(store_module, "_LEGACY_STATE_JSON", str(json_path))
    
    state_data = {"test_migrated": True}
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(state_data, f)
        
    db_path = str(tmp_path / "test_mig_json.db")
    store = StrategySchedulerStore(db_path=db_path, logger=mock_logger)
    
    assert store.load_state() == state_data
    
    assert not os.path.exists(str(json_path))
    assert os.path.exists(str(json_path) + ".migrated")


def test_migrate_json_skip_when_db_exists(tmp_path, mock_logger, monkeypatch):
    """DB에 이미 상태가 있으면 JSON 내용은 넣지 않고 파일명만 변경한다."""
    json_path = tmp_path / "scheduler_state.json"
    monkeypatch.setattr(store_module, "_LEGACY_STATE_JSON", str(json_path))

    db_path = str(tmp_path / "test_skip_json.db")
    store = StrategySchedulerStore(db_path=db_path, logger=mock_logger)
    store.save_state({"current": True})
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({"legacy": True}, f)

    store._migrate_json()

    assert store.load_state() == {"current": True}
    assert not os.path.exists(str(json_path))
    assert os.path.exists(str(json_path) + ".migrated")


def test_migrate_json_logs_error_on_invalid_json(tmp_path, mock_logger, monkeypatch):
    """JSON 파싱 실패 시 error를 로깅하고 예외를 삼킨다."""
    json_path = tmp_path / "scheduler_state.json"
    monkeypatch.setattr(store_module, "_LEGACY_STATE_JSON", str(json_path))
    json_path.write_text("{ invalid json", encoding="utf-8")

    StrategySchedulerStore(db_path=str(tmp_path / "bad_json.db"), logger=mock_logger)

    mock_logger.error.assert_called()


def test_migrate_skip_when_db_exists(store, tmp_path, mock_logger, monkeypatch):
    """DB에 이미 데이터가 있을 때 CSV 파일 내용이 마이그레이션되지 않고 파일명만 변경되는지 테스트"""
    csv_path = tmp_path / "signal_history.csv"
    monkeypatch.setattr(store_module, "_LEGACY_SIGNAL_CSV", str(csv_path))
    
    with open(csv_path, "w", encoding="utf-8") as f:
        f.write("dummy,data\n1,2")
        
    # store의 _migrate_csv 직접 호출 (초기화 시 호출되지만 재호출로 상황 재현)
    store.append_signal(MockSignalRecord("S1", "1", "N1", "B", 1, 1, 1, "R", "T", True))
    store._migrate_csv()
    
    # 이미 데이터가 있으므로 내용 파싱 없이 파일 이름만 변경됨
    assert not os.path.exists(str(csv_path))
    assert os.path.exists(str(csv_path) + ".migrated")
