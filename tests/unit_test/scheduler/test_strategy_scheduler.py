# tests/unit_test/test_strategy_scheduler.py
import unittest
import asyncio
import tempfile
import shutil
import json
from unittest.mock import MagicMock, AsyncMock, patch, mock_open, call
from common.types import TradeSignal, ErrorCode, ResCommonResponse, Exchange, OrderState
from scheduler.strategy_scheduler import StrategyScheduler, StrategySchedulerConfig, SignalRecord
from scheduler.strategy_scheduler_store import StrategySchedulerStore
from interfaces.live_strategy import LiveStrategy
from services.notification_service import NotificationCategory, NotificationLevel


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

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self._scheduler = None  # tearDown에서 close() 호출용

    def tearDown(self):
        if hasattr(self, "_scheduler") and self._scheduler is not None:
            self._scheduler.close()
            self._scheduler = None
        shutil.rmtree(self.test_dir)

    def _make_scheduler(self, dry_run=True):
        vm = MagicMock()
        vm.get_holds_by_strategy.return_value = []
        vm.log_buy_async = AsyncMock(return_value=True)
        vm.log_sell_by_strategy_async = AsyncMock(return_value=True)

        oes = MagicMock()
        oes.handle_place_buy_order = AsyncMock(
            return_value=ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="OK")
        )
        oes.handle_place_sell_order = AsyncMock(
            return_value=ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="OK")
        )
        oes.resolve_submitted_order = AsyncMock()
        oes.poll_active_orders_once = AsyncMock(return_value=0)
        oes.check_stuck_orders_once = AsyncMock(return_value=0)
        oes.get_active_order_poll_interval_sec = MagicMock(return_value=StrategyScheduler.ORDER_POLL_INTERVAL_SEC)

        sqs = MagicMock()
        sqs.get_current_price = AsyncMock(
            return_value=ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data={"output": {"stck_prpr": "60000"}})
        )

        scm = MagicMock()
        scm.get_stock_code = AsyncMock(
            return_value="005930"
        )

        tm = MagicMock()
        tm.is_market_operating_hours.return_value = True
        tm.get_current_kst_time.return_value.strftime.return_value = "2023-01-01 10:00:00"

        mcs = AsyncMock()
        mcs.is_market_open_now.return_value = True
        mcs.wait_until_next_open = AsyncMock()

        mock_logger = MagicMock()

        # SQLite I/O 없이 테스트 격리: mock store 주입
        mock_store = MagicMock(spec=StrategySchedulerStore)
        mock_store.load_signal_history.return_value = []

        scheduler = StrategyScheduler(
            virtual_trade_service=vm,
            order_execution_service=oes,
            stock_query_service=sqs,
            stock_code_repository=scm,
            market_clock=tm,
            market_calendar_service=mcs,
            logger=mock_logger,
            dry_run=dry_run,
            store=mock_store,
        )
        self._scheduler = scheduler
        return scheduler, vm, oes, tm, mcs

    def test_register_strategy(self):
        """전략 등록이 정상 동작하는지 테스트."""
        scheduler, _, _, _, _ = self._make_scheduler()
        strategy = MockStrategy()
        config = StrategySchedulerConfig(strategy=strategy, interval_minutes=5)
        scheduler.register(config)

        self.assertEqual(len(scheduler._strategies), 1)
        self.assertEqual(scheduler._strategies[0].strategy.name, "테스트전략")

    async def test_poll_active_orders_if_due_calls_order_service(self):
        from datetime import datetime

        scheduler, _, oes, _, _ = self._make_scheduler(dry_run=False)
        oes.poll_active_orders_once.return_value = 2

        result = await scheduler._poll_active_orders_if_due(datetime(2026, 4, 23, 10, 0, 0))

        self.assertEqual(result, 2)
        oes.poll_active_orders_once.assert_awaited_once()
        oes.check_stuck_orders_once.assert_awaited_once_with(datetime(2026, 4, 23, 10, 0, 0))
        scheduler._logger.info.assert_called_with("[Scheduler] 활성 주문 polling 보정: 2건")

    async def test_poll_active_orders_if_due_respects_interval(self):
        from datetime import datetime, timedelta

        scheduler, _, oes, _, _ = self._make_scheduler(dry_run=False)
        first = datetime(2026, 4, 23, 10, 0, 0)

        first_result = await scheduler._poll_active_orders_if_due(first)
        second_result = await scheduler._poll_active_orders_if_due(first + timedelta(seconds=5))

        self.assertEqual(first_result, 0)
        self.assertEqual(second_result, 0)
        self.assertEqual(oes.poll_active_orders_once.await_count, 1)

    async def test_poll_active_orders_if_due_skips_dry_run(self):
        from datetime import datetime

        scheduler, _, oes, _, _ = self._make_scheduler(dry_run=True)

        result = await scheduler._poll_active_orders_if_due(datetime(2026, 4, 23, 10, 0, 0))

        self.assertEqual(result, 0)
        oes.poll_active_orders_once.assert_not_awaited()
        oes.check_stuck_orders_once.assert_not_awaited()

    async def test_poll_active_orders_if_due_uses_fast_fallback_interval(self):
        from datetime import datetime, timedelta

        scheduler, _, oes, _, _ = self._make_scheduler(dry_run=False)
        oes.get_active_order_poll_interval_sec.return_value = 5
        first = datetime(2026, 4, 23, 10, 0, 0)

        first_result = await scheduler._poll_active_orders_if_due(first)
        second_result = await scheduler._poll_active_orders_if_due(first + timedelta(seconds=5))

        self.assertEqual(first_result, 0)
        self.assertEqual(second_result, 0)
        self.assertEqual(oes.poll_active_orders_once.await_count, 2)

    async def test_poll_active_orders_if_due_skips_when_no_active_order_exists(self):
        from datetime import datetime

        scheduler, _, oes, _, _ = self._make_scheduler(dry_run=False)
        oes.get_active_order_poll_interval_sec.return_value = None

        result = await scheduler._poll_active_orders_if_due(datetime(2026, 4, 23, 10, 0, 0))

        self.assertEqual(result, 0)
        oes.poll_active_orders_once.assert_not_awaited()

    def test_get_status_with_holdings(self):
        """전략 등록 및 보유 종목이 있는 경우 get_status 테스트."""
        scheduler, vm, _, _, _ = self._make_scheduler()
        strategy = MockStrategy(name="전략A")
        config = StrategySchedulerConfig(strategy=strategy, interval_minutes=10, max_positions=5)
        scheduler.register(config)

        # Mock VirtualTradeRepository 특정 전략의 보유 종목을 반환하도록 설정
        holding_item = {"code": "005930", "name": "삼성전자", "buy_price": 70000, "qty": 1, "status": "HOLD"}
        vm.get_holds_by_strategy.return_value = [holding_item]

        # 실행 중인 상태로 가정
        scheduler._running = True
        status = scheduler.get_status()

        self.assertTrue(status["running"])
        self.assertEqual(len(status["strategies"]), 1)
        strat_status = status["strategies"][0]
        self.assertEqual(strat_status["name"], "전략A")
        self.assertEqual(strat_status["current_holds"], 1)
        self.assertEqual(strat_status["holdings"], [holding_item])
        self.assertEqual(strat_status["max_positions"], 5)
        self.assertEqual(strat_status["interval_minutes"], 10)

    def test_get_status_empty(self):
        """전략 미등록 상태에서 get_status 테스트."""
        scheduler, _, _, _, _ = self._make_scheduler()
        status = scheduler.get_status()

        self.assertFalse(status["running"])
        self.assertEqual(len(status["strategies"]), 0)

    def test_get_status_with_strategy(self):
        """전략 등록 후 get_status 테스트."""
        scheduler, _, _, _, _ = self._make_scheduler()
        strategy = MockStrategy()
        scheduler.register(StrategySchedulerConfig(strategy=strategy, max_positions=3))

        status = scheduler.get_status()
        self.assertEqual(len(status["strategies"]), 1)
        self.assertEqual(status["strategies"][0]["name"], "테스트전략")
        self.assertEqual(status["strategies"][0]["max_positions"], 3)

    async def test_execute_buy_signal_dry_run(self):
        """dry_run 모드에서 BUY 시그널 실행: CSV만 기록, API 미호출."""
        scheduler, vm, oes, _, _ = self._make_scheduler(dry_run=True)

        signal = TradeSignal(
            code="005930", name="삼성전자", action="BUY",
            price=70000, qty=1, reason="테스트", strategy_name="테스트전략"
        )
        await scheduler._execute_signal(signal)

        vm.log_buy_async.assert_awaited_once_with("테스트전략", "005930", 70000, 1)
        oes.handle_place_buy_order.assert_not_called()

    async def test_execute_sell_signal_dry_run(self):
        """dry_run 모드에서 SELL 시그널 실행: CSV만 기록."""
        scheduler, vm, oes, _, _ = self._make_scheduler(dry_run=True)

        signal = TradeSignal(
            code="005930", name="삼성전자", action="SELL",
            price=72000, qty=1, reason="익절", strategy_name="테스트전략"
        )
        await scheduler._execute_signal(signal)

        vm.log_sell_by_strategy_async.assert_awaited_once_with("테스트전략", "005930", 72000, 1)
        oes.handle_place_sell_order.assert_not_called()

    async def test_execute_buy_signal_with_api(self):
        """dry_run=False: API 주문만 실행하고 가상매매 기록은 체결 확인에 맡긴다."""
        scheduler, vm, oes, _, _ = self._make_scheduler(dry_run=False)

        signal = TradeSignal(
            code="005930", name="삼성전자", action="BUY",
            price=70000, qty=1, reason="테스트", strategy_name="테스트전략"
        )
        await scheduler._execute_signal(signal)

        vm.log_buy_async.assert_not_awaited()
        oes.handle_place_buy_order.assert_called_once_with(
            "005930",
            70000,
            1,
            exchange=Exchange.KRX,
            source="strategy:테스트전략",
            finalize_immediately=False,
        )

    async def test_run_strategy_scan_respects_max_positions(self):
        """max_positions에 도달하면 스캔을 스킵하는지 테스트."""
        scheduler, vm, _, _, _ = self._make_scheduler()

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
        vm.log_buy_async.assert_not_called()

    async def test_run_strategy_scan_respects_max_positions_growing_holdings(self):
        """remaining 슬라이싱으로 max_positions 초과 매수를 방지하는지 테스트.

        초기 보유 1개, max_positions=3 → remaining=2. 3개 신호 중 2개(A,B)만
        target_signals에 포함되어 실행되고, C는 슬라이스 초과로 실행되지 않는다.
        """
        scheduler, vm, _, _, _ = self._make_scheduler()

        h1 = {"code": "005930", "buy_price": 70000}

        # check_exits + scan 전 보유수 확인 (루프 내 DB 재조회 없음)
        vm.get_holds_by_strategy.return_value = [h1]

        buy_signals = [
            TradeSignal(code="A", name="종목A", action="BUY", price=1000, qty=1, reason="t", strategy_name="테스트전략"),
            TradeSignal(code="B", name="종목B", action="BUY", price=2000, qty=1, reason="t", strategy_name="테스트전략"),
            TradeSignal(code="C", name="종목C", action="BUY", price=3000, qty=1, reason="t", strategy_name="테스트전략"),
        ]
        strategy = MockStrategy(scan_signals=buy_signals)
        config = StrategySchedulerConfig(strategy=strategy, max_positions=3)

        await scheduler._run_strategy(config)

        # remaining=2 → target_signals=[A,B] → 2건만 실행
        self.assertEqual(vm.log_buy_async.call_count, 2)
        vm.log_buy_async.assert_any_await("테스트전략", "A", 1000, 1)
        vm.log_buy_async.assert_any_await("테스트전략", "B", 2000, 1)

    async def test_run_strategy_processes_exit_signals(self):
        """보유 종목의 청산 시그널이 실행되는지 테스트."""
        scheduler, vm, _, _, _ = self._make_scheduler()

        holdings = [{"code": "005930", "buy_price": 70000}]
        vm.get_holds_by_strategy.return_value = holdings

        sell_signal = TradeSignal(
            code="005930", name="삼성전자", action="SELL",
            price=80000, qty=1, reason="익절", strategy_name="테스트전략"
        )
        strategy = MockStrategy(exit_signals=[sell_signal])
        config = StrategySchedulerConfig(strategy=strategy, max_positions=3)

        await scheduler._run_strategy(config)

        vm.log_sell_by_strategy_async.assert_awaited_once_with("테스트전략", "005930", 80000, 1)

    async def test_run_strategy_force_exit_calls_force_liquidate(self):
        """force_exit_only=True 시 check_exits가 아닌 _force_liquidate_strategy가 호출되는지 테스트.

        기존 test_run_strategy_processes_exit_signals는 일반 check_exits 경로만 검증.
        장 마감 강제 청산(force_exit_only=True)은 전략의 check_exits에 의존하면 안 되고,
        보유 종목 전량을 시장가로 강제 매도하는 _force_liquidate_strategy를 호출해야 한다.
        """
        scheduler, vm, _, _, _ = self._make_scheduler()

        # 보유 종목 2개 (check_exits는 빈 리스트 반환 → 전략 로직으로는 매도 안 함)
        holdings = [
            {"code": "005930", "name": "삼성전자", "buy_price": 70000, "qty": 1},
            {"code": "000660", "name": "SK하이닉스", "buy_price": 120000, "qty": 1},
        ]
        vm.get_holds_by_strategy.return_value = holdings

        strategy = MockStrategy(exit_signals=[])  # check_exits가 빈 리스트 반환
        config = StrategySchedulerConfig(
            strategy=strategy, max_positions=3, force_exit_on_close=True
        )

        with patch.object(scheduler, '_force_liquidate_strategy', new_callable=AsyncMock) as mock_liq:
            await scheduler._run_strategy(config, force_exit_only=True)

            # _force_liquidate_strategy가 호출되어야 함
            mock_liq.assert_awaited_once_with(config)

    async def test_run_strategy_force_exit_sells_all_holdings(self):
        """force_exit_only=True 시 check_exits와 무관하게 보유 종목 전량이 시장가 매도되는지 테스트.

        check_exits가 '아직 매도 조건 아님'으로 판단해도,
        강제 청산 모드에서는 모든 보유 종목이 시장가(price=0)로 매도되어야 한다.
        """
        scheduler, vm, _, _, _ = self._make_scheduler()

        holdings = [
            {"code": "005930", "name": "삼성전자", "buy_price": 70000, "qty": 10},
            {"code": "000660", "name": "SK하이닉스", "buy_price": 120000, "qty": 5},
        ]
        vm.get_holds_by_strategy.return_value = holdings

        # check_exits가 빈 리스트를 반환해도 강제 청산되어야 함
        strategy = MockStrategy(exit_signals=[])
        config = StrategySchedulerConfig(
            strategy=strategy, max_positions=3, force_exit_on_close=True
        )

        await scheduler._run_strategy(config, force_exit_only=True)

        # 2개 종목 모두 매도 기록되어야 함 (시장가 price=0 → 현재가 조회 후 기록)
        self.assertEqual(vm.log_sell_by_strategy_async.call_count, 2)
        vm.log_sell_by_strategy_async.assert_any_await(
            "테스트전략", "005930", unittest.mock.ANY, 10
        )
        vm.log_sell_by_strategy_async.assert_any_await(
            "테스트전략", "000660", unittest.mock.ANY, 5
        )

    async def test_run_strategy_limits_buys_to_remaining_slots(self):
        """남은 슬롯 수만큼만 매수 시그널을 실행하는지 테스트."""
        scheduler, vm, _, _, _ = self._make_scheduler()

        # 1개 보유 중, max_positions=2 (루프 내 DB 재조회 없음)
        vm.get_holds_by_strategy.return_value = [{"code": "005930", "buy_price": 70000}]

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
        self.assertEqual(vm.log_buy_async.call_count, 1)
        vm.log_buy_async.assert_awaited_with("테스트전략", "000660", 120000, 1)

    async def test_run_strategy_limits_buys_with_realistic_growing_holdings(self):
        """초기 보유수 기반 remaining 슬라이싱으로 정확히 남은 슬롯만큼만 매수하는지 테스트.

        max_positions=3, 초기 1개 보유 → remaining=2 → 3개 신호 중 2개만
        target_signals에 포함. 루프는 in-memory 카운터(1→2→3)로 추적하며 실행.
        """
        scheduler, vm, _, _, _ = self._make_scheduler()

        h1 = {"code": "005930", "buy_price": 70000}

        # check_exits + scan 전 보유수 확인 (루프 내 DB 재조회 없음)
        vm.get_holds_by_strategy.return_value = [h1]

        buy_signals = [
            TradeSignal(code="000660", name="SK하이닉스", action="BUY",
                        price=120000, qty=1, reason="t1", strategy_name="테스트전략"),
            TradeSignal(code="035420", name="NAVER", action="BUY",
                        price=300000, qty=1, reason="t2", strategy_name="테스트전략"),
            TradeSignal(code="068270", name="셀트리온", action="BUY",
                        price=200000, qty=1, reason="t3", strategy_name="테스트전략"),
        ]
        strategy = MockStrategy(scan_signals=buy_signals)
        config = StrategySchedulerConfig(strategy=strategy, max_positions=3)

        await scheduler._run_strategy(config)

        # remaining=2 → target_signals=[000660, 035420] → 2건만 실행
        self.assertEqual(vm.log_buy_async.call_count, 2)
        vm.log_buy_async.assert_any_await("테스트전략", "000660", 120000, 1)
        vm.log_buy_async.assert_any_await("테스트전략", "035420", 300000, 1)
        # 세 번째(셀트리온)은 슬라이스에서 제외되어 매수 안 됨

    async def test_run_strategy_prevents_pyramiding(self):
        """이미 보유 중인 종목에 대한 추가 매수 신호는 무시하는지 테스트."""
        scheduler, vm, _, _, _ = self._make_scheduler()

        # 1개 보유 중 (005930)
        vm.get_holds_by_strategy.return_value = [{"code": "005930", "buy_price": 70000}]

        # 보유 중인 종목(005930)과 새로운 종목(000660) 매수 신호 발생
        buy_signals = [
            TradeSignal(code="005930", name="삼성전자", action="BUY", price=71000, qty=1, reason="추가매수", strategy_name="테스트전략"),
            TradeSignal(code="000660", name="SK하이닉스", action="BUY", price=120000, qty=1, reason="신규매수", strategy_name="테스트전략"),
        ]
        strategy = MockStrategy(scan_signals=buy_signals)
        config = StrategySchedulerConfig(strategy=strategy, max_positions=5) # 슬롯 충분

        await scheduler._run_strategy(config)

        # 005930은 이미 보유 중이므로 매수 실행 안 됨 (호출 횟수 1회)
        # 000660만 매수 실행됨
        self.assertEqual(vm.log_buy_async.call_count, 1)
        vm.log_buy_async.assert_awaited_with("테스트전략", "000660", 120000, 1)

    async def test_run_strategy_recheck_blocks_excess_buys(self):
        """in-memory 카운터로 remaining 슬롯을 소진하면 추가 매수를 차단하는지 테스트."""
        scheduler, vm, _, _, _ = self._make_scheduler()

        # 0개 보유, max_positions=2 → remaining=2 (루프 내 DB 재조회 없음)
        vm.get_holds_by_strategy.return_value = []

        buy_signals = [
            TradeSignal(code="A", name="종목A", action="BUY", price=1000, qty=1, reason="t", strategy_name="테스트전략"),
            TradeSignal(code="B", name="종목B", action="BUY", price=2000, qty=1, reason="t", strategy_name="테스트전략"),
            TradeSignal(code="C", name="종목C", action="BUY", price=3000, qty=1, reason="t", strategy_name="테스트전략"),
        ]
        strategy = MockStrategy(scan_signals=buy_signals)
        config = StrategySchedulerConfig(strategy=strategy, max_positions=2)

        await scheduler._run_strategy(config)

        # remaining=2 → target_signals=[A,B]. in-memory 카운터 0→1→2로 추적.
        # C는 슬라이스 초과로 target_signals에 포함되지 않아 A,B 2건만 실행
        self.assertEqual(vm.log_buy_async.call_count, 2)
        vm.log_buy_async.assert_any_await("테스트전략", "A", 1000, 1)
        vm.log_buy_async.assert_any_await("테스트전략", "B", 2000, 1)

    async def test_run_strategy_allows_pyramiding_if_enabled(self):
        """allow_pyramiding=True일 때 보유 종목 추가 매수 허용 테스트."""
        scheduler, vm, _, _, _ = self._make_scheduler()

        # 1개 보유 중 (005930)
        vm.get_holds_by_strategy.return_value = [{"code": "005930", "buy_price": 70000}]

        # 보유 중인 종목(005930)과 새로운 종목(000660) 매수 신호 발생
        buy_signals = [
            TradeSignal(code="005930", name="삼성전자", action="BUY", price=71000, qty=1, reason="추가매수", strategy_name="테스트전략"),
            TradeSignal(code="000660", name="SK하이닉스", action="BUY", price=120000, qty=1, reason="신규매수", strategy_name="테스트전략"),
        ]
        strategy = MockStrategy(scan_signals=buy_signals)
        config = StrategySchedulerConfig(strategy=strategy, max_positions=5, allow_pyramiding=True)

        await scheduler._run_strategy(config)

        # allow_pyramiding=True이므로 두 종목 모두 매수 실행되어야 함
        self.assertEqual(vm.log_buy_async.call_count, 2)
        vm.log_buy_async.assert_any_await("테스트전략", "005930", 71000, 1)
        vm.log_buy_async.assert_any_await("테스트전략", "000660", 120000, 1)

    async def test_start_and_stop(self):
        """스케줄러 start/stop 생명주기 테스트."""
        scheduler, _, _, _, _ = self._make_scheduler()

        self.assertFalse(scheduler._running)
        await scheduler.start()
        self.assertTrue(scheduler._running)
        self.assertIsNotNone(scheduler._task)

        await scheduler.stop()
        self.assertFalse(scheduler._running)

    async def test_signal_history_recorded(self):
        """시그널 실행 시 이력이 기록되는지 테스트."""
        scheduler, vm, oes, tm, _ = self._make_scheduler(dry_run=True)

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
        scheduler, vm, oes, tm, _ = self._make_scheduler(dry_run=True)

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

    async def test_execute_signal_api_failure(self):
        """API 주문 실패 시 처리 테스트."""
        scheduler, vm, oes, _, _ = self._make_scheduler(dry_run=False)

        # API 실패 응답 설정
        oes.handle_place_buy_order.return_value = ResCommonResponse(
            rt_cd=ErrorCode.API_ERROR.value, msg1="주문 실패"
        )

        signal = TradeSignal(
            code="005930", name="삼성전자", action="BUY",
            price=70000, qty=1, reason="테스트", strategy_name="테스트전략"
        )
        await scheduler._execute_signal(signal)

        # 로그에 경고가 남았는지 확인 (API 주문 실패)
        scheduler._logger.warning.assert_called()
        # 히스토리에 실패로 기록되었는지 확인
        self.assertFalse(scheduler._signal_history[-1].api_success)

    async def test_execute_signal_api_exception(self):
        """API 주문 중 예외 발생 시 처리 테스트."""
        scheduler, vm, oes, _, _ = self._make_scheduler(dry_run=False)
        
        # 로거 Mock 강제 설정 (AttributeError 방지)
        scheduler._logger = MagicMock()

        # API 예외 설정
        oes.handle_place_buy_order.side_effect = Exception("Network Error")

        signal = TradeSignal(
            code="005930", name="삼성전자", action="BUY",
            price=70000, qty=1, reason="테스트", strategy_name="테스트전략"
        )
        await scheduler._execute_signal(signal)

        # 로그에 에러가 남았는지 확인
        scheduler._logger.error.assert_called()
        # 히스토리에 실패로 기록되었는지 확인
        self.assertFalse(scheduler._signal_history[-1].api_success)

    async def test_individual_strategy_control(self):
        """개별 전략 시작/정지 테스트."""
        scheduler, _, _, _, _ = self._make_scheduler()
        strategy = MockStrategy(name="전략A")
        config = StrategySchedulerConfig(strategy=strategy)
        scheduler.register(config)

        # 정지
        self.assertTrue(await scheduler.stop_strategy("전략A"))
        self.assertFalse(scheduler._strategies[0].enabled)

        # 없는 전략 정지
        self.assertFalse(await scheduler.stop_strategy("없는전략"))

        # 시작 (루프가 안 돌고 있으면 시작됨)
        self.assertFalse(scheduler._running)
        with patch.object(scheduler, '_loop', new_callable=AsyncMock):  # loop 실행 방지
            self.assertTrue(await scheduler.start_strategy("전략A"))
            self.assertTrue(scheduler._strategies[0].enabled)
            self.assertTrue(scheduler._running)
            self.assertIsNotNone(scheduler._task)

        # 이미 실행 중일 때 전략만 활성화
        scheduler._strategies[0].enabled = False
        self.assertTrue(await scheduler.start_strategy("전략A"))
        self.assertTrue(scheduler._strategies[0].enabled)

    async def test_update_max_positions_success(self):
        """최대 포지션 수가 성공적으로 변경되는지 테스트."""
        scheduler, _, _, _, _ = self._make_scheduler()
        strategy = MockStrategy(name="전략A")
        config = StrategySchedulerConfig(strategy=strategy, max_positions=5)
        scheduler.register(config)

        with patch.object(scheduler, '_save_scheduler_state') as mock_save:
            result = await scheduler.update_max_positions("전략A", 10)
            self.assertTrue(result)
            self.assertEqual(config.max_positions, 10)
            mock_save.assert_called_once()

    async def test_update_max_positions_invalid_value(self):
        """1 미만의 값으로 변경 시도 시 실패하는지 테스트."""
        scheduler, _, _, _, _ = self._make_scheduler()
        strategy = MockStrategy(name="전략A")
        config = StrategySchedulerConfig(strategy=strategy, max_positions=5)
        scheduler.register(config)

        with patch.object(scheduler, '_save_scheduler_state') as mock_save:
            result = await scheduler.update_max_positions("전략A", 0)
            self.assertFalse(result)
            self.assertEqual(config.max_positions, 5)  # 변경되지 않아야 함
            mock_save.assert_not_called()

    async def test_update_max_positions_not_found(self):
        """존재하지 않는 전략의 포지션 수 변경 시도 시 실패하는지 테스트."""
        scheduler, _, _, _, _ = self._make_scheduler()
        result = await scheduler.update_max_positions("없는전략", 10)
        self.assertFalse(result)

    async def test_persistence_save_restore(self):
        """상태 저장 및 복원 테스트."""
        scheduler, vm, _, _, _ = self._make_scheduler()
        strategy = MockStrategy(name="전략A")
        config = StrategySchedulerConfig(strategy=strategy)
        scheduler.register(config)
        scheduler._running = True
        config.enabled = True

        vm.get_holds.return_value = [{"code": "005930", "name": "삼성전자"}]

        # Save State
        scheduler._save_scheduler_state()
        scheduler._store.save_state.assert_called_once()
        saved_state = scheduler._store.save_state.call_args[0][0]
        self.assertEqual(saved_state["enabled_strategies"], ["전략A"])
        self.assertEqual(saved_state["current_positions"], [{"code": "005930", "name": "삼성전자"}])

        # Restore State
        scheduler._running = False
        config.enabled = False

        scheduler._store.load_state.return_value = {
            "running": True,
            "enabled_strategies": ["전략A"],
            "current_positions": [{"code": "005930", "name": "삼성전자"}],
        }
        with patch.object(scheduler, '_loop', new_callable=AsyncMock):
            await scheduler.restore_state()
            self.assertTrue(scheduler._running)
            self.assertTrue(config.enabled)

    async def test_restore_state_file_not_found(self):
        """저장된 상태가 없을 때 복원 시도 테스트."""
        scheduler, _, _, _, _ = self._make_scheduler()
        scheduler._store.load_state.return_value = None
        await scheduler.restore_state()
        self.assertFalse(scheduler._running)

    async def test_restore_state_no_state(self):
        """저장된 상태가 없을 때(None 반환) 복원 시도 테스트."""
        scheduler, _, _, _, _ = self._make_scheduler()
        scheduler._store.load_state.return_value = None
        await scheduler.restore_state()
        self.assertFalse(scheduler._running)

    async def test_restore_state_exception(self):
        """restore_state 중 예외 발생 시 처리 테스트."""
        scheduler, _, _, _, _ = self._make_scheduler()
        scheduler._store.load_state.side_effect = Exception("DB Error")
        await scheduler.restore_state()
        self.assertFalse(scheduler._running)

    def test_save_state_exception(self):
        """상태 저장 중 예외 발생 테스트."""
        scheduler, _, _, _, _ = self._make_scheduler()
        scheduler._store.save_state.side_effect = IOError("Disk full")
        scheduler._save_scheduler_state()
        scheduler._logger.error.assert_called()

    async def test_clear_saved_state(self):
        """저장된 상태 삭제 테스트."""
        scheduler, _, _, _, _ = self._make_scheduler()
        scheduler.clear_saved_state()
        scheduler._store.clear_state.assert_called_once()

    async def test_clear_saved_state_oserror(self):
        """저장된 상태 삭제 중 예외 발생 시 로깅 테스트."""
        scheduler, _, _, _, _ = self._make_scheduler()
        scheduler._store.clear_state.side_effect = OSError("File in use")
        scheduler.clear_saved_state()
        scheduler._logger.error.assert_called()

    def test_state_file_thread_safety(self):
        """멀티 스레드에서 save/clear 동시 호출 시 예외 없이 완료되는지 확인."""
        scheduler, _, _, _, _ = self._make_scheduler()
        scheduler.register(StrategySchedulerConfig(strategy=MockStrategy(name="전략A")))

        import concurrent.futures

        def worker(action, count):
            for _ in range(count):
                if action == "save":
                    scheduler._save_scheduler_state()
                elif action == "clear":
                    scheduler.clear_saved_state()

        thread_count = 10
        iterations = 100
        with concurrent.futures.ThreadPoolExecutor(max_workers=thread_count) as executor:
            futures = [
                executor.submit(worker, "save" if i % 2 == 0 else "clear", iterations)
                for i in range(thread_count)
            ]
            for future in concurrent.futures.as_completed(futures):
                future.result()

        self.assertTrue(True)

    async def test_state_file_async_concurrency(self):
        """비동기/동기 혼재 상황에서 save/restore 동시 호출 시 예외 없이 완료되는지 확인."""
        scheduler, _, _, _, _ = self._make_scheduler()
        scheduler.register(StrategySchedulerConfig(strategy=MockStrategy(name="전략A")))

        scheduler._store.load_state.return_value = {
            "running": True,
            "enabled_strategies": ["전략A"],
            "current_positions": [],
            "strategy_configs": {},
        }

        with patch.object(scheduler, '_loop', new_callable=AsyncMock):
            async def async_worker():
                for _ in range(50):
                    await scheduler.restore_state()

            def sync_worker():
                for _ in range(50):
                    scheduler._save_scheduler_state()

            loop = asyncio.get_running_loop()
            async_task = asyncio.create_task(async_worker())
            thread_future = loop.run_in_executor(None, sync_worker)

            await asyncio.gather(async_task, thread_future)

        self.assertTrue(True)

    def test_load_signal_history_real(self):
        """DB에서 시그널 이력 로드 테스트."""
        scheduler, _, _, _, _ = self._make_scheduler()
        scheduler._store.load_signal_history.return_value = [
            {"strategy_name": "전략A", "code": "005930", "name": "삼성전자",
             "action": "BUY", "price": 70000, "qty": 1, "return_rate": None,
             "reason": "테스트", "timestamp": "2023-01-01 10:00:00", "api_success": True}
        ]
        records = scheduler._load_signal_history()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].code, "005930")
        self.assertTrue(records[0].api_success)

    async def test_loop_market_closed_smart_wait(self):
        """장 마감 시 스마트 대기(다음 영업일 개장까지) 테스트."""
        scheduler, _, _, tm, mcs = self._make_scheduler()
        
        # 달력이 장이 닫혔다고 응답함
        mcs.is_market_open_now.return_value = False

        scheduler._running = True

        # wait_until_next_open 안에서 CancelledError를 발생시켜 루프를 종료시킴
        mcs.wait_until_next_open.side_effect = asyncio.CancelledError()

        try:
            await scheduler._loop()
        except asyncio.CancelledError:
            pass

        # 달력 매니저의 대기 메서드가 호출되었는지 완벽하게 확인됨!
        mcs.wait_until_next_open.assert_awaited_once()

    async def test_loop_force_exit(self):
        """장 마감 임박 시 강제 청산 로직 테스트 (전략별 설정 구분 확인)."""
        scheduler, vm, _, tm, _ = self._make_scheduler()

        # 전략 A: 강제 청산 설정 O
        strategy_a = MockStrategy(name="전략A")
        config_a = StrategySchedulerConfig(strategy=strategy_a, force_exit_on_close=True, interval_minutes=0)
        scheduler.register(config_a)

        # 전략 B: 강제 청산 설정 X
        strategy_b = MockStrategy(name="전략B")
        config_b = StrategySchedulerConfig(strategy=strategy_b, force_exit_on_close=False, interval_minutes=0)
        scheduler.register(config_b)

        # 현재 시간: 15:20, 마감 시간: 15:40 (10분 남음 -> FORCE_EXIT_MINUTES_BEFORE(15) 이내)
        import pytz
        from datetime import datetime
        kst = pytz.timezone("Asia/Seoul")
        now = kst.localize(datetime(2023, 1, 1, 15, 20))
        # 두 번째 루프에서는 쿨다운(60초) 이후 시점
        now_after_cooldown = kst.localize(datetime(2023, 1, 1, 15, 21, 1))
        close_time = kst.localize(datetime(2023, 1, 1, 15, 30))

        tm.get_current_kst_time.side_effect = [now, now_after_cooldown]
        tm.get_market_close_time.return_value = close_time

        scheduler._running = True

        # _run_strategy가 force_exit_only=True로 호출되는지 확인하기 위해 spy/mock
        with patch.object(scheduler, '_run_strategy', new_callable=AsyncMock) as mock_run:
            # 두 번째 루프 이터레이션 후 종료 (전략B는 쿨다운 후 두 번째 루프에서 실행)
            with patch("asyncio.sleep", side_effect=[None, asyncio.CancelledError]):
                try:
                    await scheduler._loop()
                except asyncio.CancelledError:
                    pass

            # 전략 A는 강제 청산 모드(True)로 실행되어야 함
            mock_run.assert_any_call(config_a, force_exit_only=True)

            # 전략 B는 일반 모드(False)로 실행되어야 함 (쿨다운 이후 두 번째 루프에서 실행)
            mock_run.assert_any_call(config_b, force_exit_only=False)

    async def test_loop_exception_handling(self):
        """루프 내 예외 발생 시 계속 실행되는지 테스트."""
        scheduler, _, _, tm, _ = self._make_scheduler()
        
        # 전략 실행 시 예외 발생
        strategy = MockStrategy(name="전략A")
        config = StrategySchedulerConfig(strategy=strategy, interval_minutes=0) # 즉시 실행
        scheduler.register(config)
        
        scheduler._running = True
        
        # _run_strategy가 예외를 던지도록 설정
        with patch.object(scheduler, '_run_strategy', side_effect=Exception("Strategy Error")):
            # sleep 호출 시 CancelledError로 루프 종료
            with patch("asyncio.sleep", side_effect=[None, asyncio.CancelledError]):
                try:
                    await scheduler._loop()
                except asyncio.CancelledError:
                    pass
        
        # 에러 로그가 호출되었는지 확인
        scheduler._logger.error.assert_called()
    
    async def test_force_liquidate_strategy_execution(self):
        """강제 청산 실행 시: 현재가 조회 후 시장가 매도 주문 및 로깅 확인."""
        scheduler, vm, oes, _, _ = self._make_scheduler(dry_run=False)

        # SQS Mock 설정
        scheduler._sqs.get_current_price = AsyncMock(
            return_value=ResCommonResponse(
                rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data={"output": {"stck_prpr": "60000"}}
            )
        )

        strategy = MockStrategy(name="TestStrategy")
        config = StrategySchedulerConfig(strategy=strategy, force_exit_on_close=True, order_qty=5)

        # Mock: 보유 종목 1개 존재
        vm.get_holds_by_strategy.return_value = [{"code": "005930", "name": "Samsung", "buy_price": 50000}]

        await scheduler._force_liquidate_strategy(config)

        # 1. 현재가 조회 호출됨
        scheduler._sqs.get_current_price.assert_called_with("005930", caller="StrategyScheduler")
        # 2. API 매도 주문은 가격 0(시장가)으로 호출됨
        oes.handle_place_sell_order.assert_called_once_with(
            "005930",
            0,
            5,
            exchange=Exchange.KRX,
            source="strategy:TestStrategy",
            finalize_immediately=False,
        )
        # 3. live 모드 가상매매 기록은 체결 확인 이후 OrderExecutionService가 처리
        vm.log_sell_by_strategy_async.assert_not_awaited()

    async def test_force_liquidate_uses_holding_qty(self):
        """강제 청산 시 설정된 주문 수량이 아닌 실제 보유 수량을 사용하는지 테스트."""
        scheduler, vm, oes, _, _ = self._make_scheduler(dry_run=False)

        # SQS Mock 설정
        scheduler._sqs.get_current_price = AsyncMock(
            return_value=ResCommonResponse(
                rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data={"output": {"stck_prpr": "60000"}}
            )
        )

        strategy = MockStrategy(name="TestStrategy")
        # 설정된 주문 수량은 10 (기본값보다 큰 값으로 설정하여 차이 확인)
        config = StrategySchedulerConfig(strategy=strategy, force_exit_on_close=True, order_qty=10)

        # Mock: 보유 종목 1개 존재, 실제 보유 수량은 3 (설정값과 다르게 설정)
        vm.get_holds_by_strategy.return_value = [
            {"code": "005930", "name": "Samsung", "buy_price": 50000, "qty": 3}
        ]

        await scheduler._force_liquidate_strategy(config)

        # API 매도 주문은 실제 보유 수량인 3으로 호출되어야 함 (설정값 10 무시)
        oes.handle_place_sell_order.assert_called_once_with(
            "005930",
            0,
            3,
            exchange=Exchange.KRX,
            source="strategy:TestStrategy",
            finalize_immediately=False,
        )
        vm.log_sell_by_strategy_async.assert_not_awaited()

    async def test_stop_strategy_triggers_liquidation(self):
        """stop_strategy 호출 시 강제 청산 옵션이 켜져 있으면 청산 수행."""
        scheduler, _, _, _, _ = self._make_scheduler()
        strategy = MockStrategy(name="AutoCloseStrategy")
        config = StrategySchedulerConfig(strategy=strategy, force_exit_on_close=True)
        scheduler.register(config)
        
        # 기본적으로 register하면 enabled=True가 되지만 명시적으로 확인
        config.enabled = True

        with patch.object(scheduler, '_force_liquidate_strategy', new_callable=AsyncMock) as mock_liquidate:
            await scheduler.stop_strategy("AutoCloseStrategy")
            
            mock_liquidate.assert_called_once_with(config)
            self.assertFalse(config.enabled)

    async def test_stop_strategy_no_liquidation_if_disabled(self):
        """이미 비활성화된 전략은 stop_strategy 호출 시 강제 청산 미수행."""
        scheduler, _, _, _, _ = self._make_scheduler()
        strategy = MockStrategy(name="DisabledStrategy")
        config = StrategySchedulerConfig(strategy=strategy, force_exit_on_close=True)
        scheduler.register(config)
        config.enabled = False

        with patch.object(scheduler, '_force_liquidate_strategy', new_callable=AsyncMock) as mock_liquidate:
            await scheduler.stop_strategy("DisabledStrategy")
            
            mock_liquidate.assert_not_called()
            self.assertFalse(config.enabled)

    async def test_stop_strategy_no_liquidation_if_option_off(self):
        """강제 청산 옵션이 꺼져 있는 전략은 강제 청산 미수행."""
        scheduler, _, _, _, _ = self._make_scheduler()
        strategy = MockStrategy(name="LongTermStrategy")
        config = StrategySchedulerConfig(strategy=strategy, force_exit_on_close=False)
        scheduler.register(config)
        config.enabled = True

        with patch.object(scheduler, '_force_liquidate_strategy', new_callable=AsyncMock) as mock_liquidate:
            await scheduler.stop_strategy("LongTermStrategy")
            
            mock_liquidate.assert_not_called()
            self.assertFalse(config.enabled)

    async def test_scheduler_stop_calls_stop_strategy(self):
        """scheduler.stop() 호출 시 등록된 모든 전략에 대해 stop_strategy 호출."""
        scheduler, _, _, _, _ = self._make_scheduler()
        s1 = MockStrategy(name="S1")
        s2 = MockStrategy(name="S2")
        scheduler.register(StrategySchedulerConfig(strategy=s1))
        scheduler.register(StrategySchedulerConfig(strategy=s2))

        with patch.object(scheduler, 'stop_strategy', new_callable=AsyncMock) as mock_stop_strat:
            await scheduler.stop()

            self.assertEqual(mock_stop_strat.call_count, 2)
            # 순서는 보장되지 않을 수 있으므로 any_call 사용
            mock_stop_strat.assert_any_call("S1", perform_force_exit=True)
            mock_stop_strat.assert_any_call("S2", perform_force_exit=True)

    async def test_stop_with_save_state_skips_liquidation(self):
        """상태 저장 모드로 정지 시 강제 청산 생략."""
        scheduler, _, _, _, _ = self._make_scheduler()
        strategy = MockStrategy(name="RestartStrategy")
        config = StrategySchedulerConfig(strategy=strategy, force_exit_on_close=True)
        scheduler.register(config)
        config.enabled = True

        with patch.object(scheduler, 'stop_strategy', new_callable=AsyncMock) as mock_stop_strat:
            await scheduler.stop(save_state=True)
            
            # perform_force_exit=False로 호출되었는지 확인
            mock_stop_strat.assert_called_once_with("RestartStrategy", perform_force_exit=False)

    async def test_execute_signal_market_price_logging(self):
        """시그널 가격이 0(시장가)일 때, 현재가를 조회하여 로그에 남기는지 테스트."""
        scheduler, vm, oes, _, _ = self._make_scheduler(dry_run=False)

        # SQS Mock 설정
        scheduler._sqs.get_current_price = AsyncMock(
            return_value=ResCommonResponse(
                rt_cd=ErrorCode.SUCCESS.value, msg1="Success", data={"output": {"stck_prpr": "80000"}}
            )
        )

        signal = TradeSignal(
            strategy_name="TestStrat", code="000660", name="Hynix",
            action="SELL", price=0, qty=10, reason="Market Sell"
        )

        await scheduler._execute_signal(signal)

        # 1. 현재가 조회 수행 확인
        scheduler._sqs.get_current_price.assert_called_with("000660", caller="StrategyScheduler")
        # 2. 주문은 0원(시장가)으로 나갔는지 확인
        from common.types import Exchange
        oes.handle_place_sell_order.assert_called_once_with(
            "000660",
            0,
            10,
            exchange=Exchange.KRX,
            source="strategy:TestStrat",
            finalize_immediately=False,
        )
        vm.log_sell_by_strategy_async.assert_not_awaited()

    async def test_run_strategy_concurrent_execution(self):
        """_run_strategy가 여러 시그널을 동시(concurrent)에 처리하는지 테스트."""
        scheduler, vm, _, _, _ = self._make_scheduler()
        
        # 3개의 매수 시그널 생성
        buy_signals = [
            TradeSignal(code=f"0000{i}", name=f"Stock{i}", action="BUY", price=1000, qty=1, reason="Test", strategy_name="TestStrat")
            for i in range(3)
        ]
        
        strategy = MockStrategy(scan_signals=buy_signals)
        config = StrategySchedulerConfig(strategy=strategy, max_positions=10) # 충분한 슬롯
        
        # _execute_signal을 감시
        with patch.object(scheduler, '_execute_signal', new_callable=AsyncMock) as mock_exec:
            await scheduler._run_strategy(config)
            
            # 3번 호출되었는지 확인
            self.assertEqual(mock_exec.await_count, 3)
            # 호출된 인자 확인 (순서는 보장되지 않으므로 any_order=True)
            expected_calls = [call(sig) for sig in buy_signals]
            mock_exec.assert_has_awaits(expected_calls, any_order=True)

    async def test_append_signal_csv_thread_execution(self):
        """_append_signal_db가 asyncio.to_thread를 사용하여 동기 메서드를 실행하는지 테스트."""
        scheduler, _, _, _, _ = self._make_scheduler()
        record = MagicMock()

        with patch("asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
            await scheduler._append_signal_db(record)
            mock_to_thread.assert_awaited_once_with(scheduler._store.append_signal, record)

    async def test_run_strategy_parallel_execution_with_exception(self):
        """병렬 매수 실행 중 한 신호에서 예외 발생해도 나머지 신호는 모두 실행된다."""
        scheduler, vm, _, _, _ = self._make_scheduler()

        buy_signals = [
            TradeSignal(code="00001", name="S1", action="BUY", price=1000, qty=1, reason="T", strategy_name="S"),
            TradeSignal(code="00002", name="S2", action="BUY", price=1000, qty=1, reason="T", strategy_name="S"),
        ]

        strategy = MockStrategy(scan_signals=buy_signals)
        config = StrategySchedulerConfig(strategy=strategy, max_positions=10)

        # _execute_signal 모킹: 첫 번째에서 예외 발생
        async def mock_execute(signal):
            if signal.code == "00001":
                raise ValueError("Test Error")
            return

        with patch.object(scheduler, '_execute_signal', side_effect=mock_execute) as mock_exec:
            # return_exceptions=True 이므로 예외가 전파되지 않음
            await scheduler._run_strategy(config)

            # 병렬 실행 — 첫 번째 예외와 무관하게 두 신호 모두 실행
            self.assertEqual(mock_exec.call_count, 2)

    async def test_execute_signal_market_price_exception_handling(self):
        """_execute_signal에서 현재가 조회 중 예외 발생 시 0원으로 처리되는지 테스트."""
        scheduler, vm, oes, _, _ = self._make_scheduler(dry_run=False)

        # SQS Mock 설정: 예외 발생
        scheduler._sqs.get_current_price.side_effect = Exception("API Error")

        signal = TradeSignal(
            strategy_name="TestStrat", code="000660", name="Hynix",
            action="SELL", price=0, qty=10, reason="Market Sell"
        )

        await scheduler._execute_signal(signal)

        # 1. 현재가 조회 수행 확인 (예외 발생했음)
        scheduler._sqs.get_current_price.assert_called_with("000660", caller="StrategyScheduler")
        
        # 2. 주문은 0원(시장가)으로 나갔는지 확인
        from common.types import Exchange
        oes.handle_place_sell_order.assert_called_once_with(
            "000660",
            0,
            10,
            exchange=Exchange.KRX,
            source="strategy:TestStrat",
            finalize_immediately=False,
        )

        vm.log_sell_by_strategy_async.assert_not_awaited()

    async def test_start_already_running(self):
        """이미 실행 중일 때 start 호출 시 경고 로그 및 리턴 테스트."""
        scheduler, _, _, _, _ = self._make_scheduler()
        scheduler._running = True
        
        await scheduler.start()
        
        scheduler._logger.warning.assert_called_with("[Scheduler] 이미 실행 중")

    async def test_start_enables_strategies(self):
        """start 호출 시 등록된 전략들이 활성화되는지 테스트."""
        scheduler, _, _, _, _ = self._make_scheduler()
        strategy = MockStrategy()
        config = StrategySchedulerConfig(strategy=strategy, enabled=False)
        scheduler.register(config)
        
        await scheduler.start()
        
        self.assertTrue(config.enabled)
        # Cleanup
        await scheduler.stop()

    async def test_loop_skips_disabled_strategy(self):
        """루프에서 비활성화된 전략은 실행하지 않는지 테스트."""
        scheduler, _, _, tm, _ = self._make_scheduler()

        strategy = MockStrategy(name="DisabledStrat")
        config = StrategySchedulerConfig(strategy=strategy, enabled=False, interval_minutes=0)
        scheduler.register(config)
        
        scheduler._running = True
        
        with patch.object(scheduler, '_run_strategy', new_callable=AsyncMock) as mock_run:
            with patch("asyncio.sleep", side_effect=[None, asyncio.CancelledError]):
                try:
                    await scheduler._loop()
                except asyncio.CancelledError:
                    pass
            
            mock_run.assert_not_called()

    async def test_execute_signal_api_response_none(self):
        """API 응답이 None일 때 처리 테스트."""
        scheduler, vm, oes, _, _ = self._make_scheduler(dry_run=False)
        oes.handle_place_buy_order.return_value = None
        
        signal = TradeSignal(
            code="005930", name="Samsung", action="BUY", price=1000, qty=1, reason="Test", strategy_name="S"
        )
        
        await scheduler._execute_signal(signal)
        
        # 로그에 "응답 없음"이 포함되어야 함
        args, _ = scheduler._logger.warning.call_args
        self.assertIn("응답 없음", args[0])
        self.assertFalse(scheduler._signal_history[-1].api_success)

    async def test_force_liquidate_no_holdings(self):
        """보유 종목이 없을 때 강제 청산 메서드 조기 리턴 테스트."""
        scheduler, vm, _, _, _ = self._make_scheduler()
        vm.get_holds_by_strategy.return_value = []
        
        strategy = MockStrategy(name="S")
        config = StrategySchedulerConfig(strategy=strategy)
        
        with patch.object(scheduler, '_execute_signal', new_callable=AsyncMock) as mock_exec:
            await scheduler._force_liquidate_strategy(config)
            mock_exec.assert_not_called()

    async def test_force_liquidate_holding_no_code(self):
        """보유 종목 정보에 코드가 없을 때 건너뛰기 테스트."""
        scheduler, vm, _, _, _ = self._make_scheduler()
        # code 키가 없는 holding
        vm.get_holds_by_strategy.return_value = [{"name": "NoCodeStock", "qty": 1}]
        
        strategy = MockStrategy(name="S")
        config = StrategySchedulerConfig(strategy=strategy)
        
        with patch.object(scheduler, '_execute_signal', new_callable=AsyncMock) as mock_exec:
            await scheduler._force_liquidate_strategy(config)
            mock_exec.assert_not_called()

    async def test_start_strategy_not_found(self):
        """존재하지 않는 전략 시작 시 False 반환 테스트."""
        scheduler, _, _, _, _ = self._make_scheduler()
        result = await scheduler.start_strategy("NonExistent")
        self.assertFalse(result)

    def test_load_signal_history_file_not_exists(self):
        """시그널 히스토리 파일이 없을 때 빈 리스트 반환 테스트."""
        scheduler, _, _, _, _ = self._make_scheduler()
        with patch("os.path.exists", return_value=False):
            records = scheduler._load_signal_history()
            self.assertEqual(records, [])

    def test_load_signal_history_exception(self):
        """시그널 히스토리 로드 중 예외 발생 시 처리 테스트."""
        scheduler, _, _, _, _ = self._make_scheduler()
        scheduler._store.load_signal_history.side_effect = Exception("Read Error")
        records = scheduler._load_signal_history()
        self.assertEqual(records, [])
        scheduler._logger.error.assert_called()

    async def test_restore_state_empty_enabled_names(self):
        """상태 파일에 활성 전략 목록이 비어있을 때 리턴 테스트."""
        scheduler, _, _, _, _ = self._make_scheduler()
        state_data = {
            "running": False,
            "enabled_strategies": [],
            "current_positions": []
        }
        
        with patch("os.path.exists", return_value=True):
            with patch("builtins.open", mock_open(read_data=json.dumps(state_data))):
                with patch("json.load", return_value=state_data):
                    await scheduler.restore_state()
                    
                    self.assertFalse(scheduler._running)

    async def test_append_signal_db_records_signal(self):
        """_append_signal_db가 store.append_signal을 호출하는지 테스트."""
        scheduler, _, _, _, _ = self._make_scheduler()
        record = SignalRecord("S", "001", "Name", "BUY", 1000, "Reason", "2023-01-01")
        await scheduler._append_signal_db(record)
        scheduler._store.append_signal.assert_called_once_with(record)

    async def test_execute_signal_api_failure_branches(self):
        """API 응답 실패 시 분기 처리 테스트 (Line 294 coverage)."""
        scheduler, vm, oes, _, _ = self._make_scheduler(dry_run=False)

        # Case 1: BUY 주문 실패, resp 있음
        oes.handle_place_buy_order.return_value = ResCommonResponse(
            rt_cd=ErrorCode.API_ERROR.value, msg1="매수 거부"
        )
        signal_buy = TradeSignal(
            code="005930", name="Samsung", action="BUY", price=1000, qty=1, reason="Test", strategy_name="S"
        )
        await scheduler._execute_signal(signal_buy)
        
        # 로그 확인
        args, _ = scheduler._logger.warning.call_args
        self.assertIn("매수 거부", args[0])
        
        # Case 2: SELL 주문 실패, resp 있음
        oes.handle_place_sell_order.return_value = ResCommonResponse(
            rt_cd=ErrorCode.API_ERROR.value, msg1="매도 거부"
        )
        signal_sell = TradeSignal(
            code="005930", name="Samsung", action="SELL", price=1000, qty=1, reason="Test", strategy_name="S"
        )
        await scheduler._execute_signal(signal_sell)
        
        # 로그 확인
        args, _ = scheduler._logger.warning.call_args
        self.assertIn("매도 거부", args[0])

        # Case 3: resp가 None인 경우
        oes.handle_place_sell_order.return_value = None
        await scheduler._execute_signal(signal_sell)
        args, _ = scheduler._logger.warning.call_args
        self.assertIn("응답 없음", args[0])

    def test_load_signal_history_branches(self):
        """시그널 히스토리 로드 분기 테스트."""
        scheduler, _, _, _, _ = self._make_scheduler()

        # Case 1: 빈 이력 -> 빈 리스트 반환
        scheduler._store.load_signal_history.return_value = []
        records = scheduler._load_signal_history()
        self.assertEqual(records, [])

        # Case 2: 데이터 있음 -> 레코드 반환
        scheduler._store.load_signal_history.return_value = [
            {"strategy_name": "S", "code": "001", "name": "Name",
             "action": "BUY", "price": 1000, "qty": 1, "return_rate": None,
             "reason": "Reason", "timestamp": "2023-01-01", "api_success": True}
        ]
        records = scheduler._load_signal_history()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].code, "001")

    async def test_append_signal_db_uses_to_thread(self):
        """_append_signal_db가 asyncio.to_thread를 사용하여 store.append_signal을 호출하는지 테스트."""
        scheduler, _, _, _, _ = self._make_scheduler()
        record = SignalRecord("S", "001", "Name", "BUY", 1000, "Reason", "2023-01-01")

        with patch("asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
            await scheduler._append_signal_db(record)
            mock_to_thread.assert_awaited_once_with(scheduler._store.append_signal, record)

    async def test_execute_signal_market_price_lookup_failure(self):
        """시장가 주문 시 현재가 조회 실패(응답은 성공이나 데이터 없음) 테스트."""
        scheduler, vm, oes, _, _ = self._make_scheduler(dry_run=False)
        
        # 응답은 성공이지만 output이 없는 경우
        scheduler._sqs.get_current_price.return_value = ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data={}
        )
        
        signal = TradeSignal(
            strategy_name="S", code="005930", name="Samsung",
            action="BUY", price=0, qty=1, reason="Market Buy"
        )
        
        await scheduler._execute_signal(signal)
        
        vm.log_buy_async.assert_not_awaited()

    async def test_execute_signal_market_price_lookup_object_response(self):
        """시장가 주문 시 현재가 조회가 객체 형태로 반환될 때 테스트."""
        scheduler, vm, oes, _, _ = self._make_scheduler(dry_run=False)
        
        # 데이터가 객체 형태인 경우 모의
        mock_output = MagicMock()
        mock_output.stck_prpr = "55000"
        # isinstance(mock_output, dict) -> False
        
        mock_data = MagicMock()
        mock_data.output = mock_output
        # isinstance(mock_data, dict) -> False
        
        scheduler._sqs.get_current_price.return_value = ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data=mock_data
        )
        
        signal = TradeSignal(
            strategy_name="S", code="005930", name="Samsung",
            action="BUY", price=0, qty=1, reason="Market Buy"
        )
        
        await scheduler._execute_signal(signal)
        
        vm.log_buy_async.assert_not_awaited()

    async def test_run_strategy_updates_qty(self):
        """전략 실행 시 시그널 수량이 1 이하이면 설정된 주문 수량으로 업데이트하는지 테스트."""
        scheduler, vm, _, _, _ = self._make_scheduler()

        # 주문 수량을 10으로 설정
        strategy = MockStrategy(scan_signals=[
            TradeSignal(code="005930", name="Samsung", action="BUY", price=1000, qty=1, reason="T", strategy_name="S")
        ])
        config = StrategySchedulerConfig(strategy=strategy, order_qty=10)
        
        await scheduler._run_strategy(config)
        
        # log_buy_async 호출 시 qty가 10이어야 함
        vm.log_buy_async.assert_awaited_once_with("S", "005930", 1000, 10)

    def test_load_signal_history_max_limit(self):
        """시그널 히스토리 로드 시 MAX_HISTORY 제한이 store에 전달되는지 테스트."""
        scheduler, _, _, _, _ = self._make_scheduler()
        scheduler.MAX_HISTORY = 5

        scheduler._store.load_signal_history.return_value = [
            {"strategy_name": "S", "code": f"00{i}", "name": "Name",
             "action": "BUY", "price": 1000, "qty": 1, "return_rate": None,
             "reason": "Reason", "timestamp": "2023-01-01", "api_success": True}
            for i in range(5)
        ]

        records = scheduler._load_signal_history()
        scheduler._store.load_signal_history.assert_called_with(limit=5)
        self.assertEqual(len(records), 5)

    async def test_execute_signal_history_truncation(self):
        """시그널 실행 후 히스토리 제한 유지 테스트."""
        scheduler, _, _, _, _ = self._make_scheduler(dry_run=True)
        scheduler.MAX_HISTORY = 2
        
        # 이미 2개 있다고 가정
        scheduler._signal_history = [
            SignalRecord("S", "001", "N", "BUY", 100, "R", "T"),
            SignalRecord("S", "002", "N", "BUY", 100, "R", "T")
        ]
        
        signal = TradeSignal(
            strategy_name="S", code="003", name="N", action="BUY", price=100, qty=1, reason="R"
        )
        await scheduler._execute_signal(signal)
        
        self.assertEqual(len(scheduler._signal_history), 2)
        self.assertEqual(scheduler._signal_history[0].code, "002")
        self.assertEqual(scheduler._signal_history[1].code, "003")

    async def test_append_signal_db_exception_handling(self):
        """store.append_signal 예외 발생 시 로그 기록 테스트."""
        scheduler, _, _, _, _ = self._make_scheduler()
        record = SignalRecord("S", "001", "Name", "BUY", 1000, "Reason", "2023-01-01")

        scheduler._store.append_signal.side_effect = IOError("Disk full")
        await scheduler._append_signal_db(record)

        scheduler._logger.error.assert_called()
        args, _ = scheduler._logger.error.call_args
        self.assertIn("시그널 DB 저장 실패", args[0])

    async def test_loop_outer_exception_handling(self):
        """루프 자체(외부) 예외 발생 시 로그 기록 및 계속 실행 테스트."""
        scheduler, _, _, tm, _ = self._make_scheduler()

        tm.get_current_kst_time.side_effect = Exception("Time Error")
        
        scheduler._running = True
        
        with patch("asyncio.sleep", side_effect=[None, asyncio.CancelledError]):
            try:
                await scheduler._loop()
            except asyncio.CancelledError:
                pass
        
        scheduler._logger.error.assert_called()
        args, _ = scheduler._logger.error.call_args
        self.assertIn("루프 오류", args[0])
        self.assertIn("Time Error", args[0])

    async def test_force_liquidate_fallback_qty(self):
        """강제 청산 시 보유 수량 정보가 없으면 설정된 주문 수량을 사용하는지 테스트."""
        scheduler, vm, oes, _, _ = self._make_scheduler(dry_run=False)
        
        scheduler._sqs.get_current_price = AsyncMock(
            return_value=ResCommonResponse(rt_cd="0", msg1="OK", data={"output": {"stck_prpr": "1000"}})
        )
        
        strategy = MockStrategy(name="S")
        config = StrategySchedulerConfig(strategy=strategy, force_exit_on_close=True, order_qty=10)
        
        vm.get_holds_by_strategy.return_value = [{"code": "005930", "name": "Samsung", "qty": 0}]
        
        await scheduler._force_liquidate_strategy(config)

        oes.handle_place_sell_order.assert_called_once_with(
            "005930",
            0,
            10,
            exchange=Exchange.KRX,
            source="strategy:S",
            finalize_immediately=False,
        )

    async def test_execute_signal_market_price_api_error(self):
        """시장가 주문 시 현재가 조회 API가 실패 코드를 반환할 때 0원 유지 테스트."""
        scheduler, vm, oes, _, _ = self._make_scheduler(dry_run=False)
        
        scheduler._sqs.get_current_price.return_value = ResCommonResponse(
            rt_cd="1", msg1="Fail", data=None
        )
        
        signal = TradeSignal(
            strategy_name="S", code="005930", name="Samsung",
            action="BUY", price=0, qty=1, reason="Market Buy"
        )
        
        await scheduler._execute_signal(signal)
        
        vm.log_buy_async.assert_not_awaited()

    async def test_execute_signal_market_price_lookup_object_missing_attr(self):
        """시장가 주문 시 현재가 조회 객체에 속성이 없을 때 테스트."""
        scheduler, vm, oes, _, _ = self._make_scheduler(dry_run=False)
        
        class EmptyObject:
            pass
            
        mock_data = MagicMock()
        mock_data.output = EmptyObject()
        
        scheduler._sqs.get_current_price.return_value = ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data=mock_data
        )
        
        signal = TradeSignal(
            strategy_name="S", code="005930", name="Samsung",
            action="BUY", price=0, qty=1, reason="Market Buy"
        )
        
        await scheduler._execute_signal(signal)
        
        vm.log_buy_async.assert_not_awaited()

    async def test_execute_signal_market_price_lookup_data_object_missing_output(self):
        """시장가 주문 시 현재가 조회 데이터 객체에 output 속성이 없을 때 테스트."""
        scheduler, vm, oes, _, _ = self._make_scheduler(dry_run=False)
        
        class EmptyObject:
            pass
            
        scheduler._sqs.get_current_price.return_value = ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data=EmptyObject()
        )
        
        signal = TradeSignal(
            strategy_name="S", code="005930", name="Samsung",
            action="BUY", price=0, qty=1, reason="Market Buy"
        )
        
        await scheduler._execute_signal(signal)
        
        vm.log_buy_async.assert_not_awaited()

    # ── SSE 구독자 관리 테스트 ──

    async def test_create_and_remove_subscriber_queue(self):
        """SSE 구독자 큐 생성 및 제거 테스트."""
        scheduler, _, _, _, _ = self._make_scheduler()

        q1 = scheduler.create_subscriber_queue()
        q2 = scheduler.create_subscriber_queue()
        self.assertEqual(len(scheduler._subscriber_queues), 2)
        self.assertIn(q1, scheduler._subscriber_queues)
        self.assertIn(q2, scheduler._subscriber_queues)

        scheduler.remove_subscriber_queue(q1)
        self.assertEqual(len(scheduler._subscriber_queues), 1)
        self.assertNotIn(q1, scheduler._subscriber_queues)

        # 이미 제거된 큐 다시 제거 시 에러 없음
        scheduler.remove_subscriber_queue(q1)
        self.assertEqual(len(scheduler._subscriber_queues), 1)

    async def test_notify_subscribers_on_signal(self):
        """시그널 실행 시 SSE 구독자에게 데이터가 전파되는지 테스트."""
        scheduler, _, _, _, _ = self._make_scheduler(dry_run=True)

        q = scheduler.create_subscriber_queue()

        signal = TradeSignal(
            strategy_name="TestStrat", code="005930", name="삼성전자",
            action="BUY", price=70000, qty=1, reason="모멘텀 돌파"
        )
        await scheduler._execute_signal(signal)

        # 큐에 데이터가 들어왔는지 확인
        self.assertFalse(q.empty())
        data = q.get_nowait()
        data = json.loads(data)
        self.assertEqual(data["code"], "005930")
        self.assertEqual(data["action"], "BUY")
        self.assertEqual(data["strategy_name"], "TestStrat")
        self.assertEqual(data["price"], 70000)
        self.assertEqual(data["reason"], "모멘텀 돌파")
        self.assertTrue(data["api_success"])

    async def test_notify_multiple_subscribers(self):
        """여러 구독자에게 동시에 전파되는지 테스트."""
        scheduler, _, _, _, _ = self._make_scheduler(dry_run=True)

        q1 = scheduler.create_subscriber_queue()
        q2 = scheduler.create_subscriber_queue()

        signal = TradeSignal(
            strategy_name="S", code="000660", name="SK하이닉스",
            action="SELL", price=50000, qty=5, reason="손절"
        )
        await scheduler._execute_signal(signal)

        # 두 큐 모두에 데이터가 들어왔는지 확인
        self.assertFalse(q1.empty())
        self.assertFalse(q2.empty())
        d1 = q1.get_nowait()
        d2 = q2.get_nowait()
        d1 = json.loads(d1)
        d2 = json.loads(d2)
        self.assertEqual(d1["code"], "000660")
        self.assertEqual(d2["code"], "000660")

    async def test_notify_subscribers_queue_full(self):
        """구독자 큐가 가득 찬 경우 오래된 메시지를 버리고 최신 메시지로 교체되는지 테스트."""
        scheduler, _, _, _, _ = self._make_scheduler(dry_run=True)

        # maxsize=1인 큐 생성
        q = asyncio.Queue(maxsize=1)
        scheduler._subscriber_queues.append(q)
        # 큐를 가득 채움 (JSON 문자열로 삽입 — 서비스 직렬화 방식 반영)
        q.put_nowait('{"dummy": true}')

        signal = TradeSignal(
            strategy_name="S", code="005930", name="삼성전자",
            action="BUY", price=70000, qty=1, reason="Test"
        )
        # 예외 없이 실행되어야 함
        await scheduler._execute_signal(signal)

        # 큐가 가득 찼을 때 오래된 메시지를 제거하고 최신 시그널로 교체
        self.assertEqual(q.qsize(), 1)
        data = json.loads(q.get_nowait())
        self.assertEqual(data["code"], "005930")
        self.assertEqual(data["action"], "BUY")

    async def test_no_subscribers_no_error(self):
        """구독자가 없을 때 _notify_subscribers 호출 시 에러 없음 테스트."""
        scheduler, _, _, _, _ = self._make_scheduler(dry_run=True)
        self.assertEqual(len(scheduler._subscriber_queues), 0)

        signal = TradeSignal(
            strategy_name="S", code="005930", name="삼성전자",
            action="BUY", price=70000, qty=1, reason="Test"
        )
        # 예외 없이 실행되어야 함
        await scheduler._execute_signal(signal)

    async def test_subscriber_removed_during_notify(self):
        """알림 중 구독자가 제거되어도 안전한지 테스트."""
        scheduler, _, _, _, _ = self._make_scheduler(dry_run=True)

        q1 = scheduler.create_subscriber_queue()
        q2 = scheduler.create_subscriber_queue()

        record = SignalRecord("S", "005930", "삼성전자", "BUY", 70000, "R", "2023-01-01")

        # _notify_subscribers는 list(self._subscriber_queues) 복사본을 순회하므로
        # 중간에 제거해도 안전해야 함
        scheduler.remove_subscriber_queue(q1)
        await scheduler._notify_subscribers(record)

        # q2만 데이터를 받아야 함
        self.assertTrue(q1.empty())
        self.assertFalse(q2.empty())

    # ── NotificationService / PriceSubscriptionService 연동 테스트 ──

    async def test_start_calls_notification_service(self):
        """start() 호출 시 NotificationService가 설정되어 있다면 알림을 전송하는지 테스트."""
        scheduler, _, _, _, _ = self._make_scheduler()
        mock_notifier = AsyncMock()
        scheduler._notification_service = mock_notifier
        scheduler.register(StrategySchedulerConfig(strategy=MockStrategy(name="전략A")))
        
        await scheduler.start()
        
        mock_notifier.emit.assert_awaited_once()
        args, kwargs = mock_notifier.emit.call_args
        self.assertEqual(args[0], NotificationCategory.SYSTEM)
        self.assertEqual(args[2], "스케줄러 시작")
        
        await scheduler.stop()

    async def test_stop_calls_notification_service(self):
        """stop() 호출 시 NotificationService가 설정되어 있다면 알림을 전송하는지 테스트."""
        scheduler, _, _, _, _ = self._make_scheduler()
        mock_notifier = AsyncMock()
        scheduler._notification_service = mock_notifier
        
        await scheduler.start()
        mock_notifier.emit.reset_mock()
        
        await scheduler.stop()
        mock_notifier.emit.assert_awaited_once()
        args, kwargs = mock_notifier.emit.call_args
        self.assertEqual(args[0], NotificationCategory.SYSTEM)
        self.assertEqual(args[2], "스케줄러 정지")

    async def test_execute_signal_notification_success(self):
        """_execute_signal() API 주문 성공 시 알림 전송 테스트."""
        scheduler, vm, oes, _, _ = self._make_scheduler(dry_run=False)
        mock_notifier = AsyncMock()
        scheduler._notification_service = mock_notifier
        
        signal = TradeSignal(code="005930", name="삼성전자", action="BUY", price=70000, qty=1, reason="Test", strategy_name="S1")
        await scheduler._execute_signal(signal)
        
        mock_notifier.emit.assert_awaited_once()
        args, kwargs = mock_notifier.emit.call_args
        self.assertEqual(args[0], NotificationCategory.STRATEGY)
        self.assertEqual(args[1], NotificationLevel.CRITICAL)
        self.assertTrue("성공" in args[2])

    async def test_execute_signal_notification_failure(self):
        """_execute_signal() API 주문 실패 시 알림 전송 테스트."""
        scheduler, vm, oes, _, _ = self._make_scheduler(dry_run=False)
        oes.handle_place_buy_order.return_value = ResCommonResponse(rt_cd="1", msg1="잔고 부족")
        
        mock_notifier = AsyncMock()
        scheduler._notification_service = mock_notifier
        
        signal = TradeSignal(code="005930", name="삼성전자", action="BUY", price=70000, qty=1, reason="Test", strategy_name="S1")
        await scheduler._execute_signal(signal)
        
        mock_notifier.emit.assert_awaited_once()
        args, kwargs = mock_notifier.emit.call_args
        self.assertEqual(args[0], NotificationCategory.STRATEGY)
        self.assertEqual(args[1], NotificationLevel.ERROR)
        self.assertTrue("실패" in args[2])

    async def test_execute_signal_price_subscription(self):
        """_execute_signal() 매수/매도 시 PriceSubscriptionService 구독/해지 호출 테스트."""
        scheduler, vm, oes, _, _ = self._make_scheduler(dry_run=False)
        mock_price_sub = AsyncMock()
        scheduler._price_sub_svc = mock_price_sub
        
        # BUY 시 구독 추가
        buy_sig = TradeSignal(code="005930", name="삼성전자", action="BUY", price=70000, qty=1, reason="Test", strategy_name="S1")
        await scheduler._execute_signal(buy_sig)
        # 비동기 Task로 실행되므로 약간 대기
        await asyncio.sleep(0.01)
        mock_price_sub.add_subscription.assert_awaited_once()
        
        # SELL 시 구독 해지
        sell_sig = TradeSignal(code="005930", name="삼성전자", action="SELL", price=70000, qty=1, reason="Test", strategy_name="S1")
        await scheduler._execute_signal(sell_sig)
        await asyncio.sleep(0.01)
        mock_price_sub.remove_subscription.assert_awaited_once()

    async def test_execute_signal_invalid_exchange_fallback(self):
        """_execute_signal()에서 유효하지 않은 exchange 값이 올 때 KRX로 폴백하는지 테스트."""
        scheduler, vm, oes, _, _ = self._make_scheduler(dry_run=False)
        
        signal = TradeSignal(code="005930", name="삼성전자", action="BUY", price=70000, qty=1, reason="Test", strategy_name="S1", exchange="INVALID")
        await scheduler._execute_signal(signal)
        
        oes.handle_place_buy_order.assert_called_once_with(
            "005930",
            70000,
            1,
            exchange=Exchange.KRX,
            source="strategy:S1",
            finalize_immediately=False,
        )

    async def test_restore_state_with_price_subscription(self):
        """restore_state()에서 보유 종목에 대해 실시간 가격 구독 복원 테스트."""
        scheduler, vm, _, _, _ = self._make_scheduler()
        mock_price_sub = AsyncMock()
        scheduler._price_sub_svc = mock_price_sub
        
        strategy = MockStrategy(name="전략A")
        config = StrategySchedulerConfig(strategy=strategy)
        scheduler.register(config)
        
        scheduler._store.load_state.return_value = {
            "running": True,
            "enabled_strategies": ["전략A"],
            "current_positions": [],
        }
        vm.get_holds_by_strategy.return_value = [{"code": "005930"}]
        
        with patch.object(scheduler, '_loop', new_callable=AsyncMock):
            await scheduler.restore_state()
            
            mock_price_sub.add_subscription.assert_awaited_once()
            args, kwargs = mock_price_sub.add_subscription.call_args
            self.assertEqual(args[0], "005930")

    async def test_stop_strategy_removes_price_subscription_category(self):
        """stop_strategy() 시 PriceSubscriptionService 카테고리 제거 테스트."""
        scheduler, _, _, _, _ = self._make_scheduler()
        mock_price_sub = AsyncMock()
        scheduler._price_sub_svc = mock_price_sub
        
        strategy = MockStrategy(name="전략A")
        config = StrategySchedulerConfig(strategy=strategy)
        scheduler.register(config)
        
        await scheduler.stop_strategy("전략A")
        
        mock_price_sub.remove_category.assert_awaited_once_with("scheduler_전략A")

    async def test_loop_force_exit_exception(self):
        """루프 중 장 마감 직후 강제 청산 시 예외가 발생해도 루프가 죽지 않는지 테스트."""
        scheduler, vm, _, tm, mcs = self._make_scheduler()
        
        mcs.is_market_open_now.return_value = False
        mcs.wait_until_next_open.side_effect = asyncio.CancelledError() # 루프 탈출용
        
        strategy = MockStrategy(name="전략A")
        config = StrategySchedulerConfig(strategy=strategy, force_exit_on_close=True, enabled=True)
        scheduler.register(config)
        
        scheduler._force_exit_done = set() 
        scheduler._running = True
        
        with patch.object(scheduler, '_run_strategy', side_effect=Exception("Liquidate Error")):
            try:
                await scheduler._loop()
            except asyncio.CancelledError:
                pass
            
            scheduler._logger.error.assert_called()

if __name__ == "__main__":
    unittest.main()
