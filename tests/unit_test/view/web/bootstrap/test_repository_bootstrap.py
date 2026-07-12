from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from view.web.bootstrap.repository_bootstrap import RepositoryBootstrap


def test_repository_bootstrap_builds_profiler_cache_and_stock_repository():
    ctx = SimpleNamespace(logger=MagicMock(), pm=None, stock_repository=None)
    config = {"performance_logging": True, "performance_threshold": 0.25}

    with patch("view.web.bootstrap.repository_bootstrap.PerformanceProfiler") as profiler, \
         patch("view.web.bootstrap.repository_bootstrap.CacheStore") as cache_store, \
         patch("view.web.bootstrap.repository_bootstrap.StockRepository") as stock_repository:
        result = RepositoryBootstrap(ctx).run(config)

    profiler.assert_called_once_with(enabled=True, threshold=0.25)
    cache_store.assert_called_once_with(config)
    cache_store.return_value.set_logger.assert_called_once_with(ctx.logger)
    stock_repository.assert_called_once_with(logger=ctx.logger)
    assert ctx.pm is profiler.return_value
    assert ctx.stock_repository is stock_repository.return_value
    assert result is cache_store.return_value
