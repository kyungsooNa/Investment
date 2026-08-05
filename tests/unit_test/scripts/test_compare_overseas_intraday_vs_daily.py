"""장중 paper vs 일봉 근사 교차검증 CLI 테스트.

같은 거래일·종목에 대해 (1) 장중 폴링 진입/청산(overseas_paper) 과 (2) 마감 후 일봉
근사(overseas_dryrun) 를 대조해, 일봉 근사가 실행 대비 얼마나 낙관적인지 정량화한다.
"""
import json

import pytest

from scripts.compare_overseas_intraday_vs_daily import (
    build_parser,
    compare,
    format_markdown_report,
    load_records,
)


def _dryrun_rec(*, code, date, target=105.0, pessimistic=-3.0, optimistic=2.0, decidable=False):
    return {
        "recorded_at": 1.0,
        "strategy": "LarryWilliamsVBO_overseas",
        "code": code,
        "signal_source": "overseas_dryrun",
        "signal": {
            "code": code, "action": "BUY", "date": date,
            "entry_price": target, "target": target,
            "exit_decidable": decidable,
            "realized_pct": pessimistic,
            "realized_pct_pessimistic": pessimistic,
            "realized_pct_optimistic": optimistic,
        },
        "snapshot": {"exchange": "NASD"},
    }


def _paper_rec(*, code, date, side, limit_price, realized_pct=None, exit_reason=None):
    inner = {"strategy": "LarryWilliamsVBO_overseas_intraday", "code": code,
             "action": "BUY" if side == "buy" else "SELL", "date": date}
    if realized_pct is not None:
        inner["realized_pct"] = realized_pct
    if exit_reason:
        inner["exit_reason"] = exit_reason
    order = {"code": code, "side": side, "qty": 3,
             "limit_price": f"{limit_price:.4f}", "rt_cd": "0", "signal": inner}
    if exit_reason:
        order["exit_reason"] = exit_reason
    return {
        "recorded_at": 2.0,
        "strategy": "LarryWilliamsVBO_overseas",
        "code": code,
        "signal_source": "overseas_paper",
        "signal": order,
        "snapshot": {"exchange": "NASD"},
    }


def _write(tmp_path, date, records):
    d = tmp_path / "event_shadow"
    d.mkdir(parents=True, exist_ok=True)
    with (d / f"{date}.jsonl").open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return d


# ── 로드 ─────────────────────────────────────────────────────────────────

def test_load_splits_daily_and_intraday_sources(tmp_path):
    shadow = _write(tmp_path, "20260512", [
        _dryrun_rec(code="AAA", date="20260512"),
        _paper_rec(code="AAA", date="20260512", side="buy", limit_price=106.0),
        _paper_rec(code="AAA", date="20260512", side="sell", limit_price=110.0,
                   realized_pct=3.77, exit_reason="eod"),
    ])

    daily, intraday = load_records(shadow, "20260512", "20260512")

    assert list(daily) == [("20260512", "AAA")]
    assert intraday[("20260512", "AAA")]["entry_price"] == 106.0
    assert intraday[("20260512", "AAA")]["exit_reason"] == "eod"


def test_load_ignores_unclosed_intraday_position(tmp_path):
    """청산 기록이 없는 진입은 왕복이 성립하지 않아 미완결로 분리한다."""
    shadow = _write(tmp_path, "20260512", [
        _paper_rec(code="AAA", date="20260512", side="buy", limit_price=106.0),
    ])

    _, intraday = load_records(shadow, "20260512", "20260512")

    assert intraday[("20260512", "AAA")]["realized_pct"] is None


# ── 대조 ─────────────────────────────────────────────────────────────────

