import os
import time
import json
import logging
from unittest.mock import MagicMock
import pytest

from core.logger import get_cache_event_logger
import core.logger
from core.loggers.cache_event_logger import CacheEventLogger
from core.loggers.log_config import reset_log_timestamp_for_test
from core.loggers.size_time_rotating_file_handler import SizeTimeRotatingFileHandler
from core.loggers.json_formatter import JsonFormatter
from repositories.cache import _LRUCache, _LFUCache
from repositories.stock_price_repository import StockPriceRepository
from repositories.stock_ohlcv_repository import StockOhlcvRepository

@pytest.fixture
def cache_logger_setup(tmp_path):
    reset_log_timestamp_for_test()

    existing = logging.getLogger("cache_event")
    for h in existing.handlers[:]:
        h.close()
        existing.removeHandler(h)

    log_dir = tmp_path / "logs"
    cache_logger = get_cache_event_logger(log_dir=str(log_dir))

    yield cache_logger, log_dir / "cache"

    inner = logging.getLogger("cache_event")
    for h in inner.handlers[:]:
        h.close()
        inner.removeHandler(h)
        
    for listener in core.logger._active_listeners[:]:
        listener.stop()
    core.logger._active_listeners.clear()


def _flush_cache_logger():
    for listener in core.logger._active_listeners:
        listener.queue.join()
        for h in listener.handlers:
            h.flush()


