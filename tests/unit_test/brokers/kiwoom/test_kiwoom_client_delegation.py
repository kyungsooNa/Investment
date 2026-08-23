"""KiwoomApiClient 가 시세·계좌 메서드를 각 API 로 위임하는지 확인한다.

Phase 4~5 범위는 시세·계좌뿐이므로 주문은 여전히 "기능 미구현" 을 돌려줘야 한다 —
골격이 조용히 잘못된 값을 흘리지 않는다는 현재의 안전 속성을 유지하기 위함이다.
"""
from unittest.mock import AsyncMock, MagicMock

from brokers.kiwoom.kiwoom_client import KiwoomApiClient
from common.types import ErrorCode, Exchange, ResCommonResponse


def _client():
    env = MagicMock()
    env.get_base_url.return_value = "https://mockapi.kiwoom.com"
    env.get_access_token = AsyncMock(return_value="tok")
    client = KiwoomApiClient(env, logger=MagicMock())
    client._quotations = MagicMock()
    client._account = MagicMock()
    return client


def _ok(data=None):
    return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="정상", data=data)


class TestQuotationDelegation:
    async def test_get_current_price_delegates(self):
        client = _client()
        client._quotations.get_current_price = AsyncMock(return_value=_ok({"output": "x"}))

        result = await client.get_current_price("005930", exchange=Exchange.NXT)

        assert result.rt_cd == ErrorCode.SUCCESS.value
        client._quotations.get_current_price.assert_awaited_once_with("005930", exchange=Exchange.NXT)

    async def test_get_stock_info_delegates(self):
        client = _client()
        client._quotations.get_stock_info_by_code = AsyncMock(return_value=_ok("info"))

        result = await client.get_stock_info_by_code("005930")

        assert result.data == "info"
        client._quotations.get_stock_info_by_code.assert_awaited_once_with("005930", exchange=Exchange.KRX)

    async def test_daily_chart_delegates(self):
        client = _client()
        client._quotations.inquire_daily_itemchartprice = AsyncMock(return_value=_ok([]))

        await client.inquire_daily_itemchartprice("005930", "20250901", "20250908")

        client._quotations.inquire_daily_itemchartprice.assert_awaited_once_with(
            "005930", "20250901", "20250908",
            fid_period_div_code="D", exchange=Exchange.KRX,
        )

    async def test_minute_chart_delegates(self):
        client = _client()
        client._quotations.get_intraday_minutes = AsyncMock(return_value=_ok([]))

        await client.get_intraday_minutes("005930", tick_scope="5")

        client._quotations.get_intraday_minutes.assert_awaited_once_with(
            "005930", tick_scope="5", base_date="", exchange=Exchange.KRX,
        )


class TestAccountDelegation:
    async def test_get_account_balance_delegates(self):
        client = _client()
        client._account.get_account_balance = AsyncMock(return_value=_ok({"output1": []}))

        result = await client.get_account_balance(exchange=Exchange.NXT)

        assert result.rt_cd == ErrorCode.SUCCESS.value
        client._account.get_account_balance.assert_awaited_once_with(exchange=Exchange.NXT)


class TestStillUnimplemented:
    async def test_order_remains_unsupported(self):
        client = _client()

        for coro in (
            client.place_stock_order("005930", "1000", "1", True),
            client.cancel_stock_order(),
        ):
            result = await coro
            assert result.rt_cd == ErrorCode.API_ERROR.value
            assert "기능 미구현" in result.msg1
