"""
종목 조회 관련 API 엔드포인트 (index.html).
현재가, 차트(OHLCV), 기술 지표, 시장 상태, 환경 전환.
"""
import time
from fastapi import APIRouter, HTTPException
from view.web.api_common import _get_ctx, _serialize_response, EnvironmentRequest
import view.web.api_common as api_common

router = APIRouter()

# /api/status 결과를 5초간 캐시하여 페이지 전환 시 broker API 반복 호출 방지
_status_cache = None
_status_cache_ts = 0.0
_STATUS_CACHE_TTL = 5.0


@router.get("/status")
async def get_status():
    """시장 상태 및 환경 정보."""
    global _status_cache, _status_cache_ts

    ctx = _get_ctx()
    if ctx is None:
        return {"market_open": False, "env_type": "미설정", "current_time": "", "initialized": False}

    now = time.monotonic()
    if _status_cache is not None and (now - _status_cache_ts) < _STATUS_CACHE_TTL:
        # 캐시된 결과 반환 (현재 시각만 갱신)
        _status_cache["current_time"] = ctx.get_current_time_str()
        return _status_cache

    result = {
        "market_open": await ctx.is_market_open_now(),
        "env_type": ctx.get_env_type(),
        "current_time": ctx.get_current_time_str(),
        "initialized": ctx.initialized
    }
    _status_cache = result
    _status_cache_ts = now
    return result


@router.get("/stock/search")
async def search_stock_by_name(q: str = ""):
    """종목명 부분 일치 검색 (자동완성용)."""
    ctx = _get_ctx()
    if not q or len(q.strip()) == 0:
        return {"results": []}
    results = ctx.stock_code_mapper.search_by_name(q.strip())
    return {"results": results}


@router.get("/stock/{code}")
async def get_stock_price(code: str):
    """현재가 조회. 종목명이 들어오면 종목코드로 변환 후 조회."""
    ctx = _get_ctx()
    # 숫자가 아닌 입력(종목명)이면 코드로 변환
    if not code.isdigit():
        resolved = ctx.stock_code_mapper.get_code_by_name(code)
        if not resolved:
            return {"rt_cd": "1", "msg1": f"종목명 '{code}'에 해당하는 종목코드를 찾을 수 없습니다.", "data": None}
        code = resolved
    t_start = ctx.pm.start_timer()
    resp = await ctx.stock_query_service.handle_get_current_stock_price(code)
    result = _serialize_response(resp)

    ctx.pm.log_timer(f"get_stock_price({code})", t_start)
    return result


@router.get("/chart/{code}")
async def get_stock_chart(code: str, period: str = "D", indicators: bool = False):
    """종목의 OHLCV 차트 데이터 조회 (기본 일봉). indicators=true 시 MA+BB 지표 포함."""
    ctx = _get_ctx()
    t_start = ctx.pm.start_timer()
    if indicators:
        resp = await ctx.stock_query_service.get_ohlcv_with_indicators(code, period)
    else:
        resp = await ctx.stock_query_service.get_ohlcv(code, period)
    result = _serialize_response(resp)
    
    ctx.pm.log_timer(f"get_stock_chart({code}, indicators={indicators})", t_start)
    return result


@router.get("/indicator/bollinger/{code}")
async def get_bollinger_bands(code: str, period: int = 20, std_dev: float = 2.0):
    """볼린저 밴드 조회 (기본: 20일, 2표준편차)"""
    ctx = _get_ctx()
    t_start = ctx.pm.start_timer()
    resp = await ctx.indicator_service.get_bollinger_bands(code, period, std_dev)
    result = _serialize_response(resp)
    
    ctx.pm.log_timer(f"get_bollinger_bands({code})", t_start)
    return result


@router.get("/indicator/rsi/{code}")
async def get_rsi(code: str, period: int = 14):
    """RSI 조회 (기본: 14일)"""
    ctx = _get_ctx()
    t_start = ctx.pm.start_timer()
    resp = await ctx.indicator_service.get_rsi(code, period)
    result = _serialize_response(resp)
    
    ctx.pm.log_timer(f"get_rsi({code})", t_start)
    return result


@router.get("/indicator/ma/{code}")
async def get_moving_average(code: str, period: int = 20, method: str = "sma"):
    """이동평균선 조회 (기본: 20일, sma)"""
    ctx = _get_ctx()
    t_start = ctx.pm.start_timer()
    resp = await ctx.indicator_service.get_moving_average(code, period, method)
    result = _serialize_response(resp)
    
    ctx.pm.log_timer(f"get_moving_average({code})", t_start)
    return result


@router.post("/environment")
async def change_environment(req: EnvironmentRequest):
    """거래 환경 변경 (모의/실전)."""
    global _status_cache, _status_cache_ts
    ctx = api_common._ctx
    if ctx is None:
        raise HTTPException(status_code=503, detail="서비스가 초기화되지 않았습니다.")
    success = await ctx.initialize_services(is_paper_trading=req.is_paper)
    # 환경 전환 시 상태 캐시 무효화
    _status_cache = None
    _status_cache_ts = 0.0
    if not success:
        raise HTTPException(status_code=500, detail="환경 전환 실패 (토큰 발급 오류)")
    ctx.start_background_tasks()
    return {"success": True, "env_type": ctx.get_env_type()}
