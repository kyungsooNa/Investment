"""Backtest execution simulator and portfolio ledger.

백테스트가 실제 주문 경로와 같은 가정을 공유할 수 있도록 체결 판단과
현금/보유 장부를 전략 코드에서 분리한다.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Mapping, Sequence

from utils.transaction_cost_utils import TransactionCostUtils


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    LIMIT = "LIMIT"
    MARKET = "MARKET"


class OrderStatus(str, Enum):
    FILLED = "FILLED"
    PARTIAL = "PARTIAL"
    UNFILLED = "UNFILLED"
    REJECTED = "REJECTED"


@dataclass(frozen=True)
class BacktestBar:
    timestamp: str
    open: float
    high: float
    low: float
    close: float
    volume: int | None = None


@dataclass(frozen=True)
class BacktestOrder:
    order_id: str
    code: str
    side: OrderSide
    order_type: OrderType
    price: float
    qty: int
    strategy: str = ""
    submitted_at: str = ""
    priority: int = 0
    decision_reason: str = ""


@dataclass(frozen=True)
class BacktestExecutionPolicy:
    market_slippage_pct: float = 0.0
    volume_participation_pct: float = 100.0
    market_price_field: str = "open"
    round_to_tick: bool = True
    opening_market_slippage_bonus_pct: float = 0.0


@dataclass(frozen=True)
class BacktestExecutionReport:
    order: BacktestOrder
    status: OrderStatus
    filled_qty: int
    remaining_qty: int
    order_price: float
    fill_price: float | None
    cost: float
    gross_amount: float
    slippage_amount_won: float | None
    slippage_pct: float | None
    reason: str
    filled_at: str
    execution_bar_policy: str = ""
    mfe: float | None = None
    mae: float | None = None


@dataclass(frozen=True)
class PortfolioReservation:
    order: BacktestOrder
    reserved_cash: float


@dataclass(frozen=True)
class PortfolioDecision:
    order: BacktestOrder
    accepted: bool
    reason: str
    reserved_cash: float = 0.0
    warnings: tuple[str, ...] = ()


@dataclass
class PortfolioPosition:
    code: str
    qty: int
    avg_price: float
    strategy: str = ""
    total_cost: float = 0.0

    @property
    def market_value_basis(self) -> float:
        return self.avg_price * self.qty


class BacktestExecutionSimulator:
    """Simulate order fills from one OHLCV bar.

    호출자는 "현재 봉 체결" 또는 "다음 봉 체결" 정책에 맞는 bar를 선택해
    넘긴다. 이 클래스는 해당 bar 안에서 가격 도달, 거래량 한도, 비용,
    슬리피지, 호가 단위 반올림만 책임진다.

    UNFILLED와 PARTIAL의 잔여 수량은 이월하지 않는다(day order 자동 취소).
    다음 봉 재시도가 필요하면 호출자가 별도 주문을 다시 만들어야 한다.
    """

    def __init__(self, policy: BacktestExecutionPolicy | None = None) -> None:
        self.policy = policy or BacktestExecutionPolicy()

    def simulate(self, order: BacktestOrder, bar: BacktestBar) -> BacktestExecutionReport:
        if order.qty <= 0:
            return self._empty_report(order, bar, OrderStatus.REJECTED, "invalid_qty")

        base_price = self._base_fill_price(order, bar)
        if base_price is None:
            return self._empty_report(order, bar, OrderStatus.UNFILLED, "limit_not_reached")

        filled_qty = self._filled_qty(order.qty, bar.volume)
        if filled_qty <= 0:
            return self._empty_report(order, bar, OrderStatus.UNFILLED, "no_volume")

        status = OrderStatus.FILLED if filled_qty == order.qty else OrderStatus.PARTIAL
        fill_price = self._apply_market_slippage(order, base_price)
        if self.policy.round_to_tick:
            fill_price = self.round_to_tick(fill_price, side=order.side)

        is_sell = order.side == OrderSide.SELL
        cost = TransactionCostUtils.calculate_cost(fill_price, filled_qty, is_sell=is_sell)
        slippage_amount = fill_price - base_price
        slippage_pct = (slippage_amount / base_price * 100) if base_price else None
        mfe, mae = self._bar_excursion(order, fill_price, bar)

        return BacktestExecutionReport(
            order=order,
            status=status,
            filled_qty=filled_qty,
            remaining_qty=order.qty - filled_qty,
            order_price=order.price,
            fill_price=fill_price,
            cost=cost,
            gross_amount=fill_price * filled_qty,
            slippage_amount_won=slippage_amount,
            slippage_pct=slippage_pct,
            reason="filled" if status == OrderStatus.FILLED else "partial_fill",
            filled_at=bar.timestamp,
            mfe=mfe,
            mae=mae,
        )

    def _base_fill_price(self, order: BacktestOrder, bar: BacktestBar) -> float | None:
        if order.order_type == OrderType.MARKET:
            return float(getattr(bar, self.policy.market_price_field, bar.open))

        limit_price = float(order.price)
        if order.side == OrderSide.BUY:
            return limit_price if bar.low <= limit_price else None
        return limit_price if bar.high >= limit_price else None

    def _filled_qty(self, requested_qty: int, bar_volume: int | None) -> int:
        if bar_volume is None:
            return requested_qty
        participation = max(self.policy.volume_participation_pct, 0.0) / 100.0
        max_qty = math.floor(bar_volume * participation)
        return min(requested_qty, max_qty)

    def _apply_market_slippage(self, order: BacktestOrder, base_price: float) -> float:
        if order.order_type != OrderType.MARKET:
            return base_price
        slip_pct = self.policy.market_slippage_pct
        if (
            self.policy.opening_market_slippage_bonus_pct > 0
            and self.policy.market_price_field == "open"
        ):
            slip_pct += self.policy.opening_market_slippage_bonus_pct
        if slip_pct <= 0:
            return base_price
        ratio = slip_pct / 100.0
        if order.side == OrderSide.BUY:
            return base_price * (1.0 + ratio)
        return base_price * (1.0 - ratio)

    def _bar_excursion(
        self,
        order: BacktestOrder,
        fill_price: float,
        bar: BacktestBar,
    ) -> tuple[float | None, float | None]:
        if fill_price <= 0:
            return None, None
        if order.side == OrderSide.BUY:
            mfe = (bar.high / fill_price - 1.0) * 100.0
            mae = (bar.low / fill_price - 1.0) * 100.0
        else:
            mfe = (fill_price / bar.low - 1.0) * 100.0 if bar.low > 0 else None
            mae = (fill_price / bar.high - 1.0) * 100.0 if bar.high > 0 else None
        return mfe, mae

    def _empty_report(
        self,
        order: BacktestOrder,
        bar: BacktestBar,
        status: OrderStatus,
        reason: str,
    ) -> BacktestExecutionReport:
        return BacktestExecutionReport(
            order=order,
            status=status,
            filled_qty=0,
            remaining_qty=max(order.qty, 0),
            order_price=order.price,
            fill_price=None,
            cost=0.0,
            gross_amount=0.0,
            slippage_amount_won=None,
            slippage_pct=None,
            reason=reason,
            filled_at=bar.timestamp,
        )

    @staticmethod
    def tick_size(price: float) -> int:
        if price < 2_000:
            return 1
        if price < 5_000:
            return 5
        if price < 20_000:
            return 10
        if price < 50_000:
            return 50
        if price < 200_000:
            return 100
        if price < 500_000:
            return 500
        return 1_000

    @classmethod
    def round_to_tick(cls, price: float, *, side: OrderSide) -> float:
        tick = cls.tick_size(price)
        if side == OrderSide.BUY:
            return float(math.ceil(price / tick) * tick)
        return float(math.floor(price / tick) * tick)


class BacktestPortfolioLedger:
    """Cash, position, and reservation ledger for portfolio backtests."""

    def __init__(self, initial_cash: float) -> None:
        self.initial_cash = float(initial_cash)
        self.cash = float(initial_cash)
        self.positions: dict[str, PortfolioPosition] = {}
        self.reservations: dict[str, PortfolioReservation] = {}
        self.realized_net_pnl = 0.0

    @property
    def reserved_cash(self) -> float:
        return sum(item.reserved_cash for item in self.reservations.values())

    @property
    def available_cash(self) -> float:
        return self.cash - self.reserved_cash

    def reserve_buy_orders(
        self,
        orders: Sequence[BacktestOrder],
        *,
        estimated_prices: Mapping[str, float] | None = None,
        max_positions_per_strategy: Mapping[str, int] | None = None,
    ) -> list[PortfolioDecision]:
        decisions: list[PortfolioDecision] = []
        strategy_counts = self._strategy_position_counts()
        accepted_new_codes: set[str] = set()
        batch_code_counts = _code_counts(
            order for order in orders
            if order.side == OrderSide.BUY
        )
        existing_reservation_codes = {
            reservation.order.code for reservation in self.reservations.values()
        }

        for order in sorted(orders, key=lambda item: item.priority, reverse=True):
            warnings = self._portfolio_warnings(
                order,
                batch_code_counts=batch_code_counts,
                existing_reservation_codes=existing_reservation_codes,
            )
            if order.side != OrderSide.BUY:
                decisions.append(
                    PortfolioDecision(order, accepted=False, reason="not_buy_order", warnings=warnings)
                )
                continue

            limit = (max_positions_per_strategy or {}).get(order.strategy)
            is_new_position = order.code not in self.positions and order.code not in accepted_new_codes
            if limit is not None and is_new_position and strategy_counts.get(order.strategy, 0) >= limit:
                decisions.append(
                    PortfolioDecision(order, accepted=False, reason="max_positions", warnings=warnings)
                )
                continue

            required_cash = self._required_buy_cash(order, estimated_prices=estimated_prices)
            if required_cash <= 0:
                decisions.append(
                    PortfolioDecision(order, accepted=False, reason="invalid_price", warnings=warnings)
                )
                continue
            if self.available_cash < required_cash:
                decisions.append(
                    PortfolioDecision(order, accepted=False, reason="cash_short", warnings=warnings)
                )
                continue

            self.reservations[order.order_id] = PortfolioReservation(order, required_cash)
            if is_new_position:
                accepted_new_codes.add(order.code)
                strategy_counts[order.strategy] = strategy_counts.get(order.strategy, 0) + 1
            decisions.append(
                PortfolioDecision(
                    order,
                    accepted=True,
                    reason="reserved",
                    reserved_cash=required_cash,
                    warnings=warnings,
                )
            )

        return decisions

    def apply_execution(self, report: BacktestExecutionReport) -> None:
        self.reservations.pop(report.order.order_id, None)
        if report.filled_qty <= 0 or report.fill_price is None:
            return

        if report.order.side == OrderSide.BUY:
            self._apply_buy(report)
        else:
            self._apply_sell(report)

    def _apply_buy(self, report: BacktestExecutionReport) -> None:
        order = report.order
        filled_cost = report.gross_amount + report.cost
        self.cash -= filled_cost

        current = self.positions.get(order.code)
        if current is None:
            self.positions[order.code] = PortfolioPosition(
                code=order.code,
                qty=report.filled_qty,
                avg_price=report.fill_price or 0.0,
                strategy=order.strategy,
                total_cost=filled_cost,
            )
            return

        new_qty = current.qty + report.filled_qty
        new_total_cost = current.total_cost + filled_cost
        current.qty = new_qty
        current.total_cost = new_total_cost
        current.avg_price = new_total_cost / new_qty if new_qty > 0 else 0.0

    def _apply_sell(self, report: BacktestExecutionReport) -> None:
        order = report.order
        current = self.positions.get(order.code)
        if current is None or current.qty <= 0:
            return

        sell_qty = min(report.filled_qty, current.qty)
        proceeds = (report.fill_price or 0.0) * sell_qty - report.cost
        avg_cost_per_share = current.total_cost / current.qty if current.qty else 0.0
        realized_cost = avg_cost_per_share * sell_qty

        self.cash += proceeds
        self.realized_net_pnl += proceeds - realized_cost
        current.qty -= sell_qty
        current.total_cost -= realized_cost

        if current.qty <= 0:
            self.positions.pop(order.code, None)
        else:
            current.avg_price = current.total_cost / current.qty

    def _required_buy_cash(
        self,
        order: BacktestOrder,
        *,
        estimated_prices: Mapping[str, float] | None,
    ) -> float:
        price = float(order.price or 0)
        if order.order_type == OrderType.MARKET:
            price = float((estimated_prices or {}).get(order.order_id) or (estimated_prices or {}).get(order.code) or 0)
        if price <= 0 or order.qty <= 0:
            return 0.0
        return price * order.qty + TransactionCostUtils.calculate_cost(price, order.qty, is_sell=False)

    def _strategy_position_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for position in self.positions.values():
            counts[position.strategy] = counts.get(position.strategy, 0) + 1
        return counts

    def _portfolio_warnings(
        self,
        order: BacktestOrder,
        *,
        batch_code_counts: Mapping[str, int],
        existing_reservation_codes: set[str],
    ) -> tuple[str, ...]:
        if order.side != OrderSide.BUY:
            return ()

        warnings: list[str] = []
        if order.code in self.positions:
            warnings.append("same_code_existing_position")
        if order.code in existing_reservation_codes:
            warnings.append("same_code_pending_order")
        if batch_code_counts.get(order.code, 0) > 1:
            warnings.append("same_code_batch_signal")
        return tuple(warnings)


def _code_counts(orders: Iterable[BacktestOrder]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for order in orders:
        counts[order.code] = counts.get(order.code, 0) + 1
    return counts
