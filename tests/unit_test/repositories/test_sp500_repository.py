"""
SP500Repository 단위 테스트 (FDR 다운로드는 전부 mock).
"""
import os
import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from repositories.sp500_repository import SP500Repository, TABLE_NAME, _write_minimal_db


def _write_db(db_path, rows):
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(f"CREATE TABLE {TABLE_NAME} (심볼 TEXT, 종목명 TEXT, 섹터 TEXT)")
        conn.executemany(f"INSERT INTO {TABLE_NAME} VALUES (?, ?, ?)", rows)
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "sp500_list.db"
    _write_db(path, [
        ("AAPL", "Apple Inc.", "Information Technology"),
        ("XOM", "Exxon Mobil", "Energy"),
    ])
    return str(path)


def test_loads_existing_db(db_path):
    repo = SP500Repository(db_path=db_path)
    assert repo.count() == 2
    assert set(repo.all_symbols()) == {"AAPL", "XOM"}


def test_get_meta_returns_name_and_sector(db_path):
    repo = SP500Repository(db_path=db_path)
    assert repo.get_meta("AAPL") == {"name": "Apple Inc.", "sector": "Information Technology"}


def test_get_meta_is_case_insensitive(db_path):
    repo = SP500Repository(db_path=db_path)
    assert repo.get_meta("aapl")["name"] == "Apple Inc."


def test_get_meta_unknown_symbol_returns_none(db_path):
    repo = SP500Repository(db_path=db_path)
    assert repo.get_meta("NOPE") is None


def test_null_sector_becomes_empty_string(tmp_path):
    path = tmp_path / "sp500_list.db"
    _write_db(path, [("AAPL", "Apple Inc.", None)])
    repo = SP500Repository(db_path=str(path))
    assert repo.get_meta("AAPL")["sector"] == ""


def test_missing_db_triggers_download(tmp_path):
    path = tmp_path / "sp500_list.db"

    def _fake_save(force_update=False):
        _write_db(path, [("AAPL", "Apple Inc.", "IT")])

    with patch("repositories.sp500_repository.save_sp500_list", side_effect=_fake_save) as mock_save:
        repo = SP500Repository(db_path=str(path))

    mock_save.assert_called_once_with(force_update=True)
    assert repo.all_symbols() == ["AAPL"]


def test_download_failure_propagates(tmp_path):
    path = tmp_path / "sp500_list.db"
    with patch("repositories.sp500_repository.save_sp500_list", side_effect=RuntimeError("no net")):
        with pytest.raises(RuntimeError):
            SP500Repository(db_path=str(path))


def test_empty_db_falls_back_to_minimal_db(tmp_path):
    """갱신까지 실패하면 최소 DB로 시작해 앱 부팅을 막지 않는다."""
    path = tmp_path / "sp500_list.db"
    _write_db(path, [])
    logger = MagicMock()

    with patch("repositories.sp500_repository.save_sp500_list", side_effect=RuntimeError("no net")):
        repo = SP500Repository(db_path=str(path), logger=logger)

    assert repo.all_symbols() == ["(NONE)"]
    assert logger.warning.called


def test_default_db_path_points_at_project_data_dir():
    with patch("repositories.sp500_repository.os.path.exists", return_value=True), \
         patch.object(SP500Repository, "_load_data"):
        repo = SP500Repository()

    assert repo._db_path.endswith(os.path.join("data", "sp500_list.db"))


def test_missing_db_download_logs_progress(tmp_path):
    path = tmp_path / "sp500_list.db"
    logger = MagicMock()

    def _fake_save(force_update=False):
        _write_db(path, [("AAPL", "Apple Inc.", "IT")])

    with patch("repositories.sp500_repository.save_sp500_list", side_effect=_fake_save):
        repo = SP500Repository(db_path=str(path), logger=logger)

    assert repo.count() == 1
    assert logger.info.call_count >= 2


def test_download_failure_is_logged_before_propagating(tmp_path):
    path = tmp_path / "sp500_list.db"
    logger = MagicMock()

    with patch("repositories.sp500_repository.save_sp500_list", side_effect=RuntimeError("no net")):
        with pytest.raises(RuntimeError):
            SP500Repository(db_path=str(path), logger=logger)

    logger.error.assert_called_once()


def test_minimal_db_row_is_treated_as_empty_and_refreshed(tmp_path):
    """(NONE) 한 줄짜리 최소 DB는 정상 데이터로 보지 않고 갱신을 시도한다."""
    path = tmp_path / "sp500_list.db"
    _write_db(path, [("(NONE)", "(구성종목 없음)", "")])

    def _fake_save(force_update=False):
        _write_db(path, [("AAPL", "Apple Inc.", "IT")])

    with patch("repositories.sp500_repository.save_sp500_list", side_effect=_fake_save) as mock_save:
        repo = SP500Repository(db_path=str(path))

    mock_save.assert_called_once_with(force_update=True)
    assert repo.all_symbols() == ["AAPL"]


def test_corrupted_db_is_removed_and_rebuilt(tmp_path):
    path = tmp_path / "sp500_list.db"
    path.write_bytes(b"not a sqlite file")

    def _fake_save(force_update=False):
        _write_db(path, [("AAPL", "Apple Inc.", "IT")])

    with patch("repositories.sp500_repository.save_sp500_list", side_effect=_fake_save):
        repo = SP500Repository(db_path=str(path))

    assert repo.all_symbols() == ["AAPL"]


def test_refresh_that_returns_minimal_db_again_falls_back(tmp_path):
    """갱신이 성공해도 결과가 최소 DB면 최소 DB 경로로 마무리한다."""
    path = tmp_path / "sp500_list.db"
    _write_db(path, [])

    def _fake_save(force_update=False):
        conn = sqlite3.connect(path)
        try:
            conn.execute(f"DELETE FROM {TABLE_NAME}")
            conn.execute(f"INSERT INTO {TABLE_NAME} VALUES ('(NONE)', '(구성종목 없음)', '')")
            conn.commit()
        finally:
            conn.close()

    with patch("repositories.sp500_repository.save_sp500_list", side_effect=_fake_save):
        repo = SP500Repository(db_path=str(path))

    assert repo.all_symbols() == ["(NONE)"]


def test_delete_failure_during_recovery_is_logged(tmp_path):
    path = tmp_path / "sp500_list.db"
    _write_db(path, [])
    logger = MagicMock()

    def _fake_save(force_update=False):
        _write_db(path, [("AAPL", "Apple Inc.", "IT")])

    with patch("repositories.sp500_repository.save_sp500_list", side_effect=_fake_save), \
         patch("repositories.sp500_repository.os.remove", side_effect=OSError("삭제 불가")):
        repo = SP500Repository(db_path=str(path), logger=logger)

    logger.error.assert_called_once()
    assert repo.count() >= 1


def test_write_minimal_db_creates_single_placeholder_row(tmp_path):
    path = str(tmp_path / "nested" / "sp500_list.db")
    logger = MagicMock()

    _write_minimal_db(path, logger)

    conn = sqlite3.connect(path)
    try:
        rows = conn.execute(f"SELECT * FROM {TABLE_NAME}").fetchall()
    finally:
        conn.close()
    assert rows == [("(NONE)", "(구성종목 없음)", "")]
    logger.warning.assert_called_once()
