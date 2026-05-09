from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from common.trade_journal_schema import normalize_backtest_decision
from common.types import TradeSignal
from interfaces.live_strategy import LiveStrategy
from strategies.debug.rejection_collector import RejectionCollector, RejectionEvent
from services.backtest_execution_simulator import (
    BacktestOrder,
    BacktestPortfolioLedger,
    OrderSide,
    OrderType,
    PortfolioDecision,
)


@dataclass
class DebugReport:
    """전략 디버깅 실행 결과."""
    strategy_name: str
    requested_codes: Optional[List[str]]  # CLI --codes 그대로 (None이면 universe 전체)
    scanned_codes: List[str]              # 실제 scan에 들어간 종목 (proxy 통과 후)
    missing_codes: List[str]              # requested 중 universe 교집합에 없는 종목
    signals: List[TradeSignal]
    events: List[RejectionEvent]
    limitations: List[str] = field(default_factory=list)
    journal_records: List[dict] = field(default_factory=list)
    portfolio_decisions: List[PortfolioDecision] = field(default_factory=list)


class _UniverseFilterProxy:
    """OneilUniverseService를 감싸 watchlist만 필터링, 나머지 호출은 원본에 위임.

    get_watchlist()가 Dict[code, item] 형태로 반환한다는 것을 전제로 한다.
    """

    def __init__(self, inner: object, allowed: Optional[Set[str]] = None) -> None:
        self._inner = inner
        self._allowed = allowed
        self._last_full_set: Set[str] = set()
        self._last_full_codes: List[str] = []
        self._last_scanned_codes: List[str] = []

    async def get_watchlist(self, **kw) -> Dict:
        full: Dict = await self._inner.get_watchlist(**kw)
        self._last_full_codes = list(full.keys())
        self._last_full_set = set(self._last_full_codes)
        if self._allowed is None:
            self._last_scanned_codes = list(full.keys())
            return full
        filtered = {code: item for code, item in full.items() if code in self._allowed}
        self._last_scanned_codes = list(filtered.keys())
        return filtered

    def __getattr__(self, name: str):
        # is_market_timing_ok 등 다른 메서드는 원본에 그대로 위임
        return getattr(self._inner, name)


class StrategyDebugRunner:
    """전략을 한 번 실행하면서 RejectionCollector로 탈락 이유를 수집한다.

    사용 예:
        debug_logger = logging.getLogger("strategy_debug.OneilPocketPivot")
        strategy = OneilPocketPivotStrategy(..., logger=debug_logger)
        runner = StrategyDebugRunner(strategy, debug_logger)
        report = await runner.run(candidate_codes=["005930", "000660"])

    StageGuard 활성화:
        runner = StrategyDebugRunner(strategy, debug_logger, stage_service=minervini_svc)
    """

    LIMITATIONS = [
        "entry_rejected(reason=low_execution_strength) 로그에는 entry_type 필드가 없어 "
        "PP/BGU 중 어느 쪽이 통과 후 탈락했는지 '추정'으로만 표시함.",
        "StageGuard 탈락은 stage_service가 주입된 디버그 실행에서만 수집함.",
    ]

    def __init__(
        self,
        strategy: LiveStrategy,
        debug_logger: logging.Logger,
        stage_service=None,
        allowed_stages: tuple = (0, 2),
        backtest_journal_repository=None,
        target_date: str = "",
        backtest_portfolio_ledger: BacktestPortfolioLedger | None = None,
        max_positions_per_strategy: dict[str, int] | None = None,
    ) -> None:
        self._strategy = strategy
        self._debug_logger = debug_logger
        self._stage_service = stage_service
        self._allowed_stages = allowed_stages
        self._backtest_journal_repository = backtest_journal_repository
        self._target_date = target_date
        self._backtest_portfolio_ledger = backtest_portfolio_ledger
        self._max_positions_per_strategy = max_positions_per_strategy

    async def _apply_stage_guard(self, codes: List[str]) -> List[str]:
        """stage_service가 주입된 경우 stage 필터를 적용하고 stage_blocked 이벤트를 emit한다."""
        allowed: List[str] = []
        for code in codes:
            try:
                result = await self._stage_service.get_stage_for_code(code)
                stage = result[0] if isinstance(result, tuple) else int(result)
            except Exception:
                stage = -1
            if stage in self._allowed_stages:
                allowed.append(code)
            else:
                self._debug_logger.info({"event": "stage_blocked", "code": code, "stage": stage})
        return allowed

    async def run(self, candidate_codes: Optional[List[str]] = None) -> DebugReport:
        """전략을 1회 실행하고 DebugReport를 반환한다.

        Args:
            candidate_codes: 스캔할 종목 코드 목록. None이면 universe 전체를 스캔.
                             universe watchlist에 없는 코드는 missing_codes에 기록된다.
        """
        original_universe = getattr(self._strategy, "_universe", None)
        proxy: Optional[_UniverseFilterProxy] = None
        scanned_codes: List[str] = []
        missing_codes: List[str] = []

        if original_universe is not None:
            allowed = set(candidate_codes) if candidate_codes else None
            proxy = _UniverseFilterProxy(original_universe, allowed=allowed)

        try:
            if proxy is not None:
                self._strategy._universe = proxy
                # candidate_codes 지정 시 missing_codes 정확히 계산, stage_service 주입 시 stage 체크를 위해 먼저 호출
                if candidate_codes or self._stage_service is not None:
                    await proxy.get_watchlist()

                if self._stage_service is not None:
                    passed = await self._apply_stage_guard(proxy._last_scanned_codes)
                    proxy._allowed = set(passed)

            with RejectionCollector(logger=self._debug_logger) as col:
                signals = await self._strategy.scan()

            if proxy is not None and candidate_codes:
                full_set = proxy._last_full_set
                scanned_codes = [c for c in candidate_codes if c in full_set]
                missing_codes = [c for c in candidate_codes if c not in full_set]
            elif proxy is not None:
                scanned_codes = list(proxy._last_scanned_codes)
            elif candidate_codes:
                scanned_codes = list(candidate_codes)

        finally:
            if proxy is not None and original_universe is not None:
                self._strategy._universe = original_universe

        report = DebugReport(
            strategy_name=self._strategy.name,
            requested_codes=candidate_codes,
            scanned_codes=scanned_codes,
            missing_codes=missing_codes,
            signals=signals,
            events=col.events,
            limitations=list(self.LIMITATIONS),
        )
        report.portfolio_decisions = self._reserve_signal_orders(signals)
        report.journal_records = _build_debug_journal_records(report, target_date=self._target_date)

        if self._backtest_journal_repository is not None and report.journal_records:
            target_date = self._target_date or _target_date_from_records(report.journal_records)
            self._backtest_journal_repository.save_run(
                report.journal_records,
                run_id=f"debug_{report.strategy_name}_{target_date or 'unknown'}",
                strategy=report.strategy_name,
                target_date=target_date,
                metadata={
                    "requested_codes": report.requested_codes,
                    "scanned_codes": report.scanned_codes,
                    "missing_codes": report.missing_codes,
                    "event_count": len(report.events),
                    "signal_count": len(report.signals),
                },
            )

        return report

    def _reserve_signal_orders(self, signals: List[TradeSignal]) -> List[PortfolioDecision]:
        if self._backtest_portfolio_ledger is None:
            return []

        orders = [
            _signal_to_backtest_order(signal, idx)
            for idx, signal in enumerate(signals)
            if signal.action == "BUY"
        ]
        if not orders:
            return []
        return self._backtest_portfolio_ledger.reserve_buy_orders(
            orders,
            max_positions_per_strategy=self._max_positions_per_strategy,
        )


