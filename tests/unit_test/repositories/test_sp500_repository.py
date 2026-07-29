"""
SP500Repository 단위 테스트 (FDR 다운로드는 전부 mock).
"""
import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from repositories.sp500_repository import SP500Repository, TABLE_NAME


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
