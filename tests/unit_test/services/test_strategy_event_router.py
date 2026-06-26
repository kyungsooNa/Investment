"""StrategyEventRouter 단위 테스트 (P2 2-4 PR-1).

라우터 책임:
- (strategy_name, code) subscribe/unsubscribe
- on_price_tick 시 매칭된 evaluator 동시 디스패치
- throttle (default 0.5s) / stale snapshot (default 5s) / market_open / kill_switch 게이트
- evaluator 예외는 흡수 (호출자 흐름 차단 금지)
"""
from __future__ import annotations

from typing import List, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from common.types import TradeSignal
from services.strategy_event_router import StrategyEventRouter


def _signal(code: str, strategy: str) -> TradeSignal:
    return TradeSignal(
        code=code,
        name=code,
        action="BUY",
        price=10000,
        qty=1,
        reason="test",
        strategy_name=strategy,
    )


def _market_clock(is_open: bool = True):
    mc = MagicMock()
    mc.is_market_open_now = AsyncMock(return_value=is_open)
    return mc


def _time_only_market_clock(is_open: bool = True):
    mc = MagicMock()
    mc.is_market_operating_hours = MagicMock(return_value=is_open)
    return mc


def _kill_switch(allowed: bool = True):
    ks = MagicMock()
    ks.check_strategies_allowed = AsyncMock(return_value=(allowed, "ok"))
    return ks


@pytest.mark.asyncio
async def test_subscribe_registers_strategy_for_code():
    router = StrategyEventRouter(market_clock=_market_clock())
    evaluator = AsyncMock(return_value=None)

    router.subscribe("005930", strategy_name="VBO", evaluator=evaluator)

    assert router.subscribers_for("005930") == ["VBO"]


@pytest.mark.asyncio
async def test_subscribe_same_strategy_replaces_evaluator_not_duplicates():
    router = StrategyEventRouter(market_clock=_market_clock())
    old_eval = AsyncMock(return_value=None)
    new_eval = AsyncMock(return_value=_signal("005930", "VBO"))

    router.subscribe("005930", strategy_name="VBO", evaluator=old_eval)
    router.subscribe("005930", strategy_name="VBO", evaluator=new_eval)

    assert router.subscribers_for("005930") == ["VBO"]
    results = await router.on_price_tick("005930", {"price": "10000"})
    assert len(results) == 1
    old_eval.assert_not_called()
    new_eval.assert_awaited_once()


@pytest.mark.asyncio
async def test_market_clock_without_calendar_method_uses_operating_hours():
    router = StrategyEventRouter(market_clock=_time_only_market_clock(is_open=True))
    evaluator = AsyncMock(return_value=_signal("005930", "VBO"))
    router.subscribe("005930", strategy_name="VBO", evaluator=evaluator)

    results = await router.on_price_tick("005930", {"price": "10000"})

    assert len(results) == 1
    evaluator.assert_awaited_once()


@pytest.mark.asyncio
async def test_unsubscribe_removes_strategy_and_clears_throttle():
    router = StrategyEventRouter(market_clock=_market_clock(), throttle_sec=0.5)
    evaluator = AsyncMock(return_value=_signal("005930", "VBO"))
    router.subscribe("005930", strategy_name="VBO", evaluator=evaluator)
    await router.on_price_tick("005930", {"price": "10000"}, now_ts=100.0)

    router.unsubscribe("005930", "VBO")
    router.subscribe("005930", strategy_name="VBO", evaluator=evaluator)

    # throttle state 가 해제되었으므로 즉시 dispatch 되어야 한다.
    results = await router.on_price_tick("005930", {"price": "10000"}, now_ts=100.1)
    assert len(results) == 1


@pytest.mark.asyncio
async def test_on_price_tick_unknown_code_returns_empty():
    router = StrategyEventRouter(market_clock=_market_clock())
    results = await router.on_price_tick("999999", {"price": "10000"})
    assert results == []


@pytest.mark.asyncio
async def test_on_price_tick_dispatches_evaluator_and_returns_signal():
    router = StrategyEventRouter(market_clock=_market_clock())
    sig = _signal("005930", "VBO")
    evaluator = AsyncMock(return_value=sig)
    router.subscribe("005930", strategy_name="VBO", evaluator=evaluator)

    results = await router.on_price_tick("005930", {"price": "10000"})

    evaluator.assert_awaited_once_with("005930", {"price": "10000"})
    assert results == [sig]


