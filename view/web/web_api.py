"""
FastAPI 라우터: 웹 뷰용 API 엔드포인트.
서비스 레이어(StockQueryService, OrderExecutionService)를 직접 호출한다.
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from common.types import ErrorCode
from fastapi import APIRouter, Request, Form, Response, HTTPException, Depends
from fastapi.responses import RedirectResponse, JSONResponse

router = APIRouter(prefix="/api")
_ctx = None  # 전역 변수로 선언

@router.post("/auth/login")
async def login(response: Response, username: str = Form(...), password: str = Form(...)):
    ctx = _get_ctx()
    auth_config = ctx.full_config.get("auth", {})

    print(f"\n=== 로그인 시도 ===")
    print(f"입력 ID: {username} / PW: {password}")
    print(f"설정 ID: {auth_config.get('username')} / PW: {auth_config.get('password')}")
    print(f"==================\n")

    if username == auth_config.get("username") and password == auth_config.get("password"):
        response = JSONResponse(content={"success": True})
        # 쿠키 설정
        response.set_cookie(
            key="access_token", 
            value=auth_config.get("secret_key"), 
            httponly=True,
            samesite="lax" # 로컬 테스트 시 안정성 위함
        )
        return response
    
    return JSONResponse(content={"success": False, "msg": "아이디 또는 비밀번호가 틀렸습니다."}, status_code=401)

# 로그인 여부를 확인하는 공통 함수
def check_auth(request: Request):
    ctx = _get_ctx()
    expected_token = ctx.env.active_config.get("auth", {}).get("secret_key")
    token = request.cookies.get("access_token")
    
    if token != expected_token:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return True

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


def set_ctx(ctx): # set_context에서 set_ctx로 변경
    global _ctx
    _ctx = ctx


def _get_ctx():
    if _ctx is None:
        # 이 부분이 브라우저에 보이는 에러 메시지를 생성합니다.
        from fastapi import HTTPException
        raise HTTPException(status_code=503, detail="서비스가 초기화되지 않았습니다.")
    return _ctx


# --- 엔드포인트 ---

@router.get("/status")
async def get_status():
    """시장 상태 및 환경 정보."""
    ctx = _get_ctx()
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
    
    # 1. 기존 응답 직렬화
    result = _serialize_response(resp)
    
    # 2. [수정됨] 환경 설정(env) 찾기 우선순위 적용
    env = None
    
    # (1순위) 웹 앱 Context가 직접 가진 env 사용 (가장 확실)
    if hasattr(ctx, 'env') and ctx.env:
        env = ctx.env
    # (2순위) Broker를 통해 env 접근 (1단계 적용 시 작동함)
    elif hasattr(ctx, 'broker') and ctx.broker and hasattr(ctx.broker, 'env'):
        env = ctx.broker.env
    
    # 3. 계좌 정보 추출
    if env:
        # 설정값 가져오기 (active_config가 없으면 전체 설정 로드 시도)
        config = getattr(env, 'active_config', None) or {}
        if not config and hasattr(env, 'get_full_config'):
             try:
                 config = env.get_full_config()
             except:
                 config = {}

        # 계좌번호 키 찾기 (설정 파일마다 키 이름이 다를 수 있음)
        acc_no = (
            config.get("stock_account_number") or 
            config.get("CANO") or 
            config.get("account_number") or 
            getattr(env, 'stock_account_number', None) or 
            getattr(env, 'paper_stock_account_number', None) or
            "번호없음"
        )
        
        acc_type = "모의투자" if getattr(env, 'is_paper_trading', False) else "실전투자"
        
        result['account_info'] = {
            "number": acc_no,
            "type": acc_type
        }
    else:
        result['account_info'] = {
            "number": "연동실패",
            "type": "Env Not Found"
        }
        
    return result


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
