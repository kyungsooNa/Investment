"""ThemeClassificationTask 단위 테스트 (주기 가드 + force_run)."""
import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

from task.background.after_market.theme_classification_task import ThemeClassificationTask


def _make_task(collector, repo, refresh_interval_days=7):
    return ThemeClassificationTask(
        collector_service=collector,
        classification_repository=repo,
        market_calendar_service=MagicMock(),
        market_clock=MagicMock(),
        logger=MagicMock(),
        refresh_interval_days=refresh_interval_days,
    )


@pytest.mark.asyncio
async def test_collects_when_no_prior_data():
    collector = MagicMock()
    collector.collect_naver_themes = AsyncMock(return_value=12)
    repo = MagicMock()
    repo.get_latest_collected_at = AsyncMock(return_value=None)
    task = _make_task(collector, repo)

    await task._on_market_closed("20260621")
    collector.collect_naver_themes.assert_awaited_once()
    assert task.get_progress()["record_count"] == 12


@pytest.mark.asyncio
async def test_skips_when_recently_collected():
    collector = MagicMock()
    collector.collect_naver_themes = AsyncMock()
    repo = MagicMock()
    recent = (datetime.now() - timedelta(days=2)).isoformat(timespec="seconds")
    repo.get_latest_collected_at = AsyncMock(return_value=recent)
    task = _make_task(collector, repo, refresh_interval_days=7)

    await task._on_market_closed("20260621")
    collector.collect_naver_themes.assert_not_awaited()
    assert task.get_progress()["status"] == "skipped"


@pytest.mark.asyncio
async def test_collects_when_interval_elapsed():
    collector = MagicMock()
    collector.collect_naver_themes = AsyncMock(return_value=5)
    repo = MagicMock()
    stale = (datetime.now() - timedelta(days=10)).isoformat(timespec="seconds")
    repo.get_latest_collected_at = AsyncMock(return_value=stale)
    task = _make_task(collector, repo, refresh_interval_days=7)

    await task._on_market_closed("20260621")
    collector.collect_naver_themes.assert_awaited_once()


@pytest.mark.asyncio
async def test_force_run_ignores_guard():
    collector = MagicMock()
    collector.collect_naver_themes = AsyncMock(return_value=3)
    repo = MagicMock()
    recent = datetime.now().isoformat(timespec="seconds")
    repo.get_latest_collected_at = AsyncMock(return_value=recent)
    task = _make_task(collector, repo)

    await task.force_run()
    collector.collect_naver_themes.assert_awaited_once()
