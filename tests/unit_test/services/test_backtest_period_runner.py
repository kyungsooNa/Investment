from __future__ import annotations

from dataclasses import dataclass

import pytest

from common.types import TradeSignal
from services.backtest_execution_simulator import BacktestBar, BacktestPortfolioLedger
from services.backtest_period_runner import BacktestPeriodRunner, BacktestPeriodRunnerConfig


class FakeStrategy:
    name = "OneilPocketPivot"

    def __init__(self) -> None:
        self.current_date = ""
        self.scan_calls: list[str] = []
        self.exit_holdings: list[list[dict]] = []

    def set_backtest_date(self, date_ymd: str) -> None:
        self.current_date = date_ymd

    async def scan(self):
        self.scan_calls.append(self.current_date)
        if self.current_date == "20260501":
            return [
                TradeSignal(
                    code="005930",
                    name="삼성전자",
                    action="BUY",
                    price=70_000,
                    qty=2,
                    reason="pocket_pivot",
                    strategy_name=self.name,
                )
            ]
        return []

    async def check_exits(self, holdings):
        self.exit_holdings.append(holdings)
        if self.current_date == "20260502" and holdings:
            return [
                TradeSignal(
                    code="005930",
                    name="삼성전자",
                    action="SELL",
                    price=77_000,
                    qty=2,
                    reason="target_hit",
                    strategy_name=self.name,
                )
            ]
        return []


@dataclass
class StaticBarProvider:
    bars: dict[tuple[str, str, str], BacktestBar]

    async def get_bar(self, *, signal: TradeSignal, date_ymd: str, side: str) -> BacktestBar:
        return self.bars[(date_ymd, signal.code, side)]


class DatedStaticBarProvider(StaticBarProvider):
    def __init__(self, bars: dict[tuple[str, str, str], BacktestBar]) -> None:
        super().__init__(bars)
        self.dates: list[str] = []

    def set_backtest_date(self, date_ymd: str) -> None:
        self.dates.append(date_ymd)


class FakeBacktestJournalRepository:
    def __init__(self) -> None:
        self.saved_runs: list[dict] = []

    def save_run(self, records, *, run_id=None, strategy="", target_date="", metadata=None):
        saved = {
            "records": list(records),
            "run_id": run_id,
            "strategy": strategy,
            "target_date": target_date,
            "metadata": metadata or {},
            "record_count": len(list(records)),
        }
        self.saved_runs.append(saved)
        return {
            "run_id": run_id,
            "strategy": strategy,
            "target_date": target_date,
            "record_count": saved["record_count"],
            "metadata": saved["metadata"],
        }


@pytest.mark.asyncio
async def test_period_runner_executes_buy_and_sell_through_ledger():
    strategy = FakeStrategy()
    provider = StaticBarProvider({
        ("20260501", "005930", "BUY"): BacktestBar("20260501 091000", 70_000, 70_500, 69_500, 70_200, 1_000),
        ("20260502", "005930", "SELL"): BacktestBar("20260502 100000", 77_000, 77_500, 76_500, 77_100, 1_000),
    })
    ledger = BacktestPortfolioLedger(initial_cash=1_000_000)
    runner = BacktestPeriodRunner(strategy=strategy, bar_provider=provider, ledger=ledger)

    result = await runner.run(["20260501", "20260502"])

    assert result.strategy_name == "OneilPocketPivot"
    assert [report.order.side.value for report in result.execution_reports] == ["BUY", "SELL"]
    assert result.execution_reports[0].fill_price == 70_000
    assert result.execution_reports[1].fill_price == 77_000
    assert result.portfolio["positions"] == {}
    assert result.portfolio["cash"] > 1_000_000
    assert result.portfolio["realized_net_pnl"] > 0
    assert strategy.exit_holdings[-1][0]["code"] == "005930"
    assert strategy.exit_holdings[-1][0]["qty"] == 2


@pytest.mark.asyncio
async def test_period_runner_records_cash_short_as_rejected_journal():
    strategy = FakeStrategy()
    provider = StaticBarProvider({
        ("20260501", "005930", "BUY"): BacktestBar("20260501 091000", 70_000, 70_500, 69_500, 70_200, 1_000),
    })
    ledger = BacktestPortfolioLedger(initial_cash=100_000)
    runner = BacktestPeriodRunner(strategy=strategy, bar_provider=provider, ledger=ledger)

    result = await runner.run(["20260501"])

    assert result.execution_reports == []
    assert result.journal_records[0]["status"] == "REJECTED"
    assert result.journal_records[0]["code"] == "005930"
    assert result.journal_records[0]["rejected_reason"] == "cash_short"


@pytest.mark.asyncio
async def test_period_runner_applies_strategy_max_positions():
    strategy = FakeStrategy()
    provider = StaticBarProvider({
        ("20260501", "005930", "BUY"): BacktestBar("20260501 091000", 70_000, 70_500, 69_500, 70_200, 1_000),
    })
    ledger = BacktestPortfolioLedger(initial_cash=1_000_000)
    runner = BacktestPeriodRunner(
        strategy=strategy,
        bar_provider=provider,
        ledger=ledger,
        config=BacktestPeriodRunnerConfig(max_positions_per_strategy={"OneilPocketPivot": 0}),
    )

    result = await runner.run(["20260501"])

    assert result.execution_reports == []
    assert result.journal_records[0]["status"] == "REJECTED"
    assert result.journal_records[0]["rejected_reason"] == "max_positions"


@pytest.mark.asyncio
async def test_period_runner_sets_backtest_date_on_bar_provider():
    strategy = FakeStrategy()
    provider = DatedStaticBarProvider({
        ("20260501", "005930", "BUY"): BacktestBar("20260501 091000", 70_000, 70_500, 69_500, 70_200, 1_000),
    })
    runner = BacktestPeriodRunner(
        strategy=strategy,
        bar_provider=provider,
        ledger=BacktestPortfolioLedger(initial_cash=1_000_000),
    )

    await runner.run(["20260501"])

    assert strategy.scan_calls == ["20260501"]
    assert provider.dates == ["20260501"]


@pytest.mark.asyncio
async def test_period_runner_persists_standard_journal_run_when_repository_is_injected():
    strategy = FakeStrategy()
    provider = StaticBarProvider({
        ("20260501", "005930", "BUY"): BacktestBar("20260501 091000", 70_000, 70_500, 69_500, 70_200, 1_000),
        ("20260502", "005930", "SELL"): BacktestBar("20260502 100000", 77_000, 77_500, 76_500, 77_100, 1_000),
    })
    repo = FakeBacktestJournalRepository()
    runner = BacktestPeriodRunner(
        strategy=strategy,
        bar_provider=provider,
        ledger=BacktestPortfolioLedger(initial_cash=1_000_000),
        backtest_journal_repository=repo,
        run_id="period_pp_20260501_20260502",
    )

    result = await runner.run(["20260501", "20260502"])

    assert result.saved_journal_run["run_id"] == "period_pp_20260501_20260502"
    assert len(repo.saved_runs) == 1
    saved = repo.saved_runs[0]
    assert saved["strategy"] == "OneilPocketPivot"
    assert saved["target_date"] == "20260501_20260502"
    assert saved["metadata"]["date_count"] == 2
    assert saved["metadata"]["initial_cash"] == 1_000_000
    assert [record["side"] for record in saved["records"]] == ["BUY", "SELL"]
    assert all(record["source"] == "backtest" for record in saved["records"])
    assert result.journal_records == saved["records"]
