"""
LogCleanupTask 단위 테스트.
장 마감 5시간 후 오래된 로그 파일을 정리하는 유지보수 태스크 검증.
"""
import asyncio
import os
import time
import pytest
from unittest.mock import MagicMock, AsyncMock, patch, call

from task.background.after_market.log_cleanup_task import LogCleanupTask
from interfaces.schedulable_task import TaskPriority, TaskState


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_mcs():
    mcs = MagicMock()
    mcs.get_latest_trading_date = AsyncMock(return_value="20260320")
    return mcs


@pytest.fixture
def mock_market_clock():
    return MagicMock()


@pytest.fixture
def task(mock_mcs, mock_market_clock):
    return LogCleanupTask(
        log_dir="logs",
        delete_days=30,
        compress_days=7,
        market_calendar_service=mock_mcs,
        market_clock=mock_market_clock,
        logger=MagicMock(),
    )


# ---------------------------------------------------------------------------
# 태스크 속성
# ---------------------------------------------------------------------------

class TestTaskProperties:

    def test_task_name(self, task):
        assert task.task_name == "log_cleanup"

    def test_priority(self, task):
        assert task.priority == TaskPriority.MAINTENANCE

    def test_scheduler_label(self, task):
        assert task._scheduler_label == "LogCleanup"

    def test_initial_state(self, task):
        assert task.state == TaskState.IDLE

    def test_initial_progress(self, task):
        p = task.get_progress()
        assert p["running"] is False

    def test_get_progress_returns_snapshot(self, task):
        """get_progress()는 내부 dict의 복사본을 반환해야 한다."""
        p = task.get_progress()
        p["running"] = True
        assert task._progress["running"] is False


# ---------------------------------------------------------------------------
# Start / Stop
# ---------------------------------------------------------------------------

class TestStartStop:

    async def test_start_sets_running_state(self, task):
        await task.start()
        assert task.state == TaskState.RUNNING
        assert len(task._tasks) == 1
        await task.stop()

    async def test_start_idempotent(self, task):
        await task.start()
        count = len(task._tasks)
        await task.start()
        assert len(task._tasks) == count
        await task.stop()

    async def test_stop_sets_stopped_state(self, task):
        await task.start()
        await task.stop()
        assert task.state == TaskState.STOPPED
        assert len(task._tasks) == 0

    async def test_stop_without_start(self, task):
        await task.stop()
        assert task.state == TaskState.STOPPED


# ---------------------------------------------------------------------------
# Suspend / Resume
# ---------------------------------------------------------------------------

class TestSuspendResume:

    async def test_suspend_from_running(self, task):
        task._state = TaskState.RUNNING
        await task.suspend()
        assert task.state == TaskState.SUSPENDED

    async def test_resume_from_suspended(self, task):
        task._state = TaskState.SUSPENDED
        await task.resume()
        assert task.state == TaskState.RUNNING

    async def test_suspend_when_not_running_is_noop(self, task):
        assert task.state == TaskState.IDLE
        await task.suspend()
        assert task.state == TaskState.IDLE

    async def test_resume_when_not_suspended_is_noop(self, task):
        assert task.state == TaskState.IDLE
        await task.resume()
        assert task.state == TaskState.IDLE


# ---------------------------------------------------------------------------
# _on_market_closed
# ---------------------------------------------------------------------------

class TestOnMarketClosed:

    async def test_runs_cleanup_for_new_date(self, task):
        task._last_cleaned_date = None
        with patch("asyncio.to_thread", new_callable=AsyncMock) as mock_thread:
            await task._on_market_closed("20260320")
            mock_thread.assert_awaited_once_with(task._cleanup, "20260320")

    async def test_skips_cleanup_for_same_date(self, task):
        task._last_cleaned_date = "20260320"
        with patch("asyncio.to_thread", new_callable=AsyncMock) as mock_thread:
            await task._on_market_closed("20260320")
            mock_thread.assert_not_awaited()

    async def test_runs_cleanup_for_next_trading_date(self, task):
        task._last_cleaned_date = "20260319"
        with patch("asyncio.to_thread", new_callable=AsyncMock) as mock_thread:
            await task._on_market_closed("20260320")
            mock_thread.assert_awaited_once_with(task._cleanup, "20260320")


# ---------------------------------------------------------------------------
# _cleanup (동기 핵심 로직)
# ---------------------------------------------------------------------------

