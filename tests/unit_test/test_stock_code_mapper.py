# tests\unit_test\test_stock_code_mapper.py

import pytest
import pandas as pd
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch, call
from services.stock_query_service import StockQueryService

# 경로 문제를 피하기 위해, 테스트 실행 시 프로젝트 루트를 기준으로 import
from market_data.stock_code_mapper import StockCodeMapper, _write_minimal_csv
from common.types import ErrorCode, ResCommonResponse, ResTopMarketCapApiItem

# --- 테스트를 위한 모의(Mock) 데이터 및 Fixture ---

@pytest.fixture
def mock_stock_df():
    """테스트용 가짜 주식 코드/이름 데이터프레임을 생성하는 Fixture."""
    data = {
        '종목코드': ['005930', '000660', '035720'],
        '종목명': ['삼성전자', 'SK하이닉스', '카카오']
    }
    return pd.DataFrame(data)


@pytest.fixture
def mock_logger():
    """테스트용 모의 로거(Logger) 객체를 생성하는 Fixture."""
    return MagicMock()


# --- StockCodeMapper 클래스 테스트 ---

@patch('os.path.exists')
@patch('pandas.read_csv')
def test_initialization_with_explicit_path(mock_read_csv, mock_exists, mock_stock_df, mock_logger):
    """
    명시적인 CSV 경로로 초기화가 정상적으로 동작하는지 테스트합니다.
    """
    # Arrange: pd.read_csv가 모의 데이터프레임을 반환하도록 설정
    mock_read_csv.return_value = mock_stock_df
    mock_exists.return_value = True  # 파일이 항상 존재한다고 가정
    csv_path = "fake/path/to/codes.csv"

    # Act
    mapper = StockCodeMapper(csv_path=csv_path, logger=mock_logger)

    # Assert
    # 1. pd.read_csv가 정확한 경로로 호출되었는지 확인
    mock_read_csv.assert_called_once_with(csv_path, dtype={"종목코드": str})

    # 2. 내부 딕셔너리가 정확히 생성되었는지 확인
    assert mapper.code_to_name['005930'] == '삼성전자'
    assert mapper.name_to_code['SK하이닉스'] == '000660'

    # 3. 로거가 정상적으로 호출되었는지 확인
    calls = [call.args[0] for call in mock_logger.info.call_args_list]
    assert f"🔄 종목코드 매핑 CSV 로드 완료: {csv_path}" in calls

@patch('os.path.exists')
@patch('pandas.read_csv')
@patch('os.path.abspath')
def test_initialization_with_default_path(mock_abspath, mock_read_csv, mock_exists, mock_stock_df):
    """
    기본 CSV 경로로 초기화가 정상적으로 동작하는지 테스트합니다.
    """
    # Arrange
    mock_read_csv.return_value = mock_stock_df
    mock_exists.return_value = True # 파일이 항상 존재한다고 가정
    # os.path.abspath가 예측 가능한 경로를 반환하도록 설정
    mock_abspath.return_value = "/project_root"
    expected_path = os.path.join("/project_root", "data", "stock_code_list.csv")

    # Act
    mapper = StockCodeMapper()

    # Assert
    mock_read_csv.assert_called_once_with(expected_path, dtype={"종목코드": str})
    assert mapper.code_to_name['005930'] == '삼성전자'


@patch('market_data.stock_code_mapper.save_stock_code_list')
@patch('os.path.exists')
def test_initialization_file_not_found(mock_exists, mock_save_stock_code_list, mock_logger):
    """
    CSV 파일을 찾지 못하고 생성에도 실패했을 때 예외가 발생하는지 테스트합니다.
    """
    # Arrange
    mock_exists.return_value = False  # 파일이 존재하지 않는다고 설정
    error_message = "File generation failed"
    mock_save_stock_code_list.side_effect = FileNotFoundError(error_message)

    # Act & Assert
    with pytest.raises(FileNotFoundError):
        StockCodeMapper(logger=mock_logger)

    # 로거가 파일 생성 실패 에러 메시지를 기록했는지 확인
    mock_logger.error.assert_called_once_with(f"❌ 종목코드 매핑 CSV 파일 생성 실패: {error_message}")


