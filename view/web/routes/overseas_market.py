"""
미국장 시가총액/랭킹 API 엔드포인트 (overseas_marketcap.html).
"""
import asyncio
from fastapi import APIRouter, HTTPException, Query
from services.overseas_market_stats_service import RANKING_CATEGORIES
from view.web.api_common import _get_ctx
from view.web.market_mode_utils import is_market_enabled

router = APIRouter(tags=["overseas-market"])


def _require_overseas(ctx, what: str):
    if not is_market_enabled(ctx, "overseas_us"):
        raise HTTPException(
            status_code=400,
            detail=f"{what}은 overseas_us가 enabled된 run에서만 사용할 수 있습니다.",
        )
    service = getattr(ctx, "overseas_market_stats_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail=f"{what} 서비스가 준비되지 않았습니다.")
    return service


@router.get("/overseas/top-market-cap")
async def get_overseas_top_market_cap(limit: int = Query(30, ge=1, le=500)):
    """S&P 500 유니버스 기준 미국 시가총액 상위 종목."""
    ctx = _get_ctx()
    service = _require_overseas(ctx, "미국주식 시가총액")
    try:
        data = await asyncio.wait_for(service.get_top_market_cap(limit=limit), timeout=30.0)
    except asyncio.TimeoutError:
        ctx.logger.warning("[overseas_market] 미국 시가총액 조회 타임아웃 (30s 초과)")
        return {"rt_cd": "1", "msg1": "미국 시가총액 조회 시간이 초과되었습니다. 잠시 후 다시 시도해주세요.", "data": None}
    except Exception as exc:
        ctx.logger.warning(f"[overseas_market] 미국 시가총액 조회 실패: {exc}")
        return {"rt_cd": "1", "msg1": "미국 시가총액을 조회하지 못했습니다. 잠시 후 다시 시도해주세요.", "data": None}

    return {"rt_cd": "0", "msg1": "미국 시가총액 조회 성공", "data": data}


@router.get("/overseas/trades")
async def get_overseas_trades():
    """미국장 USD 원장의 성과 요약 + 거래 목록.

    국내 `/api/virtual/*` 와 원장이 분리돼 있다 — 통화가 달라 같은 표에 섞을 수 없다.
    """
    ctx = _get_ctx()
    if not is_market_enabled(ctx, "overseas_us"):
        raise HTTPException(
            status_code=400,
            detail="미국주식 거래 기록은 overseas_us가 enabled된 run에서만 사용할 수 있습니다.",
        )
    ledger = getattr(ctx, "overseas_trade_repository", None)
    if ledger is None:
        raise HTTPException(status_code=503, detail="미국주식 거래 원장이 준비되지 않았습니다.")

    return {
        "rt_cd": "0",
        "msg1": "미국주식 거래 기록 조회 성공",
        "data": {"summary": ledger.get_summary(), "trades": ledger.get_all_trades()},
    }


@router.get("/overseas/ranking/{category}")
async def get_overseas_ranking(category: str, limit: int = Query(30, ge=1, le=500)):
    """S&P 500 유니버스 기준 미국 랭킹 (rise/fall/volume/trading_value)."""
    ctx = _get_ctx()
    service = _require_overseas(ctx, "미국주식 랭킹")
    if category not in RANKING_CATEGORIES:
        raise HTTPException(
            status_code=400,
            detail=f"category는 {', '.join(RANKING_CATEGORIES)} 중 하나여야 합니다.",
        )
    try:
        data = await asyncio.wait_for(
            service.get_ranking(category, limit=limit), timeout=30.0
        )
    except asyncio.TimeoutError:
        ctx.logger.warning(f"[overseas_market] 미국 랭킹 조회 타임아웃 ({category}, 30s 초과)")
        return {"rt_cd": "1", "msg1": "미국 랭킹 조회 시간이 초과되었습니다. 잠시 후 다시 시도해주세요.", "data": None}
    except Exception as exc:
        ctx.logger.warning(f"[overseas_market] 미국 랭킹 조회 실패 ({category}): {exc}")
        return {"rt_cd": "1", "msg1": "미국 랭킹을 조회하지 못했습니다. 잠시 후 다시 시도해주세요.", "data": None}

    return {"rt_cd": "0", "msg1": "미국 랭킹 조회 성공", "data": data}
