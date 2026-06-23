"""
AfterMarketLoop / run_after_market_loop 단위 테스트.
APScheduler + catch-up 로직 검증.
"""
import asyncio
import pytest

from unittest.mock import MagicMock, AsyncMock, patch, mock_open

from scheduler.after_market_loop import AfterMarketLoop, run_after_market_loop
import task.background.after_market.after_market_task_base as base_module
import config.task_config_loader as loader_module
from config.task_config_loader import load_after_market_delays as _load_after_market_delays


# ── 헬퍼 ──

def _make_loop(
    *,
    hour: int = 18,
    minute: int = 0,
    today: str = "20260420",
    latest_trading_date: str = "20260420",
    last_run: str | None = None,
    delay_sec: int = 0,
    with_store: bool = False,
    with_mcs: bool = True,
    timezone: str | None = None,
    cron_hour: int | None = None,
    cron_minute: int | None = None,
) -> AfterMarketLoop:
    tm = MagicMock()
    tm.get_current_kst_time.return_value = MagicMock(hour=hour, minute=minute)
    tm.get_current_kst_date_str.return_value = today

    mcs = None
    if with_mcs:
        mcs = MagicMock()
        mcs.get_latest_trading_date = AsyncMock(return_value=latest_trading_date)

    store = None
    if with_store:
        store = MagicMock()
        store.load_keyed.return_value = last_run

    kwargs = {}
    if timezone is not None:
        kwargs["timezone"] = timezone
    if cron_hour is not None:
        kwargs["cron_hour"] = cron_hour
    if cron_minute is not None:
        kwargs["cron_minute"] = cron_minute

    return AfterMarketLoop(
        mcs=mcs,
        market_clock=tm,
        logger=MagicMock(),
        on_market_closed=AsyncMock(),
        label="Test",
        delay_sec=delay_sec,
        store=store,
        **kwargs,
    )


# ── catch-up 로직 ──

class TestCatchUp:

    async def test_not_triggered_before_close(self):
        """15:41 이전이면 catch-up을 실행하지 않는다."""
        loop = _make_loop(hour=14, minute=0)
        with patch("asyncio.create_task") as mock_create:
            await loop._catch_up_if_needed()
            mock_create.assert_not_called()

    async def test_not_triggered_at_exactly_close_boundary(self):
        """15:40 (마감 1분 전)이면 catch-up을 실행하지 않는다."""
        loop = _make_loop(hour=15, minute=40)
        with patch("asyncio.create_task") as mock_create:
            await loop._catch_up_if_needed()
            mock_create.assert_not_called()

    async def test_triggered_when_not_yet_run(self):
        """15:41 이후, 오늘 실행 기록이 없으면 즉시 1회 실행한다."""
        loop = _make_loop(hour=18, minute=0, with_store=True, last_run=None)
        created = []
        with patch("asyncio.create_task", side_effect=lambda c: created.append(c)):
            await loop._catch_up_if_needed()
        assert len(created) == 1

    async def test_skipped_when_already_run_today(self):
        """오늘 이미 실행했으면 catch-up을 스킵한다."""
        loop = _make_loop(hour=18, minute=0, with_store=True, last_run="20260420")
        with patch("asyncio.create_task") as mock_create:
            await loop._catch_up_if_needed()
            mock_create.assert_not_called()

    async def test_skipped_on_holiday(self):
        """오늘이 휴장일(latest_trading_date != today)이면 catch-up을 스킵한다."""
        loop = _make_loop(
            hour=18, minute=0,
            today="20260420", latest_trading_date="20260419",  # 어제가 마지막 거래일
        )
        with patch("asyncio.create_task") as mock_create:
            await loop._catch_up_if_needed()
            mock_create.assert_not_called()

    async def test_triggered_at_exactly_1541(self):
        """정확히 15:41이면 catch-up을 트리거한다."""
        loop = _make_loop(hour=15, minute=41, with_store=True, last_run=None)
        created = []
        with patch("asyncio.create_task", side_effect=lambda c: created.append(c)):
            await loop._catch_up_if_needed()
        assert len(created) == 1


