import os
import pytest
import pytest_asyncio
import sqlite3
import aiosqlite
import time
from unittest.mock import patch, MagicMock, AsyncMock
from contextlib import contextmanager

from repositories.stock_repository import StockRepository, _LRUCache


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

def test_lru_cache_stats():
    """LRU 캐시의 get_stats 메서드 (expand=True/False) 검증"""
    cache = _LRUCache(capacity=2)
    cache.put("A", {"ohlcv": [1, 2], "current_price_data": {}, "last_updated": 100, "price_updated_at": 101})
    cache.put("B", "NotADict")
    
    cache.get("A", caller="tester", item_type="ohlcv")
    cache.get("C") # miss
    
    # expand=False
    stats = cache.get_stats(expand=False)
    assert stats["hits"] == 1
    assert stats["misses"] == 1
    assert "tester" in stats["callers"]
    assert "items" not in stats
    
    # expand=True
    stats_expanded = cache.get_stats(expand=True)
    assert "items" in stats_expanded
    items = stats_expanded["items"]
    assert items[0]["code"] == "A"
    assert items[0]["has_ohlcv"] is True
    assert items[0]["ohlcv_length"] == 2
    assert items[1]["code"] == "B"
    assert "has_ohlcv" not in items[1]

# --- StockRepository 테스트 ---

@pytest_asyncio.fixture
async def repo(tmp_path):
    """테스트용 임시 DB를 사용하는 StockRepository 인스턴스 제공."""
    db_path = str(tmp_path / "test_stocks.db")
    repository = StockRepository(db_path=db_path)
    yield repository
    await repository.close()


@pytest.mark.asyncio
async def test_init_creates_db_and_table(repo):
    """DB 초기화 시 ohlcv 테이블이 정상적으로 생성되는지 확인합니다."""
    assert os.path.exists(repo._db_path)
    
    async with repo._get_connection() as conn:
        async with conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='ohlcv'") as cursor:
            row = await cursor.fetchone()
            assert row is not None


@pytest.mark.asyncio
async def test_upsert_ohlcv(repo):
    """여러 건의 OHLCV 데이터가 정상적으로 삽입 및 갱신되는지 테스트합니다."""
    records = [
        {"code": "005930", "date": "20250101", "open": 100, "high": 110, "low": 90, "close": 105, "volume": 1000},
        {"code": "005930", "date": "20250102", "open": 105, "high": 120, "low": 100, "close": 115, "volume": 2000},
    ]
    
    await repo.upsert_ohlcv(records)
    
    async with repo._get_connection() as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute("SELECT * FROM ohlcv WHERE code='005930' ORDER BY date ASC") as cursor:
            rows = await cursor.fetchall()
        
        assert len(rows) == 2
        assert rows[0]["date"] == "20250101"
        assert rows[0]["close"] == 105
        assert rows[1]["date"] == "20250102"
        assert rows[1]["close"] == 115
        conn.row_factory = None

    # 동일 날짜 데이터로 업데이트 (INSERT OR REPLACE)
    updated_record = [{"code": "005930", "date": "20250102", "open": 105, "high": 120, "low": 100, "close": 118, "volume": 2500}]
    await repo.upsert_ohlcv(updated_record)
    
    async with repo._get_connection() as conn:
        async with conn.execute("SELECT close, volume FROM ohlcv WHERE code='005930' AND date='20250102'") as cursor:
            row = await cursor.fetchone()
            assert row[0] == 118
            assert row[1] == 2500


@pytest.mark.asyncio
async def test_get_stock_data_db_read_and_cache(repo):
    """DB에서 데이터를 읽어와서 올바르게 오름차순 정렬 후 반환하고, 메모리에 캐싱하는지 확인합니다."""
    records = [
        {"code": "005930", "date": "20250101", "open": 10, "high": 20, "low": 5, "close": 15, "volume": 100},
        {"code": "005930", "date": "20250102", "open": 15, "high": 25, "low": 10, "close": 20, "volume": 200},
        {"code": "005930", "date": "20250103", "open": 20, "high": 30, "low": 15, "close": 25, "volume": 300},
    ]
    await repo.upsert_ohlcv(records)
    
    # limit=2 로 조회 시 최신 2개(0102, 0103)를 가져와서 과거순(오름차순)으로 반환해야 함
    result = await repo.get_stock_data("005930", ohlcv_limit=2)
    
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


@pytest.mark.asyncio
async def test_get_stock_data_not_found(repo):
    """존재하지 않는 종목 조회 시 None을 반환하는지 테스트합니다."""
    result = await repo.get_stock_data("999999")
    assert result is None


