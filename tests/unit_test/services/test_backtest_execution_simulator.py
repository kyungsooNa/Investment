import pytest

from services.backtest_execution_simulator import (
    BacktestBar,
    BacktestExecutionPolicy,
    BacktestExecutionSimulator,
    BacktestOrder,
    BacktestPortfolioLedger,
    OrderSide,
    OrderStatus,
    OrderType,
)
from utils.transaction_cost_utils import TransactionCostUtils


def test_limit_buy_partial_fill_uses_high_low_reach_and_volume_cap():
    simulator = BacktestExecutionSimulator(
        BacktestExecutionPolicy(volume_participation_pct=50.0)
    )
    order = BacktestOrder(
        order_id="o1",
        code="005930",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        price=10_000,
        qty=100,
        strategy="S1",
    )
    bar = BacktestBar(
        timestamp="20260509 091000",
        open=10_100,
        high=10_200,
        low=9_900,
        close=10_050,
        volume=50,
    )

    report = simulator.simulate(order, bar)

    assert report.status == OrderStatus.PARTIAL
    assert report.filled_qty == 25
    assert report.remaining_qty == 75
    assert report.fill_price == 10_000
    assert report.cost == pytest.approx(
        TransactionCostUtils.calculate_cost(10_000, 25, is_sell=False)
    )


def test_limit_order_not_filled_when_price_is_not_reached():
    simulator = BacktestExecutionSimulator()
    order = BacktestOrder(
        order_id="o1",
        code="005930",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        price=10_000,
        qty=10,
        strategy="S1",
    )
    bar = BacktestBar(
        timestamp="20260509 091000",
        open=10_200,
        high=10_300,
        low=10_100,
        close=10_150,
        volume=100,
    )

    report = simulator.simulate(order, bar)

    assert report.status == OrderStatus.UNFILLED
    assert report.filled_qty == 0
    assert report.fill_price is None
    assert report.reason == "limit_not_reached"


def test_market_sell_applies_slippage_and_rounds_to_tick():
    simulator = BacktestExecutionSimulator(
        BacktestExecutionPolicy(market_slippage_pct=0.2)
    )
    order = BacktestOrder(
        order_id="o1",
        code="005930",
        side=OrderSide.SELL,
        order_type=OrderType.MARKET,
        price=0,
        qty=10,
        strategy="S1",
    )
    bar = BacktestBar(
        timestamp="20260509 091000",
        open=10_003,
        high=10_100,
        low=9_900,
        close=10_000,
        volume=1_000,
    )

    report = simulator.simulate(order, bar)

    assert report.status == OrderStatus.FILLED
    assert report.fill_price == 9_980
    assert report.slippage_amount_won == -23
    assert report.cost == pytest.approx(
        TransactionCostUtils.calculate_cost(9_980, 10, is_sell=True)
    )


def test_portfolio_ledger_reserves_cash_and_rejects_cash_short_orders():
    ledger = BacktestPortfolioLedger(initial_cash=1_000_000)
    first = BacktestOrder(
        order_id="o1",
        code="005930",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        price=600_000,
        qty=1,
        strategy="S1",
        priority=10,
    )
    second = BacktestOrder(
        order_id="o2",
        code="000660",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        price=500_000,
        qty=1,
        strategy="S1",
        priority=1,
    )

    decisions = ledger.reserve_buy_orders([second, first])

    assert [d.order.order_id for d in decisions] == ["o1", "o2"]
    assert decisions[0].accepted is True
    assert decisions[1].accepted is False
    assert decisions[1].reason == "cash_short"
    assert ledger.reserved_cash == pytest.approx(
        600_000 + TransactionCostUtils.calculate_cost(600_000, 1, is_sell=False)
    )


def test_portfolio_ledger_applies_buy_and_sell_fills_with_net_cash():
    ledger = BacktestPortfolioLedger(initial_cash=1_000_000)
    buy = BacktestOrder(
        order_id="buy1",
        code="005930",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        price=10_000,
        qty=10,
        strategy="S1",
    )
    sell = BacktestOrder(
        order_id="sell1",
        code="005930",
        side=OrderSide.SELL,
        order_type=OrderType.LIMIT,
        price=11_000,
        qty=10,
        strategy="S1",
    )
    simulator = BacktestExecutionSimulator()
    buy_report = simulator.simulate(
        buy,
        BacktestBar("20260509 091000", 10_000, 10_100, 9_900, 10_050, 1_000),
    )
    sell_report = simulator.simulate(
        sell,
        BacktestBar("20260509 100000", 11_000, 11_100, 10_900, 11_050, 1_000),
    )

    ledger.apply_execution(buy_report)
    ledger.apply_execution(sell_report)

    expected_cash = (
        1_000_000
        - 10_000 * 10
        - TransactionCostUtils.calculate_cost(10_000, 10, is_sell=False)
        + 11_000 * 10
        - TransactionCostUtils.calculate_cost(11_000, 10, is_sell=True)
    )
    assert ledger.cash == pytest.approx(expected_cash)
    assert ledger.positions == {}
    assert ledger.realized_net_pnl == pytest.approx(expected_cash - 1_000_000)
