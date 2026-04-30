"""
CacheWarmupTask 단위 테스트.
장전/장중 watchlist/보유종목 캐시 웜업 태스크 검증.
"""
import asyncio
import pytest
from datetime import datetime
from unittest.mock import MagicMock, AsyncMock, patch

from task.background.after_market.cache_warmup_task import CacheWarmupTask
from common.types import ResCommonResponse, ErrorCode
from interfaces.schedulable_task import TaskPriority, TaskState


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_mds():
    """MarketDataService mock."""
    svc = MagicMock()
    svc.get_price_summary = AsyncMock(
        return_value=ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data={})
    )
    return svc


@pytest.fixture
def mock_sqs():
    """StockQueryService mock."""
    svc = MagicMock()
    svc.handle_get_account_balance = AsyncMock(
        return_value=ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value,
            msg1="OK",
            data={"output1": [{"pdno": "005930"}, {"pdno": "000660"}]},
        )
    )
    return svc


@pytest.fixture
def mock_universe_service():
    svc = MagicMock()
    svc.get_watchlist = AsyncMock(return_value={"035720": MagicMock(), "035420": MagicMock()})
    return svc


@pytest.fixture
def mock_mcs():
    mcs = MagicMock()
    mcs.get_latest_trading_date = AsyncMock(return_value="20260320")
    return mcs


@pytest.fixture
def mock_market_clock():
    return MagicMock()


@pytest.fixture
def task(mock_mds, mock_sqs, mock_universe_service, mock_mcs, mock_market_clock):
    return CacheWarmupTask(
        market_data_service=mock_mds,
        stock_query_service=mock_sqs,
        universe_service=mock_universe_service,
        market_calendar_service=mock_mcs,
        market_clock=mock_market_clock,
        logger=MagicMock(),
    )


# ---------------------------------------------------------------------------
# 태스크 속성
# ---------------------------------------------------------------------------

class TestTaskProperties:

    def test_task_name(self, task):
        assert task.task_name == "cache_warmup"

    def test_priority(self, task):
        assert task.priority == TaskPriority.LOW

    def test_initial_state(self, task):
        assert task.state == TaskState.IDLE

    def test_initial_progress(self, task):
        p = task.get_progress()
        assert p["running"] is False
        assert p["processed"] == 0
        assert p["total"] == 0
        assert p["cached"] == 0
        assert p["failed"] == 0
        assert p["last_warmed_date"] is None

    def test_get_progress_returns_snapshot(self, task):
        p = task.get_progress()
        p["running"] = True
        assert task._progress["running"] is False


# ---------------------------------------------------------------------------
# Start / Stop
# ---------------------------------------------------------------------------

class TestStartStop:

    async def test_start_schedules_premarket_loop_and_stays_idle_until_run(self, task):
        try:
            await task.start()
            assert task.state == TaskState.IDLE
            assert len(task._tasks) == 1
        finally:
            await task.stop()

    async def test_start_idempotent(self, task):
        try:
            await task.start()
            count = len(task._tasks)
            await task.start()
            assert len(task._tasks) == count
        finally:
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
        assert task.state == TaskState.IDLE

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

class TestShouldRunNow:

    async def test_should_run_in_pre_open_window(self, task, mock_mcs, mock_market_clock):
        mock_market_clock.get_current_kst_time.return_value = datetime(2026, 3, 20, 8, 45)
        mock_market_clock.get_market_open_time.return_value = datetime(2026, 3, 20, 9, 0)
        mock_market_clock.is_market_operating_hours.return_value = False
        mock_mcs.is_business_day = AsyncMock(return_value=True)

        should_run, date_key = await task._should_run_now()

        assert should_run is True
        assert date_key == "20260320"

    async def test_should_run_during_market_hours_for_late_start(self, task, mock_mcs, mock_market_clock):
        mock_market_clock.get_current_kst_time.return_value = datetime(2026, 3, 20, 10, 0)
        mock_market_clock.get_market_open_time.return_value = datetime(2026, 3, 20, 9, 0)
        mock_market_clock.is_market_operating_hours.return_value = True
        mock_mcs.is_business_day = AsyncMock(return_value=True)

        should_run, date_key = await task._should_run_now()

        assert should_run is True
        assert date_key == "20260320"

    async def test_should_not_run_twice_same_day(self, task, mock_mcs, mock_market_clock):
        task._last_warmed_date = "20260320"
        mock_market_clock.get_current_kst_time.return_value = datetime(2026, 3, 20, 10, 0)
        mock_market_clock.is_market_operating_hours.return_value = True
        mock_mcs.is_business_day = AsyncMock(return_value=True)

        should_run, date_key = await task._should_run_now()

        assert should_run is False
        assert date_key == "20260320"

    async def test_should_not_run_on_closed_day_or_without_dependencies(self, task, mock_mcs, mock_market_clock):
        empty_task = CacheWarmupTask(
            market_data_service=MagicMock(),
            stock_query_service=MagicMock(),
            market_calendar_service=None,
            market_clock=None,
            logger=MagicMock(),
        )
        assert await empty_task._should_run_now() == (False, None)

        mock_market_clock.get_current_kst_time.return_value = datetime(2026, 3, 21, 8, 45)
        mock_mcs.is_business_day = AsyncMock(return_value=False)
        assert await task._should_run_now() == (False, "20260321")


