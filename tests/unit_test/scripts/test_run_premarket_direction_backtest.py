"""장전 코스피/코스닥 방향 예측 백테스트 단위 테스트.

핵심 불변식: 한국 거래일 D 의 특징값은 **D 장 시작 전에 알 수 있는 정보만** 쓴다.
미국 세션 날짜 T 의 종가는 KST 로 T+1 새벽에 확정되므로 T < D 인 세션만 쓴다.
"""
from __future__ import annotations

import json

import pytest

from scripts.run_premarket_direction_backtest import (
    analyze_operational,
    bucket_hit_rates,
    evaluate_model,
    historical_rows_before,
    sign_rule_hit_rate,
    walk_forward_logit,
)
from services.premarket_direction_service import (
    FORECAST_FEATURES,
    MU,
    FeatureSpec,
    asof_return,
    build_dataset,
    parse_naver_fchart,
    parse_yahoo_chart,
)


def _kr(d, o, c):
    return {"date": d, "open": o, "high": max(o, c), "low": min(o, c), "close": c}


def _us(d, c):
    return {"date": d, "close": c}


# ── 파서 ─────────────────────────────────────────────────────────────

def test_parse_naver_fchart_reads_ohlc_rows_in_order():
    xml = (
        '<?xml version="1.0" encoding="EUC-KR" ?><protocol><chartdata symbol="KOSPI">'
        '<item data="20261117|5008.09|5084.06|4957.0|4968.48|193978" />'
        '<item data="20261118|5098.82|5133.14|4943.17|5067.44|219000" />'
        "</chartdata></protocol>"
    )
    rows = parse_naver_fchart(xml)
    assert rows == [
        {"date": "2026-11-17", "open": 5008.09, "high": 5084.06, "low": 4957.0, "close": 4968.48},
        {"date": "2026-11-18", "open": 5098.82, "high": 5133.14, "low": 4943.17, "close": 5067.44},
    ]


def test_parse_yahoo_chart_uses_exchange_local_date_and_skips_null_close():
    # 2026-11-17 09:30 America/New_York (gmtoffset -18000) = 14:30 UTC
    ts1 = 1794925800
    payload = {"chart": {"result": [{
        "meta": {"gmtoffset": -18000},
        "timestamp": [ts1, ts1 + 86400, ts1 + 2 * 86400],
        "indicators": {"quote": [{"close": [100.0, None, 102.0]}]},
    }]}}
    rows = parse_yahoo_chart(json.dumps(payload))
    assert rows == [_us("2026-11-17", 100.0), _us("2026-11-19", 102.0)]


# ── as-of 수익률 ─────────────────────────────────────────────────────

def test_asof_return_uses_only_sessions_strictly_before_target_date():
    series = [_us("2026-11-16", 100.0), _us("2026-11-17", 110.0), _us("2026-11-18", 999.0)]
    # 한국 D=11-18, 직전 한국거래일 11-17 → 미국 11-17 세션(종가 11-18 새벽 확정)만 반영.
    # 미국 11-18 세션은 한국 11-18 장중/이후에 열리므로 쓰면 미래참조.
    assert asof_return(series, prev_date="2026-11-17", date="2026-11-18") == pytest.approx(0.10)


def test_asof_return_compounds_us_sessions_across_korean_holiday():
    series = [_us("2026-11-13", 100.0), _us("2026-11-16", 105.0), _us("2026-11-17", 110.25)]
    # 한국 11-16·17 휴장 가정: 직전 한국거래일 11-13 → D=11-18.
    # 기준가 = 11-13 미만 마지막 미국 종가. 없으면 계산 불가(None).
    assert asof_return(series, prev_date="2026-11-13", date="2026-11-18") is None
    series = [_us("2026-11-12", 100.0)] + series
    assert asof_return(series, prev_date="2026-11-13", date="2026-11-18") == pytest.approx(0.1025)


def test_asof_return_is_zero_when_no_new_us_session():
    series = [_us("2026-11-16", 100.0), _us("2026-11-17", 110.0)]
    # prev_date 이후 새 미국 세션이 없으면(미국 휴장) 변화 없음 = 0.0
    assert asof_return(series, prev_date="2026-11-18", date="2026-11-18") == pytest.approx(0.0)


# ── 데이터셋 ─────────────────────────────────────────────────────────