def _read_json_lines(log_dir):
    files = list(log_dir.glob("*.log.json"))
    assert len(files) == 1
    with open(files[0], encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def test_get_cache_event_logger_creates_file(cache_logger_setup):
    cache_logger, cache_log_dir = cache_logger_setup

    assert isinstance(cache_logger, CacheEventLogger)
    assert cache_log_dir.is_dir()

    inner = logging.getLogger("cache_event")
    assert not inner.propagate
    assert inner.level == logging.DEBUG
    assert len(inner.handlers) == 1
    
    handler = None
    for listener in core.logger._active_listeners:
        for h in listener.handlers:
            if isinstance(h, SizeTimeRotatingFileHandler):
                handler = h
                break
        if handler: break
                
    assert isinstance(handler, SizeTimeRotatingFileHandler)
    assert isinstance(handler.formatter, JsonFormatter)

    cache_logger.log_ohlcv_miss("005930", "test")
    _flush_cache_logger()

    log_files = list(cache_log_dir.glob("*_cache_*.log.json"))
    assert len(log_files) == 1


def test_get_cache_event_logger_returns_same_logger_on_second_call(tmp_path):
    reset_log_timestamp_for_test()
    existing = logging.getLogger("cache_event")
    for h in existing.handlers[:]:
        h.close()
        existing.removeHandler(h)

    log_dir = tmp_path / "logs"
    logger1 = get_cache_event_logger(log_dir=str(log_dir))
    logger2 = get_cache_event_logger(log_dir=str(log_dir))

    logger1.log_ohlcv_miss("005930", "test")
    _flush_cache_logger()

    log_files = list((log_dir / "cache").glob("*_cache_*.log.json"))
    assert len(log_files) == 1

    for h in logging.getLogger("cache_event").handlers[:]:
        h.close()
        logging.getLogger("cache_event").removeHandler(h)


def test_log_price_set_new_entry(cache_logger_setup):
    cache_logger, cache_log_dir = cache_logger_setup

    cache_logger.log_price_set("005930", "api", None, "75000", is_new=True)
    _flush_cache_logger()

    lines = _read_json_lines(cache_log_dir)
    d = lines[0]["data"]
    assert d["action"] == "price_set"
    assert d["code"] == "005930"
    assert d["caller"] == "api"
    assert d["before_price"] is None
    assert d["after_price"] == "75000"
    assert d["is_new"] is True
    assert lines[0]["level"] == "INFO"


def test_log_price_set_update(cache_logger_setup):
    cache_logger, cache_log_dir = cache_logger_setup

    cache_logger.log_price_set("005930", "api", "74000", "75000", is_new=False)
    _flush_cache_logger()

    lines = _read_json_lines(cache_log_dir)
    d = lines[0]["data"]
    assert d["before_price"] == "74000"
    assert d["after_price"] == "75000"
    assert d["is_new"] is False


def test_log_price_hit_fields(cache_logger_setup):
    cache_logger, cache_log_dir = cache_logger_setup

    cache_logger.log_price_hit("005930", "strategy_service", 1.23, is_streaming=True)
    _flush_cache_logger()

    lines = _read_json_lines(cache_log_dir)
    d = lines[0]["data"]
    assert d["action"] == "price_hit"
    assert d["code"] == "005930"
    assert d["caller"] == "strategy_service"
    assert d["age_sec"] == 1.23
    assert d["is_streaming"] is True
    assert lines[0]["level"] == "DEBUG"


def test_log_price_miss_not_found(cache_logger_setup):
    cache_logger, cache_log_dir = cache_logger_setup

    cache_logger.log_price_miss("000660", "market_data", "not_found")
    _flush_cache_logger()

    lines = _read_json_lines(cache_log_dir)
    d = lines[0]["data"]
    assert d["action"] == "price_miss"
    assert d["code"] == "000660"
    assert d["reason"] == "not_found"
    assert lines[0]["level"] == "DEBUG"


def test_log_price_miss_ttl_expired(cache_logger_setup):
    cache_logger, cache_log_dir = cache_logger_setup

    cache_logger.log_price_miss("000660", "market_data", "ttl_expired")
    _flush_cache_logger()

    lines = _read_json_lines(cache_log_dir)
    assert lines[0]["data"]["reason"] == "ttl_expired"


def test_log_price_update_tick_fields(cache_logger_setup):
    cache_logger, cache_log_dir = cache_logger_setup

    cache_logger.log_price_update_tick("005930", "74000", "75000", volume=123456)
    _flush_cache_logger()

    lines = _read_json_lines(cache_log_dir)
    d = lines[0]["data"]
    assert d["action"] == "price_update_tick"
    assert d["before_price"] == "74000"
    assert d["after_price"] == "75000"
    assert d["volume"] == 123456
    assert lines[0]["level"] == "DEBUG"


def test_log_price_evicted_is_warning(cache_logger_setup):
    cache_logger, cache_log_dir = cache_logger_setup

    cache_logger.log_price_evicted("005930", capacity=3000)
    _flush_cache_logger()

    lines = _read_json_lines(cache_log_dir)
    d = lines[0]["data"]
    assert d["action"] == "price_evicted"
    assert d["code"] == "005930"
    assert d["capacity"] == 3000
    assert lines[0]["level"] == "WARNING"


def test_log_streaming_mark_and_unmark(cache_logger_setup):
    cache_logger, cache_log_dir = cache_logger_setup

    cache_logger.log_streaming_mark("005930", streaming_count=5)
    cache_logger.log_streaming_unmark("005930", streaming_count=4)
    _flush_cache_logger()

    lines = _read_json_lines(cache_log_dir)
    assert len(lines) == 2

    mark = lines[0]["data"]
    assert mark["action"] == "streaming_mark"
    assert mark["code"] == "005930"
    assert mark["streaming_count"] == 5
    assert lines[0]["level"] == "INFO"

    unmark = lines[1]["data"]
    assert unmark["action"] == "streaming_unmark"
    assert unmark["streaming_count"] == 4
    assert lines[1]["level"] == "INFO"


def test_log_ohlcv_loaded_fields(cache_logger_setup):
    cache_logger, cache_log_dir = cache_logger_setup

    cache_logger.log_ohlcv_loaded("005930", "strategy", 600, "20250401")
    _flush_cache_logger()

    lines = _read_json_lines(cache_log_dir)
    d = lines[0]["data"]
    assert d["action"] == "ohlcv_loaded"
    assert d["code"] == "005930"
    assert d["caller"] == "strategy"
    assert d["ohlcv_count"] == 600
    assert d["latest_date"] == "20250401"
    assert lines[0]["level"] == "INFO"


def test_log_ohlcv_hit_fields(cache_logger_setup):
    cache_logger, cache_log_dir = cache_logger_setup

    cache_logger.log_ohlcv_hit("005930", "backtest", ohlcv_count=601, has_today_candle=True)
    _flush_cache_logger()

    lines = _read_json_lines(cache_log_dir)
    d = lines[0]["data"]
    assert d["action"] == "ohlcv_hit"
    assert d["ohlcv_count"] == 601
    assert d["has_today_candle"] is True
    assert lines[0]["level"] == "DEBUG"


def test_log_ohlcv_miss_fields(cache_logger_setup):
    cache_logger, cache_log_dir = cache_logger_setup

    cache_logger.log_ohlcv_miss("000660", "momentum_strategy")
    _flush_cache_logger()

    lines = _read_json_lines(cache_log_dir)
    d = lines[0]["data"]
    assert d["action"] == "ohlcv_miss"
    assert d["code"] == "000660"
    assert d["caller"] == "momentum_strategy"
    assert lines[0]["level"] == "DEBUG"


def test_log_ohlcv_evicted_is_warning(cache_logger_setup):
    cache_logger, cache_log_dir = cache_logger_setup

    cache_logger.log_ohlcv_evicted("012345", freq=2, ohlcv_count=300, capacity=500)
    _flush_cache_logger()

    lines = _read_json_lines(cache_log_dir)
    d = lines[0]["data"]
    assert d["action"] == "ohlcv_evicted"
    assert d["code"] == "012345"
    assert d["freq"] == 2
    assert d["ohlcv_count"] == 300
    assert d["capacity"] == 500
    assert lines[0]["level"] == "WARNING"


def test_log_ohlcv_invalidated(cache_logger_setup):
    cache_logger, cache_log_dir = cache_logger_setup

    cache_logger.log_ohlcv_invalidated("005930")
    _flush_cache_logger()

    lines = _read_json_lines(cache_log_dir)
    d = lines[0]["data"]
    assert d["action"] == "ohlcv_invalidated"
    assert d["code"] == "005930"
    assert lines[0]["level"] == "INFO"


def test_log_ohlcv_upsert_fields(cache_logger_setup):
    cache_logger, cache_log_dir = cache_logger_setup

    cache_logger.log_ohlcv_upsert(
        record_count=1200,
        code_count=2,
        invalidated_codes=["000660", "005930"],
    )
    _flush_cache_logger()

    lines = _read_json_lines(cache_log_dir)
    d = lines[0]["data"]
    assert d["action"] == "ohlcv_upsert"
    assert d["record_count"] == 1200
    assert d["code_count"] == 2
    assert d["invalidated_codes"] == ["000660", "005930"]
    assert lines[0]["level"] == "INFO"


def test_log_today_candle_update(cache_logger_setup):
    cache_logger, cache_log_dir = cache_logger_setup

    cache_logger.log_today_candle("005930", 74000, 75000, high=75500, low=73000, is_new_candle=False)
    _flush_cache_logger()

    lines = _read_json_lines(cache_log_dir)
    d = lines[0]["data"]
    assert d["action"] == "today_candle"
    assert d["before_price"] == 74000
    assert d["after_price"] == 75000
    assert d["high"] == 75500
    assert d["low"] == 73000
    assert d["is_new_candle"] is False
    assert lines[0]["level"] == "DEBUG"


def test_log_today_candle_new(cache_logger_setup):
    cache_logger, cache_log_dir = cache_logger_setup

    cache_logger.log_today_candle("005930", None, 75000, high=75000, low=75000, is_new_candle=True)
    _flush_cache_logger()

    lines = _read_json_lines(cache_log_dir)
    d = lines[0]["data"]
    assert d["is_new_candle"] is True
    assert d["before_price"] is None


def test_log_stats_combined_hit_rate(cache_logger_setup):
    cache_logger, cache_log_dir = cache_logger_setup

    price_stats = {"hits": 80, "misses": 20, "hit_rate": 80.0, "current_size": 100}
    ohlcv_stats = {"hits": 60, "misses": 40, "hit_rate": 60.0, "current_size": 50}
    cache_logger.log_stats(price_stats, ohlcv_stats)
    _flush_cache_logger()

    lines = _read_json_lines(cache_log_dir)
    d = lines[0]["data"]
    assert d["action"] == "cache_stats"
    assert d["price"]["hits"] == 80
    assert d["ohlcv"]["hits"] == 60
    assert d["combined"]["hits"] == 140
    assert d["combined"]["misses"] == 60
    assert d["combined"]["hit_rate"] == 70.0
    assert lines[0]["level"] == "INFO"


def test_log_stats_zero_requests(cache_logger_setup):
    cache_logger, cache_log_dir = cache_logger_setup

    cache_logger.log_stats(
        {"hits": 0, "misses": 0, "hit_rate": 0.0, "current_size": 0},
        {"hits": 0, "misses": 0, "hit_rate": 0.0, "current_size": 0},
    )
    _flush_cache_logger()

    lines = _read_json_lines(cache_log_dir)
    assert lines[0]["data"]["combined"]["hit_rate"] == 0.0


def test_lru_cache_eviction_callback_fires():
    evicted = []
    lru = _LRUCache(capacity=2, on_evict=lambda k: evicted.append(k))
    lru.put("A", 1)
    lru.put("B", 2)
    lru.put("C", 3)

    assert evicted == ["A"]


def test_lru_cache_eviction_callback_none_does_not_raise():
    lru = _LRUCache(capacity=1)
    lru.put("A", 1)
    lru.put("B", 2)


def test_lfu_cache_eviction_callback_fires_with_freq_and_ohlcv_count():
    evicted = []
    lfu = _LFUCache(capacity=2, on_evict=lambda k, f, c: evicted.append((k, f, c)))

    lfu.put("A", {"ohlcv_historical": [1, 2, 3]})
    lfu.put("B", {"ohlcv_historical": [1]})
    lfu.get("A")
    lfu.put("C", {})

    assert len(evicted) == 1
    key, freq, ohlcv_count = evicted[0]
    assert key == "B"
    assert freq == 0
    assert ohlcv_count == 1


def test_lfu_cache_eviction_callback_non_dict_value():
    evicted = []
    lfu = _LFUCache(capacity=1, on_evict=lambda k, f, c: evicted.append((k, f, c)))
    lfu.put("A", "not_a_dict")
    lfu.put("B", {})

    assert evicted[0][2] == 0


def test_lfu_cache_eviction_callback_exception_does_not_propagate():
    def bad_callback(k, f, c):
        raise RuntimeError("callback error")

    lfu = _LFUCache(capacity=1, on_evict=bad_callback)
    lfu.put("A", {})
    lfu.put("B", {})
    assert "B" in lfu._cache


def test_stock_price_repo_logs_price_set_new():
    mock_cache_logger = MagicMock(spec=CacheEventLogger)
    repo = StockPriceRepository(cache_logger=mock_cache_logger)

    repo.set_current_price("005930", {"output": {"stck_prpr": "75000"}})

    mock_cache_logger.log_price_set.assert_called_once()
    args = mock_cache_logger.log_price_set.call_args[0]
    assert args[0] == "005930"
    assert args[4] is True


def test_stock_price_repo_logs_price_set_update():
    mock_cache_logger = MagicMock(spec=CacheEventLogger)
    repo = StockPriceRepository(cache_logger=mock_cache_logger)

    repo.set_current_price("005930", {"output": {"stck_prpr": "74000"}})
    repo.set_current_price("005930", {"output": {"stck_prpr": "75000"}})

    calls = mock_cache_logger.log_price_set.call_args_list
    assert calls[0][0][4] is True
    assert calls[1][0][4] is False
    assert calls[1][0][2] == "74000"
    assert calls[1][0][3] == "75000"


def test_stock_price_repo_logs_price_miss_not_found():
    mock_cache_logger = MagicMock(spec=CacheEventLogger)
    repo = StockPriceRepository(cache_logger=mock_cache_logger)

    result = repo.get_current_price("999999", caller="test")

    assert result is None
    mock_cache_logger.log_price_miss.assert_called_once_with("999999", "test", "not_found")


def test_stock_price_repo_logs_price_miss_ttl_expired():
    mock_cache_logger = MagicMock(spec=CacheEventLogger)
    repo = StockPriceRepository(cache_logger=mock_cache_logger)

    repo.set_current_price("005930", {"output": {"stck_prpr": "75000"}})
    cached = repo._price_cache.get("005930", count_stats=False)
    cached["price_updated_at"] = time.time() - 9999

    result = repo.get_current_price("005930", max_age_sec=3.0, caller="test")

    assert result is None
    mock_cache_logger.log_price_miss.assert_called_once_with("005930", "test", "ttl_expired")


def test_stock_price_repo_logs_price_hit():
    mock_cache_logger = MagicMock(spec=CacheEventLogger)
    repo = StockPriceRepository(cache_logger=mock_cache_logger)

    repo.set_current_price("005930", {"output": {"stck_prpr": "75000"}})
    result = repo.get_current_price("005930", caller="test")

    assert result is not None
    mock_cache_logger.log_price_hit.assert_called_once()
    args = mock_cache_logger.log_price_hit.call_args[0]
    assert args[0] == "005930"
    assert args[1] == "test"
    assert args[3] is False


def test_stock_price_repo_logs_streaming_mark_unmark():
    mock_cache_logger = MagicMock(spec=CacheEventLogger)
    repo = StockPriceRepository(cache_logger=mock_cache_logger)

    repo.mark_streaming("005930")
    repo.mark_streaming("000660")
    repo.unmark_streaming("005930")

    mark_calls = mock_cache_logger.log_streaming_mark.call_args_list
    assert mark_calls[0][0] == ("005930", 1)
    assert mark_calls[1][0] == ("000660", 2)

    unmark_args = mock_cache_logger.log_streaming_unmark.call_args[0]
    assert unmark_args[0] == "005930"
    assert unmark_args[1] == 1


def test_stock_price_repo_logs_price_update_tick_only_on_change():
    mock_cache_logger = MagicMock(spec=CacheEventLogger)
    repo = StockPriceRepository(cache_logger=mock_cache_logger)

    repo.set_current_price("005930", {"output": {"stck_prpr": "75000"}})
    mock_cache_logger.reset_mock()

    repo.update_current_price("005930", 75000)
    mock_cache_logger.log_price_update_tick.assert_not_called()

    repo.update_current_price("005930", 76000)
    mock_cache_logger.log_price_update_tick.assert_called_once()
    args = mock_cache_logger.log_price_update_tick.call_args[0]
    assert args[1] == "75000"
    assert args[2] == "76000"


def test_stock_price_repo_eviction_logs_warning():
    mock_cache_logger = MagicMock(spec=CacheEventLogger)
    repo = StockPriceRepository(cache_logger=mock_cache_logger)
    repo._price_cache.capacity = 2

    repo.set_current_price("A", {"output": {"stck_prpr": "1000"}})
    repo.set_current_price("B", {"output": {"stck_prpr": "2000"}})
    mock_cache_logger.reset_mock()
    repo.set_current_price("C", {"output": {"stck_prpr": "3000"}})

    mock_cache_logger.log_price_evicted.assert_called_once()
    args = mock_cache_logger.log_price_evicted.call_args[0]
    assert args[0] == "A"


def test_stock_ohlcv_repo_logs_upsert_and_invalidation(tmp_path):
    import asyncio

    mock_cache_logger = MagicMock(spec=CacheEventLogger)
    db_path = str(tmp_path / "test.db")
    repo = StockOhlcvRepository(db_path=db_path, cache_logger=mock_cache_logger)

    records = [
        {"code": "005930", "date": "20250401", "open": 74000, "high": 75000, "low": 73000, "close": 74500, "volume": 100000},
        {"code": "000660", "date": "20250401", "open": 90000, "high": 91000, "low": 89000, "close": 90500, "volume": 50000},
    ]

    async def run():
        await repo.upsert_ohlcv(records)
        await repo.close()

    asyncio.run(run())

    assert mock_cache_logger.log_ohlcv_invalidated.call_count == 2
    invalidated_codes = {c[0][0] for c in mock_cache_logger.log_ohlcv_invalidated.call_args_list}
    assert invalidated_codes == {"005930", "000660"}

    mock_cache_logger.log_ohlcv_upsert.assert_called_once()
    args = mock_cache_logger.log_ohlcv_upsert.call_args[1]
    assert args["record_count"] == 2
    assert args["code_count"] == 2


async def test_stock_ohlcv_repo_logs_ohlcv_loaded_and_hit(tmp_path):
    mock_cache_logger = MagicMock(spec=CacheEventLogger)
    db_path = str(tmp_path / "test.db")
    repo = StockOhlcvRepository(db_path=db_path, cache_logger=mock_cache_logger)

    records = [
        {"code": "005930", "date": "20250401", "open": 74000, "high": 75000, "low": 73000, "close": 74500, "volume": 100000},
        {"code": "005930", "date": "20250331", "open": 73000, "high": 74000, "low": 72000, "close": 73500, "volume": 90000},
    ]
    await repo.upsert_ohlcv(records)
    mock_cache_logger.reset_mock()

    result = await repo.get_stock_data("005930", ohlcv_limit=2, caller="test")
    assert result is not None
    mock_cache_logger.log_ohlcv_miss.assert_called_once_with("005930", "test")
    mock_cache_logger.log_ohlcv_loaded.assert_called_once()
    loaded_args = mock_cache_logger.log_ohlcv_loaded.call_args[0]
    assert loaded_args[0] == "005930"
    assert loaded_args[2] == 2
    assert loaded_args[3] == "20250401"

    mock_cache_logger.reset_mock()

    result2 = await repo.get_stock_data("005930", ohlcv_limit=2, caller="test")
    assert result2 is not None
    mock_cache_logger.log_ohlcv_hit.assert_called_once()
    mock_cache_logger.log_ohlcv_loaded.assert_not_called()

    await repo.close()


async def test_stock_ohlcv_repo_logs_ohlcv_eviction(tmp_path):
    mock_cache_logger = MagicMock(spec=CacheEventLogger)
    db_path = str(tmp_path / "test.db")
    repo = StockOhlcvRepository(db_path=db_path, cache_logger=mock_cache_logger)
    repo._ohlcv_cache.capacity = 1

    for code in ["005930", "000660"]:
        await repo.upsert_ohlcv([
            {"code": code, "date": "20250401", "open": 1000, "high": 1100, "low": 900, "close": 1050, "volume": 1000}
        ])

    mock_cache_logger.reset_mock()

    await repo.get_stock_data("005930", caller="test")
    await repo.get_stock_data("000660", caller="test")

    mock_cache_logger.log_ohlcv_evicted.assert_called_once()
    call = mock_cache_logger.log_ohlcv_evicted.call_args
    assert call[0][0] == "005930"
    assert call[1]["capacity"] == 1

    await repo.close()
