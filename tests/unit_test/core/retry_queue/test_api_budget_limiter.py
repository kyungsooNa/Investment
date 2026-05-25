import asyncio

import pytest

from core.retry_queue.api_budget_limiter import (
    DEFAULT_API_BUDGET_LIMITS,
    DEFAULT_API_RATE_LIMITS_PER_SEC,
    ApiBudgetLimiter,
)


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


def test_default_category_is_explicitly_configured():
    limiter = ApiBudgetLimiter()

    snapshot = limiter.snapshot()

    assert snapshot["default"]["limit"] == 4


def test_default_limits_include_endpoint_specific_real_operation_categories():
    assert DEFAULT_API_BUDGET_LIMITS == {
        "quotation_price": 4,
        "quotation_ohlcv": 2,
        "quotation_conclusion": 3,
        "quotation": 4,
        "account_balance": 1,
        "account_reconciliation": 1,
        "account": 1,
        "default": 4,
    }


def test_default_rate_limits_use_conservative_operation_defaults():
    assert DEFAULT_API_RATE_LIMITS_PER_SEC == {
        "quotation_price": 8.0,
        "quotation_ohlcv": 3.0,
        "quotation_conclusion": 5.0,
        "quotation": 8.0,
        "account_balance": 2.0,
        "account_reconciliation": 2.0,
        "account": 2.0,
        "default": 8.0,
    }


def test_limiter_snapshot_exposes_rate_limits():
    limiter = ApiBudgetLimiter()

    snapshot = limiter.snapshot()

    assert snapshot["quotation_price"]["rate_limit_per_sec"] == 8.0
    assert snapshot["account_reconciliation"]["rate_limit_per_sec"] == 2.0


async def test_none_category_uses_default_budget():
    limiter = ApiBudgetLimiter()

    async with limiter.acquire(None):
        snapshot = limiter.snapshot()

    assert snapshot["default"]["acquired_total"] == 1


async def test_rate_limiter_reserves_future_slots_without_busy_loop():
    sleeps = []
    now = 100.0

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    limiter = ApiBudgetLimiter(
        {"quotation_price": 4},
        rate_limits_per_sec={"quotation_price": 2.0},
        monotonic=lambda: now,
        sleep=fake_sleep,
    )

    async with limiter.acquire("quotation_price"):
        pass
    async with limiter.acquire("quotation_price"):
        pass
    async with limiter.acquire("quotation_price"):
        pass

    assert sleeps == [0.5, 1.0]


@pytest.mark.real_sleep
async def test_account_reconciliation_budget_is_independent_from_quotation_scan_budget():
    limiter = ApiBudgetLimiter(
        {"quotation_price": 1, "account_reconciliation": 1},
        rate_limits_per_sec={"quotation_price": 100.0, "account_reconciliation": 100.0},
    )
    quote_started = asyncio.Event()
    release_quote = asyncio.Event()
    blocked_quote_started = asyncio.Event()
    reconcile_started = asyncio.Event()

    async def hold_quote():
        async with limiter.acquire("quotation_price"):
            quote_started.set()
            await release_quote.wait()

    async def blocked_quote():
        async with limiter.acquire("quotation_price"):
            blocked_quote_started.set()

    async def reconcile():
        async with limiter.acquire("account_reconciliation"):
            reconcile_started.set()

    quote_task = asyncio.create_task(hold_quote())
    await asyncio.wait_for(quote_started.wait(), timeout=1)

    blocked_quote_task = asyncio.create_task(blocked_quote())
    reconcile_task = asyncio.create_task(reconcile())

    await asyncio.wait_for(reconcile_started.wait(), timeout=1)
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(blocked_quote_started.wait(), timeout=0.05)

    release_quote.set()
    await asyncio.gather(quote_task, blocked_quote_task, reconcile_task)


@pytest.mark.real_sleep
async def test_account_reconciliation_starts_during_quotation_burst_load():
    """quotation_price burst가 대기열을 만들더라도 account_reconciliation은 별도 budget으로 즉시 시작."""
    limiter = ApiBudgetLimiter(
        {"quotation_price": 1, "account_reconciliation": 1},
        rate_limits_per_sec={"quotation_price": 100.0, "account_reconciliation": 100.0},
    )
    first_quote_started = asyncio.Event()
    release_quote = asyncio.Event()
    blocked_quote_started = asyncio.Event()
    reconcile_started = asyncio.Event()

    async def hold_first_quote():
        async with limiter.acquire("quotation_price"):
            first_quote_started.set()
            await release_quote.wait()

    async def queued_quote():
        async with limiter.acquire("quotation_price"):
            blocked_quote_started.set()

    async def reconcile():
        async with limiter.acquire("account_reconciliation"):
            reconcile_started.set()

    first_task = asyncio.create_task(hold_first_quote())
    await asyncio.wait_for(first_quote_started.wait(), timeout=1)
    quote_tasks = [asyncio.create_task(queued_quote()) for _ in range(5)]
    reconcile_task = asyncio.create_task(reconcile())

    await asyncio.wait_for(reconcile_started.wait(), timeout=1)
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(blocked_quote_started.wait(), timeout=0.05)

    release_quote.set()
    await asyncio.gather(first_task, *quote_tasks, reconcile_task)
