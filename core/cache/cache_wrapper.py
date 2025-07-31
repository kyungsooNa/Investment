# core/cache_wrapper.py
from typing import TypeVar
from core.cache.cache_manager import get_cache_manager
from typing import Callable
from core.cache.cache_config import load_cache_config


T = TypeVar("T")


class ClientWithCache:
    def __init__(self, client, logger, time_manager, mode_fn: Callable[[], str]):
        self._client = client
        self._logger = logger  # ✅ 로거 주입 @TODO 추후 Logger.get_instance()
        self._time_manager = time_manager  # ✅ 로거 주입 @TODO 추후 TimeManager.get_instance()
        self._mode_fn = mode_fn  # 동적으로 모드 가져오기
        get_cache_manager().set_logger(self._logger)

        # ✅ 설정에서 읽기
        _config = load_cache_config()
        self.cached_methods = set(_config["cache"]["enabled_methods"])

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
            cached = get_cache_manager().get(key)
            if cached is not None:
                return cached

            # ✅ 2. API 호출
            self._logger.debug(f"🌐 실시간 API 호출: {key}")
            result = await orig_attr(*args, **kwargs)

            # ✅ 3. 캐싱 조건 판단
            if not self._time_manager.is_market_open():
                get_cache_manager().set(key, result, save_to_file=True)
            else:
                get_cache_manager().set(key, result, save_to_file=False)

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


def cache_wrap_client(api_client: T, logger, time_manager, mode_getter: Callable[[], str]) -> T:
    return ClientWithCache(api_client, logger, time_manager, mode_getter)
