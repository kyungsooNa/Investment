"""Regression tests for config/cache_config.yaml invariants.

These tests pin policy decisions about which methods are eligible for
ClientWithCache's after-hours caching. The cache wrapper bypasses the cache
during market hours, but for after-hours it returns the cached payload until
the next trading day. That behavior is unsafe for methods that already have
short-lived caching elsewhere (e.g. StockRepository 3s cache + DB snapshot
fallback in MarketDataService), so those methods MUST NOT appear in
``enabled_methods``.
"""
from __future__ import annotations

from core.cache.cache_config import load_cache_config


def test_get_current_price_is_not_globally_cached():
    """get_current_price는 ClientWithCache의 전역 캐시 대상에서 제외되어야 한다.

    이유:
    - MarketDataService가 이미 자체적으로 3초 단기 캐시(StockRepository) +
      장 마감 후 DB 스냅샷 폴백을 운영한다.
    - ClientWithCache의 after-hours 캐시는 동일 거래일 내 첫 호출 결과를
      다음 거래일 개장 전까지 그대로 반환한다. force_fresh=True 의도가
      broker wrapper 계층까지 전파되지 않아 주문/손절/리스크 경로의 신선도
      요구와 충돌한다.
    """
    config = load_cache_config()
    enabled_methods = config["cache"]["enabled_methods"]
    assert "get_current_price" not in enabled_methods, (
        "get_current_price는 짧은 TTL이 필요한 핵심 시세 메서드이므로 "
        "enabled_methods에서 제거되어 있어야 한다. "
        "MarketDataService 내부의 단기 캐시/DB 스냅샷 폴백이 캐싱을 담당한다."
    )
