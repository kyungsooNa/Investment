import asyncio
from datetime import datetime

import pytest
import pytz

from services.ai_usage_limiter import AiUsageLimitExceeded, AiUsageLimiter


PACIFIC = pytz.timezone("America/Los_Angeles")


async def test_interactive_requests_cannot_consume_disclosure_reserve(tmp_path):
    limiter = AiUsageLimiter(
        db_path=tmp_path / "usage.db",
        daily_request_limit=3,
        disclosure_reserve=1,
        now_provider=lambda: PACIFIC.localize(datetime(2026, 7, 19, 10, 0)),
    )

    await limiter.reserve("stock")
    await limiter.reserve("ranking")

    with pytest.raises(AiUsageLimitExceeded) as exc_info:
        await limiter.reserve("stock")

    assert exc_info.value.limit_kind == "interactive"
    await limiter.reserve("disclosure")
    with pytest.raises(AiUsageLimitExceeded) as total_exc:
        await limiter.reserve("disclosure")
    assert total_exc.value.limit_kind == "daily"

    snapshot = await limiter.get_snapshot()
    assert snapshot["used"] == 3
    assert snapshot["interactive_used"] == 2
    assert snapshot["disclosure_used"] == 1
    assert snapshot["daily_limit"] == 3
    assert snapshot["interactive_limit"] == 2


async def test_usage_persists_across_instances_and_resets_on_pacific_date(tmp_path):
    db_path = tmp_path / "usage.db"
    current = [PACIFIC.localize(datetime(2026, 7, 19, 23, 59))]
    first = AiUsageLimiter(
        db_path=db_path,
        daily_request_limit=2,
        disclosure_reserve=0,
        now_provider=lambda: current[0],
    )
    await first.reserve("stock")

    second = AiUsageLimiter(
        db_path=db_path,
        daily_request_limit=2,
        disclosure_reserve=0,
        now_provider=lambda: current[0],
    )
    assert (await second.get_snapshot())["used"] == 1

    current[0] = PACIFIC.localize(datetime(2026, 7, 20, 0, 1))
    snapshot = await second.get_snapshot()
    assert snapshot["used"] == 0
    assert snapshot["period_key"] == "2026-07-20"


async def test_concurrent_reservations_do_not_exceed_limit(tmp_path):
    limiter = AiUsageLimiter(
        db_path=tmp_path / "usage.db",
        daily_request_limit=5,
        disclosure_reserve=0,
        now_provider=lambda: PACIFIC.localize(datetime(2026, 7, 19, 10, 0)),
    )

    results = await asyncio.gather(
        *(limiter.reserve("stock") for _ in range(10)),
        return_exceptions=True,
    )

    assert sum(result is None for result in results) == 5
    assert sum(isinstance(result, AiUsageLimitExceeded) for result in results) == 5
    assert (await limiter.get_snapshot())["used"] == 5


async def test_zero_daily_limit_disables_local_blocking(tmp_path):
    limiter = AiUsageLimiter(
        db_path=tmp_path / "usage.db",
        daily_request_limit=0,
        disclosure_reserve=0,
    )

    for _ in range(3):
        await limiter.reserve("stock")

    snapshot = await limiter.get_snapshot()
    assert snapshot["enabled"] is False
    assert snapshot["daily_limit"] == 0


async def test_snapshot_reports_usage_broken_down_by_type(tmp_path):
    """어떤 기능이 한도를 소비하는지 보려면 usage_type 별 내역이 필요하다."""
    limiter = AiUsageLimiter(
        db_path=tmp_path / "usage.db",
        daily_request_limit=100,
        disclosure_reserve=20,
        now_provider=lambda: PACIFIC.localize(datetime(2026, 7, 20, 10, 0)),
    )

    await limiter.reserve("stock")
    await limiter.reserve("news")
    await limiter.reserve("news")
    await limiter.reserve("disclosure")

    snapshot = await limiter.get_snapshot()

    assert snapshot["by_type"] == {"stock": 1, "news": 2, "disclosure": 1}
    assert snapshot["used"] == 4
    assert snapshot["interactive_used"] == 3


async def test_snapshot_by_type_is_empty_when_unused(tmp_path):
    limiter = AiUsageLimiter(db_path=tmp_path / "usage.db", daily_request_limit=10)

    snapshot = await limiter.get_snapshot()

    assert snapshot["by_type"] == {}
    assert snapshot["used"] == 0


async def test_disabled_snapshot_still_exposes_by_type_key(tmp_path):
    limiter = AiUsageLimiter(db_path=tmp_path / "usage.db", daily_request_limit=0)

    snapshot = await limiter.get_snapshot()

    assert snapshot["enabled"] is False
    assert snapshot["by_type"] == {}
