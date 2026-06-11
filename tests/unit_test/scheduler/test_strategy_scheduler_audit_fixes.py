# tests/unit_test/scheduler/test_strategy_scheduler_audit_fixes.py
"""StrategyScheduler 코드 리뷰(S-1~S-7, todo_list.md) 수정 잠금 테스트."""
import asyncio
from datetime import datetime
from unittest.mock import MagicMock, AsyncMock

from common.types import TradeSignal, ErrorCode, ResCommonResponse
from scheduler.strategy_scheduler import (
    StrategyScheduler,
    StrategySchedulerConfig,
    SignalRecord,
)
from scheduler.strategy_scheduler_store import StrategySchedulerStore
from interfaces.live_strategy import LiveStrategy

_SENTINEL = object()


class MockStrategy(LiveStrategy):
    """scan/check_exits 반환값을 그대로 돌려주는 테스트 전략 (None 반환 가능)."""

    def __init__(self, name="감사테스트전략", scan_return=_SENTINEL, exit_return=_SENTINEL):
        self._name = name
        self._scan_return = [] if scan_return is _SENTINEL else scan_return
        self._exit_return = [] if exit_return is _SENTINEL else exit_return
        self.scan_called = False

    @property
    def name(self) -> str:
        return self._name

    async def scan(self):
        self.scan_called = True
        return self._scan_return

    async def check_exits(self, holdings):
        return self._exit_return


def _make_scheduler(dry_run=True, event_router=None):
    vm = MagicMock()
    vm.get_holds_by_strategy.return_value = []
    vm.log_buy_async = AsyncMock(return_value=True)
    vm.log_sell_by_strategy_async = AsyncMock(return_value=1.0)

    oes = MagicMock()
    oes.handle_place_buy_order = AsyncMock(
        return_value=ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="OK")
    )
    oes.handle_place_sell_order = AsyncMock(
        return_value=ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="OK")
    )
    oes.poll_active_orders_once = AsyncMock(return_value=0)
    oes.check_stuck_orders_once = AsyncMock(return_value=0)

    sqs = MagicMock()
    sqs.get_current_price = AsyncMock(
        return_value=ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value, msg1="OK",
            data={"output": {"stck_prpr": "60000"}},
        )
    )

    repo = MagicMock()
    repo.get_name_by_code.return_value = "테스트종목"

    tm = MagicMock()
    tm.get_current_kst_time.return_value = datetime(2026, 6, 12, 10, 0, 0)

    mcs = AsyncMock()
    mcs.is_market_open_now.return_value = True

    mock_store = MagicMock(spec=StrategySchedulerStore)
    mock_store.load_signal_history.return_value = []

    logger = MagicMock()

    scheduler = StrategyScheduler(
        virtual_trade_service=vm,
        order_execution_service=oes,
        stock_query_service=sqs,
        stock_code_repository=repo,
        market_clock=tm,
        market_calendar_service=mcs,
        logger=logger,
        dry_run=dry_run,
        store=mock_store,
        event_router=event_router,
    )
    return scheduler, vm, oes, tm, logger


def _buy_signal(strategy_name, code, price=10000):
    return TradeSignal(
        strategy_name=strategy_name, code=code, name=f"종목{code}",
        action="BUY", price=price, qty=1, reason="테스트 매수",
    )


def _sell_signal(strategy_name, code, price=10000):
    return TradeSignal(
        strategy_name=strategy_name, code=code, name=f"종목{code}",
        action="SELL", price=price, qty=1, reason="테스트 매도",
    )


# ── S-1. stop()의 강제청산 데드 패스 ──

async def test_stop_performs_force_exit_for_enabled_force_exit_strategy():
    """stop(save_state=False) 시 enabled+force_exit_on_close 전략은 실제 청산 경로를 탄다."""
    scheduler, _, _, _, _ = _make_scheduler()
    strategy = MockStrategy(name="당일청산전략")
    cfg = StrategySchedulerConfig(strategy=strategy, force_exit_on_close=True)
    scheduler.register(cfg)
    assert cfg.enabled is True

    scheduler._force_liquidate_strategy = AsyncMock()
    try:
        await scheduler.stop(save_state=False)
        scheduler._force_liquidate_strategy.assert_awaited_once_with(cfg)
        assert cfg.enabled is False
    finally:
        scheduler.close()


