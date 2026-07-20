from datetime import datetime
import sqlite3

import pytest

from repositories.dart_disclosure_repository import DartDisclosureRepository
from services.dart_disclosure_client import DartDisclosure
from services.dart_disclosure_rule_service import DisclosureImportance


@pytest.fixture
def disclosure():
    return DartDisclosure(
        corp_class="Y",
        corp_name="삼성전자",
        corp_code="00126380",
        stock_code="005930",
        report_name="전환사채권발행결정",
        receipt_no="20260714001234",
        filer_name="삼성전자",
        receipt_date="20260714",
        remarks="유",
    )


@pytest.fixture
def importance():
    return DisclosureImportance(score=85, level="HIGH", reasons=["전환사채 발행 관련 공시"])


async def test_save_detected_is_idempotent(tmp_path, disclosure, importance):
    repo = DartDisclosureRepository(tmp_path / "dart.db")

    assert await repo.save_detected(disclosure, importance) is True
    assert await repo.save_detected(disclosure, importance) is False
    assert await repo.has_receipt(disclosure.receipt_no) is True


async def test_get_known_receipt_nos_checks_page_in_one_query(tmp_path, disclosure, importance):
    repo = DartDisclosureRepository(tmp_path / "dart.db")
    await repo.save_detected(disclosure, importance)

    known = await repo.get_known_receipt_nos(
        [disclosure.receipt_no, "20260714009999"]
    )

    assert known == {disclosure.receipt_no}


async def test_pending_immediate_excludes_suppressed_and_sent(tmp_path, disclosure, importance):
    repo = DartDisclosureRepository(tmp_path / "dart.db")
    await repo.save_detected(disclosure, importance, suppress_immediate=True)
    assert await repo.get_pending_immediate(70) == []

    second = disclosure.__class__(**{**disclosure.__dict__, "receipt_no": "20260714001235"})
    await repo.save_detected(second, importance)
    pending = await repo.get_pending_immediate(70)
    assert [item.disclosure.receipt_no for item in pending] == ["20260714001235"]

    await repo.mark_immediate_sent(second.receipt_no, datetime(2026, 7, 14, 12, 0, 0))
    assert await repo.get_pending_immediate(70) == []


async def test_initialization_state_persists(tmp_path):
    path = tmp_path / "dart.db"
    repo = DartDisclosureRepository(path)
    assert await repo.is_initialized() is False

    await repo.mark_initialized()

    assert await DartDisclosureRepository(path).is_initialized() is True


async def test_digest_rows_are_marked_after_send(tmp_path, disclosure, importance):
    repo = DartDisclosureRepository(tmp_path / "dart.db")
    normal_importance = DisclosureImportance(score=30, level="NORMAL", reasons=["정기보고서"])
    await repo.save_detected(disclosure, normal_importance)

    pending = await repo.get_pending_digest("20260714", immediate_threshold=70)
    assert len(pending) == 1

    await repo.mark_digest_sent([disclosure.receipt_no], datetime(2026, 7, 14, 19, 40, 0))
    assert await repo.get_pending_digest("20260714", immediate_threshold=70) == []


async def test_get_recent_by_stock_code_filters_and_orders(tmp_path, disclosure, importance):
    repo = DartDisclosureRepository(tmp_path / "dart.db")
    older = disclosure.__class__(
        **{
            **disclosure.__dict__,
            "receipt_no": "20260713000001",
            "receipt_date": "20260713",
            "report_name": "사업보고서",
        }
    )
    other_stock = disclosure.__class__(
        **{
            **disclosure.__dict__,
            "receipt_no": "20260715000001",
            "receipt_date": "20260715",
            "stock_code": "000660",
        }
    )
    await repo.save_detected(older, importance)
    await repo.save_detected(disclosure, importance)
    await repo.save_detected(other_stock, importance)

    rows = await repo.get_recent_by_stock_code("005930", limit=1)

    assert len(rows) == 1
    assert rows[0].disclosure.receipt_no == disclosure.receipt_no


async def test_event_key_round_trips_and_legacy_schema_is_migrated(
    tmp_path, disclosure, importance
):
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE disclosures (
                rcept_no TEXT PRIMARY KEY, corp_code TEXT NOT NULL,
                stock_code TEXT NOT NULL, corp_name TEXT NOT NULL,
                report_name TEXT NOT NULL, filer_name TEXT NOT NULL,
                receipt_date TEXT NOT NULL, remarks TEXT NOT NULL,
                importance_score INTEGER NOT NULL, importance_level TEXT NOT NULL,
                importance_reasons TEXT NOT NULL, detected_at TEXT NOT NULL,
                alert_suppressed INTEGER NOT NULL DEFAULT 0,
                immediate_sent_at TEXT, digest_sent_at TEXT,
                send_retry_count INTEGER NOT NULL DEFAULT 0
            )
            """
        )

    repo = DartDisclosureRepository(path)
    await repo.save_detected(
        disclosure,
        importance,
        event_key="ELS|37980,37981|20000000000",
    )

    rows = await repo.get_recent_by_stock_code(disclosure.stock_code)
    assert rows[0].event_key == "ELS|37980,37981|20000000000"
