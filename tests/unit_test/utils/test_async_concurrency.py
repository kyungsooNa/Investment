"""utils.async_concurrency 단위 테스트."""
from __future__ import annotations

import asyncio
from typing import List

import pytest

from utils.async_concurrency import bounded_gather


async def _make_coro(value: int, delay: float = 0.0) -> int:
    if delay:
        await asyncio.sleep(delay)
    return value


async def _make_failing_coro() -> int:
    raise RuntimeError("boom")


@pytest.mark.asyncio
async def test_bounded_gather_empty_returns_empty_list():
    result = await bounded_gather([], limit=5)
    assert result == []


@pytest.mark.asyncio
async def test_bounded_gather_preserves_order():
    coros = [_make_coro(i, delay=0.005 * ((5 - i) % 3)) for i in range(5)]
    result = await bounded_gather(coros, limit=3)
    assert result == [0, 1, 2, 3, 4]


@pytest.mark.asyncio
async def test_bounded_gather_limit_greater_than_input_works_like_plain_gather():
    coros = [_make_coro(i) for i in range(3)]
    result = await bounded_gather(coros, limit=100)
    assert result == [0, 1, 2]


@pytest.mark.asyncio
async def test_bounded_gather_propagates_exception_when_not_returning():
    async def _ok() -> int:
        return 1

    coros = [_ok(), _make_failing_coro(), _ok()]
    with pytest.raises(RuntimeError, match="boom"):
        await bounded_gather(coros, limit=2)


@pytest.mark.asyncio
async def test_bounded_gather_return_exceptions_true_returns_in_place():
    async def _ok(v: int) -> int:
        return v

    coros = [_ok(1), _make_failing_coro(), _ok(3)]
    result = await bounded_gather(coros, limit=2, return_exceptions=True)
    assert result[0] == 1
    assert isinstance(result[1], RuntimeError)
    assert result[2] == 3


@pytest.mark.asyncio
async def test_bounded_gather_respects_concurrency_limit():
    in_flight = 0
    peak = 0
    lock = asyncio.Lock()

    async def _tracked() -> int:
        nonlocal in_flight, peak
        async with lock:
            in_flight += 1
            peak = max(peak, in_flight)
        try:
            await asyncio.sleep(0.01)
            return in_flight
        finally:
            async with lock:
                in_flight -= 1

    coros = [_tracked() for _ in range(12)]
    await bounded_gather(coros, limit=3)
    assert peak <= 3
    assert peak >= 1


@pytest.mark.asyncio
async def test_bounded_gather_rejects_non_positive_limit():
    with pytest.raises(ValueError):
        await bounded_gather([_make_coro(1)], limit=0)
    with pytest.raises(ValueError):
        await bounded_gather([_make_coro(1)], limit=-1)
