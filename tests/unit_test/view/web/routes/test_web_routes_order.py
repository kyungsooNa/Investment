"""
주문 관련 테스트 (order.html).
"""
import pytest
from common.overseas_types import OverseasExchange
from common.types import ErrorCode, ResCommonResponse
from view.web.security import (
    CSRF_COOKIE_NAME,
    CSRF_HEADER_NAME,
    SESSION_COOKIE_NAME,
    issue_session,
)


def _authenticate_as_operator(web_client, mock_web_ctx):
    auth_config = mock_web_ctx.full_config["auth"]
    auth_config["users"] = [
        {
            "username": "ops",
            "password_hash": auth_config["password_hash"],
            "role": "operator",
            "enabled": True,
        }
    ]
    token, claims = issue_session(
        auth_config,
        "ops",
        role="operator",
    )
    web_client.cookies.set(SESSION_COOKIE_NAME, token)
    web_client.cookies.set(CSRF_COOKIE_NAME, claims.csrf_token)
    web_client.headers[CSRF_HEADER_NAME] = claims.csrf_token


@pytest.mark.asyncio
async def test_place_order_buy(web_client, mock_web_ctx):
    """POST /api/order (매수) 엔드포인트 테스트"""
    mock_web_ctx.order_execution_service.handle_buy_stock.return_value = ResCommonResponse(
        rt_cd="0", msg1="Order Placed", data={"ord_no": "12345"}
    )

    payload = {"code": "005930", "price": "70000", "qty": "10", "side": "buy"}
    response = web_client.post("/api/order", json=payload)

    assert response.status_code == 200
    assert response.json()["data"]["ord_no"] == "12345"

    mock_web_ctx.order_execution_service.handle_buy_stock.assert_awaited_once_with(
        "005930", "10", "70000", source="manual:수동매매", finalize_immediately=False
    )
    mock_web_ctx.virtual_trade_service.log_buy.assert_not_called()


@pytest.mark.asyncio
async def test_place_order_sell(web_client, mock_web_ctx):
    """POST /api/order (매도) 엔드포인트 테스트"""
    mock_web_ctx.order_execution_service.handle_sell_stock.return_value = ResCommonResponse(
        rt_cd="0", msg1="Order Placed", data={"ord_no": "12345"}
    )
    payload = {"code": "005930", "price": "70000", "qty": "10", "side": "sell"}
    response = web_client.post("/api/order", json=payload)
    assert response.status_code == 200
    mock_web_ctx.order_execution_service.handle_sell_stock.assert_awaited_once_with(
        "005930", "10", "70000", source="manual:수동매매", finalize_immediately=False
    )
    mock_web_ctx.virtual_trade_service.log_sell.assert_not_called()


@pytest.mark.asyncio
async def test_demo_mode_order_is_recorded_without_order_service(
    web_client, mock_web_ctx
):
    mock_web_ctx.full_config["deployment"] = {"demo_mode": True}
    mock_web_ctx.demo_market_data_service.place_order.return_value = {
        "rt_cd": "0",
        "msg1": "데모 주문이 기록되었습니다.",
        "data": {"ord_no": "DEMO-0001", "demo": True},
    }

    response = web_client.post(
        "/api/order",
        json={"code": "005930", "price": "70000", "qty": "2", "side": "buy"},
    )

    assert response.status_code == 200
    assert response.json()["data"]["demo"] is True
    mock_web_ctx.demo_market_data_service.place_order.assert_called_once_with(
        code="005930",
        side="buy",
        qty="2",
        price="70000",
    )
    mock_web_ctx.order_execution_service.handle_buy_stock.assert_not_awaited()