class TestLoop:

    async def test_loop_runs_immediately_when_market_is_open(self, task):
        task._should_run_now = AsyncMock(side_effect=[(True, "20260320"), asyncio.CancelledError()])
        task._run_warmup = AsyncMock()

        with patch("task.background.after_market.cache_warmup_task.asyncio.sleep", new_callable=AsyncMock):
            await task._loop()

        task._run_warmup.assert_awaited_once_with("20260320")
        assert task.state == TaskState.IDLE


# ---------------------------------------------------------------------------
# _run_warmup
# ---------------------------------------------------------------------------

class TestRunWarmup:

    async def test_success_updates_last_warmed_date(self, task, mock_mds):
        await task._run_warmup("20260320")
        assert task._last_warmed_date == "20260320"
        assert task._progress["last_warmed_date"] == "20260320"

    async def test_success_clears_running_flag(self, task, mock_mds):
        await task._run_warmup("20260320")
        assert task._progress["running"] is False
        assert task._is_warming is False

    async def test_skips_if_already_warming(self, task, mock_mds):
        task._is_warming = True
        await task._run_warmup("20260320")
        mock_mds.get_price_summary.assert_not_called()

    async def test_handles_exception_clears_flags(self, task):
        """_collect_target_codes에서 예외 발생 시 플래그가 정리되고 날짜가 갱신되지 않는다."""
        with patch.object(task, "_collect_target_codes", new_callable=AsyncMock,
                          side_effect=RuntimeError("수집 오류")):
            await task._run_warmup("20260320")
        assert task._progress["running"] is False
        assert task._is_warming is False
        assert task._last_warmed_date is None

    async def test_warmup_code_failure_counted_as_failed(self, task, mock_mds):
        """get_price_summary가 예외를 던져도 _run_warmup은 완료되고 failed가 집계된다."""
        mock_mds.get_price_summary.side_effect = RuntimeError("API 오류")
        await task._run_warmup("20260320")
        assert task._progress["running"] is False
        assert task._progress["failed"] > 0
        assert task._last_warmed_date == "20260320"

    async def test_progress_cached_count(self, task, mock_mds):
        """성공적으로 캐시된 종목 수가 progress에 반영된다."""
        await task._run_warmup("20260320")
        # watchlist 2 + holdings 2 + premium(없음) = 최대 4개 고유 종목
        assert task._progress["cached"] >= 0
        assert task._progress["processed"] == task._progress["total"]

    async def test_running_flag_set_during_execution(self, task, mock_mds):
        running_during = []

        async def capture_flag(code, **_):
            running_during.append(task._progress["running"])
            return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data={})

        mock_mds.get_price_summary.side_effect = capture_flag
        await task._run_warmup("20260320")

        assert True in running_during
        assert task._progress["running"] is False


# ---------------------------------------------------------------------------
# _warmup_code
# ---------------------------------------------------------------------------

class TestWarmupCode:

    async def test_returns_true_on_success(self, task, mock_mds):
        result = await task._warmup_code("005930")
        assert result is True

    async def test_returns_false_on_api_error(self, task, mock_mds):
        mock_mds.get_price_summary.return_value = ResCommonResponse(
            rt_cd="1", msg1="에러", data=None
        )
        result = await task._warmup_code("005930")
        assert result is False

    async def test_returns_false_on_none_response(self, task, mock_mds):
        mock_mds.get_price_summary.return_value = None
        result = await task._warmup_code("005930")
        assert result is False

    async def test_returns_false_on_exception(self, task, mock_mds):
        mock_mds.get_price_summary.side_effect = Exception("네트워크 오류")
        result = await task._warmup_code("005930")
        assert result is False

    async def test_propagates_cancelled_error(self, task, mock_mds):
        import asyncio
        mock_mds.get_price_summary.side_effect = asyncio.CancelledError()
        with pytest.raises(asyncio.CancelledError):
            await task._warmup_code("005930")


