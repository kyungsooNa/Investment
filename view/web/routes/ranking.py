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


@router.get("/ranking/minervini_stage2")
async def get_minervini_stage2():
    """Minervini Stage 2에 해당하는 전체 종목 목록을 RS 순으로 반환한다.

    각 항목은 dict: { code, name, stck_prpr, prdy_ctrt, stage, rs_rating, market_cap }
    """
    ctx = _get_ctx()
    if not ctx:
        return {"rt_cd": "1", "msg1": "Context 미설정", "data": None}

    codes = list(ctx.stock_code_repository.name_to_code.values())
    minervini = ctx.minervini_stage_service
    sqs = ctx.stock_query_service
    rs_svc = getattr(ctx, 'rs_rating_service', None)

    import asyncio

    sem = asyncio.Semaphore(20)

    async def check_code(code: str):
        async with sem:
            try:
                res = await asyncio.wait_for(minervini.get_stage_for_code(code), timeout=3.0)
            except Exception:
                return None
            stage = res[0] if isinstance(res, (list, tuple)) else res
            if stage != minervini.STAGE_2_ADVANCING:
                return None
            # fetch price and rs and market cap
            price = None
            rate = None
            market_cap = None
            rs_val = None
            try:
                presp = await asyncio.wait_for(sqs.get_current_price(code, caller="ranking:minervini"), timeout=3.0)
                if presp and getattr(presp, 'rt_cd', None) == '0' and getattr(presp, 'data', None):
                    out = presp.data if isinstance(presp.data, dict) else getattr(presp, 'data', presp.data)
                    # try dict-like first
                    if isinstance(out, dict):
                        price = out.get('stck_prpr') or out.get('stck_prpr')
                        rate = out.get('prdy_ctrt') or out.get('prdy_ctrt')
                        market_cap = out.get('stck_avls') or out.get('stck_avls')
                    else:
                        price = getattr(out, 'stck_prpr', None)
                        rate = getattr(out, 'prdy_ctrt', None)
                        market_cap = getattr(out, 'stck_avls', None)
            except Exception:
                pass
            try:
                if rs_svc:
                    rresp = await asyncio.wait_for(rs_svc.get_rating(code), timeout=2.5)
                    if rresp and getattr(rresp, 'rt_cd', None) == '0' and getattr(rresp, 'data', None):
                        rs_val = int(rresp.data.rs_rating)
            except Exception:
                rs_val = None

            name = ctx.stock_code_repository.get_name_by_code(code) or ''
            return {
                'code': code,
                'name': name,
                'stck_prpr': price or 0,
                'prdy_ctrt': rate or 0,
                'stage': int(stage),
                'rs_rating': rs_val or 0,
                'market_cap': market_cap or 0,
            }

    tasks = [check_code(c) for c in codes]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    items = []
    for r in results:
        if isinstance(r, Exception):
            # ignore individual task exceptions (timeout/cancel/etc.)
            continue
        if r:
            items.append(r)
    # sort by RS desc
    items.sort(key=lambda x: (x.get('rs_rating') or 0), reverse=True)

    return {"rt_cd": "0", "msg1": "성공", "data": items}


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
