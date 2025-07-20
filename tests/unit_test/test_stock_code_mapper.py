import pytest
import pandas as pd
from unittest.mock import patch, MagicMock
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from app.stock_query_service import StockQueryService

# 경로 문제를 피하기 위해, 테스트 실행 시 프로젝트 루트를 기준으로 import
from market_data.stock_code_mapper import StockCodeMapper


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

@patch('pandas.read_csv')
def test_initialization_with_explicit_path(mock_read_csv, mock_stock_df, mock_logger):
    """
    명시적인 CSV 경로로 초기화가 정상적으로 동작하는지 테스트합니다.
    """
    # Arrange: pd.read_csv가 모의 데이터프레임을 반환하도록 설정
    mock_read_csv.return_value = mock_stock_df
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
    mock_logger.info.assert_called_once_with(f"🔄 종목코드 매핑 CSV 로드 완료: {csv_path}")


@patch('pandas.read_csv')
@patch('os.path.abspath')
def test_initialization_with_default_path(mock_abspath, mock_read_csv, mock_stock_df):
    """
    기본 CSV 경로로 초기화가 정상적으로 동작하는지 테스트합니다.
    """
    # Arrange
    mock_read_csv.return_value = mock_stock_df
    # os.path.abspath가 예측 가능한 경로를 반환하도록 설정
    mock_abspath.return_value = "/project_root"
    expected_path = os.path.join("/project_root", "data", "stock_code_list.csv")

    # Act
    mapper = StockCodeMapper()

    # Assert
    mock_read_csv.assert_called_once_with(expected_path, dtype={"종목코드": str})
    assert mapper.code_to_name['005930'] == '삼성전자'


@patch('pandas.read_csv')
def test_initialization_file_not_found(mock_read_csv, mock_logger):
    """
    CSV 파일을 찾지 못했을 때 예외가 발생하는지 테스트합니다.
    """
    # Arrange: pd.read_csv가 FileNotFoundError를 발생시키도록 설정
    error_message = "File not found"
    mock_read_csv.side_effect = FileNotFoundError(error_message)

    # Act & Assert
    with pytest.raises(FileNotFoundError):
        StockCodeMapper(logger=mock_logger)

    # 로거가 에러 메시지를 기록했는지 확인
    mock_logger.error.assert_called_once_with(f"❌ 종목코드 매핑 CSV 로드 실패: {error_message}")


@patch('pandas.read_csv')
def test_get_name_by_code(mock_read_csv, mock_stock_df, mock_logger):
    """get_name_by_code 메서드의 성공/실패 시나리오를 테스트합니다."""
    # Arrange
    mock_read_csv.return_value = mock_stock_df
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


@patch('pandas.read_csv')
def test_get_code_by_name(mock_read_csv, mock_stock_df, mock_logger):
    """get_code_by_name 메서드의 성공/실패 시나리오를 테스트합니다."""
    # Arrange
    mock_read_csv.return_value = mock_stock_df
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

@patch('pandas.read_csv')
def test_initialization_file_not_found_without_logger(mock_read_csv):
    """
    CSV 로드 실패 시 logger가 없더라도 정상적으로 예외가 발생하는지 테스트합니다 (line 24 분기 타기).
    """
    # Arrange: FileNotFoundError 유도
    mock_read_csv.side_effect = FileNotFoundError("파일 없음")

    # Act & Assert
    with pytest.raises(FileNotFoundError):
        StockCodeMapper(logger=None)  # logger 없이 초기화

class TestHandleYesterdayUpperLimitStocks(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.mock_trading_service = AsyncMock()
        self.mock_logger = MagicMock()
        self.print_patch = patch("builtins.print")
        self.mock_print = self.print_patch.start()

        self.service = StockQueryService(
            trading_service=self.mock_trading_service,
            logger=self.mock_logger,
            time_manager=None
        )

    async def asyncTearDown(self):
        self.print_patch.stop()

    async def test_success_case(self):
        self.mock_trading_service.get_top_market_cap_stocks_code.return_value = {
            "rt_cd": "0",
            "output": [{"mksc_shrn_iscd": "000660"}]
        }
        self.mock_trading_service.get_yesterday_upper_limit_stocks.return_value = [
            {"name": "SK하이닉스", "code": "000660", "price": 120000, "change_rate": 29.9}
        ]

        await self.service.handle_yesterday_upper_limit_stocks()

        self.mock_logger.info.assert_any_call("전일 상한가 종목 조회 성공. 총 1개")
        self.mock_print.assert_any_call("  SK하이닉스 (000660): 120000원 (등락률: +29.9%)")

    async def test_fail_market_cap_response(self):
        self.mock_trading_service.get_top_market_cap_stocks_code.return_value = {"rt_cd": "1", "msg1": "에러"}

        await self.service.handle_yesterday_upper_limit_stocks()

        self.mock_logger.warning.assert_called_once()
        self.mock_print.assert_any_call("전일 상한가 종목 조회 실패: 에러")

    async def test_empty_output(self):
        self.mock_trading_service.get_top_market_cap_stocks_code.return_value = {"rt_cd": "0", "output": []}

        await self.service.handle_yesterday_upper_limit_stocks()

        self.mock_logger.info.assert_called_with("조회된 시가총액 종목 코드 없음.")
        self.mock_print.assert_any_call("전일 상한가 종목 조회 대상이 없습니다.")

    async def test_no_upper_limit_stocks(self):
        self.mock_trading_service.get_top_market_cap_stocks_code.return_value = {
            "rt_cd": "0", "output": [{"mksc_shrn_iscd": "000660"}]
        }
        self.mock_trading_service.get_yesterday_upper_limit_stocks.return_value = []

        await self.service.handle_yesterday_upper_limit_stocks()

        self.mock_logger.info.assert_called_with("전일 상한가 종목 없음.")
        self.mock_print.assert_any_call("현재 전일 상한가에 해당하는 종목이 없습니다.")

    async def test_exception(self):
        self.mock_trading_service.get_top_market_cap_stocks_code.side_effect = Exception("예외 발생")

        await self.service.handle_yesterday_upper_limit_stocks()

        self.mock_logger.error.assert_called()
        self.mock_print.assert_any_call("전일 상한가 종목 조회 중 오류 발생: 예외 발생")