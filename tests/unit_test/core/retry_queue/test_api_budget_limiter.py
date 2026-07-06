import asyncio

import pytest

from core.api_priority import PRIORITY_EMERGENCY
from core.retry_queue.api_budget_limiter import (
    DEFAULT_API_BUDGET_LIMITS,
    DEFAULT_API_EMERGENCY_LIMITS,
    DEFAULT_API_EMERGENCY_RATE_LIMITS_PER_SEC,
    DEFAULT_API_EMERGENCY_GLOBAL_RATE_LIMIT_PER_SEC,
    DEFAULT_API_GLOBAL_RATE_LIMIT_PER_SEC,
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
        "order_submit": 1,
        "order_cancel": 1,
        "websocket_connect": 1,
        "websocket_subscribe": 1,
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
        "order_submit": 2.0,
        "order_cancel": 2.0,
        "websocket_connect": 1.0,
        "websocket_subscribe": 5.0,
        "default": 8.0,
    }
    assert DEFAULT_API_GLOBAL_RATE_LIMIT_PER_SEC == 8.0
    assert DEFAULT_API_EMERGENCY_GLOBAL_RATE_LIMIT_PER_SEC == 2.0


def test_limiter_snapshot_exposes_rate_limits():
    limiter = ApiBudgetLimiter()

    snapshot = limiter.snapshot()

    assert snapshot["_global"]["rate_limit_per_sec"] == 8.0
    assert snapshot["_global"]["emergency"]["rate_limit_per_sec"] == 2.0
    assert snapshot["quotation_price"]["rate_limit_per_sec"] == 8.0
    assert snapshot["account_reconciliation"]["rate_limit_per_sec"] == 2.0
    assert snapshot["quotation_price"]["rate_wait_total"] == 0
    assert snapshot["quotation_price"]["rate_wait_seconds_total"] == 0.0


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
        global_rate_limit_per_sec=float("inf"),
        emergency_global_rate_limit_per_sec=float("inf"),
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


async def test_rate_limiter_snapshot_tracks_preemptive_throttle_waits():
    sleeps = []
    now = 100.0

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    limiter = ApiBudgetLimiter(
        {"order_submit": 2},
        rate_limits_per_sec={"order_submit": 2.0},
        global_rate_limit_per_sec=float("inf"),
        emergency_global_rate_limit_per_sec=float("inf"),
        monotonic=lambda: now,
        sleep=fake_sleep,
    )

    async with limiter.acquire("order_submit"):
        pass
    async with limiter.acquire("order_submit"):
        pass
    async with limiter.acquire("order_submit"):
        pass

    snapshot = limiter.snapshot()
    assert sleeps == [0.5, 1.0]
    assert snapshot["order_submit"]["rate_wait_total"] == 2
    assert snapshot["order_submit"]["rate_wait_seconds_total"] == 1.5


async def test_global_rate_limiter_caps_total_rps_across_categories():
    """카테고리가 달라도 normal lane 전체 합산 RPS 는 global bucket 으로 제한한다."""
    sleeps: list[float] = []
    now = 100.0

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    limiter = ApiBudgetLimiter(
        {
            "quotation_price": 4,
            "quotation_ohlcv": 4,
            "account_balance": 4,
        },
        rate_limits_per_sec={
            "quotation_price": 100.0,
            "quotation_ohlcv": 100.0,
            "account_balance": 100.0,
        },
        global_rate_limit_per_sec=2.0,
        monotonic=lambda: now,
        sleep=fake_sleep,
    )

    async with limiter.acquire("quotation_price"):
        pass
    async with limiter.acquire("quotation_ohlcv"):
        pass
    async with limiter.acquire("account_balance"):
        pass

    snapshot = limiter.snapshot()
    assert sleeps == [0.5, 1.0]
    assert snapshot["_global"]["rate_wait_total"] == 2
    assert snapshot["_global"]["rate_wait_seconds_total"] == 1.5


async def test_emergency_global_rate_bucket_is_independent_from_normal_global_bucket():
    """긴급 주문은 category lane 뿐 아니라 global rate bucket 도 normal 과 분리한다."""
    sleeps: list[float] = []
    now = 100.0

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    limiter = ApiBudgetLimiter(
        {"order_submit": 4},
        rate_limits_per_sec={"order_submit": float("inf")},
        emergency_limits={"order_submit": 4},
        emergency_rate_limits_per_sec={"order_submit": float("inf")},
        global_rate_limit_per_sec=2.0,
        emergency_global_rate_limit_per_sec=2.0,
        monotonic=lambda: now,
        sleep=fake_sleep,
    )

    async with limiter.acquire("order_submit"):
        pass
    async with limiter.acquire("order_submit"):
        pass
    async with limiter.acquire("order_submit", priority=PRIORITY_EMERGENCY):
        pass
    async with limiter.acquire("order_submit", priority=PRIORITY_EMERGENCY):
        pass

    snapshot = limiter.snapshot()
    assert sleeps == [0.5, 0.5]
    assert snapshot["_global"]["rate_wait_total"] == 1
    assert snapshot["_global"]["emergency"]["rate_wait_total"] == 1


