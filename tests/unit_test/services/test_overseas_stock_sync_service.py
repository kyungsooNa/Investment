# tests/unit_test/services/test_overseas_stock_sync_service.py

import json
import sqlite3
import os
from datetime import datetime, timedelta
import pandas as pd
import pytest
from services import overseas_stock_sync_service as svc
from unittest.mock import patch


@pytest.fixture(autouse=True)
def setup_and_teardown(tmp_path, mocker):
    """각 테스트 전후로 임시 데이터 경로로 모킹."""
    temp_data_dir = tmp_path / "data"
    temp_db_file_path = temp_data_dir / "overseas_stock_code_list.db"
    temp_metadata_path = temp_data_dir / "overseas_metadata.json"
    temp_data_dir.mkdir(parents=True, exist_ok=True)

    mocker.patch.object(svc, "ROOT_DIR", new=str(tmp_path))
    mocker.patch.object(svc, "DATA_DIR", new=str(temp_data_dir))
    mocker.patch.object(svc, "DB_FILE_PATH", new=str(temp_db_file_path))
    mocker.patch.object(svc, "METADATA_PATH", new=str(temp_metadata_path))
    yield


def _fake_listing(market):
    data = {
        "NASDAQ": {"Symbol": ["AAPL", "NVDA"], "Name": ["Apple Inc", "NVIDIA Corp"]},
        "NYSE": {"Symbol": ["LLY"], "Name": ["Eli Lilly and Co"]},
        "AMEX": {"Symbol": ["IMO"], "Name": ["Imperial Oil Ltd"]},
    }[market]
    df = pd.DataFrame(data)
    df["IndustryCode"] = "0"
    df["Industry"] = "x"
    return df


@patch("FinanceDataReader.StockListing", side_effect=_fake_listing)
def test_force_update_saves_files(mock_listing):
    """force_update=True이면 3개 거래소 합산 DB와 메타데이터가 저장된다."""
    svc.save_overseas_stock_code_list(force_update=True)

    assert os.path.exists(svc.DB_FILE_PATH)
    assert os.path.exists(svc.METADATA_PATH)

    conn = sqlite3.connect(svc.DB_FILE_PATH)
    df = pd.read_sql(f"SELECT * FROM {svc.TABLE_NAME}", conn)
    conn.close()

    assert list(df.columns) == ["심볼", "종목명", "거래소"]
    assert len(df) == 4  # 2 + 1 + 1
    aapl = df[df["심볼"] == "AAPL"].iloc[0]
    assert aapl["종목명"] == "Apple Inc"
    assert aapl["거래소"] == "NASD"
    assert df[df["심볼"] == "LLY"].iloc[0]["거래소"] == "NYSE"
    assert df[df["심볼"] == "IMO"].iloc[0]["거래소"] == "AMEX"


@patch("FinanceDataReader.StockListing", side_effect=_fake_listing)
def test_metadata_blocks_update_within_7_days(mock_listing, capfd):
    svc.save_overseas_stock_code_list(force_update=True)
    svc.save_overseas_stock_code_list(force_update=False)
    captured = capfd.readouterr()
    assert "이미 업데이트됨" in captured.out


def test_needs_update_logic():
    old_date = (datetime.today() - timedelta(days=8)).strftime("%Y-%m-%d")
    with open(svc.METADATA_PATH, "w", encoding="utf-8") as f:
        json.dump({"last_updated": old_date}, f)
    assert svc._needs_update() is True

    recent_date = (datetime.today() - timedelta(days=1)).strftime("%Y-%m-%d")
    with open(svc.METADATA_PATH, "w", encoding="utf-8") as f:
        json.dump({"last_updated": recent_date}, f)
    assert svc._needs_update() is False


def test_needs_update_when_metadata_missing():
    assert svc._needs_update() is True


@patch("FinanceDataReader.StockListing", side_effect=_fake_listing)
def test_load_overseas_stock_code_list(mock_listing):
    svc.save_overseas_stock_code_list(force_update=True)
    df = svc.load_overseas_stock_code_list()
    assert len(df) == 4
    assert set(df["심볼"]) == {"AAPL", "NVDA", "LLY", "IMO"}
