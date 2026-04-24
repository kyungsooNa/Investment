from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from datetime import datetime, timezone
from typing import Generator, Optional
from uuid import uuid4


_trace_id_var: ContextVar[str] = ContextVar("trace_id", default="")


def new_trace_id(strategy_name: str = "") -> str:
    prefix = (strategy_name[:6].upper() if strategy_name else "TRACE").replace(" ", "_")
    ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    suffix = uuid4().hex[:8]
    return f"{prefix}-{ts}-{suffix}"


def get_trace_id() -> Optional[str]:
    value = _trace_id_var.get()
    return value or None


@contextmanager
def trace_scope(trace_id: str) -> Generator[None, None, None]:
    """현재 블록에서만 trace_id 를 활성화하고, 빠져나가면 이전 값으로 원복."""
    if not trace_id:
        yield
        return
    token: Token = _trace_id_var.set(trace_id)
    try:
        yield
    finally:
        _trace_id_var.reset(token)