@pytest.mark.asyncio
async def test_throttle_blocks_repeated_dispatch_within_window():
    router = StrategyEventRouter(market_clock=_market_clock(), throttle_sec=0.5)
    evaluator = AsyncMock(return_value=_signal("005930", "VBO"))
    router.subscribe("005930", strategy_name="VBO", evaluator=evaluator)

    await router.on_price_tick("005930", {"price": "10000"}, now_ts=100.0)
    await router.on_price_tick("005930", {"price": "10000"}, now_ts=100.3)  # within throttle
    await router.on_price_tick("005930", {"price": "10000"}, now_ts=100.6)  # past throttle

    assert evaluator.await_count == 2


@pytest.mark.asyncio
async def test_stale_snapshot_blocks_dispatch():
    router = StrategyEventRouter(market_clock=_market_clock(), stale_snapshot_sec=5.0)
    evaluator = AsyncMock(return_value=_signal("005930", "VBO"))
    router.subscribe("005930", strategy_name="VBO", evaluator=evaluator)

    await router.on_price_tick(
        "005930",
        {"price": "10000"},
        snapshot_ts=100.0,
        now_ts=110.0,  # 10s old, stale
    )

    evaluator.assert_not_called()


@pytest.mark.asyncio
async def test_market_closed_blocks_dispatch():
    router = StrategyEventRouter(market_clock=_market_clock(is_open=False))
    evaluator = AsyncMock(return_value=_signal("005930", "VBO"))
    router.subscribe("005930", strategy_name="VBO", evaluator=evaluator)

    results = await router.on_price_tick("005930", {"price": "10000"})

    assert results == []
    evaluator.assert_not_called()


@pytest.mark.asyncio
async def test_kill_switch_tripped_blocks_dispatch():
    router = StrategyEventRouter(
        market_clock=_market_clock(),
        kill_switch_service=_kill_switch(allowed=False),
    )
    evaluator = AsyncMock(return_value=_signal("005930", "VBO"))
    router.subscribe("005930", strategy_name="VBO", evaluator=evaluator)

    results = await router.on_price_tick("005930", {"price": "10000"})

    assert results == []
    evaluator.assert_not_called()


@pytest.mark.asyncio
async def test_evaluator_exception_is_absorbed():
    router = StrategyEventRouter(market_clock=_market_clock())
    raising = AsyncMock(side_effect=RuntimeError("boom"))
    ok_eval = AsyncMock(return_value=_signal("005930", "OSB"))
    router.subscribe("005930", strategy_name="VBO", evaluator=raising)
    router.subscribe("005930", strategy_name="OSB", evaluator=ok_eval)

    results = await router.on_price_tick("005930", {"price": "10000"})

    # 한쪽이 raise 해도 다른 evaluator 결과는 반환되어야 한다.
    assert len(results) == 1
    assert results[0].strategy_name == "OSB"


@pytest.mark.asyncio
async def test_multiple_strategies_dispatched_for_same_code():
    router = StrategyEventRouter(market_clock=_market_clock())
    vbo_eval = AsyncMock(return_value=_signal("005930", "VBO"))
    osb_eval = AsyncMock(return_value=_signal("005930", "OSB"))
    router.subscribe("005930", strategy_name="VBO", evaluator=vbo_eval)
    router.subscribe("005930", strategy_name="OSB", evaluator=osb_eval)

    results = await router.on_price_tick("005930", {"price": "10000"})

    vbo_eval.assert_awaited_once()
    osb_eval.assert_awaited_once()
    names = sorted(s.strategy_name for s in results)
    assert names == ["OSB", "VBO"]


@pytest.mark.asyncio
async def test_throttle_is_per_strategy_code_pair():
    router = StrategyEventRouter(market_clock=_market_clock(), throttle_sec=0.5)
    vbo = AsyncMock(return_value=_signal("005930", "VBO"))
    osb = AsyncMock(return_value=_signal("005930", "OSB"))
    router.subscribe("005930", strategy_name="VBO", evaluator=vbo)
    router.subscribe("005930", strategy_name="OSB", evaluator=osb)

    # 첫 tick: 둘 다 디스패치
    await router.on_price_tick("005930", {"price": "10000"}, now_ts=100.0)
    # throttle 내 두 번째 tick: 둘 다 차단
    await router.on_price_tick("005930", {"price": "10000"}, now_ts=100.2)

    assert vbo.await_count == 1
    assert osb.await_count == 1


