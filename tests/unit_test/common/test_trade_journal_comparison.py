from common.trade_journal_comparison import compare_trade_journals


def _record(
    *,
    source,
    strategy="S1",
    code="005930",
    signal_time="2026-05-05 09:10:00",
    net_return=1.0,
    net_pnl=1000.0,
    fill_price=10100.0,
):
    return {
        "schema_version": 1,
        "source": source,
        "strategy": strategy,
        "code": code,
        "signal_time": signal_time,
        "decision_reason": "exit",
        "rejected_reason": "",
        "side": "ROUND_TRIP",
        "order_price": 10000.0,
        "fill_price": fill_price,
        "qty": 1,
        "status": "SOLD",
        "cost": 30.0,
        "gross_pnl": 1000.0,
        "net_pnl": net_pnl,
        "gross_return": 10.0,
        "net_return": net_return,
        "mfe": None,
        "mae": None,
        "metadata": {},
    }


def test_compare_trade_journals_matches_by_strategy_code_and_date():
    backtest = [_record(source="backtest", net_return=2.5, net_pnl=2500, fill_price=10500)]
    live = [_record(source="virtual_trade", net_return=1.0, net_pnl=1000, fill_price=10300)]

    report = compare_trade_journals(backtest, live)

    assert report["summary"] == {
        "backtest_count": 1,
        "live_count": 1,
        "matched_count": 1,
        "unmatched_backtest_count": 0,
        "unmatched_live_count": 0,
        "avg_net_return_diff": -1.5,
        "avg_abs_net_return_diff": 1.5,
        "avg_fill_price_diff_pct": -1.9048,
        "total_net_pnl_diff": -1500.0,
    }
    row = report["matches"][0]
    assert row["strategy"] == "S1"
    assert row["code"] == "005930"
    assert row["trade_date"] == "2026-05-05"
    assert row["backtest_net_return"] == 2.5
    assert row["live_net_return"] == 1.0
    assert row["net_return_diff"] == -1.5
    assert row["fill_price_diff_pct"] == -1.9048
    assert row["net_pnl_diff"] == -1500.0


def test_compare_trade_journals_keeps_unmatched_records():
    backtest = [
        _record(source="backtest", code="005930"),
        _record(source="backtest", code="000660"),
    ]
    live = [
        _record(source="virtual_trade", code="005930"),
        _record(source="virtual_trade", code="035420"),
    ]

    report = compare_trade_journals(backtest, live)

    assert report["summary"]["matched_count"] == 1
    assert report["summary"]["unmatched_backtest_count"] == 1
    assert report["summary"]["unmatched_live_count"] == 1
    assert report["unmatched_backtest"][0]["code"] == "000660"
    assert report["unmatched_live"][0]["code"] == "035420"


def test_compare_trade_journals_pairs_multiple_records_for_same_key_in_time_order():
    backtest = [
        _record(source="backtest", signal_time="2026-05-05 09:10:00", net_return=1.0),
        _record(source="backtest", signal_time="2026-05-05 13:10:00", net_return=3.0),
    ]
    live = [
        _record(source="virtual_trade", signal_time="2026-05-05 13:20:00", net_return=2.5),
        _record(source="virtual_trade", signal_time="2026-05-05 09:20:00", net_return=0.5),
    ]

    report = compare_trade_journals(backtest, live)

    assert [row["net_return_diff"] for row in report["matches"]] == [-0.5, -0.5]
    assert [row["backtest_signal_time"] for row in report["matches"]] == [
        "2026-05-05 09:10:00",
        "2026-05-05 13:10:00",
    ]
