import json

import pandas as pd

from scripts import build_point_in_time_universe as mod


def _current_df():
    return pd.DataFrame([
        {
            "Code": "005930",
            "Name": "삼성전자",
            "Market": "KOSPI",
            "ListingDate": "1975-06-11",
        },
        {
            "Code": "123456",
            "Name": "미래상장",
            "Market": "KOSDAQ",
            "ListingDate": "2026-06-10",
        },
    ])


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
            "Symbol": "008110",
            "Name": "대동전자",
            "Market": "KOSPI",
            "SecuGroup": "주권",
            "Kind": "보통주",
            "ListingDate": "1990-06-05",
            "DelistingDate": "2026-03-30",
            "Reason": "감사의견 한정",
        },
    ])


def test_main_writes_point_in_time_snapshot_and_gap_report(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(mod, "fetch_current_listing", lambda: _current_df())
    monkeypatch.setattr(mod, "fetch_fdr_delisting_listing", lambda: _delisted_df())

    json_out = tmp_path / "snapshot.json"
    csv_out = tmp_path / "snapshot.csv"
    md_out = tmp_path / "report.md"
    exit_code = mod.main([
        "--as-of-date", "20260604",
        "--output-json", str(json_out),
        "--output-csv", str(csv_out),
        "--output-markdown", str(md_out),
    ])

    assert exit_code == 0
    payload = json.loads(json_out.read_text(encoding="utf-8"))
    assert [r["symbol"] for r in payload["records"]] == ["005930", "230980"]
    assert payload["survivorship_gap"]["delisted_only_count"] == 1
    assert payload["survivorship_gap"]["current_only_count"] == 1
    assert "230980" in csv_out.read_text(encoding="utf-8")
    assert "Survivorship Gap" in md_out.read_text(encoding="utf-8")
    assert str(json_out) in capsys.readouterr().out
