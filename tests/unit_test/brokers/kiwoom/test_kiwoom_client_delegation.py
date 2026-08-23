"""KiwoomApiClient 가 시세·계좌·주문 메서드를 각 API 로 위임하는지 확인한다.

Phase 4~6 으로 REST 경로는 모두 구현됐다. WebSocket 계열은 아직 골격이라
조용히 성공을 반환하지 않는다는 점을 함께 고정한다.
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
    client._trading = MagicMock()
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


class TestTradingDelegation:
    async def test_place_stock_order_delegates(self):
        client = _client()
        client._trading.place_stock_order = AsyncMock(return_value=_ok({"output": {"ODNO": "1"}}))

        result = await client.place_stock_order("005930", 70000, 1, True, exchange=Exchange.NXT)

        assert result.data["output"]["ODNO"] == "1"
        client._trading.place_stock_order.assert_awaited_once_with(
            "005930", 70000, 1, True, exchange=Exchange.NXT,
        )

    async def test_cancel_stock_order_delegates(self):
        client = _client()
        client._trading.cancel_stock_order = AsyncMock(return_value=_ok({"output": {"ODNO": "2"}}))

        await client.cancel_stock_order(broker_order_no="9", stock_code="005930", order_qty=1)

        client._trading.cancel_stock_order.assert_awaited_once_with(
            broker_order_no="9", stock_code="005930", order_qty=1,
        )


class TestWebsocketStillSkeleton:
    async def test_websocket_paths_do_not_claim_success(self):
        """Phase 8 미착수 — 실시간 경로가 성공한 척하면 무틱을 정상으로 오인한다."""
        client = _client()

        assert client.is_websocket_receive_alive() is False
        assert await client.connect_websocket() is False
        assert await client.subscribe_realtime_price("005930") is False
        assert client.get_subscription_ledger() == {}
