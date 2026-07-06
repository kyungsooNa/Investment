from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from common.types import TradeSignal
from core.market_clock import MarketClock
from services.newhigh_strategy_coverage_backtest_service import (
    NewHighStrategyCoverageBacktestService,
)


def _fake_strategy_factory(**kwargs):
    strategy_key = kwargs["strategy_key"]
    universe = MagicMock()
    universe.get_watchlist = AsyncMock(
        return_value={"005930": object(), "000660": object(), "035720": object()}
    )

    class FakeStrategy:
        name = strategy_key
        _universe = universe

        async def scan(self):
            watchlist = await self._universe.get_watchlist()
            if strategy_key != "buy_strategy" or "005930" not in watchlist:
                return []
            return [
                TradeSignal(
                    code="005930",
                    name="삼성전자",
                    action="BUY",
                    price=70_000,
                    qty=1,
                    reason="replay_signal",
                    strategy_name=strategy_key,
                )
            ]

    return FakeStrategy()


def _make_service(*, stock_repository=None, repo=None, strategy_factory=_fake_strategy_factory, env=None):
    if stock_repository is None:
        stock_repository = MagicMock()
        stock_repository.get_newhigh_stocks = AsyncMock(
            return_value=[
                {"code": "005930", "name": "삼성전자"},
                {"code": "000660", "name": "SK하이닉스"},
                {"code": "035720", "name": "카카오"},
            ]
        )
    repo = repo or MagicMock()
    repo.save_run.return_value = {"run_id": "saved"}
    return NewHighStrategyCoverageBacktestService(
        stock_repository=stock_repository,
        stock_query_service=AsyncMock(),
        universe_service=MagicMock(),
        indicator_service=MagicMock(),
        market_clock=MarketClock(),
        backtest_journal_repository=repo,
        strategy_factory=strategy_factory,
        env=env or SimpleNamespace(is_paper_trading=False),
        logger=MagicMock(),
    ), stock_repository, repo


@pytest.mark.asyncio
async def test_run_calculates_not_bought_rates_and_saves_journals():
    service, stock_repository, repo = _make_service()

    result = await service.run("20260505", strategy_keys=["buy_strategy", "miss_strategy"])

    assert result.skipped is False
    assert result.newhigh_count == 3
    assert result.strategy_count == 2
    assert result.all_strategy_missed_count == 2
    assert result.all_strategy_missed_rate == pytest.approx(2 / 3)
    stock_repository.get_newhigh_stocks.assert_awaited_once_with("20260505")

    by_strategy = {row.strategy_key: row for row in result.strategy_summaries}
    assert by_strategy["buy_strategy"].bought_count == 1
    assert by_strategy["buy_strategy"].not_bought_count == 2
    assert by_strategy["buy_strategy"].not_bought_rate == pytest.approx(2 / 3)
    assert by_strategy["buy_strategy"].no_signal_count == 2
    assert by_strategy["miss_strategy"].bought_count == 0
    assert by_strategy["miss_strategy"].not_bought_count == 3

    assert repo.save_run.call_count == 2
    first_records = repo.save_run.call_args_list[0].args[0]
    by_code = {record["code"]: record for record in first_records}
    assert by_code["005930"]["metadata"]["newhigh_coverage_status"] == "bought"
    assert by_code["000660"]["rejected_reason"] == "no_signal"
    assert by_code["000660"]["metadata"]["newhigh_coverage_status"] == "no_signal"

    first_kwargs = repo.save_run.call_args_list[0].kwargs
    assert first_kwargs["run_id"] == "newhigh_coverage_buy_strategy_20260505"
    assert first_kwargs["metadata"]["audit_type"] == "newhigh_strategy_coverage"
    assert first_kwargs["metadata"]["not_bought_rate"] == pytest.approx(2 / 3)


@pytest.mark.asyncio
async def test_run_skips_when_no_newhigh_stocks():
    stock_repository = MagicMock()
    stock_repository.get_newhigh_stocks = AsyncMock(return_value=[])
    service, _, repo = _make_service(stock_repository=stock_repository)

    result = await service.run("20260505", strategy_keys=["buy_strategy"])

    assert result.skipped is True
    assert result.skip_reason == "no_newhigh_stocks"
    repo.save_run.assert_not_called()


@pytest.mark.asyncio
async def test_run_marks_data_unavailable_when_strategy_fails():
    def raising_factory(**_kwargs):
        raise RuntimeError("replay data missing")

    service, _, repo = _make_service(strategy_factory=raising_factory)

    result = await service.run("20260505", strategy_keys=["broken_strategy"])

    summary = result.strategy_summaries[0]
    assert summary.data_unavailable_count == 3
    assert summary.not_bought_count == 0
    records = repo.save_run.call_args.args[0]
    assert {record["metadata"]["newhigh_coverage_status"] for record in records} == {
        "data_unavailable"
    }


@pytest.mark.asyncio
async def test_run_skips_paper_mode():
    service, _, repo = _make_service(env=SimpleNamespace(is_paper_trading=True))

    result = await service.run("20260505", strategy_keys=["buy_strategy"])

    assert result.skipped is True
    assert result.skip_reason == "historical_intraday_unavailable_in_paper"
    repo.save_run.assert_not_called()