# ── timezone / cron 시각 파라미터화 ──

class TestSchedulerTimezoneAndCron:

    def test_default_timezone_is_seoul(self):
        """기본값은 Asia/Seoul (국내 태스크 무영향)."""
        loop = _make_loop()
        assert str(loop._scheduler.timezone) == "Asia/Seoul"

    def test_custom_timezone_applied_to_scheduler(self):
        """timezone 인자가 APScheduler 에 반영된다 (미국장 등)."""
        loop = _make_loop(timezone="America/New_York")
        assert str(loop._scheduler.timezone) == "America/New_York"

    async def test_custom_cron_boundary_triggers_catchup(self):
        """cron_hour/minute 경계 이후이면 catch-up 을 트리거한다 (16:30 ET 등)."""
        loop = _make_loop(
            hour=16, minute=30, cron_hour=16, cron_minute=30,
            with_store=True, last_run=None,
        )
        created = []
        with patch("asyncio.create_task", side_effect=lambda c: created.append(c)):
            await loop._catch_up_if_needed()
        assert len(created) == 1

    async def test_custom_cron_boundary_skips_before(self):
        """cron 경계 1분 전이면 catch-up 을 트리거하지 않는다."""
        loop = _make_loop(hour=16, minute=29, cron_hour=16, cron_minute=30)
        with patch("asyncio.create_task") as mock_create:
            await loop._catch_up_if_needed()
            mock_create.assert_not_called()


# ── _run_job 실행 로직 ──

