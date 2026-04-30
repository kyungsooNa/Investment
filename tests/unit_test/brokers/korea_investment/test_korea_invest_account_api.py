import pytest
from unittest.mock import AsyncMock, MagicMock

from brokers.korea_investment.korea_invest_account_api import KoreaInvestApiAccount
from brokers.korea_investment.korea_invest_url_keys import EndpointKey
from common.types import Exchange


def get_api():
    mock_logger = MagicMock()
    mock_env = MagicMock()
    mock_market_clock = AsyncMock()
    mock_trid_provider = MagicMock()

    return  KoreaInvestApiAccount(
        env=mock_env,
        logger=mock_logger,
        market_clock=mock_market_clock,
        trid_provider=mock_trid_provider,
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


@pytest.mark.asyncio
async def test_inquire_daily_ccld_builds_order_query_params():
    api = get_api()
    api._env.active_config = {
        "custtype": "P",
        "stock_account_number": "12345678-01",
    }
    api._trid_provider.account_inquire_daily_ccld.return_value = "VTTC0081R"
    api.call_api = AsyncMock(return_value={"result": "success"})

    result = await api.inquire_daily_ccld(
        start_date="20260423",
        end_date="20260423",
        side_code="02",
        stock_code="005930",
        order_no="A0001",
        exchange=Exchange.NXT,
    )

    assert result == {"result": "success"}
    args, kwargs = api.call_api.await_args
    assert args[0] == "GET"
    assert args[1] == EndpointKey.INQUIRE_DAILY_CCLD
    assert kwargs["params"]["CANO"] == "12345678"
    assert kwargs["params"]["ACNT_PRDT_CD"] == "01"
    assert kwargs["params"]["SLL_BUY_DVSN_CD"] == "02"
    assert kwargs["params"]["PDNO"] == "005930"
    assert kwargs["params"]["ODNO"] == "A0001"
    assert kwargs["params"]["EXCG_ID_DVSN_CD"] == "NX"


@pytest.mark.asyncio
async def test_get_account_balance_without_product_code_defaults_to_01_and_nxt_flag():
    api = get_api()
    api._env.is_paper_trading = False
    api._env.active_config = {
        "custtype": "P",
        "stock_account_number": "12345678",
    }
    api._trid_provider.account_inquire_balance.return_value = "TTTC8434R"
    api.call_api = AsyncMock(return_value={"result": "balance"})

    result = await api.get_account_balance(exchange=Exchange.NXT)

    assert result == {"result": "balance"}
    args, kwargs = api.call_api.await_args
    assert args[0] == "GET"
    assert args[1] == EndpointKey.INQUIRE_BALANCE
    assert kwargs["params"]["CANO"] == "12345678"
    assert kwargs["params"]["ACNT_PRDT_CD"] == "01"
    assert kwargs["params"]["AFHR_FLPR_YN"] == "X"


@pytest.mark.asyncio
async def test_inquire_unfilled_orders_builds_params_for_plain_account_and_nxt():
    api = get_api()
    api._env.active_config = {
        "custtype": "P",
        "stock_account_number": "12345678",
    }
    api._trid_provider.account_inquire_psbl_rvsecncl.return_value = "TTTC8036R"
    api.call_api = AsyncMock(return_value={"result": "unfilled"})

    result = await api.inquire_unfilled_orders(
        exchange=Exchange.NXT,
        inqr_dvsn_1="1",
        inqr_dvsn_2="2",
    )

    assert result == {"result": "unfilled"}
    args, kwargs = api.call_api.await_args
    assert args[0] == "GET"
    assert args[1] == EndpointKey.INQUIRE_PSBL_RVSECNCL
    assert kwargs["params"]["CANO"] == "12345678"
    assert kwargs["params"]["ACNT_PRDT_CD"] == "01"
    assert kwargs["params"]["CTX_AREA_FK100"] == ""
    assert kwargs["params"]["CTX_AREA_NK100"] == ""
    assert kwargs["params"]["INQR_DVSN_1"] == "1"
    assert kwargs["params"]["INQR_DVSN_2"] == "2"
    assert kwargs["params"]["EXCG_ID_DVSN_CD"] == "NX"


@pytest.mark.asyncio
async def test_inquire_filled_history_delegates_to_daily_ccld_with_filled_filter():
    api = get_api()
    api.inquire_daily_ccld = AsyncMock(return_value={"result": "filled"})

    result = await api.inquire_filled_history(
        start_date="20260401",
        end_date="20260430",
        exchange=Exchange.NXT,
    )

    assert result == {"result": "filled"}
    api.inquire_daily_ccld.assert_awaited_once_with(
        start_date="20260401",
        end_date="20260430",
        ccld_dvsn="01",
        exchange=Exchange.NXT,
    )
