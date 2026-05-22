"""SignalSink / NullSignalSink 단위 테스트 (P2 2-4 PR-3 선행)."""
from __future__ import annotations

import inspect

import pytest

from common.types import TradeSignal
from services.strategy_signal_sink import NullSignalSink, SignalSink


def _signal() -> TradeSignal:
    return TradeSignal(
        code="005930",
        name="005930",
        action="BUY",
        price=10000,
        qty=1,
        reason="test",
        strategy_name="VBO",
    )


@pytest.mark.asyncio
async def test_null_signal_sink_publish_returns_none():
    sink = NullSignalSink()
    result = await sink.publish(
        _signal(),
        context={"signal_source": "event", "strategy_name": "VBO", "code": "005930", "snapshot_ts": 0.0},
    )
    assert result is None


def test_null_signal_sink_satisfies_protocol():
    sink = NullSignalSink()
    assert isinstance(sink, SignalSink)
    assert hasattr(sink, "publish")
    assert inspect.iscoroutinefunction(sink.publish)
