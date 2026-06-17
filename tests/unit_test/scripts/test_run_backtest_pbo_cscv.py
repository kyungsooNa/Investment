"""run_backtest 의 formal PBO(CSCV) 배선 회귀 테스트 (P1 1-7).

parameter-stability sweep 후보를 config로 보고 config별 per-period net_pnl 행렬을
조립 → compute_pbo_cscv. 여기서는 플래그 파싱 + 콘솔 렌더만 검증(알고리즘/행렬
빌더 자체는 test_multiple_testing_bias_service 로 잠겨 있음).
"""
from __future__ import annotations

from scripts.run_backtest import _format_pbo_cscv_console_lines, _parse_args


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
