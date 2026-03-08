import pytest
from unittest.mock import MagicMock, AsyncMock
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
    
    broker._client = kis_client
    
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