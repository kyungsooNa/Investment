# tests/unit_test/services/test_strategy_log_report_regime.py
"""HTML 일일 리포트의 전략별 regime 분해 섹션 검증 (R-2 후속).

전략별 dominant regime 과 버킷별 평균순익을 노출하고, 전 전략이 같은
regime 에 몰려 있는지(=단일 regime 베팅) concentration 으로 드러낸다.
비교 가능한 전략이 2개 미만이면 섹션을 생략한다.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from services.strategy_log_report_service import StrategyLogReportService


def _sold(strategy, code, signal_time, net_pnl, net_return,
          kospi="bull", kosdaq="bull", stock_market="KOSPI"):
    return {
        "strategy": strategy,
        "code": code,
        "status": "SOLD",
        "signal_time": signal_time,
        "net_pnl": net_pnl,
        "net_return": net_return,
        "market_regime": {
            "kospi": kospi,
            "kosdaq": kosdaq,
            "stock_market": stock_market,
            "trading_value_surge": False,
        },
    }


def test_section_reports_per_strategy_regime_and_concentration():
    vts = MagicMock()
    vts.get_standard_journal_records.return_value = [
        _sold("S1", "000001", "20260514", 100.0, 2.0, stock_market="KOSPI"),
        _sold("S1", "000002", "20260515", -50.0, -1.0, stock_market="KOSPI"),
        _sold("S2", "000003", "20260514", 300.0, 1.5, stock_market="KOSPI"),
    ]
    svc = StrategyLogReportService(log_dir=".", virtual_trade_service=vts)
    section = svc._build_regime_decomposition_section("20260610")

    assert section is not None
    assert "regime" in section.lower() or "국면" in section
    assert "S1" in section
    assert "S2" in section
    # 두 전략 모두 KOSPI 상승 -> 집중도 100%
    assert "집중도" in section
    assert "100%" in section


def test_section_none_without_service():
    svc = StrategyLogReportService(log_dir=".")
    assert svc._build_regime_decomposition_section("20260610") is None


def test_section_none_on_empty_journal():
    vts = MagicMock()
    vts.get_standard_journal_records.return_value = []
    svc = StrategyLogReportService(log_dir=".", virtual_trade_service=vts)
    assert svc._build_regime_decomposition_section("20260610") is None


def test_section_none_when_fewer_than_two_strategies():
    vts = MagicMock()
    vts.get_standard_journal_records.return_value = [
        _sold("S1", "000001", "20260514", 100.0, 2.0, stock_market="KOSPI"),
    ]
    svc = StrategyLogReportService(log_dir=".", virtual_trade_service=vts)
    assert svc._build_regime_decomposition_section("20260610") is None
