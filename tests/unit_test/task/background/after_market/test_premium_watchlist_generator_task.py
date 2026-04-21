"""
PremiumWatchlistGeneratorTask 단위 테스트.
장 마감 후 전일 기준 우량주 자동 생성 태스크 검증.
"""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from task.background.after_market.premium_watchlist_generator_task import PremiumWatchlistGeneratorTask
from interfaces.schedulable_task import TaskPriority, TaskState
from services.notification_service import NotificationCategory, NotificationLevel


# --- Fixtures ---


_SAMPLE_KOSPI_STOCK = {
    "code": "000001", "name": "StockA", "market": "KOSPI",
    "total_score": 50.0, "rs_rating": 80, "market_cap": 500000000000,
    "avg_trading_value_5d": 20000000000, "minervini_stage": 2,
}
_SAMPLE_KOSDAQ_STOCK = {
    "code": "000002", "name": "StockB", "market": "KOSDAQ",
    "total_score": 40.0, "rs_rating": 70, "market_cap": 300000000000,
    "avg_trading_value_5d": 10000000000, "minervini_stage": 0,
}


@pytest.fixture
def mock_universe_service():
    svc = MagicMock()
    svc.generate_premium_watchlist = AsyncMock(return_value={
        "kospi_count": 30,
        "kosdaq_count": 20,
        "kospi_stocks": [_SAMPLE_KOSPI_STOCK] * 30,
        "kosdaq_stocks": [_SAMPLE_KOSDAQ_STOCK] * 20,
    })
    svc.generation_progress = {
        "running": False,
        "phase": None,
        "processed": 0,
        "total": 0,
        "passed": 0,
        "selected": 0,
        "elapsed": 0.0,
    }
    svc.get_premium_stocks_meta = MagicMock(return_value=None)
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
def task(mock_universe_service, mock_mcs, mock_market_clock):
    return PremiumWatchlistGeneratorTask(
        universe_service=mock_universe_service,
        market_calendar_service=mock_mcs,
        market_clock=mock_market_clock,
        logger=MagicMock(),
    )


# --- 태스크 속성 테스트 ---


class TestTaskProperties:

    def test_task_name(self, task):
        assert task.task_name == "전일기준주도주_생성"

    def test_scheduler_label(self, task):
        assert task._scheduler_label == "전일기준우량주생성"

    def test_priority(self, task):
        assert task.priority == TaskPriority.LOW

    def test_initial_state(self, task):
        assert task.state == TaskState.IDLE

    def test_initial_progress(self, task):
        progress = task.get_progress()
        assert progress["running"] is False
        assert progress["last_generated_date"] is None
        assert progress["last_result"] is None


# --- get_progress 테스트 ---


class TestGetProgress:

    def test_get_progress_merges_service_data(self, task, mock_universe_service):
        """get_progress()는 _progress와 service.generation_progress를 병합한다."""
        mock_universe_service.generation_progress = {
            "running": True,
            "phase": "1차_필터(시총)",
            "processed": 50,
            "total": 200,
            "passed": 30,
            "selected": 0,
            "elapsed": 3.5,
        }

        progress = task.get_progress()

        assert progress["phase"] == "1차_필터(시총)"
        assert progress["processed"] == 50
        assert progress["total"] == 200
        assert progress["passed"] == 30
        assert progress["elapsed"] == 3.5

    def test_get_progress_includes_own_fields(self, task, mock_universe_service):
        """get_progress()는 태스크 자체 필드(running, last_generated_date 등)도 포함한다."""
        task._progress["last_generated_date"] = "20260320"
        task._progress["last_result"] = {"kospi_count": 10, "kosdaq_count": 5}

        progress = task.get_progress()

        assert progress["last_generated_date"] == "20260320"
        assert progress["last_result"]["kospi_count"] == 10

    def test_get_progress_returns_snapshot(self, task, mock_universe_service):
        """get_progress()는 복사본을 반환하여 원본을 보호한다."""
        progress = task.get_progress()
        progress["running"] = True  # 반환된 dict 수정

        assert task._progress["running"] is False  # 원본에 영향 없음


# --- Start/Stop 테스트 ---


