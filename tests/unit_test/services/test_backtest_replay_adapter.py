from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from common.types import ResCommonResponse, TradeSignal
from services.backtest_period_runner import BacktestExecutionBarPolicy, BacktestPeriodRunner
from services.backtest_execution_simulator import BacktestPortfolioLedger
from services.backtest_replay_adapter import (
    StockQueryBacktestReplayService,
    StockQueryDailyMtmBarProvider,
    StockQueryIntradayReplayBarProvider,
)


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
async def test_replay_provider_selects_next_bar_after_signal_bar_when_policy_requests_it():
    sqs = AsyncMock()
    sqs.get_day_intraday_minutes_list.return_value = [
        {
            "stck_bsop_date": "20260501",
            "stck_cntg_hour": "090000",
            "stck_oprc": "70600",
            "stck_hgpr": "71000",
            "stck_lwpr": "70400",
            "stck_prpr": "70500",
            "cntg_vol": "100",
        },
        {
            "stck_bsop_date": "20260501",
            "stck_cntg_hour": "090100",
            "stck_oprc": "70500",
            "stck_hgpr": "70600",
            "stck_lwpr": "70100",
            "stck_prpr": "70200",
            "cntg_vol": "200",
        },
    ]
    provider = StockQueryIntradayReplayBarProvider(sqs)

    bar = await provider.get_bar(
        signal=_signal(price=70_500),
        date_ymd="20260501",
        side="BUY",
        execution_policy=BacktestExecutionBarPolicy.NEXT_BAR,
    )

    assert bar.timestamp == "20260501 090100"
    assert bar.open == 70_500


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


@pytest.mark.asyncio
async def test_stock_query_backtest_replay_service_synthesizes_current_price_with_program_daily():
    sqs = AsyncMock()
    sqs.get_day_intraday_minutes_list.return_value = [
        {
            "stck_bsop_date": "20260501",
            "stck_cntg_hour": "090000",
            "stck_oprc": "70000",
            "stck_hgpr": "71000",
            "stck_lwpr": "69500",
            "stck_prpr": "70500",
            "cntg_vol": "10",
        },
        {
            "stck_bsop_date": "20260501",
            "stck_cntg_hour": "091000",
            "stck_oprc": "70600",
            "stck_hgpr": "72000",
            "stck_lwpr": "70400",
            "stck_prpr": "71800",
            "cntg_vol": "20",
            "stck_sdpr": "69000",
        },
    ]
    program_provider = AsyncMock()
    program_provider.get_program_trade_by_stock_daily.return_value = {
        "stck_bsop_date": "20260501",
        "whol_smtn_ntby_qty": "30000",
    }
    replay = StockQueryBacktestReplayService(sqs, program_provider=program_provider)
    replay.set_backtest_date("20260501")

    response = await replay.get_current_price("005930")

    output = response.data["output"]
    assert response.rt_cd == "0"
    assert output["stck_prpr"] == "71800"
    assert output["stck_oprc"] == "70000"
    assert output["stck_hgpr"] == "72000"
    assert output["stck_lwpr"] == "69500"
    assert output["acml_vol"] == "30"
    assert output["stck_sdpr"] == "69000"
    assert output["prdy_vrss"] == "2800"
    assert output["prdy_vrss_sign"] == "2"
    assert output["pgtr_ntby_qty"] == "30000"
    sqs.get_current_price.assert_not_awaited()
    program_provider.get_program_trade_by_stock_daily.assert_awaited_once_with("005930", "20260501")


@pytest.mark.asyncio
async def test_stock_query_backtest_replay_service_replays_execution_strength_from_intraday_rows():
    sqs = AsyncMock()
    sqs.get_day_intraday_minutes_list.return_value = [
        {"stck_bsop_date": "20260501", "stck_cntg_hour": "090000", "stck_prpr": "70000", "tday_rltv": "121.5"},
        {"stck_bsop_date": "20260501", "stck_cntg_hour": "091000", "stck_prpr": "71000", "execution_strength": "132.4"},
    ]
    replay = StockQueryBacktestReplayService(sqs)
    replay.set_backtest_date("20260501")

    response = await replay.get_stock_conclusion("005930")

    assert response.rt_cd == "0"
    assert response.data["output"] == [{"tday_rltv": "132.4"}]
    sqs.get_stock_conclusion.assert_not_awaited()


@pytest.mark.asyncio
async def test_stock_query_backtest_replay_service_uses_backtest_date_for_recent_daily_ohlcv():
    sqs = AsyncMock()
    sqs.get_recent_daily_ohlcv.return_value = "delegated"
    replay = StockQueryBacktestReplayService(sqs)
    replay.set_backtest_date("20260501")

    result = await replay.get_recent_daily_ohlcv("005930", limit=60)

    assert result == "delegated"
    sqs.get_recent_daily_ohlcv.assert_awaited_once_with("005930", limit=60, end_date="20260501")


@pytest.mark.asyncio
async def test_daily_mtm_provider_returns_only_intermediate_holding_daily_bars():
    sqs = AsyncMock()
    sqs.get_recent_daily_ohlcv.return_value = ResCommonResponse(
        rt_cd="0",
        msg1="OK",
        data=[
            {"date": "20260501", "open": "10000", "high": "11000", "low": "9900", "close": "10500", "volume": "10"},
            {"date": "20260502", "open": "10600", "high": "12000", "low": "10100", "close": "11800", "volume": "20"},
            {"date": "20260503", "open": "11700", "high": "11900", "low": "9700", "close": "9800", "volume": "30"},
            {"date": "20260504", "open": "9900", "high": "10000", "low": "9500", "close": "9600", "volume": "40"},
        ],
    )
    provider = StockQueryDailyMtmBarProvider(sqs)

    bars = await provider.get_holding_bars(
        code="005930",
        start_ymd="20260501",
        end_ymd="20260504",
    )

    assert [bar.timestamp for bar in bars] == ["20260502", "20260503"]
    assert bars[0].open == 10_600
    assert bars[0].high == 12_000
    assert bars[1].low == 9_700
    assert bars[1].close == 9_800
    assert bars[1].volume == 30
    sqs.get_recent_daily_ohlcv.assert_awaited_once_with(
        "005930",
        limit=14,
        end_date="20260504",
    )