class TestCleanup:

    def _make_file_tree(self, files: dict):
        """os.walk 용 (root, dirs, files) 목록 생성 헬퍼.
        files: {"root_path": ["file1.log", "file2.json", ...]}
        """
        return [(root, [], fnames) for root, fnames in files.items()]

    def test_deletes_old_log_files(self, task):
        """mtime < cutoff 인 .log 파일은 삭제된다."""
        old_mtime = time.time() - (31 * 86400)  # 31일 전

        walk_result = [("logs", [], ["old.log", "old.json"])]

        with patch("os.walk", return_value=walk_result), \
             patch("os.path.getmtime", return_value=old_mtime), \
             patch("os.remove") as mock_remove:
            task._cleanup("20260320")

        assert mock_remove.call_count == 2
        assert task._last_cleaned_date == "20260320"

    def test_compresses_old_log_files(self, task):
        """mtime < compress_cutoff 인 파일은 gzip으로 압축된다."""
        old_mtime = time.time() - (10 * 86400)  # 10일 전 (압축 대상)

        walk_result = [("logs", [], ["old.log"])]

        with patch("os.walk", return_value=walk_result), \
             patch("os.path.getmtime", return_value=old_mtime), \
             patch("builtins.open"), \
             patch("gzip.open"), \
             patch("shutil.copyfileobj"), \
             patch("os.remove") as mock_remove:
            task._cleanup("20260320")

        mock_remove.assert_called_once()

    def test_skips_recent_files(self, task):
        """mtime >= cutoff 인 파일은 삭제하지 않는다."""
        recent_mtime = time.time() - (5 * 86400)  # 5일 전

        walk_result = [("logs", [], ["recent.log"])]

        with patch("os.walk", return_value=walk_result), \
             patch("os.path.getmtime", return_value=recent_mtime), \
             patch("os.remove") as mock_remove:
            task._cleanup("20260320")

        mock_remove.assert_not_called()

    def test_skips_non_log_files(self, task):
        """확장자가 .log, .json 이 아닌 파일은 무시한다."""
        old_mtime = time.time() - (31 * 86400)

        walk_result = [("logs", [], ["data.csv", "config.yaml", "readme.txt"])]

        with patch("os.walk", return_value=walk_result), \
             patch("os.path.getmtime", return_value=old_mtime), \
             patch("os.remove") as mock_remove:
            task._cleanup("20260320")

        mock_remove.assert_not_called()

    def test_handles_remove_exception(self, task):
        """os.remove 예외 발생 시 경고 로그만 남기고 계속 진행한다."""
        old_mtime = time.time() - (31 * 86400)

        walk_result = [("logs", [], ["a.log", "b.log"])]

        with patch("os.walk", return_value=walk_result), \
             patch("os.path.getmtime", return_value=old_mtime), \
             patch("os.remove", side_effect=OSError("삭제 실패")):
            task._cleanup("20260320")  # 예외가 전파되지 않아야 함

        task._logger.warning.assert_called()
        assert task._last_cleaned_date == "20260320"

    def test_updates_last_cleaned_date(self, task):
        """_cleanup 완료 후 _last_cleaned_date 가 갱신된다."""
        walk_result = [("logs", [], [])]

        with patch("os.walk", return_value=walk_result):
            task._cleanup("20260320")

        assert task._last_cleaned_date == "20260320"

    def test_progress_running_flag_cleared_after_cleanup(self, task):
        """_cleanup 완료 후 progress["running"]은 False여야 한다."""
        walk_result = [("logs", [], [])]

        with patch("os.walk", return_value=walk_result):
            task._cleanup("20260320")

        assert task._progress["running"] is False

    def test_uses_configured_days_as_cutoff(self, mock_mcs, mock_market_clock):
        """days 파라미터가 cutoff 계산에 정확히 반영된다."""
        task_7days = LogCleanupTask(
            log_dir="logs",
            delete_days=7,
            compress_days=3,
            market_calendar_service=mock_mcs,
            market_clock=mock_market_clock,
            logger=MagicMock(),
        )
        # 8일 전 파일 → 삭제 대상
        old_mtime = time.time() - (8 * 86400)
        walk_result = [("logs", [], ["old.log"])]

        with patch("os.walk", return_value=walk_result), \
             patch("os.path.getmtime", return_value=old_mtime), \
             patch("os.remove") as mock_remove:
            task_7days._cleanup("20260320")

        mock_remove.assert_called_once()

    def test_respects_log_dir_parameter(self, mock_mcs, mock_market_clock):
        """log_dir 파라미터가 os.walk에 그대로 전달된다."""
        custom_task = LogCleanupTask(
            log_dir="custom/logs",
            delete_days=30,
            compress_days=7,
            market_calendar_service=mock_mcs,
            market_clock=mock_market_clock,
            logger=MagicMock(),
        )
        with patch("os.walk", return_value=[]) as mock_walk:
            custom_task._cleanup("20260320")

        mock_walk.assert_called_once_with("custom/logs")

    def test_walks_subdirectories(self, task):
        """하위 디렉토리의 파일도 순회하여 삭제한다."""
        old_mtime = time.time() - (31 * 86400)

        walk_result = [
            ("logs", ["subdir"], ["root.log"]),
            ("logs/subdir", [], ["sub.log"]),
        ]

        with patch("os.walk", return_value=walk_result), \
             patch("os.path.getmtime", return_value=old_mtime), \
             patch("os.remove") as mock_remove:
            task._cleanup("20260320")

        assert mock_remove.call_count == 2
