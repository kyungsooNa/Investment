"""키움 전 계층 통과 검증 (K-1 Phase 7).

검증 범위 (httpx 만 mock, 나머지는 실제 객체):
  BrokerAPIWrapper("kiwoom") → ClientWithCache → ClientWithRetryQueue
    → KiwoomApiClient → Kiwoom{Quotations,Account,Trading}Api
      → KiwoomApiBase._execute_request → [MOCK: _async_session.post]

기존 `test_it_kiwoom_broker_api_wrapper.py` 는 클라이언트가 "생성되는지" 만 봤다.
여기서는 래핑 계층을 실제로 통과해 값이 온전히 돌아오는지, 그리고 주문 경로가
재시도 큐를 타지 않는지를 본다 — 재시도되면 중복 주문이 난다.
"""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from brokers.broker_api_wrapper import BrokerAPIWrapper
from common.types import ErrorCode, Exchange


def _http_response(payload, status=200):
    resp = MagicMock()
    resp.status_code = status
    resp.headers = {}
    resp.text = json.dumps(payload, ensure_ascii=False)
    resp.content = resp.text.encode("utf-8")
    resp.json = lambda: payload
    resp.raise_for_status = MagicMock()
    return resp


def _env():
    env = MagicMock()
    env.is_paper_trading = True
    env.get_base_url.return_value = "https://mockapi.kiwoom.com"
    env.get_access_token = AsyncMock(return_value="tok")
    return env


@pytest.fixture
def kiwoom_wrapper():
    """실제 KiwoomApiClient 를 쓰되 종목 저장소만 mock 한다."""
    with patch("brokers.broker_api_wrapper.StockCodeRepository"):
        wrapper = BrokerAPIWrapper(
            broker="kiwoom",
            env=_env(),
            logger=MagicMock(),
            market_clock=MagicMock(),
        )
    yield wrapper


def _patch_all_sessions(wrapper, responder):
    """키움 하위 API 3종의 httpx 세션 post 를 한 번에 갈아끼운다.

    래핑 계층(cache/retry) 너머의 실제 client 를 찾아 들어간다.
    """
    client = wrapper._client
    while not hasattr(client, "_quotations"):
        client = getattr(client, "_client", None)
        assert client is not None, "래핑 계층 안에서 KiwoomApiClient 를 찾지 못했다"

    posts = []

    async def fake_post(url, headers=None, json=None, **kwargs):
        posts.append({"url": url, "api_id": (headers or {}).get("api-id"), "body": json})
        return responder((headers or {}).get("api-id"))

    for api in (client._quotations, client._account, client._trading):
        api._async_session.post = AsyncMock(side_effect=fake_post)
    return posts


_KA10001 = {
    "stk_cd": "005930", "cur_prc": "+70100", "open_pric": "69800",
    "high_pric": "70500", "low_pric": "-69600", "trde_qty": "9263135",
    "return_code": 0, "return_msg": "정상",
}
_KT00018 = {
    "tot_evlt_amt": "000000025789890",
    "acnt_evlt_remn_indv_tot": [
        {"stk_cd": "A005930", "stk_nm": "삼성전자", "rmnd_qty": "000000000000003",
         "cur_prc": "000000059000", "pur_pric": "000000000124500", "prft_rt": "-52.71"},
    ],
    "return_code": 0,
}
_KT00001 = {"entr": "000000000017534", "return_code": 0}
_KT10000 = {"ord_no": "00024", "return_code": 0}


def _default_responder(api_id):
    return _http_response({
        "ka10001": _KA10001,
        "kt00018": _KT00018,
        "kt00001": _KT00001,
        "kt10000": _KT10000,
    }.get(api_id, {"return_code": 0}))


class TestQuotationPipeline:
    async def test_current_price_survives_cache_and_retry_layers(self, kiwoom_wrapper):
        _patch_all_sessions(kiwoom_wrapper, _default_responder)

        result = await kiwoom_wrapper.get_current_price("005930")

        assert result.rt_cd == ErrorCode.SUCCESS.value
        # 부호 정규화가 래핑 계층을 지나서도 유지돼야 한다.
        assert result.data["output"].stck_prpr == "70100"

    async def test_minute_chart_reaches_kiwoom_api(self, kiwoom_wrapper):
        """Phase 4 에서 새로 만든 메서드가 래퍼 프록시를 통과하는지 확인한다."""
        posts = _patch_all_sessions(kiwoom_wrapper, lambda api_id: _http_response({
            "stk_min_pole_chart_qry": [
                {"cntr_tm": "20250908153000", "open_pric": "1", "high_pric": "2",
                 "low_pric": "3", "cur_prc": "4", "trde_qty": "5"},
            ],
            "return_code": 0,
        }))

        result = await kiwoom_wrapper._client.get_intraday_minutes("005930", tick_scope="5")

        assert result.rt_cd == ErrorCode.SUCCESS.value
        assert posts[-1]["api_id"] == "ka10080"
        assert posts[-1]["body"]["tic_scope"] == "5"


