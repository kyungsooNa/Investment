import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from repositories.rs_rating_repository import RSRatingRepository


@pytest.mark.asyncio
async def test_upsert_and_query(tmp_path):
    db = str(tmp_path / "rs.db")
    repo = RSRatingRepository(db_path=db)

    # empty batch returns 0
    assert await repo.upsert_batch([]) == 0

    # insert two records
    records = [
        {"trade_date": "2026-04-14", "code": "0001", "rs_rating": 90, "weighted_rs": 0.9},
        {"trade_date": "2026-04-14", "code": "0002", "rs_rating": 80, "weighted_rs": 0.8},
    ]
    assert await repo.upsert_batch(records) == 2

    # get by date
    by_date = await repo.get_by_date("2026-04-14")
    assert by_date == {"0001": 90, "0002": 80}

    # get single
    single = await repo.get_single("0001", "2026-04-14")
    assert single is not None
    assert single.rs_rating == 90

    # latest date
    latest = await repo.get_latest_date()
    assert latest == "2026-04-14"

    # upsert (conflict) should update
    await repo.upsert_batch([
        {"trade_date": "2026-04-14", "code": "0001", "rs_rating": 95, "weighted_rs": 0.95}
    ])
    updated = await repo.get_single("0001", "2026-04-14")
    assert updated.rs_rating == 95

    # non-existent date -> empty dict
    assert await repo.get_by_date("1999-01-01") == {}

    await repo.close()


@pytest.mark.asyncio
async def test_get_by_code_limit(tmp_path):
    db = str(tmp_path / "rs2.db")
    repo = RSRatingRepository(db_path=db)

    # create 6 historical entries for a single code
    records = []
    for i in range(1, 7):
        d = f"2026-04-{i:02d}"
        records.append({"trade_date": d, "code": "0003", "rs_rating": i * 10, "weighted_rs": i * 0.1})

    assert await repo.upsert_batch(records) == 6

    # limit should return only 3 latest entries
    res = await repo.get_by_code("0003", limit=3)
    assert len(res) == 3
    assert res[0].trade_date == "2026-04-06"

    await repo.close()


@pytest.mark.asyncio
async def test_empty_query_paths_and_close_resets_connections(tmp_path):
    db = str(tmp_path / "rs3.db")
    repo = RSRatingRepository(db_path=db)

    assert await repo.get_by_code("MISSING") == []
    assert await repo.get_single("MISSING", "2026-04-14") is None
    assert await repo.get_latest_date() is None

    await repo._get_write_conn()
    await repo._get_read_conn()
    assert repo._write_conn is not None
    assert repo._read_conn is not None

    await repo.close()

    assert repo._write_conn is None
    assert repo._read_conn is None


@pytest.mark.asyncio
async def test_upsert_and_query_error_paths_are_handled(tmp_path):
    db = str(tmp_path / "rs4.db")
    logger = MagicMock()
    repo = RSRatingRepository(db_path=db, logger=logger)
    repo._write_lock = asyncio.Lock()
    repo._read_conn_lock = asyncio.Lock()

    failing_conn = MagicMock()
    failing_conn.executemany = AsyncMock(side_effect=Exception("write fail"))
    repo._get_write_conn = AsyncMock(return_value=failing_conn)
    assert await repo.upsert_batch([
        {"trade_date": "2026-04-14", "code": "0001", "rs_rating": 90, "weighted_rs": 0.9}
    ]) == 0

    repo._get_read_conn = AsyncMock(side_effect=Exception("read fail"))
    assert await repo.get_by_date("2026-04-14") == {}
    assert await repo.get_by_code("0001") == []
    assert await repo.get_single("0001", "2026-04-14") is None
    assert await repo.get_latest_date() is None

    assert logger.error.call_count >= 5


def test_init_db_sync_logs_error_on_sqlite_failure(tmp_path):
    db = str(tmp_path / "rs5.db")
    logger = MagicMock()

    with patch("repositories.rs_rating_repository.sqlite3.connect", side_effect=Exception("db init fail")):
        RSRatingRepository(db_path=db, logger=logger)

    logger.error.assert_called_once()
