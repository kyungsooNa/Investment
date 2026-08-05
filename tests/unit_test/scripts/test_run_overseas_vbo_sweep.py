"""해외 VBO 파라미터 스윕 CLI 테스트.

편향 수정(#769) 이후의 백테스트로 K·손절·괴리상한 그리드를 돌려, 판정 가능 건
집계와 비관·낙관 bracket 을 조합별로 비교한다. 실주문/라이브 경로와 무관한
오프라인 분석 도구.
"""
import json

import pytest

from scripts.run_overseas_vbo_sweep import (
    build_parser,
    ohlcv_coverage,
    parse_grid,
    run_sweep,
    format_markdown_report,
)


def _bar(d, o, h, l, c):
    return {"date": d, "open": o, "high": h, "low": l, "close": c, "volume": 1000}


def _write_ohlcv(tmp_path, code, bars):
    d = tmp_path / "ohlcv"
    d.mkdir(parents=True, exist_ok=True)
    with (d / f"{code}.jsonl").open("w", encoding="utf-8") as f:
        for b in bars:
            f.write(json.dumps(b) + "\n")
    return d


# ── 그리드 파싱 ──────────────────────────────────────────────────────────

def test_parse_grid_reads_float_list():
    assert parse_grid("0.3,0.5,0.7") == [0.3, 0.5, 0.7]


def test_parse_grid_maps_none_token_to_no_filter():
    """빈 토큰은 '필터 없음'(None) 을 뜻한다 — 상한 미적용 대조군."""
    assert parse_grid("none,1.5,2.5") == [None, 1.5, 2.5]


def test_parse_grid_ignores_blanks():
    assert parse_grid(" 0.5 , , 0.7 ") == [0.5, 0.7]


# ── 스윕 ─────────────────────────────────────────────────────────────────

def test_run_sweep_produces_row_per_combination(tmp_path):
    bars = [_bar("20260511", 100, 110, 100, 105), _bar("20260512", 100, 120, 104, 115)]
    ohlcv_dir = _write_ohlcv(tmp_path, "AAA", bars)

    rows = run_sweep(
        ohlcv_dir=ohlcv_dir,
        k_values=[0.3, 0.5],
        stop_values=[-3.0, -5.0],
        gap_caps=[None],
        cost_pct=0.0,
    )

    assert len(rows) == 4
    assert {(r["k"], r["stop_loss_pct"]) for r in rows} == {
        (0.3, -3.0), (0.3, -5.0), (0.5, -3.0), (0.5, -5.0)
    }


def test_run_sweep_reports_decidability_bracket(tmp_path):
    # 저 104 > 손절가 → 판정 가능(eod). 종가 115, 진입 105 → +9.52%
    bars = [_bar("20260511", 100, 110, 100, 105), _bar("20260512", 100, 120, 104, 115)]
    ohlcv_dir = _write_ohlcv(tmp_path, "AAA", bars)

    row = run_sweep(ohlcv_dir=ohlcv_dir, k_values=[0.5], stop_values=[-3.0],
                    gap_caps=[None], cost_pct=0.0)[0]

    assert row["trades"] == 1
    assert row["decided"] == 1
    assert row["undecided"] == 0
    assert row["avg_net_pessimistic"] == pytest.approx((115 / 105 - 1) * 100)
    assert row["avg_net_optimistic"] == pytest.approx((115 / 105 - 1) * 100)


def test_run_sweep_gap_cap_reduces_trade_count(tmp_path):
    # 전일 range 20 → target 110, 괴리 10% — 상한 2% 에서는 진입 없음
    bars = [_bar("20260511", 100, 120, 100, 110), _bar("20260512", 100, 130, 99, 128)]
    ohlcv_dir = _write_ohlcv(tmp_path, "AAA", bars)

    rows = run_sweep(ohlcv_dir=ohlcv_dir, k_values=[0.5], stop_values=[-3.0],
                     gap_caps=[None, 2.0], cost_pct=0.0)

    by_cap = {r["max_gap_to_target_pct"]: r for r in rows}
    assert by_cap[None]["trades"] == 1
    assert by_cap[2.0]["trades"] == 0


