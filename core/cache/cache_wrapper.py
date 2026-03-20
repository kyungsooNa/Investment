# core/cache_wrapper.py
from typing import TypeVar, Callable, Optional, Any

from common.types import ResCommonResponse, ErrorCode
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
            config: Optional[dict] = None,
            market_calendar_service: Optional[Any] = None  # [추가] MarketCalendarService 주입
    ):
        self._client = client
        self._logger = logger
        self._time_manager = time_manager
        self._mcs = market_calendar_service  # [추가]
        self._mode_fn = mode_fn  # 동적으로 모드 가져오기

        # ✅ 설정에서 읽기
        if config is None:
            config = load_cache_config()

        cache_cfg = config.get("cache", {})

        self._file_enabled = bool(cache_cfg.get("file_cache_enabled", True))
        self._memory_enabled = bool(cache_cfg.get("memory_cache_enabled", True))
        self._caching_enabled = self._file_enabled or self._memory_enabled

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

        def _build_cache_key(mode: str, func_name: str, args: tuple, kwargs: dict) -> str:
            arg_str = "_".join(map(str, args)) if args else ""
            kwarg_str = "_".join(f"{k}={v}" for k, v in sorted(kwargs.items())) if kwargs else ""
            parts = [p for p in [mode, func_name, arg_str, kwarg_str] if p]
            return "_".join(parts)

        async def wrapped(*args, **kwargs):
            # _skip_cache 플래그가 있으면 캐시를 완전히 우회 (무한 재귀 방지용)
            skip_cache = kwargs.pop("_skip_cache", False)
            if skip_cache:
                self._logger.debug(f"🔓 _skip_cache=True → 캐시 우회: {name}")
                return await orig_attr(*args, **kwargs)

            mode = self._mode_fn() or "unknown"
            key = _build_cache_key(mode, name, args, kwargs)

            # 캐시 전체 비활성화면 즉시 API 호출
            if not self._caching_enabled:
                self._logger.debug(f"🧺 Caching disabled → direct API call: {key}")
                return await orig_attr(*args, **kwargs)

            # ✅ 1. 메모리 or 파일 캐시 조회
            # [수정] is_market_open_now는 async 메서드이므로 await 필요
            is_open = False
            if self._mcs:
                is_open = await self._mcs.is_market_open_now()

            if is_open:
                self._logger.debug(f"⏳ 시장 개장 중 → 캐시 우회: {key}")
            else:
                raw = self._cache.get_raw(key)
                wrapper, cache_type = raw if raw is not None else (None, None)

                if wrapper:
                    cache_time = self._parse_timestamp(wrapper.get("timestamp"))
                    
                    is_valid = False
                    # [수정] next_open_time도 async 메서드
                    next_open_time = await self._mcs.get_next_open_time() if self._mcs else None

                    if cache_time and next_open_time and cache_time < next_open_time:
                        # [수정] MarketCalendarService가 있으면 실제 거래일 기준으로 검증
                        if self._mcs:
                            latest_trading_date_str = await self._mcs.get_latest_trading_date()
                            if latest_trading_date_str:
                                cache_date_str = cache_time.strftime("%Y%m%d")
                                if cache_date_str > latest_trading_date_str:
                                    # 캐시가 최근 거래일 이후에 저장됨 → 유효
                                    is_valid = True
                                elif cache_date_str == latest_trading_date_str:
                                    # 캐시 날짜 == 최근 거래일: 장 마감(15:30) 이후에 저장된 경우만 유효
                                    # 장 마감 전에 저장된 캐시는 전일 데이터이므로 무효
                                    # 최근 거래일 날짜 기준 장 마감 시간과 비교 (다음날 접근 시에도 정확히 비교)
                                    latest_trading_date_dt = datetime.strptime(latest_trading_date_str, "%Y%m%d")
                                    market_close = self._time_manager.get_market_close_time(target_dt=latest_trading_date_dt)
                                    if cache_time >= market_close:
                                        is_valid = True
                                    else:
                                        self._logger.debug(
                                            f"📉 캐시 만료 (거래일 {latest_trading_date_str} 장 마감 전 저장: {cache_time})"
                                        )
                                else:
                                    self._logger.debug(f"📉 캐시 만료 (최근 거래일 {latest_trading_date_str} > 캐시 데이터 {cache_date_str})")
                            else:
                                # mcs이 거래일을 확인할 수 없는 경우 캐시를 유효한 것으로 간주
                                is_valid = True

                    if is_valid:
                        if cache_type == "memory":
                            if self._cache.memory_cache and self._cache.memory_cache.has(key):
                                self._logger.debug(f"🧠 Memory Cache HIT (유효): {key}")
                        elif cache_type == "file":
                            if self._cache.file_cache and self._cache.file_cache.exists(key):
                                self._logger.debug(f"📂 File Cache HIT (유효): {key}")
                        cached_result = wrapper.get("data")
                        try:
                            if cached_result is not None:
                                cached_result._cache_hit = True
                        except AttributeError:
                            pass  # dict 등 속성 설정 불가 타입
                        return cached_result
                    else:
                        if self._cache.file_cache and self._cache.file_cache.exists(key):
                            self._logger.debug(f"📂 File Cache 무시 (만료됨): {key} / 저장 시각: {cache_time}")
                            self._cache.file_cache.delete(key)
                        if self._cache.memory_cache:
                            self._cache.memory_cache.delete(key)

            # ✅ 2. API 호출
            self._logger.debug(f"🌐 실시간 API 호출: {key}")
            result = await orig_attr(*args, **kwargs)

            if isinstance(result, ResCommonResponse) and result.rt_cd == ErrorCode.SUCCESS.value:
                # ✅ 3. 캐싱 데이터 저장
                self._cache.set(key, {
                    "data": result,
                    "timestamp": datetime.now().isoformat()
                }, save_to_file=True)
            else:
                self._logger.debug(f"응답 실패로 🧠📂 Cache Update 무시 : {key}")

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
        cache_manager: Optional[CacheManager] = None,
        market_calendar_service: Optional[Any] = None
) -> T:
    return ClientWithCache(
        client=api_client,
        logger=logger,
        time_manager=time_manager,
        mode_fn=mode_getter,
        cache_manager=cache_manager,
        config=config,
        market_calendar_service=market_calendar_service
    )
