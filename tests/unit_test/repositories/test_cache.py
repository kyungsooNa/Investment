# tests/unit_test/repositories/test_cache.py
"""
_LRUCache, _LFUCache 단위 테스트.
- 기본 동작 (put/get/delete, eviction)
- 통계 추적 (hits/misses, item_hits, caller_stats)
- get_stats() expand=True/False
"""
import pytest
from repositories.cache import _LRUCache, _LFUCache


# ════════════════════════════════════════════════════════════
# _LRUCache
# ════════════════════════════════════════════════════════════

class TestLRUCacheBasic:
    def test_get_from_empty_returns_none(self):
        cache = _LRUCache(capacity=3)
        assert cache.get("missing") is None

    def test_put_and_get(self):
        cache = _LRUCache(capacity=3)
        cache.put("A", 42)
        assert cache.get("A") == 42

    def test_get_moves_to_end(self):
        """get 호출이 해당 키를 '최근 사용'으로 갱신하여 eviction 순서를 바꾸는지 확인."""
        cache = _LRUCache(capacity=2)
        cache.put("A", 1)
        cache.put("B", 2)
        cache.get("A")        # A를 최근으로 올림
        cache.put("C", 3)     # 용량 초과 → B(가장 오래됨) evict
        assert cache.get("A") == 1
        assert cache.get("C") == 3
        assert cache.get("B") is None

    def test_put_overwrites_existing(self):
        cache = _LRUCache(capacity=2)
        cache.put("A", 1)
        cache.put("A", 99)
        assert cache.get("A") == 99
        assert len(cache.cache) == 1

    def test_eviction_removes_lru_item(self):
        """용량 초과 시 가장 오래된(LRU) 항목이 evict된다."""
        cache = _LRUCache(capacity=2)
        cache.put("A", 1)
        cache.put("B", 2)
        cache.put("C", 3)     # A가 evict
        assert cache.get("A") is None
        assert cache.get("B") == 2
        assert cache.get("C") == 3

    def test_delete_existing_key(self):
        cache = _LRUCache(capacity=3)
        cache.put("A", 1)
        cache.get("A")        # item_hits["A"] = 1
        cache.delete("A")
        assert cache.get("A") is None
        assert "A" not in cache.item_hits

    def test_delete_nonexistent_key_is_safe(self):
        cache = _LRUCache(capacity=3)
        cache.delete("NONEXISTENT")  # 예외 없이 무시


class TestLRUCacheStats:
    def test_hit_and_miss_counters(self):
        cache = _LRUCache(capacity=3)
        cache.put("A", 1)
        cache.get("A")   # hit
        cache.get("B")   # miss
        assert cache.hits == 1
        assert cache.misses == 1

    def test_item_hits_incremented_on_hit(self):
        cache = _LRUCache(capacity=3)
        cache.put("A", 1)
        cache.get("A")
        cache.get("A")
        assert cache.item_hits["A"] == 2

    def test_item_hits_not_incremented_on_miss(self):
        cache = _LRUCache(capacity=3)
        cache.get("MISS")
        assert cache.item_hits["MISS"] == 0

    def test_item_hits_cleared_on_eviction(self):
        cache = _LRUCache(capacity=2)
        cache.put("A", 1)
        cache.get("A")        # item_hits["A"] = 1
        cache.put("B", 2)
        cache.put("C", 3)     # A evict → item_hits["A"] 삭제
        assert "A" not in cache.item_hits

    def test_count_stats_false_skips_all_tracking(self):
        cache = _LRUCache(capacity=3)
        cache.put("A", 1)
        cache.get("A", count_stats=False)
        cache.get("MISS", count_stats=False)
        assert cache.hits == 0
        assert cache.misses == 0
        assert len(cache.caller_stats) == 0

    def test_caller_stats_on_hit(self):
        cache = _LRUCache(capacity=3)
        cache.put("A", 1)
        cache.get("A", caller="svc_a", item_type="price")
        stats = cache.caller_stats["svc_a"]
        assert stats["hits"] == 1
        assert stats["misses"] == 0
        assert stats["items"]["price"] == 1

    def test_caller_stats_on_miss(self):
        cache = _LRUCache(capacity=3)
        cache.get("MISS", caller="svc_b", item_type="ohlcv")
        stats = cache.caller_stats["svc_b"]
        assert stats["hits"] == 0
        assert stats["misses"] == 1
        assert stats["items"]["ohlcv"] == 1
        # miss여도 keys는 카운트됨
        assert stats["keys"]["MISS"] == 1

    def test_caller_stats_multiple_callers(self):
        cache = _LRUCache(capacity=3)
        cache.put("A", 1)
        cache.get("A", caller="alpha")
        cache.get("MISS", caller="beta")
        assert cache.caller_stats["alpha"]["hits"] == 1
        assert cache.caller_stats["beta"]["misses"] == 1

    def test_caller_stats_keys_counted_on_both_hit_and_miss(self):
        cache = _LRUCache(capacity=3)
        cache.put("A", 1)
        cache.get("A", caller="svc")     # hit → keys["A"] += 1
        cache.get("MISS", caller="svc")  # miss → keys["MISS"] += 1
        assert cache.caller_stats["svc"]["keys"]["A"] == 1
        assert cache.caller_stats["svc"]["keys"]["MISS"] == 1


