# repositories/stock_repository.py
"""
개별 종목의 상세 데이터(OHLCV 등)와 인메모리 캐시를 관리하는 Repository.
차트 조회 및 개별 종목 지표 계산을 위한 초고속 데이터 제공을 목적으로 한다.
"""
import os
import sqlite3
import time
import logging
import threading
import collections
from contextlib import contextmanager
from typing import Optional, List, Dict, Any


class _LRUCache:
    """내장 OrderedDict를 활용한 인메모리 LRU(Least Recently Used) 캐시"""
    def __init__(self, capacity: int = 500):
        self.cache = collections.OrderedDict()
        self.capacity = capacity
        self.hits = 0
        self.misses = 0
        self.item_hits = collections.defaultdict(int)
        self.caller_stats = collections.defaultdict(lambda: {"hits": 0, "misses": 0, "keys": collections.defaultdict(int), "items": collections.defaultdict(int)})

    def get(self, key, count_stats: bool = True, caller: str = "unknown", item_type: str = "unknown"):
        if count_stats:
            self.caller_stats[caller]["keys"][key] += 1
            self.caller_stats[caller]["items"][item_type] += 1
            
        if key not in self.cache:
            if count_stats:
                self.misses += 1
                self.caller_stats[caller]["misses"] += 1
            return None
        if count_stats:
            self.hits += 1
            self.item_hits[key] += 1
            self.caller_stats[caller]["hits"] += 1
        self.cache.move_to_end(key)
        return self.cache[key]

    def put(self, key, value):
        self.cache[key] = value
        self.cache.move_to_end(key)
        if len(self.cache) > self.capacity:
            removed_key, _ = self.cache.popitem(last=False)
            if removed_key in self.item_hits:
                del self.item_hits[removed_key]

    def get_stats(self, expand: bool = False) -> dict:
        """캐시 적중률 통계를 반환합니다."""
        total = self.hits + self.misses
        hit_rate = (self.hits / total * 100) if total > 0 else 0.0
        
        callers_out = {}
        for c, s in self.caller_stats.items():
            callers_out[c] = {
                "hits": s["hits"],
                "misses": s["misses"],
                "items": dict(s["items"])
            }
            if expand:
                # 너무 길어지는 것을 방지하기 위해 각 caller별 가장 많이 찾은 종목 20개까지만 표시
                callers_out[c]["keys"] = dict(sorted(s["keys"].items(), key=lambda item: item[1], reverse=True)[:20])

        stats = {
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": round(hit_rate, 2),
            "total_requests": total,
            "current_size": len(self.cache),
            "callers": callers_out
        }

        if expand:
            items = []
            for key, val in self.cache.items():
                if isinstance(val, dict):
                    items.append({
                        "code": key,
                        "hit_count": self.item_hits.get(key, 0),
                        "has_ohlcv": "ohlcv" in val and len(val["ohlcv"]) > 0,
                        "ohlcv_length": len(val["ohlcv"]) if "ohlcv" in val else 0,
                        "has_current_price": "current_price_data" in val,
                        "last_updated": val.get("last_updated"),
                        "price_updated_at": val.get("price_updated_at")
                    })
                else:
                    items.append({
                        "code": key,
                        "hit_count": self.item_hits.get(key, 0),
                    })
            items.sort(key=lambda x: x.get("hit_count", 0), reverse=True)
            stats["items"] = items

        return stats


