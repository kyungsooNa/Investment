import pytest
import pandas as pd
from unittest.mock import patch, MagicMock
from utils.kr_stock_data import StockCodeNameResolver

# --- 테스트를 위한 모의(Mock) 데이터프레임 생성 ---
@pytest.fixture
def mock_stock_df():
    """테스트용 가짜 주식 코드/이름 데이터프레임을 생성합니다."""
    data = {
        'code': ['005930', '000660', '035720'],
        'name': ['삼성전자', 'SK하이닉스', '카카오']
    }
    return pd.DataFrame(data)


# --- StockCodeNameResolver 클래스 테스트 ---

@patch('pandas.read_csv')
def test_initialization(mock_read_csv, mock_stock_df):
    """
    클래스 초기화 시 read_csv를 호출하고, code/name 맵이 잘 생성되는지 테스트합니다.
    """
    # Arrange: pandas.read_csv가 모의 데이터프레임을 반환하도록 설정
    mock_read_csv.return_value = mock_stock_df

    # Act
    resolver = StockCodeNameResolver()

    # Assert
    # 1. read_csv가 "data/stock_code_list.csv" 인자와 함께 호출되었는지 확인
    mock_read_csv.assert_called_once_with("data/stock_code_list.csv", dtype=str)

    # 2. 내부 딕셔너리가 정확히 생성되었는지 확인
    assert len(resolver.code_to_name) == 3
    assert resolver.code_to_name['005930'] == '삼성전자'
    assert resolver.name_to_code['SK하이닉스'] == '000660'


@patch('pandas.read_csv')
def test_get_name_success(mock_read_csv, mock_stock_df):
    """get_name 메서드가 올바른 주식 코드로 이름을 잘 반환하는지 테스트합니다."""
    # Arrange
    mock_read_csv.return_value = mock_stock_df
    resolver = StockCodeNameResolver()

    # Act
    result = resolver.get_name('005930')

    # Assert
    assert result == '삼성전자'


@patch('pandas.read_csv')
def test_get_name_failure(mock_read_csv, mock_stock_df):
    """get_name 메서드가 존재하지 않는 코드로 None을 반환하는지 테스트합니다."""
    # Arrange
    mock_read_csv.return_value = mock_stock_df
    resolver = StockCodeNameResolver()

    # Act
    result = resolver.get_name('999999') # 존재하지 않는 코드

    # Assert
    assert result is None


@patch('pandas.read_csv')
def test_get_code_success(mock_read_csv, mock_stock_df):
    """get_code 메서드가 올바른 이름으로 주식 코드를 잘 반환하는지 테스트합니다."""
    # Arrange
    mock_read_csv.return_value = mock_stock_df
    resolver = StockCodeNameResolver()

    # Act
    result = resolver.get_code('카카오')

    # Assert
    assert result == '035720'


@patch('pandas.read_csv')
def test_get_code_failure(mock_read_csv, mock_stock_df):
    """get_code 메서드가 존재하지 않는 이름으로 None을 반환하는지 테스트합니다."""
    # Arrange
    mock_read_csv.return_value = mock_stock_df
    resolver = StockCodeNameResolver()

    # Act
    result = resolver.get_code('없는회사') # 존재하지 않는 이름

    # Assert
    assert result is None