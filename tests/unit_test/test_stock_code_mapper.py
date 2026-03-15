# tests\unit_test\test_stock_code_mapper.py

import pytest
import pandas as pd
import sqlite3
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch, call
from services.stock_query_service import StockQueryService

# 경로 문제를 피하기 위해, 테스트 실행 시 프로젝트 루트를 기준으로 import
from market_data.stock_code_mapper import StockCodeMapper, _write_minimal_db, TABLE_NAME
from common.types import ErrorCode, ResCommonResponse, ResTopMarketCapApiItem


# --- 헬퍼: 테스트용 SQLite DB 생성 ---

def _create_test_db(db_path, data=None):
    """테스트용 SQLite DB를 생성합니다."""
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    if data is None:
        data = {
            '종목코드': ['005930', '000660', '035720'],
            '종목명': ['삼성전자', 'SK하이닉스', '카카오'],
        }
    df = pd.DataFrame(data)
    df.to_sql(TABLE_NAME, conn, if_exists="replace", index=False)
    conn.commit()
    conn.close()


# --- 테스트를 위한 모의(Mock) 데이터 및 Fixture ---

@pytest.fixture
def mock_logger():
    """테스트용 모의 로거(Logger) 객체를 생성하는 Fixture."""
    return MagicMock()


@pytest.fixture
def test_db(tmp_path):
    """테스트용 SQLite DB 경로를 생성하고 기본 데이터를 넣습니다."""
    db_path = str(tmp_path / "stock_code_list.db")
    _create_test_db(db_path)
    return db_path


@pytest.fixture
def test_db_with_market(tmp_path):
    """시장구분 포함 테스트용 DB."""
    db_path = str(tmp_path / "stock_code_list.db")
    _create_test_db(db_path, {
        '종목코드': ['005930', '123456'],
        '종목명': ['삼성전자', '코스닥종목'],
        '시장구분': ['KOSPI', 'KOSDAQ'],
    })
    return db_path


# --- StockCodeMapper 클래스 테스트 ---

def test_initialization_with_explicit_path(test_db, mock_logger):
    """
    명시적인 DB 경로로 초기화가 정상적으로 동작하는지 테스트합니다.
    """
    mapper = StockCodeMapper(db_path=test_db, logger=mock_logger)

    assert mapper.code_to_name['005930'] == '삼성전자'
    assert mapper.name_to_code['SK하이닉스'] == '000660'

    calls = [call.args[0] for call in mock_logger.info.call_args_list]
    assert any("DB 로드 완료" in c for c in calls)


def test_initialization_with_default_path(mock_logger):
    """
    기본 DB 경로로 초기화가 정상적으로 동작하는지 테스트합니다.
    """
    with patch('os.path.abspath') as mock_abspath, \
         patch('os.path.exists', return_value=True), \
         patch('pandas.read_sql') as mock_read_sql:
        mock_abspath.return_value = "/project_root"
        mock_read_sql.return_value = pd.DataFrame({
            '종목코드': ['005930'], '종목명': ['삼성전자']
        })

        mapper = StockCodeMapper()

        assert mapper.code_to_name['005930'] == '삼성전자'


@patch('market_data.stock_code_mapper.save_stock_code_list')
def test_initialization_file_not_found(mock_save_stock_code_list, mock_logger, tmp_path):
    """
    DB 파일을 찾지 못하고 생성에도 실패했을 때 예외가 발생하는지 테스트합니다.
    """
    db_path = str(tmp_path / "nonexistent" / "stock_code_list.db")
    error_message = "File generation failed"
    mock_save_stock_code_list.side_effect = FileNotFoundError(error_message)

    with pytest.raises(FileNotFoundError):
        StockCodeMapper(db_path=db_path, logger=mock_logger)

    mock_logger.error.assert_called_once_with(f"❌ 종목코드 매핑 DB 파일 생성 실패: {error_message}")


def test_get_name_by_code(test_db, mock_logger):
    """get_name_by_code 메서드의 성공/실패 시나리오를 테스트합니다."""
    mapper = StockCodeMapper(db_path=test_db, logger=mock_logger)

    # 성공
    assert mapper.get_name_by_code('005930') == '삼성전자'

    # 실패
    assert mapper.get_name_by_code('999999') == ""
    mock_logger.warning.assert_called_once_with("❗ 종목명 없음: 999999")


def test_get_code_by_name(test_db, mock_logger):
    """get_code_by_name 메서드의 성공/실패 시나리오를 테스트합니다."""
    mapper = StockCodeMapper(db_path=test_db, logger=mock_logger)

    # 성공
    assert mapper.get_code_by_name('카카오') == '035720'

    # 실패
    assert mapper.get_code_by_name('없는회사') == ""
    mock_logger.warning.assert_called_once_with("❗ 종목코드 없음: 없는회사")


@patch('market_data.stock_code_mapper.save_stock_code_list')
def test_initialization_file_not_found_without_logger(mock_save, tmp_path):
    """
    DB 로드 실패 시 logger가 없더라도 정상적으로 예외가 발생하는지 테스트합니다.
    """
    db_path = str(tmp_path / "nonexistent" / "stock_code_list.db")
    mock_save.side_effect = FileNotFoundError("파일 없음")

    with pytest.raises(FileNotFoundError):
        StockCodeMapper(db_path=db_path, logger=None)


