import pytest
from unittest.mock import AsyncMock, MagicMock
from app.transaction_handlers import TransactionHandlers

@pytest.mark.asyncio
async def test_handle_buy_order_when_market_closed():
    mock_logger = MagicMock()
    mock_time_manager = MagicMock()
    mock_time_manager.is_market_open.return_value = False

    mock_trading_service = MagicMock()

    handlers = TransactionHandlers(
        trading_service=mock_trading_service,  # ✅ 정확한 인자
        logger=mock_logger,
        time_manager=mock_time_manager
    )

    await handlers.handle_place_buy_order("005930", "70000", "10", "00")

    mock_logger.warning.assert_any_call("시장이 닫혀 있어 매수 주문을 제출하지 못했습니다.")