class TestStartStop:

    async def test_start_sets_running_state(self, task):
        """start() 후 상태가 RUNNING이고 내부 태스크가 생성된다."""
        await task.start()
        assert task.state == TaskState.RUNNING
        assert len(task._tasks) == 1
        await task.stop()

    async def test_start_idempotent(self, task):
        """이미 RUNNING이면 start()를 중복 호출해도 태스크가 추가되지 않는다."""
        await task.start()
        task_count = len(task._tasks)

        await task.start()  # 중복 호출
        assert len(task._tasks) == task_count

        await task.stop()

    async def test_stop_sets_stopped_state(self, task):
        """stop() 후 상태가 STOPPED이고 태스크 목록이 비워진다."""
        await task.start()
        await task.stop()

        assert task.state == TaskState.STOPPED
        assert len(task._tasks) == 0

    async def test_stop_without_start(self, task):
        """start() 없이 stop()을 호출해도 오류가 없다."""
        await task.stop()  # 예외 없이 완료되어야 함
        assert task.state == TaskState.STOPPED


# --- Suspend/Resume 테스트 ---


class TestSuspendResume:

    async def test_suspend_from_running(self, task):
        """RUNNING 상태에서 suspend()하면 SUSPENDED가 된다."""
        task._state = TaskState.RUNNING
        await task.suspend()
        assert task.state == TaskState.SUSPENDED

    async def test_resume_from_suspended(self, task):
        """SUSPENDED 상태에서 resume()하면 RUNNING이 된다."""
        task._state = TaskState.SUSPENDED
        await task.resume()
        assert task.state == TaskState.RUNNING

    async def test_suspend_when_not_running_is_noop(self, task):
        """RUNNING이 아닌 상태에서 suspend()는 상태를 바꾸지 않는다."""
        assert task.state == TaskState.IDLE
        await task.suspend()
        assert task.state == TaskState.IDLE

    async def test_resume_when_not_suspended_is_noop(self, task):
        """SUSPENDED가 아닌 상태에서 resume()는 상태를 바꾸지 않는다."""
        assert task.state == TaskState.IDLE
        await task.resume()
        assert task.state == TaskState.IDLE


# --- _on_market_closed 테스트 ---


