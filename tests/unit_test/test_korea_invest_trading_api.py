import pytest
import json
import httpx
from unittest.mock import AsyncMock, MagicMock, patch
from brokers.korea_investment.korea_invest_trading_api import KoreaInvestApiTrading
from common.types import ErrorCode, ResCommonResponse


def make_api():
    api = KoreaInvestApiTrading(MagicMock(), MagicMock())
    # _get_hashkey에서 path 구성할 때 active_config를 읽으므로 최소 필드 세팅
    api._env.active_config = {
        "base_url": "https://mock.api",
        "api_key": "abc",
        "api_secret_key": "def",
    }
    return api

class FakeResp:
    def __init__(self, payload, status_ok=True, text=None):
        self._payload = payload
        self.text = text if text is not None else json.dumps(payload)
        self._status_ok = status_ok

    def raise_for_status(self):
        if not self._status_ok:
            # 간단히 HTTPStatusError 흉내 (request/response None 처리 가능)
            raise httpx.HTTPStatusError("boom", request=None, response=None)

    def json(self):
        return self._payload

class FakeRespBadJson:
    text = "not-json"
    def raise_for_status(self):
        return None
    def json(self):
        # _get_hashkey가 json.JSONDecodeError를 캐치하므로 정확히 그 예외를 던짐
        raise json.JSONDecodeError("bad json", "doc", 0)

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
    trading_api = KoreaInvestApiTrading(MagicMock(), mock_logger)

    # mock _get_hashkey and call_api
    trading_api._get_hashkey = AsyncMock(return_value='mocked_hash')
    trading_api.call_api = AsyncMock(return_value={'status': 'buy_success'})

    result = await trading_api.place_stock_order(
        stock_code='005930',
        order_price='70000',
        order_qty='10',
        is_buy=True
    )

    # 1) 결과 확인
    assert result == {'status': 'buy_success'}

    # 2) hashkey를 계산하려고 _get_hashkey가 호출되었는지 확인
    trading_api._get_hashkey.assert_awaited()
    # 필요시 인자까지 확인하려면:
    # args, kwargs = trading_api._get_hashkey.call_args
    # assert 'ord_unpr' in args[0]  # 등등

    # 3) API 호출이 1회 일어난 것 확인(헤더 적용은 내부에서 수행됨)
    trading_api.call_api.assert_awaited_once()

    # 4) 새 설계에서는 temp 컨텍스트 종료 후 hashkey가 지워지는 것이 정상
    assert trading_api._headers.build().get('hashkey') is None

@pytest.mark.asyncio
async def test_get_hashkey_success():
    api = make_api()
    with patch.object(KoreaInvestApiTrading, "call_api", new=AsyncMock()) as mock_call:
        mock_call.return_value = ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value, msg1="ok",
            data=FakeResp({"HASH": "abc123"})
        )
        out = await api._get_hashkey({"k":"v"})
        assert out == "abc123"
        mock_call.assert_awaited_once()

@pytest.mark.asyncio
async def test_get_hashkey_network_error():
    api = make_api()
    # call_api 자체가 네트워크 계열 예외를 던지도록 모킹
    with patch.object(KoreaInvestApiTrading, "call_api", new=AsyncMock(
        side_effect=httpx.RequestError("network error")
    )):
        out = await api._get_hashkey({"k":"v"})
        assert out is None

@pytest.mark.asyncio
async def test_get_hashkey_json_decode_error():
    api = make_api()
    with patch.object(KoreaInvestApiTrading, "call_api", new=AsyncMock()) as mock_call:
        mock_call.return_value = ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value, msg1="ok",
            data=FakeRespBadJson()
        )
        out = await api._get_hashkey({"k":"v"})
        assert out is None

@pytest.mark.asyncio
async def test_get_hashkey_unexpected_exception():
    api = KoreaInvestApiTrading(MagicMock(), MagicMock())
    api._env.active_config = {
        "base_url": "https://mock.api",
        "api_key": "abc",
        "api_secret_key": "def",
    }

    # call_api가 예기치 못한 예외를 던지도록 모킹
    with patch.object(KoreaInvestApiTrading, "call_api", new=AsyncMock(side_effect=Exception("Unexpected Error"))):
        result = await api._get_hashkey({"test": "value"})
        assert result is None

@pytest.mark.asyncio
async def test_get_hashkey_missing_hash_field():
    api = make_api()
    with patch.object(KoreaInvestApiTrading, "call_api", new=AsyncMock()) as mock_call:
        # HASH 키가 없음
        mock_call.return_value = ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value, msg1="ok",
            data=FakeResp({"NO_HASH": "zzz"})
        )
        out = await api._get_hashkey({"k":"v"})
        assert out is None

@pytest.mark.asyncio
async def test_place_stock_order_sell_success():
    mock_logger = MagicMock()
    mock_env = MagicMock()
    mock_env.active_config = {
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
    mock_env.set_trading_mode(True)
    trading_api = KoreaInvestApiTrading(mock_env, mock_logger)

    trading_api._get_hashkey = AsyncMock(return_value="hash123")
    trading_api.call_api = AsyncMock(return_value={"status": "sell_success"})

    result = await trading_api.place_stock_order(
        stock_code='005930',
        order_price='70000',
        order_qty='10',
        is_buy=False
    )
    assert result == {'status': 'sell_success'}

    # 2) hashkey를 계산하려고 _get_hashkey가 호출되었는지 확인
    trading_api._get_hashkey.assert_awaited()
    # 필요시 인자까지 확인하려면:
    # args, kwargs = trading_api._get_hashkey.call_args
    # assert 'ord_unpr' in args[0]  # 등등

    # 3) API 호출이 1회 일어난 것 확인(헤더 적용은 내부에서 수행됨)
    trading_api.call_api.assert_awaited_once()

    # 4) 새 설계에서는 temp 컨텍스트 종료 후 hashkey가 지워지는 것이 정상
    assert trading_api._headers.build().get('hashkey') is None

@pytest.mark.asyncio
async def test_place_stock_order_hashkey_none():
    mock_logger = MagicMock()
    api = KoreaInvestApiTrading(MagicMock(), mock_logger)

    # 최소 config (TR_ID 접근 시 필요할 수 있음)
    api._env.active_config = {
        "base_url": "https://mock.api",
        "api_key": "abc",
        "api_secret_key": "def",
        "custtype": "P",
        "is_paper_trading": True,
        "stock_account_number": "12345678",
        "tr_ids": {
            "trading": {
                "order_cash_buy_paper": "VTTC0802U",
                "order_cash_buy_real": "TTTC0802U",
                "order_cash_sell_paper": "VTTC0801U",
                "order_cash_sell_real": "TTTC0801U",
            }
        }
    }

    # 1) 해시키 실패 모킹
    api._get_hashkey = AsyncMock(return_value=None)

    # 2) 주문 API가 호출되지 않아야 함
    with patch.object(KoreaInvestApiTrading, "call_api", new=AsyncMock()) as mock_call:
        result = await api.place_stock_order(
            stock_code="005930",
            order_price="70000",
            order_qty="10",
            is_buy=True,
        )

        # 현재 구현 계약에 맞춘 검증
        assert isinstance(result, ResCommonResponse)
        assert result.rt_cd == ErrorCode.MISSING_KEY.value
        assert "hashkey 계산 실패" in result.msg1

        # 해시키 실패했으므로 실제 주문 API 호출 X
        mock_call.assert_not_awaited()
