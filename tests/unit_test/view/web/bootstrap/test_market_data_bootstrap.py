from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from view.web.bootstrap.market_data_bootstrap import MarketDataBootstrap


def test_market_data_bootstrap_builds_core_market_data_services():
    ctx = SimpleNamespace(
        logger=MagicMock(),
        broker=MagicMock(),
        env=SimpleNamespace(is_paper_trading=True),
        market_clock=MagicMock(),
        _mcs=MagicMock(),
        pm=MagicMock(),
        stock_repository=MagicMock(),
        stock_code_repository=MagicMock(),
        operator_alert_service=MagicMock(),
        full_config={},
        enabled_market_modes=["domestic"],
    )
    cache_store = MagicMock()

    with patch("view.web.bootstrap.market_data_bootstrap.MarketDataService") as market_data, \
         patch("view.web.bootstrap.market_data_bootstrap.IndicatorService") as indicator, \
         patch("view.web.bootstrap.market_data_bootstrap.DataQualityService") as quality, \
         patch("view.web.bootstrap.market_data_bootstrap.ThemeTradingValueSnapshotRepository") as snapshots:
        MarketDataBootstrap(
            ctx,
            us_market_calendar_factory=MagicMock(),
        ).run(cache_store)

    assert ctx.market_data_service is market_data.return_value
    assert ctx.indicator_service is indicator.return_value
    assert ctx.data_quality_service is quality.return_value
    assert ctx.theme_trading_value_snapshot_repository is snapshots.return_value
    quality.return_value.apply_trading_mode.assert_called_once_with(True)