@pytest.mark.real_sleep
async def test_limiter_tracks_semaphore_wait_seconds_when_concurrency_limit_saturated():
    """rate 대기와 별개로, concurrency semaphore 대기(동시성 한도 포화)도 누적 계측한다."""
    limiter = ApiBudgetLimiter(
        {"quotation_price": 1},
        rate_limits_per_sec={"quotation_price": 100.0},
        global_rate_limit_per_sec=100.0,
        emergency_global_rate_limit_per_sec=100.0,
    )
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    second_started = asyncio.Event()
    second_waiting = asyncio.Event()

    async def run_first():
        async with limiter.acquire("quotation_price"):
            first_started.set()
            await release_first.wait()

    async def run_second():
        second_waiting.set()
        async with limiter.acquire("quotation_price"):
            second_started.set()

    first_task = asyncio.create_task(run_first())
    await asyncio.wait_for(first_started.wait(), timeout=1)

    second_task = asyncio.create_task(run_second())
    await asyncio.wait_for(second_waiting.wait(), timeout=1)
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(second_started.wait(), timeout=0.05)

    release_first.set()
    await asyncio.wait_for(second_started.wait(), timeout=1)
    await asyncio.gather(first_task, second_task)

    snapshot = limiter.snapshot()
    assert snapshot["quotation_price"]["semaphore_wait_total"] == 1
    assert snapshot["quotation_price"]["semaphore_wait_seconds_total"] > 0.0


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


@pytest.mark.real_sleep
async def test_opening_burst_load_keeps_order_reconcile_and_emergency_lanes_available():
    """장초반 조회 burst 중에도 reconcile/order/emergency lane 은 서로 독립적으로 진입한다."""
    limiter = ApiBudgetLimiter(
        {
            "quotation_price": 1,
            "quotation_conclusion": 1,
            "account_reconciliation": 1,
            "order_submit": 1,
        },
        rate_limits_per_sec={
            "quotation_price": 100.0,
            "quotation_conclusion": 100.0,
            "account_reconciliation": 100.0,
            "order_submit": 100.0,
        },
        emergency_limits={"order_submit": 1},
        emergency_rate_limits_per_sec={"order_submit": 100.0},
    )
    release_quote = asyncio.Event()
    release_order = asyncio.Event()
    quote_started = asyncio.Event()
    order_started = asyncio.Event()
    blocked_quote_started = asyncio.Event()
    blocked_order_started = asyncio.Event()
    conclusion_started = asyncio.Event()
    reconcile_started = asyncio.Event()
    emergency_started = asyncio.Event()

    async def hold(category, started, release, *, priority=None):
        kwargs = {"priority": priority} if priority is not None else {}
        async with limiter.acquire(category, **kwargs):
            started.set()
            await release.wait()

    async def quick(category, started, *, priority=None):
        kwargs = {"priority": priority} if priority is not None else {}
        async with limiter.acquire(category, **kwargs):
            started.set()

    quote_task = asyncio.create_task(hold("quotation_price", quote_started, release_quote))
    order_task = asyncio.create_task(hold("order_submit", order_started, release_order))
    await asyncio.wait_for(quote_started.wait(), timeout=1)
    await asyncio.wait_for(order_started.wait(), timeout=1)

    blocked_quote_task = asyncio.create_task(quick("quotation_price", blocked_quote_started))
    blocked_order_task = asyncio.create_task(quick("order_submit", blocked_order_started))
    conclusion_task = asyncio.create_task(quick("quotation_conclusion", conclusion_started))
    reconcile_task = asyncio.create_task(quick("account_reconciliation", reconcile_started))
    emergency_task = asyncio.create_task(
        quick("order_submit", emergency_started, priority=PRIORITY_EMERGENCY)
    )

    await asyncio.wait_for(conclusion_started.wait(), timeout=1)
    await asyncio.wait_for(reconcile_started.wait(), timeout=1)
    await asyncio.wait_for(emergency_started.wait(), timeout=1)
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(blocked_quote_started.wait(), timeout=0.05)
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(blocked_order_started.wait(), timeout=0.05)

    release_quote.set()
    release_order.set()
    await asyncio.gather(
        quote_task,
        order_task,
        blocked_quote_task,
        blocked_order_task,
        conclusion_task,
        reconcile_task,
        emergency_task,
    )


# --- Emergency priority lane 테스트 ---


def test_default_emergency_lane_covers_order_categories_only():
    """emergency lane 기본 적용 대상은 주문/취소 카테고리로 한정한다."""
    assert DEFAULT_API_EMERGENCY_LIMITS == {
        "order_submit": 1,
        "order_cancel": 1,
    }
    assert DEFAULT_API_EMERGENCY_RATE_LIMITS_PER_SEC == {
        "order_submit": 2.0,
        "order_cancel": 2.0,
    }


