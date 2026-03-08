# tests/unit_test/test_db_cache_manager.py
import os
import json
import time
import sqlite3
import concurrent.futures
from typing import Any
import pytest
from unittest.mock import MagicMock, patch
from pydantic import BaseModel
from dataclasses import dataclass
from core.cache.db_cache_manager import DBCacheManager

class PydanticDummy(BaseModel):
    x: int
    y: str

@dataclass
class DataClassDummy:
    a: int
    b: int

    @classmethod
    def from_dict(cls, d):
        return cls(**d)

    def to_dict(self):
        return {"a": self.a, "b": self.b}

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

def test_cleanup_old_files_with_size_limit(tmp_path):
    """용량 제한에 따른 데이터 정리 테스트"""
    mgr = _mk_manager(str(tmp_path))
    
    # 1MB = 1024 * 1024 bytes
    # 600KB 데이터 2개 생성 (총 1.2MB approx)
    large_data = "x" * (600 * 1024) 
    
    # 시간차를 두고 저장 (오래된 것이 삭제되어야 함)
    # 직접 DB에 insert하여 updated_at을 제어
    conn = sqlite3.connect(str(tmp_path / "cache.db"))
    
    now = time.time()
    old_time = now - 100
    new_time = now
    
    # Key 1: Old (should be deleted)
    val1 = json.dumps({"data": large_data})
    conn.execute("INSERT INTO cache (key, value, updated_at) VALUES (?, ?, ?)", 
                 ("key_old", val1, old_time))
                 
    # Key 2: New (should be kept)
    val2 = json.dumps({"data": large_data})
    conn.execute("INSERT INTO cache (key, value, updated_at) VALUES (?, ?, ?)", 
                 ("key_new", val2, new_time))
                 
    conn.commit()
    conn.close()
    
    # max_size_mb=1 (1MB limit)
    # 총 데이터 크기는 약 1.2MB. 제한은 1MB.
    # 오래된 key_old가 삭제되어야 함.
    mgr.cleanup_old_files(days=7, max_size_mb=1)
    
    assert mgr.exists("key_new")
    assert not mgr.exists("key_old")

def test_cleanup_old_files_ohlcv_retention(tmp_path):
    """OHLCV 데이터 별도 보관 기간(1년) 적용 테스트"""
    mgr = _mk_manager(str(tmp_path))
    
    now = time.time()
    day = 86400
    
    # 1. 일반 데이터 (8일 전 -> 삭제 대상)
    mgr.set("normal_old", {"v": 1}, save_to_file=True)
    
    # 2. OHLCV 데이터 (100일 전 -> 유지 대상)
    mgr.set("ohlcv_past_005930", {"v": 2}, save_to_file=True)
    
    # 3. OHLCV 데이터 (400일 전 -> 삭제 대상)
    mgr.set("ohlcv_past_000660", {"v": 3}, save_to_file=True)
    
    # 4. 지표 데이터 (100일 전 -> 유지 대상)
    mgr.set("indicators_chart_005930", {"v": 4}, save_to_file=True)
    
    # DB 조작하여 updated_at 수정
    conn = sqlite3.connect(str(tmp_path / "cache.db"))
    conn.execute("UPDATE cache SET updated_at = ? WHERE key = ?", (now - 8 * day, "normal_old"))
    conn.execute("UPDATE cache SET updated_at = ? WHERE key = ?", (now - 100 * day, "ohlcv_past_005930"))
    conn.execute("UPDATE cache SET updated_at = ? WHERE key = ?", (now - 400 * day, "ohlcv_past_000660"))
    conn.execute("UPDATE cache SET updated_at = ? WHERE key = ?", (now - 100 * day, "indicators_chart_005930"))
    conn.commit()
    conn.close()
    
    mgr.cleanup_old_files(days=7)
    
    assert not mgr.exists("normal_old")          # 7일 지났으므로 삭제됨
    assert mgr.exists("ohlcv_past_005930")       # 100일 지났지만 OHLCV(1년)이므로 유지됨
    assert not mgr.exists("ohlcv_past_000660")   # 400일 지났으므로 삭제됨
    assert mgr.exists("indicators_chart_005930") # 100일 지났지만 지표 데이터이므로 유지됨

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

def test_persistence_across_instances(tmp_path):
    """앱 재실행(인스턴스 재생성) 시 데이터 유지 확인"""
    base_dir = str(tmp_path)
    
    # 1. 첫 번째 인스턴스로 데이터 저장
    mgr1 = _mk_manager(base_dir)
    mgr1.set("persist_key", {"data": "survive"}, save_to_file=True)
    # 연결 닫기 (앱 종료 시뮬레이션)
    mgr1._conn.close()
    
    # 2. 두 번째 인스턴스로 데이터 조회 (앱 재실행 시뮬레이션)
    mgr2 = _mk_manager(base_dir)
    loaded = mgr2.get_raw("persist_key")
    
    assert loaded == {"data": "survive"}

# ----------------------------------------------------------------
# 추가된 테스트 케이스 (커버리지 향상)
# ----------------------------------------------------------------

