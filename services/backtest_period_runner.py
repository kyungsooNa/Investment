"""Period backtest runner for active live-strategy contracts.

이 runner는 전략 자체를 리팩토링하지 않고 LiveStrategy의 scan/check_exits
contract를 기간 루프로 감싼다. 과거 데이터 replay adapter는 bar_provider로
주입하며, 주문 예약/체결/장부 반영은 P0-3 공통 모듈을 사용한다.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Protocol, Sequence

from common.types import Exchange, OrderSide as LiveOrderSide
from common.trade_journal_schema import normalize_backtest_decision, normalize_backtest_execution
from common.types import TradeSignal
from interfaces.live_strategy import LiveStrategy
from services.backtest_execution_simulator import (
    BacktestBar,
    BacktestExecutionReport,
    BacktestExecutionSimulator,
    BacktestOrder,
    BacktestPortfolioLedger,
    OrderSide,
    OrderType,
)


class BacktestBarProvider(Protocol):
    async def get_bar(
        self,
        *,
        signal: TradeSignal,
        date_ymd: str,
        side: str,
        execution_policy: str,
    ) -> BacktestBar:
        ...


class BacktestPositionSizer(Protocol):
    async def adjust_buy_qty(self, signal: TradeSignal, exchange: Exchange | None = None) -> tuple[int, str]:
        ...


class BacktestRiskGate(Protocol):
    async def validate_order(self, **kwargs):
        ...


class BacktestExecutionBarPolicy(str, Enum):
    CURRENT_BAR = "current_bar"
    NEXT_BAR = "next_bar"


@dataclass(frozen=True)
class BacktestPeriodRunnerConfig:
    max_positions_per_strategy: dict[str, int] | None = None
    default_qty: int = 1
    execution_bar_policy: BacktestExecutionBarPolicy | str = BacktestExecutionBarPolicy.CURRENT_BAR


@dataclass
class BacktestPeriodRunResult:
    strategy_name: str
    dates: list[str]
    execution_bar_policy: str = ""
    execution_reports: list[BacktestExecutionReport] = field(default_factory=list)
    journal_records: list[dict] = field(default_factory=list)
    portfolio: dict = field(default_factory=dict)
    saved_journal_run: dict = field(default_factory=dict)


class BacktestPeriodRunner:
    def __init__(
        self,
        *,
        strategy: LiveStrategy,
        bar_provider: BacktestBarProvider,
        ledger: BacktestPortfolioLedger,
        simulator: BacktestExecutionSimulator | None = None,
        config: BacktestPeriodRunnerConfig | None = None,
        backtest_journal_repository=None,
        run_id: str | None = None,
        metadata: dict | None = None,
        position_sizing_service: BacktestPositionSizer | None = None,
        risk_gate_service: BacktestRiskGate | None = None,
        date_context_targets: Sequence[object] | None = None,
    ) -> None:
        self._strategy = strategy
        self._bar_provider = bar_provider
        self._ledger = ledger
        self._simulator = simulator or BacktestExecutionSimulator()
        self._config = config or BacktestPeriodRunnerConfig()
        self._backtest_journal_repository = backtest_journal_repository
        self._run_id = run_id
        self._metadata = metadata or {}
        self._position_sizing_service = position_sizing_service
        self._risk_gate_service = risk_gate_service
        self._date_context_targets = list(date_context_targets or [])
        self._position_excursions: dict[str, dict[str, float | None]] = {}

    async def run(self, dates: Sequence[str]) -> BacktestPeriodRunResult:
        result = BacktestPeriodRunResult(
            strategy_name=self._strategy.name,
            dates=[str(date) for date in dates],
            execution_bar_policy=_execution_bar_policy_value(self._config.execution_bar_policy),
        )

        for date_ymd in result.dates:
            self._set_backtest_date(date_ymd)
            await self._run_exits(date_ymd, result)
            await self._run_entries(date_ymd, result)

        result.portfolio = self._portfolio_summary()
        self._persist_journal(result)
        return result

    async def _run_exits(self, date_ymd: str, result: BacktestPeriodRunResult) -> None:
        holdings = self._holdings_for_strategy()
        sell_signals = await self._strategy.check_exits(holdings)
        for signal in sell_signals:
            order = self._signal_to_order(signal, 0, side=OrderSide.SELL)
            blocked_reason = await self._risk_gate_rejection_reason(order, signal, side=OrderSide.SELL)
            if blocked_reason:
                result.journal_records.append(
                    self._rejected_signal_record(signal, date_ymd, blocked_reason, qty=order.qty)
                )
                continue

            report = await self._execute_signal(signal, date_ymd, side=OrderSide.SELL, order=order)
            sell_metrics = self._sell_realized_metrics(report)
            self._ledger.apply_execution(report)
            self._forget_position_excursion_if_closed(report)
            result.execution_reports.append(report)
            result.journal_records.append(
                self._execution_record(report, realized_metrics=sell_metrics)
            )

    async def _run_entries(self, date_ymd: str, result: BacktestPeriodRunResult) -> None:
        buy_signals = [
            signal for signal in await self._strategy.scan()
            if signal.action == "BUY"
        ]
        sized_signals: list[TradeSignal] = []
        for signal in buy_signals:
            sized_signal = await self._apply_position_sizing(signal, date_ymd, result)
            if sized_signal is not None:
                sized_signals.append(sized_signal)

        orders = [
            self._signal_to_order(signal, idx, side=OrderSide.BUY)
            for idx, signal in enumerate(sized_signals)
        ]
        risk_passed: list[tuple[BacktestOrder, TradeSignal]] = []
        for order, signal in zip(orders, sized_signals):
            blocked_reason = await self._risk_gate_rejection_reason(order, signal, side=OrderSide.BUY)
            if blocked_reason:
                result.journal_records.append(
                    self._rejected_signal_record(signal, date_ymd, blocked_reason, qty=order.qty)
                )
                continue
            risk_passed.append((order, signal))

        decisions = self._ledger.reserve_buy_orders(
            [order for order, _ in risk_passed],
            max_positions_per_strategy=self._config.max_positions_per_strategy,
        )
        signal_by_order_id = {
            order.order_id: signal
            for order, signal in risk_passed
        }

        for decision in decisions:
            signal = signal_by_order_id[decision.order.order_id]
            if not decision.accepted:
                result.journal_records.append(
                    self._rejected_signal_record(signal, date_ymd, decision.reason)
                )
                continue

            report = await self._execute_signal(signal, date_ymd, side=OrderSide.BUY, order=decision.order)
            self._ledger.apply_execution(report)
            self._remember_position_excursion(report)
            result.execution_reports.append(report)
            result.journal_records.append(self._execution_record(report))
            if report.filled_qty <= 0:
                result.journal_records.append(
                    self._rejected_signal_record(signal, date_ymd, report.reason)
                )

    async def _execute_signal(
        self,
        signal: TradeSignal,
        date_ymd: str,
        *,
        side: OrderSide,
        order: BacktestOrder | None = None,
    ) -> BacktestExecutionReport:
        execution_policy = _execution_bar_policy_value(self._config.execution_bar_policy)
        bar = await self._bar_provider.get_bar(
            signal=signal,
            date_ymd=date_ymd,
            side=side.value,
            execution_policy=execution_policy,
        )
        order = order or self._signal_to_order(signal, 0, side=side)
        report = self._simulator.simulate(order, bar)
        if side == OrderSide.SELL:
            report = self._with_holding_period_excursion(report, bar)
        return replace(report, execution_bar_policy=execution_policy)

    async def _apply_position_sizing(
        self,
        signal: TradeSignal,
        date_ymd: str,
        result: BacktestPeriodRunResult,
    ) -> TradeSignal | None:
        if self._position_sizing_service is None:
            return signal

        qty, reason = await self._position_sizing_service.adjust_buy_qty(
            signal,
            _exchange_for_signal(signal),
        )
        if qty == 0:
            result.journal_records.append(
                self._rejected_signal_record(
                    signal,
                    date_ymd,
                    f"sizing_skip:{reason}",
                    qty=0,
                )
            )
            return None
        return _signal_with_qty(signal, qty)

    async def _risk_gate_rejection_reason(
        self,
        order: BacktestOrder,
        signal: TradeSignal,
        *,
        side: OrderSide,
    ) -> str:
        if self._risk_gate_service is None:
            return ""

        response = await self._risk_gate_service.validate_order(
            stock_code=order.code,
            price=int(order.price),
            qty=order.qty,
            side=_live_order_side(side),
            exchange=_exchange_for_signal(signal),
            active_order_count=len(self._ledger.reservations),
            source=f"strategy:{order.strategy}",
            strategy_name=order.strategy,
        )
        if response is None:
            return ""
        data = getattr(response, "data", None)
        rule = data.get("rule") if isinstance(data, dict) else None
        return f"risk_gate:{rule or 'blocked'}"

    def _holdings_for_strategy(self) -> list[dict]:
        holdings: list[dict] = []
        for position in self._ledger.positions.values():
            if position.strategy != self._strategy.name:
                continue
            holdings.append({
                "strategy": position.strategy,
                "code": position.code,
                "name": position.code,
                "buy_price": position.avg_price,
                "qty": position.qty,
                "status": "HOLD",
            })
        return holdings

    def _signal_to_order(self, signal: TradeSignal, idx: int, *, side: OrderSide) -> BacktestOrder:
        return BacktestOrder(
            order_id=f"{self._strategy.name}_{signal.code}_{signal.action}_{idx}",
            code=signal.code,
            side=side,
            order_type=OrderType.LIMIT,
            price=signal.price,
            qty=signal.qty or self._config.default_qty,
            strategy=signal.strategy_name or self._strategy.name,
            submitted_at="",
            priority=0,
            decision_reason=signal.reason,
        )

    def _rejected_signal_record(
        self,
        signal: TradeSignal,
        date_ymd: str,
        reason: str,
        *,
        qty: int | None = None,
    ) -> dict:
        return normalize_backtest_decision(
            {
                "signal_time": _signal_time(date_ymd),
                "current": signal.price,
                "qty": self._resolved_qty(signal, qty),
                "rejected_reason": reason,
                "strategy": signal.strategy_name or self._strategy.name,
                "name": signal.name,
                "action": signal.action,
                "exchange": signal.exchange,
            },
            stock_code=signal.code,
            strategy=signal.strategy_name or self._strategy.name,
            accepted=False,
        )

    def _resolved_qty(self, signal: TradeSignal, qty: int | None = None) -> int:
        if qty is not None:
            return qty
        return signal.qty if signal.qty is not None else self._config.default_qty

    def _execution_record(
        self,
        report: BacktestExecutionReport,
        *,
        realized_metrics: dict | None = None,
    ) -> dict:
        record = normalize_backtest_execution(report)
        if realized_metrics and report.order.side == OrderSide.SELL and report.filled_qty > 0:
            record["status"] = "SOLD"
            record["net_pnl"] = realized_metrics.get("net_pnl")
            record["net_return"] = realized_metrics.get("net_return")
            metadata = dict(record.get("metadata") or {})
            metadata.update({
                "realized_cost": realized_metrics.get("realized_cost"),
                "net_proceeds": realized_metrics.get("net_proceeds"),
            })
            record["metadata"] = metadata
        return record

    def _remember_position_excursion(self, report: BacktestExecutionReport) -> None:
        if report.order.side != OrderSide.BUY or report.filled_qty <= 0:
            return
        current = self._position_excursions.get(report.order.code, {})
        self._position_excursions[report.order.code] = {
            "mfe": _max_optional(current.get("mfe"), report.mfe),
            "mae": _min_optional(current.get("mae"), report.mae),
        }

    def _forget_position_excursion_if_closed(self, report: BacktestExecutionReport) -> None:
        position = self._ledger.positions.get(report.order.code)
        if position is None or position.qty <= 0:
            self._position_excursions.pop(report.order.code, None)

    def _with_holding_period_excursion(
        self,
        report: BacktestExecutionReport,
        bar: BacktestBar,
    ) -> BacktestExecutionReport:
        position = self._ledger.positions.get(report.order.code)
        if position is None or position.avg_price <= 0 or report.filled_qty <= 0:
            return report

        bar_mfe = (bar.high / position.avg_price - 1.0) * 100.0
        bar_mae = (bar.low / position.avg_price - 1.0) * 100.0
        current = self._position_excursions.get(report.order.code, {})
        return replace(
            report,
            mfe=_max_optional(current.get("mfe"), bar_mfe),
            mae=_min_optional(current.get("mae"), bar_mae),
        )

    def _sell_realized_metrics(self, report: BacktestExecutionReport) -> dict:
        if report.order.side != OrderSide.SELL or report.filled_qty <= 0 or report.fill_price is None:
            return {}
        position = self._ledger.positions.get(report.order.code)
        if position is None or position.qty <= 0:
            return {}

        sell_qty = min(report.filled_qty, position.qty)
        avg_cost_per_share = position.total_cost / position.qty if position.qty else 0.0
        realized_cost = avg_cost_per_share * sell_qty
        net_proceeds = report.fill_price * sell_qty - report.cost
        net_pnl = net_proceeds - realized_cost
        net_return = (net_pnl / realized_cost * 100.0) if realized_cost else None
        return {
            "realized_cost": realized_cost,
            "net_proceeds": net_proceeds,
            "net_pnl": net_pnl,
            "net_return": net_return,
        }

    def _portfolio_summary(self) -> dict:
        return {
            "initial_cash": self._ledger.initial_cash,
            "cash": self._ledger.cash,
            "reserved_cash": self._ledger.reserved_cash,
            "available_cash": self._ledger.available_cash,
            "realized_net_pnl": self._ledger.realized_net_pnl,
            "positions": {
                code: {
                    "qty": position.qty,
                    "avg_price": position.avg_price,
                    "strategy": position.strategy,
                    "total_cost": position.total_cost,
                }
                for code, position in self._ledger.positions.items()
            },
        }

    def _set_backtest_date(self, date_ymd: str) -> None:
        for target in (self._strategy, self._bar_provider, *self._date_context_targets):
            setter = getattr(target, "set_backtest_date", None)
            if callable(setter):
                setter(date_ymd)

    def _persist_journal(self, result: BacktestPeriodRunResult) -> None:
        if self._backtest_journal_repository is None or not result.journal_records:
            return
        metadata = {
            "dates": result.dates,
            "date_count": len(result.dates),
            "initial_cash": self._ledger.initial_cash,
            "execution_report_count": len(result.execution_reports),
            "execution_bar_policy": _execution_bar_policy_value(self._config.execution_bar_policy),
            "portfolio": result.portfolio,
            **self._metadata,
        }
        result.saved_journal_run = self._backtest_journal_repository.save_run(
            result.journal_records,
            run_id=self._run_id or _default_run_id(result.strategy_name, result.dates),
            strategy=result.strategy_name,
            target_date=_target_date(result.dates),
            metadata=metadata,
        )


def _signal_time(date_ymd: str) -> str:
    if len(str(date_ymd)) == 8:
        return f"{date_ymd[:4]}-{date_ymd[4:6]}-{date_ymd[6:8]} 00:00:00"
    return str(date_ymd)


def _target_date(dates: Sequence[str]) -> str:
    if not dates:
        return ""
    if len(dates) == 1:
        return str(dates[0])
    return f"{dates[0]}_{dates[-1]}"


def _default_run_id(strategy_name: str, dates: Sequence[str]) -> str:
    target_date = _target_date(dates) or "unknown"
    return f"period_{strategy_name}_{target_date}"


def _signal_with_qty(signal: TradeSignal, qty: int) -> TradeSignal:
    return signal.model_copy(update={"qty": qty})


def _exchange_for_signal(signal: TradeSignal) -> Exchange:
    try:
        return Exchange(signal.exchange) if signal.exchange else Exchange.KRX
    except ValueError:
        return Exchange.KRX


def _live_order_side(side: OrderSide) -> LiveOrderSide:
    return LiveOrderSide.BUY if side == OrderSide.BUY else LiveOrderSide.SELL


def _max_optional(left: float | None, right: float | None) -> float | None:
    values = [value for value in (left, right) if value is not None]
    return max(values) if values else None


def _min_optional(left: float | None, right: float | None) -> float | None:
    values = [value for value in (left, right) if value is not None]
    return min(values) if values else None


def _execution_bar_policy_value(policy: BacktestExecutionBarPolicy | str) -> str:
    return str(getattr(policy, "value", policy) or BacktestExecutionBarPolicy.CURRENT_BAR.value)
