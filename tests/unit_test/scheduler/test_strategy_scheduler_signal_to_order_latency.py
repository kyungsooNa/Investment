"""StrategyScheduler signal-to-order latency 로그 단위 테스트 (P2 2-2 후속)."""
from __future__ import annotations

import time
import shutil
import tempfile
import unittest
from typing import List
from unittest.mock import AsyncMock, MagicMock

from common.types import ErrorCode, ResCommonResponse, TradeSignal
from interfaces.live_strategy import LiveStrategy
from scheduler.strategy_scheduler import StrategyScheduler, StrategySchedulerConfig
from scheduler.strategy_scheduler_store import StrategySchedulerStore


def _signal(action: str, code: str = "005930", *, created_at: float | None = None) -> TradeSignal:
    return TradeSignal(
        code=code,
        name=code,
        action=action,
        price=10000,
        qty=1,
        reason="t",
        strategy_name="LATENCY_TEST",
        created_at=created_at,
    )


class _StubStrategy(LiveStrategy):
    def __init__(
        self,
        name: str = "LATENCY_TEST",
        scan_signals: List[TradeSignal] = None,
        exit_signals: List[TradeSignal] = None,
    ):
        self._name = name
        self._scan_signals = scan_signals or []
        self._exit_signals = exit_signals or []

    @property
    def name(self) -> str:
        return self._name

    async def scan(self):
        return list(self._scan_signals)

    async def check_exits(self, holdings):
        return list(self._exit_signals)


class TestSignalToOrderLatency(unittest.IsolatedAsyncioTestCase):
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

        mock_logger = MagicMock()

        scheduler = StrategyScheduler(
            virtual_trade_service=vm,
            order_execution_service=oes,
            stock_query_service=sqs,
            stock_code_repository=scm,
            market_clock=tm,
            market_calendar_service=mcs,
            logger=mock_logger,
            dry_run=True,
            store=mock_store,
        )
        self._scheduler = scheduler
        return scheduler, mock_logger, vm

    @staticmethod
    def _latency_records(mock_logger):
        records = []
        for call in mock_logger.info.call_args_list:
            if not call.args:
                continue
            arg = call.args[0]
            if isinstance(arg, dict) and arg.get("event") == "signal_to_order_latency":
                records.append(arg)
        return records

    async def test_log_signal_to_order_latency_emits_event(self):
        scheduler, mock_logger, _ = self._make_scheduler()
        signal = _signal("BUY", created_at=time.time() - 0.01)

        scheduler._log_signal_to_order_latency(signal, tid="TID_X")

        records = self._latency_records(mock_logger)
        self.assertEqual(len(records), 1)
        rec = records[0]
        self.assertEqual(rec["strategy_name"], "LATENCY_TEST")
        self.assertEqual(rec["code"], "005930")
        self.assertEqual(rec["action"], "BUY")
        self.assertEqual(rec["trace_id"], "TID_X")
        self.assertGreaterEqual(rec["latency_ms"], 0.0)

    async def test_log_signal_to_order_latency_skips_when_created_at_missing(self):
        scheduler, mock_logger, _ = self._make_scheduler()
        signal = _signal("BUY", created_at=None)

        scheduler._log_signal_to_order_latency(signal, tid="TID_Y")

        self.assertEqual(self._latency_records(mock_logger), [])

    async def test_scan_signals_get_created_at_stamped(self):
        """scan() 결과 신호가 dispatch 전에 created_at stamp 되어야 한다."""
        scheduler, mock_logger, _ = self._make_scheduler()
        unstamped = _signal("BUY", created_at=None)
        strategy = _StubStrategy(scan_signals=[unstamped])
        config = StrategySchedulerConfig(strategy=strategy, max_positions=10)
        scheduler.register(config)

        await scheduler._run_strategy(config)

        # 같은 signal 인스턴스를 보유하므로 stamp 확인 가능
        self.assertIsNotNone(unstamped.created_at)
        # _log_signal_to_order_latency 가 호출되어 log event 발행
        records = self._latency_records(mock_logger)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["action"], "BUY")

    async def test_check_exits_signals_get_created_at_stamped(self):
        """check_exits 결과 sell 신호도 stamp 되고 latency log 발행 (dry_run 모드)."""
        scheduler, mock_logger, vm = self._make_scheduler()
        vm.get_holds_by_strategy.return_value = [{"code": "005930", "buy_price": 9000}]
        sell_sig = _signal("SELL", created_at=None)
        strategy = _StubStrategy(exit_signals=[sell_sig])
        config = StrategySchedulerConfig(strategy=strategy, max_positions=10)
        scheduler.register(config)

        await scheduler._run_strategy(config)

        self.assertIsNotNone(sell_sig.created_at)
        records = self._latency_records(mock_logger)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["action"], "SELL")

    async def test_existing_created_at_is_preserved(self):
        """이미 created_at 이 있는 신호는 scheduler stamp 가 덮어쓰지 않는다."""
        scheduler, mock_logger, _ = self._make_scheduler()
        old_ts = time.time() - 5.0
        stamped = _signal("BUY", created_at=old_ts)
        strategy = _StubStrategy(scan_signals=[stamped])
        config = StrategySchedulerConfig(strategy=strategy, max_positions=10)
        scheduler.register(config)

        await scheduler._run_strategy(config)

        self.assertEqual(stamped.created_at, old_ts)
        records = self._latency_records(mock_logger)
        self.assertEqual(len(records), 1)
        # latency 가 5초 이상이어야 한다 (5000ms+).
        self.assertGreater(records[0]["latency_ms"], 4900.0)


if __name__ == "__main__":
    unittest.main()
