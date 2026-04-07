# repositories/stock_price_repository.py
"""
현재가 인메모리 캐시 및 WebSocket 스트리밍 상태를 전담하는 Repository.
- 용량 3000: KOSPI+KOSDAQ 전종목을 커버하여 대량 스캔 시에도 eviction 없음
- TTL: streaming 종목은 ∞, non-streaming 종목은 기본 3초
"""
import time
import logging
from typing import Optional, TYPE_CHECKING

from repositories.cache import _LRUCache

if TYPE_CHECKING:
    from core.logger import CacheEventLogger

_PRICE_CACHE_CAPACITY = 3000


class StockPriceRepository:
    """현재가 캐시 및 WebSocket 스트리밍 TTL 관리 저장소."""

    def __init__(self, logger=None, cache_logger: "CacheEventLogger | None" = None):
        self._logger = logger or logging.getLogger(__name__)
        self._cache_logger = cache_logger
        # 전종목(~2300) + 여유분을 수용하는 현재가 전용 캐시
        self._price_cache = _LRUCache(
            capacity=_PRICE_CACHE_CAPACITY,
            on_evict=self._on_price_evicted,
        )
        # 현재 WebSocket으로 실시간 스트리밍 중인 종목 코드 집합
        self._streaming_codes: set = set()

    def _on_price_evicted(self, code: str) -> None:
        if self._cache_logger:
            self._cache_logger.log_price_evicted(code, capacity=self._price_cache.capacity)

    def set_current_price(self, code: str, price_data: dict):
        """현재가 API 응답 전체 데이터를 캐시에 저장합니다."""
        cached = self._price_cache.get(code, count_stats=False, item_type="set_price")
        is_new = cached is None
        if self._cache_logger:
            before_price = None
            if not is_new and isinstance(cached, dict):
                existing = cached.get("current_price_data")
                if isinstance(existing, dict):
                    _out = existing.get("output", {})
                    before_price = (_out.get("stck_prpr") if isinstance(_out, dict) else getattr(_out, "stck_prpr", None)) if "output" in existing else existing.get("stck_prpr")
            after_price = None
            if isinstance(price_data, dict):
                _out = price_data.get("output", {})
                after_price = (_out.get("stck_prpr") if isinstance(_out, dict) else getattr(_out, "stck_prpr", None)) if "output" in price_data else price_data.get("stck_prpr")
            self._cache_logger.log_price_set(code, "api", before_price, after_price, is_new)
        if not cached:
            cached = {}
            self._price_cache.put(code, cached)
        cached["current_price_data"] = price_data
        cached["price_updated_at"] = time.time()

    def get_current_price(self, code: str, max_age_sec: float = 3.0,
                          count_stats: bool = True, caller: str = "unknown") -> Optional[dict]:
        """캐시된 현재가 데이터를 반환합니다. TTL 만료 시 None 반환."""
        cached = self._price_cache.get(code, count_stats=count_stats,
                                       caller=caller, item_type="current_price")
        if cached and "current_price_data" in cached:
            is_streaming = code in self._streaming_codes
            effective_max_age = float('inf') if is_streaming else max_age_sec
            age_sec = time.time() - cached.get("price_updated_at", 0)
            if age_sec <= effective_max_age:
                if self._cache_logger and count_stats:
                    self._cache_logger.log_price_hit(code, caller, age_sec, is_streaming)
                return cached["current_price_data"]
            if self._cache_logger and count_stats:
                self._cache_logger.log_price_miss(code, caller, "ttl_expired")
            return None
        if self._cache_logger and count_stats:
            self._cache_logger.log_price_miss(code, caller, "not_found")
        return None

    def update_current_price(self, code: str, current_price: float, volume: int = 0):
        """WebSocket 틱 데이터로 현재가 캐시를 즉시 갱신합니다."""
        cached = self._price_cache.get(code, count_stats=False, item_type="update_tick")
        if not cached:
            cached = {}
            self._price_cache.put(code, cached)

        if "current_price_data" not in cached:
            cached["current_price_data"] = {"output": {}}

        output = cached["current_price_data"].get("output")
        before_price = None
        if isinstance(output, dict):
            before_price = output.get("stck_prpr")
            output["stck_prpr"] = str(int(current_price))
            if volume > 0:
                output["acml_vol"] = str(volume)
        elif output is not None:
            try:
                before_price = getattr(output, "stck_prpr", None)
                setattr(output, "stck_prpr", str(int(current_price)))
                if volume > 0:
                    setattr(output, "acml_vol", str(volume))
            except Exception:
                pass

        cached["price_updated_at"] = time.time()
        if self._cache_logger and before_price != str(int(current_price)):
            self._cache_logger.log_price_update_tick(
                code, before_price, str(int(current_price)), volume
            )

    def mark_streaming(self, code: str) -> None:
        """해당 종목이 실시간 스트리밍 중임을 등록. TTL 우회 활성화."""
        self._streaming_codes.add(code)
        if self._cache_logger:
            self._cache_logger.log_streaming_mark(code, len(self._streaming_codes))

    def unmark_streaming(self, code: str) -> None:
        """실시간 스트리밍 종료. TTL 우회 해제."""
        self._streaming_codes.discard(code)
        if self._cache_logger:
            self._cache_logger.log_streaming_unmark(code, len(self._streaming_codes))

    def is_streaming(self, code: str) -> bool:
        """해당 종목이 현재 스트리밍 중인지 여부."""
        return code in self._streaming_codes

    def get_cache_stats(self, expand: bool = False) -> dict:
        """현재가 캐시 통계를 반환합니다."""
        stats = self._price_cache.get_stats(expand=expand)
        stats["streaming_count"] = len(self._streaming_codes)
        if expand and "items" in stats:
            for item in stats["items"]:
                item["is_streaming"] = item.get("code") in self._streaming_codes
        return stats