# --- 추가 테스트 케이스 (Coverage 향상) ---

def test_write_minimal_db(tmp_path):
    """_write_minimal_db 함수가 정상적으로 최소 DB를 생성하는지 테스트합니다."""
    db_path = str(tmp_path / "minimal.db")
    logger = MagicMock()

    _write_minimal_db(db_path, logger)

    assert os.path.exists(db_path)
    conn = sqlite3.connect(db_path)
    df = pd.read_sql(f"SELECT * FROM {TABLE_NAME}", conn, dtype={"종목코드": str})
    conn.close()
    assert not df.empty
    assert "종목코드" in df.columns
    assert df.iloc[0]["종목코드"] == "000000"
    logger.warning.assert_called_once()


@patch('market_data.stock_code_mapper.save_stock_code_list')
def test_init_creates_file_success(mock_save, mock_logger, tmp_path):
    """DB 파일이 없을 때 생성을 시도하고 성공하는 케이스를 테스트합니다."""
    db_path = str(tmp_path / "stock_code_list.db")

    def create_db_side_effect(**kwargs):
        _create_test_db(db_path)

    mock_save.side_effect = create_db_side_effect

    mapper = StockCodeMapper(db_path=db_path, logger=mock_logger)

    mock_save.assert_called_once_with(force_update=True)
    assert any("생성 완료" in call.args[0] for call in mock_logger.info.call_args_list)


@patch('market_data.stock_code_mapper.save_stock_code_list')
def test_load_data_empty_db_recovery_success(mock_save, mock_logger, tmp_path):
    """DB가 비어있을 때 갱신을 시도하여 성공하는 케이스를 테스트합니다."""
    db_path = str(tmp_path / "stock_code_list.db")

    # 빈 DB 생성 (테이블 없음)
    conn = sqlite3.connect(db_path)
    conn.close()

    def create_db_side_effect(**kwargs):
        _create_test_db(db_path)

    mock_save.side_effect = create_db_side_effect

    mapper = StockCodeMapper(db_path=db_path, logger=mock_logger)

    mock_save.assert_called_once_with(force_update=True)
    assert mapper.code_to_name['005930'] == '삼성전자'
    assert any("비어 있음" in call.args[0] for call in mock_logger.warning.call_args_list)


@patch('market_data.stock_code_mapper._write_minimal_db')
@patch('market_data.stock_code_mapper.save_stock_code_list')
def test_load_data_empty_db_recovery_fail_minimal(mock_save, mock_write_minimal, mock_logger, tmp_path):
    """DB가 비어있고 갱신도 실패했을 때 최소 DB를 사용하는 케이스를 테스트합니다."""
    db_path = str(tmp_path / "stock_code_list.db")

    # 빈 DB 생성
    conn = sqlite3.connect(db_path)
    conn.close()

    mock_save.side_effect = Exception("Update Failed")

    def write_minimal_side_effect(path, logger=None):
        _write_minimal_db(path, logger=None)  # 실제 최소 DB 생성 (로그 없이)

    mock_write_minimal.side_effect = write_minimal_side_effect

    mapper = StockCodeMapper(db_path=db_path, logger=mock_logger)

    mock_write_minimal.assert_called_once()
    assert mapper.code_to_name['000000'] == '(종목목록 없음)'
    assert any("최소 DB" in call.args[0] for call in mock_logger.warning.call_args_list)


def test_load_data_generic_exception(mock_logger, tmp_path):
    """일반적인 에러 발생 시 예외를 다시 던지는지 테스트합니다."""
    db_path = str(tmp_path / "stock_code_list.db")

    # 정상 DB이지만 잘못된 테이블 구조 (read_sql 시 키 에러 발생)
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE stocks (wrong_col TEXT)")
    conn.execute("INSERT INTO stocks VALUES ('x')")
    conn.commit()
    conn.close()

    with pytest.raises(Exception):
        StockCodeMapper(db_path=db_path, logger=mock_logger)

    mock_logger.error.assert_called()


def test_kosdaq_methods(test_db_with_market, mock_logger):
    """get_kosdaq_codes 및 is_kosdaq 메서드를 테스트합니다."""
    mapper = StockCodeMapper(db_path=test_db_with_market, logger=mock_logger)

    # get_kosdaq_codes
    kosdaq = mapper.get_kosdaq_codes()
    assert '123456' in kosdaq
    assert '005930' not in kosdaq

    # is_kosdaq
    assert mapper.is_kosdaq('123456') is True
    assert mapper.is_kosdaq('005930') is False
    assert mapper.is_kosdaq('999999') is False


def test_kosdaq_methods_missing_column(test_db, mock_logger):
    """시장구분 컬럼이 없을 때 KOSDAQ 관련 메서드가 안전하게 동작하는지 테스트합니다."""
    mapper = StockCodeMapper(db_path=test_db, logger=mock_logger)

    assert mapper.get_kosdaq_codes() == []
    assert mapper.is_kosdaq('005930') is False