@pytest.mark.asyncio
async def test_evaluator_returning_none_yields_no_signal():
    router = StrategyEventRouter(market_clock=_market_clock())
    evaluator = AsyncMock(return_value=None)
    router.subscribe("005930", strategy_name="VBO", evaluator=evaluator)

    results = await router.on_price_tick("005930", {"price": "10000"})

    evaluator.assert_awaited_once()
    assert results == []


@pytest.mark.asyncio
async def test_subscribe_with_empty_args_is_no_op():
    router = StrategyEventRouter(market_clock=_market_clock())
    evaluator = AsyncMock(return_value=None)

    router.subscribe("", strategy_name="VBO", evaluator=evaluator)
    router.subscribe("005930", strategy_name="", evaluator=evaluator)
    router.subscribe("005930", strategy_name="VBO", evaluator=None)  # type: ignore[arg-type]

    assert router.subscribers_for("005930") == []
    assert router.subscribers_for("") == []


@pytest.mark.asyncio
async def test_router_without_market_clock_dispatches():
    """market_clock 미주입 시 게이트 없이 동작 (테스트/PR-1 minimal 시나리오)."""
    router = StrategyEventRouter(market_clock=None)
    evaluator = AsyncMock(return_value=_signal("005930", "VBO"))
    router.subscribe("005930", strategy_name="VBO", evaluator=evaluator)

    results = await router.on_price_tick("005930", {"price": "10000"})

    evaluator.assert_awaited_once()
    assert len(results) == 1


# === PR-3 선행: signal sink contract ===


@pytest.mark.asyncio
async def test_signal_sink_publishes_each_non_none_signal():
    """signal_sink 주입 시 non-None 평가 신호마다 publish 호출 + context 표준 키 검증."""
    sink = MagicMock()
    sink.publish = AsyncMock(return_value=None)

    router = StrategyEventRouter(market_clock=_market_clock(), signal_sink=sink)
    sig_a = _signal("005930", "VBO")
    sig_b = _signal("005930", "OSB")
    router.subscribe("005930", strategy_name="VBO", evaluator=AsyncMock(return_value=sig_a))
    router.subscribe("005930", strategy_name="OSB", evaluator=AsyncMock(return_value=sig_b))

    results = await router.on_price_tick(
        "005930", {"price": "10000"}, snapshot_ts=12345.0, now_ts=12345.0
    )

    assert len(results) == 2
    assert sink.publish.await_count == 2
    published_contexts = [kwargs["context"] for (_args, kwargs) in sink.publish.await_args_list]
    for ctx in published_contexts:
        assert ctx["signal_source"] == "event"
        assert ctx["code"] == "005930"
        assert ctx["snapshot_ts"] == 12345.0
        assert ctx["strategy_name"] in {"VBO", "OSB"}
    assert {c["strategy_name"] for c in published_contexts} == {"VBO", "OSB"}


@pytest.mark.asyncio
async def test_signal_sink_publish_exception_is_absorbed():
    """sink.publish 예외 시 다른 evaluator 흐름 보존 + 반환 List 정상."""
    sink = MagicMock()
    sink.publish = AsyncMock(side_effect=RuntimeError("sink boom"))

    router = StrategyEventRouter(market_clock=_market_clock(), signal_sink=sink)
    sig = _signal("005930", "VBO")
    router.subscribe("005930", strategy_name="VBO", evaluator=AsyncMock(return_value=sig))

    results = await router.on_price_tick("005930", {"price": "10000"})

    assert results == [sig]
    sink.publish.assert_awaited_once()


@pytest.mark.asyncio
async def test_signal_sink_not_called_for_none_signals():
    """evaluator 가 None 반환하면 publish 호출되지 않는다."""
    sink = MagicMock()
    sink.publish = AsyncMock(return_value=None)

    router = StrategyEventRouter(market_clock=_market_clock(), signal_sink=sink)
    router.subscribe("005930", strategy_name="VBO", evaluator=AsyncMock(return_value=None))

    results = await router.on_price_tick("005930", {"price": "10000"})

    assert results == []
    sink.publish.assert_not_awaited()


