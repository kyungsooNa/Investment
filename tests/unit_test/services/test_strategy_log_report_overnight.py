# tests/unit_test/services/test_strategy_log_report_overnight.py
"""HTML 일일 리포트의 오버나이트 노출 섹션 검증 (R-4 후속).

장 마감 후 남은 HOLD(=익일 갭 노출)와 실현 멀티세션 보유 다운사이드를 노출하고,
노출이 없으면 섹션을 생략하는지 확인한다.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from services.strategy_log_report_service import StrategyLogReportService


def _hold(strategy: str, code: str, buy_date: str) -> dict:
    return {
        "strategy": strategy,
        "code": code,
        "status": "HOLD",
        "signal_time": buy_date,
        "net_return": None,
        "metadata": {"buy_date": buy_date, "sell_date": None},
    }


def _sold(strategy: str, code: str, buy_date: str, sell_date: str, net_return: float) -> dict:
    return {
        "strategy": strategy,
        "code": code,
        "status": "SOLD",
        "signal_time": buy_date,
        "net_return": net_return,
        "metadata": {"buy_date": buy_date, "sell_date": sell_date},
    }


def test_section_reports_open_holds_and_realized_overnight():
    vts = MagicMock()
    vts.get_standard_journal_records.return_value = [
        _hold("S1", "000001", "2026-06-05 10:00:00"),
        _hold("S1", "000002", "2026-06-09 13:00:00"),
        _sold("S2", "000003", "2026-06-01 10:00:00", "2026-06-04 14:00:00", -4.0),
    ]
    svc = StrategyLogReportService(log_dir=".", virtual_trade_service=vts)
    section = svc._build_overnight_exposure_section("20260610")

    assert section is not None
    assert "오버나이트 노출" in section
    assert "현재 보유" in section
    assert "S1" in section
    assert "실현 멀티세션 보유" in section
    assert "S2" in section
    # 최저 순익이 음수로 표기.
    assert "-4.00%" in section


def test_section_none_without_service():
    svc = StrategyLogReportService(log_dir=".")
    assert svc._build_overnight_exposure_section("20260610") is None


def test_section_none_on_empty_journal():
    vts = MagicMock()
    vts.get_standard_journal_records.return_value = []
    svc = StrategyLogReportService(log_dir=".", virtual_trade_service=vts)
    assert svc._build_overnight_exposure_section("20260610") is None


def test_section_none_when_only_intraday_and_no_holds():
    vts = MagicMock()
    # 당일 청산만 -> 오버나이트 노출 없음 -> 섹션 생략.
    vts.get_standard_journal_records.return_value = [
        _sold("S1", "000001", "2026-06-10 10:00:00", "2026-06-10 15:00:00", 1.0),
    ]
    svc = StrategyLogReportService(log_dir=".", virtual_trade_service=vts)
    assert svc._build_overnight_exposure_section("20260610") is None