def test_init_with_none_config(tmp_path):
    """config가 None일 때 load_cache_config 호출 확인"""
    with patch("core.cache.db_cache_manager.load_cache_config") as mock_load:
        mock_load.return_value = {"cache": {"base_dir": str(tmp_path)}}
        mgr = DBCacheManager(config=None)
        assert mgr._base_dir == str(tmp_path)

def test_init_db_exception(tmp_path):
    """DB 초기화 중 예외 발생 시 로깅 테스트"""
    mgr = _mk_manager(str(tmp_path))
    logger = MagicMock()
    mgr.set_logger(logger)
    
    # _init_db를 직접 호출하여 예외 유발
    mgr._conn.close()
    mgr._conn = MagicMock()
    mgr._conn.execute.side_effect = sqlite3.Error("DB Init Error")
    mgr._init_db()

    logger.error.assert_called()
    assert "DB 초기화 실패" in logger.error.call_args[0][0]

def test_get_connection_rollback(tmp_path):
    """트랜잭션 롤백 테스트"""
    mgr = _mk_manager(str(tmp_path))
    
    # Replace real connection with mock
    mgr._conn.close()
    mgr._conn = MagicMock()
    
    with pytest.raises(ValueError):
        with mgr._get_connection():
            raise ValueError("Intentional Error")
            
    mgr._conn.commit.assert_not_called()
    mgr._conn.rollback.assert_called_once()

def test_serialize_complex_types(tmp_path):
    """to_dict, list, dict 등 복합 타입 직렬화 테스트"""
    mgr = _mk_manager(str(tmp_path))
    
    # 1. to_dict method
    dc = DataClassDummy(a=1, b=2)
    assert mgr._serialize(dc) == {"a": 1, "b": 2}
    
    # 2. list/tuple
    data_list = [dc, PydanticDummy(x=3, y="z")]
    serialized_list = mgr._serialize(data_list)
    assert serialized_list == [{"a": 1, "b": 2}, {"x": 3, "y": "z"}]
    
    # 3. dict
    data_dict = {"key": dc}
    serialized_dict = mgr._serialize(data_dict)
    assert serialized_dict == {"key": {"a": 1, "b": 2}}

def test_deserialize_dataclass_and_res_common_response(tmp_path):
    """Dataclass 및 ResCommonResponse 특수 로직 역직렬화 테스트"""
    # ResCommonResponse 흉내 (이름이 중요)
    @dataclass
    class ResCommonResponse:
        rt_cd: str
        data: Any = None
        
        @classmethod
        def from_dict(cls, d):
            return cls(**d)

    mgr = _mk_manager(str(tmp_path), classes=[ResCommonResponse, DataClassDummy])
    
    # Nested structure: ResCommonResponse containing DataClassDummy
    raw_data = {
        "rt_cd": "0",
        "data": {"a": 10, "b": 20}
    }
    
    # _deserialize 호출
    # ResCommonResponse의 data 필드가 재귀적으로 _deserialize 되어야 함
    result = mgr._deserialize(raw_data)
    
    assert isinstance(result, ResCommonResponse)
    assert isinstance(result.data, DataClassDummy)
    assert result.data.a == 10

def test_deserialize_list(tmp_path):
    """리스트 역직렬화 테스트"""
    mgr = _mk_manager(str(tmp_path), classes=[DataClassDummy])
    raw_list = [{"a": 1, "b": 2}, {"a": 3, "b": 4}]
    
    result = mgr._deserialize(raw_list)
    assert isinstance(result, list)
    assert isinstance(result[0], DataClassDummy)
    assert result[1].a == 3

def test_deserialize_fallback_on_error(tmp_path):
    """역직렬화 시도 중 에러 발생 시 딕셔너리로 반환 (혹은 다음 클래스 시도)"""
    # PydanticDummy requires x(int), y(str). Provide wrong types to cause validation error.
    mgr = _mk_manager(str(tmp_path), classes=[PydanticDummy])
    
    raw_data = {"x": "not_int", "y": 123} # Invalid types
    
    # Should fail Pydantic validation and return raw dict (recursive)
    res = mgr._deserialize(raw_data)
    assert isinstance(res, dict)
    assert res["x"] == "not_int"

def test_set_save_to_file_false(tmp_path):
    """save_to_file=False일 때 저장 안 함"""
    mgr = _mk_manager(str(tmp_path))
    mgr.set("no_save", {"a": 1}, save_to_file=False)
    assert not mgr.exists("no_save")

def test_set_exception_logging(tmp_path):
    """set 중 예외 발생 시 로깅"""
    mgr = _mk_manager(str(tmp_path))
    logger = MagicMock()
    mgr.set_logger(logger)
    
    # json.dumps 실패 유도 (직렬화 불가능 객체)
    class Unserializable:
        pass
        
    mgr.set("error_key", Unserializable(), save_to_file=True)
    logger.error.assert_called()
    assert "DB cache 저장 실패" in logger.error.call_args[0][0]

