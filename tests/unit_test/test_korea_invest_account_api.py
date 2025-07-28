import pytest
from unittest.mock import AsyncMock, MagicMock

from brokers.korea_investment.korea_invest_account_api import KoreaInvestApiAccount

@pytest.mark.asyncio
async def test_get_account_balance():
    # Mock config
    mock_config = {
        'tr_ids': {'account': {'inquire_balance_paper': 'mock_tr_id'}},
        'custtype': 'P',
        'stock_account_number': '12345678'
    }

    mock_logger = MagicMock()
    mock_env = MagicMock()
    mock_api = KoreaInvestApiAccount(
        env=mock_env,
        logger=mock_logger
    )
    mock_api.call_api = AsyncMock(return_value={'result': 'success'})

    result = await mock_api.get_account_balance()

    assert result == {'result': 'success'}
    mock_logger.info.assert_called()

@pytest.mark.asyncio
async def test_get_real_account_balance_with_dash():
    mock_config = {
        'tr_ids': {'account': {'inquire_balance_real': 'real_tr_id'}},
        'custtype': 'P',
        'stock_account_number': '12345678-01'
    }

    mock_env = MagicMock()
    mock_logger = MagicMock()
    mock_api = KoreaInvestApiAccount(
        env=mock_env,
        logger=mock_logger
    )
    mock_api.call_api = AsyncMock(return_value={'result': 'real_success'})

    result = await mock_api.get_real_account_balance()

    assert result == {'result': 'real_success'}
    mock_logger.info.assert_called_with("실전 계좌 잔고 조회 시도...")

@pytest.mark.asyncio
async def test_get_real_account_balance_without_dash():
    mock_config = {
        'tr_ids': {'account': {'inquire_balance_real': 'real_tr_id'}},
        'custtype': 'P',
        'stock_account_number': '12345678'
    }

    mock_env = MagicMock()
    mock_logger = MagicMock()
    mock_api = KoreaInvestApiAccount(
        env=mock_env,
        logger=mock_logger
    )
    mock_api.call_api = AsyncMock(return_value={'result': 'real_success_without_dash'})

    result = await mock_api.get_real_account_balance()

    assert result == {'result': 'real_success_without_dash'}
    mock_logger.info.assert_called_with("실전 계좌 잔고 조회 시도...")
