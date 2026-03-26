# repositories/cache.py
"""
인메모리 캐시 구현체.
- _LRUCache : 현재가(price) 캐시용 — recency 기반 eviction
- _LFUCache : OHLCV 캐시용 — frequency 기반 eviction (자주 쓰이는 종목이 밀려나지 않음)
"""
import collections
from typing import Optional, Any


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

    def delete(self, key):
        if key in self.cache:
            del self.cache[key]
            if key in self.item_hits:
                del self.item_hits[key]

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
            for key, val in list(self.cache.items()):
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


class _LFUCache:
    """
    LFU(Least Frequently Used) 캐시 — OHLCV 데이터 전용.
    접근 빈도가 낮은 항목부터 evict하여 자주 분석되는 종목이 캐시에 오래 남음.
    """
    def __init__(self, capacity: int = 500):
        self._cache: dict = {}                          # key → value
        self._freq: dict = collections.defaultdict(int) # key → access count
        self.capacity = capacity
        self.hits = 0
        self.misses = 0
        self.caller_stats = collections.defaultdict(lambda: {"hits": 0, "misses": 0, "keys": collections.defaultdict(int), "items": collections.defaultdict(int)})

    def get(self, key, count_stats: bool = True, caller: str = "unknown", item_type: str = "unknown") -> Optional[Any]:
        if key not in self._cache:
            if count_stats:
                self.misses += 1
                self.caller_stats[caller]["misses"] += 1
                self.caller_stats[caller]["items"][item_type] += 1
            return None
        if count_stats:
            self.hits += 1
            self._freq[key] += 1
            self.caller_stats[caller]["hits"] += 1
            self.caller_stats[caller]["keys"][key] += 1
            self.caller_stats[caller]["items"][item_type] += 1
        return self._cache[key]

    def put(self, key, value):
        if key in self._cache:
            self._cache[key] = value
            self._freq[key] += 1
            return
        if len(self._cache) >= self.capacity:
            lfu_key = min(self._freq, key=lambda k: self._freq[k])
            del self._cache[lfu_key]
            del self._freq[lfu_key]
        self._cache[key] = value
        self._freq[key] = 0

    def delete(self, key):
        if key in self._cache:
            del self._cache[key]
            if key in self._freq:
                del self._freq[key]

    def get_stats(self, expand: bool = False) -> dict:
        """캐시 적중률 통계를 반환합니다."""
        total = self.hits + self.misses
        hit_rate = (self.hits / total * 100) if total > 0 else 0.0

        callers_out = {}
        for c, s in self.caller_stats.items():
            callers_out[c] = {
                "hits": s["hits"],
                "misses": s["misses"],
                "items": dict(s["items"]),
            }
            if expand:
                callers_out[c]["keys"] = dict(sorted(s["keys"].items(), key=lambda item: item[1], reverse=True)[:20])

        stats = {
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": round(hit_rate, 2),
            "total_requests": total,
            "current_size": len(self._cache),
            "callers": callers_out,
        }
        if expand:
            items = []
            for k, v in self._cache.items():
                recent_dates: list[str] = []
                if isinstance(v, dict):
                    historical = v.get("ohlcv_historical", [])
                    tail = historical[-5:] if len(historical) >= 5 else historical
                    recent_dates = [
                        c["date"] for c in reversed(tail)
                        if isinstance(c, dict) and "date" in c
                    ]
                    today = v.get("ohlcv_today")
                    if today and isinstance(today, dict) and "date" in today:
                        date_str = today["date"]
                        if date_str not in recent_dates:
                            recent_dates.insert(0, date_str)
                    recent_dates = recent_dates[:5]
                items.append({
                    "code": k,
                    "freq": self._freq.get(k, 0),
                    "historical_complete": v.get("historical_complete", False) if isinstance(v, dict) else False,
                    "ohlcv_count": len(v.get("ohlcv_historical", [])) if isinstance(v, dict) else 0,
                    "has_today_candle": bool(v.get("ohlcv_today")) if isinstance(v, dict) else False,
                    "recent_dates": recent_dates,
                })
            items.sort(key=lambda x: x["freq"], reverse=True)
            stats["items"] = items
        return stats
