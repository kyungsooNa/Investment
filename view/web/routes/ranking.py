"""
랭킹/시가총액 관련 API 엔드포인트 (ranking.html, marketcap.html).
"""
from fastapi import APIRouter, HTTPException
from common.types import ErrorCode
from view.web.api_common import _get_ctx, _serialize_response, _serialize_list_items

router = APIRouter()


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
async def get_top_market_cap(limit: int = 20, market: str = "0001"):
    """시가총액 상위 종목. market: 0001=거래소(코스피), 1001=코스닥"""
    ctx = _get_ctx()
    if market not in ("0000", "0001", "1001", "2001"):
        market = "0001"
    resp = await ctx.broker.get_top_market_cap_stocks_code(market, limit)
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