class TestLRUCacheGetStats:
    def test_no_requests_returns_zero_hit_rate(self):
        cache = _LRUCache(capacity=3)
        stats = cache.get_stats()
        assert stats["hit_rate"] == 0.0
        assert stats["total_requests"] == 0

    def test_expand_false_has_no_items_key(self):
        cache = _LRUCache(capacity=3)
        cache.put("A", 1)
        cache.get("A")
        stats = cache.get_stats(expand=False)
        assert "items" not in stats
        assert "callers" in stats

    def test_expand_true_includes_items(self):
        cache = _LRUCache(capacity=3)
        cache.put("A", {"ohlcv": [1, 2], "current_price_data": {}, "last_updated": 100, "price_updated_at": 101})
        cache.put("B", "not_a_dict")
        cache.get("A")   # hit_count=1
        stats = cache.get_stats(expand=True)
        assert "items" in stats
        a_item = next(i for i in stats["items"] if i["code"] == "A")
        assert a_item["has_ohlcv"] is True
        assert a_item["ohlcv_length"] == 2
        assert a_item["has_current_price"] is True
        assert a_item["hit_count"] == 1

    def test_expand_true_non_dict_value_minimal_fields(self):
        cache = _LRUCache(capacity=3)
        cache.put("B", "not_a_dict")
        stats = cache.get_stats(expand=True)
        b_item = next(i for i in stats["items"] if i["code"] == "B")
        assert "hit_count" in b_item
        assert "has_ohlcv" not in b_item

    def test_expand_true_items_sorted_by_hit_count_desc(self):
        cache = _LRUCache(capacity=3)
        cache.put("A", 1)
        cache.put("B", 2)
        cache.put("C", 3)
        cache.get("C")
        cache.get("C")
        cache.get("B")
        stats = cache.get_stats(expand=True)
        codes = [i["code"] for i in stats["items"]]
        assert codes[0] == "C"
        assert codes[1] == "B"

    def test_expand_true_callers_include_keys(self):
        cache = _LRUCache(capacity=3)
        cache.put("A", 1)
        cache.get("A", caller="svc", item_type="price")
        stats = cache.get_stats(expand=True)
        assert "keys" in stats["callers"]["svc"]

    def test_expand_false_callers_no_keys(self):
        cache = _LRUCache(capacity=3)
        cache.put("A", 1)
        cache.get("A", caller="svc")
        stats = cache.get_stats(expand=False)
        assert "keys" not in stats["callers"]["svc"]

    def test_hit_rate_calculation(self):
        cache = _LRUCache(capacity=3)
        cache.put("A", 1)
        cache.get("A")   # hit
        cache.get("B")   # miss
        cache.get("C")   # miss
        stats = cache.get_stats()
        # 1 hit / 3 total = 33.33%
        assert stats["hit_rate"] == round(1 / 3 * 100, 2)

    def test_current_size_reflects_cache_length(self):
        cache = _LRUCache(capacity=5)
        cache.put("A", 1)
        cache.put("B", 2)
        stats = cache.get_stats()
        assert stats["current_size"] == 2


# ════════════════════════════════════════════════════════════
# _LFUCache
# ════════════════════════════════════════════════════════════

