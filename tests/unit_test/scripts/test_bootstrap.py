from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from scripts import _bootstrap


@pytest.mark.asyncio
async def test_bootstrap_pp_strategy_injects_broker_into_stock_query_service():
    env = MagicMock()
    env.get_access_token = AsyncMock(return_value=True)
    env.get_real_access_token = AsyncMock(return_value=True)
    broker = MagicMock()
    market_clock = MagicMock()
    market_calendar = MagicMock()
    stock_query_service = MagicMock()

    with (
        patch.object(_bootstrap, "load_configs", return_value={}),
        patch.object(_bootstrap, "MarketClock", return_value=market_clock),
        patch.object(_bootstrap, "StockCodeRepository", return_value=MagicMock()),
        patch.object(_bootstrap, "MarketCalendarService", return_value=market_calendar),
        patch.object(_bootstrap, "KoreaInvestApiEnv", return_value=env),
        patch.object(_bootstrap, "BrokerAPIWrapper", return_value=broker),
        patch.object(_bootstrap, "PerformanceProfiler", return_value=MagicMock()),
        patch.object(_bootstrap, "CacheStore", return_value=MagicMock()),
        patch.object(_bootstrap, "MarketDataService", return_value=MagicMock()),
        patch.object(_bootstrap, "IndicatorService", return_value=MagicMock()),
        patch.object(_bootstrap, "StockQueryService", return_value=stock_query_service) as stock_query_cls,
        patch.object(_bootstrap, "OneilUniverseService", return_value=MagicMock()),
    ):
        result_sqs, _, _ = await _bootstrap.bootstrap_pp_strategy()

    assert result_sqs is stock_query_service
    assert stock_query_cls.call_args.kwargs["broker_api_wrapper"] is broker
