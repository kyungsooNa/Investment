"""EventShadowJournalService 단위 테스트 (P2 2-4 PR-2).

shadow 신호는 실 주문을 발생시키지 않고 별도 journal 파일에 기록되어, 폴링 경로와
event 경로의 신호 발생 시점/차이를 오프라인으로 비교하기 위한 데이터로 사용된다.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

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


def test_record_ignores_missing_required_fields(tmp_path, logger):
    svc = EventShadowJournalService(log_root=tmp_path, logger=logger)
    svc.record(strategy_name="", code="005930", signal={}, snapshot={})
    svc.record(strategy_name="VBO", code="", signal={}, snapshot={})

    assert svc.get_records() == []