class TestOnMarketClosed:

    async def test_runs_generation_for_new_date(self, task, mock_universe_service):
        """장 마감 콜백에서 새 날짜이면 생성을 실행한다."""
        task._last_generated_date = None  # 아직 생성 안 함
        mock_universe_service.get_premium_stocks_meta = MagicMock(return_value=None)

        await task._on_market_closed("20260320")

        mock_universe_service.generate_premium_watchlist.assert_awaited_once()

    async def test_skips_generation_for_same_date_in_memory(self, task, mock_universe_service):
        """인메모리에 동일 날짜가 기록된 경우 파일 확인 없이 즉시 건너뛴다."""
        task._last_generated_date = "20260320"

        await task._on_market_closed("20260320")

        mock_universe_service.generate_premium_watchlist.assert_not_called()
        # 파일 메타 조회도 불필요
        mock_universe_service.get_premium_stocks_meta.assert_not_called()

    async def test_skips_generation_if_file_already_exists(self, task, mock_universe_service):
        """파일에 당일 기준 우량주가 이미 있으면 재생성하지 않는다 (서버 재시작 후 스킵 시나리오)."""
        task._last_generated_date = None  # 인메모리는 없음
        mock_universe_service.get_premium_stocks_meta = MagicMock(return_value={
            "generated_date": "20260320",
            "generated_at": "2026-03-20T16:05:00",
        })

        await task._on_market_closed("20260320")

        mock_universe_service.generate_premium_watchlist.assert_not_called()
        assert task._last_generated_date == "20260320"
        assert task._progress["last_generated_date"] == "20260320"

    async def test_runs_generation_if_file_has_different_date(self, task, mock_universe_service):
        """파일이 있지만 다른 날짜(전전날 등)이면 생성을 실행한다."""
        task._last_generated_date = None
        mock_universe_service.get_premium_stocks_meta = MagicMock(return_value={
            "generated_date": "20260319",  # 어제 파일
            "generated_at": "2026-03-19T16:05:00",
        })

        await task._on_market_closed("20260320")

        mock_universe_service.generate_premium_watchlist.assert_awaited_once()

    async def test_runs_generation_if_file_meta_is_none(self, task, mock_universe_service):
        """파일이 없으면(메타 None) 생성을 실행한다."""
        task._last_generated_date = None
        mock_universe_service.get_premium_stocks_meta = MagicMock(return_value=None)

        await task._on_market_closed("20260320")

        mock_universe_service.generate_premium_watchlist.assert_awaited_once()

    async def test_runs_generation_for_next_trading_date(self, task, mock_universe_service):
        """다음 거래일에 대해서는 다시 생성을 실행한다."""
        task._last_generated_date = "20260319"  # 전날 생성 완료
        mock_universe_service.get_premium_stocks_meta = MagicMock(return_value={
            "generated_date": "20260319",
            "generated_at": "2026-03-19T16:05:00",
        })

        await task._on_market_closed("20260320")

        mock_universe_service.generate_premium_watchlist.assert_awaited_once()

    async def test_skips_weekend_run_when_file_has_friday_trading_date(self, task, mock_universe_service):
        """버그 재현 시나리오: 일요일(0322)에 실행, latest_trading_date=금요일(0320).
        파일 generated_date가 거래일(0320)로 저장되어 있으면 재생성하지 않는다."""
        task._last_generated_date = None
        # 파일은 금요일 장 마감 후 생성됨 → generated_date = 거래일(금요일)
        mock_universe_service.get_premium_stocks_meta = MagicMock(return_value={
            "generated_date": "20260320",   # 거래일 (금요일)
            "generated_at": "2026-03-20T16:05:00",  # 실제 생성 시각
        })

        await task._on_market_closed("20260320")  # latest_trading_date = 금요일

        mock_universe_service.generate_premium_watchlist.assert_not_called()
        assert task._last_generated_date == "20260320"

    async def test_generates_when_file_has_old_wall_clock_date_not_trading_date(self, task, mock_universe_service):
        """과거 버그 패턴: 파일이 wall-clock 날짜(0322 일요일)로 저장된 경우
        trading_date(0320 금요일)와 불일치 → 올바르게 생성을 실행한다."""
        task._last_generated_date = None
        # 과거 버그: generated_date = 저장 시점 날짜(일요일)
        mock_universe_service.get_premium_stocks_meta = MagicMock(return_value={
            "generated_date": "20260322",   # 잘못된 값: 일요일 날짜
            "generated_at": "2026-03-22T10:00:00",
        })

        await task._on_market_closed("20260320")  # latest_trading_date = 금요일

        # 날짜 불일치이므로 생성을 실행해야 함 (버그가 있던 경우 이 TC가 통과하지 못했음)
        mock_universe_service.generate_premium_watchlist.assert_awaited_once()


# --- _run_generation 테스트 ---


class TestOnMarketClosedFileSkip:
    """서버 재시작 후 파일 기반 스킵 시나리오 상세 검증."""

    async def test_skips_and_updates_progress_last_generated_date(self, task, mock_universe_service):
        """파일 스킵 시 _progress['last_generated_date']가 업데이트된다."""
        mock_universe_service.get_premium_stocks_meta = MagicMock(return_value={
            "generated_date": "20260320",
            "generated_at": "2026-03-20T16:05:00",
        })

        await task._on_market_closed("20260320")

        assert task._progress["last_generated_date"] == "20260320"
        mock_universe_service.generate_premium_watchlist.assert_not_called()

    async def test_skips_only_once_per_date(self, task, mock_universe_service):
        """파일 스킵 후 인메모리에 날짜가 기록되어 다음 콜백은 파일 조회도 하지 않는다."""
        mock_universe_service.get_premium_stocks_meta = MagicMock(return_value={
            "generated_date": "20260320",
            "generated_at": "2026-03-20T16:05:00",
        })

        await task._on_market_closed("20260320")  # 1회차: 파일 확인 후 스킵
        await task._on_market_closed("20260320")  # 2회차: 인메모리 확인 후 즉시 스킵

        assert mock_universe_service.get_premium_stocks_meta.call_count == 1


