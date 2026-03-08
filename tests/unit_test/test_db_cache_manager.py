# tests/unit_test/test_db_cache_manager.py
import os
import json
import time
import sqlite3
import concurrent.futures
from typing import Any
import pytest
from unittest.mock import MagicMock
from pydantic import BaseModel
from core.cache.db_cache_manager import DBCacheManager

class PydanticDummy(BaseModel):
    x: int
    y: str

def _mk_manager(base_dir: str, classes: list[type] | None = None) -> DBCacheManager:
    cfg = {"cache": {"base_dir": base_dir, "deserializable_classes": []}}
    m = DBCacheManager(config=cfg)
    if classes is not None:
        m._deserializable_classes = classes
    return m

def test_db_init_creates_file(tmp_path):
    """DB 파일 생성 확인"""
    mgr = _mk_manager(str(tmp_path))
    db_path = tmp_path / "cache.db"
    assert db_path.exists()

def test_set_and_get_raw(tmp_path):
    """데이터 저장 및 조회 테스트"""
    mgr = _mk_manager(str(tmp_path))
    key = "test_key"
    data = {"data": {"foo": "bar"}}
    
    mgr.set(key, data, save_to_file=True)
    
    # DB 직접 확인
    conn = sqlite3.connect(str(tmp_path / "cache.db"))
    cursor = conn.execute("SELECT value FROM cache WHERE key=?", (key,))
    row = cursor.fetchone()
    assert row is not None
    assert json.loads(row[0]) == data
    conn.close()
    
    # get_raw 확인
    loaded = mgr.get_raw(key)
    assert loaded == data

def test_delete(tmp_path):
    """데이터 삭제 테스트"""
    mgr = _mk_manager(str(tmp_path))
    key = "del_key"
    mgr.set(key, {"val": 1}, save_to_file=True)
    
    assert mgr.exists(key)
    mgr.delete(key)
    assert not mgr.exists(key)

def test_clear(tmp_path):
    """전체 삭제 테스트"""
    mgr = _mk_manager(str(tmp_path))
    mgr.set("k1", {"v": 1}, save_to_file=True)
    mgr.set("k2", {"v": 2}, save_to_file=True)
    
    mgr.clear()
    
    assert not mgr.exists("k1")
    assert not mgr.exists("k2")

def test_cleanup_old_files(tmp_path):
    """오래된 데이터 정리 테스트"""
    mgr = _mk_manager(str(tmp_path))
    
    # 1. 최신 데이터
    mgr.set("fresh", {"v": 1}, save_to_file=True)
    
    # 2. 오래된 데이터 (직접 DB 조작)
    past = time.time() - (8 * 86400)
    conn = sqlite3.connect(str(tmp_path / "cache.db"))
    conn.execute("INSERT INTO cache (key, value, updated_at) VALUES (?, ?, ?)", 
                 ("old", json.dumps({"v": 2}), past))
    conn.commit()
    conn.close()
    
    mgr.cleanup_old_files(days=7)
    
    assert mgr.exists("fresh")
    assert not mgr.exists("old")

def test_pydantic_serialization(tmp_path):
    """Pydantic 모델 직렬화/역직렬화 테스트"""
    mgr = _mk_manager(str(tmp_path), classes=[PydanticDummy])
    obj = PydanticDummy(x=10, y="test")
    
    # 저장 (직렬화)
    mgr.set("pydantic", {"data": obj}, save_to_file=True)
    
    # 로드 (역직렬화)
    loaded = mgr.get_raw("pydantic")
    assert isinstance(loaded["data"], PydanticDummy)
    assert loaded["data"].x == 10

def test_set_logger(tmp_path):
    """로거 설정 및 로그 출력 테스트"""
    mgr = _mk_manager(str(tmp_path))
    logger = MagicMock()
    mgr.set_logger(logger)
    
    mgr.set("log_test", {}, save_to_file=True)
    logger.debug.assert_called()

def test_singleton_connection_reuse(tmp_path):
    """싱글톤 커넥션이 재사용되는지 검증"""
    mgr = _mk_manager(str(tmp_path))
    
    # _get_connection은 context manager이므로 yield된 connection을 확인
    with mgr._get_connection() as conn1:
        pass
        
    with mgr._get_connection() as conn2:
        pass
        
    # 같은 객체여야 함 (DBCacheManager 인스턴스 내에서)
    assert conn1 is conn2
    assert conn1 is mgr._conn

def test_multithread_safety(tmp_path):
    """멀티스레드 환경에서 DB 작업이 안전한지 검증"""
    mgr = _mk_manager(str(tmp_path))
    
    def worker(idx):
        key = f"key_{idx}"
        value = {"data": idx}
        # 쓰기
        mgr.set(key, value, save_to_file=True)
        # 읽기
        loaded = mgr.get_raw(key)
        return loaded == value

    workers_count = 50
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(worker, i) for i in range(workers_count)]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]
    
    assert all(results)
    
    # 최종적으로 DB에 모든 키가 있는지 확인
    with mgr._get_connection() as conn:
        cursor = conn.execute("SELECT count(*) FROM cache")
        count = cursor.fetchone()[0]
        assert count == workers_count