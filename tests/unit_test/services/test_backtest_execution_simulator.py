import math

from services.backtest_execution_simulator import (
    BacktestExecutionSimulator,
    BacktestOrder,
    BacktestBar,
    BacktestExecutionPolicy,
    OrderSide,
    OrderType,
    OrderStatus,
)


def make_order(code="X", qty=10, price=100.0, side=OrderSide.BUY, order_type=OrderType.LIMIT):
    return BacktestOrder(order_id="o1", code=code, side=side, order_type=order_type, price=price, qty=qty)


def test_invalid_qty_rejected():
    sim = BacktestExecutionSimulator()
    order = make_order(qty=0)
    bar = BacktestBar(timestamp="2022-01-01 090000", open=100, high=110, low=90, close=105, volume=100)
    rep = sim.simulate(order, bar)
    assert rep.status == OrderStatus.REJECTED
    assert rep.filled_qty == 0


def test_limit_buy_not_reached_unfilled():
    sim = BacktestExecutionSimulator()
    order = make_order(price=50.0, qty=10, side=OrderSide.BUY, order_type=OrderType.LIMIT)
    bar = BacktestBar(timestamp="t", open=100, high=110, low=90, close=105, volume=100)
    rep = sim.simulate(order, bar)
    assert rep.status == OrderStatus.UNFILLED


def test_market_order_fills_at_open_and_rounds_tick():
    policy = BacktestExecutionPolicy(market_slippage_pct=0.0, market_price_field="open", round_to_tick=True)
    sim = BacktestExecutionSimulator(policy=policy)
    order = make_order(price=0.0, qty=5, side=OrderSide.BUY, order_type=OrderType.MARKET)
    bar = BacktestBar(timestamp="t", open=123.4, high=125, low=120, close=124, volume=None)
    rep = sim.simulate(order, bar)
    assert rep.filled_qty == 5
    assert rep.status == OrderStatus.FILLED
    # fill_price should be rounded up for BUY to tick size
    tick = sim.tick_size(rep.fill_price or bar.open)
    # ensure fill_price is a multiple of tick
    assert (rep.fill_price / tick).is_integer()


def test_partial_fill_when_volume_limited_and_slippage():
    policy = BacktestExecutionPolicy(market_slippage_pct=1.0, volume_participation_pct=50.0, round_to_tick=False)
    sim = BacktestExecutionSimulator(policy=policy)
    order = make_order(price=105.0, qty=100, side=OrderSide.SELL, order_type=OrderType.LIMIT)
    # bar has limited volume -> participation 50% -> max_qty = floor(200 * 0.5) = 100
    bar = BacktestBar(timestamp="t2", open=105, high=200, low=100, close=150, volume=200)
    rep = sim.simulate(order, bar)
    # base price for SELL limit: limit_price if bar.high >= limit_price -> filled possible
    assert rep.filled_qty <= order.qty
    assert rep.status in (OrderStatus.FILLED, OrderStatus.PARTIAL)
    # slippage applied for MARKET orders only; for limit, market_slippage_pct shouldn't change base price
    # but fill_price should be not None when filled
    if rep.filled_qty > 0:
        assert rep.fill_price is not None


def test_tick_size_boundaries_and_rounding():
    sim = BacktestExecutionSimulator()
    # check tick sizes boundaries
    assert sim.tick_size(1000) == 1
    assert sim.tick_size(3000) == 5
    assert sim.tick_size(10000) == 10
    assert sim.tick_size(30000) == 50
    assert sim.tick_size(100000) == 100
    assert sim.tick_size(300000) == 500
    assert sim.tick_size(1_000_000) == 1000

    # rounding behavior
    price = 12345.0
    rounded_buy = sim.round_to_tick(price, side=OrderSide.BUY)
    rounded_sell = sim.round_to_tick(price, side=OrderSide.SELL)
    assert rounded_buy >= price
    assert rounded_sell <= price
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


def test_portfolio_ledger_reports_same_code_batch_overlap_without_blocking():
    ledger = BacktestPortfolioLedger(initial_cash=1_000_000)
    first = BacktestOrder(
        order_id="o1",
        code="005930",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        price=70_000,
        qty=1,
        strategy="S1",
    )
    second = BacktestOrder(
        order_id="o2",
        code="005930",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        price=71_000,
        qty=1,
        strategy="S2",
    )

    decisions = ledger.reserve_buy_orders([first, second])

    assert [decision.accepted for decision in decisions] == [True, True]
    assert decisions[0].warnings == ("same_code_batch_signal",)
    assert decisions[1].warnings == ("same_code_batch_signal",)