class TestRunGeneration:

    async def test_run_generation_success(self, task, mock_universe_service):
        """생성 성공 시 진행률, last_generated_date, last_result가 업데이트된다."""
        await task._run_generation("20260320")

        # trading_date가 서비스에 전달되어야 함 (generated_date를 거래일로 저장)
        mock_universe_service.generate_premium_watchlist.assert_awaited_once_with(trading_date="20260320")
        assert task._last_generated_date == "20260320"
        assert task._progress["last_generated_date"] == "20260320"
        assert task._progress["last_result"]["kospi_count"] == 30
        assert task._progress["last_result"]["kosdaq_count"] == 20

    async def test_run_generation_clears_running_flag_after_success(self, task):
        """생성 완료 후 running 플래그가 False로 초기화된다."""
        await task._run_generation("20260320")

        assert task._progress["running"] is False
        assert task._is_generating is False

    async def test_run_generation_skips_if_already_generating(self, task, mock_universe_service):
        """이미 생성 중이면 중복 실행을 건너뛴다."""
        task._is_generating = True

        await task._run_generation("20260320")

        mock_universe_service.generate_premium_watchlist.assert_not_called()

    async def test_run_generation_handles_exception(self, task, mock_universe_service):
        """서비스 예외 발생 시 running/is_generating 플래그가 정리된다."""
        mock_universe_service.generate_premium_watchlist.side_effect = RuntimeError("생성 실패")

        await task._run_generation("20260320")  # 예외가 전파되면 안 됨

        assert task._progress["running"] is False
        assert task._is_generating is False
        assert task._last_generated_date is None  # 실패 시 날짜 미갱신

    async def test_run_generation_sets_running_flag_during_execution(self, task, mock_universe_service):
        """생성 중에는 running 플래그가 True이다."""
        running_during = []

        async def _mock_generate(trading_date=None):
            running_during.append(task._progress["running"])
            return {"kospi_count": 5, "kosdaq_count": 3}

        mock_universe_service.generate_premium_watchlist = _mock_generate

        await task._run_generation("20260320")

        assert running_during == [True]
        assert task._progress["running"] is False  # 완료 후 False


# --- 강제 생성(force_run) 테스트 ---


class TestForceGenerate:

    async def test_force_run_success(self, task, mock_mcs, mock_universe_service):
        """정상적으로 최근 거래일을 가져와 생성을 강제 실행한다."""
        await task.force_run()
        
        mock_mcs.get_latest_trading_date.assert_awaited_once()
        mock_universe_service.generate_premium_watchlist.assert_awaited_once_with(trading_date="20260320")

    async def test_force_run_stops_if_no_trading_date(self, task, mock_mcs, mock_universe_service):
        """최근 거래일을 확인할 수 없으면 강제 생성을 중단한다."""
        mock_mcs.get_latest_trading_date.return_value = None
        
        await task.force_run()
        
        mock_universe_service.generate_premium_watchlist.assert_not_called()

    async def test_force_run_stops_if_no_mcs(self, task, mock_universe_service):
        """mcs가 없으면 강제 생성을 중단한다."""
        task._mcs = None
        
        await task.force_run()
        
        mock_universe_service.generate_premium_watchlist.assert_not_called()


# --- Notification 테스트 ---


