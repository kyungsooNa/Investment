import json

import pandas as pd
import pytest

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


def test_full_records_payload_includes_all_current_and_delisted(monkeypatch):
    """--full-records 모드는 as-of 필터 없이 current+delisted 전체 레코드를 담는다."""
    monkeypatch.setattr(mod, "fetch_current_listing", lambda: _current_df())
    monkeypatch.setattr(mod, "fetch_fdr_delisting_listing", lambda: _delisted_df())

    payload = mod.build_full_records_payload(markets=("KOSPI", "KOSDAQ"))

    symbols = {r["symbol"] for r in payload["records"]}
    # as-of 스냅샷과 달리 008110(기간 전 상폐)·123456(미래 IPO)도 모두 포함
    assert symbols == {"005930", "123456", "230980", "008110"}
    assert payload["config"]["full_records"] is True
    assert payload["config"]["as_of_date"] is None
    assert "survivorship_gap" not in payload


def test_full_records_feed_provider_period_filtering(monkeypatch):
    """full-records 페이로드를 provider 에 먹이면 기간 내 임의 날짜 as-of 필터가 동작한다."""
    monkeypatch.setattr(mod, "fetch_current_listing", lambda: _current_df())
    monkeypatch.setattr(mod, "fetch_fdr_delisting_listing", lambda: _delisted_df())

    from services.point_in_time_universe_provider import PointInTimeUniverseProvider

    payload = mod.build_full_records_payload(markets=("KOSPI", "KOSDAQ"))
    provider = PointInTimeUniverseProvider.from_snapshot_payload(payload)

    # 008110 상폐 2026-03-30: 상폐 전엔 delisted 후보, 상폐 후엔 제외
    assert "008110" in provider.delisted_codes_as_of("20260301")
    assert "008110" not in provider.delisted_codes_as_of("20260401")
    # 230980 상폐 2026-06-05
    assert "230980" in provider.delisted_codes_as_of("20260604")
    assert "230980" not in provider.delisted_codes_as_of("20260606")


def test_main_full_records_writes_json_without_as_of_date(monkeypatch, tmp_path):
    """--full-records 는 --as-of-date 없이도 JSON 을 쓴다."""
    monkeypatch.setattr(mod, "fetch_current_listing", lambda: _current_df())
    monkeypatch.setattr(mod, "fetch_fdr_delisting_listing", lambda: _delisted_df())

    json_out = tmp_path / "full.json"
    exit_code = mod.main(["--full-records", "--output-json", str(json_out)])

    assert exit_code == 0
    payload = json.loads(json_out.read_text(encoding="utf-8"))
    assert payload["config"]["full_records"] is True
    assert len(payload["records"]) == 4


def test_main_requires_as_of_date_or_full_records(monkeypatch):
    monkeypatch.setattr(mod, "fetch_current_listing", lambda: _current_df())
    monkeypatch.setattr(mod, "fetch_fdr_delisting_listing", lambda: _delisted_df())
    with pytest.raises(SystemExit):
        mod.main([])


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
