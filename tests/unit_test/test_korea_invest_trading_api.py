import pytest
import requests
import json

from unittest.mock import AsyncMock, MagicMock, patch
from brokers.korea_investment.korea_invest_trading_api import KoreaInvestApiTrading


@pytest.mark.asyncio
async def test_place_stock_order_buy_success():
    mock_config = {
        'base_url': 'https://mock.api',
        'api_key': 'test_key',
        'api_secret_key': 'test_secret',
        'custtype': 'P',
        'stock_account_number': '12345678',
        'is_paper_trading': True,
        'tr_ids': {
            'trading': {
                'order_cash_buy_paper': 'BUY_PAPER_ID',
                'order_cash_buy_real': 'BUY_REAL_ID',
                'order_cash_sell_paper': 'SELL_PAPER_ID',
                'order_cash_sell_real': 'SELL_REAL_ID'
            }
        }
    }

    mock_logger = MagicMock()
    trading_api = KoreaInvestApiTrading("https://mock.api", {}, mock_config, MagicMock(), mock_logger)

    # mock _get_hashkey and call_api
    trading_api._get_hashkey = AsyncMock(return_value='mocked_hash')
    trading_api.call_api = AsyncMock(return_value={'status': 'success'})

    result = await trading_api.place_stock_order(
        stock_code='005930',
        order_price='70000',
        order_qty='10',
        trade_type='buy',
        order_dvsn='00'
    )

    assert result == {'status': 'success'}
    assert trading_api._headers["hashkey"] == 'mocked_hash'
    mock_logger.info.assert_called()


@pytest.mark.asyncio
async def test_place_stock_order_invalid_type():
    mock_config = {
        'tr_ids': {'trading': {}},
        'custtype': 'P',
        'stock_account_number': '12345678',
        'is_paper_trading': True
    }

    mock_logger = MagicMock()
    trading_api = KoreaInvestApiTrading(
        "https://mock.api",
        {},
        mock_config,
        MagicMock(),  # token_manager 자리 추가
        mock_logger
    )
    result = await trading_api.place_stock_order('005930', '70000', '10', '잘못된타입', '00')
    assert result is None
    mock_logger.error.assert_called_once()


@pytest.mark.asyncio
@patch("requests.post")
async def test_get_hashkey_success(mock_post):
    # Mock HTTP response
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {'HASH': 'abc123'}
    mock_post.return_value = mock_response

    mock_config = {
        'base_url': 'https://mock.api',
        'api_key': 'abc',
        'api_secret_key': 'def'
    }

    trading_api = KoreaInvestApiTrading('https://mock.api', {}, mock_config, MagicMock(), MagicMock())
    result = await trading_api._get_hashkey({'test': 'value'})

    assert result == 'abc123'


@pytest.mark.asyncio
@patch("requests.post", side_effect=requests.exceptions.RequestException("network error"))
async def test_get_hashkey_network_error(mock_post):
    trading_api = KoreaInvestApiTrading('https://mock.api', {}, {
        'base_url': 'https://mock.api',
        'api_key': 'abc',
        'api_secret_key': 'def'
    }, MagicMock(), MagicMock())

    result = await trading_api._get_hashkey({'test': 'value'})
    assert result is None


@pytest.mark.asyncio
@patch("requests.post")
async def test_get_hashkey_json_decode_error(mock_post):
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.side_effect = json.JSONDecodeError("Expecting value", "", 0)
    mock_response.text = "invalid json"
    mock_post.return_value = mock_response

    trading_api = KoreaInvestApiTrading('https://mock.api', {}, {
        'base_url': 'https://mock.api',
        'api_key': 'abc',
        'api_secret_key': 'def'
    }, MagicMock(), MagicMock())

    result = await trading_api._get_hashkey({'test': 'value'})
    assert result is None


@pytest.mark.asyncio
@patch("requests.post")
async def test_get_hashkey_unexpected_exception(mock_post):
    mock_post.side_effect = Exception("Unexpected Error")

    trading_api = KoreaInvestApiTrading('https://mock.api', {}, {
        'base_url': 'https://mock.api',
        'api_key': 'abc',
        'api_secret_key': 'def'
    }, MagicMock(), MagicMock())

    result = await trading_api._get_hashkey({'test': 'value'})
    assert result is None


@pytest.mark.asyncio
@patch("requests.post")
async def test_get_hashkey_missing_hash_field(mock_post):
    # 응답 JSON에 'HASH' 키가 없는 경우
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {'unexpected_key': 'value'}
    mock_post.return_value = mock_response

    mock_logger = MagicMock()

    trading_api = KoreaInvestApiTrading('https://mock.api', {}, {
        'base_url': 'https://mock.api',
        'api_key': 'abc',
        'api_secret_key': 'def'
    }, MagicMock(), mock_logger)

    result = await trading_api._get_hashkey({'test': 'value'})

    assert result is None
    mock_logger.error.assert_called_with("Hashkey API 응답에 HASH 값이 없습니다: {'unexpected_key': 'value'}")

@pytest.mark.asyncio
async def test_place_stock_order_sell_success():
    # is_paper_trading=True → order_cash_sell_paper 실행 검증
    mock_config = {
        'base_url': 'https://mock.api',
        'api_key': 'abc',
        'api_secret_key': 'def',
        'custtype': 'P',
        'stock_account_number': '12345678',
        'is_paper_trading': True,
        'tr_ids': {
            'trading': {
                'order_cash_buy_paper': 'BUY_PAPER',
                'order_cash_buy_real': 'BUY_REAL',
                'order_cash_sell_paper': 'SELL_PAPER',
                'order_cash_sell_real': 'SELL_REAL'
            }
        }
    }

    mock_logger = MagicMock()
    trading_api = KoreaInvestApiTrading("https://mock.api", {}, mock_config, MagicMock(), mock_logger)

    trading_api._get_hashkey = AsyncMock(return_value="hash123")
    trading_api.call_api = AsyncMock(return_value={"status": "sell_success"})

    result = await trading_api.place_stock_order(
        stock_code='005930',
        order_price='70000',
        order_qty='10',
        trade_type='sell',
        order_dvsn='00'
    )

    assert result == {"status": "sell_success"}
    assert trading_api._headers["tr_id"] == "SELL_PAPER"
    mock_logger.info.assert_called()



@pytest.mark.asyncio
async def test_place_stock_order_hashkey_none():
    mock_config = {
        'base_url': 'https://mock.api',
        'api_key': 'abc',
        'api_secret_key': 'def',
        'custtype': 'P',
        'stock_account_number': '12345678',
        'is_paper_trading': True,
        'tr_ids': {
            'trading': {
                'order_cash_buy_paper': 'BUY_PAPER',
                'order_cash_buy_real': 'BUY_REAL',
                'order_cash_sell_paper': 'SELL_PAPER',
                'order_cash_sell_real': 'SELL_REAL'
            }
        }
    }

    mock_logger = MagicMock()
    trading_api = KoreaInvestApiTrading("https://mock.api", {}, mock_config, MagicMock(), mock_logger)

    # 해시 생성 실패 상황을 모의
    trading_api._get_hashkey = AsyncMock(return_value=None)

    # 실제 주문 시도
    result = await trading_api.place_stock_order(
        stock_code='005930',
        order_price='70000',
        order_qty='10',
        trade_type='buy',
        order_dvsn='00'
    )

    # 검증: 해시 생성 실패로 인해 주문 시도 중단
    assert result is None
