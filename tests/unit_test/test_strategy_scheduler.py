# tests/unit_test/test_strategy_scheduler.py
import unittest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch, mock_open, call
from common.types import TradeSignal, ErrorCode, ResCommonResponse
from scheduler.strategy_scheduler import StrategyScheduler, StrategySchedulerConfig, SCHEDULER_STATE_FILE
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
        vm.log_buy_async = AsyncMock()
        vm.log_sell_by_strategy_async = AsyncMock()

        oes = MagicMock()
        oes.handle_place_buy_order = AsyncMock(
            return_value=ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="OK")
        )
        oes.handle_place_sell_order = AsyncMock(
            return_value=ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="OK")
        )

        tm = MagicMock()
        tm.is_market_open.return_value = True

        mock_logger = MagicMock()

        # CSV 파일 I/O를 차단하여 테스트 격리
        with patch.object(StrategyScheduler, '_load_signal_history', return_value=[]):
            scheduler = StrategyScheduler(
                virtual_manager=vm,
                order_execution_service=oes,
                time_manager=tm,
                logger=mock_logger,
                dry_run=dry_run,
            )
        scheduler._append_signal_csv = AsyncMock()
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

        vm.log_buy_async.assert_awaited_once_with("테스트전략", "005930", 70000, 1)
        oes.handle_place_buy_order.assert_not_called()

    async def test_execute_sell_signal_dry_run(self):
        """dry_run 모드에서 SELL 시그널 실행: CSV만 기록."""
        scheduler, vm, oes, _ = self._make_scheduler(dry_run=True)

        signal = TradeSignal(
            code="005930", name="삼성전자", action="SELL",
            price=72000, qty=1, reason="익절", strategy_name="테스트전략"
        )
        await scheduler._execute_signal(signal)

        vm.log_sell_by_strategy_async.assert_awaited_once_with("테스트전략", "005930", 72000, 1)
        oes.handle_place_sell_order.assert_not_called()

    async def test_execute_buy_signal_with_api(self):
        """dry_run=False: CSV 기록 + API 주문 모두 실행."""
        scheduler, vm, oes, _ = self._make_scheduler(dry_run=False)

        signal = TradeSignal(
            code="005930", name="삼성전자", action="BUY",
            price=70000, qty=1, reason="테스트", strategy_name="테스트전략"
        )
        await scheduler._execute_signal(signal)

        vm.log_buy_async.assert_awaited_once_with("테스트전략", "005930", 70000, 1)
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
        vm.log_buy_async.assert_not_called()

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

        vm.log_sell_by_strategy_async.assert_awaited_once_with("테스트전략", "005930", 80000, 1)

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
        self.assertEqual(vm.log_buy_async.call_count, 1)
        vm.log_buy_async.assert_awaited_with("테스트전략", "000660", 120000, 1)

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

    async def test_execute_signal_api_failure(self):
        """API 주문 실패 시 처리 테스트."""
        scheduler, vm, oes, _ = self._make_scheduler(dry_run=False)

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
        scheduler, vm, oes, _ = self._make_scheduler(dry_run=False)
        
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
        scheduler, _, _, _ = self._make_scheduler()
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

    async def test_persistence_save_restore(self):
        """상태 저장 및 복원 테스트."""
        scheduler, vm, _, _ = self._make_scheduler()
        strategy = MockStrategy(name="전략A")
        config = StrategySchedulerConfig(strategy=strategy)
        scheduler.register(config)
        scheduler._running = True
        config.enabled = True

        # Mock VM holds
        vm.get_holds.return_value = [{"code": "005930", "name": "삼성전자"}]

        # Save State
        with patch("builtins.open", new_callable=MagicMock) as mock_open_func:
            with patch("json.dump") as mock_json_dump:
                scheduler._save_scheduler_state()
                mock_open_func.assert_called_with(SCHEDULER_STATE_FILE, "w", encoding="utf-8")
                mock_json_dump.assert_called()
                args, _ = mock_json_dump.call_args
                self.assertEqual(args[0]["enabled_strategies"], ["전략A"])
                self.assertEqual(args[0]["current_positions"], [{"code": "005930", "name": "삼성전자"}])

        # Restore State
        scheduler._running = False
        config.enabled = False

        with patch("os.path.exists", return_value=True):
            with patch("builtins.open", new_callable=MagicMock):
                with patch("json.load", return_value={
                    "running": True,
                    "enabled_strategies": ["전략A"],
                    "current_positions": [{"code": "005930", "name": "삼성전자"}]
                }):
                    with patch.object(scheduler, '_loop', new_callable=AsyncMock):  # loop 실행 방지
                        await scheduler.restore_state()

                        self.assertTrue(scheduler._running)
                        self.assertTrue(config.enabled)

    async def test_restore_state_file_not_found(self):
        """상태 파일이 없을 때 복원 시도 테스트."""
        scheduler, _, _, _ = self._make_scheduler()
        with patch("os.path.exists", return_value=False):
            await scheduler.restore_state()
            self.assertFalse(scheduler._running)

    async def test_restore_state_corrupted_file(self):
        """상태 파일이 손상되었을 때 복원 시도 테스트."""
        scheduler, _, _, _ = self._make_scheduler()
        with patch("os.path.exists", return_value=True):
            with patch("builtins.open", mock_open(read_data="{invalid_json")):
                await scheduler.restore_state()
                scheduler._logger.error.assert_called()
                self.assertFalse(scheduler._running)

    def test_save_state_exception(self):
        """상태 저장 중 예외 발생 테스트."""
        scheduler, _, _, _ = self._make_scheduler()
        with patch("builtins.open", side_effect=IOError("Disk full")):
            scheduler._save_scheduler_state()
            scheduler._logger.error.assert_called()

    async def test_clear_saved_state(self):
        """저장된 상태 삭제 테스트."""
        scheduler, _, _, _ = self._make_scheduler()
        with patch("os.path.exists", return_value=True):
            with patch("os.remove") as mock_remove:
                scheduler.clear_saved_state()
                mock_remove.assert_called_with(SCHEDULER_STATE_FILE)

    def test_load_signal_history_real(self):
        """CSV 파일에서 시그널 이력 로드 테스트 (mocking open)."""
        # _make_scheduler는 _load_signal_history를 patch하지만 __init__ 동안만 유효하므로
        # 생성된 객체에서는 원본 메서드를 호출할 수 있음.
        scheduler, _, _, _ = self._make_scheduler()

        csv_content = "strategy_name,code,name,action,price,reason,timestamp,api_success\n" \
                      "전략A,005930,삼성전자,BUY,70000,테스트,2023-01-01 10:00:00,True"

        with patch("os.path.exists", return_value=True):
            with patch("builtins.open", mock_open(read_data=csv_content)):
                records = scheduler._load_signal_history()
                self.assertEqual(len(records), 1)
                self.assertEqual(records[0].code, "005930")
                self.assertTrue(records[0].api_success)

    async def test_loop_market_closed(self):
        """장 마감 시 루프 대기 테스트."""
        scheduler, _, _, tm = self._make_scheduler()
        tm.is_market_open.return_value = False

        scheduler._running = True

        # asyncio.sleep을 mock하여 루프를 한 번 돌고 종료하도록 설정 (CancelledError 발생)
        with patch("asyncio.sleep", side_effect=[None, asyncio.CancelledError]) as mock_sleep:
            try:
                await scheduler._loop()
            except asyncio.CancelledError:
                pass

            # sleep이 MARKET_CLOSED_SLEEP_SEC 만큼 호출되었는지 확인
            mock_sleep.assert_called_with(scheduler.MARKET_CLOSED_SLEEP_SEC)

    async def test_loop_force_exit(self):
        """장 마감 임박 시 강제 청산 로직 테스트 (전략별 설정 구분 확인)."""
        scheduler, vm, _, tm = self._make_scheduler()

        # 전략 A: 강제 청산 설정 O
        strategy_a = MockStrategy(name="전략A")
        config_a = StrategySchedulerConfig(strategy=strategy_a, force_exit_on_close=True, interval_minutes=60)
        scheduler.register(config_a)

        # 전략 B: 강제 청산 설정 X
        strategy_b = MockStrategy(name="전략B")
        config_b = StrategySchedulerConfig(strategy=strategy_b, force_exit_on_close=False, interval_minutes=60)
        scheduler.register(config_b)

        tm.is_market_open.return_value = True

        # 현재 시간: 15:20, 마감 시간: 15:30 (10분 남음 -> FORCE_EXIT_MINUTES_BEFORE(15) 이내)
        import pytz
        from datetime import datetime
        kst = pytz.timezone("Asia/Seoul")
        now = kst.localize(datetime(2023, 1, 1, 15, 20))
        close_time = kst.localize(datetime(2023, 1, 1, 15, 30))

        tm.get_current_kst_time.return_value = now
        tm.get_market_close_time.return_value = close_time

        scheduler._running = True

        # _run_strategy가 force_exit_only=True로 호출되는지 확인하기 위해 spy/mock
        with patch.object(scheduler, '_run_strategy', new_callable=AsyncMock) as mock_run:
            with patch("asyncio.sleep", side_effect=asyncio.CancelledError):
                try:
                    await scheduler._loop()
                except asyncio.CancelledError:
                    pass

            # 전략 A는 강제 청산 모드(True)로 실행되어야 함
            mock_run.assert_any_call(config_a, force_exit_only=True)
            
            # 전략 B는 일반 모드(False)로 실행되어야 함 (should_run=True 조건에 의해 실행됨)
            mock_run.assert_any_call(config_b, force_exit_only=False)

    async def test_loop_exception_handling(self):
        """루프 내 예외 발생 시 계속 실행되는지 테스트."""
        scheduler, _, _, tm = self._make_scheduler()
        tm.is_market_open.return_value = True
        
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
        scheduler, vm, oes, _ = self._make_scheduler(dry_run=False)
        
        # OES 내부에 trading_service Mock 설정
        oes.trading_service = MagicMock()
        oes.trading_service.get_current_stock_price = AsyncMock(
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
        oes.trading_service.get_current_stock_price.assert_called_with("005930")
        # 2. API 매도 주문은 가격 0(시장가)으로 호출됨
        oes.handle_place_sell_order.assert_called_once_with("005930", 0, 5)
        # 3. VM 로그 기록은 조회된 현재가(60000)로 기록됨
        vm.log_sell_by_strategy_async.assert_awaited_once_with("TestStrategy", "005930", 60000, 5)

    async def test_force_liquidate_uses_holding_qty(self):
        """강제 청산 시 설정된 주문 수량이 아닌 실제 보유 수량을 사용하는지 테스트."""
        scheduler, vm, oes, _ = self._make_scheduler(dry_run=False)

        # OES 내부에 trading_service Mock 설정
        oes.trading_service = MagicMock()
        oes.trading_service.get_current_stock_price = AsyncMock(
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
        oes.handle_place_sell_order.assert_called_once_with("005930", 0, 3)
        # VM 로그 기록도 3으로 기록되어야 함
        vm.log_sell_by_strategy_async.assert_awaited_once_with("TestStrategy", "005930", 60000, 3)

    async def test_stop_strategy_triggers_liquidation(self):
        """stop_strategy 호출 시 강제 청산 옵션이 켜져 있으면 청산 수행."""
        scheduler, _, _, _ = self._make_scheduler()
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
        scheduler, _, _, _ = self._make_scheduler()
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
        scheduler, _, _, _ = self._make_scheduler()
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
        scheduler, _, _, _ = self._make_scheduler()
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
        scheduler, _, _, _ = self._make_scheduler()
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
        scheduler, vm, oes, _ = self._make_scheduler(dry_run=False)

        # OES 내부에 trading_service Mock 설정
        oes.trading_service = MagicMock()
        oes.trading_service.get_current_stock_price = AsyncMock(
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
        oes.trading_service.get_current_stock_price.assert_called_with("000660")
        # 2. 주문은 0원(시장가)으로 나갔는지 확인
        oes.handle_place_sell_order.assert_called_once_with("000660", 0, 10)
        # 3. 로그는 조회된 80000원으로 기록되었는지 확인
        vm.log_sell_by_strategy_async.assert_awaited_once_with("TestStrat", "000660", 80000, 10)

    async def test_run_strategy_concurrent_execution(self):
        """_run_strategy가 여러 시그널을 동시(concurrent)에 처리하는지 테스트."""
        scheduler, vm, _, _ = self._make_scheduler()
        
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
        """_append_signal_csv가 asyncio.to_thread를 사용하여 동기 메서드를 실행하는지 테스트."""
        vm = MagicMock()
        oes = MagicMock()
        tm = MagicMock()
        
        # 파일 로드 방지
        with patch.object(StrategyScheduler, '_load_signal_history', return_value=[]):
            scheduler = StrategyScheduler(vm, oes, tm, dry_run=True)
        
        record = MagicMock()
        
        with patch("asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
            await scheduler._append_signal_csv(record)
            
            # asyncio.to_thread가 호출되었는지 확인
            mock_to_thread.assert_awaited_once_with(scheduler._append_signal_csv_sync, record)

    async def test_run_strategy_concurrent_execution_with_exception(self):
        """_run_strategy에서 asyncio.as_completed 사용 시 예외가 발생하면 전파되는지 테스트."""
        scheduler, vm, _, _ = self._make_scheduler()
        
        buy_signals = [
            TradeSignal(code="00001", name="S1", action="BUY", price=1000, qty=1, reason="T", strategy_name="S"),
            TradeSignal(code="00002", name="S2", action="BUY", price=1000, qty=1, reason="T", strategy_name="S"),
        ]
        
        strategy = MockStrategy(scan_signals=buy_signals)
        config = StrategySchedulerConfig(strategy=strategy, max_positions=10)
        
        # _execute_signal 모킹: 하나는 성공, 하나는 예외
        async def mock_execute(signal):
            if signal.code == "00001":
                raise ValueError("Test Error")
            return

        with patch.object(scheduler, '_execute_signal', side_effect=mock_execute) as mock_exec:
            with self.assertRaises(ValueError):
                await scheduler._run_strategy(config)
            
            # 두 시그널 모두에 대해 _execute_signal이 호출되어 코루틴이 생성되었는지 확인
            self.assertEqual(mock_exec.call_count, 2)

if __name__ == "__main__":
    unittest.main()
