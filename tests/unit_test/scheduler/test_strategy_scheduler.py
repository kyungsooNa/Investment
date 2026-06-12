# tests/unit_test/test_strategy_scheduler.py
import unittest
import asyncio
import tempfile
import shutil
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, AsyncMock, patch, mock_open, call, ANY
from common.types import TradeSignal, ErrorCode, ResCommonResponse, Exchange, OrderState, OrderSide, OrderContext
from scheduler.strategy_scheduler import StrategyScheduler, StrategySchedulerConfig, SignalRecord
from scheduler.strategy_scheduler_store import StrategySchedulerStore
from interfaces.live_strategy import LiveStrategy
from services.notification_service import NotificationCategory, NotificationLevel
from services.strategy_live_expansion_gate_service import StrategyLiveExpansionGateService


class MockStrategy(LiveStrategy):
    """테스트용 전략 구현."""

    def __init__(self, name="테스트전략", scan_signals=None, exit_signals=None, strategy_id=None, display_name=None):
        self._name = name
        self._strategy_id = strategy_id
        self._display_name = display_name
        self._scan_signals = scan_signals or []
        self._exit_signals = exit_signals or []

    @property
    def name(self) -> str:
        return self._name

    @property
    def strategy_id(self) -> str:
        return self._strategy_id or self._name

    @property
    def display_name(self) -> str:
        return self._display_name or self._name

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

    def _make_scheduler(self, dry_run=True, live_expansion_gate_service=None):
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
        oes.get_order_context = MagicMock(return_value=None)

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
            live_expansion_gate_service=live_expansion_gate_service,
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

    async def test_poll_active_orders_if_due_handles_poll_exception(self):
        from datetime import datetime

        scheduler, _, oes, _, _ = self._make_scheduler(dry_run=False)
        oes.poll_active_orders_once.side_effect = RuntimeError("poll failed")

        result = await scheduler._poll_active_orders_if_due(datetime(2026, 4, 23, 10, 0, 0))

        self.assertEqual(result, 0)
        scheduler._logger.warning.assert_called()

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
        self.assertEqual(strat_status["display_name"], "전략A")
        self.assertEqual(strat_status["current_holds"], 1)
        self.assertEqual(strat_status["holdings"], [holding_item])
        self.assertEqual(strat_status["max_positions"], 5)
        self.assertEqual(strat_status["interval_minutes"], 10)

    def test_get_status_prunes_strategy_position_state_when_virtual_trade_is_empty(self):
        """가상매매 DB에 HOLD가 없으면 전략 position_state만으로 보유를 되살리지 않는다."""
        scheduler, vm, _, _, _ = self._make_scheduler()
        strategy = MockStrategy(name="오닐PP/BGU")
        strategy._position_state = {
            "489790": SimpleNamespace(entry_price=82000, entry_date="20260424")
        }
        strategy._save_state = MagicMock()
        scheduler._signal_history = [
            SignalRecord(
                strategy_name="오닐PP/BGU",
                code="489790",
                name="한화비전",
                action="BUY",
                price=82000,
                qty=6,
                reason="test",
                timestamp="2026-04-24 13:14:18",
                api_success=True,
            )
        ]
        scheduler.register(StrategySchedulerConfig(strategy=strategy, interval_minutes=3, max_positions=5))
        vm.get_holds_by_strategy.return_value = []

        status = scheduler.get_status()

        self.assertEqual(status["strategies"][0]["current_holds"], 0)
        self.assertEqual(status["strategies"][0]["holdings"], [])
        self.assertEqual(strategy._position_state, {})
        strategy._save_state.assert_called_once()

    def test_get_status_ignores_successful_buy_signal_history_when_virtual_trade_is_empty(self):
        """성공 BUY 이력만으로는 보유 포지션을 복원하지 않는다."""
        scheduler, vm, _, _, _ = self._make_scheduler()
        strategy = MockStrategy(name="래리윌리엄스VBO")
        scheduler._signal_history = [
            SignalRecord(
                strategy_name="래리윌리엄스VBO",
                code="425420",
                name="티에프이",
                action="BUY",
                price=63500,
                qty=3,
                reason="submitted",
                timestamp="2026-04-29 09:14:17",
                api_success=True,
            ),
            SignalRecord(
                strategy_name="래리윌리엄스VBO",
                code="006340",
                name="대원전선",
                action="BUY",
                price=11620,
                qty=0,
                reason="sizing_skip:risk_zero",
                timestamp="2026-04-29 09:14:17",
                api_success=False,
            ),
        ]
        scheduler.register(StrategySchedulerConfig(strategy=strategy, max_positions=3))
        vm.get_holds_by_strategy.return_value = []

        status = scheduler.get_status()

        self.assertEqual(status["strategies"][0]["current_holds"], 0)
        self.assertEqual(status["strategies"][0]["holdings"], [])

    def test_get_status_uses_virtual_trade_holdings_as_current_position_source(self):
        """현재 보유 목록은 signal_history/position_state가 아니라 virtual trade 원장을 기준으로 한다."""
        scheduler, vm, _, _, _ = self._make_scheduler()
        strategy = MockStrategy(name="거래량돌파(전통)")
        strategy._position_state = {
            "B": SimpleNamespace(breakout_level=1000, peak_price=1100),
            "010060": SimpleNamespace(breakout_level=326500, peak_price=377500),
        }
        strategy._save_state = MagicMock()
        scheduler._signal_history = [
            SignalRecord(
                strategy_name="거래량돌파(전통)",
                code="010060",
                name="OCI홀딩스",
                action="BUY",
                price=377500,
                qty=1,
                reason="test",
                timestamp="2026-04-24 12:38:34",
                api_success=True,
            )
        ]
        scheduler.register(StrategySchedulerConfig(strategy=strategy, interval_minutes=1, max_positions=5))
        vm.get_holds_by_strategy.return_value = [{
            "strategy": "거래량돌파(전통)",
            "code": "010060",
            "name": "OCI홀딩스",
            "buy_price": 326500,
            "qty": 2,
            "buy_date": "2026-04-24 12:39:00",
            "status": "HOLD",
        }]

        status = scheduler.get_status()

        self.assertEqual(status["strategies"][0]["current_holds"], 1)
        holding = status["strategies"][0]["holdings"][0]
        self.assertEqual(holding["code"], "010060")
        self.assertEqual(holding["buy_price"], 326500)
        self.assertEqual(holding["qty"], 2)
        self.assertEqual(holding["buy_date"], "2026-04-24 12:39:00")
        self.assertNotIn("B", strategy._position_state)

    def test_get_status_prunes_disabled_force_exit_strategy_state_without_position_evidence(self):
        """비활성 당일청산 전략에 DB/신호 근거 없는 state만 남으면 stale로 정리한다."""
        scheduler, vm, _, _, _ = self._make_scheduler()
        strategy = MockStrategy(name="거래량돌파(전통)")
        strategy._position_state = {
            "B": SimpleNamespace(breakout_level=1000, peak_price=1100),
            "010060": SimpleNamespace(breakout_level=326500, peak_price=377500),
        }
        strategy._save_state = MagicMock()
        scheduler.register(StrategySchedulerConfig(
            strategy=strategy,
            interval_minutes=1,
            max_positions=5,
            enabled=False,
            force_exit_on_close=True,
        ))
        vm.get_holds_by_strategy.return_value = []

        status = scheduler.get_status()

        self.assertEqual(status["strategies"][0]["current_holds"], 0)
        self.assertEqual(status["strategies"][0]["holdings"], [])
        self.assertEqual(strategy._position_state, {})
        strategy._save_state.assert_called_once()

    def test_get_status_prunes_disabled_force_exit_strategy_state_even_when_signal_evidence_exists(self):
        """비활성 당일청산 전략은 과거 시그널 이력만으로 stale state를 유지하지 않는다."""
        scheduler, vm, _, _, _ = self._make_scheduler()
        strategy = MockStrategy(name="거래량돌파(전통)")
        strategy._position_state = {
            "010060": SimpleNamespace(breakout_level=326500, peak_price=377500),
        }
        strategy._save_state = MagicMock()
        scheduler._signal_history = [
            SignalRecord(
                strategy_name="거래량돌파(전통)",
                code="010060",
                name="OCI홀딩스",
                action="BUY",
                price=377500,
                qty=1,
                reason="test",
                timestamp="2026-04-24 12:38:34",
                api_success=True,
            )
        ]
        scheduler.register(StrategySchedulerConfig(
            strategy=strategy,
            interval_minutes=1,
            max_positions=5,
            enabled=False,
            force_exit_on_close=True,
        ))
        vm.get_holds_by_strategy.return_value = []

        status = scheduler.get_status()

        self.assertEqual(status["strategies"][0]["current_holds"], 0)
        self.assertEqual(status["strategies"][0]["holdings"], [])
        self.assertEqual(strategy._position_state, {})
        strategy._save_state.assert_called_once()

    def test_get_status_empty(self):
        """전략 미등록 상태에서 get_status 테스트."""
        scheduler, _, _, _, _ = self._make_scheduler()
        status = scheduler.get_status()

        self.assertFalse(status["running"])
        self.assertEqual(len(status["strategies"]), 0)

    async def test_stop_flushes_pending_strategy_state_saves(self):
        """스케줄러 종료 시 background state save task 완료를 기다린다."""
        scheduler, _, _, _, _ = self._make_scheduler()

        with patch("scheduler.strategy_scheduler.StrategyStateIO.flush_pending", new=AsyncMock()) as flush:
            await scheduler.stop(save_state=True)

        flush.assert_awaited_once()

    def test_get_status_with_strategy(self):
        """전략 등록 후 get_status 테스트."""
        scheduler, _, _, _, _ = self._make_scheduler()
        strategy = MockStrategy()
        scheduler.register(StrategySchedulerConfig(strategy=strategy, max_positions=3))

        status = scheduler.get_status()
        self.assertEqual(len(status["strategies"]), 1)
        self.assertEqual(status["strategies"][0]["name"], "테스트전략")
        self.assertEqual(status["strategies"][0]["display_name"], "테스트전략")
        self.assertEqual(status["strategies"][0]["max_positions"], 3)

    def test_get_status_exposes_strategy_id_and_display_name(self):
        """상태 API는 storage key와 표시명을 분리해 노출한다."""
        scheduler, _, _, _, _ = self._make_scheduler()
        strategy = MockStrategy(
            name="legacy-display",
            strategy_id="stable_strategy_id",
            display_name="표시 전략명",
        )
        scheduler.register(StrategySchedulerConfig(strategy=strategy, max_positions=3))

        status = scheduler.get_status()

        strategy_status = status["strategies"][0]
        self.assertEqual(strategy_status["name"], "legacy-display")
        self.assertEqual(strategy_status["strategy_id"], "stable_strategy_id")
        self.assertEqual(strategy_status["display_name"], "표시 전략명")

    async def test_execute_buy_signal_passes_signal_price_policy_to_order_execution(self):
        """TradeSignal 가격 정책 필드를 주문 실행 계층으로 전달한다."""
        scheduler, _, oes, _, _ = self._make_scheduler(dry_run=False)
        signal = TradeSignal(
            code="005930",
            name="삼성전자",
            action="BUY",
            price=70_000,
            qty=1,
            reason="테스트",
            strategy_name="테스트전략",
            invalidation_price=68_000,
            stop_loss_price=66_000,
        )

        await scheduler._execute_signal(signal)

        oes.handle_place_buy_order.assert_awaited_once()
        kwargs = oes.handle_place_buy_order.await_args.kwargs
        self.assertEqual(kwargs["invalidation_price"], 68_000)
        self.assertEqual(kwargs["stop_loss_price"], 66_000)

    async def test_execute_buy_signal_dry_run(self):
        """dry_run 모드에서 BUY 시그널 실행: CSV만 기록, API 미호출."""
        scheduler, vm, oes, _, _ = self._make_scheduler(dry_run=True)

        signal = TradeSignal(
            code="005930", name="삼성전자", action="BUY",
            price=70000, qty=1, reason="테스트", strategy_name="테스트전략"
        )
        await scheduler._execute_signal(signal)

        vm.log_buy_async.assert_awaited_once_with("테스트전략", "005930", 70000, 1, volatility_20d_annualized=None)
        oes.handle_place_buy_order.assert_not_called()

    async def test_execute_buy_signal_dry_run_passes_config_hash_when_present(self):
        """dry-run journal 기록 시 signal.config_hash 를 보존한다."""
        scheduler, vm, _, _, _ = self._make_scheduler(dry_run=True)

        signal = TradeSignal(
            code="005930", name="삼성전자", action="BUY",
            price=70000, qty=1, reason="테스트", strategy_name="테스트전략",
            config_hash="abc123def456",
        )
        await scheduler._execute_signal(signal)

        vm.log_buy_async.assert_awaited_once_with(
            "테스트전략",
            "005930",
            70000,
            1,
            volatility_20d_annualized=None,
            config_hash="abc123def456",
        )

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
            trace_id=ANY,
            volatility_20d_annualized=None,
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

    async def test_run_strategy_allows_exits_but_skips_scan_when_live_gate_blocks_entries(self):
        """실전 확대 gate 차단은 기존 포지션 청산을 유지하고 신규 scan만 막는다."""
        gate = MagicMock()
        gate.check_strategy.return_value = SimpleNamespace(
            allowed=False,
            reason="profitability_gate_missing",
            details={},
        )
        scheduler, vm, _, _, _ = self._make_scheduler(
            dry_run=False,
            live_expansion_gate_service=gate,
        )
        vm.get_holds_by_strategy.return_value = [
            {"code": "005930", "name": "삼성전자", "buy_price": 70000, "qty": 1},
        ]
        sell_signal = TradeSignal(
            code="005930", name="삼성전자", action="SELL",
            price=72000, qty=1, reason="익절", strategy_name="테스트전략"
        )
        strategy = MockStrategy(
            scan_signals=[
                TradeSignal(
                    code="000660", name="SK하이닉스", action="BUY",
                    price=120000, qty=1, reason="스캔", strategy_name="테스트전략"
                )
            ],
            exit_signals=[sell_signal],
        )
        strategy.scan = AsyncMock(return_value=strategy._scan_signals)
        config = StrategySchedulerConfig(strategy=strategy, max_positions=3)
        scheduler.register(config)

        with patch.object(scheduler, "_execute_signal", new_callable=AsyncMock) as mock_execute:
            await scheduler._run_strategy(config)

        mock_execute.assert_awaited_once_with(sell_signal)
        strategy.scan.assert_not_awaited()
        scheduler._logger.warning.assert_called()

    async def test_execute_buy_signal_blocks_order_when_live_gate_blocks_entries(self):
        """방어선: scan 밖에서 들어온 BUY도 실전 확대 gate 미통과면 주문하지 않는다."""
        gate = MagicMock()
        gate.check_strategy.return_value = SimpleNamespace(
            allowed=False,
            reason="profitability_gate_fail",
            details={"blocking_reasons": ["profit_factor_below"]},
        )
        scheduler, _, oes, _, _ = self._make_scheduler(
            dry_run=False,
            live_expansion_gate_service=gate,
        )

        signal = TradeSignal(
            code="005930", name="삼성전자", action="BUY",
            price=70000, qty=1, reason="테스트", strategy_name="테스트전략"
        )
        await scheduler._execute_signal(signal)

        oes.handle_place_buy_order.assert_not_called()
        self.assertEqual(scheduler._signal_history[-1].api_success, False)
        self.assertIn("profitability_gate_blocked", scheduler._signal_history[-1].reason)

    async def test_execute_sell_signal_ignores_live_gate(self):
        """실전 확대 gate는 청산 주문을 막지 않는다."""
        gate = MagicMock()
        gate.check_strategy.return_value = SimpleNamespace(
            allowed=False,
            reason="profitability_gate_fail",
            details={},
        )
        scheduler, _, oes, _, _ = self._make_scheduler(
            dry_run=False,
            live_expansion_gate_service=gate,
        )

        signal = TradeSignal(
            code="005930", name="삼성전자", action="SELL",
            price=72000, qty=1, reason="익절", strategy_name="테스트전략"
        )
        await scheduler._execute_signal(signal)

        gate.check_strategy.assert_not_called()
        oes.handle_place_sell_order.assert_awaited_once()

    # ── P1 1-6: 실제 gate 서비스로 fail-close end-to-end 잠금 ───────────
    # 위 테스트들은 mock 결정에 대한 scheduler 반응만 검증한다. 아래는 실제
    # StrategyLiveExpansionGateService 를 주입해, journal provider 부재/실적
    # 부재라는 *실전 fail-close 조건* 자체가 BUY 를 막는지 end-to-end 로 고정한다.

    async def test_real_gate_fail_closes_buy_when_journal_provider_absent(self):
        """실전 모드 + journal provider 부재 → 실제 gate가 BUY를 fail-close 한다."""
        gate = StrategyLiveExpansionGateService(
            journal_records_provider=None,
            is_paper_trading_fn=lambda: False,
        )
        scheduler, _, oes, _, _ = self._make_scheduler(
            dry_run=False, live_expansion_gate_service=gate,
        )
        signal = TradeSignal(
            code="005930", name="삼성전자", action="BUY",
            price=70000, qty=1, reason="테스트", strategy_name="테스트전략",
        )
        await scheduler._execute_signal(signal)

        oes.handle_place_buy_order.assert_not_called()
        self.assertFalse(scheduler._signal_history[-1].api_success)
        self.assertIn("profitability_gate_unavailable", scheduler._signal_history[-1].reason)

    async def test_real_gate_fail_closes_buy_when_strategy_missing_from_journal(self):
        """실전 모드 + journal에 해당 전략 실적 없음 → profitability_gate_missing 으로 fail-close."""
        gate = StrategyLiveExpansionGateService(
            journal_records_provider=lambda: [],  # 기록 없음 → 전략 결과 부재
            is_paper_trading_fn=lambda: False,
        )
        scheduler, _, oes, _, _ = self._make_scheduler(
            dry_run=False, live_expansion_gate_service=gate,
        )
        signal = TradeSignal(
            code="005930", name="삼성전자", action="BUY",
            price=70000, qty=1, reason="테스트", strategy_name="테스트전략",
        )
        await scheduler._execute_signal(signal)

        oes.handle_place_buy_order.assert_not_called()
        self.assertFalse(scheduler._signal_history[-1].api_success)
        self.assertIn("profitability_gate_missing", scheduler._signal_history[-1].reason)

    async def test_real_gate_allows_buy_in_paper_mode_without_journal(self):
        """모의 모드에서는 실제 gate가 provider 부재여도 진입을 막지 않는다 (paper_mode bypass)."""
        gate = StrategyLiveExpansionGateService(
            journal_records_provider=None,
            is_paper_trading_fn=lambda: True,
        )
        scheduler, _, _, _, _ = self._make_scheduler(
            dry_run=False, live_expansion_gate_service=gate,
        )
        decision = scheduler._check_live_expansion_gate("테스트전략")
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.reason, "paper_mode")

    def test_virtual_trade_log_kwargs_includes_price_policy_fields(self):
        """P1 1-6 (b): signal의 price-policy 3필드가 journal log kwargs로 전달된다."""
        signal = TradeSignal(
            code="005930", name="삼성전자", action="BUY",
            price=70000, qty=1, reason="테스트", strategy_name="테스트전략",
            invalidation_price=68000.0, stop_loss_price=66500.0, target_price=80000.0,
        )
        kwargs = StrategyScheduler._virtual_trade_log_kwargs(signal)
        self.assertEqual(kwargs["invalidation_price"], 68000.0)
        self.assertEqual(kwargs["stop_loss_price"], 66500.0)
        self.assertEqual(kwargs["target_price"], 80000.0)

    def test_virtual_trade_log_kwargs_omits_absent_price_policy_fields(self):
        """price-policy 미지정(None) 필드는 kwargs에 넣지 않는다 (log_buy 기본 None 유지)."""
        signal = TradeSignal(
            code="005930", name="삼성전자", action="BUY",
            price=70000, qty=1, reason="테스트", strategy_name="테스트전략",
        )
        kwargs = StrategyScheduler._virtual_trade_log_kwargs(signal)
        self.assertNotIn("invalidation_price", kwargs)
        self.assertNotIn("stop_loss_price", kwargs)
        self.assertNotIn("target_price", kwargs)

    def test_virtual_trade_log_kwargs_includes_signal_metadata_fields(self):
        """P1 1-6: signal의 metadata 5필드가 journal log kwargs로 전달된다."""
        signal = TradeSignal(
            code="005930", name="삼성전자", action="BUY",
            price=70000, qty=1, reason="테스트", strategy_name="테스트전략",
            entry_reason="pocket_pivot_breakout", trailing_rule="atr_2x",
            expected_holding_period_days=5, confidence=0.75,
            required_data=["ohlcv", "volume_profile"],
        )
        kwargs = StrategyScheduler._virtual_trade_log_kwargs(signal)
        self.assertEqual(kwargs["entry_reason"], "pocket_pivot_breakout")
        self.assertEqual(kwargs["trailing_rule"], "atr_2x")
        self.assertEqual(kwargs["expected_holding_period_days"], 5)
        self.assertEqual(kwargs["confidence"], 0.75)
        self.assertEqual(kwargs["required_data"], ["ohlcv", "volume_profile"])

    def test_virtual_trade_log_kwargs_omits_absent_signal_metadata_fields(self):
        """metadata 미지정(None) 필드는 kwargs에 넣지 않는다 (log_buy 기본 None 유지)."""
        signal = TradeSignal(
            code="005930", name="삼성전자", action="BUY",
            price=70000, qty=1, reason="테스트", strategy_name="테스트전략",
        )
        kwargs = StrategyScheduler._virtual_trade_log_kwargs(signal)
        self.assertNotIn("entry_reason", kwargs)
        self.assertNotIn("trailing_rule", kwargs)
        self.assertNotIn("expected_holding_period_days", kwargs)
        self.assertNotIn("confidence", kwargs)
        self.assertNotIn("required_data", kwargs)

    # ── R-2: 매수 시점 market_regime snapshot 캡처 ──────────────────────

    @staticmethod
    def _regime_svc_with(kospi=None, kosdaq=None):
        svc = MagicMock()
        snaps = {}
        if kospi is not None:
            s = MagicMock(); s.regime_label = kospi; snaps["KOSPI"] = s
        if kosdaq is not None:
            s = MagicMock(); s.regime_label = kosdaq; snaps["KOSDAQ"] = s
        svc.get_cached_snapshot.side_effect = lambda m: snaps.get(m)
        return svc

    def test_market_regime_log_kwargs_builds_dict_from_cached_snapshots(self):
        svc = self._regime_svc_with(kospi="bull", kosdaq="sideways")
        repo = MagicMock(); repo.is_kosdaq.return_value = False
        kwargs = StrategyScheduler._market_regime_log_kwargs(svc, repo, "005930")
        self.assertEqual(kwargs["market_regime"], {
            "kospi": "bull", "kosdaq": "sideways", "stock_market": "KOSPI",
        })

    def test_market_regime_log_kwargs_marks_kosdaq_stock(self):
        svc = self._regime_svc_with(kospi="bear", kosdaq="bear")
        repo = MagicMock(); repo.is_kosdaq.return_value = True
        kwargs = StrategyScheduler._market_regime_log_kwargs(svc, repo, "035720")
        self.assertEqual(kwargs["market_regime"]["stock_market"], "KOSDAQ")
        self.assertEqual(kwargs["market_regime"]["kosdaq"], "bear")

    def test_market_regime_log_kwargs_omitted_without_service(self):
        self.assertEqual(
            StrategyScheduler._market_regime_log_kwargs(None, MagicMock(), "005930"), {}
        )

    def test_market_regime_log_kwargs_omitted_when_no_snapshot(self):
        svc = MagicMock(); svc.get_cached_snapshot.return_value = None
        self.assertEqual(
            StrategyScheduler._market_regime_log_kwargs(svc, MagicMock(), "005930"), {}
        )

    def test_market_regime_log_kwargs_partial_snapshot_keeps_none_label(self):
        svc = self._regime_svc_with(kospi="bull")  # KOSDAQ 미분류
        repo = MagicMock(); repo.is_kosdaq.return_value = False
        kwargs = StrategyScheduler._market_regime_log_kwargs(svc, repo, "005930")
        self.assertEqual(kwargs["market_regime"]["kospi"], "bull")
        self.assertIsNone(kwargs["market_regime"]["kosdaq"])

    # ── P2 2-4 exit fast-path shadow: 보유 종목 손절 shadow 구독/기록 ──────

    def _make_shadow_scheduler(self):
        """event_router + event_shadow_journal 주입된 scheduler (price_sub_svc 없음 → sync no-op)."""
        scheduler, vm, oes, tm, mcs = self._make_scheduler(dry_run=True)
        scheduler._event_router = MagicMock()
        scheduler._event_router.subscribe = MagicMock()
        scheduler._event_router.unsubscribe = MagicMock()
        scheduler._event_shadow_journal = MagicMock()
        return scheduler, vm

    async def test_refresh_exit_shadow_subscribes_held_codes_with_exit_subscriber_name(self):
        """event_driven_shadow 전략의 보유 종목을 exit subscriber name 으로 router 구독한다."""
        scheduler, vm = self._make_shadow_scheduler()
        vm.get_holds_by_strategy.return_value = [
            {"code": "005930", "name": "삼성전자", "buy_price": 70000, "qty": 1},
        ]
        cfg = StrategySchedulerConfig(strategy=MockStrategy(name="VBO"), event_driven_shadow=True)

        await scheduler._refresh_exit_shadow_subscriptions(cfg)

        scheduler._event_router.subscribe.assert_called_once()
        _, kwargs = scheduler._event_router.subscribe.call_args
        self.assertEqual(scheduler._event_router.subscribe.call_args.args[0], "005930")
        self.assertTrue(kwargs["strategy_name"].endswith("__exit"))
        self.assertTrue(callable(kwargs["evaluator"]))

    async def test_refresh_exit_shadow_noop_when_flag_off(self):
        """event_driven_shadow=False 면 보유 종목이 있어도 구독하지 않는다."""
        scheduler, vm = self._make_shadow_scheduler()
        vm.get_holds_by_strategy.return_value = [{"code": "005930", "buy_price": 70000, "qty": 1}]
        cfg = StrategySchedulerConfig(strategy=MockStrategy(name="VBO"), event_driven_shadow=False)

        await scheduler._refresh_exit_shadow_subscriptions(cfg)

        scheduler._event_router.subscribe.assert_not_called()

    async def test_refresh_exit_shadow_unsubscribes_sold_codes(self):
        """이전 사이클 보유 종목이 청산되면 다음 refresh 에서 unsubscribe 한다."""
        scheduler, vm = self._make_shadow_scheduler()
        cfg = StrategySchedulerConfig(strategy=MockStrategy(name="VBO"), event_driven_shadow=True)

        vm.get_holds_by_strategy.return_value = [{"code": "005930", "buy_price": 70000, "qty": 1}]
        await scheduler._refresh_exit_shadow_subscriptions(cfg)

        vm.get_holds_by_strategy.return_value = []
        await scheduler._refresh_exit_shadow_subscriptions(cfg)

        scheduler._event_router.unsubscribe.assert_called_once()
        self.assertEqual(scheduler._event_router.unsubscribe.call_args.args[0], "005930")
        self.assertTrue(scheduler._event_router.unsubscribe.call_args.args[1].endswith("__exit"))

    async def test_exit_shadow_evaluator_records_signal_source_exit_and_returns_none(self):
        """exit shadow evaluator: evaluate_exit_single → journal(signal_source=event_shadow_exit) → None."""
        scheduler, _ = self._make_shadow_scheduler()
        sell = TradeSignal(code="005930", name="삼성전자", action="SELL",
                           price=67800, qty=1, reason="칼손절(net,shadow)", strategy_name="VBO")
        strategy = MagicMock()
        strategy.name = "VBO"
        strategy.evaluate_exit_single = AsyncMock(return_value=sell)
        holdings_by_code = {"005930": {"code": "005930", "buy_price": 70000, "qty": 1}}

        evaluator = scheduler._build_exit_shadow_evaluator(strategy, holdings_by_code)
        result = await evaluator("005930", {"price": "67800"})

        self.assertIsNone(result)  # 실 주문 미발생
        scheduler._event_shadow_journal.record.assert_called_once()
        _, rkwargs = scheduler._event_shadow_journal.record.call_args
        self.assertEqual(rkwargs["signal_source"], "event_shadow_exit")
        self.assertEqual(rkwargs["code"], "005930")
        self.assertEqual(rkwargs["strategy_name"], "VBO")

    async def test_exit_shadow_evaluator_no_signal_does_not_record(self):
        """evaluate_exit_single 가 None 이면 journal 기록하지 않는다."""
        scheduler, _ = self._make_shadow_scheduler()
        strategy = MagicMock()
        strategy.name = "VBO"
        strategy.evaluate_exit_single = AsyncMock(return_value=None)

        evaluator = scheduler._build_exit_shadow_evaluator(strategy, {"005930": {"buy_price": 70000}})
        result = await evaluator("005930", {"price": "70000"})

        self.assertIsNone(result)
        scheduler._event_shadow_journal.record.assert_not_called()

    async def test_run_strategy_scans_and_logs_rejections_when_position_full_option_enabled(self):
        """옵션이 켜져 있으면 max_positions 도달 후에도 스캔하고 매수 신호를 reject 로그로 남긴다."""
        scheduler, vm, _, _, _ = self._make_scheduler()

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
        strategy.scan = AsyncMock(return_value=[buy_signal])
        config = StrategySchedulerConfig(
            strategy=strategy,
            max_positions=3,
            scan_when_position_full=True,
        )

        await scheduler._run_strategy(config)

        strategy.scan.assert_awaited_once()
        vm.log_buy_async.assert_not_called()

        rejection_logs = [
            args[0]
            for args, _ in scheduler._logger.info.call_args_list
            if args and isinstance(args[0], dict) and args[0].get("event") == "signal_rejected"
        ]
        self.assertEqual(len(rejection_logs), 1)
        self.assertEqual(rejection_logs[0]["reason"], "max_positions_reached")
        self.assertEqual(rejection_logs[0]["current_holds"], 3)
        self.assertEqual(rejection_logs[0]["max_positions"], 3)

    async def test_run_strategy_logs_rejections_for_signals_beyond_remaining_slots(self):
        """남은 슬롯을 초과한 매수 신호도 포지션 한도 reject 로그로 남긴다."""
        scheduler, vm, _, _, _ = self._make_scheduler()
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

        vm.log_buy_async.assert_awaited_once_with("테스트전략", "000660", 120000, 1, volatility_20d_annualized=None)
        rejection_logs = [
            args[0]
            for args, _ in scheduler._logger.info.call_args_list
            if args and isinstance(args[0], dict) and args[0].get("event") == "signal_rejected"
        ]
        self.assertEqual(len(rejection_logs), 1)
        self.assertEqual(rejection_logs[0]["code"], "035420")
        self.assertEqual(rejection_logs[0]["reason"], "max_positions_reached")

    async def test_run_strategy_uses_virtual_trade_holdings_for_exit_and_capacity(self):
        """exit/max_positions 판단은 virtual trade 원장의 HOLD만 사용한다."""
        scheduler, vm, _, _, _ = self._make_scheduler()
        strategy = MockStrategy(name="오닐PP/BGU")
        strategy._position_state = {
            "489790": SimpleNamespace(entry_price=82000, entry_date="20260424")
        }
        strategy.check_exits = AsyncMock(return_value=[])
        strategy.scan = AsyncMock(return_value=[
            TradeSignal(
                code="100840", name="SNT에너지", action="BUY",
                price=57700, qty=8, reason="scan", strategy_name="오닐PP/BGU"
            )
        ])
        scheduler._signal_history = [
            SignalRecord(
                strategy_name="오닐PP/BGU",
                code="489790",
                name="한화비전",
                action="BUY",
                price=82000,
                qty=6,
                reason="test",
                timestamp="2026-04-24 13:14:18",
                api_success=True,
            )
        ]
        config = StrategySchedulerConfig(strategy=strategy, max_positions=1)
        scheduler.register(config)
        vm.get_holds_by_strategy.return_value = [{
            "strategy": "오닐PP/BGU",
            "code": "489790",
            "name": "한화비전",
            "buy_price": 82000,
            "qty": 6,
            "buy_date": "2026-04-24 13:14:18",
            "status": "HOLD",
        }]

        await scheduler._run_strategy(config)

        strategy.check_exits.assert_awaited_once()
        self.assertEqual(strategy.check_exits.await_args.args[0][0]["code"], "489790")
        strategy.scan.assert_not_awaited()

    async def test_run_strategy_ignores_state_only_holding_and_continues_scan(self):
        """원장 HOLD가 없으면 state-only 보유는 청산 대상/포지션 슬롯으로 취급하지 않는다."""
        scheduler, vm, _, _, _ = self._make_scheduler()
        strategy = MockStrategy(name="거래량돌파(전통)")
        strategy._position_state = {
            "010060": SimpleNamespace(breakout_level=326500, peak_price=377500)
        }
        strategy._save_state = MagicMock()
        strategy.check_exits = AsyncMock(return_value=[])
        strategy.scan = AsyncMock(return_value=[])
        scheduler._signal_history = [
            SignalRecord(
                strategy_name="거래량돌파(전통)",
                code="010060",
                name="OCI홀딩스",
                action="BUY",
                price=377500,
                qty=1,
                reason="test",
                timestamp="2026-04-24 12:38:34",
                api_success=True,
            )
        ]
        config = StrategySchedulerConfig(strategy=strategy, max_positions=5)
        scheduler.register(config)
        vm.get_holds_by_strategy.return_value = []

        await scheduler._run_strategy(config)

        strategy.check_exits.assert_not_awaited()
        strategy.scan.assert_awaited_once()
        self.assertEqual(strategy._position_state, {})

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
        vm.log_buy_async.assert_any_await("테스트전략", "A", 1000, 1, volatility_20d_annualized=None)
        vm.log_buy_async.assert_any_await("테스트전략", "B", 2000, 1, volatility_20d_annualized=None)

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
        vm.log_buy_async.assert_awaited_with("테스트전략", "000660", 120000, 1, volatility_20d_annualized=None)

    async def test_run_strategy_rolls_back_rejected_stateful_scan_signal(self):
        """전략 scan이 position_state를 먼저 늘려도 rejected 신호는 즉시 롤백한다."""
        scheduler, vm, _, _, _ = self._make_scheduler()

        vm.get_holds_by_strategy.return_value = [
            {"code": "005930", "buy_price": 70000},
            {"code": "000660", "buy_price": 120000},
            {"code": "035420", "buy_price": 300000},
            {"code": "068270", "buy_price": 200000},
        ]

        buy_signals = [
            TradeSignal(code="111111", name="첫번째", action="BUY",
                        price=1000, qty=1, reason="t1", strategy_name="오닐PP/BGU"),
            TradeSignal(code="222222", name="두번째", action="BUY",
                        price=2000, qty=1, reason="t2", strategy_name="오닐PP/BGU"),
        ]
        strategy = MockStrategy(name="오닐PP/BGU")
        strategy._position_state = {}
        strategy._save_state = MagicMock()

        async def scan_with_state_side_effect():
            strategy._position_state["111111"] = SimpleNamespace(entry_price=1000)
            strategy._position_state["222222"] = SimpleNamespace(entry_price=2000)
            return buy_signals

        strategy.scan = AsyncMock(side_effect=scan_with_state_side_effect)
        config = StrategySchedulerConfig(strategy=strategy, max_positions=5)

        await scheduler._run_strategy(config)

        vm.log_buy_async.assert_awaited_once_with("오닐PP/BGU", "111111", 1000, 1, volatility_20d_annualized=None)
        self.assertIn("111111", strategy._position_state)
        self.assertNotIn("222222", strategy._position_state)
        strategy._save_state.assert_called_once()

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
        vm.log_buy_async.assert_any_await("테스트전략", "000660", 120000, 1, volatility_20d_annualized=None)
        vm.log_buy_async.assert_any_await("테스트전략", "035420", 300000, 1, volatility_20d_annualized=None)
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
        vm.log_buy_async.assert_awaited_with("테스트전략", "000660", 120000, 1, volatility_20d_annualized=None)

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
        vm.log_buy_async.assert_any_await("테스트전략", "A", 1000, 1, volatility_20d_annualized=None)
        vm.log_buy_async.assert_any_await("테스트전략", "B", 2000, 1, volatility_20d_annualized=None)

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
        vm.log_buy_async.assert_any_await("테스트전략", "005930", 71000, 1, volatility_20d_annualized=None)
        vm.log_buy_async.assert_any_await("테스트전략", "000660", 120000, 1, volatility_20d_annualized=None)

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

    async def test_start_strategy_clears_stop_event_after_full_stop(self):
        """전체 정지 후 개별 전략 시작 시 루프가 즉시 종료되지 않도록 stop_event를 해제한다."""
        scheduler, _, _, _, _ = self._make_scheduler()
        strategy = MockStrategy(name="RSI2눌림목")
        scheduler.register(StrategySchedulerConfig(strategy=strategy, enabled=False))
        scheduler._stop_event.set()

        with patch.object(scheduler, '_loop', new_callable=AsyncMock):
            self.assertTrue(await scheduler.start_strategy("RSI2눌림목"))

        self.assertFalse(scheduler._stop_event.is_set())
        self.assertTrue(scheduler._running)
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

        vm.get_holds_by_strategy.return_value = [{"code": "005930", "name": "삼성전자"}]

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

    async def test_restore_state_does_not_start_duplicate_loop_when_already_running(self):
        """이미 실행 중이면 restore_state가 새 메인 루프를 만들지 않아야 한다."""
        scheduler, _, _, _, _ = self._make_scheduler()
        scheduler.register(StrategySchedulerConfig(strategy=MockStrategy(name="전략A")))
        scheduler._running = True
        existing_task = object()
        scheduler._task = existing_task

        def close_unexpected_coro(coro):
            coro.close()
            return object()

        with patch("scheduler.strategy_scheduler.asyncio.create_task", side_effect=close_unexpected_coro) as mock_create_task:
            await scheduler.restore_state()

        scheduler._store.load_state.assert_not_called()
        mock_create_task.assert_not_called()
        self.assertIs(scheduler._task, existing_task)
        scheduler._logger.warning.assert_called_with(
            "[Scheduler] 이미 실행 중 - 상태 복원으로 새 루프를 만들지 않습니다."
        )

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

    async def test_loop_market_closed_does_not_force_exit(self):
        """장 종료 후에는 force_exit 시그널을 새로 만들지 않는다."""
        scheduler, _, _, _, mcs = self._make_scheduler()
        mcs.is_market_open_now.return_value = False
        mcs.wait_until_next_open.side_effect = asyncio.CancelledError()

        strategy = MockStrategy(name="전략A")
        config = StrategySchedulerConfig(
            strategy=strategy,
            force_exit_on_close=True,
            enabled=True,
        )
        scheduler.register(config)
        scheduler._running = True

        with patch.object(scheduler, "_run_strategy", new_callable=AsyncMock) as mock_run:
            try:
                await scheduler._loop()
            except asyncio.CancelledError:
                pass

        mock_run.assert_not_awaited()
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

        # 현재 시간: 15:05, 마감 시간: 15:30 (25분 남음 -> 강제청산 윈도우 이내, 컷오프 이전)
        import pytz
        from datetime import datetime
        kst = pytz.timezone("Asia/Seoul")
        now = kst.localize(datetime(2023, 1, 1, 15, 5))
        # 두 번째 루프에서는 쿨다운(60초) 이후 시점
        now_after_cooldown = kst.localize(datetime(2023, 1, 1, 15, 6, 1))
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

    async def test_loop_skips_strategy_after_order_cutoff(self):
        """주문 컷오프 이후에는 일반 실행과 강제청산 모두 시그널을 만들지 않는다."""
        from datetime import datetime

        scheduler, _, _, tm, mcs = self._make_scheduler()
        now_dt = datetime(2026, 5, 19, 15, 20, 0)
        close_dt = datetime(2026, 5, 19, 15, 40, 0)
        tm.get_current_kst_time.return_value = now_dt
        tm.get_market_close_time.return_value = close_dt

        strategy = MockStrategy(name="전략A")
        config = StrategySchedulerConfig(
            strategy=strategy,
            force_exit_on_close=True,
            interval_minutes=0,
        )
        scheduler.register(config)
        scheduler._running = True
        mcs.is_market_open_now.side_effect = [True, asyncio.CancelledError()]

        with patch.object(scheduler, "_run_strategy", new_callable=AsyncMock) as mock_run:
            try:
                await scheduler._loop()
            except asyncio.CancelledError:
                pass

        mock_run.assert_not_awaited()

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
            source="strategy_force_exit:TestStrategy",
            finalize_immediately=False,
            trace_id=ANY,
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
            source="strategy_force_exit:TestStrategy",
            finalize_immediately=False,
            trace_id=ANY,
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

    async def test_stop_strategy_clears_force_exit_position_state(self):
        """당일청산 전략 정지 후 내부 position_state가 UI 보유로 되살아나지 않도록 정리한다."""
        scheduler, _, _, _, _ = self._make_scheduler()
        strategy = MockStrategy(name="AutoCloseStrategy")
        strategy._position_state = {"005930": SimpleNamespace(entry_price=70000)}
        strategy._save_state = MagicMock()
        config = StrategySchedulerConfig(
            strategy=strategy,
            force_exit_on_close=True,
            enabled=True,
        )
        scheduler.register(config)

        with patch.object(scheduler, '_force_liquidate_strategy', new_callable=AsyncMock):
            await scheduler.stop_strategy("AutoCloseStrategy")

        self.assertEqual(strategy._position_state, {})
        strategy._save_state.assert_called_once()

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
            trace_id=ANY,
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
            trace_id=ANY,
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

    async def test_duplicate_strategy_failure_alert_is_suppressed_for_same_day(self):
        scheduler, _, oes, _, _ = self._make_scheduler(dry_run=False)
        notification_service = AsyncMock()
        notification_service.emit = AsyncMock()
        scheduler._notification_service = notification_service
        oes.handle_place_sell_order.return_value = ResCommonResponse(
            rt_cd=ErrorCode.ORDER_POLICY_BLOCKED.value,
            msg1="Order Policy 차단: 거래정지 상태 종목은 주문할 수 없습니다.",
        )
        signal = TradeSignal(
            code="037030",
            name="파워넷",
            action="SELL",
            price=9980,
            qty=1,
            reason="오버나이트방어: 매수일(20260506) ≠ 오늘(20260507)",
            strategy_name="래리윌리엄스VBO",
        )

        await scheduler._execute_signal(signal)
        await scheduler._execute_signal(signal)

        notification_service.emit.assert_awaited_once()
        args, _ = notification_service.emit.await_args
        self.assertEqual(args[0], NotificationCategory.STRATEGY)
        self.assertIn("Order Policy 차단", args[3])

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

    async def test_run_strategy_does_not_override_signal_qty(self):
        """override 제거 — cfg.order_qty는 qty=None dry-run 전용 fallback이며,
        전략이 명시한 qty>0 신호는 그대로 사용한다."""
        scheduler, vm, _, _, _ = self._make_scheduler()

        strategy = MockStrategy(scan_signals=[
            TradeSignal(code="005930", name="Samsung", action="BUY", price=1000, qty=1, reason="T", strategy_name="S")
        ])
        config = StrategySchedulerConfig(strategy=strategy, order_qty=10)

        await scheduler._run_strategy(config)

        # qty=1 신호는 cfg.order_qty=10 으로 override 되지 않고 1 그대로 기록
        vm.log_buy_async.assert_awaited_once_with("S", "005930", 1000, 1, volatility_20d_annualized=None)

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

    def test_get_status_includes_force_exit_on_close(self):
        """get_status()가 force_exit_on_close 필드를 올바르게 노출하는지 테스트."""
        scheduler, _, _, _, _ = self._make_scheduler()
        strategy_on = MockStrategy(name="당일청산전략")
        strategy_off = MockStrategy(name="일반전략")
        scheduler.register(StrategySchedulerConfig(strategy=strategy_on, force_exit_on_close=True))
        scheduler.register(StrategySchedulerConfig(strategy=strategy_off, force_exit_on_close=False))

        status = scheduler.get_status()
        by_name = {s["name"]: s for s in status["strategies"]}
        self.assertTrue(by_name["당일청산전략"]["force_exit_on_close"])
        self.assertFalse(by_name["일반전략"]["force_exit_on_close"])

    async def test_execute_signal_buy_failure_cleans_position_state(self):
        """BUY API 실패 시 strategy의 선반영 매수 상태가 제거되는지 테스트."""
        scheduler, _, oes, _, _ = self._make_scheduler(dry_run=False)
        strategy = MockStrategy(name="테스트전략")
        strategy._position_state = {"028050": {}}
        strategy._bought_today = {"028050"}
        strategy._save_state = MagicMock()
        scheduler.register(StrategySchedulerConfig(strategy=strategy))

        oes.handle_place_buy_order.return_value = ResCommonResponse(
            rt_cd=ErrorCode.API_ERROR.value, msg1="매수 거부"
        )
        signal = TradeSignal(
            code="028050", name="보락", action="BUY",
            price=5000, qty=1, reason="테스트", strategy_name="테스트전략"
        )
        await scheduler._execute_signal(signal)

        self.assertNotIn("028050", strategy._position_state)
        self.assertNotIn("028050", strategy._bought_today)
        strategy._save_state.assert_called_once()

    async def test_order_policy_buy_block_excludes_code_from_universe_for_day(self):
        """정책 차단 BUY 실패는 universe 당일 제외 목록에 등록해 반복 주문을 막는다."""
        scheduler, _, oes, _, _ = self._make_scheduler(dry_run=False)
        strategy = MockStrategy(name="첫눌림목")
        strategy._universe = MagicMock()
        strategy._universe.exclude_code_for_today = MagicMock()
        scheduler.register(StrategySchedulerConfig(strategy=strategy))

        oes.handle_place_buy_order.return_value = ResCommonResponse(
            rt_cd=ErrorCode.ORDER_POLICY_BLOCKED.value,
            msg1="Order Policy 차단: 투자경고/위험 또는 거래정지 상태 종목은 주문할 수 없습니다.",
            data={
                "gate": "order_policy",
                "rule": "investment_warning_stock",
                "reason": "투자경고/위험 또는 거래정지 상태 종목은 주문할 수 없습니다.",
            },
        )
        signal = TradeSignal(
            code="033640", name="네패스", action="BUY",
            price=32050, qty=62, reason="첫눌림목", strategy_name="첫눌림목",
        )

        await scheduler._execute_signal(signal)

        strategy._universe.exclude_code_for_today.assert_called_once()
        args, kwargs = strategy._universe.exclude_code_for_today.call_args
        self.assertEqual(args[0], "033640")
        self.assertEqual(kwargs["reason"], "investment_warning_stock")

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
            source="strategy_force_exit:S",
            finalize_immediately=False,
            trace_id=ANY,
        )

    async def test_force_liquidate_uses_broker_holding_for_successful_buy_missing_journal(self):
        """원장 HOLD 누락이어도 당일 성공 BUY 이력과 실제 잔고가 있으면 강제 청산한다."""
        from datetime import datetime

        scheduler, vm, oes, tm, _ = self._make_scheduler(dry_run=False)
        tm.get_current_kst_time.return_value = datetime(2026, 6, 9, 15, 10, 0)

        strategy = MockStrategy(name="래리윌리엄스VBO")
        config = StrategySchedulerConfig(strategy=strategy, force_exit_on_close=True, order_qty=1)
        vm.get_holds_by_strategy.return_value = []

        scheduler._signal_history = [
            SignalRecord(
                strategy_name="래리윌리엄스VBO",
                code="023530",
                name="롯데쇼핑",
                action="BUY",
                price=179100,
                qty=11,
                reason="VBO돌파",
                timestamp="2026-06-09 10:07:35",
                api_success=True,
            )
        ]
        oes.broker_api_wrapper.get_account_balance = AsyncMock(
            return_value=ResCommonResponse(
                rt_cd=ErrorCode.SUCCESS.value,
                msg1="OK",
                data={"output1": [{"pdno": "023530", "hldg_qty": "11", "prdt_name": "롯데쇼핑"}]},
            )
        )
        oes.broker_api_wrapper.get_asking_price = AsyncMock(
            return_value=ResCommonResponse(
                rt_cd=ErrorCode.SUCCESS.value,
                msg1="OK",
                data={"output1": {"bidp1": "180000"}},
            )
        )

        await scheduler._force_liquidate_strategy(config)

        oes.handle_place_sell_order.assert_called_once_with(
            "023530",
            180000,
            11,
            exchange=Exchange.KRX,
            source="strategy_force_exit:래리윌리엄스VBO",
            finalize_immediately=False,
            trace_id=ANY,
        )

    async def test_force_liquidate_skips_signal_history_recovery_when_buy_order_active(self):
        """당일 BUY 주문이 아직 미체결 대기 중이면 신호 이력만으로 강제 청산하지 않는다."""
        from datetime import datetime

        scheduler, vm, oes, tm, _ = self._make_scheduler(dry_run=False)
        tm.get_current_kst_time.return_value = datetime(2026, 6, 12, 15, 10, 0)

        strategy = MockStrategy(name="래리윌리엄스VBO")
        config = StrategySchedulerConfig(strategy=strategy, force_exit_on_close=True, order_qty=1)
        vm.get_holds_by_strategy.return_value = []

        scheduler._signal_history = [
            SignalRecord(
                strategy_name="래리윌리엄스VBO",
                code="403870",
                name="HPSP",
                action="BUY",
                price=71500,
                qty=27,
                reason="VBO돌파",
                timestamp="2026-06-12 12:02:47",
                api_success=True,
            )
        ]
        oes.get_order_context.return_value = OrderContext(
            order_key="KRX:403870:BUY",
            stock_code="403870",
            side=OrderSide.BUY,
            state=OrderState.SUBMITTED,
            exchange=Exchange.KRX,
            price=71500,
            qty=27,
            filled_qty=0,
            broker_order_no="0000024244",
            source="strategy:래리윌리엄스VBO",
        )
        oes.broker_api_wrapper.get_account_balance = AsyncMock(
            return_value=ResCommonResponse(
                rt_cd=ErrorCode.SUCCESS.value,
                msg1="OK",
                data={"output1": [{"pdno": "403870", "hldg_qty": "27", "prdt_name": "HPSP"}]},
            )
        )

        await scheduler._force_liquidate_strategy(config)

        oes.handle_place_sell_order.assert_not_called()
        self.assertTrue(
            any(
                "active BUY order" in str(call_args)
                for call_args in scheduler._logger.warning.call_args_list
            )
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

    async def test_start_returns_early_when_already_running(self):
        scheduler, _, _, _, _ = self._make_scheduler()
        scheduler._running = True
        scheduler._kill_switch = AsyncMock()

        await scheduler.start()

        scheduler._logger.warning.assert_called_once()
        scheduler._kill_switch.check_strategies_allowed.assert_not_awaited()
        self.assertIsNone(scheduler._task)

    async def test_start_logs_kill_switch_warning_and_still_starts(self):
        scheduler, _, _, _, _ = self._make_scheduler()
        scheduler._kill_switch = AsyncMock()
        scheduler._kill_switch.check_strategies_allowed = AsyncMock(
            return_value=(False, "manual trip")
        )
        scheduler.register(StrategySchedulerConfig(strategy=MockStrategy(name="Guarded")))

        with patch.object(scheduler, "_loop", new_callable=AsyncMock):
            await scheduler.start()
            await scheduler.stop(save_state=True)

        scheduler._kill_switch.check_strategies_allowed.assert_awaited_once()
        scheduler._logger.warning.assert_called()

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
        self.assertTrue("주문 접수" in args[2])
        self.assertTrue("체결은 별도 확인 필요" in args[3])

    async def test_execute_sell_signal_notification_includes_estimated_return_rate(self):
        """실전 매도 알림은 체결 확정 전에도 보유 매수가 기준 수익률을 포함한다."""
        scheduler, vm, oes, _, _ = self._make_scheduler(dry_run=False)
        vm.get_holds_by_strategy.return_value = [
            {"strategy": "S1", "code": "005930", "buy_price": 10000, "qty": 1}
        ]
        mock_notifier = AsyncMock()
        scheduler._notification_service = mock_notifier

        signal = TradeSignal(
            code="005930", name="삼성전자", action="SELL",
            price=11000, qty=1, reason="익절", strategy_name="S1"
        )

        await scheduler._execute_signal(signal)

        vm.log_sell_by_strategy_async.assert_not_awaited()
        mock_notifier.emit.assert_awaited_once()
        args, kwargs = mock_notifier.emit.call_args
        self.assertEqual(args[0], NotificationCategory.STRATEGY)
        self.assertEqual(kwargs["metadata"]["return_rate"], 10.0)
        self.assertEqual(scheduler._signal_history[-1].return_rate, 10.0)

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
            trace_id=ANY,
            volatility_20d_annualized=None,
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

    async def test_loop_market_closed_does_not_run_force_exit_exception_path(self):
        """장 마감 후에는 강제 청산을 시도하지 않아 예외 경로도 타지 않는다."""
        scheduler, vm, _, tm, mcs = self._make_scheduler()

        mcs.is_market_open_now.return_value = False
        mcs.wait_until_next_open.side_effect = asyncio.CancelledError() # 루프 탈출용
        
        strategy = MockStrategy(name="전략A")
        config = StrategySchedulerConfig(strategy=strategy, force_exit_on_close=True, enabled=True)
        scheduler.register(config)
        
        scheduler._force_exit_done = set() 
        scheduler._running = True
        
        with patch.object(scheduler, '_run_strategy', side_effect=Exception("Liquidate Error")) as mock_run:
            try:
                await scheduler._loop()
            except asyncio.CancelledError:
                pass

        mock_run.assert_not_called()
        scheduler._logger.error.assert_not_called()

    # ── Kill Switch 테스트 ───────────────────────────────────────────────────

    async def test_loop_skips_strategy_when_kill_switch_tripped(self):
        """Kill Switch 트립 시 _loop()가 일반 전략을 실행하지 않는지 테스트."""
        from datetime import datetime
        scheduler, _, oes, tm, mcs = self._make_scheduler()

        mock_ks = AsyncMock()
        mock_ks.check_strategies_allowed = AsyncMock(return_value=(False, "일 손실 한도 초과"))
        scheduler._kill_switch = mock_ks

        now_dt = datetime(2026, 4, 24, 10, 0, 0)
        close_dt = datetime(2026, 4, 24, 15, 30, 0)
        tm.get_current_kst_time.return_value = now_dt
        tm.get_market_close_time.return_value = close_dt

        strategy = MockStrategy(name="전략A", scan_signals=[])
        config = StrategySchedulerConfig(strategy=strategy, interval_minutes=0)
        scheduler.register(config)
        scheduler._running = True

        mcs.is_market_open_now.side_effect = [True, asyncio.CancelledError()]

        with patch.object(scheduler, "_run_strategy", new_callable=AsyncMock) as mock_run:
            try:
                await scheduler._loop()
            except asyncio.CancelledError:
                pass

        mock_run.assert_not_awaited()
        scheduler._logger.warning.assert_called()

    async def test_loop_allows_force_exit_when_kill_switch_tripped(self):
        """Kill Switch 트립 중에도 force_exit_on_close 강제 청산은 실행되는지 테스트."""
        from datetime import datetime
        scheduler, _, oes, tm, mcs = self._make_scheduler()

        mock_ks = AsyncMock()
        mock_ks.check_strategies_allowed = AsyncMock(return_value=(False, "수동 트립"))
        scheduler._kill_switch = mock_ks

        # 마감 25분 전 (FORCE_EXIT_MINUTES_BEFORE=30 이내)
        now_dt = datetime(2026, 4, 24, 15, 5, 0)
        close_dt = datetime(2026, 4, 24, 15, 30, 0)
        tm.get_current_kst_time.return_value = now_dt
        tm.get_market_close_time.return_value = close_dt

        strategy = MockStrategy(name="전략A")
        config = StrategySchedulerConfig(
            strategy=strategy, force_exit_on_close=True, interval_minutes=60
        )
        scheduler.register(config)
        scheduler._running = True

        mcs.is_market_open_now.side_effect = [True, asyncio.CancelledError()]

        with patch.object(scheduler, "_run_strategy", new_callable=AsyncMock) as mock_run:
            try:
                await scheduler._loop()
            except asyncio.CancelledError:
                pass

        # force_exit_only=True 로 한 번 호출되어야 함
        mock_run.assert_awaited_once()
        _, kwargs = mock_run.call_args
        self.assertTrue(kwargs.get("force_exit_only"))

    async def test_loop_skips_due_strategy_inside_stagger_window(self):
        from datetime import datetime

        scheduler, _, _, tm, mcs = self._make_scheduler()
        now_dt = datetime(2026, 4, 24, 10, 0, 0)
        close_dt = datetime(2026, 4, 24, 15, 30, 0)
        tm.get_current_kst_time.return_value = now_dt
        tm.get_market_close_time.return_value = close_dt
        mcs.is_market_open_now.return_value = True

        strategy = MockStrategy(name="Staggered")
        scheduler.register(StrategySchedulerConfig(strategy=strategy, interval_minutes=0))
        scheduler._last_execution_time = now_dt
        scheduler._running = True

        with patch.object(scheduler, "_run_reconciliation", new_callable=AsyncMock), \
             patch.object(scheduler, "_run_strategy", new_callable=AsyncMock) as mock_run, \
             patch("asyncio.sleep", side_effect=asyncio.CancelledError):
            await scheduler._loop()

        mock_run.assert_not_awaited()

    async def test_force_liquidate_uses_best_bid_when_orderbook_available(self):
        scheduler, vm, oes, _, _ = self._make_scheduler(dry_run=False)
        oes.broker_api_wrapper.get_asking_price = AsyncMock(
            return_value=ResCommonResponse(
                rt_cd=ErrorCode.SUCCESS.value,
                msg1="OK",
                data={"bidp1": "12345"},
            )
        )
        vm.get_holds_by_strategy.return_value = [
            {"code": "005930", "name": "Samsung", "qty": 2}
        ]
        config = StrategySchedulerConfig(strategy=MockStrategy(name="BidExit"), order_qty=7)

        with patch.object(scheduler, "_execute_signal", new_callable=AsyncMock) as mock_exec:
            await scheduler._force_liquidate_strategy(config)

        signal = mock_exec.await_args.args[0]
        self.assertEqual(signal.price, 12345)
        self.assertEqual(signal.qty, 2)

    async def test_force_liquidate_reads_nested_output1_best_bid(self):
        scheduler, vm, oes, _, _ = self._make_scheduler(dry_run=False)
        oes.broker_api_wrapper.get_asking_price = AsyncMock(
            return_value=ResCommonResponse(
                rt_cd=ErrorCode.SUCCESS.value,
                msg1="OK",
                data={"output1": {"bidp1": "8560"}},
            )
        )
        vm.get_holds_by_strategy.return_value = [
            {"code": "037030", "name": "Powernet", "qty": 1}
        ]
        config = StrategySchedulerConfig(strategy=MockStrategy(name="NestedBidExit"))

        with patch.object(scheduler, "_execute_signal", new_callable=AsyncMock) as mock_exec:
            await scheduler._force_liquidate_strategy(config)

        signal = mock_exec.await_args.args[0]
        self.assertEqual(signal.price, 8560)
        self.assertIn("지정가", signal.reason)

    async def test_execute_force_liquidation_signal_marks_force_exit_source(self):
        scheduler, _, oes, _, _ = self._make_scheduler(dry_run=False)

        signal = TradeSignal(
            code="037030", name="Powernet", action="SELL",
            price=8560, qty=1,
            reason="전략 종료 강제 청산 (지정가 8,560원)",
            strategy_name="래리윌리엄스VBO",
        )
        await scheduler._execute_signal(signal)

        self.assertEqual(
            oes.handle_place_sell_order.await_args.kwargs["source"],
            "strategy_force_exit:래리윌리엄스VBO",
        )

    async def test_execute_strategy_sell_reprices_to_best_bid_when_above_book(self):
        scheduler, _, oes, _, _ = self._make_scheduler(dry_run=False)
        oes.broker_api_wrapper = MagicMock()
        oes.broker_api_wrapper.get_asking_price = AsyncMock(
            return_value=ResCommonResponse(
                rt_cd=ErrorCode.SUCCESS.value,
                msg1="OK",
                data={"output1": {"bidp1": "7850"}},
            )
        )

        signal = TradeSignal(
            code="037030", name="Powernet", action="SELL",
            price=8210, qty=58,
            reason="하드스탑(고점대비 -17.7%)",
            strategy_name="오닐PP/BGU",
        )
        await scheduler._execute_signal(signal)

        oes.handle_place_sell_order.assert_awaited_once()
        self.assertEqual(oes.handle_place_sell_order.await_args.args[1], 7850)
        self.assertEqual(scheduler._signal_history[-1].price, 7850)

    async def test_run_reconciliation_clears_strategy_state_for_force_closed_codes(self):
        scheduler, vm, oes, _, _ = self._make_scheduler(dry_run=False)
        strategy = MockStrategy(name="오닐PP/BGU")
        strategy._position_state = {"064350": SimpleNamespace(entry_price=265500)}
        strategy._save_state = MagicMock()
        scheduler.register(StrategySchedulerConfig(strategy=strategy))
        oes.broker_api_wrapper.get_account_balance = AsyncMock(
            return_value=ResCommonResponse(
                rt_cd=ErrorCode.SUCCESS.value,
                msg1="OK",
                data={"output1": []},
            )
        )
        vm.reconcile_with_broker = AsyncMock(
            return_value={"force_closed": ["064350"], "unknown_in_broker": []}
        )

        await scheduler._run_reconciliation()

        self.assertEqual(strategy._position_state, {})
        strategy._save_state.assert_called_once()

    async def test_run_reconciliation_emits_warning_on_mismatch(self):
        scheduler, vm, oes, _, _ = self._make_scheduler(dry_run=False)
        scheduler._notification_service = AsyncMock()
        oes.broker_api_wrapper.get_account_balance = AsyncMock(
            return_value=ResCommonResponse(
                rt_cd=ErrorCode.SUCCESS.value,
                msg1="OK",
                data={"output1": [{"pdno": "005930"}]},
            )
        )
        vm.reconcile_with_broker = AsyncMock(
            return_value={"force_closed": ["000660"], "unknown_in_broker": []}
        )

        await scheduler._run_reconciliation()

        vm.reconcile_with_broker.assert_awaited_once()
        scheduler._notification_service.emit.assert_awaited_once()

    async def test_run_reconciliation_skips_when_balance_api_fails(self):
        scheduler, vm, oes, _, _ = self._make_scheduler(dry_run=False)
        oes.broker_api_wrapper.get_account_balance = AsyncMock(
            return_value=ResCommonResponse(rt_cd="1", msg1="fail", data=None)
        )
        vm.reconcile_with_broker = AsyncMock()

        await scheduler._run_reconciliation()

        vm.reconcile_with_broker.assert_not_awaited()
        scheduler._logger.warning.assert_called()

    async def test_run_reconciliation_logs_exception(self):
        scheduler, _, oes, _, _ = self._make_scheduler(dry_run=False)
        oes.broker_api_wrapper.get_account_balance = AsyncMock(
            side_effect=RuntimeError("balance failed")
        )

        await scheduler._run_reconciliation()

        scheduler._logger.error.assert_called()

    def test_signal_history_helpers_ignore_mismatches_and_closed_buys(self):
        scheduler, _, _, _, _ = self._make_scheduler()
        scheduler._signal_history = [
            SignalRecord("Other", "005930", "Other", "BUY", 1000, 5, api_success=True),
            SignalRecord("S", "005930", "Samsung", "BUY", 1000, 3, api_success=True),
            SignalRecord("S", "005930", "Samsung", "SELL", 1100, 3, api_success=True),
            SignalRecord("S", "005930", "Samsung", "BUY", 1200, 2, api_success=False),
        ]

        self.assertEqual(scheduler._get_signal_net_qty("S", "005930", only_success=True), 0)
        self.assertEqual(scheduler._get_signal_net_qty("S", "005930", only_success=False), 2)
        self.assertIsNone(scheduler._get_latest_open_buy_record("S", "005930", only_success=True))
        self.assertEqual(
            scheduler._get_latest_open_buy_record("S", "005930", only_success=False).price,
            1200,
        )

    def test_persist_strategy_position_state_logs_save_failure(self):
        scheduler, _, _, _, _ = self._make_scheduler()
        strategy = MockStrategy(name="Stateful")
        strategy._save_state = MagicMock(side_effect=RuntimeError("save failed"))

        scheduler._persist_strategy_position_state(strategy)

        scheduler._logger.warning.assert_called()

    async def test_restore_state_saves_when_disabled_force_exit_state_pruned(self):
        scheduler, _, _, _, _ = self._make_scheduler()
        strategy = MockStrategy(name="DisabledForceExit")
        strategy._position_state = {"123456": SimpleNamespace(entry_price=1000)}
        strategy._save_state = MagicMock()
        scheduler.register(StrategySchedulerConfig(
            strategy=strategy,
            enabled=False,
            force_exit_on_close=True,
        ))
        scheduler._store.load_state.return_value = {
            "enabled_strategies": [],
            "current_positions": [{"code": "999999"}],
            "strategy_configs": {"DisabledForceExit": {"max_positions": 9}},
        }

        with patch.object(scheduler, "_save_scheduler_state") as mock_save:
            await scheduler.restore_state()

        self.assertEqual(scheduler._strategies[0].max_positions, 9)
        self.assertEqual(strategy._position_state, {})
        mock_save.assert_called_once()

    async def test_restore_state_logs_unexpected_restore_error(self):
        scheduler, _, _, _, _ = self._make_scheduler()
        scheduler._store.load_state.return_value = {"enabled_strategies": ["S"]}
        scheduler.register(StrategySchedulerConfig(strategy=MockStrategy(name="S")))

        with patch("asyncio.create_task", side_effect=RuntimeError("task failed")):
            await scheduler.restore_state()

        scheduler._logger.error.assert_called()

    # ── 시장 상태 필터 ON/OFF ────────────────────────────────────────────────

    async def test_loop_runs_enabled_strategy_when_market_open(self):
        """시장 상태 필터 ON: 장중 + enabled=True → _run_strategy 호출."""
        from datetime import datetime
        scheduler, _, _, tm, mcs = self._make_scheduler()

        now_dt = datetime(2026, 4, 24, 10, 0, 0)
        close_dt = datetime(2026, 4, 24, 15, 30, 0)
        tm.get_current_kst_time.return_value = now_dt
        tm.get_market_close_time.return_value = close_dt

        strategy = MockStrategy(name="EnabledStrat")
        config = StrategySchedulerConfig(strategy=strategy, enabled=True, interval_minutes=0)
        scheduler.register(config)
        scheduler._running = True

        # 첫 루프: 장중 처리, 두 번째: CancelledError로 루프 탈출
        mcs.is_market_open_now.side_effect = [True, asyncio.CancelledError()]

        with patch.object(scheduler, "_run_reconciliation", new_callable=AsyncMock), \
             patch.object(scheduler, "_run_strategy", new_callable=AsyncMock) as mock_run, \
             patch.object(scheduler, "_poll_active_orders_if_due", new_callable=AsyncMock), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            try:
                await scheduler._loop()
            except asyncio.CancelledError:
                pass

        mock_run.assert_awaited()

    async def test_loop_skips_all_strategies_when_market_closed(self):
        """시장 상태 필터 OFF: 장 외 시간 → _run_strategy 호출 없이 wait_until_next_open 대기."""
        scheduler, _, _, _, mcs = self._make_scheduler()

        mcs.is_market_open_now.return_value = False
        mcs.wait_until_next_open.side_effect = asyncio.CancelledError()

        strategy = MockStrategy(name="AnyStrat")
        scheduler.register(StrategySchedulerConfig(strategy=strategy, enabled=True, interval_minutes=0))
        scheduler._running = True

        with patch.object(scheduler, "_run_strategy", new_callable=AsyncMock) as mock_run:
            try:
                await scheduler._loop()
            except asyncio.CancelledError:
                pass

        mock_run.assert_not_awaited()
        mcs.wait_until_next_open.assert_awaited_once()

    async def test_loop_continues_after_market_closed_wait_returns(self):
        scheduler, _, _, _, mcs = self._make_scheduler()
        scheduler._running = True
        scheduler._force_exit_done = set()
        mcs.is_market_open_now.return_value = False
        mcs.wait_until_next_open = AsyncMock(
            side_effect=[None, asyncio.CancelledError()]
        )

        await scheduler._loop()

        self.assertEqual(mcs.wait_until_next_open.await_count, 2)

    async def test_loop_skips_disabled_strategy_and_logs_run_exception(self):
        from datetime import datetime

        scheduler, _, _, tm, mcs = self._make_scheduler()
        now_dt = datetime(2026, 4, 24, 10, 0, 0)
        tm.get_current_kst_time.return_value = now_dt
        tm.get_market_close_time.return_value = datetime(2026, 4, 24, 15, 30, 0)
        mcs.is_market_open_now.return_value = True
        scheduler.register(StrategySchedulerConfig(
            strategy=MockStrategy(name="Disabled"),
            enabled=False,
            interval_minutes=0,
        ))
        scheduler.register(StrategySchedulerConfig(
            strategy=MockStrategy(name="Raises"),
            interval_minutes=0,
        ))
        scheduler._running = True

        with patch.object(scheduler, "_run_reconciliation", new_callable=AsyncMock), \
             patch.object(scheduler, "_run_strategy", new_callable=AsyncMock) as mock_run, \
             patch("asyncio.sleep", side_effect=asyncio.CancelledError):
            mock_run.side_effect = RuntimeError("run failed")
            await scheduler._loop()

        mock_run.assert_awaited_once()
        scheduler._logger.error.assert_called()

    async def test_loop_emits_notification_on_unexpected_loop_error(self):
        scheduler, _, _, _, mcs = self._make_scheduler()
        scheduler._notification_service = AsyncMock()
        scheduler._running = True
        mcs.is_market_open_now.side_effect = RuntimeError("calendar failed")

        with patch("asyncio.sleep", side_effect=asyncio.CancelledError):
            try:
                await scheduler._loop()
            except asyncio.CancelledError:
                pass

        scheduler._notification_service.emit.assert_awaited_once()

    async def test_loop_runs_due_strategy_after_stagger_window(self):
        from datetime import datetime, timedelta

        scheduler, _, _, tm, mcs = self._make_scheduler()
        now_dt = datetime(2026, 4, 24, 10, 0, 0)
        tm.get_current_kst_time.return_value = now_dt
        tm.get_market_close_time.return_value = datetime(2026, 4, 24, 15, 30, 0)
        mcs.is_market_open_now.return_value = True
        scheduler.register(StrategySchedulerConfig(
            strategy=MockStrategy(name="Due"),
            interval_minutes=0,
        ))
        scheduler._last_execution_time = now_dt - timedelta(
            seconds=StrategyScheduler.STAGGER_INTERVAL_SEC
        )
        scheduler._running = True

        with patch.object(scheduler, "_run_reconciliation", new_callable=AsyncMock), \
             patch.object(scheduler, "_run_strategy", new_callable=AsyncMock) as mock_run, \
             patch("asyncio.sleep", side_effect=asyncio.CancelledError):
            await scheduler._loop()

        mock_run.assert_awaited_once()

    async def test_execute_signal_dry_run_uses_name_fallback_and_price_subscriptions(self):
        scheduler, vm, _, _, _ = self._make_scheduler(dry_run=True)
        scheduler.stock_code_repository.get_name_by_code.return_value = "Samsung"
        scheduler._price_sub_svc = AsyncMock()

        buy_sig = TradeSignal(
            code="005930", name="005930", action="BUY",
            price=70000, qty=1, reason="Test", strategy_name="DrySub"
        )
        await scheduler._execute_signal(buy_sig)

        self.assertEqual(buy_sig.name, "Samsung")
        scheduler._price_sub_svc.add_subscription.assert_awaited_once()

        sell_sig = TradeSignal(
            code="005930", name="", action="SELL",
            price=71000, qty=1, reason="Test", strategy_name="DrySub"
        )
        await scheduler._execute_signal(sell_sig)

        self.assertEqual(sell_sig.name, "Samsung")
        scheduler._price_sub_svc.remove_subscription.assert_awaited_once_with(
            "005930", "scheduler_DrySub"
        )

    async def test_force_liquidate_skips_empty_code_and_falls_back_on_orderbook_error(self):
        scheduler, vm, oes, _, _ = self._make_scheduler(dry_run=False)
        oes.broker_api_wrapper.get_asking_price = AsyncMock(
            side_effect=RuntimeError("orderbook failed")
        )
        vm.get_holds_by_strategy.return_value = [
            {"code": "", "name": "Empty", "qty": 1},
            {"code": "005930", "name": "Samsung", "qty": 0},
        ]
        config = StrategySchedulerConfig(
            strategy=MockStrategy(name="ExitFallback"),
            order_qty=7,
        )

        with patch.object(scheduler, "_execute_signal", new_callable=AsyncMock) as mock_exec:
            await scheduler._force_liquidate_strategy(config)

        mock_exec.assert_awaited_once()
        signal = mock_exec.await_args.args[0]
        self.assertEqual(signal.code, "005930")
        self.assertEqual(signal.price, 0)
        self.assertEqual(signal.qty, 7)
        scheduler._logger.warning.assert_called()

    def test_position_evidence_uses_repo_holdings_and_persist_noop_without_save_hook(self):
        scheduler, _, _, _, _ = self._make_scheduler()
        strategy = MockStrategy(name="NoSaveHook")
        scheduler._signal_history = [
            SignalRecord("S", "005930", "Samsung", "BUY", 70000, 1, api_success=True)
        ]

        self.assertTrue(
            scheduler._has_open_position_evidence(
                "S",
                "005930",
                repo_holdings=[{"code": " 005930 "}],
            )
        )
        self.assertFalse(
            scheduler._has_open_position_evidence(
                "S",
                "005930",
                repo_holdings=[],
                allow_signal_history=False,
            )
        )

        scheduler._persist_strategy_position_state(strategy)

        scheduler._logger.warning.assert_not_called()

    def test_prune_stale_position_state_keeps_recent_successful_buy_signal(self):
        scheduler, _, _, _, _ = self._make_scheduler()
        strategy = MockStrategy(name="Stateful")
        strategy._position_state = {"023530": SimpleNamespace(entry_price=191100)}
        scheduler._signal_history = [
            SignalRecord(
                strategy_name="Stateful",
                code="023530",
                name="LotteShopping",
                action="BUY",
                price=191100,
                qty=10,
                reason="entry",
                timestamp="2023-01-01 10:00:01",
                api_success=True,
            )
        ]
        config = StrategySchedulerConfig(strategy=strategy)

        removed = scheduler._prune_stale_position_state(config, repo_holdings=[])

        self.assertFalse(removed)
        self.assertIn("023530", strategy._position_state)

    def test_position_evidence_successful_sell_clears_successful_buy_signal(self):
        scheduler, _, _, _, _ = self._make_scheduler()
        scheduler._signal_history = [
            SignalRecord(
                "Stateful", "023530", "LotteShopping", "BUY", 191100, 10,
                timestamp="2023-01-01 10:00:01",
                api_success=True,
            ),
            SignalRecord(
                "Stateful", "023530", "LotteShopping", "SELL", 190000, 10,
                timestamp="2023-01-01 10:05:01",
                api_success=True,
            ),
        ]

        self.assertFalse(
            scheduler._has_open_position_evidence(
                "Stateful",
                "023530",
                repo_holdings=[],
            )
        )

    def test_build_strategy_state_holding_uses_failed_signal_entry_date_and_name_fallbacks(self):
        scheduler, _, _, _, _ = self._make_scheduler()
        scheduler.stock_code_repository.get_name_by_code.return_value = "FallbackName"
        scheduler._signal_history = [
            SignalRecord(
                strategy_name="StateFallback",
                code="005930",
                name="",
                action="BUY",
                price=70000,
                qty=3,
                reason="failed buy",
                timestamp="",
                api_success=False,
            )
        ]

        holding = scheduler._build_strategy_state_holding(
            "StateFallback",
            "005930",
            SimpleNamespace(entry_date="20260424"),
        )

        self.assertEqual(holding["buy_price"], 70000)
        self.assertEqual(holding["qty"], 3)
        self.assertEqual(holding["buy_date"], "2026-04-24 00:00:00")
        self.assertEqual(holding["name"], "FallbackName")

        holding_with_text_date = scheduler._build_strategy_state_holding(
            "NoSignal",
            "000660",
            SimpleNamespace(entry_date="2026/04/24"),
        )

        self.assertEqual(holding_with_text_date["buy_date"], "2026/04/24")
        self.assertEqual(holding_with_text_date["qty"], 1)

    def test_get_strategy_holdings_skips_blank_position_state_code(self):
        scheduler, vm, _, _, _ = self._make_scheduler()
        strategy = MockStrategy(name="BlankState")
        strategy._position_state = {
            "": SimpleNamespace(entry_price=1000),
            "005930": SimpleNamespace(entry_price=70000),
        }
        strategy._save_state = MagicMock()
        scheduler._signal_history = [
            SignalRecord(
                strategy_name="BlankState",
                code="005930",
                name="Samsung",
                action="BUY",
                price=70000,
                qty=1,
                timestamp="2026-04-24 09:00:00",
                api_success=True,
            )
        ]
        scheduler.stock_code_repository.get_name_by_code.return_value = "Samsung"
        vm.get_holds_by_strategy.return_value = []
        config = StrategySchedulerConfig(strategy=strategy)

        holdings = scheduler._get_strategy_holdings(config)

        self.assertEqual(holdings, [])
        self.assertEqual(strategy._position_state, {})
        strategy._save_state.assert_called_once()

    def test_get_strategy_holdings_prunes_state_without_open_evidence(self):
        scheduler, vm, _, _, _ = self._make_scheduler()
        strategy = MockStrategy(name="첫눌림목")
        strategy._position_state = {
            "819550": SimpleNamespace(entry_price=96000, entry_date="20260308"),
        }
        strategy._save_state = MagicMock()
        vm.get_holds_by_strategy.return_value = []
        config = StrategySchedulerConfig(strategy=strategy)

        holdings = scheduler._get_strategy_holdings(config)

        self.assertEqual(holdings, [])
        self.assertEqual(strategy._position_state, {})
        strategy._save_state.assert_called_once()
        scheduler._logger.warning.assert_called()

    async def test_restore_state_price_subscription_skips_unrestored_strategy_and_empty_code(self):
        scheduler, vm, _, _, _ = self._make_scheduler()
        scheduler._price_sub_svc = AsyncMock()
        scheduler.register(StrategySchedulerConfig(strategy=MockStrategy(name="Restored")))
        scheduler.register(StrategySchedulerConfig(strategy=MockStrategy(name="Stopped")))
        scheduler._store.load_state.return_value = {
            "enabled_strategies": ["Restored"],
            "current_positions": [],
            "strategy_configs": {},
        }
        vm.get_holds_by_strategy.return_value = [
            {"code": ""},
            {"code": "005930"},
        ]

        with patch.object(scheduler, "_loop", new_callable=AsyncMock):
            await scheduler.restore_state()

        scheduler._price_sub_svc.add_subscription.assert_awaited_once()
        self.assertEqual(
            scheduler._price_sub_svc.add_subscription.await_args.args[0],
            "005930",
        )

if __name__ == "__main__":
    unittest.main()