# === PR-3 선행: signal debounce (Q5) ===


@pytest.mark.asyncio
async def test_signal_debounce_blocks_duplicate_within_window():
    """signal_debounce_sec 활성 시 같은 (strategy, code) 두 번째 신호는 window 안에서 차단."""
    sink = MagicMock()
    sink.publish = AsyncMock(return_value=None)
    sig = _signal("005930", "VBO")

    router = StrategyEventRouter(
        market_clock=_market_clock(),
        signal_sink=sink,
        throttle_sec=0.0,  # evaluator throttle 비활성 (debounce 단독 검증)
        signal_debounce_sec=1.0,
    )
    router.subscribe(
        "005930", strategy_name="VBO", evaluator=AsyncMock(return_value=sig)
    )

    first = await router.on_price_tick("005930", {"price": "10000"}, now_ts=100.0)
    second = await router.on_price_tick("005930", {"price": "10000"}, now_ts=100.5)

    assert first == [sig]
    assert second == []
    sink.publish.assert_awaited_once()


@pytest.mark.asyncio
async def test_signal_debounce_allows_after_window():
    """debounce window 경과 후에는 같은 (strategy, code) 신호 재발행 허용."""
    sink = MagicMock()
    sink.publish = AsyncMock(return_value=None)
    sig = _signal("005930", "VBO")

    router = StrategyEventRouter(
        market_clock=_market_clock(),
        signal_sink=sink,
        throttle_sec=0.0,
        signal_debounce_sec=0.5,
    )
    router.subscribe(
        "005930", strategy_name="VBO", evaluator=AsyncMock(return_value=sig)
    )

    first = await router.on_price_tick("005930", {"price": "10000"}, now_ts=100.0)
    second = await router.on_price_tick("005930", {"price": "10000"}, now_ts=100.6)

    assert first == [sig]
    assert second == [sig]
    assert sink.publish.await_count == 2


@pytest.mark.asyncio
async def test_signal_debounce_disabled_when_none():
    """signal_debounce_sec=None(기본) 이면 모든 신호 통과 — backward compat 검증."""
    sink = MagicMock()
    sink.publish = AsyncMock(return_value=None)
    sig = _signal("005930", "VBO")

    router = StrategyEventRouter(
        market_clock=_market_clock(),
        signal_sink=sink,
        throttle_sec=0.0,
        signal_debounce_sec=None,
    )
    router.subscribe(
        "005930", strategy_name="VBO", evaluator=AsyncMock(return_value=sig)
    )

    first = await router.on_price_tick("005930", {"price": "10000"}, now_ts=100.0)
    second = await router.on_price_tick("005930", {"price": "10000"}, now_ts=100.001)

    assert first == [sig]
    assert second == [sig]
    assert sink.publish.await_count == 2


@pytest.mark.asyncio
async def test_unsubscribe_clears_debounce_state():
    """unsubscribe 후 재구독 시 debounce 상태가 초기화돼 즉시 발행 허용."""
    sig = _signal("005930", "VBO")
    evaluator = AsyncMock(return_value=sig)

    router = StrategyEventRouter(
        market_clock=_market_clock(),
        throttle_sec=0.0,
        signal_debounce_sec=10.0,
    )
    router.subscribe("005930", strategy_name="VBO", evaluator=evaluator)

    first = await router.on_price_tick("005930", {"price": "10000"}, now_ts=100.0)
    assert first == [sig]

    router.unsubscribe("005930", "VBO")
    router.subscribe("005930", strategy_name="VBO", evaluator=evaluator)

    second = await router.on_price_tick("005930", {"price": "10000"}, now_ts=100.1)
    assert second == [sig]


# === PR-3 선행: trigger crossing tick — throttle/debounce 분리 회귀 (Q5) ===
#
# 운영 정책: throttle_sec=0.1 (evaluator burst 흡수), signal_debounce_sec=0.5
# (중복 신호 publish 차단). crossing tick (prev_price < trigger <= current_price)
# 이 evaluator throttle window 에 막히지 않아야 한다는 설계 결정을
# 시나리오 단위로 잠근다. (todo_list.md P2 2-4 PR-3 선행 남은 확인)