@pytest.mark.asyncio
async def test_place_order_invalid_side(web_client, mock_web_ctx):
    """POST /api/order 잘못된 side 테스트"""
    payload = {"code": "005930", "price": "70000", "qty": "10", "side": "invalid"}
    response = web_client.post("/api/order", json=payload)
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_real_order_without_confirmation_is_blocked(web_client, mock_web_ctx):
    mock_web_ctx.env.is_paper_trading = False
    mock_web_ctx.full_config["deployment"] = {
        "public_mode": False,
        "allow_live_trading": True,
    }

    payload = {"code": "005930", "price": "70000", "qty": "10", "side": "buy"}
    response = web_client.post("/api/order", json=payload)

    assert response.status_code == 400
    mock_web_ctx.order_execution_service.handle_buy_stock.assert_not_awaited()


@pytest.mark.asyncio
async def test_real_order_with_confirmation_calls_service(web_client, mock_web_ctx):
    mock_web_ctx.env.is_paper_trading = False
    mock_web_ctx.full_config["deployment"] = {
        "public_mode": False,
        "allow_live_trading": True,
    }
    mock_web_ctx.order_execution_service.handle_buy_stock.return_value = ResCommonResponse(
        rt_cd="0", msg1="Order Placed", data={"ord_no": "12345"}
    )

    payload = {
        "code": "005930",
        "price": "70000",
        "qty": "10",
        "side": "buy",
        "real_order_confirmation": "REAL",
    }
    response = web_client.post("/api/order", json=payload)

    assert response.status_code == 200
    mock_web_ctx.order_execution_service.handle_buy_stock.assert_awaited_once()


@pytest.mark.asyncio
async def test_operator_cannot_place_real_order(web_client, mock_web_ctx):
    mock_web_ctx.env.is_paper_trading = False
    mock_web_ctx.full_config["deployment"] = {
        "public_mode": False,
        "allow_live_trading": True,
    }
    _authenticate_as_operator(web_client, mock_web_ctx)

    response = web_client.post(
        "/api/order",
        json={
            "code": "005930",
            "price": "70000",
            "qty": "10",
            "side": "buy",
            "real_order_confirmation": "REAL",
        },
    )

    assert response.status_code == 403
    mock_web_ctx.order_execution_service.handle_buy_stock.assert_not_awaited()


@pytest.mark.asyncio
async def test_real_order_requires_global_live_trading_gate(web_client, mock_web_ctx):
    mock_web_ctx.env.is_paper_trading = False
    mock_web_ctx.full_config["deployment"] = {
        "public_mode": False,
        "allow_live_trading": False,
    }

    response = web_client.post(
        "/api/order",
        json={
            "code": "005930",
            "price": "70000",
            "qty": "10",
            "side": "buy",
            "real_order_confirmation": "REAL",
        },
    )

    assert response.json()["rt_cd"] == ErrorCode.ORDER_POLICY_BLOCKED.value
    assert response.json()["data"]["rule"] == "global_live_trading_disabled"
    mock_web_ctx.order_execution_service.handle_buy_stock.assert_not_awaited()


@pytest.mark.asyncio
async def test_public_mode_blocks_real_order_even_when_gate_enabled(web_client, mock_web_ctx):
    mock_web_ctx.env.is_paper_trading = False
    mock_web_ctx.full_config["deployment"] = {
        "public_mode": True,
        "allow_live_trading": True,
    }

    response = web_client.post(
        "/api/order",
        json={
            "code": "005930",
            "price": "70000",
            "qty": "10",
            "side": "buy",
            "real_order_confirmation": "REAL",
        },
    )

    assert response.json()["rt_cd"] == ErrorCode.ORDER_POLICY_BLOCKED.value
    assert response.json()["data"]["rule"] == "public_mode_live_trading_blocked"
    mock_web_ctx.order_execution_service.handle_buy_stock.assert_not_awaited()


