import pytest
from unittest.mock import AsyncMock, MagicMock

from brokers.korea_investment.korea_invest_account_api import KoreaInvestApiAccount
from tests.unit_test.test_time_manager import time_manager


def get_api():
    mock_logger = MagicMock()
    mock_env = MagicMock()
    mock_time_manager = AsyncMock()

    return  KoreaInvestApiAccount(
        env=mock_env,
        logger=mock_logger,
        time_manager=mock_time_manager,
    )

@pytest.mark.asyncio
async def test_get_account_balance():
    api = get_api()
    api._env.is_paper_trading = True

    api.call_api = AsyncMock(return_value={'result': 'success'})

    result = await api.get_account_balance()

    assert result == {'result': 'success'}
    api._logger.info.assert_called()

@pytest.mark.asyncio
async def test_get_real_account_balance_with_dash():
    api = get_api()
    api._env.is_paper_trading = False

    api.call_api = AsyncMock(return_value={'result': 'real_success'})

    result = await api.get_account_balance()

    assert result == {'result': 'real_success'}
    api._logger.info.assert_called_with("실전투자 계좌 잔고 조회 시도...")

@pytest.mark.asyncio
async def test_get_real_account_balance_without_dash():
    api = get_api()
    api._env.is_paper_trading = False

    api.call_api = AsyncMock(return_value={'result': 'real_success_without_dash'})

    result = await api.get_account_balance()

    assert result == {'result': 'real_success_without_dash'}
    api._logger.info.assert_called_with("실전투자 계좌 잔고 조회 시도...")
