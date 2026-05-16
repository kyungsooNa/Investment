# tests/unit_test/services/test_strategy_log_report_volatility.py
"""HTML 일일 리포트의 변동성 섹션 검증."""
from __future__ import annotations

import json
import os
import tempfile
from typing import Optional

import pytest

from services.strategy_log_report_service import StrategyLogReportService


def _write_log(path: str, entries: list):
    with open(path, "w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _buy_entry(code: str, name: str, *, reason: str = "오닐돌파", price: int = 70000,
               volatility: Optional[float] = None,
               date: str = "2026-04-18") -> dict:
    metrics = {"price": price}
    if volatility is not None:
        metrics["volatility_20d_annualized"] = volatility
    return {
        "timestamp": f"{date} 10:00:00,000",
        "level": "INFO",
        "name": "strategy.OSB",
        "data": {
            "event": "buy_signal_generated",
            "code": code,
            "name": name,
            "reason": reason,
            "price": price,
            "metrics": metrics,
        },
    }


@pytest.fixture
def log_dir():
    with tempfile.TemporaryDirectory() as d:
        yield d


@pytest.mark.asyncio
async def test_volatility_section_present_when_buys_carry_volatility(log_dir):
    log_path = os.path.join(log_dir, "20260418_093000_OSB.log.json")
    _write_log(log_path, [
        _buy_entry("005930", "삼성전자", volatility=0.30),
        _buy_entry("000660", "SK하이닉스", volatility=0.40),
        _buy_entry("035420", "NAVER", volatility=0.50),
    ])

    svc = StrategyLogReportService(log_dir=log_dir)
    report = await svc.generate_report("20260418")

    assert "매수 종목 변동성" in report
    assert "OSB" in report
    # 평균 = (0.30 + 0.40 + 0.50) / 3 = 0.40 → 40.0%
    assert "평균 40.0%" in report
    # 중앙값 = 0.40 → 40.0%
    assert "중앙값 40.0%" in report
    assert "3건" in report


@pytest.mark.asyncio
async def test_volatility_section_absent_when_no_volatility_metric(log_dir):
    log_path = os.path.join(log_dir, "20260418_093000_OSB.log.json")
    _write_log(log_path, [
        _buy_entry("005930", "삼성전자", volatility=None),
    ])

    svc = StrategyLogReportService(log_dir=log_dir)
    report = await svc.generate_report("20260418")
    assert "매수 종목 변동성" not in report


@pytest.mark.asyncio
async def test_volatility_section_skips_strategies_without_samples(log_dir):
    """일부 전략에만 volatility 가 있어도 해당 전략만 노출, 나머지는 생략."""
    osb_log = os.path.join(log_dir, "20260418_093000_OSB.log.json")
    htf_log = os.path.join(log_dir, "20260418_093000_HTF.log.json")
    _write_log(osb_log, [_buy_entry("005930", "삼성전자", volatility=0.25)])
    _write_log(htf_log, [_buy_entry("000660", "SK하이닉스", volatility=None)])

    svc = StrategyLogReportService(log_dir=log_dir)
    report = await svc.generate_report("20260418")

    assert "매수 종목 변동성" in report
    # OSB 만 노출 — HTF 는 변동성 섹션에서 제외 (다른 섹션에서는 등장 가능)
    vol_section_start = report.index("매수 종목 변동성")
    vol_section = report[vol_section_start:]
    # 다음 섹션까지의 범위 안에서 OSB 가 있어야 함
    assert "OSB" in vol_section
    # 단순 in 검사로는 다른 섹션의 HTF 등장과 구분이 안되므로, 라인 단위 검사
    osb_line_found = any(
        "OSB" in line and "평균" in line and "중앙값" in line
        for line in vol_section.splitlines()
    )
    assert osb_line_found


def test_build_volatility_section_direct():
    """순수 함수 단위로 평균/중앙값 계산 정확성을 검증."""
    svc = StrategyLogReportService(log_dir=".")
    summaries = [
        {
            "name": "S1",
            "bought": {
                "A": {"volatility_20d_annualized": 0.20},
                "B": {"volatility_20d_annualized": 0.40},
                "C": {"volatility_20d_annualized": 0.60},
            },
        },
        {
            "name": "S2",
            "bought": {
                "X": {"volatility_20d_annualized": None},  # 표본 제외
                "Y": {"volatility_20d_annualized": 0.10},
                "Z": {"volatility_20d_annualized": 0.30},
            },
        },
    ]
    section = svc._build_volatility_section(summaries)
    assert section is not None
    # 평균 내림차순 정렬: S1(0.40) 위, S2(0.20) 아래
    s1_pos = section.index("S1 ")
    s2_pos = section.index("S2 ")
    assert s1_pos < s2_pos
    assert "평균 40.0%" in section  # S1
    assert "중앙값 40.0%" in section
    assert "평균 20.0%" in section  # S2 (Y=0.10, Z=0.30, 평균 0.20, 중앙값 0.20)
    assert "2건" in section  # S2 표본 2개 (X 는 None 제외)


def test_build_volatility_section_returns_none_when_empty():
    svc = StrategyLogReportService(log_dir=".")
    assert svc._build_volatility_section([]) is None
    assert svc._build_volatility_section([{"name": "S", "bought": {}}]) is None
    assert svc._build_volatility_section(
        [{"name": "S", "bought": {"A": {"volatility_20d_annualized": None}}}]
    ) is None