class TestLFUCacheBasic:
    def test_get_from_empty_returns_none(self):
        cache = _LFUCache(capacity=3)
        assert cache.get("missing") is None

    def test_put_and_get(self):
        cache = _LFUCache(capacity=3)
        cache.put("A", {"v": 1})
        assert cache.get("A") == {"v": 1}

    def test_initial_freq_is_zero(self):
        """새로 삽입된 항목의 초기 freq는 0."""
        cache = _LFUCache(capacity=3)
        cache.put("A", 1)
        assert cache._freq["A"] == 0

    def test_get_increments_freq(self):
        cache = _LFUCache(capacity=3)
        cache.put("A", 1)
        cache.get("A")
        cache.get("A")
        assert cache._freq["A"] == 2

    def test_put_existing_key_updates_value_and_freq(self):
        cache = _LFUCache(capacity=3)
        cache.put("A", 1)
        cache.put("A", 99)     # 이미 존재 → 값 갱신, freq += 1
        assert cache.get("A") == 99
        assert cache._freq["A"] == 2   # put(+1) + get(+1)

    def test_eviction_removes_least_frequent(self):
        cache = _LFUCache(capacity=2)
        cache.put("A", 1)
        cache.put("B", 2)
        cache.get("A")    # freq A=1, B=0
        cache.put("C", 3) # B evict (freq=0)
        assert cache.get("A") == 1
        assert cache.get("C") == 3
        assert cache.get("B") is None

    def test_eviction_freq_entry_removed(self):
        cache = _LFUCache(capacity=2)
        cache.put("A", 1)
        cache.put("B", 2)
        cache.get("A")
        cache.put("C", 3)  # B evict
        assert "B" not in cache._freq

    def test_delete_existing_key(self):
        cache = _LFUCache(capacity=3)
        cache.put("A", 1)
        cache.get("A")
        cache.delete("A")
        assert cache.get("A") is None
        assert "A" not in cache._freq

    def test_delete_nonexistent_key_is_safe(self):
        cache = _LFUCache(capacity=3)
        cache.delete("NONEXISTENT")   # 예외 없이 무시


class TestLFUCacheStats:
    def test_hit_and_miss_counters(self):
        cache = _LFUCache(capacity=3)
        cache.put("A", 1)
        cache.get("A")    # hit
        cache.get("B")    # miss
        assert cache.hits == 1
        assert cache.misses == 1

    def test_count_stats_false_skips_all_tracking(self):
        cache = _LFUCache(capacity=3)
        cache.put("A", 1)
        cache.get("A", count_stats=False)
        cache.get("MISS", count_stats=False)
        assert cache.hits == 0
        assert cache.misses == 0
        assert len(cache.caller_stats) == 0

    def test_caller_stats_on_hit(self):
        """[신규] _LFUCache도 hit 시 caller_stats를 추적한다."""
        cache = _LFUCache(capacity=3)
        cache.put("A", 1)
        cache.get("A", caller="svc_ohlcv", item_type="ohlcv")
        stats = cache.caller_stats["svc_ohlcv"]
        assert stats["hits"] == 1
        assert stats["misses"] == 0
        assert stats["items"]["ohlcv"] == 1
        assert stats["keys"]["A"] == 1

    def test_caller_stats_on_miss(self):
        """[신규] _LFUCache도 miss 시 caller_stats를 추적한다."""
        cache = _LFUCache(capacity=3)
        cache.get("MISS", caller="svc_ohlcv", item_type="ohlcv")
        stats = cache.caller_stats["svc_ohlcv"]
        assert stats["hits"] == 0
        assert stats["misses"] == 1
        assert stats["items"]["ohlcv"] == 1

    def test_caller_stats_miss_does_not_track_keys(self):
        """[신규] miss 시에는 keys를 카운트하지 않는다 (LRU와 달리 LFU는 miss에서 keys 미추적)."""
        cache = _LFUCache(capacity=3)
        cache.get("MISS", caller="svc")
        assert cache.caller_stats["svc"]["keys"]["MISS"] == 0

    def test_caller_stats_multiple_callers(self):
        """[신규] 여러 caller가 독립적으로 추적된다."""
        cache = _LFUCache(capacity=3)
        cache.put("A", 1)
        cache.get("A", caller="alpha")
        cache.get("MISS", caller="beta")
        assert cache.caller_stats["alpha"]["hits"] == 1
        assert cache.caller_stats["beta"]["misses"] == 1

    def test_caller_stats_accumulate_across_multiple_calls(self):
        """[신규] 동일 caller의 여러 호출이 누적된다."""
        cache = _LFUCache(capacity=3)
        cache.put("A", 1)
        cache.put("B", 2)
        cache.get("A", caller="svc", item_type="ohlcv")
        cache.get("A", caller="svc", item_type="ohlcv")
        cache.get("B", caller="svc", item_type="ohlcv")
        cache.get("C", caller="svc", item_type="ohlcv")  # miss
        stats = cache.caller_stats["svc"]
        assert stats["hits"] == 3
        assert stats["misses"] == 1
        assert stats["items"]["ohlcv"] == 4


