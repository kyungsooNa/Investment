import pytest

from common.trade_journal_schema import (
    STANDARD_TRADE_JOURNAL_FIELDS,
    normalize_backtest_decision,
    normalize_backtest_execution,
    normalize_backtest_trade,
    normalize_virtual_trade,
)
from services.backtest_execution_simulator import (
    BacktestExecutionReport,
    BacktestOrder,
    OrderSide,
    OrderStatus,
    OrderType,
)


def test_normalize_virtual_trade_outputs_standard_schema_with_net_values():
    trade = {
        "strategy": "S1",
        "code": "005930",
        "buy_date": "2026-05-05 09:10:00",
        "buy_price": 10000,
        "qty": 10,
        "sell_date": "2026-05-05 10:00:00",
        "sell_price": 11000,
        "return_rate": 10.0,
        "status": "SOLD",
        "reason": "target_hit",
        "mfe_pct": 12.5,
        "mae_pct": -1.2,
    }

    normalized = normalize_virtual_trade(trade)

    assert tuple(normalized.keys()) == STANDARD_TRADE_JOURNAL_FIELDS
    assert normalized["schema_version"] == 1
    assert normalized["source"] == "virtual_trade"
    assert normalized["strategy"] == "S1"
    assert normalized["code"] == "005930"
    assert normalized["signal_time"] == "2026-05-05 09:10:00"
    assert normalized["decision_reason"] == "target_hit"
    assert normalized["rejected_reason"] == ""
    assert normalized["order_price"] == 10000.0
    assert normalized["fill_price"] == 11000.0
    assert normalized["qty"] == 10
    assert normalized["gross_pnl"] == 10000.0
    assert normalized["net_pnl"] < normalized["gross_pnl"]
    assert normalized["net_return"] < normalized["gross_return"]
    assert normalized["mfe"] == 12.5
    assert normalized["mae"] == -1.2


def test_normalize_failed_virtual_trade_uses_rejected_reason():
    trade = {
        "strategy": "S1",
        "code": "005930",
        "buy_date": "2026-05-05 09:10:00",
        "buy_price": 10000,
        "qty": 3,
        "sell_date": None,
        "sell_price": None,
        "return_rate": 0.0,
        "status": "FAILED",
        "reason": "risk_gate_blocked",
    }

    normalized = normalize_virtual_trade(trade)

    assert normalized["status"] == "FAILED"
    assert normalized["decision_reason"] == ""
    assert normalized["rejected_reason"] == "risk_gate_blocked"
    assert normalized["fill_price"] is None
    assert normalized["gross_pnl"] is None
    assert normalized["net_pnl"] is None


def test_normalize_backtest_trade_matches_standard_schema():
    trade = {
        "entry_time": "20260505 091000",
        "entry_px": 10000,
        "exit_time": "20260505 100000",
        "exit_px": 9500,
        "outcome": "stop_loss",
        "ret_pct": -5.0,
        "mfe_pct": 1.5,
        "mae_pct": -6.0,
    }

    normalized = normalize_backtest_trade(
        trade,
        stock_code="005930",
        strategy="VolumeBreakout",
        qty=2,
    )

    assert tuple(normalized.keys()) == STANDARD_TRADE_JOURNAL_FIELDS
    assert normalized["source"] == "backtest"
    assert normalized["strategy"] == "VolumeBreakout"
    assert normalized["code"] == "005930"
    assert normalized["signal_time"] == "20260505 091000"
    assert normalized["decision_reason"] == "stop_loss"
    assert normalized["order_price"] == 10000.0
    assert normalized["fill_price"] == 9500.0
    assert normalized["qty"] == 2
    assert normalized["gross_pnl"] == -1000.0
    assert normalized["net_pnl"] < normalized["gross_pnl"]
    assert normalized["mfe"] == 1.5
    assert normalized["mae"] == -6.0


def test_normalize_backtest_trade_requires_prices():
    with pytest.raises(ValueError):
        normalize_backtest_trade({"entry_px": 10000}, stock_code="005930")


