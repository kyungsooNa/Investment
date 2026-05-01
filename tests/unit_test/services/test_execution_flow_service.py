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


@pytest.mark.asyncio
async def test_safe_call_falls_back_to_legacy_provider_without_exchange():
    class LegacyProvider:
        def get_stock_conclusion(self, stock_code):
            return _response({"output": {"tday_rltv": "110"}})

        def get_time_concluded_prices(self, stock_code):
            return _response({"output": []})

    service = ExecutionFlowService(LegacyProvider(), cache_ttl_sec=0)

    snapshot = await service.get_snapshot("005930")

    assert snapshot.execution_strength_pct == 110.0


@pytest.mark.asyncio
async def test_safe_call_logs_and_marks_source_unavailable_on_exception():
    provider = MagicMock()
    provider.get_stock_conclusion.side_effect = RuntimeError("conclusion down")
    provider.get_time_concluded_prices.side_effect = RuntimeError("time down")
    logger = MagicMock()
    service = ExecutionFlowService(provider, logger=logger, cache_ttl_sec=0)

    snapshot = await service.get_snapshot("005930")

    assert snapshot.quality_flags == ["conclusion_unavailable", "time_concluded_unavailable"]
    assert logger.warning.call_count == 2


def test_rows_accept_to_dict_dict_output_list_and_plain_list():
    class RowContainer:
        def to_dict(self):
            return {"output": {"stck_prpr": "10000"}}

    assert ExecutionFlowService._rows(RowContainer()) == [{"stck_prpr": "10000"}]
    assert ExecutionFlowService._rows({"foo": "bar"}) == [{"foo": "bar"}]
    assert ExecutionFlowService._rows([{"a": 1}, "bad", {"b": 2}]) == [{"a": 1}, {"b": 2}]


def test_value_and_time_parsers_handle_explicit_and_invalid_inputs():
    measured_at = datetime(2026, 4, 30, 9, 0, 0)

    assert ExecutionFlowService._row_trade_value({"cntg_pbmn": "1,234", "cntg_vol": "99"}) == 1234
    assert ExecutionFlowService._to_float("bad") is None
    assert ExecutionFlowService._parse_trade_time({"stck_cntg_hour": "930"}, measured_at) is None
    assert ExecutionFlowService._parse_trade_time({"stck_cntg_hour": "246000"}, measured_at) is None
