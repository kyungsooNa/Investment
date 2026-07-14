from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from view.web.bootstrap.query_bootstrap import QueryBootstrap


def test_query_bootstrap_builds_domestic_ranking_and_query_service():
    ctx = SimpleNamespace(
        broker=MagicMock(),
        stock_code_repository=MagicMock(),
        env=MagicMock(),
        logger=MagicMock(),
        market_clock=MagicMock(),
        pm=MagicMock(),
        notification_service=MagicMock(),
        telegram_reporter=MagicMock(),
        _mcs=MagicMock(),
        market_data_service=MagicMock(),
        worker_pool=MagicMock(),
        stock_repository=MagicMock(),
        theme_classification_repository=MagicMock(),
        indicator_service=MagicMock(),
        streaming_event_logger=MagicMock(),
    )

    with patch("view.web.bootstrap.query_bootstrap.RankingTask") as ranking, \
         patch("view.web.bootstrap.query_bootstrap.StockQueryService") as query, \
         patch("view.web.bootstrap.query_bootstrap.PeriodRankingRepository") as period_repo:
        QueryBootstrap(ctx, us_market_calendar_factory=MagicMock()).run(
            config={},
            is_overseas_us=False,
            needs_batch=False,
        )

    assert ctx.ranking_task is ranking.return_value
    assert ctx.stock_query_service is query.return_value
    assert ctx.market_cap_gap_service is None
    assert ctx.ytd_ranking_report_task is None
    _, kwargs = ranking.call_args
    assert kwargs["period_ranking_repository"] is period_repo.return_value


def test_query_bootstrap_builds_ytd_weekly_report_for_batch_mode():
    ctx = SimpleNamespace(
        broker=MagicMock(), stock_code_repository=MagicMock(), stock_repository=MagicMock(),
        env=MagicMock(), logger=MagicMock(), market_clock=MagicMock(), pm=MagicMock(),
        notification_service=MagicMock(), telegram_reporter=MagicMock(), _mcs=MagicMock(),
        market_data_service=MagicMock(), worker_pool=MagicMock(),
        theme_classification_repository=MagicMock(), indicator_service=MagicMock(),
        streaming_event_logger=MagicMock(),
    )

    with patch("view.web.bootstrap.query_bootstrap.RankingTask"), \
         patch("view.web.bootstrap.query_bootstrap.StockQueryService"), \
         patch("view.web.bootstrap.query_bootstrap.PeriodRankingRepository"), \
         patch("view.web.bootstrap.query_bootstrap.MarketCapGapService"), \
         patch("view.web.bootstrap.query_bootstrap.MarketCapGapReportTask"), \
         patch("view.web.bootstrap.query_bootstrap.YtdRankingReportTask") as ytd_task, \
         patch("view.web.bootstrap.query_bootstrap.StrategySchedulerStore"):
        QueryBootstrap(ctx, us_market_calendar_factory=MagicMock()).run(
            config={}, is_overseas_us=False, needs_batch=True,
        )

    assert ctx.ytd_ranking_report_task is ytd_task.return_value
    _, kwargs = ytd_task.call_args
    assert kwargs["stock_repository"] is ctx.stock_repository
    assert kwargs["worker_pool"] is ctx.worker_pool
