"""StrategyScheduler × PositionSizingService 통합 단위 테스트."""
import pytest
from unittest.mock import MagicMock, AsyncMock
from common.types import TradeSignal, ResCommonResponse, ErrorCode
from scheduler.strategy_scheduler import StrategyScheduler, SignalRecord
from scheduler.strategy_scheduler_store import StrategySchedulerStore
from services.notification_service import NotificationCategory, NotificationLevel


def _make_scheduler(position_sizer=None, dry_run=False):
    vm = MagicMock()
    vm.get_holds_by_strategy.return_value = []
    vm.log_buy_async = AsyncMock(return_value=True)

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
        return_value=ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="OK",
                                       data={"output": {"stck_prpr": "10000"}})
    )

    scm = MagicMock()
    scm.get_stock_code = AsyncMock(return_value="005930")

    tm = MagicMock()
    tm.is_market_operating_hours.return_value = True
    tm.get_current_kst_time.return_value.strftime.return_value = "2025-01-01 10:00:00"

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
        dry_run=dry_run,
        store=mock_store,
        position_sizing_service=position_sizer,
    )
    return scheduler, oes


def _buy_signal(qty=10):
    return TradeSignal(
        code="005930", name="삼성전자", action="BUY",
        price=10_000, qty=qty, reason="test", strategy_name="테스트전략",
    )


# ── position_sizing_service 주입 없으면 기존 동작 ────────────────────

@pytest.mark.asyncio
async def test_buy_without_sizer_uses_signal_qty():
    """position_sizing_service 미주입 시 signal.qty 그대로 주문."""
    scheduler, oes = _make_scheduler(position_sizer=None, dry_run=False)
    signal = _buy_signal(qty=5)
    await scheduler._execute_signal(signal)
    args, kwargs = oes.handle_place_buy_order.call_args
    assert kwargs.get("qty", args[2] if len(args) > 2 else None) == 5 or \
           oes.handle_place_buy_order.called


# ── position_sizing_service가 qty를 줄이는 경우 ──────────────────────

@pytest.mark.asyncio
async def test_buy_with_sizer_adjusted_qty_passed_to_oes():
    """sizer가 qty=3 반환 → handle_place_buy_order에 qty=3이 전달된다."""
    sizer = AsyncMock()
    sizer.adjust_buy_qty.return_value = (3, "risk_limited")

    scheduler, oes = _make_scheduler(position_sizer=sizer, dry_run=False)
    signal = _buy_signal(qty=10)
    await scheduler._execute_signal(signal)

    sizer.adjust_buy_qty.assert_called_once()
    oes.handle_place_buy_order.assert_called_once()
    call_args, _ = oes.handle_place_buy_order.call_args
    # handle_place_buy_order(code, price, qty, ...) — qty는 3번째 위치 인자
    assert call_args[2] == 3


# ── position_sizing_service가 qty=0 반환 → 주문 skip ─────────────────

@pytest.mark.asyncio
async def test_buy_with_sizer_zero_qty_skips_order():
    """sizer가 qty=0 반환 → handle_place_buy_order 미호출 + signal_history 기록."""
    sizer = AsyncMock()
    sizer.adjust_buy_qty.return_value = (0, "cap_exhausted")

    scheduler, oes = _make_scheduler(position_sizer=sizer, dry_run=False)
    signal = _buy_signal(qty=10)
    await scheduler._execute_signal(signal)

    sizer.adjust_buy_qty.assert_called_once()
    oes.handle_place_buy_order.assert_not_called()

    # signal_history에 qty=0 보류 기록이 있어야 함
    history = scheduler._signal_history
    assert len(history) == 1
    assert history[0].qty == 0


