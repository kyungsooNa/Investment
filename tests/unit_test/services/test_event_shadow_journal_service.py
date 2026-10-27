"""EventShadowJournalService 단위 테스트 (P2 2-4 PR-2).

shadow 신호는 실 주문을 발생시키지 않고 별도 journal 파일에 기록되어, 폴링 경로와
event 경로의 신호 발생 시점/차이를 오프라인으로 비교하기 위한 데이터로 사용된다.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from services.event_shadow_journal_service import EventShadowJournalService


@pytest.fixture
def logger():
    return MagicMock()


def test_record_appends_to_buffer(tmp_path, logger):
    svc = EventShadowJournalService(log_root=tmp_path, logger=logger)
    svc.record(
        strategy_name="래리윌리엄스VBO",
        code="005930",
        signal={"action": "BUY", "price": 75000},
        snapshot={"price": "75000", "received_at": 12345.6},
    )

    records = svc.get_records()
    assert len(records) == 1
    rec = records[0]
    assert rec["strategy"] == "래리윌리엄스VBO"
    assert rec["code"] == "005930"
    assert rec["signal_source"] == "event_shadow"
    assert rec["signal"] == {"action": "BUY", "price": 75000}
    assert rec["snapshot"] == {"price": "75000", "received_at": 12345.6}
    assert "recorded_at" in rec


def test_record_default_signal_source_unchanged_when_none(tmp_path, logger):
    """P2 2-4 exit: signal_source 미지정 시 기존 entry 기본값 유지 (회귀 방어)."""
    svc = EventShadowJournalService(log_root=tmp_path, logger=logger)
    svc.record(strategy_name="VBO", code="005930", signal={"action": "BUY"}, snapshot={})
    assert svc.get_records()[0]["signal_source"] == "event_shadow"


def test_record_exit_signal_source_override(tmp_path, logger):
    """P2 2-4 exit: signal_source 를 지정하면 exit shadow 로 구분 기록한다."""
    svc = EventShadowJournalService(log_root=tmp_path, logger=logger)
    svc.record(
        strategy_name="래리윌리엄스VBO",
        code="005930",
        signal={"action": "SELL", "price": 68000, "reason": "손절(shadow)"},
        snapshot={"price": "68000"},
        signal_source="event_shadow_exit",
    )
    rec = svc.get_records()[0]
    assert rec["signal_source"] == "event_shadow_exit"
    assert rec["signal"]["action"] == "SELL"


def test_record_status_appends_diagnostic_record(tmp_path, logger):
    svc = EventShadowJournalService(log_root=tmp_path, logger=logger)
    svc.record_status(
        strategy_name="래리윌리엄스VBO",
        event="subscriptions_refreshed",
        details={"candidate_count": 2, "added_count": 1},
    )

    records = svc.get_records()
    assert len(records) == 1
    rec = records[0]
    assert rec["strategy"] == "래리윌리엄스VBO"
    assert rec["signal_source"] == "event_shadow_status"
    assert rec["event"] == "subscriptions_refreshed"
    assert rec["details"] == {"candidate_count": 2, "added_count": 1}
    assert "recorded_at" in rec


def test_flush_writes_jsonl_and_clears_buffer(tmp_path, logger):
    svc = EventShadowJournalService(log_root=tmp_path, logger=logger)
    svc.record(strategy_name="VBO", code="005930", signal={"a": 1}, snapshot={})
    svc.record(strategy_name="VBO", code="000660", signal={"a": 2}, snapshot={})

    path = svc.flush_to_file(date_str="20260518")

    assert path is not None
    assert path.exists()
    assert path.name == "20260518.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["code"] == "005930"
    # 버퍼 비워짐
    assert svc.get_records() == []


def test_flush_empty_buffer_returns_none(tmp_path, logger):
    svc = EventShadowJournalService(log_root=tmp_path, logger=logger)
    result = svc.flush_to_file(date_str="20260518")
    assert result is None


def test_flush_appends_to_existing_file(tmp_path, logger):
    svc = EventShadowJournalService(log_root=tmp_path, logger=logger)
    svc.record(strategy_name="VBO", code="005930", signal={"a": 1}, snapshot={})
    svc.flush_to_file(date_str="20260518")

    svc.record(strategy_name="VBO", code="000660", signal={"a": 2}, snapshot={})
    path = svc.flush_to_file(date_str="20260518")

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2  # 첫 flush 1줄 + 두 번째 flush 1줄


def test_flush_to_file_keeps_records_on_write_failure(tmp_path, logger):
    """flush 쓰기 실패 시 레코드가 유실되지 않고 버퍼에 보존된다."""
    svc = EventShadowJournalService(log_root=tmp_path, logger=logger)
    svc.record(strategy_name="VBO", code="005930", signal={"a": 1}, snapshot={})

    with patch.object(Path, "open", side_effect=OSError("disk full")):
        result = svc.flush_to_file(date_str="20260518")

    assert result is None
    assert len(svc.get_records()) == 1
    logger.warning.assert_called_once()


def test_record_ignores_missing_required_fields(tmp_path, logger):
    svc = EventShadowJournalService(log_root=tmp_path, logger=logger)
    svc.record(strategy_name="", code="005930", signal={}, snapshot={})
    svc.record(strategy_name="VBO", code="", signal={}, snapshot={})

    assert svc.get_records() == []


def test_standard_journal_reads_persisted_and_buffered_signals_only(tmp_path, logger):
    svc = EventShadowJournalService(log_root=tmp_path, logger=logger)
    svc.record(
        strategy_name="VBO",
        code="005930",
        signal={"action": "BUY", "price": 70000, "qty": 2, "reason": "진입 관찰"},
        snapshot={"price": "70000"},
    )
    svc.record(
        strategy_name="VBO",
        code="005930",
        signal={"action": "BUY", "price": 70100, "qty": 2, "reason": "같은 날 반복 관찰"},
        snapshot={"price": "70100"},
    )
    svc.record_status(strategy_name="VBO", event="subscriptions_refreshed")
    svc.flush_to_file(date_str="20261026")
    svc.record(
        strategy_name="VBO",
        code="005930",
        signal={"action": "SELL", "price": 71000, "qty": 2, "reason": "청산 관찰"},
        snapshot={"price": "71000"},
        signal_source="event_shadow_exit",
    )

    records = svc.get_standard_journal_records()

    assert len(records) == 2
    assert [record["source"] for record in records] == [
        "event_shadow",
        "event_shadow_exit",
    ]
    assert [record["side"] for record in records] == ["BUY", "SELL"]
    assert all(record["status"] == "SIGNAL" for record in records)
    assert all(record["net_pnl"] is None for record in records)
    assert records[0]["decision_reason"] == "진입 관찰"
