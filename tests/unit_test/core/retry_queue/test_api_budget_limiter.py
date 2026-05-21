import asyncio

import pytest

from core.retry_queue.api_budget_limiter import ApiBudgetLimiter


@pytest.mark.real_sleep
async def test_limiter_serializes_requests_when_category_limit_is_one():
    limiter = ApiBudgetLimiter({"quotation": 1})
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    second_started = asyncio.Event()

    async def run_first():
        async with limiter.acquire("quotation"):
            first_started.set()
            await release_first.wait()

    async def run_second():
        async with limiter.acquire("quotation"):
            second_started.set()

    first_task = asyncio.create_task(run_first())
    await asyncio.wait_for(first_started.wait(), timeout=1)

    second_task = asyncio.create_task(run_second())
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(second_started.wait(), timeout=0.05)

    release_first.set()
    await asyncio.wait_for(second_started.wait(), timeout=1)
    await asyncio.gather(first_task, second_task)


def test_limiter_snapshot_exposes_configured_limits():
    limiter = ApiBudgetLimiter({"quotation": 3, "account": 1})

    snapshot = limiter.snapshot()

    assert snapshot["quotation"]["limit"] == 3
    assert snapshot["account"]["limit"] == 1
