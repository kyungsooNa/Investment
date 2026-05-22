import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from common.types import (
    Exchange,
    OrderContext,
    OrderExecutionReport,
    OrderSide,
    OrderState,
)
from config.config_loader import ExecutionQualityReportConfig
from services.execution_quality_reporter import ExecutionQualityReporter
from services.notification_service import NotificationCategory, NotificationLevel
from services.order_policy_service import OrderPolicyDecision


class _Logger:
    def __init__(self):
        self.info = MagicMock()
        self.warning = MagicMock()
        self.debug = MagicMock()
        self.error = MagicMock()


def _make_decision(context: dict | None = None) -> OrderPolicyDecision:
    return OrderPolicyDecision(allowed=True, rule="test", reason="", context=context or {})


def _make_reporter(
    *,
    config: ExecutionQualityReportConfig | None = None,
    notification: AsyncMock | None = None,
    now: datetime | None = None,
) -> tuple[ExecutionQualityReporter, _Logger, AsyncMock]:
    logger = _Logger()
    notif = notification if notification is not None else AsyncMock()
    cfg = config or ExecutionQualityReportConfig()
    reporter = ExecutionQualityReporter(
        logger=logger,
        config=cfg,
        notification_service=notif,
        now_provider=(lambda: now) if now else None,
    )
    return reporter, logger, notif


def _make_context(**overrides) -> OrderContext:
    base = dict(
        order_key="005930:BUY:KRX",
        stock_code="005930",
        side=OrderSide.BUY,
        state=OrderState.FILLED,
        exchange=Exchange.KRX,
        price=70000,
        qty=10,
        filled_qty=10,
        remaining_qty=0,
        created_at=datetime(2026, 5, 17, 9, 0, 0),
        expected_fill_price=70000,
        average_fill_price=70500.0,
        slippage_amount_won=500.0,
        slippage_pct=0.7142857,
        first_fill_latency_sec=12.5,
    )
    base.update(overrides)
    return OrderContext(**base)


# ── resolve_expected_fill_price ─────────────────────────────────────────────

def test_resolve_expected_fill_price_returns_price_when_positive():
    reporter, *_ = _make_reporter()
    assert reporter.resolve_expected_fill_price(70000, None) == 70000


def test_resolve_expected_fill_price_returns_none_when_no_policy_and_market_order():
    reporter, *_ = _make_reporter()
    assert reporter.resolve_expected_fill_price(0, None) is None


def test_resolve_expected_fill_price_uses_executable_price_from_policy():
    reporter, *_ = _make_reporter()
    decision = _make_decision({"executable_price": 71000})
    assert reporter.resolve_expected_fill_price(0, decision) == 71000


def test_resolve_expected_fill_price_falls_back_to_reference_price():
    reporter, *_ = _make_reporter()
    decision = _make_decision({"reference_price": 72000})
    assert reporter.resolve_expected_fill_price(0, decision) == 72000


def test_resolve_expected_fill_price_handles_invalid_value():
    reporter, *_ = _make_reporter()
    decision = _make_decision({"executable_price": "not-a-number"})
    assert reporter.resolve_expected_fill_price(0, decision) is None


# ── resolve_order_type ──────────────────────────────────────────────────────

def test_resolve_order_type_market_when_price_zero():
    reporter, *_ = _make_reporter()
    assert reporter.resolve_order_type(0, None) == "market"


def test_resolve_order_type_limit_when_price_positive():
    reporter, *_ = _make_reporter()
    assert reporter.resolve_order_type(70000, None) == "limit"


def test_resolve_order_type_uses_policy_override():
    reporter, *_ = _make_reporter()
    decision = _make_decision({"order_type": "iceberg"})
    assert reporter.resolve_order_type(70000, decision) == "iceberg"


# ── resolve_spread_pct ──────────────────────────────────────────────────────

def test_resolve_spread_pct_none_without_policy():
    reporter, *_ = _make_reporter()
    assert reporter.resolve_spread_pct(None) is None


def test_resolve_spread_pct_parses_float():
    reporter, *_ = _make_reporter()
    decision = _make_decision({"spread_pct": "0.5"})
    assert reporter.resolve_spread_pct(decision) == 0.5


def test_resolve_spread_pct_handles_invalid():
    reporter, *_ = _make_reporter()
    decision = _make_decision({"spread_pct": "nope"})
    assert reporter.resolve_spread_pct(decision) is None


# ── build_update ────────────────────────────────────────────────────────────