def _make_threshold_evaluator(trigger: int, signal: TradeSignal):
    """price >= trigger 일 때만 signal 을 반환하는 evaluator + 호출 카운터."""
    state = {"calls": 0}

    async def _evaluator(code: str, snapshot: dict):
        state["calls"] += 1
        return signal if int(snapshot["price"]) >= trigger else None

    return _evaluator, state


@pytest.mark.asyncio
async def test_crossing_tick_evaluated_under_operational_throttle_split():
    """운영 정책(throttle=0.1, debounce=0.5)에서 throttle 경과 후 도착한
    crossing tick 이 evaluator 평가까지 진행되어 신호를 발행한다."""
    sink = MagicMock()
    sink.publish = AsyncMock(return_value=None)
    sig = _signal("005930", "VBO")
    evaluator, state = _make_threshold_evaluator(trigger=10000, signal=sig)

    router = StrategyEventRouter(
        market_clock=_market_clock(),
        signal_sink=sink,
        throttle_sec=0.1,
        signal_debounce_sec=0.5,
    )
    router.subscribe("005930", strategy_name="VBO", evaluator=evaluator)

    # t=100.00: trigger 미달 — evaluator 평가만, 신호 없음
    r1 = await router.on_price_tick("005930", {"price": "9999"}, now_ts=100.00)
    # t=100.15: throttle 0.1 경과 후 crossing tick — 평가되어 신호 발행
    r2 = await router.on_price_tick("005930", {"price": "10001"}, now_ts=100.15)

    assert state["calls"] == 2, "throttle 경과 후 crossing tick 도 evaluator 평가 대상"
    assert r1 == []
    assert r2 == [sig]
    sink.publish.assert_awaited_once()


@pytest.mark.asyncio
async def test_crossing_tick_blocked_when_legacy_single_throttle_covers_window():
    """레거시 단일 throttle(0.5)에서는 0.2초 뒤의 crossing tick 이 throttle 에
    막혀 evaluator 가 평가되지 않는다 — throttle/debounce 분리 결정의 근거 회귀."""
    sig = _signal("005930", "VBO")
    evaluator, state = _make_threshold_evaluator(trigger=10000, signal=sig)

    router = StrategyEventRouter(
        market_clock=_market_clock(),
        throttle_sec=0.5,
        signal_debounce_sec=None,
    )
    router.subscribe("005930", strategy_name="VBO", evaluator=evaluator)

    r1 = await router.on_price_tick("005930", {"price": "9999"}, now_ts=100.00)
    # throttle window(0.5) 내 crossing tick — evaluator 자체가 차단됨
    r2 = await router.on_price_tick("005930", {"price": "10001"}, now_ts=100.20)

    assert state["calls"] == 1, "throttle window 안의 crossing tick 은 평가되지 않음"
    assert r1 == []
    assert r2 == []


@pytest.mark.asyncio
async def test_continuous_crossing_ticks_evaluated_but_signal_publish_debounced():
    """crossing 이후 trigger 위에서 연속 tick 이 도착해도 evaluator 는 throttle 만
    통과하면 매번 평가되며, signal_debounce_sec 가 중복 신호 publish 만 차단한다."""
    sink = MagicMock()
    sink.publish = AsyncMock(return_value=None)
    sig = _signal("005930", "VBO")
    evaluator, state = _make_threshold_evaluator(trigger=10000, signal=sig)

    router = StrategyEventRouter(
        market_clock=_market_clock(),
        signal_sink=sink,
        throttle_sec=0.1,
        signal_debounce_sec=0.5,
    )
    router.subscribe("005930", strategy_name="VBO", evaluator=evaluator)

    # 5 ticks @ 0.15s 간격: throttle(0.1) 매번 통과, debounce(0.5) 는 첫·마지막만 publish
    timestamps = [100.00, 100.15, 100.30, 100.45, 100.60]
    results: List[List[TradeSignal]] = []
    for ts in timestamps:
        results.append(
            await router.on_price_tick("005930", {"price": "10001"}, now_ts=ts)
        )

    assert state["calls"] == 5, "throttle 간격(0.15s)이 throttle_sec(0.1) 초과 → 매번 평가"
    published = [r for r in results if r]
    # t=100.00 (init publish) + t=100.60 (gap 0.60 >= debounce 0.5 → 재발행)
    assert len(published) == 2
    assert sink.publish.await_count == 2