class TestNotification:

    @pytest.fixture
    def mock_ns(self):
        ns = MagicMock()
        ns.emit = AsyncMock()
        return ns

    @pytest.fixture
    def task_with_ns(self, mock_universe_service, mock_mcs, mock_market_clock, mock_ns):
        return PremiumWatchlistGeneratorTask(
            universe_service=mock_universe_service,
            market_calendar_service=mock_mcs,
            market_clock=mock_market_clock,
            logger=MagicMock(),
            notification_service=mock_ns,
        )

    async def test_on_market_closed_skip_in_memory_emits_notification(self, task_with_ns, mock_ns):
        task_with_ns._last_generated_date = "20260320"
        await task_with_ns._on_market_closed("20260320")
        mock_ns.emit.assert_awaited_once_with(
            NotificationCategory.BACKGROUND, NotificationLevel.INFO, "전일기준우량주 생성 스킵",
            "20260320 이미 생성 완료된 상태입니다."
        )

    async def test_on_market_closed_skip_in_file_emits_notification(self, task_with_ns, mock_universe_service, mock_ns):
        task_with_ns._last_generated_date = None
        mock_universe_service.get_premium_stocks_meta = MagicMock(return_value={
            "generated_date": "20260320",
            "generated_at": "2026-03-20T16:05:00",
        })
        await task_with_ns._on_market_closed("20260320")
        mock_ns.emit.assert_awaited_once_with(
            NotificationCategory.BACKGROUND, NotificationLevel.INFO, "전일기준우량주 생성 스킵",
            "20260320 이미 생성 완료된 상태입니다."
        )

    async def test_run_generation_success_emits_notification(self, task_with_ns, mock_ns):
        await task_with_ns._run_generation("20260320")
        assert mock_ns.emit.await_count == 1
        call_args = mock_ns.emit.await_args[0]
        assert call_args[0] == NotificationCategory.BACKGROUND
        assert call_args[1] == NotificationLevel.INFO
        assert call_args[2] == "전일기준우량주 생성 완료"
        assert "KOSPI 30개" in call_args[3]
        assert "KOSDAQ 20개" in call_args[3]

    async def test_run_generation_failure_emits_notification(self, task_with_ns, mock_universe_service, mock_ns):
        mock_universe_service.generate_premium_watchlist.side_effect = RuntimeError("생성 실패 테스트")
        await task_with_ns._run_generation("20260320")
        mock_ns.emit.assert_awaited_once_with(
            NotificationCategory.BACKGROUND, NotificationLevel.ERROR, "전일기준우량주 생성 실패", "생성 실패 테스트"
        )


# --- TelegramReporter 연동 테스트 ---


class TestTelegramReporterIntegration:

    @pytest.fixture
    def mock_reporter(self):
        reporter = MagicMock()
        reporter.send_premium_watchlist_report = AsyncMock()
        return reporter

    @pytest.fixture
    def task_with_reporter(self, mock_universe_service, mock_mcs, mock_market_clock, mock_reporter):
        return PremiumWatchlistGeneratorTask(
            universe_service=mock_universe_service,
            market_calendar_service=mock_mcs,
            market_clock=mock_market_clock,
            logger=MagicMock(),
            telegram_reporter=mock_reporter,
        )

    async def test_run_generation_calls_reporter_on_success(self, task_with_reporter, mock_reporter):
        """생성 성공 시 TelegramReporter.send_premium_watchlist_report가 호출된다."""
        with patch("asyncio.create_task") as mock_create_task:
            await task_with_reporter._run_generation("20260320")
            mock_create_task.assert_called_once()

    async def test_run_generation_passes_stocks_to_reporter(self, task_with_reporter, mock_reporter, mock_universe_service):
        """리포터에 kospi_stocks / kosdaq_stocks 가 올바르게 전달된다."""
        captured_coro = None

        def capture_task(coro):
            nonlocal captured_coro
            captured_coro = coro
            return MagicMock()

        with patch("asyncio.create_task", side_effect=capture_task):
            await task_with_reporter._run_generation("20260320")

        assert captured_coro is not None
        await captured_coro  # 실제 코루틴 실행

        mock_reporter.send_premium_watchlist_report.assert_awaited_once_with(
            kospi=[_SAMPLE_KOSPI_STOCK] * 30,
            kosdaq=[_SAMPLE_KOSDAQ_STOCK] * 20,
            report_date="20260320",
        )

    async def test_run_generation_no_reporter_no_error(self, task, mock_universe_service):
        """reporter가 없으면 오류 없이 동작한다."""
        assert task._telegram_reporter is None
        await task._run_generation("20260320")  # 예외 없이 완료
        mock_universe_service.generate_premium_watchlist.assert_awaited_once()

    async def test_run_generation_failure_does_not_call_reporter(self, task_with_reporter, mock_universe_service, mock_reporter):
        """생성 실패 시 리포터를 호출하지 않는다."""
        mock_universe_service.generate_premium_watchlist.side_effect = RuntimeError("실패")
        with patch("asyncio.create_task") as mock_create_task:
            await task_with_reporter._run_generation("20260320")
            mock_create_task.assert_not_called()