def test_compare_measures_entry_slippage_vs_daily_target(tmp_path):
    """장중 진입가는 목표가가 아니라 폴링가 — 그 차이가 일봉 근사의 낙관 편의다."""
    shadow = _write(tmp_path, "20260512", [
        _dryrun_rec(code="AAA", date="20260512", target=100.0),
        _paper_rec(code="AAA", date="20260512", side="buy", limit_price=102.0),
        _paper_rec(code="AAA", date="20260512", side="sell", limit_price=103.0,
                   realized_pct=0.98, exit_reason="eod"),
    ])

    report = compare(*load_records(shadow, "20260512", "20260512"), cost_pct=0.0)

    assert report["matched"] == 1
    assert report["avg_entry_slippage_pct"] == pytest.approx(2.0)


def test_compare_reports_realized_gap_against_optimistic(tmp_path):
    shadow = _write(tmp_path, "20260512", [
        _dryrun_rec(code="AAA", date="20260512", target=100.0, optimistic=3.0, pessimistic=-3.0),
        _paper_rec(code="AAA", date="20260512", side="buy", limit_price=100.0),
        _paper_rec(code="AAA", date="20260512", side="sell", limit_price=101.0,
                   realized_pct=1.0, exit_reason="eod"),
    ])

    report = compare(*load_records(shadow, "20260512", "20260512"), cost_pct=0.0)

    assert report["avg_intraday_net_pct"] == pytest.approx(1.0)
    assert report["avg_daily_optimistic_pct"] == pytest.approx(3.0)
    # 일봉 낙관이 장중 실현보다 2%p 높다 = 근사가 그만큼 낙관적
    assert report["optimistic_minus_intraday_pct"] == pytest.approx(2.0)


def test_compare_applies_round_trip_cost_to_intraday(tmp_path):
    shadow = _write(tmp_path, "20260512", [
        _dryrun_rec(code="AAA", date="20260512", target=100.0),
        _paper_rec(code="AAA", date="20260512", side="buy", limit_price=100.0),
        _paper_rec(code="AAA", date="20260512", side="sell", limit_price=101.0,
                   realized_pct=1.0, exit_reason="eod"),
    ])

    report = compare(*load_records(shadow, "20260512", "20260512"), cost_pct=0.5)

    assert report["avg_intraday_net_pct"] == pytest.approx(0.5)


def test_compare_counts_signal_coverage_both_ways(tmp_path):
    shadow = _write(tmp_path, "20260512", [
        # 일봉만 신호 (장중엔 미진입 — 폴링 간격에 걸러짐 등)
        _dryrun_rec(code="AAA", date="20260512"),
        # 장중만 진입 (일봉 근사로는 신호 없음)
        _paper_rec(code="BBB", date="20260512", side="buy", limit_price=50.0),
        _paper_rec(code="BBB", date="20260512", side="sell", limit_price=51.0,
                   realized_pct=2.0, exit_reason="eod"),
    ])

    report = compare(*load_records(shadow, "20260512", "20260512"), cost_pct=0.0)

    assert report["matched"] == 0
    assert report["daily_only"] == 1
    assert report["intraday_only"] == 1


def test_compare_empty_inputs():
    report = compare({}, {}, cost_pct=0.5)

    assert report["matched"] == 0
    assert report["avg_entry_slippage_pct"] is None


def test_format_markdown_smoke():
    md = format_markdown_report(
        {"matched": 1, "daily_only": 0, "intraday_only": 0, "unclosed": 0,
         "avg_entry_slippage_pct": 1.0, "avg_intraday_net_pct": 0.5,
         "avg_daily_optimistic_pct": 2.0, "avg_daily_pessimistic_pct": -1.0,
         "optimistic_minus_intraday_pct": 1.5, "by_exit_reason": {"eod": 1}},
        config={"cost_pct": 0.5},
    )

    assert "진입 슬리피지" in md
    assert "optimistic_minus_intraday" in md


def test_parser_defaults():
    args = build_parser().parse_args(["--date-from", "20260801", "--date-to", "20260810"])

    assert args.shadow_dir == "logs/strategies/event_shadow"
    assert args.cost_pct == 0.5