@pytest.mark.asyncio
async def test_place_order_market_price_and_exception(web_client, mock_web_ctx):
    """POST /api/order 시장가(0) 주문 및 로깅 예외 테스트"""
    mock_web_ctx.order_execution_service.handle_buy_stock.return_value = ResCommonResponse(
        rt_cd="0", msg1="Success", data={}
    )
    mock_web_ctx.stock_query_service.handle_get_current_stock_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="Success", data={"price": "50000"}
    )

    # 1. 시장가 주문 -> API 성공만 반환하고 가상매매 기록은 체결 확인 이후 처리
    payload = {"code": "005930", "price": "0", "qty": "1", "side": "buy"}
    response = web_client.post("/api/order", json=payload)
    assert response.status_code == 200
    mock_web_ctx.virtual_trade_service.log_buy.assert_not_called()

    # 2. 기존 수동 로깅 경로가 없어 예외가 발생하지 않아야 함
    mock_web_ctx.virtual_trade_service.log_buy.side_effect = Exception("Logging Error")
    response = web_client.post("/api/order", json=payload)
    assert response.status_code == 200  # 예외가 발생해도 API는 성공해야 함


@pytest.mark.asyncio
async def test_place_order_market_price_api_fail(web_client, mock_web_ctx):
    """POST /api/order 시장가(0) 주문 시 현재가 조회 실패 테스트"""
    mock_web_ctx.order_execution_service.handle_buy_stock.return_value = ResCommonResponse(
        rt_cd="0", msg1="Success", data={}
    )
    mock_web_ctx.stock_query_service.handle_get_current_stock_price.return_value = ResCommonResponse(
        rt_cd="1", msg1="Fail", data=None
    )

    payload = {"code": "005930", "price": "0", "qty": "1", "side": "buy"}
    response = web_client.post("/api/order", json=payload)
    assert response.status_code == 200

    mock_web_ctx.virtual_trade_service.log_buy.assert_not_called()


@pytest.mark.asyncio
async def test_place_order_market_price_lookup_exception(web_client, mock_web_ctx):
    """POST /api/order 시장가(0) 주문 시 현재가 조회 중 예외 발생 테스트"""
    # 주문은 성공한다고 가정
    mock_web_ctx.order_execution_service.handle_buy_stock.return_value = ResCommonResponse(
        rt_cd="0", msg1="Success", data={}
    )
    
    # 현재가 조회 시 예외 발생 설정
    mock_web_ctx.stock_query_service.handle_get_current_stock_price.side_effect = Exception("Lookup Error")

    payload = {"code": "005930", "price": "0", "qty": "1", "side": "buy"}
    response = web_client.post("/api/order", json=payload)
    
    assert response.status_code == 200
    
    mock_web_ctx.virtual_trade_service.log_buy.assert_not_called()


def _install_overseas_manual_service(ctx, resp=None):
    """수동 해외주문은 kill-switch/저널 게이팅을 거쳐야 하므로 라우트가 직접 broker를
    호출하지 않는다. 게이팅 서비스를 mock 으로 심고 반환한다."""
    from unittest.mock import AsyncMock, MagicMock

    svc = MagicMock()
    svc.place_entry = AsyncMock(
        return_value=resp or ResCommonResponse(rt_cd="0", msg1="Order Placed", data={"ord_no": "A12345"})
    )
    svc.place_exit = AsyncMock(
        return_value=resp or ResCommonResponse(rt_cd="0", msg1="Order Placed", data={"ord_no": "A12345"})
    )
    ctx.overseas_manual_order_service = svc
    return svc