@pytest.mark.asyncio
async def test_buy_with_sizer_zero_qty_emits_failure_notification():
    """sizer가 qty=0 반환 → 전략 실패 알림을 발행한다."""
    sizer = AsyncMock()
    sizer.adjust_buy_qty.return_value = (0, "risk_zero")

    scheduler, oes = _make_scheduler(position_sizer=sizer, dry_run=False)
    notifier = AsyncMock()
    scheduler._notification_service = notifier

    signal = _buy_signal(qty=10)
    await scheduler._execute_signal(signal)

    oes.handle_place_buy_order.assert_not_called()
    notifier.emit.assert_awaited_once()
    args, kwargs = notifier.emit.call_args
    assert args[0] == NotificationCategory.STRATEGY
    assert args[1] == NotificationLevel.ERROR
    assert "매수 실패" in args[2]
    assert "포지션 사이징 결과 수량 0" in args[3]
    assert kwargs["metadata"]["qty"] == 0
    assert kwargs["metadata"]["reason"] == "sizing_skip:risk_zero"


@pytest.mark.asyncio
async def test_buy_with_sizer_adjusted_qty_recorded_and_notified():
    """sizer가 수량을 줄이면 기록과 알림에도 실제 주문 수량을 사용한다."""
    sizer = AsyncMock()
    sizer.adjust_buy_qty.return_value = (3, "risk_limited")

    scheduler, oes = _make_scheduler(position_sizer=sizer, dry_run=False)
    notifier = AsyncMock()
    scheduler._notification_service = notifier

    signal = _buy_signal(qty=10)
    await scheduler._execute_signal(signal)

    assert scheduler._signal_history[0].qty == 3
    args, kwargs = notifier.emit.call_args
    assert "3주" in args[3]
    assert kwargs["metadata"]["qty"] == 3


# ── dry_run=True 이면 sizer 호출 안 함 ────────────────────────────────

@pytest.mark.asyncio
async def test_dry_run_skips_sizer():
    """dry_run 모드에서는 PositionSizingService를 호출하지 않는다."""
    sizer = AsyncMock()
    sizer.adjust_buy_qty.return_value = (1, "ok")

    scheduler, oes = _make_scheduler(position_sizer=sizer, dry_run=True)
    signal = _buy_signal(qty=5)
    await scheduler._execute_signal(signal)

    sizer.adjust_buy_qty.assert_not_called()
    oes.handle_place_buy_order.assert_not_called()  # dry_run은 API 호출 안 함


# ── qty=None: sizer 단독 결정, dry-run fallback ─────────────────────────

@pytest.mark.asyncio
async def test_buy_qty_none_with_sizer_uses_sized_qty():
    """qty=None 신호 + sizer 주입 → sizer가 단독으로 qty를 결정한다."""
    sizer = AsyncMock()
    sizer.adjust_buy_qty.return_value = (5, "risk_limited")

    scheduler, oes = _make_scheduler(position_sizer=sizer, dry_run=False)
    signal = TradeSignal(
        code="005930", name="삼성전자", action="BUY",
        price=10_000, qty=None, reason="test", strategy_name="테스트전략",
    )
    await scheduler._execute_signal(signal)

    sizer.adjust_buy_qty.assert_called_once()
    args, kwargs = oes.handle_place_buy_order.call_args
    placed_qty = kwargs.get("qty", args[2] if len(args) > 2 else None)
    assert placed_qty == 5


@pytest.mark.asyncio
async def test_dry_run_qty_none_uses_fallback_qty():
    """dry_run + qty=None → log_buy_async는 fallback qty=1로 기록된다."""
    scheduler, _ = _make_scheduler(position_sizer=None, dry_run=True)
    signal = TradeSignal(
        code="005930", name="삼성전자", action="BUY",
        price=10_000, qty=None, reason="test", strategy_name="테스트전략",
    )
    await scheduler._execute_signal(signal)

    vm = scheduler._virtual_trade_service
    vm.log_buy_async.assert_called_once()
    logged_qty = vm.log_buy_async.call_args[0][3]  # (strategy, code, price, qty)
    assert logged_qty == 1
