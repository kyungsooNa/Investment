"""공통 저장소와 성능 프로파일러 초기화 조립."""

from typing import Any, TYPE_CHECKING

from core.cache.cache_store import CacheStore
from core.performance_profiler import PerformanceProfiler
from repositories.stock_repository import StockRepository

if TYPE_CHECKING:  # pragma: no cover
    from view.web.web_app_initializer import WebAppContext


class RepositoryBootstrap:
    """서비스 조립에 앞서 공통 인프라 저장소를 컨텍스트에 구성한다."""

    def __init__(self, context: "WebAppContext") -> None:
        self._ctx = context

    def run(self, config: dict[str, Any]) -> CacheStore:
        ctx = self._ctx
        perf_log = config.get("performance_logging", False)
        perf_threshold = config.get("performance_threshold", 0.1)
        ctx.pm = PerformanceProfiler(enabled=perf_log, threshold=perf_threshold)

        try:
            cache_store = CacheStore(config)
            cache_store.set_logger(ctx.logger)
            ctx.stock_repository = StockRepository(logger=ctx.logger)
            return cache_store
        except Exception as exc:
            ctx.logger.critical(
                f"[ServiceBootstrap:Repository] 초기화 실패: {exc}",
                exc_info=True,
            )
            raise
