from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from common.types import TradeSignal
from services.backtest_period_runner import BacktestPeriodRunner
from services.backtest_execution_simulator import BacktestPortfolioLedger
from services.backtest_replay_adapter import StockQueryIntradayReplayBarProvider


def _signal(code="005930", price=70_000, qty=1, action="BUY"):
    return TradeSignal(
        code=code,
        name="삼성전자",
        action=action,
        price=price,
        qty=qty,
        reason="test",
        strategy_name="OneilPocketPivot",
    )


class FakeStrategy:
    name = "OneilPocketPivot"

    def __init__(self) -> None:
        self.date = ""

    def set_backtest_date(self, date_ymd: str) -> None:
        self.date = date_ymd

    async def scan(self):
        return [_signal(price=70_000, qty=1)] if self.date == "20260501" else []

    async def check_exits(self, holdings):
        if self.date == "20260502" and holdings:
            return [_signal(price=75_000, qty=1, action="SELL")]
        return []


@pytest.mark.asyncio
async def test_replay_provider_selects_first_reachable_buy_bar_and_normalizes_fields():
    sqs = AsyncMock()
    sqs.get_day_intraday_minutes_list.return_value = [
        {
            "stck_bsop_date": "20260501",
            "stck_cntg_hour": "090000",
            "stck_oprc": "71000",
            "stck_hgpr": "71500",
            "stck_lwpr": "70500",
            "stck_prpr": "71200",
            "cntg_vol": "10",
        },
        {
            "stck_bsop_date": "20260501",
            "stck_cntg_hour": "090100",
            "stck_oprc": "70400",
            "stck_hgpr": "70600",
            "stck_lwpr": "69900",
            "stck_prpr": "70000",
            "cntg_vol": "20",
        },
    ]
    provider = StockQueryIntradayReplayBarProvider(sqs)

    bar = await provider.get_bar(signal=_signal(price=70_000), date_ymd="20260501", side="BUY")

    assert bar.timestamp == "20260501 090100"
    assert bar.open == 70_400
    assert bar.high == 70_600
    assert bar.low == 69_900
    assert bar.close == 70_000
    assert bar.volume == 20


@pytest.mark.asyncio
async def test_replay_provider_uses_close_for_missing_ohlc_fields():
    sqs = AsyncMock()
    sqs.get_day_intraday_minutes_list.return_value = [
        {"date": "20260501", "time": "090000", "price": "70000", "volume": "3"},
    ]
    provider = StockQueryIntradayReplayBarProvider(sqs)

    bar = await provider.get_bar(signal=_signal(price=70_000), date_ymd="20260501", side="BUY")

    assert bar.open == 70_000
    assert bar.high == 70_000
    assert bar.low == 70_000
    assert bar.close == 70_000
    assert bar.volume == 3


@pytest.mark.asyncio
async def test_replay_provider_returns_last_bar_when_limit_is_not_reached():
    sqs = AsyncMock()
    sqs.get_day_intraday_minutes_list.return_value = [
        {"stck_bsop_date": "20260501", "stck_cntg_hour": "090000", "stck_prpr": "71000"},
        {"stck_bsop_date": "20260501", "stck_cntg_hour": "090100", "stck_prpr": "72000"},
    ]
    provider = StockQueryIntradayReplayBarProvider(sqs)

    bar = await provider.get_bar(signal=_signal(price=70_000), date_ymd="20260501", side="BUY")

    assert bar.timestamp == "20260501 090100"
    assert bar.close == 72_000


@pytest.mark.asyncio
async def test_replay_provider_caches_rows_by_code_date_and_session():
    sqs = AsyncMock()
    sqs.get_day_intraday_minutes_list.return_value = [
        {"stck_bsop_date": "20260501", "stck_cntg_hour": "090000", "stck_prpr": "70000"},
    ]
    provider = StockQueryIntradayReplayBarProvider(sqs)

    await provider.get_bar(signal=_signal(price=70_000), date_ymd="20260501", side="BUY")
    await provider.get_bar(signal=_signal(price=70_000), date_ymd="20260501", side="SELL")

    sqs.get_day_intraday_minutes_list.assert_awaited_once_with(
        "005930",
        date_ymd="20260501",
        session="REGULAR",
    )


@pytest.mark.asyncio
async def test_period_runner_uses_stock_query_replay_provider_end_to_end():
    sqs = AsyncMock()
    sqs.get_day_intraday_minutes_list.side_effect = [
        [
            {"stck_bsop_date": "20260501", "stck_cntg_hour": "090000", "stck_prpr": "70000", "cntg_vol": "100"},
        ],
        [
            {"stck_bsop_date": "20260502", "stck_cntg_hour": "100000", "stck_prpr": "75000", "cntg_vol": "100"},
        ],
    ]
    runner = BacktestPeriodRunner(
        strategy=FakeStrategy(),
        bar_provider=StockQueryIntradayReplayBarProvider(sqs),
        ledger=BacktestPortfolioLedger(initial_cash=1_000_000),
    )

    result = await runner.run(["20260501", "20260502"])

    assert [r.order.side.value for r in result.execution_reports] == ["BUY", "SELL"]
    assert result.execution_reports[0].fill_price == 70_000
    assert result.execution_reports[1].fill_price == 75_000
    assert result.portfolio["realized_net_pnl"] > 0


@pytest.mark.asyncio
async def test_replay_provider_raises_when_no_intraday_rows():
    sqs = AsyncMock()
    sqs.get_day_intraday_minutes_list.return_value = []
    provider = StockQueryIntradayReplayBarProvider(sqs)

    with pytest.raises(ValueError, match="intraday rows not found"):
        await provider.get_bar(signal=_signal(price=70_000), date_ymd="20260501", side="BUY")