@pytest.mark.asyncio
async def test_place_overseas_limit_order_goes_through_gating_service(web_client, mock_web_ctx):
    """POST /api/overseas/order는 broker 직접 호출이 아니라 게이팅 서비스를 경유한다."""
    mock_web_ctx.market_mode = "overseas_us"
    svc = _install_overseas_manual_service(mock_web_ctx)

    payload = {
        "symbol": "AAPL",
        "exchange": "NASD",
        "side": "buy",
        "qty": 3,
        "limit_price": 190.25,
        "currency": "USD",
    }
    response = web_client.post("/api/overseas/order", json=payload)

    assert response.status_code == 200
    assert response.json()["data"]["ord_no"] == "A12345"
    svc.place_entry.assert_awaited_once_with(
        code="AAPL",
        exchange=OverseasExchange.NASD,
        qty=3,
        limit_price=190.25,
        signal={"source": "manual"},
    )
    mock_web_ctx.broker.place_overseas_limit_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_place_overseas_order_requires_overseas_mode(web_client, mock_web_ctx):
    """overseas_us가 enabled되지 않은 run에서는 해외 주문 endpoint가 닫혀 있어야 한다."""
    svc = _install_overseas_manual_service(mock_web_ctx)
    payload = {
        "symbol": "AAPL",
        "exchange": "NASD",
        "side": "buy",
        "qty": 1,
        "limit_price": 190.0,
        "currency": "USD",
    }
    response = web_client.post("/api/overseas/order", json=payload)

    assert response.status_code == 400
    svc.place_entry.assert_not_awaited()


@pytest.mark.asyncio
async def test_place_overseas_order_allowed_when_domestic_active_but_overseas_enabled(web_client, mock_web_ctx):
    """국내 active run에서도 overseas_us가 enabled이면 해외 수동 지정가 주문을 허용한다."""
    mock_web_ctx.market_mode = "domestic"
    mock_web_ctx.enabled_market_modes = ["domestic", "overseas_us"]
    svc = _install_overseas_manual_service(mock_web_ctx)

    payload = {
        "symbol": "AAPL",
        "exchange": "NASD",
        "side": "buy",
        "qty": 1,
        "limit_price": 190.0,
        "currency": "USD",
    }
    response = web_client.post("/api/overseas/order", json=payload)

    assert response.status_code == 200
    svc.place_entry.assert_awaited_once_with(
        code="AAPL",
        exchange=OverseasExchange.NASD,
        qty=1,
        limit_price=190.0,
        signal={"source": "manual"},
    )


@pytest.mark.asyncio
async def test_place_overseas_real_order_requires_allow_live_trading(web_client, mock_web_ctx):
    """실전 해외주문은 allow_live_trading=False이면 정책 차단 응답을 반환한다."""
    mock_web_ctx.market_mode = "overseas_us"
    mock_web_ctx.env.is_paper_trading = False
    mock_web_ctx.full_config["deployment"] = {
        "public_mode": False,
        "allow_live_trading": True,
    }
    mock_web_ctx.full_config["overseas_stock"] = {
        "allow_live_trading": False,
    }
    svc = _install_overseas_manual_service(mock_web_ctx)

    payload = {
        "symbol": "AAPL",
        "exchange": "NASD",
        "side": "buy",
        "qty": 1,
        "limit_price": 190.0,
        "currency": "USD",
        "real_order_confirmation": "REAL",
    }
    response = web_client.post("/api/overseas/order", json=payload)

    assert response.status_code == 200
    assert response.json()["rt_cd"] == ErrorCode.ORDER_POLICY_BLOCKED.value
    assert response.json()["data"]["rule"] == "overseas_live_trading_disabled"
    svc.place_entry.assert_not_awaited()


@pytest.mark.asyncio
async def test_place_overseas_order_rejects_disabled_exchange(web_client, mock_web_ctx):
    """overseas_stock.enabled_exchanges 밖의 거래소는 400으로 차단된다."""
    from types import SimpleNamespace

    mock_web_ctx.market_mode = "overseas_us"
    mock_web_ctx.full_config = SimpleNamespace(
        auth=SimpleNamespace(secret_key="secret"),
        overseas_stock=SimpleNamespace(enabled_exchanges=["NYSE"]),
    )
    svc = _install_overseas_manual_service(mock_web_ctx)

    payload = {
        "symbol": "AAPL",
        "exchange": "NASD",  # NYSE만 활성화 → NASD는 차단
        "side": "buy",
        "qty": 1,
        "limit_price": 190.0,
        "currency": "USD",
    }
    response = web_client.post("/api/overseas/order", json=payload)

    assert response.status_code == 400
    svc.place_entry.assert_not_awaited()


