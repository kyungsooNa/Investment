import sqlite3
import time
from unittest.mock import MagicMock, patch

import pytest

from repositories.execution_strength_repo import ExecutionStrengthRepository


@pytest.fixture
def repo(tmp_path):
    instance = ExecutionStrengthRepository(
        base_dir=str(tmp_path / "es_repo"), logger=MagicMock()
    )
    yield instance
    instance.close()


def _rows(db_path):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(
            "SELECT code, trade_date, trade_time, strength FROM es_history ORDER BY id"
        ).fetchall()
    finally:
        conn.close()


def test_init_creates_db_and_table(repo):
    tables = {
        row[0]
        for row in sqlite3.connect(repo._db_path).execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert "es_history" in tables


def test_record_tick_persists_after_flush(repo):
    now = time.time()
    assert repo.record_tick("005930", "123.45", "091001", "20260704", now=now) is True

    repo.flush()

    assert _rows(repo._db_path) == [("005930", "20260704", "091001", 123.45)]


def test_record_tick_samples_per_code(repo):
    base = time.time()
    assert repo.record_tick("005930", "100.0", "090001", "20260704", now=base) is True
    # 같은 종목 60초 이내 → skip
    assert repo.record_tick("005930", "101.0", "090031", "20260704", now=base + 30) is False
    # 다른 종목은 독립 샘플링
    assert repo.record_tick("000660", "90.0", "090031", "20260704", now=base + 30) is True
    # 같은 종목 60초 경과 → 기록
    assert repo.record_tick("005930", "102.0", "090101", "20260704", now=base + 61) is True

    repo.flush()

    assert [(r[0], r[3]) for r in _rows(repo._db_path)] == [
        ("005930", 100.0),
        ("000660", 90.0),
        ("005930", 102.0),
    ]


def test_record_tick_skips_invalid_values(repo):
    now = time.time()
    assert repo.record_tick("", "100.0", "090001", "20260704", now=now) is False
    assert repo.record_tick("005930", "N/A", "090001", "20260704", now=now) is False
    assert repo.record_tick("005930", None, "090001", "20260704", now=now) is False
    assert repo.record_tick("005930", "100.0", "9:01", "20260704", now=now) is False
    assert repo.record_tick("005930", "100.0", None, "20260704", now=now) is False

    repo.flush()

    assert _rows(repo._db_path) == []


def test_record_tick_falls_back_to_local_date(repo):
    now = time.time()
    assert repo.record_tick("005930", "110.5", "091001", None, now=now) is True

    repo.flush()

    expected_date = time.strftime("%Y%m%d", time.localtime(now))
    assert _rows(repo._db_path) == [("005930", expected_date, "091001", 110.5)]


def test_buffer_flushes_on_size_threshold(repo):
    now = time.time()
    for i in range(ExecutionStrengthRepository.FLUSH_BUFFER_SIZE):
        assert repo.record_tick(f"{i:06d}", "100.0", "090001", "20260704", now=now) is True

    # 명시적 flush 없이 버퍼 임계 도달로 이미 저장됨
    assert len(_rows(repo._db_path)) == ExecutionStrengthRepository.FLUSH_BUFFER_SIZE


def test_flush_interval_triggers_flush(repo):
    now = time.time() + ExecutionStrengthRepository.FLUSH_INTERVAL_SEC + 1
    assert repo.record_tick("005930", "100.0", "090001", "20260704", now=now) is True

    # 마지막 flush 이후 FLUSH_INTERVAL_SEC 경과 → 즉시 flush
    assert len(_rows(repo._db_path)) == 1


def test_retention_cleanup_on_init(tmp_path):
    base_dir = str(tmp_path / "es_repo")
    first = ExecutionStrengthRepository(base_dir=base_dir, logger=MagicMock())
    old_ts = time.time() - (ExecutionStrengthRepository.RETENTION_DAYS + 10) * 86400
    with first._conn:
        first._conn.execute(
            "INSERT INTO es_history (code, trade_date, trade_time, strength, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            ("005930", "20260501", "090001", 100.0, old_ts),
        )
        first._conn.execute(
            "INSERT INTO es_history (code, trade_date, trade_time, strength, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            ("005930", "20260704", "090001", 101.0, time.time()),
        )
    first.close()

    second = ExecutionStrengthRepository(base_dir=base_dir, logger=MagicMock())
    second.close()

    assert _rows(second._db_path) == [("005930", "20260704", "090001", 101.0)]


def test_init_db_failure_logs_error_and_record_returns_false(tmp_path):
    logger = MagicMock()

    with patch(
        "repositories.execution_strength_repo.sqlite3.connect",
        side_effect=sqlite3.Error("boom"),
    ):
        repo = ExecutionStrengthRepository(base_dir=str(tmp_path / "es_repo"), logger=logger)

    logger.error.assert_called_once()
    assert repo.record_tick("005930", "100.0", "090001", "20260704") is False
    repo.flush()  # no-op이어야 하며 예외가 나지 않는다
    repo.close()
