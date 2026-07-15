from datetime import datetime

import pytest

from repositories.theme_trading_value_snapshot_repository import (
    ThemeTradingValueSnapshotRepository,
)


@pytest.mark.asyncio
async def test_saves_and_reads_latest_value_at_or_before_target(tmp_path):
    repo = ThemeTradingValueSnapshotRepository(tmp_path / "theme_snapshots.db")

    await repo.save_snapshot(datetime(2026, 7, 15, 10, 0), {"A": 100, "B": 200})
    await repo.save_snapshot(datetime(2026, 7, 15, 10, 1), {"A": 130})
    await repo.save_snapshot(datetime(2026, 7, 15, 10, 3), {"A": 190, "B": 260})

    values = await repo.get_values_at_or_before(
        datetime(2026, 7, 15, 10, 2), ["A", "B", "C"]
    )

    assert values == {"A": 130, "B": 200}


@pytest.mark.asyncio
async def test_does_not_use_previous_trading_day_as_three_minute_baseline(tmp_path):
    repo = ThemeTradingValueSnapshotRepository(tmp_path / "theme_snapshots.db")
    await repo.save_snapshot(datetime(2026, 7, 14, 15, 30), {"A": 9_000})

    values = await repo.get_values_at_or_before(datetime(2026, 7, 15, 9, 0), ["A"])

    assert values == {}
