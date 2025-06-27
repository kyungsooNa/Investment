# tests/test_stock_info_updater.py

import os
import shutil
import json
from datetime import datetime, timedelta

import pytest
from utils import stock_info_updater

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

def test_force_update_saves_files():
    stock_info_updater.save_stock_code_list(force_update=True)
    assert os.path.exists(stock_info_updater.CSV_FILE_PATH)
    assert os.path.exists(stock_info_updater.METADATA_PATH)

    with open(stock_info_updater.METADATA_PATH, "r", encoding="utf-8") as f:
        metadata = json.load(f)
    assert "last_updated" in metadata

def test_metadata_blocks_update_within_7_days(capfd):
    # 먼저 강제 저장
    stock_info_updater.save_stock_code_list(force_update=True)

    # 다시 실행 (force=False)
    stock_info_updater.save_stock_code_list(force_update=False)
    captured = capfd.readouterr()
    assert "이미 업데이트됨" in captured.out

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
