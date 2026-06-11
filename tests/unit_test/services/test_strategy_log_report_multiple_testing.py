# tests/unit_test/services/test_strategy_log_report_multiple_testing.py
"""HTML 일일 리포트의 다중검정 / Deflated Sharpe 섹션 검증 (P1 1-7).

live journal 의 전략별 metric(sharpe_ratio/total_net_pnl/trade_count)을
compute_multiple_testing_bias_summary 로 집계해 formal Deflated Sharpe 와
편향 경고를 리포트에 노출하는지 확인한다.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from services.strategy_log_report_service import StrategyLogReportService


def _sold(strategy: str, net_return: float, net_pnl: float, date: str = "2026-04-18") -> dict:
    return {
        "strategy": strategy,
        "status": "SOLD",
        "net_return": net_return,
        "net_pnl": net_pnl,
        "sell_date": date,
    }


def _five_strategy_journal() -> list:
    series = {
        "S1": ([3.0, 4.0, 5.0], [300.0, 400.0, 500.0]),
        "S2": ([2.0, 3.0, 4.0], [200.0, 300.0, 400.0]),
        "S3": ([1.0, 2.0, 3.0], [100.0, 200.0, 300.0]),
        "S4": ([0.5, 1.0, 1.5], [50.0, 100.0, 150.0]),
        "S5": ([0.2, 0.5, 0.8], [20.0, 50.0, 80.0]),
    }
    records = []
    for strategy, (returns, pnls) in series.items():
        for r, pnl in zip(returns, pnls):
            records.append(_sold(strategy, r, pnl))
    return records


def test_build_multiple_testing_section_reports_deflated_sharpe():
    vts = MagicMock()
    vts.get_standard_journal_records.return_value = _five_strategy_journal()

    svc = StrategyLogReportService(log_dir=".", virtual_trade_service=vts)
    section = svc._build_multiple_testing_section("20260418")

    assert section is not None
    assert "Deflated Sharpe" in section
    assert "전략 5개" in section
    # best by total_net_pnl is S1 (highest cumulative pnl).
    assert "S1" in section


def test_build_multiple_testing_section_normalizes_strategy_aliases_and_excludes_manual():
    vts = MagicMock()
    records = []
    series = {
        "래리윌리엄스VBO": ([3.0, 4.0], [300.0, 400.0]),
        "larry_williams_vbo": ([5.0, 6.0], [500.0, 600.0]),
        "RSI2눌림목": ([1.0, 2.0], [100.0, 200.0]),
        "rsi2_pullback": ([2.0, 3.0], [200.0, 300.0]),
        "오닐스퀴즈돌파": ([0.5, 1.0], [50.0, 100.0]),
        "오닐PP/BGU": ([0.4, 0.8], [40.0, 80.0]),
        "LarryWilliamsCB": ([0.3, 0.6], [30.0, 60.0]),
        "수동매매": ([10.0, 11.0], [1000.0, 1100.0]),
    }
    for strategy, (returns, pnls) in series.items():
        for r, pnl in zip(returns, pnls):
            records.append(_sold(strategy, r, pnl))
    vts.get_standard_journal_records.return_value = records

    svc = StrategyLogReportService(log_dir=".", virtual_trade_service=vts)
    section = svc._build_multiple_testing_section("20260418")

    assert section is not None
    assert "성과 표본 전략 5개" in section
    assert "수동매매" not in section
    assert "최고 래리윌리엄스VBO" in section


def test_build_multiple_testing_section_none_without_service():
    svc = StrategyLogReportService(log_dir=".")
    assert svc._build_multiple_testing_section("20260418") is None


def test_build_multiple_testing_section_none_for_single_strategy():
    vts = MagicMock()
    vts.get_standard_journal_records.return_value = [
        _sold("S1", 1.0, 100.0),
        _sold("S1", 2.0, 200.0),
    ]
    svc = StrategyLogReportService(log_dir=".", virtual_trade_service=vts)
    # trial_count < 2 -> no section.
    assert svc._build_multiple_testing_section("20260418") is None


def test_build_multiple_testing_section_none_on_empty_journal():
    vts = MagicMock()
    vts.get_standard_journal_records.return_value = []
    svc = StrategyLogReportService(log_dir=".", virtual_trade_service=vts)
    assert svc._build_multiple_testing_section("20260418") is None
