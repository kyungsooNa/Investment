import json

import pandas as pd

from scripts import backfill_delisted_ohlcv as mod


def _delisted_df():
    return pd.DataFrame([
        {
            "Symbol": "230980",
            "Name": "비유테크놀러지",
            "Market": "KOSDAQ",
            "SecuGroup": "주권",
            "Kind": "보통주",
            "ListingDate": "2016-03-02",
            "DelistingDate": "2026-06-05",
            "Reason": "감사의견 거절",
        },
        {
            "Symbol": "451700",
            "Name": "엔에이치스팩29호",
            "Market": "KOSDAQ",
            "SecuGroup": "주권",
            "Kind": "보통주",
            "ListingDate": "2023-06-23",
            "DelistingDate": "2026-06-10",
            "Reason": "피흡수합병(스팩소멸합병)",
        },
    ])


def test_normalize_ohlcv_frame_handles_date_index():
    df = pd.DataFrame(
        [
            {"Open": 1000, "High": 1200, "Low": 900, "Close": 1100, "Volume": 10000},
            {"Open": 1100, "High": 1300, "Low": 1000, "Close": 1250, "Volume": 12000},
        ],
        index=pd.to_datetime(["2026-06-03", "2026-06-04"]),
    )

    rows = mod.normalize_ohlcv_frame("230980", df)

    assert rows == [
        {
            "date": "2026-06-03",
            "symbol": "230980",
            "open": 1000,
            "high": 1200,
            "low": 900,
            "close": 1100,
            "volume": 10000,
            "change": None,
        },
        {
            "date": "2026-06-04",
            "symbol": "230980",
            "open": 1100,
            "high": 1300,
            "low": 1000,
            "close": 1250,
            "volume": 12000,
            "change": None,
        },
    ]


def test_main_writes_per_symbol_csv_and_index(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(mod, "fetch_fdr_delisting_listing", lambda: _delisted_df())

    def fake_fetch(symbol, start, end):
        assert symbol == "230980"
        assert start == "2026-06-01"
        assert end == "2026-06-05"
        return pd.DataFrame(
            [{"Open": 1000, "High": 1200, "Low": 900, "Close": 1100, "Volume": 10000}],
            index=pd.to_datetime(["2026-06-04"]),
        )

    monkeypatch.setattr(mod, "fetch_delisted_ohlcv", fake_fetch)
    output_dir = tmp_path / "ohlcv"
    exit_code = mod.main([
        "--date-from", "20260601",
        "--date-to", "20260611",
        "--exclude-spac",
        "--output-dir", str(output_dir),
    ])

    assert exit_code == 0
    csv_path = output_dir / "230980.csv"
    assert "2026-06-04" in csv_path.read_text(encoding="utf-8-sig")
    index_payload = json.loads((output_dir / "index.json").read_text(encoding="utf-8"))
    assert index_payload["totals"]["symbols"] == 1
    assert index_payload["records"][0]["symbol"] == "230980"
    assert str(output_dir) in capsys.readouterr().out
