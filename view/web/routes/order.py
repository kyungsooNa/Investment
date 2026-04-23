"""
주문 관련 API 엔드포인트 (order.html).
"""
from fastapi import APIRouter, HTTPException
from view.web.api_common import _get_ctx, _serialize_response, OrderRequest

router = APIRouter()


@router.post("/order")
async def place_order(req: OrderRequest):
    """매수/매도 주문. 가상 매매 기록은 실제 체결 확인 후 OrderExecutionService가 처리한다."""
    ctx = _get_ctx()
    t_start = ctx.pm.start_timer()

    # 1. 실제/모의 투자 주문 전송
    if req.side == "buy":
        resp = await ctx.order_execution_service.handle_buy_stock(
            req.code,
            req.qty,
            req.price,
            source="manual:수동매매",
            finalize_immediately=False,
        )
    elif req.side == "sell":
        resp = await ctx.order_execution_service.handle_sell_stock(
            req.code,
            req.qty,
            req.price,
            source="manual:수동매매",
            finalize_immediately=False,
        )
    else:
        raise HTTPException(status_code=400, detail="side는 'buy' 또는 'sell'이어야 합니다.")

    ctx.pm.log_timer("place_order", t_start)
    return _serialize_response(resp)
