# tests/unit_test/test_strategy_scheduler.py
import unittest
from unittest.mock import MagicMock, AsyncMock, patch
from common.types import TradeSignal, ErrorCode, ResCommonResponse
from scheduler.strategy_scheduler import StrategyScheduler, StrategySchedulerConfig
from interfaces.live_strategy import LiveStrategy


class MockStrategy(LiveStrategy):
    """테스트용 전략 구현."""

    def __init__(self, name="테스트전략", scan_signals=None, exit_signals=None):
        self._name = name
        self._scan_signals = scan_signals or []
        self._exit_signals = exit_signals or []

    @property
    def name(self) -> str:
        return self._name

    async def scan(self):
        return self._scan_signals

    async def check_exits(self, holdings):
        return self._exit_signals


class TestStrategyScheduler(unittest.IsolatedAsyncioTestCase):

    def _make_scheduler(self, dry_run=True):
        vm = MagicMock()
        vm.get_holds_by_strategy.return_value = []
        vm.log_buy = MagicMock()
        vm.log_sell_by_strategy = MagicMock()

        oes = MagicMock()
        oes.handle_place_buy_order = AsyncMock(
            return_value=ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="OK")
        )
        oes.handle_place_sell_order = AsyncMock(
            return_value=ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="OK")
        )

        tm = MagicMock()
        tm.is_market_open.return_value = True

        scheduler = StrategyScheduler(
            virtual_manager=vm,
            order_execution_service=oes,
            time_manager=tm,
            dry_run=dry_run,
        )
        return scheduler, vm, oes, tm

    def test_register_strategy(self):
        """전략 등록이 정상 동작하는지 테스트."""
        scheduler, _, _, _ = self._make_scheduler()
        strategy = MockStrategy()
        config = StrategySchedulerConfig(strategy=strategy, interval_minutes=5)
        scheduler.register(config)

        self.assertEqual(len(scheduler._strategies), 1)
        self.assertEqual(scheduler._strategies[0].strategy.name, "테스트전략")

    def test_get_status_empty(self):
        """전략 미등록 상태에서 get_status 테스트."""
        scheduler, _, _, _ = self._make_scheduler()
        status = scheduler.get_status()

        self.assertFalse(status["running"])
        self.assertEqual(len(status["strategies"]), 0)

    def test_get_status_with_strategy(self):
        """전략 등록 후 get_status 테스트."""
        scheduler, _, _, _ = self._make_scheduler()
        strategy = MockStrategy()
        scheduler.register(StrategySchedulerConfig(strategy=strategy, max_positions=3))

        status = scheduler.get_status()
        self.assertEqual(len(status["strategies"]), 1)
        self.assertEqual(status["strategies"][0]["name"], "테스트전략")
        self.assertEqual(status["strategies"][0]["max_positions"], 3)

    async def test_execute_buy_signal_dry_run(self):
        """dry_run 모드에서 BUY 시그널 실행: CSV만 기록, API 미호출."""
        scheduler, vm, oes, _ = self._make_scheduler(dry_run=True)

        signal = TradeSignal(
            code="005930", name="삼성전자", action="BUY",
            price=70000, qty=1, reason="테스트", strategy_name="테스트전략"
        )
        await scheduler._execute_signal(signal)

        vm.log_buy.assert_called_once_with("테스트전략", "005930", 70000)
        oes.handle_place_buy_order.assert_not_called()

    async def test_execute_sell_signal_dry_run(self):
        """dry_run 모드에서 SELL 시그널 실행: CSV만 기록."""
        scheduler, vm, oes, _ = self._make_scheduler(dry_run=True)

        signal = TradeSignal(
            code="005930", name="삼성전자", action="SELL",
            price=72000, qty=1, reason="익절", strategy_name="테스트전략"
        )
        await scheduler._execute_signal(signal)

        vm.log_sell_by_strategy.assert_called_once_with("테스트전략", "005930", 72000)
        oes.handle_place_sell_order.assert_not_called()

    async def test_execute_buy_signal_with_api(self):
        """dry_run=False: CSV 기록 + API 주문 모두 실행."""
        scheduler, vm, oes, _ = self._make_scheduler(dry_run=False)

        signal = TradeSignal(
            code="005930", name="삼성전자", action="BUY",
            price=70000, qty=1, reason="테스트", strategy_name="테스트전략"
        )
        await scheduler._execute_signal(signal)

        vm.log_buy.assert_called_once_with("테스트전략", "005930", 70000)
        oes.handle_place_buy_order.assert_called_once_with("005930", 70000, 1)

    async def test_run_strategy_scan_respects_max_positions(self):
        """max_positions에 도달하면 스캔을 스킵하는지 테스트."""
        scheduler, vm, _, _ = self._make_scheduler()

        # 이미 max_positions만큼 보유 중
        vm.get_holds_by_strategy.return_value = [
            {"code": "005930", "buy_price": 70000},
            {"code": "000660", "buy_price": 120000},
            {"code": "035420", "buy_price": 300000},
        ]

        buy_signal = TradeSignal(
            code="999999", name="테스트", action="BUY",
            price=10000, qty=1, reason="스캔", strategy_name="테스트전략"
        )
        strategy = MockStrategy(scan_signals=[buy_signal])
        config = StrategySchedulerConfig(strategy=strategy, max_positions=3)
        scheduler.register(config)

        await scheduler._run_strategy(config)

        # max_positions 도달 → log_buy 호출 안됨
        vm.log_buy.assert_not_called()

    async def test_run_strategy_processes_exit_signals(self):
        """보유 종목의 청산 시그널이 실행되는지 테스트."""
        scheduler, vm, _, _ = self._make_scheduler()

        holdings = [{"code": "005930", "buy_price": 70000}]
        vm.get_holds_by_strategy.return_value = holdings

        sell_signal = TradeSignal(
            code="005930", name="삼성전자", action="SELL",
            price=80000, qty=1, reason="익절", strategy_name="테스트전략"
        )
        strategy = MockStrategy(exit_signals=[sell_signal])
        config = StrategySchedulerConfig(strategy=strategy, max_positions=3)

        await scheduler._run_strategy(config)

        vm.log_sell_by_strategy.assert_called_once_with("테스트전략", "005930", 80000)

    async def test_run_strategy_limits_buys_to_remaining_slots(self):
        """남은 슬롯 수만큼만 매수 시그널을 실행하는지 테스트."""
        scheduler, vm, _, _ = self._make_scheduler()

        # 1개 보유 중, max_positions=2
        vm.get_holds_by_strategy.side_effect = [
            [{"code": "005930", "buy_price": 70000}],  # check_exits 호출
            [{"code": "005930", "buy_price": 70000}],  # scan 전 보유수 확인
        ]

        buy_signals = [
            TradeSignal(code="000660", name="SK하이닉스", action="BUY",
                        price=120000, qty=1, reason="테스트1", strategy_name="테스트전략"),
            TradeSignal(code="035420", name="NAVER", action="BUY",
                        price=300000, qty=1, reason="테스트2", strategy_name="테스트전략"),
        ]
        strategy = MockStrategy(scan_signals=buy_signals)
        config = StrategySchedulerConfig(strategy=strategy, max_positions=2)

        await scheduler._run_strategy(config)

        # remaining=1이므로 첫 번째만 매수
        self.assertEqual(vm.log_buy.call_count, 1)
        vm.log_buy.assert_called_with("테스트전략", "000660", 120000)

    async def test_start_and_stop(self):
        """스케줄러 start/stop 생명주기 테스트."""
        scheduler, _, _, _ = self._make_scheduler()

        self.assertFalse(scheduler._running)
        await scheduler.start()
        self.assertTrue(scheduler._running)
        self.assertIsNotNone(scheduler._task)

        await scheduler.stop()
        self.assertFalse(scheduler._running)

    async def test_signal_history_recorded(self):
        """시그널 실행 시 이력이 기록되는지 테스트."""
        scheduler, vm, oes, tm = self._make_scheduler(dry_run=True)

        import pytz
        from datetime import datetime
        kst = pytz.timezone("Asia/Seoul")
        tm.get_current_kst_time.return_value = kst.localize(datetime(2026, 2, 20, 10, 30))

        signal = TradeSignal(
            code="005930", name="삼성전자", action="BUY",
            price=70000, qty=1, reason="테스트매수", strategy_name="테스트전략"
        )
        await scheduler._execute_signal(signal)

        history = scheduler.get_signal_history()
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["code"], "005930")
        self.assertEqual(history[0]["action"], "BUY")
        self.assertEqual(history[0]["reason"], "테스트매수")
        self.assertEqual(history[0]["timestamp"], "2026-02-20 10:30:00")

    async def test_signal_history_filter_by_strategy(self):
        """전략별 이력 필터링 테스트."""
        scheduler, vm, oes, tm = self._make_scheduler(dry_run=True)

        import pytz
        from datetime import datetime
        kst = pytz.timezone("Asia/Seoul")
        tm.get_current_kst_time.return_value = kst.localize(datetime(2026, 2, 20, 11, 0))

        for name, strategy_name in [("삼성전자", "전략A"), ("SK하이닉스", "전략B")]:
            signal = TradeSignal(
                code="005930", name=name, action="BUY",
                price=70000, qty=1, reason="테스트", strategy_name=strategy_name
            )
            await scheduler._execute_signal(signal)

        all_history = scheduler.get_signal_history()
        self.assertEqual(len(all_history), 2)

        filtered = scheduler.get_signal_history("전략A")
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0]["strategy_name"], "전략A")


if __name__ == "__main__":
    unittest.main()
