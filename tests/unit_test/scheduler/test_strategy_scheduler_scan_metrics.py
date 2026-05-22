"""StrategyScheduler scan_metrics 발행 단위 테스트 (P2 2-2 1차).

scan() 직후 4종 메트릭(latency_ms / candidate_count / signal_count / rejected_reasons)을
포함한 `scan_metrics` log event 가 발행되는지 검증한다.
"""
from __future__ import annotations

import logging
import shutil
import tempfile
import unittest
from typing import List
from unittest.mock import AsyncMock, MagicMock

from common.types import ErrorCode, ResCommonResponse, TradeSignal
from interfaces.live_strategy import LiveStrategy
from scheduler.strategy_scheduler import StrategyScheduler, StrategySchedulerConfig
from scheduler.strategy_scheduler_store import StrategySchedulerStore


class _StubStrategy(LiveStrategy):
    """scan() 시 지정된 reject 이벤트를 _logger 로 emit 후 신호 리스트 반환."""

    def __init__(
        self,
        name: str = "테스트전략",
        rejections=None,  # List[str] of reason values
        signals: List[TradeSignal] = None,
        candidates: List[str] = None,
        raise_in_scan: bool = False,
    ):
        self._name = name
        self._rejections = rejections or []
        self._signals = signals or []
        self._candidates = candidates or []
        self._raise_in_scan = raise_in_scan
        self._logger = logging.getLogger(f"strategy.scan_metrics_test.{name}")
        # 프로덕션 get_strategy_logger 는 LOG_LEVEL 을 명시 적용한다.
        # 테스트에서는 root logger(WARNING) 상속을 피하기 위해 DEBUG 로 고정한다.
        self._logger.setLevel(logging.DEBUG)

    @property
    def name(self) -> str:
        return self._name

    async def scan(self):
        for reason in self._rejections:
            self._logger.info({"event": "entry_rejected", "code": "X", "reason": reason})
        if self._raise_in_scan:
            raise RuntimeError("scan boom")
        return list(self._signals)

    async def check_exits(self, holdings):
        return []

    def current_candidate_codes(self):
        return list(self._candidates)


class TestStrategySchedulerScanMetrics(unittest.IsolatedAsyncioTestCase):
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
        oes.poll_active_orders_once = AsyncMock(return_value=0)
        oes.check_stuck_orders_once = AsyncMock(return_value=0)
        oes.get_active_order_poll_interval_sec = MagicMock(
            return_value=StrategyScheduler.ORDER_POLL_INTERVAL_SEC
        )

        sqs = MagicMock()
        scm = MagicMock()
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
        return scheduler, mock_logger

    @staticmethod
    def _scan_metrics_records(mock_logger):
        events = []
        for call in mock_logger.info.call_args_list:
            if not call.args:
                continue
            arg = call.args[0]
            if isinstance(arg, dict) and arg.get("event") == "scan_metrics":
                events.append(arg)
        return events

    async def test_scan_metrics_logged_after_scan(self):
        scheduler, mock_logger = self._make_scheduler()
        sig = TradeSignal(
            code="005930", name="삼성전자", action="BUY",
            price=70000, qty=1, reason="테스트", strategy_name="VBO계측",
        )
        strategy = _StubStrategy(
            name="VBO계측",
            rejections=["reason_a", "reason_a", "reason_b"],
            signals=[sig],
            candidates=["005930", "000660", "035720"],
        )
        config = StrategySchedulerConfig(strategy=strategy, max_positions=10)
        scheduler.register(config)

        await scheduler._run_strategy(config)

        records = self._scan_metrics_records(mock_logger)
        self.assertEqual(len(records), 1)
        rec = records[0]
        self.assertEqual(rec["strategy_name"], "VBO계측")
        self.assertEqual(rec["candidate_count"], 3)
        self.assertEqual(rec["signal_count"], 1)
        self.assertEqual(rec["rejected_reasons"], {"reason_a": 2, "reason_b": 1})
        self.assertIsInstance(rec["latency_ms"], float)
        self.assertGreaterEqual(rec["latency_ms"], 0.0)

    async def test_scan_metrics_handler_removed_after_exception(self):
        scheduler, mock_logger = self._make_scheduler()
        strategy = _StubStrategy(
            name="예외전략",
            rejections=["pre_reject"],
            raise_in_scan=True,
        )
        config = StrategySchedulerConfig(strategy=strategy, max_positions=10)
        scheduler.register(config)

        with self.assertRaises(RuntimeError):
            await scheduler._run_strategy(config)

        # 핸들러가 detach 됐는지: 신규 entry_rejected 이벤트가 더 카운트되지 않아야 한다.
        # strategy logger 의 핸들러 목록에서 EntryRejectionCounter 부재 확인.
        from core.scan_rejection_counter import EntryRejectionCounter
        leftover = [h for h in strategy._logger.handlers if isinstance(h, EntryRejectionCounter)]
        self.assertEqual(leftover, [])

    async def test_scan_metrics_zero_candidate_when_helper_returns_empty(self):
        scheduler, mock_logger = self._make_scheduler()
        strategy = _StubStrategy(name="후보없음", candidates=[])
        config = StrategySchedulerConfig(strategy=strategy, max_positions=10)
        scheduler.register(config)

        await scheduler._run_strategy(config)

        records = self._scan_metrics_records(mock_logger)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["candidate_count"], 0)
        self.assertEqual(records[0]["signal_count"], 0)
        self.assertEqual(records[0]["rejected_reasons"], {})
        # sqs MagicMock 이므로 price_lookup_stats_snapshot 미연동 → 빈 dict
        self.assertEqual(records[0]["lookup_stats_delta"], {})

    async def test_scan_metrics_includes_lookup_stats_delta(self):
        scheduler, mock_logger = self._make_scheduler()
        # sqs.price_lookup_stats_snapshot 이 scan 전/후 다른 값 반환하도록 stub
        before = {"snapshot_hit": 5, "rest_fallback": 2, "no_tick_fallback": 0}
        after = {"snapshot_hit": 8, "rest_fallback": 2, "no_tick_fallback": 1}
        scheduler._sqs.price_lookup_stats_snapshot = MagicMock(side_effect=[before, after])

        strategy = _StubStrategy(name="delta전략", candidates=["005930"])
        config = StrategySchedulerConfig(strategy=strategy, max_positions=10)
        scheduler.register(config)

        await scheduler._run_strategy(config)

        records = self._scan_metrics_records(mock_logger)
        self.assertEqual(len(records), 1)
        delta = records[0]["lookup_stats_delta"]
        # 변동이 있는 키만 포함 (0인 키 제거)
        self.assertEqual(delta, {"snapshot_hit": 3, "no_tick_fallback": 1})
        self.assertNotIn("rest_fallback", delta)

    async def test_scan_metrics_lookup_stats_delta_empty_when_method_absent(self):
        scheduler, mock_logger = self._make_scheduler()
        # method 자체가 없는 경우: sqs를 spec 객체로 교체
        plain_sqs = object()
        scheduler._sqs = plain_sqs

        strategy = _StubStrategy(name="no_sqs_method", candidates=[])
        config = StrategySchedulerConfig(strategy=strategy, max_positions=10)
        scheduler.register(config)

        await scheduler._run_strategy(config)

        records = self._scan_metrics_records(mock_logger)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["lookup_stats_delta"], {})


if __name__ == "__main__":
    unittest.main()
