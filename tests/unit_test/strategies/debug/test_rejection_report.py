import json
import logging
from datetime import datetime

import pytest

from common.types import TradeSignal
from services.backtest_execution_simulator import (
    BacktestOrder,
    OrderSide,
    OrderType,
    PortfolioDecision,
)
from strategies.debug.rejection_collector import RejectionEvent
from strategies.debug.rejection_report import _event_label, format_console, format_json
from strategies.debug.strategy_debug_runner import DebugReport


def _event(event, code="005930", reason="", **details):
    payload = {"event": event, "code": code}
    if reason:
        payload["reason"] = reason
    payload.update(details)
    return RejectionEvent(
        event=event,
        code=code,
        reason=reason or event,
        details=payload,
        timestamp=datetime(2026, 4, 30, 9, 0),
        level=logging.INFO,
    )


@pytest.mark.parametrize(
    "event, reason, details, expected",
    [
        ("entry_rejected", "low_execution_strength", {"entry_type": "PP", "cgld": 80, "threshold": 120}, "cgld=80 < 120"),
        ("stage_blocked", "", {"stage": 3}, "stage=3"),
        ("pp_rejected", "poor_candle_quality", {"pos": 0.2, "threshold": 0.5}, "pos=0.2 < 0.5"),
        ("pp_rejected", "no_ma_proximity", {"closest_ma_pct": -1.25}, "최근접MA=-1.25%"),
        ("bgu_rejected", "insufficient_volume", {"proj_vol": 1000, "threshold": 2000}, "예상=1,000 < 2,000"),
        ("bgu_rejected", "low_pg_ratio", {"pg_to_tv_pct": 2.0, "threshold": 5.0}, "pg_tv=2.0% < 5.0%"),
        ("smart_money_rejected", "low_pg_metrics", {"pg_tv_pct": 1, "pg_mc_pct": 2, "mc_threshold": 3, "cgld": 4}, "pg_tv=1% pg_mc=2% < 3% cgld=4"),
        ("scan_skipped", "market_closed", {}, "market_closed"),
        ("buy_signal_generated", "", {}, "매수 신호 발생"),
    ],
)
def test_event_label_formats_known_reasons(event, reason, details, expected):
    assert expected in _event_label(_event(event, reason=reason, **details))


def test_format_console_includes_requested_scanned_missing_signals_and_limitations():
    report = DebugReport(
        strategy_name="테스트전략",
        requested_codes=["005930", "000660", "035720"],
        scanned_codes=["005930", "000660"],
        missing_codes=["035720"],
        signals=[TradeSignal(code="005930", name="삼성전자", action="BUY", price=70000, qty=1, reason="ok", strategy_name="테스트전략")],
        events=[
            _event("pp_rejected", code="000660", reason="no_ma_proximity", closest_ma_pct="N/A"),
            _event("buy_signal_generated", code="005930"),
        ],
        limitations=["테스트 한계"],
    )

    text = format_console(report)

    assert "전략 디버깅 리포트: 테스트전략" in text
    assert "[NOT_IN_WATCHLIST]  035720" in text
    assert "005930" in text and "신호" in text
    assert "000660" in text and "탈락" in text
    assert "[탈락 사유 Top 5]" in text
    assert "테스트 한계" in text


def test_format_console_handles_full_universe_and_no_event_code():
    report = DebugReport(
        strategy_name="전체전략",
        requested_codes=None,
        scanned_codes=["005930"],
        missing_codes=[],
        signals=[],
        events=[],
        limitations=[],
    )

    text = format_console(report)

    assert "universe 전체" in text
    assert "정보없음" in text


def test_format_json_serializes_signals_events_and_datetime():
    report = DebugReport(
        strategy_name="JSON전략",
        requested_codes=["005930"],
        scanned_codes=["005930"],
        missing_codes=[],
        signals=[TradeSignal(code="005930", name="삼성전자", action="BUY", price=70000, qty=1, reason="ok", strategy_name="JSON전략")],
        events=[_event("pp_rejected", reason="no_ma_proximity", closest_ma_pct=-1.0)],
        limitations=["limit"],
        journal_records=[{"source": "backtest", "code": "005930", "status": "SIGNAL"}],
    )

    payload = json.loads(format_json(report))

    assert payload["strategy_name"] == "JSON전략"
    assert payload["signals"][0]["code"] == "005930"
    assert payload["events"][0]["timestamp"] == "2026-04-30T09:00:00"
    assert payload["limitations"] == ["limit"]
    assert payload["journal_records"] == [{"source": "backtest", "code": "005930", "status": "SIGNAL"}]


def test_format_outputs_portfolio_dry_run_decisions():
    order = BacktestOrder(
        order_id="debug_1",
        code="005930",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        price=70000,
        qty=2,
        strategy="JSON전략",
    )
    report = DebugReport(
        strategy_name="JSON전략",
        requested_codes=["005930"],
        scanned_codes=["005930"],
        missing_codes=[],
        signals=[],
        events=[],
        limitations=[],
        portfolio_decisions=[
            PortfolioDecision(order=order, accepted=False, reason="cash_short")
        ],
    )

    text = format_console(report)
    payload = json.loads(format_json(report))

    assert "[포트폴리오 dry-run]" in text
    assert "cash_short" in text
    assert payload["portfolio_decisions"] == [{
        "code": "005930",
        "strategy": "JSON전략",
        "accepted": False,
        "reason": "cash_short",
        "reserved_cash": 0.0,
    }]
