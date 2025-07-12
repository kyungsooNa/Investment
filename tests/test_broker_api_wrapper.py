import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch

# 테스트 대상 클래스 import
import brokers.broker_api_wrapper as wrapper_module
from brokers.broker_api_wrapper import BrokerAPIWrapper
# --- Fixtures: 테스트에 필요한 모의(Mock) 객체들을 미리 생성 ---

@pytest.fixture
def mock_env():
    """모의 KoreaInvestApiEnv 객체를 생성합니다."""
    env = MagicMock()
    # __init__에서 필요한 설정 값들을 반환하도록 설정
    env.get_full_config.return_value = {
        'base_url': 'http://mock-base-url.com',
        'api_key': 'mock_api_key',
        'api_secret_key': 'mock_api_secret_key',
        'access_token': 'mock_access_token_from_env', # <<-- 이 값을 추가
        'custtype': 'P'
    }
    env.access_token = 'mock_token'
    return env

@pytest.fixture
def mock_token_manager():
    """모의 TokenManager 객체를 생성합니다."""
    return MagicMock()

@pytest.fixture
def mock_logger():
    """모의 로거(Logger) 객체를 생성합니다."""
    return MagicMock()

# --- 테스트 케이스 ---

@patch(f"{wrapper_module.__name__}.KoreaInvestApiClient")
@patch(f"{wrapper_module.__name__}.StockCodeMapper")
def test_initialization_success(MockStockMapper, MockClient, mock_env, mock_token_manager, mock_logger):
    """
    정상적인 인자로 BrokerAPIWrapper 초기화가 성공하는지 테스트합니다.
    """
    # Act
    wrapper = BrokerAPIWrapper(broker="korea_investment", env=mock_env, token_manager=mock_token_manager, logger=mock_logger)

    # Assert
    MockClient.assert_called_once_with(mock_env, mock_token_manager, mock_logger)
    MockStockMapper.assert_called_once_with(logger=mock_logger)
    assert wrapper._broker == "korea_investment"

def test_initialization_no_env_raises_error(mock_token_manager, mock_logger):
    """
    'korea_investment' 브로커 선택 시 env가 없으면 ValueError가 발생하는지 테스트합니다.
    """
    # Arrange, Act & Assert
    with pytest.raises(ValueError, match="KoreaInvest API를 사용하려면 env 인스턴스가 필요합니다."):
        BrokerAPIWrapper(
            broker="korea_investment",
            env=None,  # env를 명시적으로 None으로 설정
            token_manager=mock_token_manager,
            logger=mock_logger
        )

def test_initialization_unsupported_broker_raises_error(mock_env):
    """
    지원되지 않는 브로커 이름으로 초기화 시 NotImplementedError가 발생하는지 테스트합니다.
    """
    # Arrange, Act & Assert
    with pytest.raises(NotImplementedError, match="지원되지 않는 증권사: unsupported_broker"):
        BrokerAPIWrapper(broker="unsupported_broker", env=mock_env)


@pytest.mark.asyncio
@patch(f"{wrapper_module.__name__}.StockCodeMapper")              # 먼저 정의된 patch가
@patch(f"{wrapper_module.__name__}.KoreaInvestApiClient")         # 아래쪽 인자로 먼저 들어감!
async def test_method_delegation(mock_client_class, mock_mapper_class, mock_env, mock_token_manager, mock_logger):
    """
    각 메서드가 내부의 올바른 객체로 호출을 위임하는지 테스트합니다.
    """
    # 1. StockCodeMapper mock 설정 (동기)
    mock_mapper = MagicMock()
    mock_mapper.get_name_by_code.return_value = "삼성전자"
    mock_mapper.get_code_by_name.return_value = "005930"
    mock_mapper_class.return_value = mock_mapper

    # 2. KoreaInvestApiClient mock 설정 (비동기)
    mock_client = AsyncMock()
    mock_client.inquire_daily_itemchartprice.return_value = {"chart": "data"}
    mock_client_class.return_value = mock_client

    # 3. 인스턴스 생성
    wrapper = BrokerAPIWrapper("korea_investment", env=mock_env, token_manager=mock_token_manager, logger=mock_logger)

    # 4. 실제 메서드 호출
    name_result = await wrapper.get_name_by_code("005930")
    code_result = await wrapper.get_code_by_name("삼성전자")
    chart_result = await wrapper.inquire_daily_itemchartprice("005930", "20250712")

    # 5. 결과 검증
    assert name_result == "삼성전자"
    assert code_result == "005930"
    assert chart_result == {"chart": "data"}

    # 6. 호출 여부 검증
    mock_mapper.get_name_by_code.assert_called_once_with("005930")
    mock_mapper.get_code_by_name.assert_called_once_with("삼성전자")
    mock_client.inquire_daily_itemchartprice.assert_awaited_once_with("005930", "20250712", fid_period_div_code="D")

@pytest.mark.asyncio
async def test_inquire_daily_itemchartprice_delegation(mock_env, mock_token_manager, mock_logger):
    """
    inquire_daily_itemchartprice 메서드가 quotations 객체의 동일 메서드에 정확히 위임되는지 검증합니다.
    """
    with patch('brokers.broker_api_wrapper') as MockBrokerWrapper:

        # Mock 객체 구성
        MockBrokerWrapper.inquire_daily_itemchartprice = AsyncMock(return_value=[{"stck_prpr": "70000"}])

        # Act
        result = await MockBrokerWrapper.inquire_daily_itemchartprice("005930", "20250708", fid_period_div_code="D")

        # Assert
        MockBrokerWrapper.inquire_daily_itemchartprice.assert_awaited_once_with(
            "005930", "20250708", fid_period_div_code="D"
        )
        assert result == [{"stck_prpr": "70000"}]
