import pytest
from unittest.mock import MagicMock, AsyncMock
from services.trading_service import TradingService

@pytest.fixture
def mock_deps(mocker):
    """TradingService의 의존성 Mock 객체 생성"""
    broker = AsyncMock()
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

@pytest.mark.asyncio
async def test_subscribe_program_trading_invalid_code(trading_service):
    """[의미 있는 테스트] 종목 코드 누락(빈 문자열) 시 차단 검증"""
    invalid_code = ""
    
    # 만약 서비스에 검증 로직이 있다면 False를 반환해야 함
    result = await trading_service.subscribe_program_trading(invalid_code)
    
    assert result is False
    # 핵심: 잘못된 입력이므로 하위 브로커 API를 아예 호출하지 않아야 함 (자원 절약)
    trading_service._broker_api_wrapper.subscribe_program_trading.assert_not_called()

@pytest.mark.asyncio
async def test_subscribe_program_trading_exception_handling(trading_service):
    """[의미 있는 테스트] 브로커 API에서 예외 발생 시 서비스가 죽지 않고 False를 반환하는지 검증"""
    code = "005930"
    trading_service._broker_api_wrapper.subscribe_program_trading.side_effect = Exception("Connection Timeout")
    
    result = await trading_service.subscribe_program_trading(code)
    
    assert result is False
    # 서비스 레이어에서 에러 로그를 남겼는지 확인
    trading_service._logger.exception.assert_called()