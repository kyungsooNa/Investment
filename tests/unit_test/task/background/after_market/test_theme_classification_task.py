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


@pytest.mark.asyncio
async def test_collects_industries_in_the_same_run():
    """업종은 테마와 같은 사이트·같은 주기라 한 번의 실행에서 함께 수집한다.

    별도 태스크로 두면 전역 수집시각(get_latest_collected_at)을 공유해 서로의
    주기 가드를 건드린다.
    """
    collector = MagicMock()
    collector.collect_naver_themes = AsyncMock(return_value=12)
    collector.collect_naver_industries = AsyncMock(return_value=79)
    repo = MagicMock()
    repo.get_latest_collected_at = AsyncMock(return_value=None)
    task = _make_task(collector, repo)

    await task._on_market_closed("20260621")

    collector.collect_naver_themes.assert_awaited_once()
    collector.collect_naver_industries.assert_awaited_once()
    assert task.get_progress()["record_count"] == 12
    assert task.get_progress()["industry_record_count"] == 79
    assert task.get_progress()["status"] == "done"


@pytest.mark.asyncio
async def test_industry_failure_does_not_lose_the_theme_result():
    """업종 수집이 터져도 테마 결과와 진행 상태는 남아야 한다."""
    collector = MagicMock()
    collector.collect_naver_themes = AsyncMock(return_value=12)
    collector.collect_naver_industries = AsyncMock(side_effect=RuntimeError("업종 페이지 개편"))
    repo = MagicMock()
    repo.get_latest_collected_at = AsyncMock(return_value=None)
    task = _make_task(collector, repo)

    await task._on_market_closed("20260621")

    assert task.get_progress()["record_count"] == 12
    assert task.get_progress()["industry_record_count"] == 0
    assert "업종" in str(task.get_progress()["last_error"]) or task.get_progress()["last_error"]
