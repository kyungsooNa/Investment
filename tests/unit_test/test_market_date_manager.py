import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from datetime import datetime, timedelta
from managers.market_date_manager import MarketDateManager
from common.types import ResCommonResponse

@pytest.fixture
def mock_deps():
    tm = MagicMock()
    # Default time
    tm.get_current_kst_time.return_value = datetime(2025, 1, 1, 12, 0, 0)
    logger = MagicMock()
    return tm, logger

@pytest.fixture
def manager(mock_deps):
    tm, logger = mock_deps
    return MarketDateManager(tm, logger)

@pytest.fixture
def mock_broker():
    broker = MagicMock()
    # Mocking the structure: broker._client._quotations._client or broker._client._quotations
    
    # Case 1: With ClientWithCache wrapper
    # broker._client (KoreaInvestApiClient)
    #   ._quotations (ClientWithCache)
    #     ._client (KoreaInvestApiQuotations) -> This is what we need to mock inquire_daily_itemchartprice on
    
    raw_quotations = AsyncMock()
    
    quotations_wrapper = MagicMock()
    quotations_wrapper._client = raw_quotations
    
    kis_client = MagicMock()
    kis_client._quotations = quotations_wrapper
    
    # BrokerAPIWrapper wraps the client.
    # broker._client is the wrapper.
    # wrapper._client is the actual KoreaInvestApiClient (kis_client).
    client_wrapper = MagicMock()
    client_wrapper._client = kis_client
    broker._client = client_wrapper
    
    return broker, raw_quotations

@pytest.mark.asyncio
async def test_get_latest_trading_date_cached(manager, mock_deps):
    tm, _ = mock_deps
    # Setup cache
    manager._cached_date = "20250101"
    manager._last_check_date = "20250101"
    
    # Current date matches last check date
    tm.get_current_kst_time.return_value = datetime(2025, 1, 1, 15, 0, 0)
    
    result = await manager.get_latest_trading_date()
    assert result == "20250101"
    # Broker should not be accessed if cached
    assert manager._broker is None 

@pytest.mark.asyncio
async def test_get_latest_trading_date_no_broker(manager, mock_deps):
    tm, logger = mock_deps
    tm.get_current_kst_time.return_value = datetime(2025, 1, 2, 12, 0, 0)
    
    result = await manager.get_latest_trading_date()
    
    assert result is None
    logger.warning.assert_called_with("MarketDateManager: Broker is not set.")

@pytest.mark.asyncio
async def test_get_latest_trading_date_api_success(manager, mock_deps, mock_broker):
    tm, logger = mock_deps
    broker, raw_quotations = mock_broker
    
    manager.set_broker(broker)
    tm.get_current_kst_time.return_value = datetime(2025, 1, 2, 12, 0, 0)
    
    # Mock API response
    mock_resp = ResCommonResponse(
        rt_cd="0", msg1="OK", 
        data=[
            {"stck_bsop_date": "20250102"},
            {"stck_bsop_date": "20241231"}
        ]
    )
    raw_quotations.inquire_daily_itemchartprice.return_value = mock_resp
    
    result = await manager.get_latest_trading_date()
    
    assert result == "20250102"
    assert manager._cached_date == "20250102"
    assert manager._last_check_date == "20250102"
    
    # Verify API call arguments
    # start_dt = 20250102 - 7 days = 20241226
    raw_quotations.inquire_daily_itemchartprice.assert_awaited_once_with(
        "005930", "20241226", "20250102", "D"
    )

@pytest.mark.asyncio
async def test_get_latest_trading_date_api_fail(manager, mock_deps, mock_broker):
    tm, logger = mock_deps
    broker, raw_quotations = mock_broker
    
    manager.set_broker(broker)
    tm.get_current_kst_time.return_value = datetime(2025, 1, 2, 12, 0, 0)
    
    # Mock API response failure
    mock_resp = ResCommonResponse(rt_cd="1", msg1="Fail", data=None)
    raw_quotations.inquire_daily_itemchartprice.return_value = mock_resp
    
    result = await manager.get_latest_trading_date()
    
    assert result is None
    assert manager._cached_date is None

