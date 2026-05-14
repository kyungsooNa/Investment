"""/api/strategies/performance-by-regime 엔드포인트 단위 테스트.

API 함수를 직접 호출해 응답 형식과 버킷 집계를 검증한다.
"""
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from view.web.routes.strategy_report import get_performance_by_regime


def _trade_row(*, strategy="PP", code="005930", buy_date="2026-05-14", buy_price=100,
               sell_price=110, status="SOLD", market_regime=None):
    row = {
        "strategy": strategy,
        "code": code,
        "buy_date": buy_date,
        "buy_price": buy_price,
        "sell_price": sell_price,
        "qty": 1,
        "status": status,
        "reason": "target",
        "return_rate": 10.0,
    }
    if market_regime is not None:
        row["market_regime"] = market_regime
    return row


@pytest.mark.asyncio
async def test_response_shape_contains_all_buckets():
    """응답에 5개 regime 버킷이 모두 포함된다."""
    ctx = SimpleNamespace(virtual_trade_service=SimpleNamespace(get_all_trades=lambda apply_cost=True: []))
    with patch("view.web.routes.strategy_report._get_ctx", return_value=ctx):
        res = await get_performance_by_regime()
    assert set(res["buckets"].keys()) == {
        "KOSPI_BULL", "KOSDAQ_BULL", "SIDEWAYS", "BEAR", "TRADING_VALUE_SURGE",
    }
    for b in res["buckets"].values():
        assert b["trade_count"] == 0


@pytest.mark.asyncio
async def test_aggregates_kospi_bull_trade_after_normalization():
    """market_regime=KOSPI bull 인 SOLD trade 가 KOSPI_BULL 버킷에 집계된다."""
    trades = [_trade_row(market_regime={
        "kospi": "bull", "kosdaq": "bull", "stock_market": "KOSPI",
    })]
    ctx = SimpleNamespace(virtual_trade_service=SimpleNamespace(get_all_trades=lambda apply_cost=True: trades))
    with patch("view.web.routes.strategy_report._get_ctx", return_value=ctx):
        res = await get_performance_by_regime(to_date="20260514")
    assert res["buckets"]["KOSPI_BULL"]["trade_count"] == 1


@pytest.mark.asyncio
async def test_strategy_filter_excludes_other_strategies():
    trades = [
        _trade_row(strategy="PP", market_regime={"kospi": "bull", "kosdaq": "bull", "stock_market": "KOSPI"}),
        _trade_row(strategy="HTF", market_regime={"kospi": "bull", "kosdaq": "bull", "stock_market": "KOSPI"}),
    ]
    ctx = SimpleNamespace(virtual_trade_service=SimpleNamespace(get_all_trades=lambda apply_cost=True: trades))
    with patch("view.web.routes.strategy_report._get_ctx", return_value=ctx):
        res = await get_performance_by_regime(strategy="PP", to_date="20260514")
    assert res["buckets"]["KOSPI_BULL"]["trade_count"] == 1


@pytest.mark.asyncio
async def test_from_date_filter():
    """from_date 이전 trades 는 제외된다."""
    trades = [
        _trade_row(buy_date="2026-05-10", market_regime={"kospi": "bull", "kosdaq": "bull", "stock_market": "KOSPI"}),
        _trade_row(buy_date="2026-05-14", market_regime={"kospi": "bull", "kosdaq": "bull", "stock_market": "KOSPI"}),
    ]
    ctx = SimpleNamespace(virtual_trade_service=SimpleNamespace(get_all_trades=lambda apply_cost=True: trades))
    with patch("view.web.routes.strategy_report._get_ctx", return_value=ctx):
        res = await get_performance_by_regime(from_date="20260514", to_date="20260514")
    assert res["buckets"]["KOSPI_BULL"]["trade_count"] == 1


@pytest.mark.asyncio
async def test_works_without_virtual_trade_service():
    """ctx 에 virtual_trade_service 가 없으면 모든 버킷이 0."""
    ctx = SimpleNamespace()  # virtual_trade_service 미존재
    with patch("view.web.routes.strategy_report._get_ctx", return_value=ctx):
        res = await get_performance_by_regime()
    assert all(b["trade_count"] == 0 for b in res["buckets"].values())
