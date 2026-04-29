from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from common.types import ErrorCode, Exchange, ResCommonResponse
from services.execution_flow_service import ExecutionFlowService


def _response(data):
    return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data=data)


@pytest.mark.asyncio
async def test_get_snapshot_normalizes_conclusion_and_recent_trade_velocity():
    provider = AsyncMock()
    provider.get_stock_conclusion.return_value = _response({"output": [{"tday_rltv": "123.4"}]})
    provider.get_time_concluded_prices.return_value = _response({
        "output": [
            {"stck_cntg_hour": "090040", "stck_prpr": "10000", "cntg_vol": "10"},
            {"stck_cntg_hour": "090010", "stck_prpr": "10000", "cntg_vol": "20"},
            {"stck_cntg_hour": "085950", "stck_prpr": "10000", "cntg_vol": "30"},
        ]
    })
    clock = MagicMock()
    clock.get_current_kst_time.return_value = datetime(2026, 4, 30, 9, 1, 0)
    service = ExecutionFlowService(provider, market_clock=clock, sample_window_sec=60, cache_ttl_sec=0)

    snapshot = await service.get_snapshot("005930", Exchange.KRX)

    assert snapshot.execution_strength_pct == 123.4
    assert snapshot.recent_trade_count == 2
    assert snapshot.recent_trade_volume == 30
    assert snapshot.recent_trade_value_won == 300_000
    assert snapshot.trade_velocity_per_min == 2.0
    assert snapshot.volume_velocity_per_min == 30.0
    assert snapshot.last_trade_age_sec == 20.0


@pytest.mark.asyncio
async def test_get_snapshot_uses_rows_without_time_as_recent_sample():
    provider = AsyncMock()
    provider.get_stock_conclusion.return_value = _response({"output": [{"cgld": "99.5"}]})
    provider.get_time_concluded_prices.return_value = _response({
        "output": [
            {"stck_prpr": "10000", "cntg_vol": "10"},
            {"stck_prpr": "10100", "cntg_vol": "5"},
        ]
    })
    service = ExecutionFlowService(provider, sample_window_sec=60, cache_ttl_sec=0)

    snapshot = await service.get_snapshot("005930")

    assert snapshot.execution_strength_pct == 99.5
    assert snapshot.recent_trade_count == 2
    assert snapshot.recent_trade_volume == 15
    assert "last_trade_time_unavailable" in snapshot.quality_flags


@pytest.mark.asyncio
async def test_get_snapshot_marks_unavailable_sources():
    provider = AsyncMock()
    provider.get_stock_conclusion.return_value = ResCommonResponse(rt_cd="1", msg1="API error")
    provider.get_time_concluded_prices.return_value = ResCommonResponse(rt_cd="1", msg1="API error")
    service = ExecutionFlowService(provider, cache_ttl_sec=0)

    snapshot = await service.get_snapshot("005930")

    assert snapshot.execution_strength_pct is None
    assert snapshot.recent_trade_count is None
    assert snapshot.quality_flags == ["conclusion_unavailable", "time_concluded_unavailable"]


@pytest.mark.asyncio
async def test_get_snapshot_uses_short_ttl_cache():
    provider = AsyncMock()
    provider.get_stock_conclusion.return_value = _response({"output": [{"tday_rltv": "120"}]})
    provider.get_time_concluded_prices.return_value = _response({"output": []})
    service = ExecutionFlowService(provider, cache_ttl_sec=30)

    first = await service.get_snapshot("005930")
    second = await service.get_snapshot("005930")

    assert first is second
    provider.get_stock_conclusion.assert_awaited_once_with("005930", exchange=Exchange.KRX)
    provider.get_time_concluded_prices.assert_awaited_once_with("005930", exchange=Exchange.KRX)
