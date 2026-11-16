# tests/unit_test/scripts/test_run_inverse_etf_regime_backtest.py
"""인버스 ETF 레짐 백테스트 러너의 네트워크-비의존 순수 함수 테스트."""
from unittest.mock import patch

import pandas as pd

from scripts import run_inverse_etf_regime_backtest as runner
from scripts.run_inverse_etf_regime_backtest import (
    normalize_fdr_ohlcv,
    BEAR_PERIODS,
    INDEX_ETF_CODE,
    INVERSE_ETF_CODE,
)


def test_normalize_fdr_ohlcv_from_indexed_frame():
    df = pd.DataFrame(
        {"Open": [100.0, 101.0], "High": [110.0, 111.0],
         "Low": [90.0, 91.0], "Close": [105.0, 106.0]},
        index=pd.to_datetime(["2022-01-03", "2022-01-04"]),
    )
    rows = normalize_fdr_ohlcv(df)
    assert len(rows) == 2
    assert rows[0] == {"date": "2022-01-03", "open": 100.0, "high": 110.0, "low": 90.0, "close": 105.0}


def test_normalize_fdr_ohlcv_empty():
    assert normalize_fdr_ohlcv(None) == []
    assert normalize_fdr_ohlcv(pd.DataFrame()) == []


def test_normalize_sorts_ascending():
    df = pd.DataFrame(
        {"Open": [1, 2], "High": [1, 2], "Low": [1, 2], "Close": [1, 2]},
        index=pd.to_datetime(["2022-02-01", "2022-01-01"]),
    )
    rows = normalize_fdr_ohlcv(df)
    assert [r["date"] for r in rows] == ["2022-01-01", "2022-02-01"]


def test_bear_periods_well_formed():
    assert len(BEAR_PERIODS) == 4
    for p in BEAR_PERIODS:
        assert p["start"] < p["end"]
        assert p["label"]
    assert INDEX_ETF_CODE == "069500"
    assert INVERSE_ETF_CODE == "114800"


def test_normalize_kis_investor_flows_converts_million_won_to_100m_won():
    rows = [
        {
            "stck_bsop_date": "20241108",
            "frgn_ntby_tr_pbmn": "-13079",
            "orgn_ntby_tr_pbmn": "117505",
        },
        {
            "stck_bsop_date": "invalid",
            "frgn_ntby_tr_pbmn": "1",
            "orgn_ntby_tr_pbmn": "2",
        },
    ]

    assert runner.normalize_kis_investor_flows(rows, "2024-11-01", "2024-11-30") == [
        {"date": "2024-11-08", "foreign": -130.79, "institution": 1175.05},
    ]


def test_normalize_kis_investor_flows_drops_missing_amounts_and_out_of_range_rows():
    rows = [
        {
            "stck_bsop_date": "20241031",
            "frgn_ntby_tr_pbmn": "10",
            "orgn_ntby_tr_pbmn": "20",
        },
        {
            "stck_bsop_date": "20241108",
            "frgn_ntby_tr_pbmn": "",
            "orgn_ntby_tr_pbmn": "20",
        },
    ]

    assert runner.normalize_kis_investor_flows(rows, "2024-11-01", "2024-11-30") == []


def test_run_passes_confirmation_datasets_to_backtest():
    bars = [{"date": "2024-11-08", "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5}]
    flows = [{"date": "2024-11-08", "foreign": -1.0, "institution": -2.0}]
    expected = {
        "periods": [],
        "overall": {"total_trades": 0},
        "confirmation_coverage": {"candidate_dates": 0},
    }

    with (
        patch.object(runner, "_fetch", return_value=bars) as fetch,
        patch.object(runner, "_fetch_kis_investor_flows_sync", return_value=flows),
        patch.object(runner, "InverseEtfRegimeBacktest") as backtest_cls,
    ):
        backtest_cls.return_value.run_periods.return_value = expected
        result = runner.run(output="none")

    assert result == expected
    assert fetch.call_count == 4
    backtest_cls.return_value.run_periods.assert_called_once_with(
        bars,
        bars,
        runner.BEAR_PERIODS,
        investor_flows=flows,
        semiconductor_bars={"005930": bars, "000660": bars},
    )
