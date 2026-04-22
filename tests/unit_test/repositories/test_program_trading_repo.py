import asyncio
import os
import sqlite3
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from repositories.program_trading_repo import ProgramTradingRepo


@pytest.fixture
def repo(tmp_path):
    logger = MagicMock()
    instance = ProgramTradingRepo(base_dir=str(tmp_path / "pt_repo"), logger=logger)
    yield instance
    if instance._conn:
        instance._conn.close()
        instance._conn = None
    instance._executor.shutdown(wait=False)


def test_init_creates_db_and_tables(repo):
    assert os.path.exists(repo._db_path)

    conn = sqlite3.connect(repo._db_path)
    try:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    finally:
        conn.close()

    assert "pt_history" in tables
    assert "pt_snapshot" in tables


def test_init_db_logs_error_when_connect_fails(tmp_path):
    logger = MagicMock()

    with patch("repositories.program_trading_repo.sqlite3.connect", side_effect=sqlite3.Error("boom")):
        ProgramTradingRepo(base_dir=str(tmp_path / "pt_repo"), logger=logger)

    logger.error.assert_called_once()


def test_safe_int_and_safe_float_handle_invalid_values():
    assert ProgramTradingRepo._safe_int("12") == 12
    assert ProgramTradingRepo._safe_int(None) == 0
    assert ProgramTradingRepo._safe_int("bad") == 0

    assert ProgramTradingRepo._safe_float("1.25") == 1.25
    assert ProgramTradingRepo._safe_float(None) == 0.0
    assert ProgramTradingRepo._safe_float("bad") == 0.0


def test_add_record_to_buffer_and_flush_sync_persists_formatted_history(repo):
    created_at = time.time()
    repo.add_record_to_buffer(
        {
            "유가증권단축종목코드": "005930",
            "주식체결시간": "152000",
            "price": "70100",
            "rate": "1.23",
            "매도체결량": "10",
            "매도거래대금": "1000",
            "매수2체결량": "11",
            "매수2거래대금": "1100",
            "순매수체결량": "1",
            "순매수거래대금": "100",
            "매도호가잔량": "20",
            "매수호가잔량": "21",
            "전체순매수호가잔량": "1",
        },
        created_at,
    )

    repo.flush_write_buffer_sync()
    loaded = repo.load_today_history()

    assert repo._write_buffer == []
    assert loaded["005930"][0]["주식체결시간"] == "152000"
    assert loaded["005930"][0]["price"] == 70100
    assert loaded["005930"][0]["rate"] == 1.23
    assert loaded["005930"][0]["매도체결량"] == "10"


def test_load_today_history_returns_empty_dict_for_no_rows(repo):
    assert repo.load_today_history() == {}


def test_load_today_history_logs_error_and_returns_empty_dict(repo):
    with patch.object(repo, "_get_connection", side_effect=sqlite3.Error("load fail")):
        assert repo.load_today_history() == {}

    repo._logger.error.assert_called_once()


def test_bulk_insert_to_db_rolls_back_and_logs_on_failure(repo):
    fake_conn = MagicMock()
    fake_conn.executemany.side_effect = sqlite3.Error("insert fail")
    repo._conn = fake_conn

    repo._bulk_insert_to_db([("005930", "", 0, 0.0, 0, 0, 0, 0, 0, 0, 0, 0, 0, time.time())])

    fake_conn.rollback.assert_called_once()
    repo._logger.error.assert_called_once()


def test_bulk_insert_to_db_ignores_rollback_failure(repo):
    fake_conn = MagicMock()
    fake_conn.executemany.side_effect = sqlite3.Error("insert fail")
    fake_conn.rollback.side_effect = sqlite3.Error("rollback fail")
    repo._conn = fake_conn

    repo._bulk_insert_to_db([("005930", "", 0, 0.0, 0, 0, 0, 0, 0, 0, 0, 0, 0, time.time())])

    repo._logger.error.assert_called_once()


@pytest.mark.asyncio
async def test_flush_write_buffer_async_persists_rows(repo):
    repo.add_record_to_buffer({"유가증권단축종목코드": "000660"}, time.time())

    await repo._flush_write_buffer()

    loaded = repo.load_today_history()
    assert "000660" in loaded
    assert repo._write_buffer == []


