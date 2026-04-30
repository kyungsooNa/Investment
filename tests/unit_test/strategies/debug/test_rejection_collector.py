"""Unit tests for RejectionCollector and RejectionLogHandler."""
import logging
import warnings
from datetime import datetime

import pytest

from strategies.debug.rejection_collector import (
    RejectionCollector,
    RejectionEvent,
    RejectionLogHandler,
)


# ── RejectionLogHandler ──────────────────────────────────────────────

class TestRejectionLogHandler:
    def _make_record(self, msg, level=logging.INFO) -> logging.LogRecord:
        record = logging.LogRecord(
            name="strategy.OneilPocketPivot",
            level=level,
            pathname="",
            lineno=0,
            msg=msg,
            args=(),
            exc_info=None,
        )
        return record

    def test_captures_dict_event_in_captured_events(self):
        handler = RejectionLogHandler()
        handler.emit(self._make_record({"event": "pp_rejected", "code": "005930", "reason": "no_ma_proximity"}))
        assert len(handler.events) == 1
        assert handler.events[0].event == "pp_rejected"
        assert handler.events[0].code == "005930"
        assert handler.events[0].reason == "no_ma_proximity"

    def test_ignores_fstring_log(self):
        handler = RejectionLogHandler()
        handler.emit(self._make_record("Stage Guard: 3개 종목 필터링"))
        assert len(handler.events) == 0

    def test_ignores_unknown_event(self):
        handler = RejectionLogHandler()
        handler.emit(self._make_record({"event": "unknown_event", "code": "005930"}))
        assert len(handler.events) == 0

    def test_captures_debug_level_event(self):
        handler = RejectionLogHandler()
        handler.emit(self._make_record(
            {"event": "bgu_rejected", "code": "000660", "reason": "low_pg_ratio"},
            level=logging.DEBUG,
        ))
        assert len(handler.events) == 1
        assert handler.events[0].level == logging.DEBUG

    def test_captures_all_defined_events(self):
        handler = RejectionLogHandler()
        for event_name in RejectionLogHandler.CAPTURED_EVENTS:
            handler.emit(self._make_record({"event": event_name, "code": "000000"}))
        assert len(handler.events) == len(RejectionLogHandler.CAPTURED_EVENTS)

    def test_reason_defaults_to_event_when_absent(self):
        handler = RejectionLogHandler()
        handler.emit(self._make_record({"event": "scan_skipped"}))
        assert handler.events[0].reason == "scan_skipped"

    def test_details_contains_full_payload(self):
        payload = {"event": "entry_rejected", "code": "035720", "reason": "low_execution_strength", "cgld": 95, "threshold": 120}
        handler = RejectionLogHandler()
        handler.emit(self._make_record(payload))
        assert handler.events[0].details["cgld"] == 95

    def test_events_returns_copy(self):
        handler = RejectionLogHandler()
        handler.emit(self._make_record({"event": "scan_skipped"}))
        events1 = handler.events
        events2 = handler.events
        assert events1 is not events2


# ── RejectionCollector ───────────────────────────────────────────────

class TestRejectionCollector:
    def _make_logger(self, level=logging.INFO) -> logging.Logger:
        logger = logging.getLogger(f"test_collector_{id(self)}")
        logger.handlers.clear()
        logger.setLevel(level)
        return logger

    def test_captures_events_in_context(self):
        logger = self._make_logger(logging.INFO)
        with RejectionCollector(logger=logger) as col:
            logger.info({"event": "pp_rejected", "code": "005930", "reason": "no_ma_proximity"})
        assert len(col.events) == 1

    def test_handler_detached_after_exit(self):
        logger = self._make_logger()
        with RejectionCollector(logger=logger) as col:
            pass
        # 컨텍스트 종료 후 emit해도 events에 추가되면 안 됨
        logger.info({"event": "pp_rejected", "code": "005930", "reason": "x"})
        assert len(col.events) == 0

    def test_logger_level_restored_after_exit(self):
        logger = self._make_logger(logging.INFO)
        with RejectionCollector(logger=logger):
            assert logger.level == logging.DEBUG
        assert logger.level == logging.INFO

    def test_logger_level_restored_on_exception(self):
        logger = self._make_logger(logging.WARNING)
        try:
            with RejectionCollector(logger=logger):
                raise RuntimeError("test error")
        except RuntimeError:
            pass
        assert logger.level == logging.WARNING

    def test_captures_debug_event_when_logger_initially_info(self):
        # 운영 환경처럼 INFO로 설정된 logger에서도 debug 이벤트를 수집해야 함
        logger = self._make_logger(logging.INFO)
        with RejectionCollector(logger=logger) as col:
            logger.debug({"event": "bgu_rejected", "code": "000660", "reason": "low_pg_ratio"})
        assert len(col.events) == 1

    def test_strategy_logger_fallback(self):
        class FakeStrategy:
            _logger = None
        strategy = FakeStrategy()
        strategy._logger = self._make_logger()
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            with RejectionCollector(strategy=strategy) as col:
                strategy._logger.info({"event": "scan_skipped"})
            assert any("strategy._logger" in str(warning.message) for warning in w)
        assert len(col.events) == 1

    def test_raises_if_no_logger_provided(self):
        with pytest.raises(ValueError, match="requires logger="):
            with RejectionCollector():
                pass

    def test_by_code_groups_events(self):
        logger = self._make_logger()
        with RejectionCollector(logger=logger) as col:
            logger.info({"event": "pp_rejected", "code": "005930", "reason": "a"})
            logger.info({"event": "bgu_rejected", "code": "005930", "reason": "b"})
            logger.info({"event": "pp_rejected", "code": "000660", "reason": "c"})
        grouped = col.by_code()
        assert len(grouped["005930"]) == 2
        assert len(grouped["000660"]) == 1

    def test_no_duplicate_handlers_on_reuse(self):
        logger = self._make_logger()
        initial_count = len(logger.handlers)
        with RejectionCollector(logger=logger):
            assert len(logger.handlers) == initial_count + 1
        assert len(logger.handlers) == initial_count

    def test_events_empty_before_enter(self):
        logger = self._make_logger()
        col = RejectionCollector(logger=logger)
        assert col.events == []
