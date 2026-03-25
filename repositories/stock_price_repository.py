# repositories/stock_price_repository.py
"""
현재가 인메모리 캐시 및 WebSocket 스트리밍 상태를 전담하는 Repository.
- 용량 3000: KOSPI+KOSDAQ 전종목을 커버하여 대량 스캔 시에도 eviction 없음
- TTL: streaming 종목은 ∞, non-streaming 종목은 기본 3초
"""
import time
import logging
from typing import Optional

from repositories.cache import _LRUCache


class StockPriceRepository:
    """현재가 캐시 및 WebSocket 스트리밍 TTL 관리 저장소."""

    def __init__(self, logger=None):
        self._logger = logger or logging.getLogger(__name__)
        # 전종목(~2300) + 여유분을 수용하는 현재가 전용 캐시
        self._price_cache = _LRUCache(capacity=3000)
        # 현재 WebSocket으로 실시간 스트리밍 중인 종목 코드 집합
        self._streaming_codes: set = set()

    def set_current_price(self, code: str, price_data: dict):
        """현재가 API 응답 전체 데이터를 캐시에 저장합니다."""
        cached = self._price_cache.get(code, count_stats=False, item_type="set_price")
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
            effective_max_age = float('inf') if code in self._streaming_codes else max_age_sec
            if time.time() - cached.get("price_updated_at", 0) <= effective_max_age:
                return cached["current_price_data"]
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

        cached["price_updated_at"] = time.time()

    def mark_streaming(self, code: str) -> None:
        """해당 종목이 실시간 스트리밍 중임을 등록. TTL 우회 활성화."""
        self._streaming_codes.add(code)

    def unmark_streaming(self, code: str) -> None:
        """실시간 스트리밍 종료. TTL 우회 해제."""
        self._streaming_codes.discard(code)

    def is_streaming(self, code: str) -> bool:
        """해당 종목이 현재 스트리밍 중인지 여부."""
        return code in self._streaming_codes

    def get_cache_stats(self, expand: bool = False) -> dict:
        """현재가 캐시 통계를 반환합니다."""
        return self._price_cache.get_stats(expand=expand)
