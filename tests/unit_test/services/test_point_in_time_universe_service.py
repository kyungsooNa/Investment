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


def test_date_normalizer_handles_missing_compact_and_unparsable_values():
    from services.point_in_time_universe_service import _compact_date, _normalize_date

    assert _normalize_date(None) == ""
    assert _normalize_date(float("nan")) == ""
    assert _normalize_date("2026-05-01") == "2026-05-01"
    assert _normalize_date("20260501") == "2026-05-01"
    # 숫자만 8자리 이상 남으면 앞 8자리를 날짜로 본다(타임스탬프 문자열 대응).
    assert _normalize_date("20260501000000") == "2026-05-01"
    assert _normalize_date("봄철") == "봄철"

    assert _compact_date("2026-05-01") == "20260501"
    assert _compact_date("봄철") == ""


def test_text_normalizer_treats_missing_values_as_blank():
    from services.point_in_time_universe_service import _text

    assert _text(None) == ""
    assert _text(float("nan")) == ""
    assert _text("  005930  ") == "005930"


def test_symbol_normalizer_zero_fills_only_numeric_codes():
    from services.point_in_time_universe_service import _normalize_symbol

    assert _normalize_symbol("660") == "000660"
    assert _normalize_symbol("AAPL") == "AAPL"
    assert _normalize_symbol(None) == ""


def test_market_falls_back_to_the_market_id_map():
    from services.point_in_time_universe_service import _normalize_market

    assert _normalize_market({"market": "KOSPI"}) == "KOSPI"
    # market 컬럼이 없으면 MarketId 를 매핑한다.
    mapped = _normalize_market({"MarketId": "STK"})
    assert mapped
    # 매핑에 없는 값은 원본 그대로 남긴다.
    assert _normalize_market({"MarketId": "미지의시장"}) == "미지의시장"


def test_normalize_current_listings_handles_empty_frames_and_blank_symbols():
    from services.point_in_time_universe_service import normalize_current_listings

    assert normalize_current_listings(None) == []
    assert normalize_current_listings(pd.DataFrame()) == []

    df = pd.DataFrame([
        {"Symbol": "", "Name": "코드없음", "Market": "KOSPI"},
        {"Symbol": "005930", "Name": "삼성전자", "Market": "KOSPI"},
    ])
    records = normalize_current_listings(df)

    assert [r.symbol for r in records] == ["005930"]


def test_normalize_delisted_listings_skips_rows_without_symbols():
    from services.point_in_time_universe_service import normalize_delisted_listings

    records = normalize_delisted_listings([
        {"Name": "코드없음"},
        {"Symbol": "900100", "Name": "상폐기업", "Market": "KOSDAQ"},
    ])

    assert [r.symbol for r in records] == ["900100"]


def test_snapshot_rejects_an_unparsable_as_of_date():
    import pytest

    from services.point_in_time_universe_service import build_point_in_time_snapshot

    with pytest.raises(ValueError, match="as_of_date must be"):
        build_point_in_time_snapshot([], "봄철")


def test_snapshot_can_exclude_spac_shells():
    from services.point_in_time_universe_service import (
        PointInTimeUniverseRecord,
        build_point_in_time_snapshot,
    )

    records = [
        PointInTimeUniverseRecord(
            symbol="005930", name="삼성전자", market="KOSPI",
            listing_date="1975-06-11", delisting_date="", source="current",
        ),
        PointInTimeUniverseRecord(
            symbol="123456", name="엔에이치스팩25호", market="KOSDAQ",
            listing_date="2020-01-01", delisting_date="", source="current",
        ),
    ]

    kept = build_point_in_time_snapshot(records, "20260501", exclude_spac=True)

    assert [r.symbol for r in kept] == ["005930"]
