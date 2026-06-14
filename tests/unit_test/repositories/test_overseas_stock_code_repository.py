# tests/unit_test/repositories/test_overseas_stock_code_repository.py

import os
import sqlite3
import pandas as pd
import pytest
from unittest.mock import MagicMock, patch

from repositories.overseas_stock_code_repository import (
    OverseasStockCodeRepository,
    _write_minimal_db,
    TABLE_NAME,
)


def _create_test_db(db_path, data=None):
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    if data is None:
        data = {
            "심볼": ["AAPL", "NVDA", "LLY"],
            "종목명": ["Apple Inc", "NVIDIA Corp", "Eli Lilly and Co"],
            "거래소": ["NASD", "NASD", "NYSE"],
        }
    pd.DataFrame(data).to_sql(TABLE_NAME, conn, if_exists="replace", index=False)
    conn.commit()
    conn.close()


@pytest.fixture
def mock_logger():
    return MagicMock()


@pytest.fixture
def test_db(tmp_path):
    db_path = str(tmp_path / "overseas_stock_code_list.db")
    _create_test_db(db_path)
    return db_path


def test_all_symbols(test_db, mock_logger):
    repo = OverseasStockCodeRepository(db_path=test_db, logger=mock_logger)
    items = repo.all_symbols()
    assert len(items) == 3
    aapl = next(i for i in items if i["s"] == "AAPL")
    assert aapl == {"s": "AAPL", "n": "Apple Inc", "e": "NASD"}


def test_search_by_symbol_prefix(test_db):
    repo = OverseasStockCodeRepository(db_path=test_db)
    results = repo.search("AAP")
    assert len(results) == 1
    assert results[0]["s"] == "AAPL"
    assert results[0]["e"] == "NASD"

    # 소문자 입력도 매칭
    assert repo.search("aap")[0]["s"] == "AAPL"


def test_search_by_name_substring(test_db):
    repo = OverseasStockCodeRepository(db_path=test_db)
    results = repo.search("apple")
    assert len(results) == 1
    assert results[0]["s"] == "AAPL"

    results_lilly = repo.search("lilly")
    assert results_lilly[0]["s"] == "LLY"


def test_search_empty_and_no_match(test_db):
    repo = OverseasStockCodeRepository(db_path=test_db)
    assert repo.search("") == []
    assert repo.search("   ") == []
    assert repo.search("ZZZZ") == []


def test_search_limit(tmp_path):
    db_path = str(tmp_path / "ov.db")
    _create_test_db(db_path, {
        "심볼": ["A1", "A2", "A3"],
        "종목명": ["alpha one", "alpha two", "alpha three"],
        "거래소": ["NASD", "NYSE", "AMEX"],
    })
    repo = OverseasStockCodeRepository(db_path=db_path)
    assert len(repo.search("alpha", limit=2)) == 2


def test_write_minimal_db(tmp_path):
    db_path = str(tmp_path / "minimal.db")
    logger = MagicMock()
    _write_minimal_db(db_path, logger)
    assert os.path.exists(db_path)
    conn = sqlite3.connect(db_path)
    df = pd.read_sql(f"SELECT * FROM {TABLE_NAME}", conn)
    conn.close()
    assert df.iloc[0]["심볼"] == "(NONE)"
    logger.warning.assert_called_once()


@patch("repositories.overseas_stock_code_repository.save_overseas_stock_code_list")
def test_init_creates_file_when_missing(mock_save, mock_logger, tmp_path):
    db_path = str(tmp_path / "overseas_stock_code_list.db")

    def side_effect(**kwargs):
        _create_test_db(db_path)

    mock_save.side_effect = side_effect
    repo = OverseasStockCodeRepository(db_path=db_path, logger=mock_logger)
    mock_save.assert_called_once_with(force_update=True)
    assert repo.symbol_to_meta["AAPL"]["name"] == "Apple Inc"


@patch("repositories.overseas_stock_code_repository._write_minimal_db")
@patch("repositories.overseas_stock_code_repository.save_overseas_stock_code_list")
def test_recovery_falls_back_to_minimal(mock_save, mock_write_minimal, mock_logger, tmp_path):
    db_path = str(tmp_path / "overseas_stock_code_list.db")
    # 잘못된 스키마 → read 시 에러
    conn = sqlite3.connect(db_path)
    conn.execute(f"CREATE TABLE {TABLE_NAME} (wrong_col TEXT)")
    conn.execute(f"INSERT INTO {TABLE_NAME} VALUES ('x')")
    conn.commit()
    conn.close()

    mock_save.side_effect = Exception("Update Failed")
    mock_write_minimal.side_effect = lambda path, logger=None: _write_minimal_db(path, logger=None)

    repo = OverseasStockCodeRepository(db_path=db_path, logger=mock_logger)
    mock_write_minimal.assert_called_once()
    assert repo.symbol_to_meta["(NONE)"]["name"] == "(종목목록 없음)"
