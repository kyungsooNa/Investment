import os
import pytest
import sqlite3

from managers.stock_repository import StockRepository, _LRUCache


# --- _LRUCache 테스트 ---

def test_lru_cache_basic():
    """LRU 캐시의 기본 Put/Get 동작을 검증합니다."""
    cache = _LRUCache(capacity=2)
    cache.put("A", 1)
    cache.put("B", 2)
    
    assert cache.get("A") == 1
    assert cache.get("B") == 2
    assert cache.get("C") is None

def test_lru_cache_eviction():
    """캐시 용량을 초과할 때 가장 오래 전에 사용된 항목이 삭제되는지 검증합니다."""
    cache = _LRUCache(capacity=2)
    cache.put("A", 1)
    cache.put("B", 2)
    
    # A를 조회하여 최근 사용 상태로 만듦
    cache.get("A")
    
    # C를 추가하면 용량 초과. B가 가장 오래 전에 사용되었으므로 B가 삭제되어야 함
    cache.put("C", 3)
    
    assert cache.get("A") == 1
    assert cache.get("C") == 3
    assert cache.get("B") is None


# --- StockRepository 테스트 ---

@pytest.fixture
def repo(tmp_path):
    """테스트용 임시 DB를 사용하는 StockRepository 인스턴스 제공."""
    db_path = str(tmp_path / "test_stocks.db")
    repository = StockRepository(db_path=db_path)
    yield repository
    repository.close()


def test_init_creates_db_and_table(repo):
    """DB 초기화 시 ohlcv 테이블이 정상적으로 생성되는지 확인합니다."""
    assert os.path.exists(repo._db_path)
    
    with repo._get_connection() as conn:
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='ohlcv'")
        table_exists = cursor.fetchone() is not None
        assert table_exists


def test_upsert_ohlcv(repo):
    """여러 건의 OHLCV 데이터가 정상적으로 삽입 및 갱신되는지 테스트합니다."""
    records = [
        {"code": "005930", "date": "20250101", "open": 100, "high": 110, "low": 90, "close": 105, "volume": 1000},
        {"code": "005930", "date": "20250102", "open": 105, "high": 120, "low": 100, "close": 115, "volume": 2000},
    ]
    
    repo.upsert_ohlcv(records)
    
    with repo._get_connection() as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.execute("SELECT * FROM ohlcv WHERE code='005930' ORDER BY date ASC")
        rows = cursor.fetchall()
        
        assert len(rows) == 2
        assert rows[0]["date"] == "20250101"
        assert rows[0]["close"] == 105
        assert rows[1]["date"] == "20250102"
        assert rows[1]["close"] == 115

    # 동일 날짜 데이터로 업데이트 (INSERT OR REPLACE)
    updated_record = [{"code": "005930", "date": "20250102", "open": 105, "high": 120, "low": 100, "close": 118, "volume": 2500}]
    repo.upsert_ohlcv(updated_record)
    
    with repo._get_connection() as conn:
        cursor = conn.execute("SELECT close, volume FROM ohlcv WHERE code='005930' AND date='20250102'")
        row = cursor.fetchone()
        assert row[0] == 118
        assert row[1] == 2500


def test_get_stock_data_db_read_and_cache(repo):
    """DB에서 데이터를 읽어와서 올바르게 오름차순 정렬 후 반환하고, 메모리에 캐싱하는지 확인합니다."""
    records = [
        {"code": "005930", "date": "20250101", "open": 10, "high": 20, "low": 5, "close": 15, "volume": 100},
        {"code": "005930", "date": "20250102", "open": 15, "high": 25, "low": 10, "close": 20, "volume": 200},
        {"code": "005930", "date": "20250103", "open": 20, "high": 30, "low": 15, "close": 25, "volume": 300},
    ]
    repo.upsert_ohlcv(records)
    
    # limit=2 로 조회 시 최신 2개(0102, 0103)를 가져와서 과거순(오름차순)으로 반환해야 함
    result = repo.get_stock_data("005930", ohlcv_limit=2)
    
    assert result is not None
    assert result["code"] == "005930"
    assert len(result["ohlcv"]) == 2
    assert result["ohlcv"][0]["date"] == "20250102"
    assert result["ohlcv"][1]["date"] == "20250103"
    assert "last_updated" in result
    
    # 메모리 캐시에 정상적으로 올라갔는지 확인
    cached = repo._stocks_cache.get("005930")
    assert cached is not None
    assert cached["code"] == "005930"


def test_get_stock_data_not_found(repo):
    """존재하지 않는 종목 조회 시 None을 반환하는지 테스트합니다."""
    result = repo.get_stock_data("999999")
    assert result is None


def test_update_realtime_data_interface(repo):
    """update_realtime_data 껍데기 인터페이스가 호출 시 예외를 발생시키지 않는지 테스트합니다."""
    try:
        repo.update_realtime_data("005930", 75000, 1000)
    except Exception as e:
        pytest.fail(f"update_realtime_data 호출 중 예외 발생: {e}")


def test_upsert_ohlcv_empty_list(repo):
    """빈 리스트를 upsert_ohlcv에 넘겼을 때 예외 없이 무시되는지 테스트합니다."""
    try:
        repo.upsert_ohlcv([])
    except Exception as e:
        pytest.fail(f"빈 리스트 upsert 중 예외 발생: {e}")