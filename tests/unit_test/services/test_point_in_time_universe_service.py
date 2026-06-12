import pandas as pd

from services.point_in_time_universe_service import (
    PointInTimeUniverseRecord,
    build_point_in_time_snapshot,
    compare_current_to_point_in_time,
    normalize_current_listings,
    normalize_delisted_listings,
)


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


def _delisted_records():
    return [
        {
            "symbol": "230980",
            "name": "비유테크놀러지",
            "market": "KOSDAQ",
            "secu_group": "주권",
            "listing_date": "2016-03-02",
            "delisting_date": "2026-06-05",
            "reason": "감사의견 거절",
        },
        {
            "symbol": "008110",
            "name": "대동전자",
            "market": "KOSPI",
            "secu_group": "주권",
            "listing_date": "1990-06-05",
            "delisting_date": "2026-03-30",
            "reason": "감사의견 한정",
        },
        {
            "symbol": "451700",
            "name": "엔에이치스팩29호",
            "market": "KOSDAQ",
            "secu_group": "주권",
            "listing_date": "2023-06-23",
            "delisting_date": "2026-06-10",
            "reason": "피흡수합병(스팩소멸합병)",
        },
    ]


def test_snapshot_includes_delisted_stock_until_day_before_delisting():
    records = normalize_current_listings(_current_df()) + normalize_delisted_listings(
        _delisted_records()
    )

    before = build_point_in_time_snapshot(records, "20260604", exclude_spac=True)
    on_delisting_day = build_point_in_time_snapshot(records, "20260605", exclude_spac=True)

    assert [r.symbol for r in before] == ["005930", "230980"]
    assert [r.symbol for r in on_delisting_day] == ["005930"]


def test_snapshot_excludes_future_listings_to_avoid_ipo_lookahead():
    records = normalize_current_listings(_current_df()) + normalize_delisted_listings(
        _delisted_records()
    )

    snapshot = build_point_in_time_snapshot(records, "20260604", exclude_spac=True)

    assert "123456" not in {r.symbol for r in snapshot}


def test_compare_current_to_point_in_time_surfaces_survivorship_gap():
    current = normalize_current_listings(_current_df())
    pit = build_point_in_time_snapshot(
        current + normalize_delisted_listings(_delisted_records()),
        "20260604",
        exclude_spac=True,
    )

    summary = compare_current_to_point_in_time(current, pit)

    assert summary["current_count"] == 2
    assert summary["point_in_time_count"] == 2
    assert summary["delisted_only_count"] == 1
    assert summary["current_only_count"] == 1
    assert summary["delisted_only"][0]["symbol"] == "230980"
    assert summary["current_only"][0]["symbol"] == "123456"


def test_snapshot_deduplicates_by_symbol_preferring_delisted_history():
    records = [
        PointInTimeUniverseRecord(
            symbol="230980",
            name="현재목록오염",
            market="KOSDAQ",
            listing_date="",
            delisting_date="",
            source="current",
        ),
        PointInTimeUniverseRecord(
            symbol="230980",
            name="비유테크놀러지",
            market="KOSDAQ",
            listing_date="2016-03-02",
            delisting_date="2026-06-05",
            source="delisted",
            reason="감사의견 거절",
        ),
    ]

    snapshot = build_point_in_time_snapshot(records, "20260604")

    assert len(snapshot) == 1
    assert snapshot[0].name == "비유테크놀러지"
    assert snapshot[0].source == "delisted"