@pytest.mark.asyncio
async def test_get_latest_trading_date_exception(manager, mock_deps, mock_broker):
    tm, logger = mock_deps
    broker, raw_quotations = mock_broker
    
    manager.set_broker(broker)
    tm.get_current_kst_time.return_value = datetime(2025, 1, 2, 12, 0, 0)
    
    # Mock Exception
    raw_quotations.inquire_daily_itemchartprice.side_effect = Exception("API Error")
    
    result = await manager.get_latest_trading_date()
    
    assert result is None
    # Check if warning logged in _fetch_from_api
    logger.warning.assert_called()

def test_init_default_logger(mock_deps):
    """로거 없이 초기화 시 기본 로거 생성 확인"""
    tm, _ = mock_deps
    manager = MarketDateManager(tm)
    assert manager._logger is not None

@pytest.mark.asyncio
async def test_fetch_from_api_broker_structure_mismatch(manager, mock_deps):
    """Broker 구조가 예상과 다를 때 (속성 누락) None 반환"""
    tm, logger = mock_deps
    
    # 1. Broker has no _client
    broker = MagicMock()
    del broker._client 
    manager.set_broker(broker)
    
    result = await manager.get_latest_trading_date()
    assert result is None
    
    # 2. Broker has _client but no _quotations
    broker = MagicMock()
    broker._client = MagicMock()
    del broker._client._quotations
    manager.set_broker(broker)
    
    result = await manager.get_latest_trading_date()
    assert result is None

@pytest.mark.asyncio
async def test_fetch_from_api_not_wrapped(manager, mock_deps):
    """ClientWithCache로 래핑되지 않은 경우 직접 사용"""
    tm, logger = mock_deps
    
    # Raw quotations mock
    raw_quotations = AsyncMock()
    # Ensure it doesn't look like a wrapper (no _client attribute)
    del raw_quotations._client
    
    mock_resp = ResCommonResponse(
        rt_cd="0", msg1="OK", 
        data=[{"stck_bsop_date": "20250105"}]
    )
    raw_quotations.inquire_daily_itemchartprice.return_value = mock_resp
    
    # Broker setup
    kis_client = MagicMock()
    del kis_client._client
    kis_client._quotations = raw_quotations
    broker = MagicMock()
    broker._client = kis_client
    
    manager.set_broker(broker)
    
    result = await manager.get_latest_trading_date()
    assert result == "20250105"

@pytest.mark.asyncio
async def test_fetch_from_api_empty_data(manager, mock_deps, mock_broker):
    """API 응답 데이터가 비어있을 때"""
    tm, logger = mock_deps
    broker, raw_quotations = mock_broker
    manager.set_broker(broker)
    
    # Empty data list
    mock_resp = ResCommonResponse(rt_cd="0", msg1="OK", data=[])
    raw_quotations.inquire_daily_itemchartprice.return_value = mock_resp
    
    result = await manager.get_latest_trading_date()
    assert result is None

@pytest.mark.asyncio
async def test_get_latest_trading_date_outer_exception(manager, mock_deps):
    """get_latest_trading_date 내부 try-except 블록 테스트"""
    tm, logger = mock_deps
    
    # Broker 설정 (초기 체크 통과용)
    manager.set_broker(MagicMock())
    
    # _fetch_from_api가 예외를 던지도록 모킹 (내부 예외 처리가 아닌 외부 예외 처리 테스트)
    with patch.object(manager, '_fetch_from_api', side_effect=Exception("Critical Error")):
        result = await manager.get_latest_trading_date()
        
        assert result is None
        logger.error.assert_called()
        assert "Failed to fetch latest trading date" in logger.error.call_args[0][0]