async def test_stop_with_save_state_skips_force_exit():
    """stop(save_state=True)(재시작 경로)는 청산하지 않는다."""
    scheduler, _, _, _, _ = _make_scheduler()
    strategy = MockStrategy(name="당일청산전략")
    cfg = StrategySchedulerConfig(strategy=strategy, force_exit_on_close=True)
    scheduler.register(cfg)

    scheduler._force_liquidate_strategy = AsyncMock()
    try:
        await scheduler.stop(save_state=True)
        scheduler._force_liquidate_strategy.assert_not_awaited()
    finally:
        scheduler.close()


# ── S-2. scan() None 반환 가드 ──

async def test_run_strategy_handles_none_scan_result():
    """전략 scan()이 None을 반환해도 예외 없이 signal_count=0으로 기록한다."""
    scheduler, _, _, _, logger = _make_scheduler()
    strategy = MockStrategy(scan_return=None)
    cfg = StrategySchedulerConfig(strategy=strategy)
    scheduler.register(cfg)

    await scheduler._run_strategy(cfg)

    scan_metrics = [
        c.args[0] for c in logger.info.call_args_list
        if c.args and isinstance(c.args[0], dict) and c.args[0].get("event") == "scan_metrics"
    ]
    assert scan_metrics, "scan_metrics 로그가 발행되어야 한다"
    assert scan_metrics[0]["signal_count"] == 0


# ── S-4. 매도/매수 병렬 신호 실행 에러 처리 ──

async def test_sell_signal_exception_does_not_abort_run_strategy():
    """매도 신호 1건 실행 예외 시 나머지 신호 실행 + scan 단계까지 진행하고 ERROR 로그를 남긴다."""
    name = "감사테스트전략"
    sig_fail = _sell_signal(name, "000001")
    sig_ok = _sell_signal(name, "000002")
    scheduler, vm, _, _, logger = _make_scheduler()
    strategy = MockStrategy(name=name, exit_return=[sig_fail, sig_ok])
    cfg = StrategySchedulerConfig(strategy=strategy)
    scheduler.register(cfg)
    vm.get_holds_by_strategy.return_value = [
        {"code": "000001", "name": "종목1", "buy_price": 9000, "qty": 1, "status": "HOLD"},
        {"code": "000002", "name": "종목2", "buy_price": 9000, "qty": 1, "status": "HOLD"},
    ]

    executed = []

    async def _fake_execute(signal):
        executed.append(signal.code)
        if signal.code == "000001":
            raise RuntimeError("주문 처리 실패")

    scheduler._execute_signal = _fake_execute

    await scheduler._run_strategy(cfg)

    assert sorted(executed) == ["000001", "000002"]
    assert strategy.scan_called, "매도 예외 이후에도 scan 단계까지 진행해야 한다"
    error_logs = [str(c) for c in logger.error.call_args_list]
    assert any("000001" in msg for msg in error_logs), "매도 실행 예외가 ERROR 로그로 남아야 한다"


async def test_buy_signal_exception_is_logged():
    """매수 신호 실행 예외가 조용히 삼켜지지 않고 ERROR 로그로 남는다."""
    name = "감사테스트전략"
    sig_fail = _buy_signal(name, "000001")
    sig_ok = _buy_signal(name, "000002")
    scheduler, _, _, _, logger = _make_scheduler()
    strategy = MockStrategy(name=name, scan_return=[sig_fail, sig_ok])
    cfg = StrategySchedulerConfig(strategy=strategy)
    scheduler.register(cfg)

    executed = []

    async def _fake_execute(signal):
        executed.append(signal.code)
        if signal.code == "000001":
            raise RuntimeError("주문 처리 실패")

    scheduler._execute_signal = _fake_execute

    await scheduler._run_strategy(cfg)

    assert sorted(executed) == ["000001", "000002"]
    error_logs = [str(c) for c in logger.error.call_args_list]
    assert any("000001" in msg for msg in error_logs), "매수 실행 예외가 ERROR 로그로 남아야 한다"


