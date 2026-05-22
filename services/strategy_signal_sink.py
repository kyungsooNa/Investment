"""SignalSink — event-driven 전략 평가 신호의 publish 지점 contract.

P2 2-4 PR-3 선행 작업. `StrategyEventRouter`가 평가 결과 `TradeSignal`을
`List[TradeSignal]` 반환에만 의존하지 않고 명시 sink에 publish 하도록 contract 정의.

shadow 운영(`event_driven_shadow=True`)은 sink 미주입(None) 상태로 운용되어
기존 동작이 유지된다. live 진입 시(PR-3 본 작업) `OrderIntentQueue` 또는
`StrategyDispatcher` 구현체를 주입해 router 결과를 주문 파이프라인으로 흘린다.

context dict 표준 키:
  - signal_source: "event" (router 경유 신호임을 표시. 2026-05-18 Q4 결정)
  - strategy_name: 평가한 전략 이름
  - code: 종목코드
  - snapshot_ts: tick snapshot 의 epoch seconds (없으면 None)
"""
from __future__ import annotations

from typing import Any, Dict, Protocol, runtime_checkable

from common.types import TradeSignal


@runtime_checkable
class SignalSink(Protocol):
    async def publish(self, signal: TradeSignal, *, context: Dict[str, Any]) -> None:
        ...


class NullSignalSink:
    """기본 no-op sink. shadow 운영 및 sink 미주입 호환."""

    async def publish(self, signal: TradeSignal, *, context: Dict[str, Any]) -> None:
        return None