@pytest.mark.asyncio
async def test_flush_write_buffer_async_noop_when_buffer_empty(repo):
    with patch.object(repo, "_bulk_insert_to_db") as mock_bulk:
        await repo._flush_write_buffer()

    mock_bulk.assert_not_called()


def test_flush_write_buffer_sync_noop_when_buffer_empty(repo):
    with patch.object(repo, "_bulk_insert_to_db") as mock_bulk:
        repo.flush_write_buffer_sync()

    mock_bulk.assert_not_called()


@pytest.mark.asyncio
async def test_flush_loop_flushes_remaining_rows_on_cancel(repo):
    with patch.object(repo, "_flush_write_buffer", new=AsyncMock()) as mock_flush:
        with patch("repositories.program_trading_repo.asyncio.sleep", new=AsyncMock(side_effect=asyncio.CancelledError)):
            await repo._flush_loop()

    mock_flush.assert_awaited_once()


def test_save_and_load_snapshot(repo):
    assert repo.save_snapshot({"005930": {"net": 100}}) is True

    loaded = repo.load_snapshot()

    assert loaded == {"005930": {"net": 100}}
    repo._logger.info.assert_called()


def test_save_snapshot_logs_and_reraises_on_json_error(repo):
    with pytest.raises(TypeError):
        repo.save_snapshot({"bad": {1, 2, 3}})

    repo._logger.error.assert_called_once()


def test_load_snapshot_returns_none_when_missing(repo):
    assert repo.load_snapshot() is None


def test_load_snapshot_logs_error_and_returns_none_for_invalid_json(repo):
    with repo._get_connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO pt_snapshot (key, value, updated_at) VALUES (?, ?, ?)",
            ("pt_data", "{bad json", time.time()),
        )

    assert repo.load_snapshot() is None
    repo._logger.error.assert_called_once()