# ── S-5. MAX_HISTORY 트림의 당일 레코드 보존 ──

async def test_history_trim_preserves_today_records():
    """당일 신호가 MAX_HISTORY를 초과해도 당일 레코드는 트림되지 않는다 (force-exit 복구 근거 보존)."""
    scheduler, _, _, _, _ = _make_scheduler(dry_run=True)
    today_records = [
        SignalRecord(
            strategy_name="감사테스트전략", code=f"{i:06d}", name=f"종목{i}",
            action="BUY", price=10000, qty=1,
            timestamp=f"2026-06-12 09:{i % 60:02d}:00", api_success=True,
        )
        for i in range(250)
    ]
    scheduler._signal_history = list(today_records)

    await scheduler._execute_signal(_buy_signal("감사테스트전략", "999999"))

    assert len(scheduler._signal_history) == 251, "당일 레코드는 MAX_HISTORY 초과에도 보존되어야 한다"
    codes = {r.code for r in scheduler._signal_history}
    assert "000000" in codes and "999999" in codes


async def test_history_trim_drops_past_date_records_first():
    """전일 레코드는 기존 MAX_HISTORY 정책대로 트림된다."""
    scheduler, _, _, _, _ = _make_scheduler(dry_run=True)
    yesterday_records = [
        SignalRecord(
            strategy_name="감사테스트전략", code=f"{i:06d}", name=f"종목{i}",
            action="BUY", price=10000, qty=1,
            timestamp="2026-06-11 09:00:00", api_success=True,
        )
        for i in range(250)
    ]
    scheduler._signal_history = list(yesterday_records)

    await scheduler._execute_signal(_buy_signal("감사테스트전략", "999999"))

    assert len(scheduler._signal_history) == scheduler.MAX_HISTORY
    assert scheduler._signal_history[-1].code == "999999"


# ── S-6. 날짜 키 set purge ──

def test_date_rollover_purges_stale_date_keys():
    """날짜 전환 시 전일 실패 알림 키와 대사 완료 날짜가 purge된다."""
    scheduler, _, _, _, _ = _make_scheduler()
    old_key = ("20260611", "전략", "000001", "BUY", "사유", "실패")
    today_key = ("20260612", "전략", "000002", "BUY", "사유", "실패")
    scheduler._strategy_failure_alert_keys = {old_key, today_key}
    scheduler._reconciled_dates = {"2026-06-11", "2026-06-12"}
    scheduler._force_exit_done_date = "2026-06-11"
    scheduler._force_exit_done = {"전략"}

    scheduler._sync_force_exit_done_date(datetime(2026, 6, 12, 9, 0, 0))

    assert scheduler._strategy_failure_alert_keys == {today_key}
    assert scheduler._reconciled_dates == {"2026-06-12"}
    assert scheduler._force_exit_done == set()


# ── S-7. stop_strategy의 event shadow router 구독 해제 ──

async def test_stop_strategy_unsubscribes_event_shadow_router():
    """event_driven_shadow 전략 비활성화 시 entry/exit router 구독을 해제하고 추적 dict를 정리한다."""
    router = MagicMock()
    scheduler, _, _, _, _ = _make_scheduler(event_router=router)
    name = "감사테스트전략"
    strategy = MockStrategy(name=name)
    cfg = StrategySchedulerConfig(strategy=strategy, event_driven_shadow=True)
    scheduler.register(cfg)
    scheduler._event_shadow_subscriptions[name] = {"000001"}
    scheduler._exit_shadow_subscriptions[name] = {"000002"}

    result = await scheduler.stop_strategy(name)

    assert result is True
    router.unsubscribe.assert_any_call("000001", name)
    router.unsubscribe.assert_any_call("000002", f"{name}__exit")
    assert name not in scheduler._event_shadow_subscriptions
    assert name not in scheduler._exit_shadow_subscriptions
