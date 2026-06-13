"""PointInTimeUniverseProvider — 백테스트 날짜별 상장 종목 질의 (R-1 생존편향 연결층).

백테스트가 "그 날짜에 상장돼 있던 종목(상폐 포함)"을 후보로 쓰도록 하는 질의 계층.
순수 로직이라 synthetic 레코드로 완전 검증한다.
"""
from __future__ import annotations

from services.point_in_time_universe_provider import PointInTimeUniverseProvider
from services.point_in_time_universe_service import PointInTimeUniverseRecord


def _records():
    return [
        # 현재 상장 (상폐일 없음)
        {"symbol": "005930", "name": "삼성전자", "market": "KOSPI",
         "listing_date": "1975-06-11", "delisting_date": "", "source": "current"},
        # 백테스트 기간 이후 상장 (lookahead 방지 대상)
        {"symbol": "123456", "name": "미래상장", "market": "KOSDAQ",
         "listing_date": "2026-06-10", "delisting_date": "", "source": "current"},
        # 기간 중 상폐된 종목
        {"symbol": "900100", "name": "상폐기업", "market": "KOSDAQ",
         "listing_date": "2020-01-01", "delisting_date": "2026-03-15", "source": "delisted",
         "reason": "감사의견거절"},
    ]


def test_listed_codes_includes_current_and_delisted_within_window():
    p = PointInTimeUniverseProvider.from_records_dicts(_records())
    # 2026-02-01: 삼성전자 + 상폐기업(아직 상장 중), 미래상장 제외
    listed = p.listed_codes_as_of("20260201")
    assert listed == {"005930", "900100"}


def test_excludes_stock_before_listing_date():
    p = PointInTimeUniverseProvider.from_records_dicts(_records())
    # 2026-06-09: 미래상장(06-10) 아직 미상장
    assert "123456" not in p.listed_codes_as_of("20260609")
    assert "123456" in p.listed_codes_as_of("20260610")


def test_delisted_excluded_on_and_after_delisting_date():
    p = PointInTimeUniverseProvider.from_records_dicts(_records())
    # 상폐일 직전까지 포함, 상폐일 당일부터 제외
    assert "900100" in p.listed_codes_as_of("20260314")
    assert "900100" not in p.listed_codes_as_of("20260315")
    assert "900100" not in p.listed_codes_as_of("20260401")


def test_delisted_codes_as_of_returns_only_delisted_source():
    p = PointInTimeUniverseProvider.from_records_dicts(_records())
    assert p.delisted_codes_as_of("20260201") == {"900100"}
    # 상폐일 이후엔 없음
    assert p.delisted_codes_as_of("20260401") == set()


def test_from_snapshot_payload_reads_records_key():
    payload = {"config": {"as_of_date": "2026-02-01"}, "records": _records()}
    p = PointInTimeUniverseProvider.from_snapshot_payload(payload)
    assert "900100" in p.listed_codes_as_of("20260201")


def test_accepts_dataclass_records_and_normalizes_symbol():
    rec = PointInTimeUniverseRecord(
        symbol="5930", name="삼성전자", market="KOSPI", listing_date="1975-06-11",
    )
    p = PointInTimeUniverseProvider([rec])
    # 6자리 zfill 정규화
    assert "005930" in p.listed_codes_as_of("20260201")


def test_record_for_and_all_codes():
    p = PointInTimeUniverseProvider.from_records_dicts(_records())
    assert p.all_codes() == {"005930", "123456", "900100"}
    assert p.record_for("900100").source == "delisted"
    assert p.record_for("999999") is None


def test_accepts_yyyy_mm_dd_query_format():
    p = PointInTimeUniverseProvider.from_records_dicts(_records())
    assert p.listed_codes_as_of("2026-03-14") == p.listed_codes_as_of("20260314")
