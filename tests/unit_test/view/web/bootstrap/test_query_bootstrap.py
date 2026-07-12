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
        theme_classification_repository=MagicMock(),
        indicator_service=MagicMock(),
        streaming_event_logger=MagicMock(),
    )

    with patch("view.web.bootstrap.query_bootstrap.RankingTask") as ranking, \
         patch("view.web.bootstrap.query_bootstrap.StockQueryService") as query:
        QueryBootstrap(ctx, us_market_calendar_factory=MagicMock()).run(
            config={},
            is_overseas_us=False,
            needs_batch=False,
        )

    assert ctx.ranking_task is ranking.return_value
    assert ctx.stock_query_service is query.return_value
    assert ctx.market_cap_gap_service is None
