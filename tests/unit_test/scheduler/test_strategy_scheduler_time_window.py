"""StrategySchedulerConfig.skip_minutes_after_open / skip_minutes_before_close 게이트 테스트.

- 장 시작 +N분 이내: scan() skip
- 장 마감 -M분 이내: scan() skip
- check_exits, force_exit 경로는 영향 없음
- skip_minutes_*=0 이면 게이트 우회
"""
import tempfile
import shutil
import unittest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytz

from common.types import ErrorCode, ResCommonResponse, TradeSignal
from interfaces.live_strategy import LiveStrategy
from scheduler.strategy_scheduler import (
    StrategyScheduler,
    StrategySchedulerConfig,
)
from scheduler.strategy_scheduler_store import StrategySchedulerStore

KST = pytz.timezone("Asia/Seoul")


class MockStrategy(LiveStrategy):
    def __init__(self, name="테스트전략"):
        self._name = name
        self._scan_mock = AsyncMock(return_value=[])
        self._exits_mock = AsyncMock(return_value=[])

    @property
    def name(self) -> str:
        return self._name

    async def scan(self):
        return await self._scan_mock()

    async def check_exits(self, holdings):
        return await self._exits_mock(holdings)


def _kst(h, m, date="2026-01-15"):
    y, mo, d = (int(x) for x in date.split("-"))
    return KST.localize(datetime(y, mo, d, h, m))


def _make_scheduler(now_dt):
    vm = MagicMock()
    vm.get_holds_by_strategy.return_value = []
    oes = MagicMock()
    oes.poll_active_orders_once = AsyncMock(return_value=0)
    oes.check_stuck_orders_once = AsyncMock(return_value=0)
    oes.get_active_order_poll_interval_sec = MagicMock(
        return_value=StrategyScheduler.ORDER_POLL_INTERVAL_SEC
    )
    sqs = MagicMock()
    scm = MagicMock()
    mcs = AsyncMock()

    tm = MagicMock()
    tm.get_current_kst_time.return_value = now_dt
    open_dt = KST.localize(datetime(now_dt.year, now_dt.month, now_dt.day, 9, 0))
    close_dt = KST.localize(datetime(now_dt.year, now_dt.month, now_dt.day, 15, 40))
    tm.get_market_open_time.return_value = open_dt
    tm.get_market_close_time.return_value = close_dt

    mock_store = MagicMock(spec=StrategySchedulerStore)
    mock_store.load_signal_history.return_value = []

    scheduler = StrategyScheduler(
        virtual_trade_service=vm,
        order_execution_service=oes,
        stock_query_service=sqs,
        stock_code_repository=scm,
        market_clock=tm,
        market_calendar_service=mcs,
        logger=MagicMock(),
        dry_run=True,
        store=mock_store,
    )
    return scheduler


class TestTimeWindowGate(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._scheduler = None

    def tearDown(self):
        if self._scheduler is not None:
            self._scheduler.close()
        shutil.rmtree(self.tmp)

    def test_skip_after_open_blocks_within_window(self):
        """장 시작 09:00 + 5분 가드 → 09:04 에는 차단."""
        scheduler = _make_scheduler(_kst(9, 4))
        self._scheduler = scheduler
        cfg = StrategySchedulerConfig(strategy=MockStrategy(), skip_minutes_after_open=5)
        self.assertTrue(scheduler._is_scan_time_window_blocked(cfg))

    def test_skip_after_open_passes_outside_window(self):
        """장 시작 09:00 + 5분 가드 → 09:06 에는 통과."""
        scheduler = _make_scheduler(_kst(9, 6))
        self._scheduler = scheduler
        cfg = StrategySchedulerConfig(strategy=MockStrategy(), skip_minutes_after_open=5)
        self.assertFalse(scheduler._is_scan_time_window_blocked(cfg))

    def test_skip_before_close_blocks_within_window(self):
        """장 마감 15:40 - 10분 가드 → 15:35 에는 차단."""
        scheduler = _make_scheduler(_kst(15, 35))
        self._scheduler = scheduler
        cfg = StrategySchedulerConfig(strategy=MockStrategy(), skip_minutes_before_close=10)
        self.assertTrue(scheduler._is_scan_time_window_blocked(cfg))

    def test_skip_before_close_passes_outside_window(self):
        """장 마감 15:40 - 10분 가드 → 15:29 에는 통과."""
        scheduler = _make_scheduler(_kst(15, 29))
        self._scheduler = scheduler
        cfg = StrategySchedulerConfig(strategy=MockStrategy(), skip_minutes_before_close=10)
        self.assertFalse(scheduler._is_scan_time_window_blocked(cfg))

    def test_zero_setting_bypasses_gate(self):
        """skip_minutes_*=0 이면 항상 통과."""
        scheduler = _make_scheduler(_kst(9, 0))  # 정확히 개장 시각
        self._scheduler = scheduler
        cfg = StrategySchedulerConfig(strategy=MockStrategy())  # both 0
        self.assertFalse(scheduler._is_scan_time_window_blocked(cfg))

    async def test_run_strategy_skips_scan_when_blocked(self):
        """게이트 차단 시 scan() 호출되지 않음. check_exits 는 영향 없음."""
        scheduler = _make_scheduler(_kst(9, 4))
        self._scheduler = scheduler
        strategy = MockStrategy()
        cfg = StrategySchedulerConfig(strategy=strategy, skip_minutes_after_open=5)
        await scheduler._run_strategy(cfg, force_exit_only=False)
        strategy._scan_mock.assert_not_called()  # scan 차단

    async def test_run_strategy_runs_scan_when_outside_window(self):
        """게이트 외 시간이면 scan() 정상 호출."""
        scheduler = _make_scheduler(_kst(10, 0))
        self._scheduler = scheduler
        strategy = MockStrategy()
        cfg = StrategySchedulerConfig(strategy=strategy, skip_minutes_after_open=5)
        await scheduler._run_strategy(cfg, force_exit_only=False)
        strategy._scan_mock.assert_awaited_once()

    async def test_force_exit_bypasses_time_window(self):
        """force_exit_only=True 면 시간 게이트 무시."""
        scheduler = _make_scheduler(_kst(15, 35))
        scheduler._force_liquidate_strategy = AsyncMock()
        self._scheduler = scheduler
        strategy = MockStrategy()
        cfg = StrategySchedulerConfig(
            strategy=strategy,
            skip_minutes_before_close=10,
            force_exit_on_close=True,
        )
        await scheduler._run_strategy(cfg, force_exit_only=True)
        scheduler._force_liquidate_strategy.assert_awaited_once_with(cfg, sell_fraction=1.0)
        strategy._scan_mock.assert_not_called()  # force_exit 경로는 어차피 scan 안 함
