"""
랭킹/시가총액 관련 API 엔드포인트 (ranking.html, marketcap.html).
"""
from fastapi import APIRouter, HTTPException
from common.types import ErrorCode
from view.web.api_common import _get_ctx, _serialize_response, _serialize_list_items

router = APIRouter()


@router.get("/ranking/progress")
async def get_ranking_progress():
    """투자자 랭킹 수집 진행률 조회."""
    ctx = _get_ctx()
    if ctx.ranking_task:
        return ctx.ranking_task.get_investor_ranking_progress()
    return {"running": False, "processed": 0, "total": 0, "collected": 0, "elapsed": 0.0}


@router.get("/ranking/newhigh")
async def get_newhigh():
    """52주 신고가 종목 목록을 반환한다.

    DB 조회 → In-memory 캐시 → 백그라운드 갱신 트리거 순으로 데이터를 가져온다.
    """
    ctx = _get_ctx()
    svc = getattr(ctx, "newhigh_service", None)
    if not svc:
        return {"rt_cd": "1", "msg1": "NewHighService 미설정", "data": None}
    resp = await svc.get_newhigh_list()
    return {"rt_cd": resp.rt_cd, "msg1": resp.msg1, "data": resp.data}


@router.get("/ranking/newhigh_progress")
async def get_newhigh_progress():
    """신고가 탐색 진행률을 반환한다."""
    ctx = _get_ctx()
    if hasattr(ctx, "newhigh_task") and ctx.newhigh_task:
        return ctx.newhigh_task.get_progress()
    return {"running": False, "processed": 0, "total": 0, "collected": 0, "elapsed": 0.0}


@router.get("/ranking/minervini_stage2")
async def get_minervini_stage2():
    """Minervini Stage 2 종목 목록을 RS 순으로 반환한다.

    각 항목은 dict: { code, name, stck_prpr, prdy_ctrt, stage, rs_rating, market_cap }
    조회 우선순위(DB → in-memory 캐시 → 백그라운드 갱신 트리거)는 MinerviniStageService에서 처리한다.
    """
    ctx = _get_ctx()
    svc = getattr(ctx, "minervini_stage_service", None)
    if not svc:
        return {"rt_cd": "1", "msg1": "MinerviniStageService 미설정", "data": None}
    resp = await svc.get_stage2_list()
    return {"rt_cd": resp.rt_cd, "msg1": resp.msg1, "data": resp.data}


@router.get("/ranking/theme_leaders")
async def get_theme_leaders(include_industry: bool = False):
    """테마(선택적으로 업종 포함) 그룹별 주도주(RS 상위)를 그룹 강도순으로 반환한다.

    각 그룹: { normalized_name, sources, group_rs_median, member_count, leaders[...] }
    데이터 미수집 시 rt_cd="0", data=[] 로 응답한다(진행률 폴링 없음).
    """
    ctx = _get_ctx()
    svc = getattr(ctx, "theme_leader_service", None)
    if not svc:
        return {"rt_cd": "1", "msg1": "ThemeLeaderService 미설정", "data": None}
    cats = ("theme", "industry") if include_industry else ("theme",)
    resp = await svc.get_theme_leaders(category_types=cats)
    return {"rt_cd": resp.rt_cd, "msg1": resp.msg1, "data": resp.data}


@router.get("/ranking/{category}")
async def get_ranking(category: str):
    """랭킹 조회 (rise/fall/volume/trading_value/foreign_buy/foreign_sell/inst_buy/inst_sell/prsn_buy/prsn_sell)."""
    ctx = _get_ctx()
    t_start = ctx.pm.start_timer()
    valid = ("rise", "fall", "volume", "trading_value",
             "foreign_buy", "foreign_sell", "inst_buy", "inst_sell", "prsn_buy", "prsn_sell",
             "program_buy", "program_sell")
    if category not in valid:
        raise HTTPException(status_code=400, detail=f"category는 {', '.join(valid)} 중 하나여야 합니다.")

    resp = await ctx.stock_query_service.handle_get_top_stocks(category)

    ctx.pm.log_timer(f"get_ranking({category})", t_start)
    if resp and resp.rt_cd == ErrorCode.SUCCESS.value:
        return {
            "rt_cd": resp.rt_cd,
            "msg1": resp.msg1,
            "data": _serialize_list_items(resp.data)
        }
    return _serialize_response(resp)




@router.get("/top-market-cap")
async def get_top_market_cap(limit: int = 20, market: str = "0001"):
    """시가총액 상위 종목. market: 0001=거래소(코스피), 1001=코스닥"""
    ctx = _get_ctx()
    t_start = ctx.pm.start_timer()
    if market not in ("0000", "0001", "1001", "2001"):
        market = "0001"
    resp = await ctx.broker.get_top_market_cap_stocks_code(market, limit)
    ctx.pm.log_timer(f"get_top_market_cap({market}, {limit})", t_start)
    if resp and resp.rt_cd == ErrorCode.SUCCESS.value:
        items = resp.data or []
        data = []
        for idx, item in enumerate(items, 1):
            get = (lambda k: getattr(item, k, None)) if not isinstance(item, dict) else item.get
            data.append({
                "rank": str(idx),
                "name": get("hts_kor_isnm") or "",
                "code": get("mksc_shrn_iscd") or get("iscd") or "",
                "current_price": get("stck_prpr") or "0",
                "change_rate": get("prdy_ctrt") or "0",
                "market_cap": get("stck_avls") or "0",
            })
        return {"rt_cd": resp.rt_cd, "msg1": resp.msg1, "data": data}
    return _serialize_response(resp)