def test_current_price_caching(repo):
    """현재가 데이터 단기 캐싱(set_current_price, get_current_price) 및 TTL 만료 동작을 검증합니다."""
    price_data = {"output": {"stck_prpr": "80000", "acml_vol": "1000"}}
    repo.set_current_price("005930", price_data)

    # TTL 이내 조회
    cached = repo.get_current_price("005930", max_age_sec=3.0)
    assert cached is not None
    assert cached["output"]["stck_prpr"] == "80000"

    # 시간 조작으로 TTL 만료 시뮬레이션
    repo._stocks_cache.get("005930")["price_updated_at"] = time.time() - 5.0
    expired = repo.get_current_price("005930", max_age_sec=3.0)
    assert expired is None


def test_update_realtime_data_empty_cache(repo):
    """캐시가 비어있는 상태에서 실시간 틱 데이터 반영 시 현재가 구조가 자동 생성되는지 검증합니다."""
    repo.update_realtime_data("005930", 81000.0, 500)
    
    cached = repo.get_current_price("005930")
    assert cached is not None
    assert cached["output"]["stck_prpr"] == "81000"
    assert cached["output"]["acml_vol"] == "500"


def test_update_realtime_data_updates_ohlcv(repo):
    """실시간 틱 데이터 수신 시 당일 OHLCV 캔들의 고/저/종가/거래량이 갱신되는지 검증합니다."""
    initial_data = {
        "code": "005930",
        "ohlcv": [
            {"date": "20250101", "open": 70000, "high": 71000, "low": 69000, "close": 70500, "volume": 1000},
            {"date": "20250102", "open": 71000, "high": 72000, "low": 70500, "close": 71500, "volume": 2000}
        ]
    }
    repo._stocks_cache.put("005930", initial_data)
    
    # 틱 업데이트 1: 현재가 상승 (고가 갱신)
    repo.update_realtime_data("005930", 72500.0, 2500)
    last_candle = repo._stocks_cache.get("005930")["ohlcv"][-1]
    assert last_candle["close"] == 72500.0
    assert last_candle["high"] == 72500.0
    assert last_candle["low"] == 70500
    assert last_candle["volume"] == 2500
    
    # 틱 업데이트 2: 현재가 하락 (저가 갱신)
    repo.update_realtime_data("005930", 70000.0, 3000)
    last_candle = repo._stocks_cache.get("005930")["ohlcv"][-1]
    assert last_candle["close"] == 70000.0
    assert last_candle["high"] == 72500.0
    assert last_candle["low"] == 70000.0
    assert last_candle["volume"] == 3000


@pytest.mark.asyncio
async def test_upsert_ohlcv_empty_list(repo):
    """빈 리스트를 upsert_ohlcv에 넘겼을 때 예외 없이 무시되는지 테스트합니다."""
    try:
        await repo.upsert_ohlcv([])
    except Exception as e:
        pytest.fail(f"빈 리스트 upsert 중 예외 발생: {e}")


@pytest.mark.asyncio
async def test_get_latest_daily_snapshot_none_when_empty(repo):
    """daily_prices가 비었을 때 get_latest_daily_snapshot은 None을 반환합니다."""
    assert await repo.get_latest_daily_snapshot("005930") is None


@pytest.mark.asyncio
async def test_get_latest_daily_snapshot_output_structure(repo):
    """get_latest_daily_snapshot 반환값이 현재가 API 포맷 {'output': {...}} 구조인지 검증합니다."""
    await repo.upsert_daily_snapshot("20260318", [{
        "code": "005930", "name": "삼성전자", "market": "KOSPI",
        "current_price": 70000, "open_price": 69000, "high_price": 71000,
        "low_price": 68500, "prev_close": 69500, "change_price": 500,
        "change_sign": "2", "change_rate": "0.72", "volume": 1000000,
        "trading_value": 70000000000, "market_cap": 420000000000,
        "per": 12.5, "pbr": 1.3, "eps": 5600.0,
        "w52_high": 80000, "w52_low": 55000,
    }])

    result = await repo.get_latest_daily_snapshot("005930")
    assert result is not None
    assert "output" in result
    assert "_source" in result
    assert "_trade_date" in result
    assert result["output"]["stck_prpr"] == "70000"
    assert result["output"]["hts_kor_isnm"] == "삼성전자"


# --- DB 예외 및 Edge Case (커버리지 보완) ---

def test_stock_repository_init_db_error(tmp_path):
    with patch("sqlite3.connect", side_effect=Exception("DB Con Error")):
        repo = StockRepository(db_path=str(tmp_path / "err.db"))
        assert repo._conn is None

