import math

from common import trade_journal_comparison as tjc


def test_trade_date_digits_and_empty():
    # digits >= 8 -> formatted date
    assert tjc._trade_date({"signal_time": "20230501010203"}) == "2023-05-01"

    # digits < 8 -> empty string
    assert tjc._trade_date({"signal_time": "abc123"}) == ""


def test_to_float_none_and_invalid():
    assert tjc._to_float(None) is None
    assert tjc._to_float("") is None
    assert tjc._to_float("not-a-number") is None
    # object() causes TypeError inside float()
    assert tjc._to_float(object()) is None


def test_diff_and_pct_diff_none_cases():
    assert tjc._diff(None, 1) is None
    assert tjc._diff(5, None) is None

    assert tjc._pct_diff(None, 1) is None
    assert tjc._pct_diff(5, 0) is None
    assert tjc._pct_diff(5, None) is None


def test_avg_and_round_or_none():
    assert tjc._avg([]) is None

    vals = [1.23456, 1.23444]
    # average is exactly 1.2345 -> rounded to 4 digits
    assert math.isclose(tjc._avg(vals), 1.2345)

    assert tjc._round_or_none(None) is None
    assert tjc._round_or_none(1.234567, digits=3) == round(1.234567, 3)


def test_compare_trade_journals_unmatched_summary_none_total():
    backtest = [
        {
            "strategy": "S1",
            "code": "AAA",
            "signal_time": "2023-05-01T09:00:00",
            "net_return": "1.2",
            "net_pnl": "10",
            "fill_price": "100",
        }
    ]
    live = []

    res = tjc.compare_trade_journals(backtest, live)
    summary = res["summary"]
    assert summary["backtest_count"] == 1
    assert summary["live_count"] == 0
    assert summary["matched_count"] == 0
    assert summary["unmatched_backtest_count"] == 1
    assert summary["unmatched_live_count"] == 0
    # no matched net pnl diffs -> total_net_pnl_diff should be None
    assert summary["total_net_pnl_diff"] is None
