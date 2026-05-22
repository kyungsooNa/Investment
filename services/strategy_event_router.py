"""StrategyEventRouter — 이벤트 기반 단일 종목 전략 평가 라우터.

PR-1 (P2 2-4): WebSocket 체결 tick 수신 시 해당 종목을 구독한 전략들의 evaluator 를
동시 디스패치한다. 폴링 루프는 안전망으로 그대로 유지되며, 본 라우터는 추가 트리거다.

게이트 (모두 통과해야 dispatch):
  1. snapshot stale (snapshot_ts 가 now_ts 보다 stale_snapshot_sec 이상 오래되면 차단)
  2. market_open (market_clock.is_market_open_now() 가 False 면 차단)
  3. kill_switch (check_strategies_allowed 가 (False, ...) 면 차단)
  4. throttle (같은 (strategy_name, code) 가 throttle_sec 내 재호출되면 차단)

설계 의도:
  - evaluator 는 strategy 가 직접 등록 (duck typing) — `LiveStrategy` 인터페이스에는
    아직 evaluate_single 을 추가하지 않는다. 첫 전략 적용 PR-2 에서 도입한다.
  - evaluator 예외는 흡수하여 다른 구독자 결과를 보존한다.

설계 결정 (Q1-Q4, 2026-05-18 docs/event_driven_architecture.md §9):
  - throttle_sec=0.5
  - stale_snapshot_sec=5.0

PR-3 선행 (signal sink):
  - signal_sink 가 주입되면 non-None 평가 신호마다 await sink.publish(signal, context=...) 호출.
  - context 표준 키: signal_source="event", strategy_name, code, snapshot_ts.
  - sink 미주입(shadow 운영) 또는 publish 예외는 흡수 → 기존 동작 유지.

PR-3 선행 (throttle/debounce 정책, 2026-05-22 Q5):
  - throttle_sec 은 evaluator 호출 burst 흡수 목적 (default 0.5 유지, 운영은 0.1로 단축).
  - signal_debounce_sec 신규 — 같은 (strategy, code) 의 non-None 신호 publish/return 직전 단계에서
    debounce window 안의 중복 발행을 차단한다. default None=비활성, 운영은 0.5초.
  - trigger price crossing tick 이 evaluator throttle window 에 막히지 않게 두 단계 분리.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import time
from typing import Awaitable, Callable, Dict, List, Optional, Tuple

from common.types import TradeSignal
from services.strategy_signal_sink import SignalSink

EvaluatorFn = Callable[[str, dict], Awaitable[Optional[TradeSignal]]]


class StrategyEventRouter:
    def __init__(
        self,
        market_clock=None,
        kill_switch_service=None,
        logger: Optional[logging.Logger] = None,
        *,
        throttle_sec: float = 0.5,
        stale_snapshot_sec: float = 5.0,
        signal_sink: Optional[SignalSink] = None,
        signal_debounce_sec: Optional[float] = None,
    ):
        self._mc = market_clock
        self._ks = kill_switch_service
        self._logger = logger or logging.getLogger(__name__)
        self._throttle_sec = float(throttle_sec)
        self._stale_snapshot_sec = float(stale_snapshot_sec)
        self._signal_sink = signal_sink
        self._signal_debounce_sec = (
            float(signal_debounce_sec) if signal_debounce_sec is not None else None
        )
        # code → [(strategy_name, evaluator), ...]
        self._subscribers: Dict[str, List[Tuple[str, EvaluatorFn]]] = {}
        # (strategy_name, code) → last evaluator-dispatch epoch seconds
        self._last_dispatched: Dict[Tuple[str, str], float] = {}
        # (strategy_name, code) → last non-None signal publish epoch seconds
        self._last_signal_dispatched: Dict[Tuple[str, str], float] = {}

    def subscribe(self, code: str, *, strategy_name: str, evaluator: EvaluatorFn) -> None:
        if not code or not strategy_name or evaluator is None:
            return
        entries = self._subscribers.setdefault(code, [])
        for i, (name, _fn) in enumerate(entries):
            if name == strategy_name:
                entries[i] = (strategy_name, evaluator)
                return
        entries.append((strategy_name, evaluator))

    def unsubscribe(self, code: str, strategy_name: str) -> None:
        entries = self._subscribers.get(code)
        if not entries:
            return
        remaining = [(n, fn) for (n, fn) in entries if n != strategy_name]
        if remaining:
            self._subscribers[code] = remaining
        else:
            self._subscribers.pop(code, None)
        self._last_dispatched.pop((strategy_name, code), None)
        self._last_signal_dispatched.pop((strategy_name, code), None)

    def subscribers_for(self, code: str) -> List[str]:
        return [name for (name, _fn) in self._subscribers.get(code, [])]

    async def on_price_tick(
        self,
        code: str,
        snapshot: dict,
        *,
        snapshot_ts: Optional[float] = None,
        now_ts: Optional[float] = None,
    ) -> List[TradeSignal]:
        if not code:
            return []
        entries = list(self._subscribers.get(code, []))
        if not entries:
            return []

        now = now_ts if now_ts is not None else time.time()

        if snapshot_ts is not None and (now - snapshot_ts) > self._stale_snapshot_sec:
            self._logger.debug(
                f"[EventRouter] stale snapshot 차단: code={code}, age={now - snapshot_ts:.2f}s"
            )
            return []

        if self._mc is not None:
            is_open = await _maybe_await(self._mc.is_market_open_now())
            if not is_open:
                return []

        if self._ks is not None:
            try:
                allowed_pair = await _maybe_await(self._ks.check_strategies_allowed())
                allowed = bool(allowed_pair[0]) if isinstance(allowed_pair, tuple) else bool(allowed_pair)
            except Exception as e:  # 게이트 자체 오류 시 보수적으로 dispatch 차단
                self._logger.warning(f"[EventRouter] kill_switch 체크 오류: {e}")
                return []
            if not allowed:
                return []

        dispatchable: List[Tuple[str, EvaluatorFn]] = []
        for (name, evaluator) in entries:
            key = (name, code)
            last = self._last_dispatched.get(key)
            if last is not None and (now - last) < self._throttle_sec:
                continue
            self._last_dispatched[key] = now
            dispatchable.append((name, evaluator))

        if not dispatchable:
            return []

        async def _run(strategy_name: str, fn: EvaluatorFn) -> Optional[TradeSignal]:
            try:
                return await fn(code, snapshot)
            except Exception as e:
                self._logger.warning(
                    f"[EventRouter] evaluator 예외: strategy={strategy_name}, code={code}, error={e}"
                )
                return None

        results = await asyncio.gather(*(_run(n, fn) for (n, fn) in dispatchable))

        signals: List[TradeSignal] = []
        for (name, _fn), signal in zip(dispatchable, results):
            if signal is None:
                continue
            key = (name, code)
            if self._signal_debounce_sec is not None:
                last_pub = self._last_signal_dispatched.get(key)
                if last_pub is not None and (now - last_pub) < self._signal_debounce_sec:
                    self._logger.debug(
                        f"[EventRouter] signal debounce: strategy={name}, code={code}, "
                        f"age={now - last_pub:.3f}s < {self._signal_debounce_sec}s"
                    )
                    continue
                self._last_signal_dispatched[key] = now
            signals.append(signal)
            if self._signal_sink is None:
                continue
            try:
                await self._signal_sink.publish(
                    signal,
                    context={
                        "signal_source": "event",
                        "strategy_name": name,
                        "code": code,
                        "snapshot_ts": snapshot_ts,
                    },
                )
            except Exception as e:
                self._logger.warning(
                    f"[EventRouter] signal_sink.publish 예외: strategy={name}, code={code}, error={e}"
                )
        return signals


async def _maybe_await(value):
    if inspect.isawaitable(value):
        return await value
    return value
