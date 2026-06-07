# tests/unit_test/services/test_strategy_log_report_signal_metadata.py
"""HTML 일일 리포트의 신호 metadata 섹션 검증 (P1 1-6).

매수 신호의 entry_reason / confidence / expected_holding_period_days 가
journal(원장) → 리포트 분석까지 이어지는지 확인한다. trailing_rule / required_data 는
journal metadata 로 보존되며 일일 리포트 headline 에는 surfacing 하지 않는다.
"""
from __future__ import annotations

import json
import os
import tempfile
from typing import Optional
from unittest.mock import MagicMock

import pytest

from services.strategy_log_report_service import StrategyLogReportService


def _write_log(path: str, entries: list):
    with open(path, "w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _buy_entry(code: str, name: str, *, entry_reason: Optional[str] = None,
               confidence: Optional[float] = None,
               expected_holding: Optional[int] = None,
               price: int = 70000, date: str = "2026-04-18") -> dict:
    metrics = {"price": price}
    if entry_reason is not None:
        metrics["entry_reason"] = entry_reason
    if confidence is not None:
        metrics["confidence"] = confidence
    if expected_holding is not None:
        metrics["expected_holding_period_days"] = expected_holding
    return {
        "timestamp": f"{date} 10:00:00,000",
        "level": "INFO",
        "name": "strategy.OSB",
        "data": {
            "event": "buy_signal_generated",
            "code": code,
            "name": name,
            "reason": "오닐돌파",
            "price": price,
            "metrics": metrics,
        },
    }


@pytest.fixture
def log_dir():
    with tempfile.TemporaryDirectory() as d:
        yield d


@pytest.mark.asyncio
async def test_signal_metadata_section_present_via_log_metrics(log_dir):
    log_path = os.path.join(log_dir, "20260418_093000_OSB.log.json")
    _write_log(log_path, [
        _buy_entry("005930", "삼성전자", entry_reason="squeeze_breakout", confidence=0.8, expected_holding=4),
        _buy_entry("000660", "SK하이닉스", entry_reason="squeeze_breakout", confidence=0.6, expected_holding=2),
    ])

    svc = StrategyLogReportService(log_dir=log_dir)
    report = await svc.generate_report("20260418")

    assert "매수 신호 메타데이터" in report
    assert "squeeze_breakout×2" in report
    # 평균 conf = (0.8 + 0.6) / 2 = 0.70
    assert "평균 conf 0.70" in report
    # 기대보유 = (4 + 2) / 2 = 3.0일
    assert "기대보유 3.0일" in report


@pytest.mark.asyncio
async def test_signal_metadata_section_absent_when_no_metadata(log_dir):
    log_path = os.path.join(log_dir, "20260418_093000_OSB.log.json")
    _write_log(log_path, [_buy_entry("005930", "삼성전자")])

    svc = StrategyLogReportService(log_dir=log_dir)
    report = await svc.generate_report("20260418")
    assert "매수 신호 메타데이터" not in report


def test_build_signal_metadata_section_direct():
    """순수 함수 단위로 진입사유 분포 / 평균 confidence / 평균 기대보유 검증."""
    svc = StrategyLogReportService(log_dir=".")
    summaries = [
        {
            "name": "S1",
            "bought": {
                "A": {"entry_reason": "breakout", "confidence": 0.8, "expected_holding_period_days": 5},
                "B": {"entry_reason": "breakout", "confidence": 0.6, "expected_holding_period_days": 3},
                "C": {"entry_reason": "pullback", "confidence": None, "expected_holding_period_days": None},
            },
        },
        {
            "name": "S2",
            "bought": {
                "X": {"entry_reason": None, "confidence": None, "expected_holding_period_days": None},
            },
        },
    ]
    section = svc._build_signal_metadata_section(summaries)
    assert section is not None
    assert "매수 신호 메타데이터" in section
    # S1: breakout×2 (most_common 우선), pullback×1
    assert "breakout×2" in section
    assert "pullback×1" in section
    # conf 평균 = (0.8 + 0.6) / 2 = 0.70 (C 는 None 제외)
    assert "평균 conf 0.70" in section
    # 기대보유 평균 = (5 + 3) / 2 = 4.0일
    assert "기대보유 4.0일" in section
    # 표본 수 = max(reasons 3, conf 2, hold 2) = 3
    assert "3건" in section
    # S2 는 모든 필드가 None 이라 제외
    assert "S2" not in section


def test_build_signal_metadata_section_returns_none_when_empty():
    svc = StrategyLogReportService(log_dir=".")
    assert svc._build_signal_metadata_section([]) is None
    assert svc._build_signal_metadata_section([{"name": "S", "bought": {}}]) is None
    assert svc._build_signal_metadata_section(
        [{"name": "S", "bought": {"A": {"entry_reason": None, "confidence": None,
                                        "expected_holding_period_days": None}}}]
    ) is None


def test_executed_buys_by_strategy_includes_signal_metadata():
    """원장(DB) 경로의 bought dict 가 entry_reason/confidence/expected_holding 을 싣는다."""
    vts = MagicMock()
    vts.get_all_trades.return_value = [{
        "strategy": "오닐스퀴즈돌파", "code": "005930", "name": "삼성전자",
        "buy_date": "2026-04-18 10:00:00", "buy_price": 70000, "status": "HOLD",
        "reason": "돌파",
        "entry_reason": "squeeze_breakout", "confidence": 0.8,
        "expected_holding_period_days": 4,
    }]
    svc = StrategyLogReportService(log_dir=".", virtual_trade_service=vts)
    has_source, result = svc._executed_buys_by_strategy("20260418")

    assert has_source is True
    bought = next(iter(result.values()))
    info = bought["005930"]
    assert info["entry_reason"] == "squeeze_breakout"
    assert info["confidence"] == pytest.approx(0.8)
    assert info["expected_holding_period_days"] == pytest.approx(4.0)
