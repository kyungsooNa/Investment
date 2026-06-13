"""
주문 관련 테스트 (order.html).
"""
import pytest
from common.overseas_types import OverseasExchange
from common.types import ErrorCode, ResCommonResponse


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
async def test_place_order_invalid_side(web_client, mock_web_ctx):
    """POST /api/order 잘못된 side 테스트"""
    payload = {"code": "005930", "price": "70000", "qty": "10", "side": "invalid"}
    response = web_client.post("/api/order", json=payload)
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_real_order_without_confirmation_is_blocked(web_client, mock_web_ctx):
    mock_web_ctx.env.is_paper_trading = False

    payload = {"code": "005930", "price": "70000", "qty": "10", "side": "buy"}
    response = web_client.post("/api/order", json=payload)

    assert response.status_code == 400
    mock_web_ctx.order_execution_service.handle_buy_stock.assert_not_awaited()


@pytest.mark.asyncio
async def test_real_order_with_confirmation_calls_service(web_client, mock_web_ctx):
    mock_web_ctx.env.is_paper_trading = False
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


@pytest.mark.asyncio
async def test_place_overseas_limit_order_calls_broker(web_client, mock_web_ctx):
    """POST /api/overseas/order는 해외 mode에서 수동 지정가 주문만 브로커에 위임한다."""
    mock_web_ctx.market_mode = "overseas_us"
    mock_web_ctx.broker.place_overseas_limit_order.return_value = ResCommonResponse(
        rt_cd="0", msg1="Order Placed", data={"ord_no": "A12345"}
    )

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
    mock_web_ctx.broker.place_overseas_limit_order.assert_awaited_once_with(
        symbol="AAPL",
        exchange=OverseasExchange.NASD,
        side="buy",
        qty=3,
        limit_price=190.25,
    )


@pytest.mark.asyncio
async def test_place_overseas_order_requires_overseas_mode(web_client, mock_web_ctx):
    """국내 mode에서는 해외 주문 endpoint가 닫혀 있어야 한다."""
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
    mock_web_ctx.broker.place_overseas_limit_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_place_overseas_real_order_requires_allow_live_trading(web_client, mock_web_ctx):
    """실전 해외주문은 allow_live_trading=False이면 정책 차단 응답을 반환한다."""
    mock_web_ctx.market_mode = "overseas_us"
    mock_web_ctx.env.is_paper_trading = False

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
    mock_web_ctx.broker.place_overseas_limit_order.assert_not_awaited()
