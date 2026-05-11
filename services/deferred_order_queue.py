"""DeferredOrderQueue.

동일 종목에 진행 중(non-terminal)인 주문이 있을 때 신규 주문을 즉시 실패시키지 않고
보류 큐에 넣어두었다가, 기존 주문이 terminal 상태(FILLED/CANCELED/REJECTED)에 도달하면
TTL 과 옵션 risk_check 를 거쳐 자동 재시도한다.

설계 원칙:
- 메모리 only (영속화 X). 재기동 시 스케줄러가 다음 사이클에 새 신호를 만들 것이므로 휘발성으로 충분.
- key = (stock_code, side). 같은 키에 이미 보류 항목이 있으면 새 요청은 폐기 (중복 진입 방지).
- 신호 신선도: enqueue 시점 + ttl_sec(기본 60초). TTL 초과면 폐기.
- risk_check: 옵션 콜러블. False 반환 시 폐기.
- DeferredOrderQueue 자체는 broker / signal 구조를 모름 — submit_callable closure 만 호출.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Awaitable, Callable, Dict, Optional, Tuple


class EnqueueResult(Enum):
    """enqueue() 결과."""
    QUEUED = "queued"
    DUPLICATE_DROPPED = "duplicate_dropped"


SubmitCallable = Callable[[], Awaitable[object]]
RiskCheckCallable = Callable[[], Awaitable[bool]]


@dataclass
class DeferredEntry:
    stock_code: str
    side: str
    enqueued_at: datetime
    ttl_sec: float
    submit_callable: SubmitCallable
    risk_check: Optional[RiskCheckCallable] = None
    description: str = ""

    def is_expired(self, now: datetime) -> bool:
        return now - self.enqueued_at > timedelta(seconds=self.ttl_sec)


class DeferredOrderQueue:
    """주문 보류 큐. 진행 중 주문 종결 시 자동 재시도."""

    def __init__(self, logger, *, now_provider: Optional[Callable[[], datetime]] = None) -> None:
        self._logger = logger
        self._now = now_provider or datetime.now
        self._entries: Dict[Tuple[str, str], DeferredEntry] = {}
        self._lock = asyncio.Lock()

    async def enqueue(
        self,
        *,
        stock_code: str,
        side: str,
        submit_callable: SubmitCallable,
        ttl_sec: float = 60.0,
        risk_check: Optional[RiskCheckCallable] = None,
        description: str = "",
    ) -> EnqueueResult:
        key = (str(stock_code), str(side))
        async with self._lock:
            if key in self._entries:
                self._logger.info(
                    f"[DeferredOrderQueue] 중복 보류 폐기: code={stock_code} side={side} "
                    f"description={description}"
                )
                return EnqueueResult.DUPLICATE_DROPPED
            self._entries[key] = DeferredEntry(
                stock_code=str(stock_code),
                side=str(side),
                enqueued_at=self._now(),
                ttl_sec=ttl_sec,
                submit_callable=submit_callable,
                risk_check=risk_check,
                description=description,
            )
            self._logger.info(
                f"[DeferredOrderQueue] 보류 등록: code={stock_code} side={side} "
                f"ttl={ttl_sec}s description={description}"
            )
            return EnqueueResult.QUEUED

    async def notify_terminal(self, stock_code: str) -> None:
        """해당 종목의 진행 주문이 terminal 에 도달했음을 알림.

        BUY/SELL 양쪽 보류 항목을 모두 깨우려 시도한다 (각각 독립 검증).
        """
        for side in ("BUY", "SELL"):
            await self._try_release(str(stock_code), side)

    async def _try_release(self, stock_code: str, side: str) -> None:
        key = (stock_code, side)
        async with self._lock:
            entry = self._entries.pop(key, None)
        if entry is None:
            return

        if entry.is_expired(self._now()):
            self._logger.warning(
                f"[DeferredOrderQueue] TTL 만료 폐기: code={stock_code} side={side} "
                f"ttl={entry.ttl_sec}s description={entry.description}"
            )
            return

        if entry.risk_check is not None:
            try:
                ok = await entry.risk_check()
            except Exception as e:
                self._logger.warning(
                    f"[DeferredOrderQueue] risk_check 예외 → 폐기: "
                    f"code={stock_code} side={side} err={e}"
                )
                return
            if not ok:
                self._logger.info(
                    f"[DeferredOrderQueue] risk_check 실패 → 폐기: "
                    f"code={stock_code} side={side} description={entry.description}"
                )
                return

        self._logger.info(
            f"[DeferredOrderQueue] 보류 재시도 실행: code={stock_code} side={side} "
            f"description={entry.description}"
        )
        try:
            await entry.submit_callable()
        except Exception as e:
            self._logger.exception(
                f"[DeferredOrderQueue] 보류 재시도 중 예외: code={stock_code} side={side} err={e}"
            )

    def pending_count(self) -> int:
        return len(self._entries)

    def has_pending(self, stock_code: str, side: str) -> bool:
        return (str(stock_code), str(side)) in self._entries