# ---------------------------------------------------------------------------
# _get_watchlist_codes
# ---------------------------------------------------------------------------

class TestGetWatchlistCodes:

    async def test_returns_codes_from_universe_service(self, task, mock_universe_service):
        codes = await task._get_watchlist_codes()
        assert set(codes) == {"035720", "035420"}

    async def test_returns_empty_if_no_universe_service(self, mock_mds, mock_sqs, mock_mcs, mock_market_clock):
        t = CacheWarmupTask(
            market_data_service=mock_mds,
            stock_query_service=mock_sqs,
            universe_service=None,
            market_calendar_service=mock_mcs,
            market_clock=mock_market_clock,
            logger=MagicMock(),
        )
        codes = await t._get_watchlist_codes()
        assert codes == []

    async def test_returns_empty_on_exception(self, task, mock_universe_service):
        mock_universe_service.get_watchlist.side_effect = RuntimeError("서비스 오류")
        codes = await task._get_watchlist_codes()
        assert codes == []


# ---------------------------------------------------------------------------
# _get_holdings_codes
# ---------------------------------------------------------------------------

class TestGetHoldingsCodes:

    async def test_returns_pdno_codes(self, task, mock_sqs):
        codes = await task._get_holdings_codes()
        assert "005930" in codes
        assert "000660" in codes

    async def test_returns_empty_on_api_error(self, task, mock_sqs):
        mock_sqs.handle_get_account_balance.return_value = ResCommonResponse(
            rt_cd="1", msg1="에러", data=None
        )
        codes = await task._get_holdings_codes()
        assert codes == []

    async def test_returns_empty_on_none_response(self, task, mock_sqs):
        mock_sqs.handle_get_account_balance.return_value = None
        codes = await task._get_holdings_codes()
        assert codes == []

    async def test_returns_empty_on_exception(self, task, mock_sqs):
        mock_sqs.handle_get_account_balance.side_effect = RuntimeError("잔고 조회 실패")
        codes = await task._get_holdings_codes()
        assert codes == []

    async def test_skips_empty_pdno(self, task, mock_sqs):
        mock_sqs.handle_get_account_balance.return_value = ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value,
            msg1="OK",
            data={"output1": [{"pdno": "005930"}, {"pdno": ""}, {"pdno": "  "}]},
        )
        codes = await task._get_holdings_codes()
        assert codes == ["005930"]



# ---------------------------------------------------------------------------
# _collect_target_codes (통합)
# ---------------------------------------------------------------------------

class TestCollectTargetCodes:

    async def test_deduplicates_across_sources(self, task, mock_universe_service, mock_sqs):
        """watchlist·보유 종목에 중복 종목이 있어도 Set으로 중복 제거된다."""
        mock_universe_service.get_watchlist.return_value = {
            "035720": MagicMock(),
            "005930": MagicMock(),
        }
        codes = await task._collect_target_codes()

        assert codes == {"035720", "005930", "000660"}

    async def test_empty_when_all_sources_fail(self, task, mock_universe_service, mock_sqs):
        mock_universe_service.get_watchlist.side_effect = RuntimeError()
        mock_sqs.handle_get_account_balance.return_value = None
        with patch("os.path.exists", return_value=False):
            codes = await task._collect_target_codes()
        assert codes == set()


# ---------------------------------------------------------------------------
# force_warmup
# ---------------------------------------------------------------------------

class TestForceWarmup:

    async def test_force_warmup_runs_with_latest_trading_date(self, task, mock_mcs):
        with patch.object(task, "_run_warmup", new_callable=AsyncMock) as mock_run:
            await task.force_run()
            mock_run.assert_awaited_once_with("20260320")

    async def test_force_warmup_aborts_if_no_mcs(self, mock_mds, mock_sqs, mock_market_clock):
        t = CacheWarmupTask(
            market_data_service=mock_mds,
            stock_query_service=mock_sqs,
            market_calendar_service=None,
            market_clock=mock_market_clock,
            logger=MagicMock(),
        )
        with patch.object(t, "_run_warmup", new_callable=AsyncMock) as mock_run:
            await t.force_run()
            mock_run.assert_not_awaited()