def test_portfolio_ledger_reports_same_code_existing_position_overlap():
    ledger = BacktestPortfolioLedger(initial_cash=1_000_000)
    existing = BacktestOrder(
        order_id="buy1",
        code="005930",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        price=70_000,
        qty=1,
        strategy="S1",
    )
    report = BacktestExecutionSimulator().simulate(
        existing,
        BacktestBar("20260509 091000", 70_000, 70_500, 69_500, 70_200, 1_000),
    )
    ledger.apply_execution(report)

    add_order = BacktestOrder(
        order_id="o2",
        code="005930",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        price=71_000,
        qty=1,
        strategy="S2",
    )

    decisions = ledger.reserve_buy_orders([add_order])

    assert decisions[0].accepted is True
    assert decisions[0].warnings == ("same_code_existing_position",)


def test_portfolio_ledger_reports_same_code_pending_order_overlap():
    ledger = BacktestPortfolioLedger(initial_cash=1_000_000)
    first = BacktestOrder(
        order_id="o1",
        code="005930",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        price=70_000,
        qty=1,
        strategy="S1",
    )
    ledger.reserve_buy_orders([first])

    second = BacktestOrder(
        order_id="o2",
        code="005930",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        price=71_000,
        qty=1,
        strategy="S2",
    )

    decisions = ledger.reserve_buy_orders([second])

    assert decisions[0].accepted is True
    assert decisions[0].warnings == ("same_code_pending_order",)


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


def test_opening_market_slippage_bonus_buy_adds_to_base_slippage():
    policy = BacktestExecutionPolicy(
        market_slippage_pct=0.5,
        opening_market_slippage_bonus_pct=0.5,
        market_price_field="open",
        round_to_tick=False,
    )
    sim = BacktestExecutionSimulator(policy=policy)
    order = make_order(price=0.0, qty=10, side=OrderSide.BUY, order_type=OrderType.MARKET)
    bar = BacktestBar(timestamp="t", open=10_000, high=10_500, low=9_800, close=10_200, volume=None)
    rep = sim.simulate(order, bar)
    assert rep.fill_price == pytest.approx(10_100.0)
    assert rep.slippage_pct == pytest.approx(1.0)


def test_opening_market_slippage_bonus_sell_subtracts_from_base():
    policy = BacktestExecutionPolicy(
        market_slippage_pct=0.5,
        opening_market_slippage_bonus_pct=0.5,
        market_price_field="open",
        round_to_tick=False,
    )
    sim = BacktestExecutionSimulator(policy=policy)
    order = make_order(price=0.0, qty=10, side=OrderSide.SELL, order_type=OrderType.MARKET)
    bar = BacktestBar(timestamp="t", open=10_000, high=10_500, low=9_800, close=10_200, volume=None)
    rep = sim.simulate(order, bar)
    assert rep.fill_price == pytest.approx(9_900.0)
    assert rep.slippage_pct == pytest.approx(-1.0)


def test_opening_market_slippage_bonus_skipped_for_limit_order():
    policy = BacktestExecutionPolicy(
        market_slippage_pct=0.5,
        opening_market_slippage_bonus_pct=0.5,
        market_price_field="open",
        round_to_tick=False,
    )
    sim = BacktestExecutionSimulator(policy=policy)
    order = make_order(price=10_000.0, qty=10, side=OrderSide.BUY, order_type=OrderType.LIMIT)
    bar = BacktestBar(timestamp="t", open=10_000, high=10_500, low=9_500, close=10_200, volume=None)
    rep = sim.simulate(order, bar)
    assert rep.fill_price == pytest.approx(10_000.0)


def test_opening_market_slippage_bonus_skipped_when_market_price_field_is_close():
    policy = BacktestExecutionPolicy(
        market_slippage_pct=0.5,
        opening_market_slippage_bonus_pct=0.5,
        market_price_field="close",
        round_to_tick=False,
    )
    sim = BacktestExecutionSimulator(policy=policy)
    order = make_order(price=0.0, qty=10, side=OrderSide.BUY, order_type=OrderType.MARKET)
    bar = BacktestBar(timestamp="t", open=10_000, high=10_500, low=9_800, close=12_000, volume=None)
    rep = sim.simulate(order, bar)
    assert rep.fill_price == pytest.approx(12_060.0)