class TestLFUCacheGetStats:
    def test_no_requests_returns_zero_hit_rate(self):
        cache = _LFUCache(capacity=3)
        stats = cache.get_stats()
        assert stats["hit_rate"] == 0.0
        assert stats["total_requests"] == 0

    def test_expand_false_has_no_items_key(self):
        cache = _LFUCache(capacity=3)
        cache.put("A", 1)
        cache.get("A")
        stats = cache.get_stats(expand=False)
        assert "items" not in stats
        assert "callers" in stats

    def test_expand_false_callers_no_keys(self):
        """[신규] expand=False이면 callers에 keys 필드가 없다."""
        cache = _LFUCache(capacity=3)
        cache.put("A", 1)
        cache.get("A", caller="svc")
        stats = cache.get_stats(expand=False)
        assert "keys" not in stats["callers"]["svc"]

    def test_expand_true_includes_items(self):
        cache = _LFUCache(capacity=3)
        entry = {
            "ohlcv_historical": [1, 2, 3],
            "ohlcv_today": {"close": 100},
            "historical_complete": True,
        }
        cache.put("A", entry)
        cache.get("A")   # freq = 1
        stats = cache.get_stats(expand=True)
        assert "items" in stats
        a_item = stats["items"][0]
        assert a_item["code"] == "A"
        assert a_item["freq"] == 1
        assert a_item["ohlcv_count"] == 3
        assert a_item["has_today_candle"] is True
        assert a_item["historical_complete"] is True

    def test_expand_true_non_dict_value(self):
        cache = _LFUCache(capacity=3)
        cache.put("B", "not_a_dict")
        stats = cache.get_stats(expand=True)
        b_item = stats["items"][0]
        assert b_item["historical_complete"] is False
        assert b_item["ohlcv_count"] == 0
        assert b_item["has_today_candle"] is False

    def test_expand_true_items_sorted_by_freq_desc(self):
        cache = _LFUCache(capacity=3)
        cache.put("A", 1)
        cache.put("B", 2)
        cache.put("C", 3)
        cache.get("C")
        cache.get("C")
        cache.get("B")
        stats = cache.get_stats(expand=True)
        codes = [i["code"] for i in stats["items"]]
        assert codes[0] == "C"
        assert codes[1] == "B"

    def test_expand_true_callers_include_keys(self):
        """[신규] expand=True이면 callers에 keys 필드가 포함된다."""
        cache = _LFUCache(capacity=3)
        cache.put("A", 1)
        cache.get("A", caller="svc")
        stats = cache.get_stats(expand=True)
        assert "keys" in stats["callers"]["svc"]
        assert stats["callers"]["svc"]["keys"]["A"] == 1

    def test_expand_true_callers_keys_top20_limit(self):
        """[신규] callers keys는 상위 20개만 반환된다."""
        cache = _LFUCache(capacity=100)
        for i in range(30):
            code = f"CODE{i:02d}"
            cache.put(code, i)
            cache.get(code, caller="heavy_svc")
        stats = cache.get_stats(expand=True)
        assert len(stats["callers"]["heavy_svc"]["keys"]) <= 20

    def test_hit_rate_calculation(self):
        cache = _LFUCache(capacity=3)
        cache.put("A", 1)
        cache.get("A")    # hit
        cache.get("B")    # miss
        cache.get("C")    # miss
        stats = cache.get_stats()
        assert stats["hit_rate"] == round(1 / 3 * 100, 2)

    def test_current_size_reflects_cache_length(self):
        cache = _LFUCache(capacity=5)
        cache.put("A", 1)
        cache.put("B", 2)
        stats = cache.get_stats()
        assert stats["current_size"] == 2

    def test_callers_items_type_tracking(self):
        """[신규] items 필드로 item_type별 호출 횟수가 집계된다."""
        cache = _LFUCache(capacity=3)
        cache.put("A", 1)
        cache.get("A", caller="svc", item_type="ohlcv")
        cache.get("MISS", caller="svc", item_type="price")
        stats = cache.get_stats(expand=False)
        caller = stats["callers"]["svc"]
        assert caller["items"]["ohlcv"] == 1
        assert caller["items"]["price"] == 1
