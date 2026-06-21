# tests/test_stock_sync_service.py

import json
import sqlite3
import os
from datetime import datetime, timedelta
import pandas as pd
import pytest
from services import stock_sync_service
from unittest.mock import patch

TEST_DATA_DIR = "data_test"

@pytest.fixture(autouse=True)
def setup_and_teardown(tmp_path, mocker):
    """
    각 테스트 실행 전/후에 테스트 환경을 설정하고 정리합니다.
    """
    temp_data_dir = tmp_path / "data"
    temp_db_file_path = temp_data_dir / "stock_code_list.db"
    temp_csv_file_path = temp_data_dir / "stock_code_list.csv"
    temp_metadata_path = temp_data_dir / "metadata.json"

    temp_data_dir.mkdir(parents=True, exist_ok=True)

    mocker.patch.object(stock_sync_service, 'ROOT_DIR', new=str(tmp_path))
    mocker.patch.object(stock_sync_service, 'DATA_DIR', new=str(temp_data_dir))
    mocker.patch.object(stock_sync_service, 'DB_FILE_PATH', new=str(temp_db_file_path))
    mocker.patch.object(stock_sync_service, 'CSV_FILE_PATH', new=str(temp_csv_file_path))
    mocker.patch.object(stock_sync_service, 'METADATA_PATH', new=str(temp_metadata_path))

    yield


@patch("FinanceDataReader.StockListing")
def test_force_update_saves_files(mock_fdr_listing):
    """force_update=True로 save_stock_code_list를 호출하면 DB와 메타데이터가 저장됩니다."""
    mock_fdr_listing.side_effect = [
        pd.DataFrame({
            'Code': ['005930', '123456'],
            'Name': ['삼성전자', '코스닥종목'],
            'MarketId': ['STK', 'KSQ']
        }),
        pd.DataFrame(),
    ]

    stock_sync_service.save_stock_code_list(force_update=True)

    # 1. DB 파일 생성 확인 (setup_and_teardown에서 안전한 경로로 이미 모킹됨)
    assert os.path.exists(stock_sync_service.DB_FILE_PATH)

    # 2. 임시 DB 내용 확인
    conn = sqlite3.connect(stock_sync_service.DB_FILE_PATH)
    df = pd.read_sql("SELECT * FROM stocks", conn)
    conn.close()
    
    # 모킹된 데이터를 통해 KOSPI 1개, KOSDAQ 1개가 삽입되므로 2개가 정상
    assert len(df) == 2  


@patch("FinanceDataReader.StockListing")
def test_force_update_includes_etf_codes(mock_fdr_listing):
    """ETF/KR 목록도 종목명 매핑 DB에 함께 저장합니다."""
    mock_fdr_listing.side_effect = [
        pd.DataFrame({
            'Code': ['005930'],
            'Name': ['삼성전자'],
            'MarketId': ['STK'],
        }),
        pd.DataFrame({
            'Symbol': ['069500', '0162Z0'],
            'Name': ['KODEX 200', 'RISE 삼성전자SK하이닉스채권혼합50'],
        }),
    ]

    stock_sync_service.save_stock_code_list(force_update=True)

    conn = sqlite3.connect(stock_sync_service.DB_FILE_PATH)
    df = pd.read_sql("SELECT * FROM stocks", conn, dtype={"종목코드": str})
    conn.close()

    names = dict(zip(df["종목코드"], df["종목명"]))
    markets = dict(zip(df["종목코드"], df["시장구분"]))
    assert names["069500"] == "KODEX 200"
    assert names["0162Z0"] == "RISE 삼성전자SK하이닉스채권혼합50"
    assert markets["069500"] == "ETF"


@patch("FinanceDataReader.StockListing")
def test_metadata_blocks_update_within_7_days(mock_fdr_listing, caplog):
    mock_fdr_listing.side_effect = [
        pd.DataFrame({
            'Code': ['005930'],
            'Name': ['삼성전자'],
            'MarketId': ['STK']
        }),
        pd.DataFrame(),
    ]
    import logging
    # 강제로 한 번 저장
    stock_sync_service.save_stock_code_list(force_update=True)

    # 다시 저장 시도 → 최근 업데이트된 상태라 저장 생략되어야 함
    with caplog.at_level(logging.INFO):
        stock_sync_service.save_stock_code_list(force_update=False)

    assert "이미 업데이트됨" in caplog.text


def test_needs_update_logic():
    # 직접 메타데이터 파일 생성 (8일 전)
    old_date = (datetime.today() - timedelta(days=8)).strftime("%Y-%m-%d")
    with open(stock_sync_service.METADATA_PATH, "w", encoding="utf-8") as f:
        json.dump({"last_updated": old_date}, f)

    assert stock_sync_service._needs_update() is True

    # 1일 전으로 설정
    recent_date = (datetime.today() - timedelta(days=1)).strftime("%Y-%m-%d")
    with open(stock_sync_service.METADATA_PATH, "w", encoding="utf-8") as f:
        json.dump({"last_updated": recent_date}, f)

    assert stock_sync_service._needs_update() is False


@patch(f"{stock_sync_service.__name__}.os.path.exists", return_value=False)
def test_load_metadata_no_file(mock_exists):
    """
    `metadata.json` 파일이 존재하지 않을 때 `_load_metadata`가 `None`을 반환하는지 테스트합니다.
    """
    metadata = stock_sync_service._load_metadata()
    assert metadata is None
    mock_exists.assert_called_once_with(stock_sync_service.METADATA_PATH)


@patch(f"{stock_sync_service.__name__}._load_metadata", return_value=None)
def test_needs_update_when_metadata_is_none(mock_load_metadata):
    """
    `_load_metadata`가 `None`을 반환할 때 `_needs_update`가 `True`를 반환하는지 테스트합니다.
    """
    needs_update = stock_sync_service._needs_update()
    assert needs_update is True
    mock_load_metadata.assert_called_once()


def test_load_stock_code_list_success():
    """
    `load_stock_code_list` 함수가 SQLite DB를 올바르게 읽어오는지 테스트합니다.
    """
    # 테스트용 DB 생성
    db_path = stock_sync_service.DB_FILE_PATH
    conn = sqlite3.connect(db_path)
    mock_df = pd.DataFrame([{"종목코드": "005930", "종목명": "삼성전자", "시장구분": "KOSPI"}])
    mock_df.to_sql("stocks", conn, if_exists="replace", index=False)
    conn.close()

    df = stock_sync_service.load_stock_code_list()

    assert len(df) == 1
    assert df.iloc[0]["종목코드"] == "005930"
