import asyncio

import pytest

from core.api_priority import (
    PRIORITY_EMERGENCY,
    PRIORITY_NORMAL,
    current_priority,
    emergency_scope,
    priority_scope,
)


def test_default_priority_is_normal():
    assert current_priority() == PRIORITY_NORMAL


def test_emergency_scope_sets_priority_inside_block():
    assert current_priority() == PRIORITY_NORMAL
    with emergency_scope():
        assert current_priority() == PRIORITY_EMERGENCY
    assert current_priority() == PRIORITY_NORMAL


def test_emergency_scope_restores_priority_on_exception():
    with pytest.raises(RuntimeError):
        with emergency_scope():
            assert current_priority() == PRIORITY_EMERGENCY
            raise RuntimeError("boom")
    assert current_priority() == PRIORITY_NORMAL


def test_priority_scope_accepts_arbitrary_value_and_restores():
    with priority_scope("custom"):
        assert current_priority() == "custom"
    assert current_priority() == PRIORITY_NORMAL


def test_nested_emergency_scope_preserves_emergency_in_outer():
    with emergency_scope():
        assert current_priority() == PRIORITY_EMERGENCY
        with emergency_scope():
            assert current_priority() == PRIORITY_EMERGENCY
        assert current_priority() == PRIORITY_EMERGENCY
    assert current_priority() == PRIORITY_NORMAL


@pytest.mark.asyncio
async def test_emergency_scope_propagates_to_gather_tasks():
    captured: list[str] = []

    async def record():
        captured.append(current_priority())

    with emergency_scope():
        await asyncio.gather(record(), record(), record())

    assert captured == [PRIORITY_EMERGENCY] * 3
    assert current_priority() == PRIORITY_NORMAL


@pytest.mark.asyncio
async def test_emergency_scope_does_not_leak_to_unrelated_tasks():
    captured: list[str] = []

    async def record_after_yield():
        await asyncio.sleep(0)
        captured.append(current_priority())

    # 외부 task — emergency_scope 밖에서 생성 → normal priority 상속
    outside_task = asyncio.create_task(record_after_yield())

    with emergency_scope():
        # 내부 task — emergency_scope 안에서 생성 → emergency priority 상속
        inside_task = asyncio.create_task(record_after_yield())
        await inside_task

    await outside_task

    assert PRIORITY_EMERGENCY in captured
    assert PRIORITY_NORMAL in captured
