import json

import pandas as pd

from scripts import audit_delisting_universe as mod


def _fdr_df():
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
        {
            "Symbol": "257990",
            "Name": "나우코스",
            "Market": "KONEX",
            "SecuGroup": "주권",
            "Kind": "보통주",
            "ListingDate": "2020-12-30",
            "DelistingDate": "2026-06-01",
            "Reason": "타법인의 완전자회사로 편입",
        },
        {
            "Symbol": "1477601G",
            "Name": "피엠티 5R",
            "Market": "KOSDAQ",
            "SecuGroup": "신주인수권증서",
            "Kind": "보통주",
            "ListingDate": "2026-06-04",
            "DelistingDate": "2026-06-11",
            "Reason": "",
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


def test_normalize_fdr_delistings_filters_backtest_universe():
    records = mod.normalize_fdr_delistings(
        _fdr_df(),
        date_from="20260301",
        date_to="20260611",
        markets=("KOSPI", "KOSDAQ"),
        secu_groups=("주권",),
    )

    assert [r["symbol"] for r in records] == ["451700", "230980", "008110"]
    assert {r["market"] for r in records} == {"KOSPI", "KOSDAQ"}
    assert {r["secu_group"] for r in records} == {"주권"}


def test_normalize_fdr_delistings_can_exclude_spacs():
    records = mod.normalize_fdr_delistings(
        _fdr_df(),
        date_from="20260301",
        date_to="20260611",
        exclude_spac=True,
    )

    assert [r["symbol"] for r in records] == ["230980", "008110"]


def test_parse_kind_delisting_rows_from_html_table():
    html = """
    <table>
      <tr><td>2</td><td>비유테크놀러지</td><td>2026-06-05</td><td>감사의견 거절</td><td></td></tr>
      <tr><td>1</td><td>대동전자</td><td>2026-03-30</td><td>감사의견 한정</td><td>비고</td></tr>
    </table>
    """

    rows = mod.parse_kind_delisting_rows(html)

    assert rows == [
        {
            "name": "비유테크놀러지",
            "delisting_date": "2026-06-05",
            "reason": "감사의견 거절",
            "note": "",
        },
        {
            "name": "대동전자",
            "delisting_date": "2026-03-30",
            "reason": "감사의견 한정",
            "note": "비고",
        },
    ]


def test_compare_with_kind_uses_name_and_delisting_date():
    fdr_records = mod.normalize_fdr_delistings(_fdr_df(), date_from="20260301", date_to="20260611")
    kind_rows = [
        {"name": "비유테크놀러지", "delisting_date": "2026-06-05", "reason": "감사의견 거절", "note": ""},
        {"name": "대동전자", "delisting_date": "2026-03-30", "reason": "감사의견 한정", "note": ""},
        {"name": "KIND에만있는종목", "delisting_date": "2026-04-01", "reason": "x", "note": ""},
    ]

    comparison = mod.compare_with_kind(fdr_records, kind_rows)

    assert comparison["matched_count"] == 2
    assert [r["symbol"] for r in comparison["fdr_only"]] == ["451700"]
    assert comparison["kind_only"] == [
        {"name": "KIND에만있는종목", "delisting_date": "2026-04-01", "reason": "x", "note": ""}
    ]


def test_kind_market_type_arg_defaults_to_requested_markets():
    assert mod.kind_market_type_arg(("KOSPI", "KOSDAQ"), "") == "1,2"
    assert mod.kind_market_type_arg(("KONEX",), "") == "6"
    assert mod.kind_market_type_arg(("KOSPI",), "1,2,6") == "1,2,6"


def test_build_report_counts_and_records():
    fdr_records = mod.normalize_fdr_delistings(_fdr_df(), date_from="20260301", date_to="20260611")
    report = mod.build_report(
        fdr_records,
        date_from="20260301",
        date_to="20260611",
        kind_rows=None,
    )

    assert report["totals"]["fdr_filtered"] == 3
    assert report["market_counts"] == {"KOSDAQ": 2, "KOSPI": 1}
    assert report["secu_group_counts"] == {"주권": 3}
    assert report["records"][0]["symbol"] == "451700"


def test_main_writes_json_csv_and_markdown(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(mod, "fetch_fdr_delisting_listing", lambda: _fdr_df())
    monkeypatch.setattr(
        mod,
        "fetch_kind_delisting_rows",
        lambda date_from, date_to, market_type="": [
            {"name": "비유테크놀러지", "delisting_date": "2026-06-05", "reason": "감사의견 거절", "note": ""},
            {"name": "대동전자", "delisting_date": "2026-03-30", "reason": "감사의견 한정", "note": ""},
        ],
    )

    json_out = tmp_path / "report.json"
    csv_out = tmp_path / "snapshot.csv"
    md_out = tmp_path / "report.md"
    exit_code = mod.main([
        "--date-from", "20260301",
        "--date-to", "20260611",
        "--kind-check",
        "--output-json", str(json_out),
        "--output-csv", str(csv_out),
        "--output-markdown", str(md_out),
    ])

    assert exit_code == 0
    report = json.loads(json_out.read_text(encoding="utf-8"))
    assert report["totals"]["fdr_filtered"] == 3
    assert report["kind_comparison"]["matched_count"] == 2
    assert "451700" in csv_out.read_text(encoding="utf-8")
    assert "Delisting Universe Audit" in md_out.read_text(encoding="utf-8")
    assert str(json_out) in capsys.readouterr().out
