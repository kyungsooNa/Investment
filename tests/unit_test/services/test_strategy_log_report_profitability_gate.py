# tests/unit_test/services/test_strategy_log_report_profitability_gate.py
"""HTML 일일 리포트의 전략별 수익성 게이트 섹션 검증 (P1 1-6).

라이브 표준 journal 에 evaluate_strategy_profitability_gate 를 돌려
전략별 통과/차단 상태와 차단 사유를 리포트에 노출하는지 확인한다.
파이프라인 보강 전에는 이 섹션이 존재하지 않아 통과 근거를 운영자가
일일 리포트에서 볼 수 없었다.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from services.strategy_log_report_service import StrategyLogReportService
from services.strategy_profitability_gate_service import StrategyProfitabilityGateConfig


def _sold(strategy: str, net_return: float, net_pnl: float, date: str = "2026-04-18") -> dict:
    return {
        "strategy": strategy,
        "status": "SOLD",
        "net_return": net_return,
        "net_pnl": net_pnl,
        "sell_date": date,
    }


def test_section_reports_pass_status_for_profitable_strategy():
    vts = MagicMock()
    vts.get_standard_journal_records.return_value = [
        _sold("S1", 3.0, 300.0),
        _sold("S1", 4.0, 400.0),
        _sold("S1", 5.0, 500.0),
    ]
    svc = StrategyLogReportService(
        log_dir=".",
        virtual_trade_service=vts,
        profitability_gate_config=StrategyProfitabilityGateConfig(min_trades=2),
    )

    section = svc._build_profitability_gate_section("20260418")

    assert section is not None
    assert "수익성 게이트" in section
    assert "S1" in section
    assert "통과" in section
    # min_trades 진척 표기 (거래 3/2)
    assert "거래 3/2" in section


def test_section_flags_insufficient_sample_with_default_min_trades():
    vts = MagicMock()
    vts.get_standard_journal_records.return_value = [_sold("S1", 1.0, 100.0)]
    # config 미주입 → 기본 min_trades=30
    svc = StrategyLogReportService(log_dir=".", virtual_trade_service=vts)

    section = svc._build_profitability_gate_section("20260418")

    assert section is not None
    assert "표본 부족" in section
    assert "거래 1/30" in section


def test_section_renders_blocking_reason_in_korean():
    vts = MagicMock()
    vts.get_standard_journal_records.return_value = [
        _sold("S1", -2.0, -200.0),
        _sold("S1", -3.0, -300.0),
    ]
    svc = StrategyLogReportService(
        log_dir=".",
        virtual_trade_service=vts,
        profitability_gate_config=StrategyProfitabilityGateConfig(min_trades=2),
    )

    section = svc._build_profitability_gate_section("20260418")

    assert section is not None
    assert "차단" in section
    assert "순손익 음수" in section


def test_section_none_without_service():
    svc = StrategyLogReportService(log_dir=".")
    assert svc._build_profitability_gate_section("20260418") is None


def test_section_none_on_empty_journal():
    vts = MagicMock()
    vts.get_standard_journal_records.return_value = []
    svc = StrategyLogReportService(log_dir=".", virtual_trade_service=vts)
    assert svc._build_profitability_gate_section("20260418") is None