@pytest.mark.asyncio
async def test_place_overseas_order_sell_uses_place_exit(web_client, mock_web_ctx):
    """매도는 청산 경로(place_exit)로 위임된다."""
    mock_web_ctx.market_mode = "overseas_us"
    svc = _install_overseas_manual_service(mock_web_ctx)

    payload = {
        "symbol": "AAPL",
        "exchange": "NASD",
        "side": "sell",
        "qty": 2,
        "limit_price": 195.5,
        "currency": "USD",
    }
    response = web_client.post("/api/overseas/order", json=payload)

    assert response.status_code == 200
    svc.place_exit.assert_awaited_once_with(
        code="AAPL",
        exchange=OverseasExchange.NASD,
        qty=2,
        limit_price=195.5,
        reason="manual",
        signal={"source": "manual"},
    )
    svc.place_entry.assert_not_awaited()


@pytest.mark.asyncio
async def test_place_overseas_order_blocked_by_kill_switch(web_client, mock_web_ctx):
    """kill-switch 차단 응답을 라우트가 그대로 전달한다(국내 주문과 동일한 규율)."""
    mock_web_ctx.market_mode = "overseas_us"
    _install_overseas_manual_service(
        mock_web_ctx,
        resp=ResCommonResponse(
            rt_cd=ErrorCode.KILL_SWITCH_BLOCKED.value,
            msg1="Kill Switch 활성 — 일일 손실 한도 초과",
            data=None,
        ),
    )

    payload = {
        "symbol": "AAPL",
        "exchange": "NASD",
        "side": "buy",
        "qty": 1,
        "limit_price": 190.0,
        "currency": "USD",
    }
    response = web_client.post("/api/overseas/order", json=payload)

    assert response.status_code == 200
    assert response.json()["rt_cd"] == ErrorCode.KILL_SWITCH_BLOCKED.value


@pytest.mark.asyncio
async def test_place_overseas_order_without_service_returns_503(web_client, mock_web_ctx):
    """게이팅 서비스가 없으면 broker 로 우회하지 않고 503 으로 실패한다."""
    mock_web_ctx.market_mode = "overseas_us"
    mock_web_ctx.overseas_manual_order_service = None

    payload = {
        "symbol": "AAPL",
        "exchange": "NASD",
        "side": "buy",
        "qty": 1,
        "limit_price": 190.0,
        "currency": "USD",
    }
    response = web_client.post("/api/overseas/order", json=payload)

    assert response.status_code == 503
    mock_web_ctx.broker.place_overseas_limit_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_place_overseas_real_order_requires_confirmation_when_live_allowed(web_client, mock_web_ctx):
    """allow_live_trading=True 라도 확인 문자열이 없으면 400."""
    from types import SimpleNamespace

    mock_web_ctx.market_mode = "overseas_us"
    mock_web_ctx.env.is_paper_trading = False
    mock_web_ctx.full_config = SimpleNamespace(
        auth=SimpleNamespace(secret_key="secret"),
        deployment=SimpleNamespace(public_mode=False, allow_live_trading=True),
        overseas_stock=SimpleNamespace(
            allow_live_trading=True,
            enabled_exchanges=["NASD", "NYSE", "AMEX"],
        ),
    )
    svc = _install_overseas_manual_service(mock_web_ctx)

    payload = {
        "symbol": "AAPL",
        "exchange": "NASD",
        "side": "buy",
        "qty": 1,
        "limit_price": 190.0,
        "currency": "USD",
        # real_order_confirmation 누락
    }
    response = web_client.post("/api/overseas/order", json=payload)

    assert response.status_code == 400
    svc.place_entry.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancel_overseas_order_calls_broker(web_client, mock_web_ctx):
    """POST /api/overseas/order/cancel는 미체결 주문 취소를 브로커에 위임한다."""
    mock_web_ctx.market_mode = "overseas_us"
    mock_web_ctx.broker.cancel_overseas_order.return_value = ResCommonResponse(
        rt_cd="0", msg1="취소 완료", data={"odno": "39870"}
    )

    payload = {
        "symbol": "AAPL",
        "exchange": "NASD",
        "original_order_no": "0000039870",
        "qty": 1,
        "currency": "USD",
    }
    response = web_client.post("/api/overseas/order/cancel", json=payload)

    assert response.status_code == 200
    assert response.json()["rt_cd"] == "0"
    mock_web_ctx.broker.cancel_overseas_order.assert_awaited_once_with(
        symbol="AAPL",
        exchange=OverseasExchange.NASD,
        original_order_no="0000039870",
        qty=1,
        limit_price="0",
        rvse_cncl_dvsn_cd="02",
    )