@patch('os.path.exists')
@patch('pandas.read_csv')
def test_get_name_by_code(mock_read_csv, mock_exists, mock_stock_df, mock_logger):
    """get_name_by_code 메서드의 성공/실패 시나리오를 테스트합니다."""
    # Arrange
    mock_read_csv.return_value = mock_stock_df
    mock_exists.return_value = True
    mapper = StockCodeMapper(logger=mock_logger)

    # --- 성공 케이스 ---
    # Act
    name = mapper.get_name_by_code('005930')
    # Assert
    assert name == '삼성전자'

    # --- 실패 케이스 ---
    # Act
    name_fail = mapper.get_name_by_code('999999')  # 존재하지 않는 코드
    # Assert
    assert name_fail == ""
    # 실패 시 로거가 호출되었는지 확인
    mock_logger.warning.assert_called_once_with("❗ 종목명 없음: 999999")


@patch('os.path.exists')
@patch('pandas.read_csv')
def test_get_code_by_name(mock_read_csv, mock_exists, mock_stock_df, mock_logger):
    """get_code_by_name 메서드의 성공/실패 시나리오를 테스트합니다."""
    # Arrange
    mock_read_csv.return_value = mock_stock_df
    mock_exists.return_value = True
    mapper = StockCodeMapper(logger=mock_logger)

    # --- 성공 케이스 ---
    # Act
    code = mapper.get_code_by_name('카카오')
    # Assert
    assert code == '035720'

    # --- 실패 케이스 ---
    # Act
    code_fail = mapper.get_code_by_name('없는회사')  # 존재하지 않는 이름
    # Assert
    assert code_fail == ""
    # 실패 시 로거가 호출되었는지 확인
    mock_logger.warning.assert_called_once_with("❗ 종목코드 없음: 없는회사")

@patch('market_data.stock_code_mapper.save_stock_code_list')
@patch('os.path.exists')
def test_initialization_file_not_found_without_logger(mock_exists, mock_save):
    """
    CSV 로드 실패 시 logger가 없더라도 정상적으로 예외가 발생하는지 테스트합니다 (line 24 분기 타기).
    """
    # Arrange: FileNotFoundError 유도
    mock_exists.return_value = False
    mock_save.side_effect = FileNotFoundError("파일 없음")

    # Act & Assert
    with pytest.raises(FileNotFoundError):
        StockCodeMapper(logger=None)  # logger 없이 초기화

# --- 추가 테스트 케이스 (Coverage 향상) ---

def test_write_minimal_csv(tmp_path):
    """_write_minimal_csv 함수가 정상적으로 최소 CSV를 생성하는지 테스트합니다."""
    csv_path = tmp_path / "minimal.csv"
    logger = MagicMock()
    
    _write_minimal_csv(str(csv_path), logger)
    
    assert csv_path.exists()
    df = pd.read_csv(csv_path)
    assert not df.empty
    assert "종목코드" in df.columns
    assert df.iloc[0]["종목코드"] == "000000"
    logger.warning.assert_called_once()

@patch('market_data.stock_code_mapper.save_stock_code_list')
@patch('os.path.exists')
@patch('pandas.read_csv')
def test_init_creates_file_success(mock_read_csv, mock_exists, mock_save, mock_logger, mock_stock_df):
    """CSV 파일이 없을 때 생성을 시도하고 성공하는 케이스를 테스트합니다."""
    mock_exists.return_value = False
    mock_read_csv.return_value = mock_stock_df
    
    StockCodeMapper(logger=mock_logger)
    
    mock_save.assert_called_once_with(force_update=True)
    # 로그 메시지 확인
    assert any("생성 완료" in call.args[0] for call in mock_logger.info.call_args_list)

@patch('market_data.stock_code_mapper.save_stock_code_list')
@patch('os.path.exists')
@patch('pandas.read_csv')
def test_load_df_empty_csv_recovery_success(mock_read_csv, mock_exists, mock_save, mock_logger, mock_stock_df):
    """CSV가 비어있을 때 갱신을 시도하여 성공하는 케이스를 테스트합니다."""
    mock_exists.return_value = True
    
    # 첫 번째 읽기: 빈 데이터프레임 -> ValueError 발생 -> except 블록 진입
    # 두 번째 읽기: 정상 데이터프레임
    mock_read_csv.side_effect = [pd.DataFrame(), mock_stock_df]
    
    mapper = StockCodeMapper(logger=mock_logger)
    
    mock_save.assert_called_once_with(force_update=True)
    assert mapper.code_to_name['005930'] == '삼성전자'
    # 경고 로그 확인
    assert any("비어 있음" in call.args[0] for call in mock_logger.warning.call_args_list)

