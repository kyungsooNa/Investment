"""
주문 관련 API 엔드포인트 (order.html).
"""
from fastapi import APIRouter, HTTPException
from fastapi import Query
from common.overseas_types import OverseasOrderRequest
from common.types import ErrorCode, ResCommonResponse
from view.web.api_common import _get_ctx, _serialize_response, OrderRequest

router = APIRouter()


def _is_real_trading_mode(ctx) -> bool:
    env = getattr(ctx, "env", None)
    return not bool(getattr(env, "is_paper_trading", True))


@router.post("/order")
async def place_order(req: OrderRequest):
    """매수/매도 주문. 가상 매매 기록은 실제 체결 확인 후 OrderExecutionService가 처리한다."""
    ctx = _get_ctx()
    t_start = ctx.pm.start_timer()

    if _is_real_trading_mode(ctx) and req.real_order_confirmation != "REAL":
        raise HTTPException(status_code=400, detail="실전 주문 확인 문자열이 필요합니다.")

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


@router.post("/overseas/order")
async def place_overseas_order(req: OverseasOrderRequest):
    """해외주식 수동 지정가 주문. v1은 미국 3시장 + USD 지정가만 지원한다."""
    ctx = _get_ctx()
    if getattr(ctx, "market_mode", "domestic") != "overseas_us":
        raise HTTPException(status_code=400, detail="해외주식 주문은 overseas_us mode에서만 사용할 수 있습니다.")

    cfg = getattr(getattr(ctx, "full_config", None), "overseas_stock", None)
    enabled_exchanges = set(getattr(cfg, "enabled_exchanges", ["NASD", "NYSE", "AMEX"]))
    if req.exchange.value not in enabled_exchanges:
        raise HTTPException(status_code=400, detail="활성화되지 않은 해외 거래소입니다.")
    if req.currency != "USD":
        raise HTTPException(status_code=400, detail="v1은 USD 주문만 지원합니다.")
    if _is_real_trading_mode(ctx):
        if not bool(getattr(cfg, "allow_live_trading", False)):
            return _serialize_response(
                ResCommonResponse(
                    rt_cd=ErrorCode.ORDER_POLICY_BLOCKED.value,
                    msg1="실전 해외주식 주문은 overseas_stock.allow_live_trading=true 설정 전까지 차단됩니다.",
                    data={"rule": "overseas_live_trading_disabled"},
                )
            )
        if req.real_order_confirmation != "REAL":
            raise HTTPException(status_code=400, detail="실전 주문 확인 문자열이 필요합니다.")

    resp = await ctx.broker.place_overseas_limit_order(
        symbol=req.symbol,
        exchange=req.exchange,
        side=req.side,
        qty=req.qty,
        limit_price=req.limit_price,
    )
    return _serialize_response(resp)


@router.get("/overseas/orders")
async def get_overseas_orders(
    symbol: str = Query("%"),
    exchange: str = Query("NASD"),
    start_date: str = Query(""),
    end_date: str = Query(""),
    side_code: str = Query("00"),
    ccld_nccs_dvsn: str = Query("00"),
):
    ctx = _get_ctx()
    if getattr(ctx, "market_mode", "domestic") != "overseas_us":
        raise HTTPException(status_code=400, detail="해외주식 주문 조회는 overseas_us mode에서만 사용할 수 있습니다.")
    if not start_date or not end_date:
        today = ctx.market_clock.get_current_kst_time().strftime("%Y%m%d")
        start_date = start_date or today
        end_date = end_date or today
    resp = await ctx.stock_query_service.get_overseas_order_history(
        symbol=symbol,
        exchange=exchange,
        start_date=start_date,
        end_date=end_date,
        side_code=side_code,
        ccld_nccs_dvsn=ccld_nccs_dvsn,
    )
    return _serialize_response(resp)
