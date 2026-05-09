"""Period backtest runner for active live-strategy contracts.

이 runner는 전략 자체를 리팩토링하지 않고 LiveStrategy의 scan/check_exits
contract를 기간 루프로 감싼다. 과거 데이터 replay adapter는 bar_provider로
주입하며, 주문 예약/체결/장부 반영은 P0-3 공통 모듈을 사용한다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, Sequence

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
    async def get_bar(self, *, signal: TradeSignal, date_ymd: str, side: str) -> BacktestBar:
        ...


@dataclass(frozen=True)
class BacktestPeriodRunnerConfig:
    max_positions_per_strategy: dict[str, int] | None = None
    default_qty: int = 1


@dataclass
class BacktestPeriodRunResult:
    strategy_name: str
    dates: list[str]
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
    ) -> None:
        self._strategy = strategy
        self._bar_provider = bar_provider
        self._ledger = ledger
        self._simulator = simulator or BacktestExecutionSimulator()
        self._config = config or BacktestPeriodRunnerConfig()
        self._backtest_journal_repository = backtest_journal_repository
        self._run_id = run_id
        self._metadata = metadata or {}

    async def run(self, dates: Sequence[str]) -> BacktestPeriodRunResult:
        result = BacktestPeriodRunResult(
            strategy_name=self._strategy.name,
            dates=[str(date) for date in dates],
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
            report = await self._execute_signal(signal, date_ymd, side=OrderSide.SELL)
            self._ledger.apply_execution(report)
            result.execution_reports.append(report)
            result.journal_records.append(normalize_backtest_execution(report))

    async def _run_entries(self, date_ymd: str, result: BacktestPeriodRunResult) -> None:
        buy_signals = [
            signal for signal in await self._strategy.scan()
            if signal.action == "BUY"
        ]
        orders = [
            self._signal_to_order(signal, idx, side=OrderSide.BUY)
            for idx, signal in enumerate(buy_signals)
        ]
        decisions = self._ledger.reserve_buy_orders(
            orders,
            max_positions_per_strategy=self._config.max_positions_per_strategy,
        )
        signal_by_order_id = {
            order.order_id: signal
            for order, signal in zip(orders, buy_signals)
        }

        for decision in decisions:
            signal = signal_by_order_id[decision.order.order_id]
            if not decision.accepted:
                result.journal_records.append(
                    self._rejected_signal_record(signal, date_ymd, decision.reason)
                )
                continue

            report = await self._execute_signal(signal, date_ymd, side=OrderSide.BUY)
            self._ledger.apply_execution(report)
            result.execution_reports.append(report)
            result.journal_records.append(normalize_backtest_execution(report))
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
    ) -> BacktestExecutionReport:
        bar = await self._bar_provider.get_bar(
            signal=signal,
            date_ymd=date_ymd,
            side=side.value,
        )
        order = self._signal_to_order(signal, 0, side=side)
        return self._simulator.simulate(order, bar)

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
        )

    def _rejected_signal_record(self, signal: TradeSignal, date_ymd: str, reason: str) -> dict:
        return normalize_backtest_decision(
            {
                "signal_time": _signal_time(date_ymd),
                "current": signal.price,
                "qty": signal.qty or self._config.default_qty,
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
        for target in (self._strategy, self._bar_provider):
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