class StockRepository:
    """개별 종목 데이터(OHLCV, 캐시) 전담 저장소."""

    def __init__(self, db_path: str = None, logger=None):
        self._logger = logger or logging.getLogger(__name__)
        # MarketData와 DB 파일을 물리적으로 분리하여 Lock 경합 방지
        self._db_path = db_path or os.path.join("data", "stocks.db")
        self._lock = threading.Lock()
        self._conn: Optional[sqlite3.Connection] = None

        # 최대 500개 종목의 통합 데이터(현재가 + OHLCV)를 들고 있는 인메모리 캐시
        self._stocks_cache = _LRUCache(capacity=500)

        os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
        self._init_db()

    def _init_db(self):
        try:
            self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
            with self._get_connection() as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS ohlcv (
                        code TEXT NOT NULL,
                        date TEXT NOT NULL,
                        open INTEGER,
                        high INTEGER,
                        low INTEGER,
                        close INTEGER,
                        volume INTEGER,
                        PRIMARY KEY (code, date)
                    )
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_ohlcv_date ON ohlcv(date)")
        except Exception as e:
            self._logger.error(f"StockRepository DB 초기화 실패: {e}")

    @contextmanager
    def _get_connection(self):
        with self._lock:
            try:
                yield self._conn
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def upsert_ohlcv(self, records: List[Dict]):
        """여러 종목의 일봉(OHLCV) 데이터를 일괄 upsert (INSERT OR REPLACE)."""
        if not records:
            return

        try:
            with self._get_connection() as conn:
                conn.executemany(
                    """
                    INSERT OR REPLACE INTO ohlcv (
                        code, date, open, high, low, close, volume
                    ) VALUES (
                        :code, :date, :open, :high, :low, :close, :volume
                    )
                    """,
                    records,
                )
        except Exception as e:
            self._logger.error(f"StockRepository OHLCV upsert 실패: {e}")

    def get_stock_data(self, code: str, ohlcv_limit: int = 600, caller: str = "unknown") -> Optional[Dict]:
        """메모리 캐시 또는 로컬 DB에서 종목 정보(OHLCV)를 반환합니다."""
        # 1. 메모리 캐시 확인
        cached = self._stocks_cache.get(code, caller=caller, item_type="ohlcv")
        if cached and len(cached.get("ohlcv", [])) >= ohlcv_limit:
            return cached

        # 2. 캐시에 없거나 데이터가 부족하면 DB에서 읽어오기
        try:
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(
                    "SELECT date, open, high, low, close, volume FROM ohlcv "
                    "WHERE code = ? ORDER BY date DESC LIMIT ?",
                    (code, ohlcv_limit)
                )
                ohlcv_rows = cursor.fetchall()
                conn.row_factory = None

            if not ohlcv_rows:
                return None  # DB에도 데이터가 전혀 없는 경우

            # 3. 통합 객체 구성 후 캐시에 적재
            stock_data = {"code": code, "ohlcv": [dict(r) for r in reversed(ohlcv_rows)], "last_updated": time.time()}
            self._stocks_cache.put(code, stock_data)
            return stock_data
        except Exception as e:
            self._logger.error(f"StockRepository 종목 통합 데이터 조회 실패 ({code}): {e}")
            return None

    def update_realtime_data(self, code: str, current_price: float, volume: int = 0):
        """
        장 중에 수신된 실시간 틱 데이터를 메모리 캐시에 즉시 반영합니다.
        """
        # 내부 갱신용 접근이므로 통계 집계에서 제외
        cached = self._stocks_cache.get(code, count_stats=False, item_type="update_tick")
        if not cached:
            cached = {"code": code}
            self._stocks_cache.put(code, cached)
            
        # 1. 현재가 데이터 갱신 (API 응답 구조와 호환 유지)
        if "current_price_data" not in cached:
            cached["current_price_data"] = {"output": {}}
            
        output = cached["current_price_data"].get("output")
        if isinstance(output, dict):
            output["stck_prpr"] = str(int(current_price))
            if volume > 0:
                output["acml_vol"] = str(volume)
        elif output is not None:
            try:
                setattr(output, "stck_prpr", str(int(current_price)))
                if volume > 0:
                    setattr(output, "acml_vol", str(volume))
            except Exception:
                pass
                
        # TTL 갱신: 웹소켓 데이터가 들어오는 동안에는 캐시가 영구적으로 유효하도록 시간 갱신
        cached["price_updated_at"] = time.time()
        
        # 2. 당일 OHLCV 캔들 데이터 갱신 (차트용)
        if "ohlcv" in cached and cached["ohlcv"]:
            last_candle = cached["ohlcv"][-1]
            last_candle["close"] = current_price
            if volume > 0:
                last_candle["volume"] = volume
            if current_price > last_candle.get("high", current_price):
                last_candle["high"] = current_price
            if current_price < last_candle.get("low", current_price):
                last_candle["low"] = current_price

    def set_current_price(self, code: str, price_data: dict):
        """현재가 API 응답 전체 데이터를 캐시에 저장합니다."""
        # 내부 갱신용 접근이므로 통계 집계에서 제외
        cached = self._stocks_cache.get(code, count_stats=False, item_type="set_price")
        if not cached:
            cached = {"code": code}
            self._stocks_cache.put(code, cached)
        cached["current_price_data"] = price_data
        cached["price_updated_at"] = time.time()

    def get_current_price(self, code: str, max_age_sec: float = 3.0, count_stats: bool = True, caller: str = "unknown") -> Optional[dict]:
        """캐시된 현재가 데이터(dict)를 반환합니다. 지정된 TTL(초)이 만료된 경우 None 반환."""
        cached = self._stocks_cache.get(code, count_stats=count_stats, caller=caller, item_type="current_price")
        if cached and "current_price_data" in cached:
            if time.time() - cached.get("price_updated_at", 0) <= max_age_sec:
                return cached["current_price_data"]
        return None

    def get_ohlcv_summary(self, code: str) -> Dict[str, Any]:
        """DB에서 종목의 OHLCV 요약 정보를 반환합니다 (전체 데이터 로드 없이 메타만 조회).

        Returns:
            {"count": int, "latest_date": str|None, "oldest_date": str|None}
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.execute(
                    "SELECT COUNT(*), MAX(date), MIN(date) FROM ohlcv WHERE code = ?",
                    (code,),
                )
                row = cursor.fetchone()
            if row and row[0]:
                return {"count": row[0], "latest_date": row[1], "oldest_date": row[2]}
        except Exception as e:
            self._logger.error(f"StockRepository OHLCV 요약 조회 실패 ({code}): {e}")
        return {"count": 0, "latest_date": None, "oldest_date": None}

    def get_cache_stats(self, expand: bool = False) -> dict:
        """메모리 캐시의 사용 통계(적중률 등)를 반환합니다."""
        return self._stocks_cache.get_stats(expand=expand)

    def close(self):
        """DB 연결을 닫는다."""
        if self._conn:
            self._conn.close()
            self._conn = None

    def __del__(self):
        self.close()