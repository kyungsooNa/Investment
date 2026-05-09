from __future__ import annotations

from types import SimpleNamespace

from scripts.run_backtest import _build_dates, _format_console, _get_program_provider


def test_build_dates_accepts_comma_separated_dates():
    args = SimpleNamespace(dates="20260501,20260503", start_date=None, end_date=None)

    assert _build_dates(args) == ["20260501", "20260503"]


def test_build_dates_accepts_inclusive_start_end_range():
    args = SimpleNamespace(dates=None, start_date="20260501", end_date="20260503")

    assert _build_dates(args) == ["20260501", "20260502", "20260503"]


def test_format_console_summarizes_execution_and_portfolio():
    result = SimpleNamespace(
        strategy_name="오닐PP/BGU",
        dates=["20260501", "20260502"],
        execution_reports=[
            SimpleNamespace(order=SimpleNamespace(side=SimpleNamespace(value="BUY")), filled_qty=2),
            SimpleNamespace(order=SimpleNamespace(side=SimpleNamespace(value="SELL")), filled_qty=1),
        ],
        journal_records=[{"status": "REJECTED"}],
        portfolio={
            "cash": 1_100_000,
            "available_cash": 1_090_000,
            "realized_net_pnl": 90_000,
            "positions": {"005930": {"qty": 1}},
        },
    )

    text = _format_console(result)

    assert "오닐PP/BGU" in text
    assert "BUY 체결: 1" in text
    assert "SELL 체결: 1" in text
    assert "거부 기록: 1" in text
    assert "실현손익(순): 90,000" in text


def test_get_program_provider_uses_market_data_broker_when_available():
    broker = object()
    sqs = SimpleNamespace(market_data_service=SimpleNamespace(_broker_api_wrapper=broker))

    assert _get_program_provider(sqs) is broker