def _build_debug_journal_records(report: DebugReport, *, target_date: str = "") -> List[dict]:
    records: List[dict] = []
    default_time = _signal_time_from_target_date(target_date)
    portfolio_decision_by_code = {
        decision.order.code: decision
        for decision in report.portfolio_decisions
    }

    for signal in report.signals:
        portfolio_decision = portfolio_decision_by_code.get(signal.code)
        if portfolio_decision is not None and not portfolio_decision.accepted:
            records.append(
                normalize_backtest_decision(
                    {
                        "signal_time": default_time,
                        "current": signal.price,
                        "qty": signal.qty,
                        "rejected_reason": portfolio_decision.reason,
                        "strategy": signal.strategy_name or report.strategy_name,
                        "name": signal.name,
                        "action": signal.action,
                        "exchange": signal.exchange,
                    },
                    stock_code=signal.code,
                    strategy=signal.strategy_name or report.strategy_name,
                    accepted=False,
                )
            )
            continue

        records.append(
            normalize_backtest_decision(
                {
                    "signal_time": default_time,
                    "current": signal.price,
                    "qty": signal.qty,
                    "decision_reason": signal.reason or signal.action,
                    "strategy": signal.strategy_name or report.strategy_name,
                    "name": signal.name,
                    "action": signal.action,
                    "exchange": signal.exchange,
                },
                stock_code=signal.code,
                strategy=signal.strategy_name or report.strategy_name,
                accepted=True,
            )
        )

    for event in report.events:
        records.append(
            normalize_backtest_decision(
                {
                    **event.details,
                    "signal_time": _event_signal_time(event, target_date),
                    "current": _event_price(event),
                    "rejected_reason": event.reason,
                },
                stock_code=event.code,
                strategy=report.strategy_name,
                accepted=False,
            )
        )

    for code in report.missing_codes:
        records.append(
            normalize_backtest_decision(
                {
                    "signal_time": default_time,
                    "rejected_reason": "missing_from_universe",
                    "event": "missing_from_universe",
                },
                stock_code=code,
                strategy=report.strategy_name,
                accepted=False,
            )
        )

    return records


def _signal_to_backtest_order(signal: TradeSignal, idx: int) -> BacktestOrder:
    return BacktestOrder(
        order_id=f"debug_{signal.strategy_name or 'strategy'}_{signal.code}_{idx}",
        code=signal.code,
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        price=signal.price,
        qty=signal.qty or 1,
        strategy=signal.strategy_name,
        priority=0,
    )


def _event_price(event: RejectionEvent):
    for key in ("current", "price", "order_price", "stck_prpr"):
        if key in event.details:
            return event.details.get(key)
    return None


def _event_signal_time(event: RejectionEvent, target_date: str = "") -> str:
    if target_date:
        return _signal_time_from_target_date(target_date)
    return event.timestamp.strftime("%Y-%m-%d %H:%M:%S")


def _signal_time_from_target_date(target_date: str = "") -> str:
    if len(str(target_date)) == 8:
        return f"{target_date[:4]}-{target_date[4:6]}-{target_date[6:8]} 00:00:00"
    return ""


def _target_date_from_records(records: List[dict]) -> str:
    for record in records:
        signal_time = str(record.get("signal_time") or "")
        digits = "".join(ch for ch in signal_time[:10] if ch.isdigit())
        if len(digits) == 8:
            return digits
    return ""