@pytest.mark.asyncio
async def test_stock_repository_get_connection_rollback(repo):
    m_conn = AsyncMock()
    repo._conn = m_conn

    with pytest.raises(ValueError):
        async with repo._get_connection() as conn:
            raise ValueError("Rollback Trigger")
    
    # 예외 발생 시 트랜잭션 롤백이 호출되었는지 행위 검증
    m_conn.rollback.assert_awaited_once()

@pytest.mark.asyncio
async def test_stock_repository_upsert_ohlcv_db_error(repo):
    m_conn = AsyncMock()
    m_conn.executemany.side_effect = Exception("Exec Error")
    repo._conn = m_conn
    # 에러가 발생해도 프로그램이 죽지 않고 로깅만 해야 함
    await repo.upsert_ohlcv([{"code": "005930", "date": "20250101", "open": 100, "high": 100, "low": 100, "close": 100, "volume": 100}])

@pytest.mark.asyncio
async def test_stock_repository_get_stock_data_db_error(repo):
    m_conn = AsyncMock()
    m_conn.execute.side_effect = Exception("Select Error")
    repo._conn = m_conn
    
    result = await repo.get_stock_data("005930")
    assert result is None

def test_stock_repository_update_realtime_data_output_object(repo):
    """update_realtime_data: API 응답이 Dict가 아니라 Object 형태일 때 setattr 정상 동작 확인"""
    class DummyOutput:
        pass
    
    output_obj = DummyOutput()
    cached_data = {
        "code": "005930",
        "current_price_data": {"output": output_obj},
        "ohlcv": [{"date": "20250101", "close": 1000, "high": 1100, "low": 900, "volume": 100}]
    }
    repo._stocks_cache.put("005930", cached_data)
    
    repo.update_realtime_data("005930", 1200.0, 50)
    
    assert getattr(output_obj, "stck_prpr") == "1200"
    assert getattr(output_obj, "acml_vol") == "50"

@pytest.mark.asyncio
async def test_stock_repository_get_ohlcv_summary(repo):
    await repo.upsert_ohlcv([
        {"code": "005930", "date": "20250101", "open": 100, "high": 100, "low": 100, "close": 100, "volume": 100},
        {"code": "005930", "date": "20250102", "open": 100, "high": 100, "low": 100, "close": 100, "volume": 100}
    ])
    summary = await repo.get_ohlcv_summary("005930")
    assert summary["count"] == 2
    assert summary["latest_date"] == "20250102"
    
    m_conn = AsyncMock()
    m_conn.execute.side_effect = Exception("DB Error")
    repo._conn = m_conn
    
    err_summary = await repo.get_ohlcv_summary("005930")
    assert err_summary["count"] == 0

@pytest.mark.asyncio
async def test_stock_repository_get_ohlcv_max_trading_days(repo):
    await repo.upsert_ohlcv([
        {"code": "005930", "date": "20250101", "open": 100, "high": 100, "low": 100, "close": 100, "volume": 100},
        {"code": "000660", "date": "20250102", "open": 100, "high": 100, "low": 100, "close": 100, "volume": 100}
    ])
    assert await repo.get_ohlcv_max_trading_days() == 2
    
    m_conn = AsyncMock()
    m_conn.execute.side_effect = Exception("DB Error")
    repo._conn = m_conn
    
    assert await repo.get_ohlcv_max_trading_days() == 0

@pytest.mark.asyncio
async def test_stock_repository_upsert_daily_snapshot_error(repo):
    m_conn = AsyncMock()
    m_conn.executemany.side_effect = Exception("DB Error")
    repo._conn = m_conn
    await repo.upsert_daily_snapshot("20250101", [{"code": "005930"}])

@pytest.mark.asyncio
async def test_stock_repository_get_prices_by_date_error(repo):
    m_conn = AsyncMock()
    m_conn.execute.side_effect = Exception("DB Error")
    repo._conn = m_conn
    assert await repo.get_prices_by_date("20250101") == []

@pytest.mark.asyncio
async def test_stock_repository_get_price_history_error(repo):
    m_conn = AsyncMock()
    m_conn.execute.side_effect = Exception("DB Error")
    repo._conn = m_conn
    assert await repo.get_price_history("005930") == []

@pytest.mark.asyncio
async def test_stock_repository_get_latest_trade_date_error(repo):
    m_conn = AsyncMock()
    m_conn.execute.side_effect = Exception("DB Error")
    repo._conn = m_conn
    assert await repo.get_latest_trade_date() is None

def test_stock_repository_get_cache_stats(repo):
    stats = repo.get_cache_stats(expand=True)
    assert "hits" in stats
    assert "items" in stats