import pytest
from unittest.mock import MagicMock, AsyncMock
from services.trading_service import TradingService

@pytest.fixture
def mock_deps(mocker):
    """TradingService의 의존성 Mock 객체 생성"""
    broker = mocker.Mock()
    env = mocker.Mock()
    logger = mocker.Mock()
    time_manager = mocker.Mock()
    return broker, env, logger, time_manager

@pytest.fixture
def trading_service(mock_deps):
    """TradingService 인스턴스 생성"""
    broker, env, logger, time_manager = mock_deps
    return TradingService(broker, env, logger, time_manager)

@pytest.mark.asyncio
async def test_start_program_trading_success(trading_service):
    """프로그램 매매 구독 시작 성공 테스트"""
    code = "005930"
    trading_service._broker_api_wrapper.subscribe_program_trading = AsyncMock(return_value=True)

    result = await trading_service.subscribe_program_trading(code)

    assert result is True
    trading_service._broker_api_wrapper.subscribe_program_trading.assert_awaited_once_with(code)

@pytest.mark.asyncio
async def test_start_program_trading_failure(trading_service):
    """프로그램 매매 구독 시작 실패 테스트"""
    code = "005930"
    trading_service._broker_api_wrapper.subscribe_program_trading = AsyncMock(return_value=False)

    result = await trading_service.subscribe_program_trading(code)

    assert result is False
    trading_service._broker_api_wrapper.subscribe_program_trading.assert_awaited_once_with(code)

@pytest.mark.asyncio
async def test_stop_program_trading_success(trading_service):
    """프로그램 매매 구독 해지 성공 테스트"""
    code = "005930"
    trading_service._broker_api_wrapper.unsubscribe_program_trading = AsyncMock(return_value=True)

    result = await trading_service.unsubscribe_program_trading(code)

    assert result is True
    trading_service._broker_api_wrapper.unsubscribe_program_trading.assert_awaited_once_with(code)