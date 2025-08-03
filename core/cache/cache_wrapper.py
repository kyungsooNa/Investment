# core/cache_wrapper.py
from typing import TypeVar, Callable, Optional
from core.cache.cache_manager import CacheManager
from core.cache.cache_config import load_cache_config
from datetime import datetime

T = TypeVar("T")


class ClientWithCache:
    def __init__(
            self,
            client,
            logger,
            time_manager,
            mode_fn: Callable[[], str],
            cache_manager: Optional[CacheManager] = None,
            config: Optional[dict] = None
    ):
        self._client = client
        self._logger = logger  # ✅ 로거 주입 @TODO 추후 Logger.get_instance()
        self._time_manager = time_manager  # ✅ 로거 주입 @TODO 추후 TimeManager.get_instance()
        self._mode_fn = mode_fn  # 동적으로 모드 가져오기

        # ✅ 설정에서 읽기
        if config is None:
            config = load_cache_config()

        self._cache = cache_manager if cache_manager else CacheManager(config)
        self._cache.set_logger(self._logger)
        self.cached_methods = set(config["cache"]["enabled_methods"])

    def __getattr__(self, name: str):
        # ✅ 무한 루프 방지
        if name.startswith("_"):  # ✅ 내부 속성은 직접 접근
            return object.__getattribute__(self, name)

        orig_attr = getattr(self._client, name)

        if not callable(orig_attr) or name not in self.cached_methods:
            self._logger.debug(f"Bypass - {name} 캐시 건너뜀")
            return orig_attr

        def _build_cache_key(mode: str, func_name: str, args: tuple) -> str:
            arg_str = "_".join(map(str, args)) if args else ""
            return f"{mode}_{func_name}_{arg_str}"

        async def wrapped(*args, **kwargs):
            mode = self._mode_fn() or "unknown"
            key = _build_cache_key(mode, name, args)

            # ✅ 1. 메모리 or 파일 캐시 조회
            if self._time_manager.is_market_open():
                self._logger.debug(f"⏳ 시장 개장 중 → 캐시 우회: {key}")
            else:
                raw = self._cache.get_raw(key)
                wrapper, cache_type = raw if raw is not None else (None, None)

                if wrapper:
                    cache_time = self._parse_timestamp(wrapper.get("timestamp"))
                    latest_close_time = self._time_manager.get_latest_market_close_time()
                    next_open_time = self._time_manager.get_next_market_open_time()
                    is_valid = (cache_time and latest_close_time <= cache_time < next_open_time)

                    if is_valid:
                        if cache_type == "memory":
                            if self._cache.memory_cache.has(key):
                                self._logger.debug(f"🧠 Memory Cache HIT (유효): {key}")
                        elif cache_type == "file":
                            if self._cache.file_cache.exists(key):
                                self._logger.debug(f"📂 File Cache HIT (유효): {key}")
                        return wrapper.get("data")
                    else:
                        if self._cache.file_cache.exists(key):
                            self._logger.debug(f"📂 File Cache 무시 (만료됨): {key} / 저장 시각: {cache_time}")
                            self._cache.file_cache.delete(key)
                        self._cache.memory_cache.delete(key)

            # ✅ 2. API 호출
            self._logger.debug(f"🌐 실시간 API 호출: {key}")
            result = await orig_attr(*args, **kwargs)

            # ✅ 3. 캐싱 데이터 저장
            self._cache.set(key, {
                "data": result,
                "timestamp": datetime.now().isoformat()
            }, save_to_file=True)

            return result

        return wrapped

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

    def _parse_timestamp(self, timestamp_str: str) -> Optional[datetime]:
        if not timestamp_str:
            return None
        try:
            dt = datetime.fromisoformat(timestamp_str)
            if dt.tzinfo is None:
                return self._time_manager.market_timezone.localize(dt)
            return dt
        except Exception as e:
            if self._logger:
                self._logger.warning(f"[CacheWrapper] 잘못된 timestamp 포맷: {timestamp_str} ({e})")
            return None


def cache_wrap_client(
        api_client: T,
        logger,
        time_manager,
        mode_getter: Callable[[], str],
        config: Optional[dict] = None,
        cache_manager: Optional[CacheManager] = None
) -> T:
    return ClientWithCache(
        client=api_client,
        logger=logger,
        time_manager=time_manager,
        mode_fn=mode_getter,
        cache_manager=cache_manager,
        config=config
    )
