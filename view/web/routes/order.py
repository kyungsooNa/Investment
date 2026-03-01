"""
주문 관련 API 엔드포인트 (order.html).
"""
from fastapi import APIRouter, HTTPException
from view.web.api_common import _get_ctx, _serialize_response, OrderRequest

router = APIRouter()


@router.post("/order")
async def place_order(req: OrderRequest):
    """매수/매도 주문 (성공 시 가상 매매 기록에도 '수동매매'로 저장)"""
    ctx = _get_ctx()

    # 1. 실제/모의 투자 주문 전송
    if req.side == "buy":
        resp = await ctx.order_execution_service.handle_buy_stock(req.code, req.qty, req.price)
    elif req.side == "sell":
        resp = await ctx.order_execution_service.handle_sell_stock(req.code, req.qty, req.price)
    else:
        raise HTTPException(status_code=400, detail="side는 'buy' 또는 'sell'이어야 합니다.")

    # 2. [추가됨] 주문 성공 시 가상 매매 장부에도 기록 (전략명: "수동매매")
    if resp and resp.rt_cd == "0":
        # virtual_manager가 초기화되어 있는지 확인
        if hasattr(ctx, 'virtual_manager') and ctx.virtual_manager:
            try:
                # 가격 형변환 (문자열 -> 숫자)
                price_val = int(req.price) if req.price and req.price.isdigit() else 0

                # 시장가 주문(price=0)인 경우 현재가를 조회하여 사용
                if price_val == 0 and getattr(ctx, 'stock_query_service', None):
                    try:
                        price_resp = await ctx.stock_query_service.handle_get_current_stock_price(req.code)
                        if price_resp and price_resp.rt_cd == "0" and isinstance(price_resp.data, dict):
                            price_str = str(price_resp.data.get('price', '0'))
                            price_val = int(price_str) if price_str.isdigit() else 0
                    except Exception:
                        pass

                if req.side == "buy":
                    # 매수 기록 (전략명: 수동매매)
                    ctx.virtual_manager.log_buy("수동매매", req.code, price_val)
                elif req.side == "sell":
                    # 매도 기록 (수익률 계산됨)
                    ctx.virtual_manager.log_sell(req.code, price_val)

            except Exception as e:
                print(f"[WebAPI] 수동매매 기록 중 오류 발생: {e}")

    return _serialize_response(resp)
