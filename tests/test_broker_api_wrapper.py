import pytest
from unittest.mock import MagicMock, AsyncMock, patch

# 테스트 대상 클래스 import
from user_api.broker_api_wrapper import BrokerAPIWrapper

# --- Fixtures: 테스트에 필요한 모의(Mock) 객체들을 미리 생성 ---

@pytest.fixture
def mock_env():
    """모의 KoreaInvestApiEnv 객체를 생성합니다."""
    env = MagicMock()
    # __init__에서 필요한 설정 값들을 반환하도록 설정
    env.get_full_config.return_value = {
        'base_url': 'https://mock-url.com',
        'api_key': 'mock_key',
        'api_secret_key': 'mock_secret',
        'custtype': 'P',
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

def test_initialization_success(mock_env, mock_token_manager, mock_logger):
    """
    정상적인 인자로 BrokerAPIWrapper 초기화가 성공하는지 테스트합니다.
    """
    # Arrange: 모든 하위 API 클래스와 StockCodeMapper를 모킹(patch)합니다.
    with patch('user_api.broker_api_wrapper.KoreaInvestApiAccount') as MockAccount, \
         patch('user_api.broker_api_wrapper.KoreaInvestApiTrading') as MockTrading, \
         patch('user_api.broker_api_wrapper.KoreaInvestApiQuotations') as MockQuotations, \
         patch('user_api.broker_api_wrapper.StockCodeMapper') as MockMapper:

        # Act: BrokerAPIWrapper 인스턴스 생성
        wrapper = BrokerAPIWrapper(
            env=mock_env,
            token_manager=mock_token_manager,
            logger=mock_logger
        )

        # Assert: 모든 내부 객체들이 올바른 인자들로 한 번씩 초기화되었는지 확인
        MockAccount.assert_called_once()
        MockTrading.assert_called_once()
        MockQuotations.assert_called_once()
        MockMapper.assert_called_once_with(logger=mock_logger)
        assert wrapper.broker == "korea_investment"

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
async def test_method_delegation(mock_env, mock_token_manager, mock_logger):
    """
    각 메서드가 내부의 올바른 객체로 호출을 위임하는지 테스트합니다.
    """
    # Arrange: 모든 하위 API 클래스와 StockCodeMapper를 모킹(patch)합니다.
    with patch('user_api.broker_api_wrapper.KoreaInvestApiAccount') as MockAccount, \
         patch('user_api.broker_api_wrapper.KoreaInvestApiTrading') as MockTrading, \
         patch('user_api.broker_api_wrapper.KoreaInvestApiQuotations') as MockQuotations, \
         patch('user_api.broker_api_wrapper.StockCodeMapper') as MockMapper:

        # 각 모의 클래스의 인스턴스가 비동기 메서드를 가질 수 있도록 설정
        MockAccount.return_value.get_account_balance = AsyncMock()
        MockTrading.return_value.buy = AsyncMock()
        MockTrading.return_value.sell = AsyncMock()
        MockQuotations.return_value.get_price_summary = AsyncMock()
        MockQuotations.return_value.get_market_cap = AsyncMock()
        # StockCodeMapper의 메서드는 동기이므로 MagicMock으로 설정
        MockMapper.return_value.get_name_by_code = MagicMock(return_value="삼성전자")
        MockMapper.return_value.get_code_by_name = MagicMock(return_value="005930")

        # Act
        wrapper = BrokerAPIWrapper(
            env=mock_env,
            token_manager=mock_token_manager,
            logger=mock_logger
        )

        # 각 메서드를 호출
        await wrapper.get_name_by_code("005930")
        await wrapper.get_code_by_name("삼성전자")
        await wrapper.get_balance()
        await wrapper.buy_stock("005930", 10, 70000)
        await wrapper.sell_stock("000660", 5, 150000)
        await wrapper.get_price_summary("035720")
        await wrapper.get_market_cap("035420")

        # Assert: 각 메서드가 정확히 한 번씩, 올바른 인자로 호출/await 되었는지 확인
        MockMapper.return_value.get_name_by_code.assert_called_once_with("005930")
        MockMapper.return_value.get_code_by_name.assert_called_once_with("삼성전자")
        MockAccount.return_value.get_account_balance.assert_awaited_once()
        MockTrading.return_value.buy.assert_awaited_once_with("005930", 10, 70000)
        MockTrading.return_value.sell.assert_awaited_once_with("000660", 5, 150000)
        MockQuotations.return_value.get_price_summary.assert_awaited_once_with("035720")
        MockQuotations.return_value.get_market_cap.assert_awaited_once_with("035420")