class TestRunJob:

    async def test_uses_clock_date_when_no_mcs(self):
        """mcs 미주입 시 market_clock 의 날짜를 latest_trading_date 로 사용한다.

        미국장 dry-run 은 한국 거래 캘린더(mcs)가 없으므로, 클럭 날짜를
        거래일 식별자로 사용해 콜백을 호출한다(휴장 가드 vacuous).
        """
        loop = _make_loop(with_mcs=False, today="20260622")
        await loop._run_job()
        loop._on_market_closed.assert_awaited_once_with("20260622")


    async def test_calls_callback_with_latest_date(self):
        """_run_job이 콜백을 latest_trading_date와 함께 호출한다."""
        loop = _make_loop()
        await loop._run_job()
        loop._on_market_closed.assert_awaited_once_with("20260420")

    async def test_skips_callback_on_holiday(self):
        """오늘이 휴장일이면 콜백을 호출하지 않는다."""
        loop = _make_loop(today="20260420", latest_trading_date="20260419")
        await loop._run_job()
        loop._on_market_closed.assert_not_awaited()

    async def test_skips_callback_when_no_trading_date(self):
        """get_latest_trading_date가 None이면 콜백을 호출하지 않는다."""
        loop = _make_loop(latest_trading_date=None)
        await loop._run_job()
        loop._on_market_closed.assert_not_awaited()

    async def test_saves_last_run_date_after_callback(self):
        """콜백 성공 후 store에 last_run_date를 저장한다."""
        loop = _make_loop(with_store=True, last_run=None)
        await loop._run_job()
        loop._store.save_keyed.assert_called_once_with("after_market_last_run_Test", "20260420")

    async def test_does_not_save_on_holiday(self):
        """휴장일에는 store를 업데이트하지 않는다."""
        loop = _make_loop(
            today="20260420", latest_trading_date="20260419",
            with_store=True,
        )
        await loop._run_job()
        loop._store.save_keyed.assert_not_called()

    async def test_padding_delay_applied(self):
        """delay_sec > 0이면 asyncio.sleep이 호출된다."""
        loop = _make_loop(delay_sec=300)
        with patch("scheduler.after_market_loop.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await loop._run_job()
            mock_sleep.assert_awaited_once_with(300)

    async def test_callback_error_does_not_propagate(self):
        """콜백이 예외를 던져도 _run_job 자체는 예외를 전파하지 않는다."""
        loop = _make_loop()
        loop._on_market_closed.side_effect = ValueError("test error")
        await loop._run_job()  # 예외 없이 완료되어야 한다


# ── shutdown / stop ──

class TestShutdown:

    def test_shutdown_sets_stop_event(self):
        """shutdown()이 _stop_event를 설정한다."""
        loop = _make_loop()
        assert not loop._stop_event.is_set()
        loop.shutdown()
        assert loop._stop_event.is_set()

    async def test_stop_sets_stop_event(self):
        """async stop()도 _stop_event를 설정한다."""
        loop = _make_loop()
        await loop.stop()
        assert loop._stop_event.is_set()


# ── run_after_market_loop 래퍼 ──

class TestRunAfterMarketLoopWrapper:

    async def test_cancelled_error_calls_shutdown(self):
        """CancelledError 발생 시 shutdown()이 호출된다."""
        with patch("scheduler.after_market_loop.AfterMarketLoop") as MockClass:
            instance = MagicMock()
            instance.start = AsyncMock(side_effect=asyncio.CancelledError)
            instance.shutdown = MagicMock()
            MockClass.return_value = instance

            with pytest.raises(asyncio.CancelledError):
                await run_after_market_loop(
                    mcs=None, market_clock=None, logger=MagicMock(),
                    on_market_closed=AsyncMock(), label="Test",
                )

            instance.shutdown.assert_called_once()

    async def test_exception_calls_shutdown(self):
        """일반 예외 발생 시에도 shutdown()이 호출된다."""
        with patch("scheduler.after_market_loop.AfterMarketLoop") as MockClass:
            instance = MagicMock()
            instance.start = AsyncMock(side_effect=RuntimeError("boom"))
            instance.shutdown = MagicMock()
            MockClass.return_value = instance

            with pytest.raises(RuntimeError):
                await run_after_market_loop(
                    mcs=None, market_clock=None, logger=MagicMock(),
                    on_market_closed=AsyncMock(), label="Test",
                )

            instance.shutdown.assert_called_once()


# ── _load_after_market_delays ──

class TestLoadAfterMarketDelays:

    @pytest.fixture(autouse=True)
    def clear_cache(self):
        """테스트 간 간섭을 막기 위해 전역 캐시를 매번 초기화합니다."""
        loader_module._CACHED.clear()
        yield
        loader_module._CACHED.clear()

    def test_load_delays_converts_minutes_to_seconds(self):
        """분 단위 설정이 초 단위로 올바르게 변환되며 문자열도 int로 캐스팅된다."""
        yaml_content = """
        after_market_tasks:
          after_market_delay_min:
            task_a: 5
            task_b: "10"
        """
        with patch("builtins.open", mock_open(read_data=yaml_content)):
            delays = _load_after_market_delays()
            assert delays == {"task_a": 300, "task_b": 600}

    def test_load_delays_empty_yaml(self):
        """YAML 파일이 비어있거나 키가 없으면 빈 딕셔너리를 반환한다."""
        with patch("builtins.open", mock_open(read_data="")):
            delays = _load_after_market_delays()
            assert delays == {}

    def test_load_delays_file_not_found(self):
        """파일이 존재하지 않는 등 예외 발생 시 빈 딕셔너리를 반환한다."""
        with patch("builtins.open", side_effect=FileNotFoundError):
            delays = _load_after_market_delays()
            assert delays == {}

    def test_load_delays_uses_cache(self):
        """최초 로드 이후에는 캐시된 데이터를 반환하여 I/O를 수행하지 않는다."""
        loader_module._CACHED["cached_task"] = 120

        with patch("builtins.open") as mock_file:
            delays = _load_after_market_delays()
            assert delays == {"cached_task": 120}
            mock_file.assert_not_called()