def test_normalize_backtest_decision_records_signal_without_round_trip_pnl():
    normalized = normalize_backtest_decision(
        {
            "signal_time": "2026-05-05 09:10:00",
            "current": 330000,
            "after": 350000,
            "decision_reason": "follow_through",
        },
        stock_code="035720",
        strategy="Momentum",
        accepted=True,
    )

    assert tuple(normalized.keys()) == STANDARD_TRADE_JOURNAL_FIELDS
    assert normalized["source"] == "backtest"
    assert normalized["strategy"] == "Momentum"
    assert normalized["code"] == "035720"
    assert normalized["status"] == "SIGNAL"
    assert normalized["side"] == "BUY"
    assert normalized["order_price"] == 330000.0
    assert normalized["fill_price"] is None
    assert normalized["decision_reason"] == "follow_through"
    assert normalized["rejected_reason"] == ""
    assert normalized["net_pnl"] is None
    assert normalized["net_return"] is None


def test_normalize_backtest_decision_records_rejected_reason():
    normalized = normalize_backtest_decision(
        {
            "signal_time": "2026-05-05 09:10:00",
            "current": 11000,
            "rejected_reason": "추세 지속 실패",
        },
        stock_code="000660",
        strategy="Momentum",
        accepted=False,
    )

    assert normalized["status"] == "REJECTED"
    assert normalized["side"] == "REJECTED"
    assert normalized["decision_reason"] == ""
    assert normalized["rejected_reason"] == "추세 지속 실패"
    assert normalized["cost"] == 0.0


def test_normalize_backtest_execution_records_fill_report():
    report = BacktestExecutionReport(
        order=BacktestOrder(
            order_id="order-1",
            code="005930",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            price=70000,
            qty=2,
            strategy="OneilPocketPivot",
            decision_reason="pocket_pivot",
        ),
        status=OrderStatus.FILLED,
        filled_qty=2,
        remaining_qty=0,
        order_price=70000,
        fill_price=70100,
        cost=28.04,
        gross_amount=140200,
        slippage_amount_won=100,
        slippage_pct=0.1429,
        reason="filled",
        filled_at="20260501 091000",
        mfe=1.25,
        mae=-0.75,
    )

    normalized = normalize_backtest_execution(report)

    assert tuple(normalized.keys()) == STANDARD_TRADE_JOURNAL_FIELDS
    assert normalized["source"] == "backtest"
    assert normalized["strategy"] == "OneilPocketPivot"
    assert normalized["code"] == "005930"
    assert normalized["signal_time"] == "20260501 091000"
    assert normalized["decision_reason"] == "pocket_pivot"
    assert normalized["rejected_reason"] == ""
    assert normalized["side"] == "BUY"
    assert normalized["order_price"] == 70000.0
    assert normalized["fill_price"] == 70100.0
    assert normalized["qty"] == 2
    assert normalized["status"] == "FILLED"
    assert normalized["cost"] == 28.04
    assert normalized["mfe"] == 1.25
    assert normalized["mae"] == -0.75
    assert normalized["metadata"]["order_id"] == "order-1"
    assert normalized["metadata"]["gross_amount"] == 140200
    assert normalized["metadata"]["execution_reason"] == "filled"


def test_normalize_backtest_execution_records_unfilled_reason():
    report = BacktestExecutionReport(
        order=BacktestOrder(
            order_id="order-2",
            code="000660",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            price=120000,
            qty=1,
            strategy="OneilPocketPivot",
        ),
        status=OrderStatus.UNFILLED,
        filled_qty=0,
        remaining_qty=1,
        order_price=120000,
        fill_price=None,
        cost=0.0,
        gross_amount=0.0,
        slippage_amount_won=None,
        slippage_pct=None,
        reason="limit_not_reached",
        filled_at="20260501 091000",
    )

    normalized = normalize_backtest_execution(report)

    assert normalized["status"] == "UNFILLED"
    assert normalized["side"] == "BUY"
    assert normalized["qty"] == 1
    assert normalized["decision_reason"] == ""
    assert normalized["rejected_reason"] == "limit_not_reached"
    assert normalized["fill_price"] is None
