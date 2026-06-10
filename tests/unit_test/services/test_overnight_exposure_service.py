# tests/unit_test/services/test_overnight_exposure_service.py
"""전략별 오버나이트 노출 요약 순수 함수 검증 (R-4 후속).

- 현재 노출: status==HOLD 미청산 포지션 (리포트=장 마감 후이므로 익일 갭 노출).
- 실현 오버나이트: status==SOLD 이며 매수일!=매도일인 멀티세션 보유.
- 당일 청산(intraday) SOLD 는 오버나이트가 아니므로 제외한다.
"""
from __future__ import annotations

from services.overnight_exposure_service import compute_overnight_exposure_summary


def _hold(strategy: str, code: str, buy_date: str) -> dict:
    return {
        "strategy": strategy,
        "code": code,
        "status": "HOLD",
        "signal_time": buy_date,
        "net_return": None,
        "metadata": {"buy_date": buy_date, "sell_date": None},
    }


def _sold(strategy: str, code: str, buy_date: str, sell_date: str, net_return: float) -> dict:
    return {
        "strategy": strategy,
        "code": code,
        "status": "SOLD",
        "signal_time": buy_date,
        "net_return": net_return,
        "metadata": {"buy_date": buy_date, "sell_date": sell_date},
    }


def test_empty_records_returns_zero_totals():
    summary = compute_overnight_exposure_summary([])
    assert summary["open_holds"]["total"] == 0
    assert summary["open_holds"]["by_strategy"] == []
    assert summary["realized_overnight"]["total"] == 0
    assert summary["realized_overnight"]["by_strategy"] == []


def test_none_records_returns_zero_totals():
    summary = compute_overnight_exposure_summary(None)
    assert summary["open_holds"]["total"] == 0
    assert summary["realized_overnight"]["total"] == 0


def test_open_holds_counted_per_strategy_with_holding_age():
    records = [
        _hold("S1", "000001", "2026-06-05 10:00:00"),
        _hold("S1", "000002", "2026-06-09 13:00:00"),
        _hold("S2", "000003", "2026-06-08 09:30:00"),
    ]
    summary = compute_overnight_exposure_summary(records, today="2026-06-10")
    open_holds = summary["open_holds"]
    assert open_holds["total"] == 3

    by = {row["strategy"]: row for row in open_holds["by_strategy"]}
    assert by["S1"]["count"] == 2
    # 2026-06-05 -> 2026-06-10 = 5일, 2026-06-09 -> 1일.
    assert by["S1"]["max_holding_days"] == 5
    assert by["S1"]["avg_holding_days"] == 3.0
    assert by["S2"]["count"] == 1
    assert by["S2"]["max_holding_days"] == 2


def test_by_strategy_sorted_by_count_desc():
    records = [
        _hold("Low", "000001", "2026-06-09 10:00:00"),
        _hold("High", "000002", "2026-06-09 10:00:00"),
        _hold("High", "000003", "2026-06-09 10:00:00"),
    ]
    summary = compute_overnight_exposure_summary(records, today="2026-06-10")
    strategies = [row["strategy"] for row in summary["open_holds"]["by_strategy"]]
    assert strategies == ["High", "Low"]


def test_realized_overnight_excludes_intraday_sold():
    records = [
        # 멀티세션 보유 (오버나이트)
        _sold("S1", "000001", "2026-06-05 10:00:00", "2026-06-08 14:00:00", -3.0),
        # 당일 청산 (intraday) -> 제외
        _sold("S1", "000002", "2026-06-09 10:00:00", "2026-06-09 15:00:00", 1.5),
    ]
    summary = compute_overnight_exposure_summary(records, today="2026-06-10")
    realized = summary["realized_overnight"]
    assert realized["total"] == 1
    row = realized["by_strategy"][0]
    assert row["strategy"] == "S1"
    assert row["count"] == 1
    assert row["avg_holding_days"] == 3.0


def test_realized_overnight_worst_and_avg_net_return():
    records = [
        _sold("S1", "000001", "2026-06-01 10:00:00", "2026-06-03 14:00:00", -5.0),
        _sold("S1", "000002", "2026-06-02 10:00:00", "2026-06-05 14:00:00", 1.0),
    ]
    summary = compute_overnight_exposure_summary(records, today="2026-06-10")
    row = summary["realized_overnight"]["by_strategy"][0]
    assert row["count"] == 2
    assert row["worst_net_return"] == -5.0
    assert row["avg_net_return"] == -2.0


def test_hold_with_unparseable_date_still_counted():
    records = [_hold("S1", "000001", "")]
    summary = compute_overnight_exposure_summary(records, today="2026-06-10")
    assert summary["open_holds"]["total"] == 1
    row = summary["open_holds"]["by_strategy"][0]
    assert row["count"] == 1
    assert row["max_holding_days"] == 0