def test_build_update_computes_buy_slippage():
    reporter, *_ = _make_reporter(now=datetime(2026, 5, 17, 9, 0, 30))
    context = _make_context(
        state=OrderState.SUBMITTED,
        filled_qty=0,
        remaining_qty=10,
        average_fill_price=None,
        slippage_amount_won=None,
        slippage_pct=None,
        first_fill_latency_sec=None,
        expected_fill_price=70000,
    )
    report = OrderExecutionReport(
        broker_order_no="A001",
        stock_code="005930",
        side=OrderSide.BUY,
        fill_qty=10,
        fill_price=70500,
    )
    update = reporter.build_update(context, report, filled_qty=10)
    assert update["average_fill_price"] == pytest.approx(70500.0)
    assert update["last_fill_price"] == 70500
    assert update["slippage_amount_won"] == pytest.approx(500.0)
    assert update["slippage_pct"] == pytest.approx(500 / 70000 * 100)
    assert update["first_fill_latency_sec"] == pytest.approx(30.0)


def test_build_update_inverts_slippage_for_sell():
    reporter, *_ = _make_reporter(now=datetime(2026, 5, 17, 9, 0, 0))
    context = _make_context(
        order_key="005930:SELL:KRX",
        side=OrderSide.SELL,
        state=OrderState.SUBMITTED,
        filled_qty=0,
        remaining_qty=10,
        average_fill_price=None,
        slippage_amount_won=None,
        slippage_pct=None,
        first_fill_latency_sec=None,
        expected_fill_price=70000,
    )
    report = OrderExecutionReport(
        broker_order_no="A002",
        stock_code="005930",
        side=OrderSide.SELL,
        fill_qty=10,
        fill_price=69500,
    )
    update = reporter.build_update(context, report, filled_qty=10)
    # Sell: slippage = expected - avg_fill = 70000 - 69500 = +500 (adverse)
    assert update["slippage_amount_won"] == pytest.approx(500.0)


# ── log: early return + dispatch ────────────────────────────────────────────

def test_log_skips_non_terminal_without_average_fill_price():
    reporter, logger, _ = _make_reporter()
    context = _make_context(
        state=OrderState.SUBMITTED,
        average_fill_price=None,
        filled_qty=0,
        remaining_qty=10,
    )
    reporter.log(context)
    logger.info.assert_not_called()


def test_log_writes_two_lines_for_terminal_state():
    reporter, logger, _ = _make_reporter(now=datetime(2026, 5, 17, 9, 1, 0))
    context = _make_context()
    reporter.log(context)
    # 1: structured dict event, 2: formatted "[EXECUTION QUALITY] ..." string
    assert logger.info.call_count == 2
    structured_event = logger.info.call_args_list[0].args[0]
    formatted = logger.info.call_args_list[1].args[0]
    assert structured_event["event"] == "execution_quality"
    assert structured_event["order_key"] == context.order_key
    assert formatted.startswith("[EXECUTION QUALITY]")


# ── emit_alert + dedup ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_emit_alert_noop_without_notification_service():
    reporter = ExecutionQualityReporter(
        logger=_Logger(),
        config=ExecutionQualityReportConfig(),
        notification_service=None,
    )
    context = _make_context()
    # 임계 초과인 이벤트를 만들어도 notification_service가 없으면 no-op
    event = {
        "slippage_pct": 5.0,
        "first_fill_latency_sec": 100.0,
        "unfilled_ratio_pct": 0,
        "state": OrderState.FILLED.value,
    }
    reporter.emit_alert(context, event)
    # 알람이 발행되지 않아도 dedup set은 비어있어야 한다 (조기 반환)
    assert reporter._alerted == set()


@pytest.mark.asyncio
async def test_emit_alert_publishes_once_then_dedupes_same_breach():
    cfg = ExecutionQualityReportConfig(
        warn_avg_slippage_pct=0.5,
        candidate_avg_slippage_pct=5.0,
    )
    notif = AsyncMock()
    reporter, _logger, _ = _make_reporter(config=cfg, notification=notif)
    context = _make_context()
    event = {
        "slippage_pct": 1.0,  # > 0.5 warn threshold
        "first_fill_latency_sec": None,
        "unfilled_ratio_pct": None,
        "state": OrderState.FILLED.value,
        "order_key": context.order_key,
    }
    reporter.emit_alert(context, event)
    if reporter._notification_tasks:
        await asyncio.gather(*list(reporter._notification_tasks))
    assert notif.emit.await_count == 1
    args = notif.emit.await_args.args
    assert args[0] == NotificationCategory.TRADE
    assert args[1] == NotificationLevel.WARNING
    assert args[2] == "체결 품질 임계 초과"
    assert (context.order_key, "slippage_pct") in reporter._alerted

    # 두 번째 호출은 동일 (order_key, metric) → dedup으로 추가 발행 없음
    reporter.emit_alert(context, event)
    if reporter._notification_tasks:
        await asyncio.gather(*list(reporter._notification_tasks))
    assert notif.emit.await_count == 1


