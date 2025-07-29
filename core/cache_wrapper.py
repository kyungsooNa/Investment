# core/cache_wrapper.py
from typing import TypeVar
from core.cache_manager import cache_manager


CACHED_METHODS = {
    "get_stock_info_by_code",
    "get_top_market_cap_stocks_code",
    "get_current_price",
    "get_price_summary",
    "get_market_cap",
    "get_filtered_stocks_by_momentum",
    "inquire_daily_itemchartprice",
    "get_top_rise_fall_stocks",
    "get_top_volume_stocks",
    "get_top_foreign_buying_stocks",
    "get_stock_news",
    "get_etf_info",
    "search_stocks_by_keyword",
}

T = TypeVar("T")


class ClientWithCache:
    def __init__(self, client, logger):
        self._client = client
        self._logger = logger  # ✅ 로거 주입 @TODO 추후 Logger.get_instance()

    def __getattr__(self, name: str):
        attr = getattr(self._client, name)

        if callable(attr) and name in CACHED_METHODS:
            async def cached_func(*args, **kwargs):
                key = f"{name}_" + "_".join(map(str, args)) + "_" + "_".join(f"{k}={v}" for k, v in kwargs.items())

                cached = cache_manager.get(key)
                if cached is not None:
                    self._logger.debug(f"🟢 Cache HIT - {name} | key: {key}")
                    return cached

                self._logger.debug(f"🟡 Cache MISS - {name} | key: {key} → calling API")
                result = await attr(*args, **kwargs)
                cache_manager.set(key, result)
                self._logger.debug(f"✅ Cached result - {name} | key: {key}")
                return result

            return cached_func

        if callable(attr):
            self._logger.debug(f"🔄 Bypass - {name} (not in CACHED_METHODS)")
        return attr

    def __dir__(self):
        # 포함해야 할 속성 목록:
        # 1. self._client의 속성
        # 2. self 객체의 __dict__ 속성
        # 3. 클래스 자체의 속성
        return list(set(
            dir(self._client) +
            list(self.__dict__.keys()) +
            dir(type(self))
        ))

def cache_wrap_client(api_client: T, logger) -> T:
    return ClientWithCache(api_client, logger)
