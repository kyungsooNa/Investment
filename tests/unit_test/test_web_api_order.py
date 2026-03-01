"""
주문 관련 테스트 (order.html).
"""
import pytest
from common.types import ResCommonResponse


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

    mock_web_ctx.order_execution_service.handle_buy_stock.assert_awaited_once_with("005930", "10", "70000")
    mock_web_ctx.virtual_manager.log_buy.assert_called_once()


@pytest.mark.asyncio
async def test_place_order_sell(web_client, mock_web_ctx):
    """POST /api/order (매도) 엔드포인트 테스트"""
    mock_web_ctx.order_execution_service.handle_sell_stock.return_value = ResCommonResponse(
        rt_cd="0", msg1="Order Placed", data={"ord_no": "12345"}
    )
    payload = {"code": "005930", "price": "70000", "qty": "10", "side": "sell"}
    response = web_client.post("/api/order", json=payload)
    assert response.status_code == 200
    mock_web_ctx.order_execution_service.handle_sell_stock.assert_awaited_once()
    mock_web_ctx.virtual_manager.log_sell.assert_called_once()


@pytest.mark.asyncio
async def test_place_order_invalid_side(web_client, mock_web_ctx):
    """POST /api/order 잘못된 side 테스트"""
    payload = {"code": "005930", "price": "70000", "qty": "10", "side": "invalid"}
    response = web_client.post("/api/order", json=payload)
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_place_order_market_price_and_exception(web_client, mock_web_ctx):
    """POST /api/order 시장가(0) 주문 및 로깅 예외 테스트"""
    mock_web_ctx.order_execution_service.handle_buy_stock.return_value = ResCommonResponse(
        rt_cd="0", msg1="Success", data={}
    )
    mock_web_ctx.stock_query_service.handle_get_current_stock_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="Success", data={"price": "50000"}
    )

    # 1. 시장가 주문 -> 현재가 조회 호출 확인
    payload = {"code": "005930", "price": "0", "qty": "1", "side": "buy"}
    response = web_client.post("/api/order", json=payload)
    assert response.status_code == 200
    mock_web_ctx.virtual_manager.log_buy.assert_called_with("수동매매", "005930", 50000)

    # 2. 로깅 중 예외 발생 테스트
    mock_web_ctx.virtual_manager.log_buy.side_effect = Exception("Logging Error")
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

    # 가격이 0으로 전달되었는지 확인 (API 실패 시 0 유지)
    mock_web_ctx.virtual_manager.log_buy.assert_called_with("수동매매", "005930", 0)