def test_run_sweep_aggregates_across_symbols(tmp_path):
    bars = [_bar("20260511", 100, 110, 100, 105), _bar("20260512", 100, 120, 104, 115)]
    _write_ohlcv(tmp_path, "AAA", bars)
    ohlcv_dir = _write_ohlcv(tmp_path, "BBB", bars)

    row = run_sweep(ohlcv_dir=ohlcv_dir, k_values=[0.5], stop_values=[-3.0],
                    gap_caps=[None], cost_pct=0.0)[0]

    assert row["trades"] == 2
    assert row["symbols"] == 2


def test_run_sweep_respects_date_range(tmp_path):
    bars = [
        _bar("20260511", 100, 110, 100, 105),
        _bar("20260512", 100, 120, 104, 115),
        _bar("20260513", 100, 120, 104, 115),
    ]
    ohlcv_dir = _write_ohlcv(tmp_path, "AAA", bars)

    row = run_sweep(ohlcv_dir=ohlcv_dir, k_values=[0.5], stop_values=[-3.0],
                    gap_caps=[None], cost_pct=0.0,
                    date_from="20260511", date_to="20260512")[0]

    assert row["trades"] == 1


def test_run_sweep_empty_dir_returns_zero_rows(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()

    rows = run_sweep(ohlcv_dir=empty, k_values=[0.5], stop_values=[-3.0],
                     gap_caps=[None], cost_pct=0.0)

    assert rows[0]["trades"] == 0
    assert rows[0]["symbols"] == 0


# ── 커버 구간 ────────────────────────────────────────────────────────────

def test_ohlcv_coverage_reports_actual_data_range(tmp_path):
    """기간 인자를 안 줘도 리포트가 '실제로 무슨 구간을 봤는지' 말할 수 있어야 한다."""
    bars = [_bar("20260311", 100, 110, 100, 105), _bar("20260512", 100, 120, 104, 115)]
    ohlcv_dir = _write_ohlcv(tmp_path, "AAA", bars)

    cov = ohlcv_coverage(ohlcv_dir, None, None)

    assert cov == {"first_date": "20260311", "last_date": "20260512", "symbols": 1}


def test_ohlcv_coverage_respects_date_bounds(tmp_path):
    bars = [_bar("20260311", 100, 110, 100, 105), _bar("20260512", 100, 120, 104, 115)]
    ohlcv_dir = _write_ohlcv(tmp_path, "AAA", bars)

    cov = ohlcv_coverage(ohlcv_dir, "20260401", None)

    assert cov["first_date"] == "20260512"


def test_ohlcv_coverage_empty_dir(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()

    assert ohlcv_coverage(empty, None, None) == {
        "first_date": None, "last_date": None, "symbols": 0
    }


# ── 리포트 ───────────────────────────────────────────────────────────────

def test_format_markdown_includes_bracket_columns(tmp_path):
    bars = [_bar("20260511", 100, 110, 100, 105), _bar("20260512", 100, 120, 104, 115)]
    ohlcv_dir = _write_ohlcv(tmp_path, "AAA", bars)
    rows = run_sweep(ohlcv_dir=ohlcv_dir, k_values=[0.5], stop_values=[-3.0],
                     gap_caps=[None], cost_pct=0.5)

    md = format_markdown_report(rows, config={"cost_pct": 0.5})

    assert "판정 불가" in md
    assert "avg_net_pessimistic" in md
    assert "avg_net_optimistic" in md


def test_parser_defaults():
    args = build_parser().parse_args(["--ohlcv-dir", "data/x"])

    assert args.k_values == "0.3,0.5,0.7"
    assert args.cost_pct == 0.5
