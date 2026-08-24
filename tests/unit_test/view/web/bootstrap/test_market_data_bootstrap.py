from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

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


def _ctx():
    return SimpleNamespace(
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


def test_theme_leader_failure_clears_the_whole_theme_section():
    """테마 계층 초기화가 깨지면 부분 배선을 남기지 않고 전부 None 으로 되돌린다."""
    ctx = _ctx()

    with patch("view.web.bootstrap.market_data_bootstrap.MarketDataService"), \
         patch("view.web.bootstrap.market_data_bootstrap.IndicatorService"), \
         patch("view.web.bootstrap.market_data_bootstrap.DataQualityService"), \
         patch(
             "view.web.bootstrap.market_data_bootstrap.StockClassificationRepository",
             side_effect=RuntimeError("분류 DB 손상"),
         ):
        MarketDataBootstrap(ctx, us_market_calendar_factory=MagicMock()).run(MagicMock())

    assert ctx.theme_classification_repository is None
    assert ctx.theme_leader_service is None
    assert ctx.theme_daily_leader_service is None
    assert ctx.theme_trading_value_snapshot_repository is None
    ctx.logger.warning.assert_called()


def test_data_quality_failure_is_logged_as_critical_and_reraised():
    ctx = _ctx()

    with patch("view.web.bootstrap.market_data_bootstrap.MarketDataService"), \
         patch("view.web.bootstrap.market_data_bootstrap.IndicatorService"), \
         patch("view.web.bootstrap.market_data_bootstrap.ThemeTradingValueSnapshotRepository"), \
         patch(
             "view.web.bootstrap.market_data_bootstrap.DataQualityService",
             side_effect=RuntimeError("품질 서비스 초기화 실패"),
         ):
        with pytest.raises(RuntimeError, match="품질 서비스 초기화 실패"):
            MarketDataBootstrap(ctx, us_market_calendar_factory=MagicMock()).run(MagicMock())

    ctx.logger.critical.assert_called_once()