@patch('market_data.stock_code_mapper._write_minimal_csv')
@patch('market_data.stock_code_mapper.save_stock_code_list')
@patch('os.path.exists')
@patch('pandas.read_csv')
def test_load_df_empty_csv_recovery_fail_minimal(mock_read_csv, mock_exists, mock_save, mock_write_minimal, mock_logger):
    """CSV가 비어있고 갱신도 실패했을 때 최소 CSV를 사용하는 케이스를 테스트합니다."""
    mock_exists.return_value = True
    
    # 1. 첫 읽기: 빈 DF -> ValueError("비어")
    # 2. 갱신 시도: Exception 발생
    # 3. 최소 CSV 작성 호출
    # 4. 재읽기: 최소 DF 반환
    minimal_df = pd.DataFrame([{"종목코드": "000000", "종목명": "(종목목록 없음)"}])
    mock_read_csv.side_effect = [pd.DataFrame(), minimal_df]
    mock_save.side_effect = Exception("Update Failed")
    
    mapper = StockCodeMapper(logger=mock_logger)
    
    mock_write_minimal.assert_called_once()
    assert mapper.code_to_name['000000'] == '(종목목록 없음)'
    assert any("최소 CSV" in call.args[0] for call in mock_logger.warning.call_args_list)

@patch('os.path.exists')
@patch('pandas.read_csv')
def test_load_df_generic_exception(mock_read_csv, mock_exists, mock_logger):
    """비어있는 에러가 아닌 일반적인 에러 발생 시 예외를 다시 던지는지 테스트합니다."""
    mock_exists.return_value = True
    mock_read_csv.side_effect = Exception("Critical IO Error")
    
    with pytest.raises(Exception, match="Critical IO Error"):
        StockCodeMapper(logger=mock_logger)
    
    mock_logger.error.assert_called()

@patch('os.path.exists')
@patch('pandas.read_csv')
def test_kosdaq_methods(mock_read_csv, mock_exists, mock_logger):
    """get_kosdaq_codes 및 is_kosdaq 메서드를 테스트합니다."""
    mock_exists.return_value = True
    data = {
        '종목코드': ['005930', '123456'],
        '종목명': ['삼성전자', '코스닥종목'],
        '시장구분': ['KOSPI', 'KOSDAQ']
    }
    mock_read_csv.return_value = pd.DataFrame(data)
    
    mapper = StockCodeMapper(logger=mock_logger)
    
    # get_kosdaq_codes
    kosdaq = mapper.get_kosdaq_codes()
    assert '123456' in kosdaq
    assert '005930' not in kosdaq
    
    # is_kosdaq
    assert mapper.is_kosdaq('123456') is True
    assert mapper.is_kosdaq('005930') is False
    assert mapper.is_kosdaq('999999') is False

@patch('os.path.exists')
@patch('pandas.read_csv')
def test_kosdaq_methods_missing_column(mock_read_csv, mock_exists, mock_logger, mock_stock_df):
    """시장구분 컬럼이 없을 때 KOSDAQ 관련 메서드가 안전하게 동작하는지 테스트합니다."""
    mock_exists.return_value = True
    # mock_stock_df에는 '시장구분' 컬럼이 없음
    mock_read_csv.return_value = mock_stock_df
    
    mapper = StockCodeMapper(logger=mock_logger)
    
    assert mapper.get_kosdaq_codes() == []
    assert mapper.is_kosdaq('005930') is False

# --- 추가 테스트 케이스 (Coverage 향상) ---

def test_write_minimal_csv(tmp_path):
    """_write_minimal_csv 함수가 정상적으로 최소 CSV를 생성하는지 테스트합니다."""
    csv_path = tmp_path / "minimal.csv"
    logger = MagicMock()
    
    _write_minimal_csv(str(csv_path), logger)
    
    assert csv_path.exists()
    df = pd.read_csv(csv_path, dtype={"종목코드": str})
    assert not df.empty
    assert "종목코드" in df.columns
    assert df.iloc[0]["종목코드"] == "000000"
    logger.warning.assert_called_once()

@patch('market_data.stock_code_mapper.save_stock_code_list')
@patch('os.path.exists')
@patch('pandas.read_csv')
def test_init_creates_file_success(mock_read_csv, mock_exists, mock_save, mock_logger, mock_stock_df):
    """CSV 파일이 없을 때 생성을 시도하고 성공하는 케이스를 테스트합니다."""
    mock_exists.return_value = False
    mock_read_csv.return_value = mock_stock_df
    
    StockCodeMapper(logger=mock_logger)
    
    mock_save.assert_called_once_with(force_update=True)
    # 로그 메시지 확인
    assert any("생성 완료" in call.args[0] for call in mock_logger.info.call_args_list)

