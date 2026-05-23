"""StrategyScheduler signal_id / strategy_id stamping (P3-4 Phase 2c + TradeSignal Phase 2).

scheduler 는 scan() / check_exits() 직후 각 TradeSignal 에 대해
- signal_id (없으면 uuid4 stamp)
- strategy_id (없으면 cfg.strategy.strategy_id stamp)
를 자동 할당한다. 이로써 downstream consumer 가 dedup 및 strategy_id 기반
일관 추적을 할 수 있다.

기존 호출자가 signal_id / strategy_id 를 명시한 경우에는 보존한다.
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from typing import List
from unittest.mock import AsyncMock, MagicMock

from common.types import ErrorCode, ResCommonResponse, TradeSignal
from interfaces.live_strategy import LiveStrategy
from scheduler.strategy_scheduler import StrategyScheduler, StrategySchedulerConfig
from scheduler.strategy_scheduler_store import StrategySchedulerStore


def _signal(action: str, *, code: str = "005930",
            strategy_id: str | None = None,
            signal_id: str | None = None) -> TradeSignal:
    return TradeSignal(
        code=code,
        name=code,
        action=action,
        price=10000,
        qty=1,
        reason="t",
        strategy_name="STAMP_TEST_DISPLAY",
        strategy_id=strategy_id,
        signal_id=signal_id,
    )


class _StubStrategy(LiveStrategy):
    def __init__(
        self,
        strategy_id_value: str = "stamp_test",
        display_name: str = "STAMP_TEST_DISPLAY",
        scan_signals: List[TradeSignal] | None = None,
        exit_signals: List[TradeSignal] | None = None,
    ):
        self._display = display_name
        self._sid = strategy_id_value
        self._scan_signals = scan_signals or []
        self._exit_signals = exit_signals or []

    @property
    def name(self) -> str:
        return self._display

    @property
    def strategy_id(self) -> str:
        return self._sid

    async def scan(self):
        return list(self._scan_signals)

    async def check_exits(self, holdings):
        return list(self._exit_signals)


class TestSchedulerSignalStamping(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self._scheduler = None

    def tearDown(self):
        if self._scheduler is not None:
            self._scheduler.close()
        shutil.rmtree(self.test_dir)

    def _make_scheduler(self):
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
        oes.poll_active_orders_once = AsyncMock(return_value=0)
        oes.check_stuck_orders_once = AsyncMock(return_value=0)
        oes.get_active_order_poll_interval_sec = MagicMock(
            return_value=StrategyScheduler.ORDER_POLL_INTERVAL_SEC
        )

        sqs = MagicMock()
        sqs.get_current_price = AsyncMock(
            return_value=ResCommonResponse(
                rt_cd=ErrorCode.SUCCESS.value, msg1="OK",
                data={"output": {"stck_prpr": "10000"}}
            )
        )
        scm = MagicMock()
        scm.get_stock_code = AsyncMock(return_value="005930")
        scm.get_name_by_code = MagicMock(return_value="삼성전자")

        tm = MagicMock()
        tm.is_market_operating_hours.return_value = True
        tm.get_current_kst_time.return_value.strftime.return_value = "2026-05-22 10:00:00"
        mcs = AsyncMock()
        mcs.is_market_open_now.return_value = True
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
        self._scheduler = scheduler
        return scheduler

    @staticmethod
    def _cfg_for(strategy: LiveStrategy) -> StrategySchedulerConfig:
        return StrategySchedulerConfig(
            strategy=strategy,
            max_positions=10,
            interval_minutes=5,
        )

    async def test_scan_signals_get_strategy_id_stamped(self):
        scheduler = self._make_scheduler()
        scan_sigs = [_signal("BUY", code=f"00593{i}") for i in range(2)]
        strategy = _StubStrategy(
            strategy_id_value="stamp_test",
            display_name="스탬프테스트",
            scan_signals=scan_sigs,
        )
        cfg = self._cfg_for(strategy)
        await scheduler._run_strategy(cfg)
        for s in scan_sigs:
            self.assertEqual(s.strategy_id, "stamp_test")

    async def test_scan_signals_get_signal_id_stamped(self):
        scheduler = self._make_scheduler()
        scan_sigs = [_signal("BUY", code=f"00593{i}") for i in range(2)]
        strategy = _StubStrategy(scan_signals=scan_sigs)
        cfg = self._cfg_for(strategy)
        await scheduler._run_strategy(cfg)
        # signal_id 가 채워졌어야 함 (uuid 형식이라 단순 None 만 확인)
        for s in scan_sigs:
            self.assertIsNotNone(s.signal_id)
            self.assertGreater(len(s.signal_id), 0)
        # 그리고 서로 달라야 함 (uuid 라면)
        ids = {s.signal_id for s in scan_sigs}
        self.assertEqual(len(ids), len(scan_sigs))

    async def test_scan_preserves_pre_existing_signal_id_and_strategy_id(self):
        scheduler = self._make_scheduler()
        sig = _signal("BUY", strategy_id="caller_override", signal_id="custom-sig-001")
        strategy = _StubStrategy(scan_signals=[sig])
        cfg = self._cfg_for(strategy)
        await scheduler._run_strategy(cfg)
        self.assertEqual(sig.strategy_id, "caller_override")
        self.assertEqual(sig.signal_id, "custom-sig-001")

    async def test_exit_signals_get_strategy_id_stamped(self):
        scheduler = self._make_scheduler()
        # _get_strategy_holdings 가 빈 list 가 아니면 check_exits 가 호출된다.
        scheduler._virtual_trade_service.get_holds_by_strategy.return_value = [
            {"code": "005930", "buy_price": 70000, "qty": 1}
        ]
        exit_sigs = [_signal("SELL", code="005930")]
        strategy = _StubStrategy(
            strategy_id_value="exit_stamp_test",
            exit_signals=exit_sigs,
            scan_signals=[],
        )
        cfg = self._cfg_for(strategy)
        await scheduler._run_strategy(cfg)
        self.assertEqual(exit_sigs[0].strategy_id, "exit_stamp_test")
        self.assertIsNotNone(exit_sigs[0].signal_id)

    async def test_stamping_fallback_to_strategy_name_when_no_strategy_id(self):
        """strategy_id property 가 없거나 빈 경우 strategy.name 을 strategy_id 로 정규화하여 fallback."""
        class _StubNoId(LiveStrategy):
            @property
            def name(self):
                return "거래량돌파"  # display 이름

            # strategy_id property 없음

            async def scan(self):
                return [_signal("BUY")]

            async def check_exits(self, holdings):
                return []

        scheduler = self._make_scheduler()
        strat = _StubNoId()
        cfg = self._cfg_for(strat)
        sig = (await strat.scan())[0]
        # _run_strategy 실행 시 새 scan 결과의 signal 에 strategy_id 가 stamping 되도록 한다.
        # 직접 stamping 검증을 위해, 같은 signal 객체를 strategy 가 다시 반환하도록 stub 한다.
        captured = []
        class _StubReuse(LiveStrategy):
            @property
            def name(self): return "거래량돌파"

            async def scan(self):
                s = _signal("BUY")
                captured.append(s)
                return [s]

            async def check_exits(self, holdings):
                return []

        cfg2 = self._cfg_for(_StubReuse())
        await scheduler._run_strategy(cfg2)
        # resolver 가 "거래량돌파" → "volume_breakout_live" 로 변환
        self.assertEqual(captured[0].strategy_id, "volume_breakout_live")