def test_cleanup_old_data_deletes_only_expired_rows(repo):
    now = time.time()
    old_ts = now - (repo.RETENTION_DAYS * 86400) - 10

    with repo._get_connection() as conn:
        conn.executemany(
            """
            INSERT INTO pt_history (
                code, trade_time, price, rate, sell_vol, sell_amt, buy_vol, buy_amt,
                net_vol, net_amt, sell_rem, buy_rem, net_rem, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ("005930", "090000", 0, 0.0, 0, 0, 0, 0, 0, 0, 0, 0, 0, old_ts),
                ("000660", "090001", 0, 0.0, 0, 0, 0, 0, 0, 0, 0, 0, 0, now),
            ],
        )

    repo._cleanup_old_data()

    with repo._get_connection() as conn:
        rows = conn.execute("SELECT code FROM pt_history ORDER BY code").fetchall()

    assert rows == [("000660",)]


def test_cleanup_old_data_skips_optimize_when_nothing_deleted(repo):
    now = time.time()

    with repo._get_connection() as conn:
        conn.execute(
            """
            INSERT INTO pt_history (
                code, trade_time, price, rate, sell_vol, sell_amt, buy_vol, buy_amt,
                net_vol, net_amt, sell_rem, buy_rem, net_rem, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("000660", "090001", 0, 0.0, 0, 0, 0, 0, 0, 0, 0, 0, 0, now),
        )

    with patch.object(repo, "_logger") as mock_logger:
        repo._cleanup_old_data()

    mock_logger.info.assert_not_called()


def test_cleanup_old_data_logs_error_on_failure(repo):
    with patch.object(repo, "_get_connection", side_effect=sqlite3.Error("cleanup fail")):
        repo._cleanup_old_data()

    repo._logger.error.assert_called_once()


def test_cleanup_old_files_removes_legacy_files(repo):
    jsonl_path = os.path.join(repo._base_dir, "history.jsonl")
    json_path = os.path.join(repo._base_dir, "pt_data.json")
    keep_path = os.path.join(repo._base_dir, "keep.txt")

    for path in (jsonl_path, json_path, keep_path):
        with open(path, "w", encoding="utf-8") as fp:
            fp.write("x")

    repo._cleanup_old_files()

    assert not os.path.exists(jsonl_path)
    assert not os.path.exists(json_path)
    assert os.path.exists(keep_path)


def test_cleanup_old_files_logs_errors(repo):
    with patch("repositories.program_trading_repo.os.listdir", side_effect=OSError("fail")):
        repo._cleanup_old_files()

    repo._logger.error.assert_called_once()


def test_inspect_db_status_returns_snapshot_history_and_hourly_counts(repo):
    now = time.time()
    repo.save_snapshot({"ok": True})
    repo.add_record_to_buffer({"유가증권단축종목코드": "005930"}, now)
    repo.flush_write_buffer_sync()

    status = repo.inspect_db_status()

    assert status["snapshot"]["exists"] is True
    assert status["snapshot"]["updated_at"] is not None
    assert status["history"]["count"] == 1
    assert status["history"]["last_record"] is not None
    assert len(status["history"]["hourly_counts"]) == 1


def test_inspect_db_status_empty_defaults(repo):
    status = repo.inspect_db_status()

    assert status["snapshot"]["exists"] is False
    assert status["snapshot"]["updated_at"] is None
    assert status["history"]["count"] == 0
    assert status["history"]["last_record"] is None
    assert status["history"]["hourly_counts"] == {}


def test_inspect_db_status_sets_error_on_failure(repo):
    with patch.object(repo, "_get_connection", side_effect=sqlite3.Error("inspect fail")):
        status = repo.inspect_db_status()

    assert status["error"] == "inspect fail"
    repo._logger.error.assert_called_once()


@pytest.mark.asyncio
async def test_start_flush_loop_runs_cleanup_and_creates_task(repo):
    fake_task = MagicMock()

    with patch.object(repo, "_cleanup_old_data") as mock_cleanup_data, \
         patch.object(repo, "_cleanup_old_files") as mock_cleanup_files, \
         patch("repositories.program_trading_repo.asyncio.create_task", return_value=fake_task) as mock_create_task:
        repo.start_flush_loop()

    mock_cleanup_data.assert_called_once()
    mock_cleanup_files.assert_called_once()
    mock_create_task.assert_called_once()
    assert repo._flush_task is fake_task


@pytest.mark.asyncio
async def test_shutdown_cancels_task_flushes_buffer_and_closes_resources(repo):
    class CancellingTask:
        def __init__(self):
            self.cancel_called = False

        def done(self):
            return False

        def cancel(self):
            self.cancel_called = True

        def __await__(self):
            async def _raise_cancelled():
                raise asyncio.CancelledError()

            return _raise_cancelled().__await__()

    fake_task = CancellingTask()
    fake_conn = MagicMock()
    if repo._conn:
        repo._conn.close()
    repo._conn = fake_conn
    repo._flush_task = fake_task

    with patch.object(repo, "flush_write_buffer_sync") as mock_flush, \
         patch.object(repo._executor, "shutdown") as mock_shutdown:
        await repo.shutdown()

    assert fake_task.cancel_called is True
    mock_flush.assert_called_once()
    mock_shutdown.assert_called_once_with(wait=False)
    fake_conn.close.assert_called_once()
    assert repo._conn is None
    repo._logger.info.assert_called()


@pytest.mark.asyncio
async def test_shutdown_handles_already_completed_task(repo):
    repo._flush_task = asyncio.create_task(asyncio.sleep(0))
    await repo._flush_task

    with patch.object(repo, "flush_write_buffer_sync") as mock_flush, \
         patch.object(repo._executor, "shutdown") as mock_shutdown:
        await repo.shutdown()

    mock_flush.assert_called_once()
    mock_shutdown.assert_called_once_with(wait=False)


@pytest.mark.asyncio
async def test_shutdown_ignores_connection_close_failure(repo):
    fake_conn = MagicMock()
    fake_conn.close.side_effect = sqlite3.Error("close fail")
    if repo._conn:
        repo._conn.close()
    repo._conn = fake_conn

    await repo.shutdown()

    assert repo._conn is None


@pytest.mark.asyncio
async def test_shutdown_skips_close_when_connection_missing(repo):
    if repo._conn:
        repo._conn.close()
    repo._conn = None

    with patch.object(repo, "flush_write_buffer_sync") as mock_flush, \
         patch.object(repo._executor, "shutdown") as mock_shutdown:
        await repo.shutdown()

    mock_flush.assert_called_once()
    mock_shutdown.assert_called_once_with(wait=False)
