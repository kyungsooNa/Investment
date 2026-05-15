"""Standard trade journal schema for live, paper, and backtest comparison."""
from __future__ import annotations

import math
from typing import Any, Mapping

from utils.transaction_cost_utils import TransactionCostUtils


SCHEMA_VERSION = 3

STANDARD_TRADE_JOURNAL_FIELDS = (
    "schema_version",
    "source",
    "strategy",
    "code",
    "signal_time",
    "decision_reason",
    "rejected_reason",
    "side",
    "order_price",
    "fill_price",
    "qty",
    "status",
    "cost",
    "gross_pnl",
    "net_pnl",
    "gross_return",
    "net_return",
    "mfe",
    "mae",
    "market_regime",
    "volatility_20d_annualized",
    "metadata",
)


def _resolve_market_regime(
    record: Mapping[str, Any],
    explicit: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """우선순위: 명시 인자 > record.market_regime > record.metadata.market_regime > None.

    Normalizer 는 service 를 호출하지 않는다 — 호출 측이 regime snapshot 을 책임진다.
    """
    if explicit is not None:
        return dict(explicit)
    direct = record.get("market_regime") if isinstance(record, Mapping) else None
    if isinstance(direct, Mapping):
        return dict(direct)
    metadata = record.get("metadata") if isinstance(record, Mapping) else None
    if isinstance(metadata, Mapping):
        meta_regime = metadata.get("market_regime")
        if isinstance(meta_regime, Mapping):
            return dict(meta_regime)
    return None


def _resolve_volatility(
    record: Mapping[str, Any],
    explicit: float | None,
) -> float | None:
    """우선순위: 명시 인자 > record.volatility_20d_annualized > record.metadata.volatility_20d_annualized > None.

    Normalizer 는 OHLCV 계산을 수행하지 않는다 — 호출 측이 사전 계산된 값을 책임진다.
    """
    if explicit is not None:
        return _to_float(explicit)
    if isinstance(record, Mapping):
        direct = record.get("volatility_20d_annualized")
        if direct is not None:
            return _to_float(direct)
        metadata = record.get("metadata")
        if isinstance(metadata, Mapping):
            return _to_float(metadata.get("volatility_20d_annualized"))
    return None


def normalize_virtual_trade(
    trade: Mapping[str, Any],
    *,
    source: str = "virtual_trade",
    market_regime: Mapping[str, Any] | None = None,
    volatility_20d_annualized: float | None = None,
) -> dict[str, Any]:
    """Normalize a VirtualTradeRepository row into the shared journal schema.

    Existing repository rows represent one position lifecycle. The standard
    schema keeps that round-trip shape so it can be compared with backtest
    trades without changing the current persistence schema.
    """
    status = str(trade.get("status") or "").upper()
    qty = _to_int(trade.get("qty"), default=1)
    order_price = _to_float(trade.get("buy_price"))
    sell_price = _to_float(trade.get("sell_price"))
    reason = str(trade.get("reason") or "")

    is_failed = status == "FAILED"
    is_sold = status == "SOLD" and sell_price is not None
    fill_price = sell_price if is_sold else (order_price if status == "HOLD" else None)
    side = "ROUND_TRIP" if is_sold else ("REJECTED" if is_failed else "BUY")

    gross_pnl = _gross_pnl(order_price, sell_price, qty) if is_sold else None
    cost = _trade_cost(order_price, sell_price, qty, include_sell=is_sold, include_buy=not is_failed)
    net_pnl = _net_pnl(order_price, sell_price, qty) if is_sold else None
    gross_return = _to_float(trade.get("return_rate"))
    if gross_return is None and is_sold and order_price:
        gross_return = (sell_price - order_price) / order_price * 100
    net_return = (
        TransactionCostUtils.get_return_rate(order_price, sell_price, qty, apply_cost=True)
        if is_sold and order_price
        else None
    )

    return _ordered_record(
        source=source,
        strategy=str(trade.get("strategy") or ""),
        code=str(trade.get("code") or ""),
        signal_time=str(trade.get("buy_date") or ""),
        decision_reason="" if is_failed else reason,
        rejected_reason=reason if is_failed else "",
        side=side,
        order_price=order_price,
        fill_price=fill_price,
        qty=qty,
        status=status,
        cost=cost,
        gross_pnl=gross_pnl,
        net_pnl=net_pnl,
        gross_return=gross_return,
        net_return=net_return,
        mfe=_first_float(trade, "mfe", "MFE", "mfe_pct", "MFE_pct"),
        mae=_first_float(trade, "mae", "MAE", "mae_pct", "MAE_pct"),
        market_regime=_resolve_market_regime(trade, market_regime),
        volatility_20d_annualized=_resolve_volatility(trade, volatility_20d_annualized),
        metadata={k: _json_safe(v) for k, v in dict(trade).items()},
    )


def normalize_backtest_trade(
    trade: Mapping[str, Any],
    *,
    stock_code: str,
    strategy: str = "",
    qty: int = 1,
    source: str = "backtest",
    market_regime: Mapping[str, Any] | None = None,
    volatility_20d_annualized: float | None = None,
) -> dict[str, Any]:
    """Normalize a backtest trade dict into the shared journal schema."""
    order_price = _to_float(_first_value(trade, "entry_px", "entry_price", "order_price"))
    fill_price = _to_float(_first_value(trade, "exit_px", "exit_price", "fill_price"))
    if order_price is None or fill_price is None:
        raise ValueError("backtest trade requires entry and exit prices")

    normalized_qty = _to_int(qty, default=1)
    gross_return = _to_float(_first_value(trade, "ret_pct", "return_rate", "gross_return"))
    if gross_return is None and order_price:
        gross_return = (fill_price - order_price) / order_price * 100

    return _ordered_record(
        source=source,
        strategy=str(strategy or trade.get("strategy") or ""),
        code=str(stock_code or trade.get("code") or trade.get("stock_code") or ""),
        signal_time=str(_first_value(trade, "entry_time", "signal_time", default="")),
        decision_reason=str(_first_value(trade, "outcome", "decision_reason", default="")),
        rejected_reason=str(_first_value(trade, "rejected_reason", default="")),
        side="ROUND_TRIP",
        order_price=order_price,
        fill_price=fill_price,
        qty=normalized_qty,
        status=str(_first_value(trade, "status", default="SOLD")).upper(),
        cost=_trade_cost(order_price, fill_price, normalized_qty, include_sell=True, include_buy=True),
        gross_pnl=_gross_pnl(order_price, fill_price, normalized_qty),
        net_pnl=_net_pnl(order_price, fill_price, normalized_qty),
        gross_return=gross_return,
        net_return=TransactionCostUtils.get_return_rate(order_price, fill_price, normalized_qty, apply_cost=True),
        mfe=_first_float(trade, "mfe", "MFE", "mfe_pct", "MFE_pct"),
        mae=_first_float(trade, "mae", "MAE", "mae_pct", "MAE_pct"),
        market_regime=_resolve_market_regime(trade, market_regime),
        volatility_20d_annualized=_resolve_volatility(trade, volatility_20d_annualized),
        metadata={k: _json_safe(v) for k, v in dict(trade).items()},
    )


def normalize_backtest_decision(
    decision: Mapping[str, Any],
    *,
    stock_code: str,
    strategy: str = "",
    accepted: bool,
    source: str = "backtest",
    market_regime: Mapping[str, Any] | None = None,
    volatility_20d_annualized: float | None = None,
) -> dict[str, Any]:
    """Normalize a backtest signal/rejection decision into the shared schema.

    후보 검증형 백테스트는 왕복 체결이 없으므로 PnL 필드는 비워두고,
    동일 journal schema 안에서 decision/rejected reason을 비교 가능하게 남긴다.
    """
    order_price = _to_float(_first_value(decision, "current", "order_price", "price"))
    decision_reason = str(_first_value(decision, "decision_reason", "reason", default=""))
    rejected_reason = str(_first_value(decision, "rejected_reason", default=""))
    if accepted and not decision_reason:
        decision_reason = "signal"
    if not accepted and not rejected_reason:
        rejected_reason = decision_reason or "rejected"

    return _ordered_record(
        source=source,
        strategy=str(strategy or decision.get("strategy") or ""),
        code=str(stock_code or decision.get("code") or decision.get("symbol") or ""),
        signal_time=str(_first_value(decision, "signal_time", "time", "date", default="")),
        decision_reason=decision_reason if accepted else "",
        rejected_reason="" if accepted else rejected_reason,
        side="BUY" if accepted else "REJECTED",
        order_price=order_price,
        fill_price=None,
        qty=_to_int(_first_value(decision, "qty", default=1), default=1),
        status="SIGNAL" if accepted else "REJECTED",
        cost=0.0,
        gross_pnl=None,
        net_pnl=None,
        gross_return=None,
        net_return=None,
        mfe=_first_float(decision, "mfe", "MFE", "mfe_pct", "MFE_pct"),
        mae=_first_float(decision, "mae", "MAE", "mae_pct", "MAE_pct"),
        market_regime=_resolve_market_regime(decision, market_regime),
        volatility_20d_annualized=_resolve_volatility(decision, volatility_20d_annualized),
        metadata={k: _json_safe(v) for k, v in dict(decision).items()},
    )


def normalize_backtest_execution(
    report: Any,
    *,
    source: str = "backtest",
    market_regime: Mapping[str, Any] | None = None,
    volatility_20d_annualized: float | None = None,
) -> dict[str, Any]:
    """Normalize a BacktestExecutionReport into the shared journal schema."""
    order = report.order
    status = str(getattr(report.status, "value", report.status) or "").upper()
    side = str(getattr(order.side, "value", order.side) or "").upper()
    filled_qty = _to_int(getattr(report, "filled_qty", None), default=0)
    order_qty = _to_int(getattr(order, "qty", None), default=0)
    qty = filled_qty if filled_qty > 0 else order_qty
    reason = str(getattr(report, "reason", "") or "")
    decision_reason = str(getattr(order, "decision_reason", "") or reason)
    accepted = filled_qty > 0

    return _ordered_record(
        source=source,
        strategy=str(getattr(order, "strategy", "") or ""),
        code=str(getattr(order, "code", "") or ""),
        signal_time=str(getattr(report, "filled_at", "") or getattr(order, "submitted_at", "") or ""),
        decision_reason=decision_reason if accepted else "",
        rejected_reason="" if accepted else (reason or status.lower()),
        side=side,
        order_price=_to_float(getattr(report, "order_price", None)),
        fill_price=_to_float(getattr(report, "fill_price", None)),
        qty=qty,
        status=status,
        cost=_to_float(getattr(report, "cost", None)) or 0.0,
        gross_pnl=None,
        net_pnl=None,
        gross_return=None,
        net_return=None,
        mfe=_to_float(getattr(report, "mfe", None)),
        mae=_to_float(getattr(report, "mae", None)),
        market_regime=dict(market_regime) if market_regime is not None else None,
        volatility_20d_annualized=_to_float(volatility_20d_annualized),
        metadata={
            "order_id": str(getattr(order, "order_id", "") or ""),
            "order_type": str(getattr(order.order_type, "value", order.order_type) or ""),
            "requested_qty": order_qty,
            "filled_qty": filled_qty,
            "remaining_qty": _to_int(getattr(report, "remaining_qty", None), default=0),
            "gross_amount": _to_float(getattr(report, "gross_amount", None)) or 0.0,
            "execution_reason": reason,
            "slippage_amount_won": _to_float(getattr(report, "slippage_amount_won", None)),
            "slippage_pct": _to_float(getattr(report, "slippage_pct", None)),
            "priority": _to_int(getattr(order, "priority", None), default=0),
            "execution_bar_policy": str(getattr(report, "execution_bar_policy", "") or ""),
        },
    )


def _ordered_record(**values: Any) -> dict[str, Any]:
    record = {"schema_version": SCHEMA_VERSION}
    record.update(values)
    return {field: record.get(field) for field in STANDARD_TRADE_JOURNAL_FIELDS}


def _trade_cost(
    order_price: float | None,
    fill_price: float | None,
    qty: int,
    *,
    include_buy: bool,
    include_sell: bool,
) -> float:
    cost = 0.0
    if include_buy and order_price is not None:
        cost += TransactionCostUtils.calculate_cost(order_price, qty, is_sell=False)
    if include_sell and fill_price is not None:
        cost += TransactionCostUtils.calculate_cost(fill_price, qty, is_sell=True)
    return cost


def _gross_pnl(order_price: float | None, fill_price: float | None, qty: int) -> float | None:
    if order_price is None or fill_price is None:
        return None
    return (fill_price - order_price) * qty


def _net_pnl(order_price: float | None, fill_price: float | None, qty: int) -> float | None:
    if order_price is None or fill_price is None:
        return None
    buy_total = order_price * qty + TransactionCostUtils.calculate_cost(order_price, qty, is_sell=False)
    sell_total = fill_price * qty - TransactionCostUtils.calculate_cost(fill_price, qty, is_sell=True)
    return sell_total - buy_total


def _first_value(data: Mapping[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in data and data.get(key) not in (None, ""):
            return data.get(key)
    return default


def _first_float(data: Mapping[str, Any], *keys: str) -> float | None:
    return _to_float(_first_value(data, *keys))


def _to_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        result = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(result) else result


def _to_int(value: Any, *, default: int) -> int:
    result = _to_float(value)
    return default if result is None else int(result)


def _json_safe(value: Any) -> Any:
    if isinstance(value, float) and math.isnan(value):
        return None
    return value