def test_liquidity_slippage_bucket_buy_low_trading_value_adds_bonus():
    policy = BacktestExecutionPolicy(
        market_slippage_pct=0.0,
        liquidity_slippage_buckets=((1_000_000_000, 0.5), (5_000_000_000, 0.2)),
        round_to_tick=False,
    )
    sim = BacktestExecutionSimulator(policy=policy)
    order = make_order(price=0.0, qty=10, side=OrderSide.BUY, order_type=OrderType.MARKET)
    bar = BacktestBar(
        timestamp="t", open=10_000, high=10_500, low=9_800, close=10_200,
        volume=None, trading_value=500_000_000,
    )
    rep = sim.simulate(order, bar)
    # trading_value 5e8 < 1e9 and < 5e9 → max(0.5, 0.2) = 0.5%
    assert rep.fill_price == pytest.approx(10_050.0)


def test_liquidity_slippage_bucket_no_bonus_above_max_threshold():
    policy = BacktestExecutionPolicy(
        market_slippage_pct=0.0,
        liquidity_slippage_buckets=((1_000_000_000, 0.5), (5_000_000_000, 0.2)),
        round_to_tick=False,
    )
    sim = BacktestExecutionSimulator(policy=policy)
    order = make_order(price=0.0, qty=10, side=OrderSide.SELL, order_type=OrderType.MARKET)
    bar = BacktestBar(
        timestamp="t", open=10_000, high=10_500, low=9_800, close=10_200,
        volume=None, trading_value=10_000_000_000,
    )
    rep = sim.simulate(order, bar)
    # trading_value 1e10 >= all thresholds → bonus 0
    assert rep.fill_price == pytest.approx(10_000.0)


def test_liquidity_slippage_bucket_skipped_for_limit_order():
    policy = BacktestExecutionPolicy(
        market_slippage_pct=0.0,
        liquidity_slippage_buckets=((1_000_000_000, 1.0),),
        round_to_tick=False,
    )
    sim = BacktestExecutionSimulator(policy=policy)
    order = make_order(price=10_000.0, qty=10, side=OrderSide.BUY, order_type=OrderType.LIMIT)
    bar = BacktestBar(
        timestamp="t", open=10_000, high=10_500, low=9_500, close=10_200,
        volume=None, trading_value=100_000_000,
    )
    rep = sim.simulate(order, bar)
    assert rep.fill_price == pytest.approx(10_000.0)


def test_liquidity_slippage_falls_back_to_volume_times_close_when_trading_value_missing():
    policy = BacktestExecutionPolicy(
        market_slippage_pct=0.0,
        liquidity_slippage_buckets=((1_000_000_000, 0.5),),
        round_to_tick=False,
    )
    sim = BacktestExecutionSimulator(policy=policy)
    order = make_order(price=0.0, qty=10, side=OrderSide.BUY, order_type=OrderType.MARKET)
    # volume * close = 50_000 * 10_000 = 5e8 < 1e9 → 0.5% bonus
    bar = BacktestBar(
        timestamp="t", open=10_000, high=10_500, low=9_800, close=10_000,
        volume=50_000, trading_value=None,
    )
    rep = sim.simulate(order, bar)
    assert rep.fill_price == pytest.approx(10_050.0)


def test_liquidity_slippage_combines_with_base_and_opening_bonus():
    policy = BacktestExecutionPolicy(
        market_slippage_pct=0.3,
        opening_market_slippage_bonus_pct=0.2,
        liquidity_slippage_buckets=((1_000_000_000, 0.5),),
        market_price_field="open",
        round_to_tick=False,
    )
    sim = BacktestExecutionSimulator(policy=policy)
    order = make_order(price=0.0, qty=10, side=OrderSide.BUY, order_type=OrderType.MARKET)
    bar = BacktestBar(
        timestamp="t", open=10_000, high=10_500, low=9_800, close=10_200,
        volume=None, trading_value=500_000_000,
    )
    rep = sim.simulate(order, bar)
    # base 0.3 + opening 0.2 + liquidity 0.5 = 1.0% → 10_000 * 1.01 = 10_100
    assert rep.fill_price == pytest.approx(10_100.0)


def test_liquidity_slippage_zero_when_bar_has_no_volume_and_no_trading_value():
    policy = BacktestExecutionPolicy(
        market_slippage_pct=0.0,
        liquidity_slippage_buckets=((1_000_000_000, 0.5),),
        round_to_tick=False,
    )
    sim = BacktestExecutionSimulator(policy=policy)
    order = make_order(price=0.0, qty=10, side=OrderSide.BUY, order_type=OrderType.MARKET)
    bar = BacktestBar(
        timestamp="t", open=10_000, high=10_500, low=9_800, close=10_200,
        volume=None, trading_value=None,
    )
    rep = sim.simulate(order, bar)
    assert rep.fill_price == pytest.approx(10_000.0)
