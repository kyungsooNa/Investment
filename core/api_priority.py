"""Broker API 호출 우선순위 contextvar.

`OrderExecutionService.sell_all_stocks(EMERGENCY)` 같은 청산 경로가
일반 주문/조회 budget 점유와 분리된 별도 lane을 쓰도록, 호출 체인 전반의
시그니처를 변경하지 않고 ContextVar 로 priority 를 전파한다.

- "normal": 기본값. 기존 budget lane 사용.
- "emergency": ApiBudgetLimiter 가 카테고리별 emergency lane(있을 경우) 사용.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator


PRIORITY_NORMAL = "normal"
PRIORITY_EMERGENCY = "emergency"


_request_priority: ContextVar[str] = ContextVar("broker_api_request_priority", default=PRIORITY_NORMAL)


def current_priority() -> str:
    return _request_priority.get()


@contextmanager
def emergency_scope() -> Iterator[None]:
    """`with emergency_scope():` 블록 동안 priority 를 emergency 로 설정한다."""
    token = _request_priority.set(PRIORITY_EMERGENCY)
    try:
        yield
    finally:
        _request_priority.reset(token)


@contextmanager
def priority_scope(priority: str) -> Iterator[None]:
    token = _request_priority.set(priority)
    try:
        yield
    finally:
        _request_priority.reset(token)