@patch('market_data.stock_code_mapper.save_stock_code_list')
@patch('os.path.exists')
@patch('pandas.read_csv')
def test_load_df_empty_csv_recovery_success(mock_read_csv, mock_exists, mock_save, mock_logger, mock_stock_df):
    """CSV가 비어있을 때 갱신을 시도하여 성공하는 케이스를 테스트합니다."""
    mock_exists.return_value = True
    
    # 첫 번째 읽기: 빈 데이터프레임 -> ValueError 발생 -> except 블록 진입
    # 두 번째 읽기: 정상 데이터프레임
    mock_read_csv.side_effect = [pd.DataFrame(), mock_stock_df]
    
    mapper = StockCodeMapper(logger=mock_logger)
    
    mock_save.assert_called_once_with(force_update=True)
    assert mapper.code_to_name['005930'] == '삼성전자'
    # 경고 로그 확인
    assert any("비어 있음" in call.args[0] for call in mock_logger.warning.call_args_list)

@patch('market_data.stock_code_mapper._write_minimal_csv')
@patch('market_data.stock_code_mapper.save_stock_code_list')
@patch('os.path.exists')
@patch('pandas.read_csv')
def test_load_df_empty_csv_recovery_fail_minimal(mock_read_csv, mock_exists, mock_save, mock_write_minimal, mock_logger):
    """CSV가 비어있고 갱신도 실패했을 때 최소 CSV를 사용하는 케이스를 테스트합니다."""
    mock_exists.return_value = True
    
    # 1. 첫 읽기: 빈 DF -> ValueError("비어")
    # 2. 갱신 시도: Exception 발생
    # 3. 최소 CSV 작성 호출
    # 4. 재읽기: 최소 DF 반환
    minimal_df = pd.DataFrame([{"종목코드": "000000", "종목명": "(종목목록 없음)"}])
    mock_read_csv.side_effect = [pd.DataFrame(), minimal_df]
    mock_save.side_effect = Exception("Update Failed")
    
    mapper = StockCodeMapper(logger=mock_logger)
    
    mock_write_minimal.assert_called_once()
    assert mapper.code_to_name['000000'] == '(종목목록 없음)'
    assert any("최소 CSV" in call.args[0] for call in mock_logger.warning.call_args_list)

@patch('os.path.exists')
@patch('pandas.read_csv')
def test_load_df_generic_exception(mock_read_csv, mock_exists, mock_logger):
    """비어있는 에러가 아닌 일반적인 에러 발생 시 예외를 다시 던지는지 테스트합니다."""
    mock_exists.return_value = True
    mock_read_csv.side_effect = Exception("Critical IO Error")
    
    with pytest.raises(Exception, match="Critical IO Error"):
        StockCodeMapper(logger=mock_logger)
    
    mock_logger.error.assert_called()

@patch('os.path.exists')
@patch('pandas.read_csv')
def test_kosdaq_methods(mock_read_csv, mock_exists, mock_logger):
    """get_kosdaq_codes 및 is_kosdaq 메서드를 테스트합니다."""
    mock_exists.return_value = True
    data = {
        '종목코드': ['005930', '123456'],
        '종목명': ['삼성전자', '코스닥종목'],
        '시장구분': ['KOSPI', 'KOSDAQ']
    }
    mock_read_csv.return_value = pd.DataFrame(data)
    
    mapper = StockCodeMapper(logger=mock_logger)
    
    # get_kosdaq_codes
    kosdaq = mapper.get_kosdaq_codes()
    assert '123456' in kosdaq
    assert '005930' not in kosdaq
    
    # is_kosdaq
    assert mapper.is_kosdaq('123456') is True
    assert mapper.is_kosdaq('005930') is False
    assert mapper.is_kosdaq('999999') is False

@patch('os.path.exists')
@patch('pandas.read_csv')
def test_kosdaq_methods_missing_column(mock_read_csv, mock_exists, mock_logger, mock_stock_df):
    """시장구분 컬럼이 없을 때 KOSDAQ 관련 메서드가 안전하게 동작하는지 테스트합니다."""
    mock_exists.return_value = True
    # mock_stock_df에는 '시장구분' 컬럼이 없음
    mock_read_csv.return_value = mock_stock_df
    
    mapper = StockCodeMapper(logger=mock_logger)
    
    assert mapper.get_kosdaq_codes() == []
    assert mapper.is_kosdaq('005930') is False