def test_snapshot_includes_emergency_lane_when_configured():
    limiter = ApiBudgetLimiter()

    snapshot = limiter.snapshot()

    assert "emergency" in snapshot["order_submit"]
    assert snapshot["order_submit"]["emergency"]["limit"] == 1
    assert snapshot["order_submit"]["emergency"]["rate_limit_per_sec"] == 2.0


def test_snapshot_omits_emergency_lane_when_not_configured():
    limiter = ApiBudgetLimiter()

    snapshot = limiter.snapshot()

    # quotation 계열은 emergency lane 미정의 → snapshot 에 키 없음
    assert "emergency" not in snapshot["quotation_price"]
    assert "emergency" not in snapshot["account_balance"]


@pytest.mark.real_sleep
async def test_emergency_priority_uses_separate_lane_when_normal_lane_is_busy():
    """normal lane 이 점유돼도 emergency priority 호출은 별도 lane 으로 진입한다."""
    limiter = ApiBudgetLimiter(
        {"order_submit": 1},
        rate_limits_per_sec={"order_submit": 100.0},
        emergency_limits={"order_submit": 1},
        emergency_rate_limits_per_sec={"order_submit": 100.0},
    )
    normal_started = asyncio.Event()
    release_normal = asyncio.Event()
    emergency_started = asyncio.Event()

    async def hold_normal():
        async with limiter.acquire("order_submit"):
            normal_started.set()
            await release_normal.wait()

    async def emergency_call():
        async with limiter.acquire("order_submit", priority=PRIORITY_EMERGENCY):
            emergency_started.set()

    normal_task = asyncio.create_task(hold_normal())
    await asyncio.wait_for(normal_started.wait(), timeout=1)

    emergency_task = asyncio.create_task(emergency_call())
    # emergency lane 이 독립이므로 즉시 진입 가능해야 한다
    await asyncio.wait_for(emergency_started.wait(), timeout=1)

    release_normal.set()
    await asyncio.gather(normal_task, emergency_task)


@pytest.mark.real_sleep
async def test_emergency_priority_falls_back_to_normal_lane_when_emergency_undefined():
    """emergency lane 이 정의되지 않은 카테고리에서는 normal lane 으로 fallback."""
    limiter = ApiBudgetLimiter(
        {"quotation_price": 1},
        rate_limits_per_sec={"quotation_price": 100.0},
        emergency_limits={},  # emergency lane 없음
    )
    normal_started = asyncio.Event()
    release_normal = asyncio.Event()
    emergency_started = asyncio.Event()

    async def hold_normal():
        async with limiter.acquire("quotation_price"):
            normal_started.set()
            await release_normal.wait()

    async def emergency_call():
        async with limiter.acquire("quotation_price", priority=PRIORITY_EMERGENCY):
            emergency_started.set()

    normal_task = asyncio.create_task(hold_normal())
    await asyncio.wait_for(normal_started.wait(), timeout=1)

    emergency_task = asyncio.create_task(emergency_call())
    # emergency lane 미정의 → normal lane 점유로 차단되어야 한다
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(emergency_started.wait(), timeout=0.05)

    release_normal.set()
    await asyncio.wait_for(emergency_started.wait(), timeout=1)
    await asyncio.gather(normal_task, emergency_task)


@pytest.mark.real_sleep
async def test_emergency_lane_acquired_total_tracked_separately_in_snapshot():
    limiter = ApiBudgetLimiter()

    async with limiter.acquire("order_submit"):
        pass
    async with limiter.acquire("order_submit", priority=PRIORITY_EMERGENCY):
        pass
    async with limiter.acquire("order_submit", priority=PRIORITY_EMERGENCY):
        pass

    snapshot = limiter.snapshot()
    assert snapshot["order_submit"]["acquired_total"] == 1
    assert snapshot["order_submit"]["emergency"]["acquired_total"] == 2


async def test_emergency_rate_bucket_is_independent_of_normal_rate_bucket():
    """emergency lane 의 rate bucket 은 normal lane 과 독립적으로 스케줄된다."""
    sleeps: list[float] = []
    now = 100.0

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    limiter = ApiBudgetLimiter(
        {"order_submit": 4},
        rate_limits_per_sec={"order_submit": 2.0},
        emergency_limits={"order_submit": 4},
        emergency_rate_limits_per_sec={"order_submit": 2.0},
        global_rate_limit_per_sec=float("inf"),
        emergency_global_rate_limit_per_sec=float("inf"),
        monotonic=lambda: now,
        sleep=fake_sleep,
    )

    # normal lane 1회 → 다음 normal 호출은 0.5초 대기
    async with limiter.acquire("order_submit"):
        pass
    async with limiter.acquire("order_submit"):
        pass

    # 직후 emergency 호출 — emergency rate bucket 은 별도라 대기 없이 통과
    async with limiter.acquire("order_submit", priority=PRIORITY_EMERGENCY):
        pass
    async with limiter.acquire("order_submit", priority=PRIORITY_EMERGENCY):
        pass

    # 첫 호출 대기 0 + normal 두번째 0.5 + emergency 첫 0 + emergency 두번째 0.5
    assert sleeps == [0.5, 0.5]
