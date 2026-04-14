"""
랭킹/시가총액 관련 API 엔드포인트 (ranking.html, marketcap.html).
"""
import asyncio
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


@router.get("/ranking/minervini_stage2")
async def get_minervini_stage2():
    """Minervini Stage 2 종목 목록을 RS 순으로 반환한다.

    1차: DB(daily_prices)에서 최신 거래일의 Stage2 종목을 조회한다.
    2차: DB에 데이터가 없으면 MinerviniUpdateTask의 in-memory 캐시를 사용한다.
    3차: 캐시도 없고 갱신 중이면 수집 대기 상태(data=None, running=True)를 반환하고,
         갱신 중이 아니면 백그라운드 갱신을 트리거한 뒤 같은 응답을 반환한다.
    각 항목은 dict: { code, name, stck_prpr, prdy_ctrt, stage, rs_rating, market_cap }
    """
    ctx = _get_ctx()

    # 1차: DB 조회
    stock_repo = getattr(ctx, "stock_repository", None)
    if stock_repo:
        try:
            latest_date = await stock_repo.get_latest_trade_date()
            if latest_date:
                db_items = await stock_repo.get_minervini_stage2_stocks(latest_date)
                if db_items:
                    data = [
                        {
                            "code": it.get("code", ""),
                            "name": it.get("name", ""),
                            "stck_prpr": str(it.get("current_price") or 0),
                            "prdy_ctrt": str(it.get("change_rate") or 0),
                            "stage": it.get("minervini_stage", 2),
                            "rs_rating": it.get("rs_rating") or 0,
                            "market_cap": it.get("market_cap") or 0,
                        }
                        for it in db_items
                    ]
                    return {"rt_cd": "0", "msg1": "성공", "data": data}
        except Exception:
            pass

    # 2차: in-memory 캐시
    task = getattr(ctx, "minervini_update_task", None)
    if not task:
        return {"rt_cd": "1", "msg1": "MinerviniUpdateTask 미설정", "data": None}

    cache = await task.get_minervini_stage2_cache()
    if cache:
        return {"rt_cd": "0", "msg1": "성공", "data": cache}

    # 3차: 데이터 없음 — 갱신 트리거 후 수집 대기 상태 반환
    progress = task.get_progress()
    if not progress.get("running"):
        asyncio.create_task(task.refresh_minervini_stage2())

    return {"rt_cd": "0", "msg1": "수집 중", "data": []}


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
