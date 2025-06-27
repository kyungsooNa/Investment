# tests/test_stock_info_updater.py

import os
import shutil
import json
from datetime import datetime, timedelta

import pytest
from utils import stock_info_updater
from unittest.mock import patch, mock_open, MagicMock
import builtins

TEST_DATA_DIR = "data_test"

@pytest.fixture(autouse=True)
def setup_and_teardown():
    # 테스트 시작 전
    os.makedirs(TEST_DATA_DIR, exist_ok=True)
    stock_info_updater.DATA_DIR = os.path.abspath("data_test")
    stock_info_updater.CSV_FILE_PATH = os.path.join(stock_info_updater.DATA_DIR, "stock_code_list.csv")
    stock_info_updater.METADATA_PATH = os.path.join(stock_info_updater.DATA_DIR, "metadata.json")

    yield
    # 테스트 종료 후
    shutil.rmtree(TEST_DATA_DIR)

@patch(f"{stock_info_updater.__name__}.pd.DataFrame.to_csv")
@patch("builtins.open", new_callable=mock_open)
@patch(f"{stock_info_updater.__name__}.os.path.exists", return_value=True)
@patch(f"{stock_info_updater.__name__}.json.dump")
@patch(f"{stock_info_updater.__name__}.json.load", return_value={"last_updated": "2025-06-27"})
@patch(f"{stock_info_updater.__name__}.stock.get_market_ticker_list", return_value=["005930"])
@patch(f"{stock_info_updater.__name__}.stock.get_market_ticker_name", return_value="삼성전자")
def test_force_update_saves_files(
    mock_get_name,
    mock_get_list,
    mock_json_load,
    mock_json_dump,
    mock_exists,
    mock_open_file,
    mock_to_csv
):
    stock_info_updater.save_stock_code_list(force_update=True)

    mock_to_csv.assert_called_once()
    mock_open_file.assert_any_call(stock_info_updater.METADATA_PATH, "w", encoding="utf-8")
    mock_json_dump.assert_called()

@patch(f"{stock_info_updater.__name__}.pd.DataFrame.to_csv")
@patch("builtins.open", new_callable=mock_open)
@patch(f"{stock_info_updater.__name__}.os.path.exists", return_value=True)
@patch(f"{stock_info_updater.__name__}.json.dump")
@patch(f"{stock_info_updater.__name__}.json.load", return_value={"last_updated": "2025-06-27"})
@patch(f"{stock_info_updater.__name__}.stock.get_market_ticker_list", return_value=["005930"])
@patch(f"{stock_info_updater.__name__}.stock.get_market_ticker_name", return_value="삼성전자")
def test_metadata_blocks_update_within_7_days(
    mock_get_name,
    mock_get_list,
    mock_json_load,
    mock_json_dump,
    mock_exists,
    mock_open_file,
    mock_to_csv,
    capfd,
):
    # 강제로 한 번 저장 (실제로 저장되지 않음)
    stock_info_updater.save_stock_code_list(force_update=True)

    # 다시 저장 시도 → 최근 업데이트된 상태라 저장 생략되어야 함
    stock_info_updater.save_stock_code_list(force_update=False)

    captured = capfd.readouterr()
    assert "이미 업데이트됨" in captured.out

    # to_csv는 강제 저장 때만 호출되고, 두 번째 실행에서는 호출되지 않아야 함
    assert mock_to_csv.call_count == 1

def test_needs_update_logic():
    # 직접 메타데이터 파일 생성 (8일 전)
    old_date = (datetime.today() - timedelta(days=8)).strftime("%Y-%m-%d")
    with open(stock_info_updater.METADATA_PATH, "w", encoding="utf-8") as f:
        json.dump({"last_updated": old_date}, f)

    assert stock_info_updater._needs_update() is True

    # 1일 전으로 설정
    recent_date = (datetime.today() - timedelta(days=1)).strftime("%Y-%m-%d")
    with open(stock_info_updater.METADATA_PATH, "w", encoding="utf-8") as f:
        json.dump({"last_updated": recent_date}, f)

    assert stock_info_updater._needs_update() is False
