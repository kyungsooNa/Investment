"""
run_after_market_loop 공통 스케줄러 단위 테스트.
장 중 대기, 장 마감 후 콜백 실행, 스마트 대기 로직 검증.
"""
import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock, patch, mock_open

from scheduler.after_market_loop import run_after_market_loop, _smart_sleep
import task.background.after_market.after_market_task_base as base_module
from task.background.after_market.after_market_task_base import _load_after_market_delays


# --- _smart_sleep 테스트 ---


class TestSmartSleep:

    async def test_sleep_until_market_close(self):
        """장 마감 전이면 마감+1분까지 대기."""
        tm = MagicMock()
        tm.get_sleep_seconds_until_market_close.return_value = 100.0

        with patch("scheduler.after_market_loop.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await _smart_sleep(tm, MagicMock(), "Test")
            mock_sleep.assert_awaited_once_with(160.0)  # 100 + 60

    async def test_sleep_12h_after_market_close(self):
        """장 마감 후이면 12시간 대기."""
        tm = MagicMock()
        tm.get_sleep_seconds_until_market_close.return_value = 0.0

        with patch("scheduler.after_market_loop.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await _smart_sleep(tm, MagicMock(), "Test")
            mock_sleep.assert_awaited_once_with(12 * 3600)

    async def test_sleep_12h_without_market_clock(self):
        """MarketClock 없으면 12시간 대기."""
        with patch("scheduler.after_market_loop.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await _smart_sleep(None, MagicMock(), "Test")
            mock_sleep.assert_awaited_once_with(12 * 3600)


# --- run_after_market_loop 테스트 ---


class TestRunAfterMarketLoop:

    async def test_calls_callback_after_market_close(self):
        """장 마감 후 콜백이 latest_trading_date와 함께 호출된다."""
        mcs = MagicMock()
        mcs.is_market_open_now = AsyncMock(return_value=False)
        mcs.get_latest_trading_date = AsyncMock(return_value="20260318")

        callback = AsyncMock()
        call_count = 0

        async def _tracked_callback(date):
            nonlocal call_count
            await callback(date)
            call_count += 1
            if call_count >= 1:
                raise asyncio.CancelledError()  # 1회 실행 후 루프 종료

        await run_after_market_loop(
            mcs=mcs,
            market_clock=None,
            logger=MagicMock(),
            on_market_closed=_tracked_callback,
            label="Test",
        )

        callback.assert_awaited_once_with("20260318")

    async def test_waits_during_market_hours(self):
        """장 중이면 마감까지 대기 후 continue."""
        mcs = MagicMock()
        call_count = 0

        async def _mock_is_market_open():
            nonlocal call_count
            call_count += 1
            if call_count <= 1:
                return True  # 첫 번째: 장 중
            return False  # 두 번째: 장 마감

        mcs.is_market_open_now = AsyncMock(side_effect=_mock_is_market_open)
        mcs.get_latest_trading_date = AsyncMock(return_value="20260318")

        tm = MagicMock()
        tm.get_sleep_seconds_until_market_close.return_value = 0.1  # 아주 짧게

        callback_called = False

        async def _callback(date):
            nonlocal callback_called
            callback_called = True
            raise asyncio.CancelledError()

        await run_after_market_loop(
            mcs=mcs, market_clock=tm, logger=MagicMock(),
            on_market_closed=_callback, label="Test",
        )

        assert callback_called

    async def test_skips_when_no_trading_date(self):
        """거래일이 없으면 콜백을 호출하지 않는다."""
        mcs = MagicMock()
        mcs.is_market_open_now = AsyncMock(return_value=False)
        mcs.get_latest_trading_date = AsyncMock(return_value=None)

        callback = AsyncMock()
        loop_count = 0

        original_smart_sleep = _smart_sleep

        async def _mock_smart_sleep(tm, logger, label):
            nonlocal loop_count
            loop_count += 1
            if loop_count >= 1:
                raise asyncio.CancelledError()

        with patch("scheduler.after_market_loop._smart_sleep", side_effect=_mock_smart_sleep):
            await run_after_market_loop(
                mcs=mcs, market_clock=None, logger=MagicMock(),
                on_market_closed=callback, label="Test",
            )

        callback.assert_not_awaited()

    async def test_recovers_from_callback_error(self):
        """콜백 에러 시 60초 후 재시도한다."""
        mcs = MagicMock()
        mcs.is_market_open_now = AsyncMock(return_value=False)
        mcs.get_latest_trading_date = AsyncMock(return_value="20260318")

        call_count = 0

        async def _failing_callback(date):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ValueError("test error")
            raise asyncio.CancelledError()  # 2회차에서 종료

        with patch("scheduler.after_market_loop.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await run_after_market_loop(
                mcs=mcs, market_clock=None, logger=MagicMock(),
                on_market_closed=_failing_callback, label="Test",
            )

        assert call_count == 2
        # 에러 후 60초 대기가 호출되었는지 확인
        mock_sleep.assert_any_await(60)


# --- _load_after_market_delays 테스트 ---

class TestLoadAfterMarketDelays:

    @pytest.fixture(autouse=True)
    def clear_cache(self):
        """테스트 간 간섭을 막기 위해 전역 캐시를 매번 초기화합니다."""
        base_module._DEFAULT_DELAYS.clear()
        yield
        base_module._DEFAULT_DELAYS.clear()

    def test_load_delays_converts_minutes_to_seconds(self):
        """분 단위 설정이 초 단위로 올바르게 변환되며 문자열도 int로 캐스팅된다."""
        yaml_content = """
        after_market_tasks:
          after_market_delay_sec:
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
        base_module._DEFAULT_DELAYS["cached_task"] = 120
        
        with patch("builtins.open") as mock_file:
            delays = _load_after_market_delays()
            assert delays == {"cached_task": 120}
            mock_file.assert_not_called()
