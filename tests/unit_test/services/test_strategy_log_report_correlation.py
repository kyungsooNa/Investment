# tests/unit_test/services/test_strategy_log_report_correlation.py
"""HTML 일일 리포트의 전략 상관 섹션 검증 (R-2).

live journal 의 전략별 일별 net_return 시계열로 compute_strategy_correlation_summary
를 돌려 고상관 클러스터/최고 상관쌍/경고를 리포트에 노출하는지 확인한다.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from services.strategy_log_report_service import StrategyLogReportService


def _sold(strategy: str, net_return: float, date: str) -> dict:
    return {
        "strategy": strategy,
        "status": "SOLD",
        "net_return": net_return,
        "signal_time": date,
    }


def _correlated_journal() -> list:
    dates = ["2026-01-02", "2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08", "2026-01-09"]
    s1 = [1.0, 2.0, -1.0, 3.0, -2.0, 1.0]
    s2 = [1.1, 1.9, -0.9, 2.8, -1.8, 1.2]  # near-identical -> corr ~0.99
    records = []
    for d, v in zip(dates, s1):
        records.append(_sold("S1", v, d))
    for d, v in zip(dates, s2):
        records.append(_sold("S2", v, d))
    return records


def test_build_strategy_correlation_section_reports_high_pair():
    vts = MagicMock()
    vts.get_standard_journal_records.return_value = _correlated_journal()

    svc = StrategyLogReportService(log_dir=".", virtual_trade_service=vts)
    section = svc._build_strategy_correlation_section("20260109")

    assert section is not None
    assert "전략 상관" in section
    assert "S1" in section and "S2" in section
    # near-identical series -> correlation >= 0.8 warning threshold.
    assert "고상관" in section


def test_build_strategy_correlation_section_none_without_service():
    svc = StrategyLogReportService(log_dir=".")
    assert svc._build_strategy_correlation_section("20260109") is None


def test_build_strategy_correlation_section_none_when_no_pairs():
    vts = MagicMock()
    # single strategy -> no pair to correlate.
    vts.get_standard_journal_records.return_value = [_sold("S1", 1.0, "2026-01-02")]
    svc = StrategyLogReportService(log_dir=".", virtual_trade_service=vts)
    assert svc._build_strategy_correlation_section("20260109") is None


def test_build_strategy_correlation_section_none_on_empty_journal():
    vts = MagicMock()
    vts.get_standard_journal_records.return_value = []
    svc = StrategyLogReportService(log_dir=".", virtual_trade_service=vts)
    assert svc._build_strategy_correlation_section("20260109") is None
