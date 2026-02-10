"""
FastAPI 라우터: 웹 뷰용 API 엔드포인트.
서비스 레이어(StockQueryService, OrderExecutionService)를 직접 호출한다.
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from common.types import ErrorCode

router = APIRouter(prefix="/api")


def _serialize_response(resp):
    """ResCommonResponse를 JSON 직렬화 가능한 dict로 변환."""
    if resp is None:
        return {"rt_cd": "999", "msg1": "응답 없음", "data": None}
    if hasattr(resp, 'to_dict'):
        return resp.to_dict()
    return {"rt_cd": str(resp.rt_cd), "msg1": str(resp.msg1), "data": resp.data}


def _serialize_list_items(items):
    """dataclass 리스트를 dict 리스트로 변환."""
    result = []
    for item in (items or []):
        if hasattr(item, 'to_dict'):
            result.append(item.to_dict())
        elif isinstance(item, dict):
            result.append(item)
        else:
            result.append(str(item))
    return result


# --- Pydantic 모델 ---
class OrderRequest(BaseModel):
    code: str
    price: str
    qty: str
    side: str  # "buy" or "sell"


class EnvironmentRequest(BaseModel):
    is_paper: bool


# --- 컨텍스트 참조 (web_main.py에서 설정) ---
_ctx = None


def set_context(ctx):
    global _ctx
    _ctx = ctx


def _get_ctx():
    if _ctx is None or not _ctx.initialized:
        raise HTTPException(status_code=503, detail="서비스가 초기화되지 않았습니다.")
    return _ctx


# --- 엔드포인트 ---

@router.get("/status")
async def get_status():
    """시장 상태 및 환경 정보."""
    ctx = _ctx
    if ctx is None:
        return {"market_open": False, "env_type": "미설정", "current_time": "", "initialized": False}
    return {
        "market_open": ctx.is_market_open(),
        "env_type": ctx.get_env_type(),
        "current_time": ctx.get_current_time_str(),
        "initialized": ctx.initialized
    }


@router.get("/stock/{code}")
async def get_stock_price(code: str):
    """현재가 조회."""
    ctx = _get_ctx()
    resp = await ctx.stock_query_service.handle_get_current_stock_price(code)
    return _serialize_response(resp)


@router.get("/balance")
async def get_balance():
    """계좌 잔고 조회."""
    ctx = _get_ctx()
    resp = await ctx.stock_query_service.handle_get_account_balance()
    return _serialize_response(resp)


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
                
                if req.side == "buy":
                    # 매수 기록 (전략명: 수동매매)
                    ctx.virtual_manager.log_buy("수동매매", req.code, price_val)
                elif req.side == "sell":
                    # 매도 기록 (수익률 계산됨)
                    ctx.virtual_manager.log_sell(req.code, price_val)
                    
            except Exception as e:
                print(f"[WebAPI] 수동매매 기록 중 오류 발생: {e}")

    return _serialize_response(resp)


@router.get("/ranking/{category}")
async def get_ranking(category: str):
    """랭킹 조회 (rise/fall/volume/trading_value)."""
    ctx = _get_ctx()
    if category not in ("rise", "fall", "volume", "trading_value"):
        raise HTTPException(status_code=400, detail="category는 rise, fall, volume, trading_value 중 하나여야 합니다.")

    resp = await ctx.stock_query_service.handle_get_top_stocks(category)

    if resp and resp.rt_cd == ErrorCode.SUCCESS.value:
        return {
            "rt_cd": resp.rt_cd,
            "msg1": resp.msg1,
            "data": _serialize_list_items(resp.data)
        }
    return _serialize_response(resp)


@router.get("/top-market-cap")
async def get_top_market_cap(limit: int = 10):
    """시가총액 상위 종목."""
    ctx = _get_ctx()
    resp = await ctx.stock_query_service.handle_get_top_market_cap_stocks_code("J", limit)
    if resp and resp.rt_cd == ErrorCode.SUCCESS.value:
        return {
            "rt_cd": resp.rt_cd,
            "msg1": resp.msg1,
            "data": _serialize_list_items(resp.data)
        }
    return _serialize_response(resp)


@router.post("/environment")
async def change_environment(req: EnvironmentRequest):
    """거래 환경 변경 (모의/실전)."""
    ctx = _ctx
    if ctx is None:
        raise HTTPException(status_code=503, detail="서비스가 초기화되지 않았습니다.")
    success = await ctx.initialize_services(is_paper_trading=req.is_paper)
    if not success:
        raise HTTPException(status_code=500, detail="환경 전환 실패 (토큰 발급 오류)")
    return {"success": True, "env_type": ctx.get_env_type()}

@router.get("/virtual/summary")
async def get_virtual_summary():
    """가상 매매 요약 정보 조회"""
    ctx = _get_ctx()
    # ctx에 virtual_manager가 초기화되어 있어야 합니다.
    if not hasattr(ctx, 'virtual_manager'):
        return {"total_trades": 0, "win_rate": 0, "avg_return": 0}
        
    return ctx.virtual_manager.get_summary()

@router.get("/virtual/history")
async def get_virtual_history():
    """가상 매매 전체 기록 조회"""
    ctx = _get_ctx()
    if not hasattr(ctx, 'virtual_manager'):
        return []
        
    # DataFrame을 dict list로 변환해서 반환
    return ctx.virtual_manager.get_all_trades()