class TestAccountPipeline:
    async def test_balance_merges_two_apis_through_layers(self, kiwoom_wrapper):
        posts = _patch_all_sessions(kiwoom_wrapper, _default_responder)

        result = await kiwoom_wrapper.get_account_balance()

        assert result.rt_cd == ErrorCode.SUCCESS.value
        # 잔고 + 예수금 두 API 를 모두 호출한다.
        assert {p["api_id"] for p in posts} == {"kt00018", "kt00001"}
        holding = result.data["output1"][0]
        # A 접두 제거와 패딩 정규화가 계층을 지나 유지된다.
        assert holding["pdno"] == "005930"
        assert holding["hldg_qty"] == "3"
        assert result.data["output2"][0]["dnca_tot_amt"] == "17534"


class TestOrderPipeline:
    async def test_order_reaches_kiwoom_and_returns_order_number(self, kiwoom_wrapper):
        posts = _patch_all_sessions(kiwoom_wrapper, _default_responder)

        result = await kiwoom_wrapper.place_stock_order("005930", 70000, 1, is_buy=True)

        assert result.rt_cd == ErrorCode.SUCCESS.value
        assert result.data["output"]["ODNO"] == "00024"
        assert posts[-1]["api_id"] == "kt10000"
        assert posts[-1]["body"]["trde_tp"] == "0"

    def test_order_methods_bypass_the_retry_queue(self, kiwoom_wrapper):
        """주문은 멱등하지 않다 — 재시도 큐를 타면 중복 주문이 난다.

        `_EXCLUDED_METHODS` 는 메서드명 기준이라 키움에도 그대로 적용돼야 한다.
        호출 횟수만 세면 비즈니스 오류가 애초에 재시도 대상이 아니라 통과해버리므로,
        래퍼가 큐 경로(`queued`)를 반환하지 않는다는 구조 자체를 본다.
        """
        retry_layer = kiwoom_wrapper._client
        while type(retry_layer).__name__ != "ClientWithRetryQueue":
            retry_layer = getattr(retry_layer, "_client", None)
            assert retry_layer is not None, "재시도 계층을 찾지 못했다"

        assert retry_layer.place_stock_order.__name__ != "queued", (
            "주문이 재시도 큐를 타고 있다 — 중복 주문 위험"
        )
        assert retry_layer.cancel_stock_order.__name__ != "queued"
        # 대조군: 조회는 큐를 타는 것이 정상이다.
        assert retry_layer.get_current_price.__name__ == "queued"

    async def test_order_failure_is_sent_once(self, kiwoom_wrapper):
        calls = {"n": 0}

        def failing(api_id):
            calls["n"] += 1
            return _http_response({"return_code": 1, "return_msg": "증거금 부족"})

        _patch_all_sessions(kiwoom_wrapper, failing)

        result = await kiwoom_wrapper.place_stock_order("005930", 70000, 1, is_buy=True)

        assert result.rt_cd != ErrorCode.SUCCESS.value
        assert calls["n"] == 1, f"주문이 {calls['n']}회 전송됐다 — 중복 주문 위험"

    async def test_nxt_market_order_never_reaches_network(self, kiwoom_wrapper):
        posts = _patch_all_sessions(kiwoom_wrapper, _default_responder)

        result = await kiwoom_wrapper.place_stock_order(
            "005930", 0, 1, is_buy=True, exchange=Exchange.NXT,
        )

        assert result.rt_cd == ErrorCode.INVALID_INPUT.value
        assert posts == []


class TestBootstrapLockRemains:
    def test_broker_bootstrap_does_not_select_kiwoom(self):
        """Phase 7 은 배선만이고 앱 조립 경로는 여전히 한국투자다.

        실계좌 스모크 검증 전까지 키움이 운영 경로에 들어오면 안 된다.
        """
        source = __import__("pathlib").Path("view/web/bootstrap/broker_bootstrap.py").read_text(
            encoding="utf-8"
        )

        assert "kiwoom" not in source.lower(), (
            "BrokerBootstrap 이 키움을 선택하기 시작했다 — 스모크 검증 없이 운영 경로에 "
            "들어오면 안 된다. 의도한 변경이라면 이 테스트를 함께 갱신할 것."
        )