def test_build_dataset_labels_and_features():
    kr = [_kr("2026-11-16", 100, 100), _kr("2026-11-17", 101, 99), _kr("2026-11-18", 98, 103)]
    us = {"sox": [_us("2026-11-13", 50.0), _us("2026-11-16", 55.0), _us("2026-11-17", 44.0)]}
    rows = build_dataset(kr, us)
    assert [r["date"] for r in rows] == ["2026-11-17", "2026-11-18"]

    d17, d18 = rows
    assert d17["sox"] == pytest.approx(0.10)       # 11-16 세션 50→55
    assert d17["up_close"] == 0                     # 99 < 100 (B)
    assert d17["up_body"] == 0                      # 99 < 101 (C)
    assert d17["up_gap"] == 1                       # 101 > 100 (A)
    assert d17["kr_prev_ret"] is None              # 전전일 없음

    assert d18["sox"] == pytest.approx(-0.20)      # 11-17 세션 55→44
    assert d18["up_close"] == 1                     # 103 > 99
    assert d18["up_body"] == 1                      # 103 > 98
    assert d18["up_gap"] == 0                       # 98 < 99
    assert d18["kr_prev_ret"] == pytest.approx(-0.01)


# ── 평가 ─────────────────────────────────────────────────────────────

def test_sign_rule_hit_rate_respects_expected_sign_and_skips_missing():
    rows = [
        {"x": 0.01, "y": 1}, {"x": -0.02, "y": 0}, {"x": 0.03, "y": 0},
        {"x": None, "y": 1}, {"x": 0.0, "y": 1},
    ]
    res = sign_rule_hit_rate(rows, FeatureSpec("x", "X", +1), "y")
    assert res == {"n": 3, "hit_rate": pytest.approx(2 / 3)}     # x==0 은 무신호로 제외
    inv = sign_rule_hit_rate(rows, FeatureSpec("x", "X", -1), "y")
    assert inv["hit_rate"] == pytest.approx(1 / 3)


def test_bucket_hit_rates_splits_by_absolute_move():
    rows = [{"x": 0.005, "y": 1}, {"x": -0.025, "y": 0}, {"x": 0.03, "y": 1}, {"x": -0.004, "y": 1}]
    res = bucket_hit_rates(rows, FeatureSpec("x", "X", +1), "y", thresholds=(0.0, 0.01))
    assert res[0] == {"min_abs": 0.0, "n": 4, "hit_rate": pytest.approx(3 / 4)}
    assert res[1] == {"min_abs": 0.01, "n": 2, "hit_rate": pytest.approx(1.0)}


def test_walk_forward_logit_never_trains_on_future_and_learns_simple_rule():
    rows = []
    for i in range(400):
        x = ((i * 37) % 100 - 50) / 1000.0
        rows.append({"date": f"d{i:04d}", "x": x, "y": 1 if x > 0 else 0})
    preds = walk_forward_logit(rows, ["x"], "y", min_train=100, refit_every=50)
    assert all(p is None for p in preds[:100])                # 학습 구간은 예측 없음
    scored = [(p, r["y"]) for p, r in zip(preds, rows) if p is not None]
    assert len(scored) == 300
    acc = sum((p > 0.5) == bool(y) for p, y in scored) / len(scored)
    assert acc > 0.95


def test_operational_backtest_uses_live_features_and_scores_both_targets():
    rows = [{"date": f"d{i:04d}", "sox": (i % 7 - 3) / 100,
             "mu": (i % 5 - 2) / 100, "up_close": int(i % 7 > 3),
             "up_body": int(i % 5 > 2)} for i in range(40)]
    base = analyze_operational(rows, FORECAST_FEATURES[:1], min_train=10)
    hynix = analyze_operational(rows, FORECAST_FEATURES[:1] + (MU,), min_train=10)
    assert base["features"] == ["sox"]
    assert hynix["features"] == ["sox", "mu"]
    assert set(base["model"]) == {"up_close", "up_body"}
    assert base["model"]["up_close"]["n"] == 30


def test_probability_report_includes_brier_and_observed_frequency():
    rows = [{"date": f"d{i:04d}", "x": (i % 9 - 4) / 100,
             "up_close": int(i % 9 > 4)} for i in range(45)]
    result = evaluate_model(rows, ["x"], "up_close", min_train=10, refit_every=1)
    assert 0 <= result["brier_score"] <= 1
    assert 0 <= result["baseline_brier_score"] <= 1
    assert sum(bucket["n"] for bucket in result["calibration"]) == result["n"]
    for bucket in result["calibration"]:
        if bucket["n"]:
            assert 0 <= bucket["mean_predicted"] <= 1
            assert 0 <= bucket["observed_frequency"] <= 1


def test_backtest_excludes_unfinished_current_session():
    rows = [_kr("2026-11-24", 100, 101), _kr("2026-11-25", 102, 99)]
    assert historical_rows_before(rows, "2026-11-25") == rows[:1]
