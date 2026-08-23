"""키움 주문 API (Phase 6) 단위 테스트.

스펙 출처: 키움 공식 저장소 `Kiwoom-Securities/kiwoom-rest-api` 의
`kiwoom/_data/kiwoom_api_spec.json` — kt10000(매수) · kt10001(매도) · kt10003(취소).

실제 주문은 절대 호출하지 않는다(계획서 Phase 6 검증 기준) — call_api 를 전부 대체한다.
"""
from unittest.mock import AsyncMock, MagicMock

from brokers.kiwoom.kiwoom_trading_api import KiwoomTradingApi
from common.types import ErrorCode, Exchange, ResCommonResponse


def _make_api(response=None):
    env = MagicMock()
    env.get_base_url.return_value = "https://mockapi.kiwoom.com"
    env.get_access_token = AsyncMock(return_value="tok")

    api = KiwoomTradingApi(env, logger=MagicMock())
    api.call_api = AsyncMock(return_value=response or _ok({"ord_no": "00024", "return_code": 0}))
    return api


def _ok(payload):
    return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="정상", data=payload)


class TestBuyOrder:
    async def test_limit_buy_uses_kt10000_with_normal_trade_type(self):
        api = _make_api()

        await api.place_stock_order("005930", 70000, 3, is_buy=True)

        _, kwargs = api.call_api.call_args
        assert kwargs["api_id"] == "kt10000"
        body = kwargs["data"]
        assert body["stk_cd"] == "005930"
        assert body["ord_qty"] == "3"
        assert body["ord_uv"] == "70000"
        assert body["trde_tp"] == "0"          # 0:보통(지정가)
        assert body["dmst_stex_tp"] == "KRX"

    async def test_market_buy_omits_price_and_uses_market_trade_type(self):
        api = _make_api()

        await api.place_stock_order("005930", 0, 3, is_buy=True)

        body = api.call_api.call_args.kwargs["data"]
        assert body["trde_tp"] == "3"          # 3:시장가
        # 시장가에 단가를 실어 보내면 안 된다.
        assert "ord_uv" not in body

    async def test_returns_kis_shaped_order_number(self):
        api = _make_api(_ok({"ord_no": "00024", "dmst_stex_tp": "KRX", "return_code": 0}))

        result = await api.place_stock_order("005930", 70000, 1, is_buy=True)

        assert result.rt_cd == ErrorCode.SUCCESS.value
        # 소비처(fill_reconciliation 등)가 읽는 KIS 계약 키로 맞춘다.
        assert result.data["output"]["ODNO"] == "00024"

    async def test_adjusts_limit_price_to_tick_size(self):
        """지정가는 KIS 경로와 같은 호가단위 보정을 거쳐야 한다(브로커별로 달라지면 안 된다)."""
        api = _make_api()

        await api.place_stock_order("005930", 70001, 1, is_buy=True)

        body = api.call_api.call_args.kwargs["data"]
        assert body["ord_uv"] == "70000"


class TestSellOrder:
    async def test_limit_sell_uses_kt10001(self):
        api = _make_api()

        await api.place_stock_order("005930", 70000, 2, is_buy=False)

        assert api.call_api.call_args.kwargs["api_id"] == "kt10001"

    async def test_nxt_exchange_is_forwarded(self):
        api = _make_api()

        await api.place_stock_order("005930", 70000, 2, is_buy=False, exchange=Exchange.NXT)

        assert api.call_api.call_args.kwargs["data"]["dmst_stex_tp"] == "NXT"


class TestGuards:
    async def test_nxt_market_order_is_rejected(self):
        """NXT 시장가 미지원은 KIS 경로와 동일하게 막는다 — 브로커를 바꿔도 같은 거절."""
        api = _make_api()

        result = await api.place_stock_order("005930", 0, 1, is_buy=True, exchange=Exchange.NXT)

        assert result.rt_cd == ErrorCode.INVALID_INPUT.value
        api.call_api.assert_not_awaited()

    async def test_non_positive_quantity_is_rejected(self):
        api = _make_api()

        result = await api.place_stock_order("005930", 70000, 0, is_buy=True)

        assert result.rt_cd == ErrorCode.INVALID_INPUT.value
        api.call_api.assert_not_awaited()

    async def test_business_error_propagates(self):
        api = _make_api(ResCommonResponse(rt_cd=ErrorCode.API_ERROR.value, msg1="증거금 부족", data=None))

        result = await api.place_stock_order("005930", 70000, 1, is_buy=True)

        assert result.rt_cd == ErrorCode.API_ERROR.value

    async def test_missing_order_number_is_not_reported_as_success(self):
        """주문번호 없는 성공 응답을 그대로 통과시키면 체결 대사가 조용히 빈다."""
        api = _make_api(_ok({"return_code": 0}))

        result = await api.place_stock_order("005930", 70000, 1, is_buy=True)

        assert result.rt_cd != ErrorCode.SUCCESS.value


class TestCancelOrder:
    async def test_cancel_uses_kt10003_with_original_order_no(self):
        api = _make_api(_ok({"ord_no": "0000141", "base_orig_ord_no": "0000139",
                             "cncl_qty": "000000000001", "return_code": 0}))

        result = await api.cancel_stock_order(
            broker_order_no="0000139", stock_code="005930", order_qty=1,
        )

        assert result.rt_cd == ErrorCode.SUCCESS.value
        _, kwargs = api.call_api.call_args
        assert kwargs["api_id"] == "kt10003"
        body = kwargs["data"]
        assert body["orig_ord_no"] == "0000139"
        assert body["stk_cd"] == "005930"
        assert body["cncl_qty"] == "1"
        assert result.data["output"]["ODNO"] == "0000141"

    async def test_cancel_all_sends_zero_quantity(self):
        """스펙상 취소수량 '0' 은 잔량 전부 취소를 뜻한다."""
        api = _make_api(_ok({"ord_no": "0000141", "return_code": 0}))

        await api.cancel_stock_order(broker_order_no="0000139", stock_code="005930", order_qty=0)

        assert api.call_api.call_args.kwargs["data"]["cncl_qty"] == "0"
