# tests/unit_test/scripts/test_run_inverse_etf_regime_backtest.py
"""인버스 ETF 레짐 백테스트 러너의 네트워크-비의존 순수 함수 테스트."""
import pandas as pd

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
