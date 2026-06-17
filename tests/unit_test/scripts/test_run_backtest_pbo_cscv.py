"""run_backtest 의 formal PBO(CSCV) 배선 회귀 테스트 (P1 1-7).

parameter-stability sweep 후보를 config로 보고 config별 per-period net_pnl 행렬을
조립 → compute_pbo_cscv. 여기서는 플래그 파싱 + 콘솔 렌더만 검증(알고리즘/행렬
빌더 자체는 test_multiple_testing_bias_service 로 잠겨 있음).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from scripts.run_backtest import (
    _format_pbo_cscv_console_lines,
    _parse_args,
    _run_ablation_for_result,
)
from services.backtest_period_runner import BacktestPeriodRunResult
from services.strategy_ablation_service import AblationVariant


def test_parse_args_includes_pbo_cscv_flags(monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        [
            "run_backtest.py", "--strategy", "oneil_pocket_pivot", "--dates", "20250102",
            "--parameter-stability", "oneil_pocket_pivot",
            "--pbo-cscv-splits", "8", "--max-pbo-cscv", "0.6",
        ],
    )
    args = _parse_args()
    assert args.pbo_cscv_splits == 8
    assert args.max_pbo_cscv == 0.6


def test_pbo_cscv_flags_default(monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        ["run_backtest.py", "--strategy", "oneil_pocket_pivot", "--dates", "20250102"],
    )
    args = _parse_args()
    assert args.pbo_cscv_splits == 16
    assert args.max_pbo_cscv is None


def test_format_pbo_cscv_none_or_empty():
    assert _format_pbo_cscv_console_lines(None) == []
    assert _format_pbo_cscv_console_lines({}) == []


def test_format_pbo_cscv_unavailable():
    lines = _format_pbo_cscv_console_lines({"available": False, "reason": "insufficient_periods"})
    assert len(lines) == 1
    assert "unavailable" in lines[0]
    assert "insufficient_periods" in lines[0]


def test_format_pbo_cscv_warn_when_failed():
    lines = _format_pbo_cscv_console_lines({
        "available": True, "pbo": 0.72, "passed": False,
        "n_configs": 5, "n_periods_used": 80, "n_splits": 8, "n_combinations": 70,
    })
    assert "pbo=0.720" in lines[0]
    assert "WARN" in lines[0]


def test_format_pbo_cscv_pass_when_below_threshold():
    lines = _format_pbo_cscv_console_lines({
        "available": True, "pbo": 0.10, "passed": True,
        "n_configs": 5, "n_periods_used": 80, "n_splits": 8, "n_combinations": 70,
    })
    assert "PASS" in lines[0]


def test_format_pbo_cscv_report_only_when_no_threshold():
    lines = _format_pbo_cscv_console_lines({
        "available": True, "pbo": 0.40, "passed": None,
        "n_configs": 5, "n_periods_used": 80, "n_splits": 8, "n_combinations": 70,
    })
    assert "pbo=0.400" in lines[0]
    assert "WARN" not in lines[0] and "PASS" not in lines[0]


def _dated_records(base_pnl):
    return [
        {"status": "SOLD", "strategy": "S", "signal_time": f"2025-01-{d:02d}", "net_pnl": base_pnl + (d % 3)}
        for d in range(1, 13)  # 12개 distinct 청산일
    ]


@pytest.mark.asyncio
async def test_run_ablation_attaches_pbo_cscv():
    """ablation 경로도 baseline+variant 후보로 formal PBO를 산출·부착한다."""
    baseline = BacktestPeriodRunResult(
        strategy_name="오닐PP/BGU", dates=["20250101"], journal_records=_dated_records(50)
    )

    async def fake_run_variant(variant: AblationVariant) -> BacktestPeriodRunResult:
        return BacktestPeriodRunResult(
            strategy_name="오닐PP/BGU", dates=["20250101"], journal_records=_dated_records(-10)
        )

    args = SimpleNamespace(
        ablation="oneil_pocket_pivot",
        ablation_variants="pp_only,bgu_only",
        initial_cash=1_000_000.0,
        pbo_cscv_splits=4,
        max_pbo_cscv=None,
    )

    await _run_ablation_for_result(baseline, args, run_variant_fn=fake_run_variant)

    pbo = baseline.ablation["pbo_cscv"]  # type: ignore[attr-defined]
    assert pbo["available"] is True
    assert pbo["n_configs"] == 3  # baseline + pp_only + bgu_only


@pytest.mark.asyncio
async def test_run_ablation_pbo_cscv_unavailable_without_dates():
    """signal_time 없는 레코드는 행렬에 못 올라가 PBO unavailable (하위호환)."""
    baseline = BacktestPeriodRunResult(
        strategy_name="S", dates=["20250101"],
        journal_records=[{"status": "SOLD", "net_pnl": 100}],
    )

    async def fake_run_variant(variant: AblationVariant) -> BacktestPeriodRunResult:
        return BacktestPeriodRunResult(
            strategy_name="S", dates=["20250101"],
            journal_records=[{"status": "SOLD", "net_pnl": 10}],
        )

    args = SimpleNamespace(
        ablation="oneil_pocket_pivot", ablation_variants="pp_only", initial_cash=1.0,
    )
    await _run_ablation_for_result(baseline, args, run_variant_fn=fake_run_variant)
    assert baseline.ablation["pbo_cscv"]["available"] is False  # type: ignore[attr-defined]