@pytest.mark.asyncio
async def test_emit_alert_escalates_to_error_when_candidate_exceeded():
    cfg = ExecutionQualityReportConfig(
        warn_avg_slippage_pct=0.5,
        candidate_avg_slippage_pct=1.0,
    )
    notif = AsyncMock()
    reporter, _logger, _ = _make_reporter(config=cfg, notification=notif)
    context = _make_context()
    event = {
        "slippage_pct": 2.0,  # > candidate 1.0 → severity error
        "first_fill_latency_sec": None,
        "unfilled_ratio_pct": None,
        "state": OrderState.FILLED.value,
    }
    reporter.emit_alert(context, event)
    if reporter._notification_tasks:
        await asyncio.gather(*list(reporter._notification_tasks))
    assert notif.emit.await_count == 1
    assert notif.emit.await_args.args[1] == NotificationLevel.ERROR


def test_emit_alert_ignores_negative_slippage_for_buy_filled():
    """슬리피지가 0 이하(유리한 체결)이면 알람을 발행하지 않는다."""
    cfg = ExecutionQualityReportConfig(warn_avg_slippage_pct=0.1)
    notif = AsyncMock()
    reporter, _logger, _ = _make_reporter(config=cfg, notification=notif)
    context = _make_context()
    event = {
        "slippage_pct": -1.0,
        "first_fill_latency_sec": None,
        "unfilled_ratio_pct": None,
        "state": OrderState.FILLED.value,
    }
    reporter.emit_alert(context, event)
    notif.emit.assert_not_called()


def test_emit_alert_includes_incomplete_fill_only_for_terminal_canceled_or_rejected():
    cfg = ExecutionQualityReportConfig(warn_incomplete_fill_ratio_pct=10.0)
    notif = AsyncMock()
    reporter, _logger, _ = _make_reporter(config=cfg, notification=notif)
    context = _make_context(state=OrderState.CANCELED)
    event = {
        "slippage_pct": None,
        "first_fill_latency_sec": None,
        "unfilled_ratio_pct": 50.0,
        "state": OrderState.CANCELED.value,
    }
    breaches = reporter._find_breaches(event)
    metrics = {b["metric"] for b in breaches}
    assert "incomplete_fill_ratio_pct" in metrics


def test_emit_alert_ignores_unfilled_ratio_for_active_partial_fill():
    """부분체결 중 잔량은 아직 대기 중인 수량이므로 미체결 품질 알람 대상이 아니다."""
    cfg = ExecutionQualityReportConfig(
        warn_avg_unfilled_ratio_pct=20.0,
        candidate_avg_unfilled_ratio_pct=40.0,
        warn_incomplete_fill_ratio_pct=20.0,
    )
    notif = AsyncMock()
    reporter, _logger, _ = _make_reporter(config=cfg, notification=notif)
    event = {
        "slippage_pct": 0.0,
        "first_fill_latency_sec": 2.17,
        "unfilled_ratio_pct": 75.47,
        "state": OrderState.PARTIAL_FILLED.value,
    }

    breaches = reporter._find_breaches(event)

    assert breaches == []


def test_emit_alert_uses_incomplete_metric_for_terminal_remaining_qty():
    cfg = ExecutionQualityReportConfig(
        warn_avg_unfilled_ratio_pct=20.0,
        candidate_avg_unfilled_ratio_pct=40.0,
        warn_incomplete_fill_ratio_pct=20.0,
    )
    notif = AsyncMock()
    reporter, _logger, _ = _make_reporter(config=cfg, notification=notif)
    event = {
        "slippage_pct": None,
        "first_fill_latency_sec": None,
        "unfilled_ratio_pct": 75.47,
        "state": OrderState.CANCELED.value,
    }

    breaches = reporter._find_breaches(event)
    metrics = {b["metric"] for b in breaches}

    assert metrics == {"incomplete_fill_ratio_pct"}


# ── _fmt_optional ───────────────────────────────────────────────────────────

def test_fmt_optional_handles_none_and_numbers_and_garbage():
    assert ExecutionQualityReporter._fmt_optional(None) == "N/A"
    assert ExecutionQualityReporter._fmt_optional(3.14159, precision=2) == "3.14"
    assert ExecutionQualityReporter._fmt_optional("abc") == "abc"
