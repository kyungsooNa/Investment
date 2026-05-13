"""RejectionDistributionService 단위 테스트."""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime

import pytest

from services.rejection_distribution_service import RejectionDistributionService


# ── record / get_distribution ─────────────────────────────────────────────


def test_record_accumulates_counts():
    svc = RejectionDistributionService()
    svc.record("StrategyA", "insufficient_volume", date="20260513")
    svc.record("StrategyA", "insufficient_volume", date="20260513")
    svc.record("StrategyA", "low_execution_strength", date="20260513")

    dist = svc.get_distribution("StrategyA", "20260513")
    assert dist["insufficient_volume"] == 2
    assert dist["low_execution_strength"] == 1


def test_record_different_strategies_are_isolated():
    svc = RejectionDistributionService()
    svc.record("StrategyA", "insufficient_volume", date="20260513")
    svc.record("StrategyB", "insufficient_volume", date="20260513")

    assert svc.get_distribution("StrategyA", "20260513") == {"insufficient_volume": 1}
    assert svc.get_distribution("StrategyB", "20260513") == {"insufficient_volume": 1}


def test_record_different_dates_are_isolated():
    svc = RejectionDistributionService()
    svc.record("StrategyA", "insufficient_volume", date="20260513")
    svc.record("StrategyA", "insufficient_volume", date="20260514")

    assert svc.get_distribution("StrategyA", "20260513") == {"insufficient_volume": 1}
    assert svc.get_distribution("StrategyA", "20260514") == {"insufficient_volume": 1}


def test_record_ignores_empty_strategy_name():
    svc = RejectionDistributionService()
    svc.record("", "insufficient_volume", date="20260513")
    assert svc.get_distribution("", "20260513") == {}


def test_record_ignores_empty_reason_code():
    svc = RejectionDistributionService()
    svc.record("StrategyA", "", date="20260513")
    assert svc.get_distribution("StrategyA", "20260513") == {}


def test_get_distribution_returns_empty_for_unknown():
    svc = RejectionDistributionService()
    assert svc.get_distribution("Unknown", "20260513") == {}


def test_get_all_strategies_returns_all():
    svc = RejectionDistributionService()
    svc.record("StrategyA", "insufficient_volume", date="20260513")
    svc.record("StrategyB", "low_execution_strength", date="20260513")

    result = svc.get_all_strategies("20260513")
    assert "StrategyA" in result
    assert "StrategyB" in result
    assert result["StrategyA"] == {"insufficient_volume": 1}


# ── flush_to_file ─────────────────────────────────────────────────────────


def test_flush_to_file_writes_jsonl(tmp_path):
    labels = {"insufficient_volume": "거래량 미달", "low_execution_strength": "체결강도 미달"}
    svc = RejectionDistributionService(reason_labels=labels)
    svc.record("StrategyA", "insufficient_volume", date="20260513")
    svc.record("StrategyA", "insufficient_volume", date="20260513")
    svc.record("StrategyB", "low_execution_strength", date="20260513")

    svc.flush_to_file("20260513", log_dir=str(tmp_path))

    path = os.path.join(str(tmp_path), "20260513.jsonl")
    assert os.path.exists(path)
    rows = [json.loads(line) for line in open(path, encoding="utf-8")]
    assert len(rows) == 2

    a_row = next(r for r in rows if r["strategy"] == "StrategyA")
    assert a_row["reason_code"] == "insufficient_volume"
    assert a_row["count"] == 2
    assert a_row["label_kr"] == "거래량 미달"
    assert a_row["date"] == "20260513"

    b_row = next(r for r in rows if r["strategy"] == "StrategyB")
    assert b_row["reason_code"] == "low_execution_strength"
    assert b_row["count"] == 1
    assert b_row["label_kr"] == "체결강도 미달"


def test_flush_to_file_uses_reason_code_as_fallback_label(tmp_path):
    svc = RejectionDistributionService()
    svc.record("StrategyA", "unknown_reason", date="20260513")
    svc.flush_to_file("20260513", log_dir=str(tmp_path))

    rows = [json.loads(line) for line in open(os.path.join(str(tmp_path), "20260513.jsonl"), encoding="utf-8")]
    assert rows[0]["label_kr"] == "unknown_reason"


def test_flush_to_file_skips_empty_date(tmp_path):
    svc = RejectionDistributionService()
    svc.flush_to_file("20260513", log_dir=str(tmp_path))
    assert not os.path.exists(os.path.join(str(tmp_path), "20260513.jsonl"))


def test_flush_to_file_creates_directory(tmp_path):
    svc = RejectionDistributionService()
    svc.record("StrategyA", "insufficient_volume", date="20260513")
    nested = str(tmp_path / "nested" / "dir")
    svc.flush_to_file("20260513", log_dir=nested)
    assert os.path.exists(os.path.join(nested, "20260513.jsonl"))


# ── attach_to_strategy_logger ─────────────────────────────────────────────


def test_attach_captures_rejection_events():
    svc = RejectionDistributionService()
    svc.attach_to_strategy_logger()
    today = datetime.now().strftime("%Y%m%d")

    logger = logging.getLogger("strategy.TestRejectionSvc")
    logger.setLevel(logging.DEBUG)
    logger.info({"event": "entry_rejected", "code": "005930", "reason": "low_execution_strength"})

    dist = svc.get_distribution("TestRejectionSvc", today)
    assert dist.get("low_execution_strength", 0) >= 1

    # 부착된 핸들러 정리
    strategy_logger = logging.getLogger("strategy")
    from services.rejection_distribution_service import _StrategyRejectionHandler
    for h in list(strategy_logger.handlers):
        if isinstance(h, _StrategyRejectionHandler) and h._service is svc:
            strategy_logger.removeHandler(h)


def test_attach_ignores_non_rejection_events():
    svc = RejectionDistributionService()
    svc.attach_to_strategy_logger()
    today = datetime.now().strftime("%Y%m%d")

    logger = logging.getLogger("strategy.TestNonRejection")
    logger.setLevel(logging.DEBUG)
    logger.info({"event": "buy_signal_generated", "code": "005930"})
    logger.info({"event": "scan_started", "strategy_name": "test"})

    dist = svc.get_distribution("TestNonRejection", today)
    assert dist == {}

    strategy_logger = logging.getLogger("strategy")
    from services.rejection_distribution_service import _StrategyRejectionHandler
    for h in list(strategy_logger.handlers):
        if isinstance(h, _StrategyRejectionHandler) and h._service is svc:
            strategy_logger.removeHandler(h)


def test_attach_idempotent():
    svc = RejectionDistributionService()
    svc.attach_to_strategy_logger()
    svc.attach_to_strategy_logger()

    strategy_logger = logging.getLogger("strategy")
    from services.rejection_distribution_service import _StrategyRejectionHandler
    handlers = [h for h in strategy_logger.handlers if isinstance(h, _StrategyRejectionHandler) and h._service is svc]
    assert len(handlers) == 1

    for h in handlers:
        strategy_logger.removeHandler(h)
