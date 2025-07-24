# tests/test_stock_info_updater.py

import json
from datetime import datetime, timedelta
import pandas as pd # pandas import 추가
import pytest
from utils import stock_info_updater
from unittest.mock import patch, mock_open

TEST_DATA_DIR = "data_test"

@pytest.fixture(autouse=True)
def setup_and_teardown(tmp_path, mocker): # tmp_path와 mocker fixture를 인자로 받음
    """
    각 테스트 실행 전/후에 테스트 환경을 설정하고 정리합니다.
    - `stock_info_updater` 모듈의 전역 경로를 각 테스트의 고유한 임시 디렉토리로 패치합니다.
    - 테스트 종료 후 `tmp_path`는 pytest에 의해 자동으로 정리됩니다.
    """
    # 테스트 시작 전
    # tmp_path는 pytest가 각 테스트에 제공하는 고유한 임시 디렉토리 (Pathlib 객체)
    # 이 임시 디렉토리 안에 테스트용 데이터 디렉토리를 생성
    temp_data_dir = tmp_path / "data"
    temp_csv_file_path = temp_data_dir / "stock_code_list.csv"
    temp_metadata_path = temp_data_dir / "metadata.json"

    # 데이터 디렉토리 생성 (tmp_path 하위에)
    # parents=True는 상위 디렉토리가 없으면 생성, exist_ok=True는 이미 있어도 오류 없음
    temp_data_dir.mkdir(parents=True, exist_ok=True)

    # 📌 stock_info_updater 모듈 내의 전역 변수들을 각 테스트의 고유한 임시 경로로 패치
    # mocker.patch.object를 사용하면 테스트 종료 시 자동으로 원상 복구됩니다.
    mocker.patch.object(stock_info_updater, 'ROOT_DIR', new=str(tmp_path))
    mocker.patch.object(stock_info_updater, 'DATA_DIR', new=str(temp_data_dir))
    mocker.patch.object(stock_info_updater, 'CSV_FILE_PATH', new=str(temp_csv_file_path))
    mocker.patch.object(stock_info_updater, 'METADATA_PATH', new=str(temp_metadata_path))

    yield # 테스트 함수가 실행되는 지점

    # 테스트 종료 후
    # pytest의 tmp_path fixture는 테스트 종료 시 해당 임시 디렉토리를 자동으로 삭제합니다.
    # 따라서 shutil.rmtree(TEST_DATA_DIR)와 같은 명시적인 삭제 코드는 필요 없습니다.
    # 또한, mocker.patch.object는 yield 후 자동으로 패치를 원상 복구합니다.
    # 로깅 핸들처럼 명시적으로 닫아야 하는 리소스가 있다면 이곳에서 처리합니다.
    # (현재 stock_info_updater에는 해당 없음)


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
@patch(f"{stock_info_updater.__name__}._needs_update", return_value=False) # <--- 이 부분을 수정했습니다.
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

# 1. `_load_metadata` 함수에서 메타데이터 파일이 존재하지 않을 때 `None`을 반환하는 경우
# (utils/stock_info_updater.py의 25번 라인 커버)
@patch(f"{stock_info_updater.__name__}.os.path.exists", return_value=False)
def test_load_metadata_no_file(mock_exists):
    """
    `metadata.json` 파일이 존재하지 않을 때 `_load_metadata`가 `None`을 반환하는지 테스트합니다.
    이는 `utils/stock_info_updater.py`의 25번 라인 (`return None`)을 커버합니다.
    """
    # Given: os.path.exists가 False를 반환하도록 Mocking (데코레이터에서 설정됨)
    # setup_and_teardown 픽스처가 실제 파일이 없도록 보장하지만, Mocking을 통해 명시적으로 제어.

    # When
    metadata = stock_info_updater._load_metadata()

    # Then
    assert metadata is None
    mock_exists.assert_called_once_with(stock_info_updater.METADATA_PATH)


# 2. `_needs_update` 함수에서 메타데이터가 `None`일 때 `True`를 반환하는 경우
# (utils/stock_info_updater.py의 33번 라인 커버)
@patch(f"{stock_info_updater.__name__}._load_metadata", return_value=None)
def test_needs_update_when_metadata_is_none(mock_load_metadata):
    """
    `_load_metadata`가 `None`을 반환할 때 `_needs_update`가 `True`를 반환하는지 테스트합니다.
    이는 `utils/stock_info_updater.py`의 33번 라인 (`return True`)을 커버합니다.
    """
    # Given: _load_metadata가 None을 반환하도록 Mocking (데코레이터에서 설정됨)

    # When
    needs_update = stock_info_updater._needs_update()

    # Then
    assert needs_update is True
    mock_load_metadata.assert_called_once()


# 3. `load_stock_code_list` 함수 실행
# (utils/stock_info_updater.py의 74번 라인 커버)
@patch(f"{stock_info_updater.__name__}.pd.read_csv")
@patch(f"{stock_info_updater.__name__}.os.path.exists", return_value=True) # METADATA_PATH 존재 (load_metadata에서 사용)
@patch(f"{stock_info_updater.__name__}._load_metadata") # _needs_update 내부에서 _load_metadata가 호출되므로 mock
def test_load_stock_code_list_success(mock_load_metadata, mock_exists, mock_read_csv):
    """
    `load_stock_code_list` 함수가 CSV 파일을 올바르게 읽어오는지 테스트합니다.
    이는 `utils/stock_info_updater.py`의 74번 라인 (`return pd.read_csv(...)`)을 커버합니다.
    """
    # Given:
    # mock_exists는 True를 반환하여 파일이 존재한다고 가정.
    # mock_read_csv는 더미 DataFrame을 반환하도록 설정.
    mock_df = pd.DataFrame([{"종목코드": "005930", "종목명": "삼성전자", "시장구분": "KOSPI"}])
    mock_read_csv.return_value = mock_df

    # _load_metadata가 호출될 때 유효한 메타데이터를 반환하도록 설정하여
    # _needs_update가 False를 반환하도록 유도 (save_stock_code_list의 조건)
    # load_stock_code_list는 _needs_update와 무관하므로 이 Mock은 사실 필요 없음.
    # 하지만 환경을 일관성 있게 유지하기 위해 추가.
    mock_load_metadata.return_value = {"last_updated": (datetime.today() - timedelta(days=1)).strftime("%Y-%m-%d")}

    # CSV 파일이 존재한다고 가정
    # `os.path.exists`는 `_load_metadata`에서 호출될 수 있으므로 `True`로 설정합니다.
    # `load_stock_code_list` 자체는 `os.path.exists(CSV_FILE_PATH)`를 직접 검사하지 않으므로,
    # `pd.read_csv`가 성공적으로 호출되려면 파일이 있다고 가정해야 합니다.

    # When
    df = stock_info_updater.load_stock_code_list()

    # Then
    mock_read_csv.assert_called_once_with(stock_info_updater.CSV_FILE_PATH, dtype={"종목코드": str})
    assert df.equals(mock_df)