@pytest.mark.asyncio
async def test_cancel_overseas_order_requires_overseas_mode(web_client, mock_web_ctx):
    """overseas_us가 enabled되지 않은 run에서는 해외 주문 취소가 닫혀 있어야 한다."""
    payload = {
        "symbol": "AAPL",
        "exchange": "NASD",
        "original_order_no": "0000039870",
        "qty": 1,
        "currency": "USD",
    }
    response = web_client.post("/api/overseas/order/cancel", json=payload)

    assert response.status_code == 400
    mock_web_ctx.broker.cancel_overseas_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancel_overseas_real_order_requires_allow_live_trading(web_client, mock_web_ctx):
    """실전 해외주문 취소도 allow_live_trading=False이면 정책 차단 응답을 반환한다."""
    mock_web_ctx.market_mode = "overseas_us"
    mock_web_ctx.env.is_paper_trading = False
    mock_web_ctx.full_config["deployment"] = {
        "public_mode": False,
        "allow_live_trading": True,
    }
    mock_web_ctx.full_config["overseas_stock"] = {
        "allow_live_trading": False,
    }

    payload = {
        "symbol": "AAPL",
        "exchange": "NASD",
        "original_order_no": "0000039870",
        "qty": 1,
        "currency": "USD",
        "real_order_confirmation": "REAL",
    }
    response = web_client.post("/api/overseas/order/cancel", json=payload)

    assert response.status_code == 200
    assert response.json()["rt_cd"] == ErrorCode.ORDER_POLICY_BLOCKED.value
    assert response.json()["data"]["rule"] == "overseas_live_trading_disabled"
    mock_web_ctx.broker.cancel_overseas_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_overseas_orders_requires_overseas_mode(web_client, mock_web_ctx):
    """overseas_us가 enabled되지 않은 run에서는 해외 주문 조회가 닫혀 있다."""
    response = web_client.get("/api/overseas/orders")
    assert response.status_code == 400
    mock_web_ctx.stock_query_service.get_overseas_order_history.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_overseas_orders_defaults_dates_to_today(web_client, mock_web_ctx):
    """날짜 미지정 시 market_clock 기준 오늘 날짜로 채워 조회한다."""
    mock_web_ctx.market_mode = "overseas_us"
    mock_web_ctx.market_clock.get_current_kst_time.return_value.strftime.return_value = "20260614"
    mock_web_ctx.stock_query_service.get_overseas_order_history.return_value = ResCommonResponse(
        rt_cd="0", msg1="ok", data={"orders": []}
    )

    response = web_client.get("/api/overseas/orders")

    assert response.status_code == 200
    assert response.json()["rt_cd"] == "0"
    mock_web_ctx.stock_query_service.get_overseas_order_history.assert_awaited_once_with(
        symbol="%",
        exchange="NASD",
        start_date="20260614",
        end_date="20260614",
        side_code="00",
        ccld_nccs_dvsn="00",
    )