def test_delete_exception_logging(tmp_path):
    """delete 중 예외 발생 시 로깅"""
    mgr = _mk_manager(str(tmp_path))
    logger = MagicMock()
    mgr.set_logger(logger)
    
    mgr._conn.close()
    mgr._conn = MagicMock()
    mgr._conn.execute.side_effect = sqlite3.Error("Delete Error")
    mgr.delete("some_key")
        
    logger.error.assert_called()
    assert "DB cache 삭제 실패" in logger.error.call_args[0][0]

def test_clear_exception_logging(tmp_path):
    """clear 중 예외 발생 시 로깅"""
    mgr = _mk_manager(str(tmp_path))
    logger = MagicMock()
    mgr.set_logger(logger)
    
    mgr._conn.close()
    mgr._conn = MagicMock()
    mgr._conn.execute.side_effect = sqlite3.Error("Clear Error")
    mgr.clear()

    logger.error.assert_called()
    assert "전체 DB 캐시 삭제 실패" in logger.error.call_args[0][0]

def test_cleanup_exception_logging(tmp_path):
    """cleanup 중 예외 발생 시 로깅"""
    mgr = _mk_manager(str(tmp_path))
    logger = MagicMock()
    mgr.set_logger(logger)
    
    mgr._conn.close()
    mgr._conn = MagicMock()
    mgr._conn.execute.side_effect = sqlite3.Error("Cleanup Error")
    mgr.cleanup_old_files()

    logger.error.assert_called()
    assert "DB 캐시 정리 실패" in logger.error.call_args[0][0]

def test_get_raw_exception_logging(tmp_path):
    """get_raw 중 예외 발생 시 로깅"""
    mgr = _mk_manager(str(tmp_path))
    logger = MagicMock()
    mgr.set_logger(logger)
    
    mgr._conn.close()
    mgr._conn = MagicMock()
    mgr._conn.execute.side_effect = sqlite3.Error("Select Error")
    res = mgr.get_raw("key")

    assert res is None
    logger.error.assert_called()
    assert "[DBCache] Load Error" in logger.error.call_args[0][0]

def test_exists_exception_handling(tmp_path):
    """exists 중 예외 발생 시 False 반환"""
    mgr = _mk_manager(str(tmp_path))
    
    mgr._conn.close()
    mgr._conn = MagicMock()
    mgr._conn.execute.side_effect = sqlite3.Error("Exists Error")
    assert mgr.exists("key") is False

class SmallPydantic(BaseModel):
    """ResDailyChartApiItem과 유사한 소수 필드 클래스"""
    stck_bsop_date: str
    stck_oprc: str
    stck_hgpr: str
    stck_lwpr: str
    stck_clpr: str
    acml_vol: str

    def to_dict(self):
        return self.model_dump()


def test_deserialize_best_match_not_first_match(tmp_path):
    """소수 필드 클래스가 대형 dict에 잘못 매칭되지 않는지 테스트

    get_investor_trade_by_stock_daily 캐시 역직렬화 버그 재현:
    투자자 매매동향 데이터(15+필드)가 ResDailyChartApiItem(6필드)로 잘못 변환되던 문제
    """
    mgr = _mk_manager(str(tmp_path), classes=[SmallPydantic])

    investor_trade_data = {
        "stck_bsop_date": "20260306",
        "stck_oprc": "8170",
        "stck_hgpr": "8170",
        "stck_lwpr": "7820",
        "stck_clpr": "8050",
        "acml_vol": "9714",
        "stck_prpr": "8050",
        "prdy_vrss": "-120",
        "prdy_vrss_sign": "5",
        "prdy_ctrt": "-1.47",
        "frgn_ntby_qty": "1234",
        "orgn_ntby_qty": "-5678",
        "prsn_ntby_qty": "4444",
        "frgn_ntby_tr_pbmn": "9940000",
        "orgn_ntby_tr_pbmn": "-45700000",
    }

    result = mgr._deserialize(investor_trade_data)

    # SmallPydantic(6필드) coverage = 6/15 = 40% < 50% → 매칭 안되어야 함
    assert isinstance(result, dict), "투자자 매매동향 데이터가 SmallPydantic으로 잘못 변환됨"
    assert "frgn_ntby_qty" in result
    assert result["frgn_ntby_qty"] == "1234"


def test_deserialize_picks_best_match_among_candidates(tmp_path):
    """여러 후보 클래스 중 가장 높은 coverage를 가진 클래스가 선택되는지 검증"""
    class BigPydantic(BaseModel):
        a: int
        b: int
        c: int
        d: int

    class SmallPydantic2(BaseModel):
        a: int
        b: int

    mgr = _mk_manager(str(tmp_path), classes=[SmallPydantic2, BigPydantic])

    raw = {"a": 1, "b": 2, "c": 3, "d": 4}
    result = mgr._deserialize(raw)
    assert isinstance(result, BigPydantic), f"Expected BigPydantic, got {type(result)}"
    assert result.a == 1 and result.d == 4


def test_del_closes_connection(tmp_path):
    """__del__ 호출 시 연결 종료 확인"""
    mgr = _mk_manager(str(tmp_path))
    # Replace real connection with mock to verify close call
    mgr._conn.close()
    mgr._conn = MagicMock()
    
    mgr.__del__()
    mgr._conn.close.assert_called_once()