"""
랭킹/시가총액 관련 API 엔드포인트 (ranking.html, marketcap.html).
"""
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from common.types import ErrorCode
from services.ai_usage_limiter import AiUsageLimitExceeded
from view.web.api_common import _get_ctx, _serialize_response, _serialize_list_items

router = APIRouter()


class RankingAIAnalysisRequest(BaseModel):
    candidates: list[dict[str, Any]] = Field(default_factory=list)
    market_context: dict[str, Any] | None = None
    max_candidates: int = Field(20, ge=1, le=20)

    model_config = {"extra": "forbid"}


@router.get("/ranking/progress")
async def get_ranking_progress():
    """투자자 랭킹 수집 진행률 조회."""
    ctx = _get_ctx()
    if ctx.ranking_task:
        return ctx.ranking_task.get_investor_ranking_progress()
    return {"running": False, "processed": 0, "total": 0, "collected": 0, "elapsed": 0.0}


@router.get("/ranking/period_progress")
async def get_period_ranking_progress():
    """기간수급 수집 진행률 조회."""
    ctx = _get_ctx()
    if ctx.ranking_task:
        return ctx.ranking_task.get_period_ranking_progress()
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


@router.get("/ranking/themes/intraday")
async def get_intraday_theme_leaders():
    """장중 태스크가 매분 계산한 최근 3분 주도 테마를 반환한다."""
    ctx = _get_ctx()
    task = getattr(ctx, "theme_intraday_leader_alert_task", None)
    if not task:
        return {
            "rt_cd": ErrorCode.API_ERROR.value,
            "msg1": "장중 주도 테마 태스크가 활성화되지 않았습니다.",
            "data": [],
        }
    latest = task.get_latest_report()
    data = latest.get("data") or []
    return {
        "rt_cd": ErrorCode.SUCCESS.value,
        "msg1": "장중 주도 테마 조회 성공" if data else "장중 테마 데이터를 수집 중입니다.",
        "captured_at": latest.get("captured_at"),
        "data": data,
    }


@router.post("/ranking/ai-analysis")
async def analyze_ranking_candidates(payload: RankingAIAnalysisRequest):
    """현재 랭킹 후보를 AI provider로 해설한다. 주문/전략 판단에는 사용하지 않는다."""
    ctx = _get_ctx()
    candidates = payload.candidates or []
    if not candidates:
        return {
            "rt_cd": ErrorCode.EMPTY_VALUES.value,
            "msg1": "AI 분석 대상 후보가 없습니다.",
            "data": None,
        }

    ai_service = getattr(ctx, "ai_analysis_service", None)
    if ai_service is None:
        raise HTTPException(
            status_code=503,
            detail="AI 분석이 비활성화되어 있습니다. ai_analysis 설정을 확인하세요.",
        )

    try:
        resp = await ai_service.analyze_leading_stocks(
            candidates,
            market_context=payload.market_context or {},
            max_candidates=payload.max_candidates,
        )
    except AiUsageLimitExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    return _serialize_response(resp)


@router.get("/ranking/investor-period")
async def get_period_investor_program_ranking(
    days: int = Query(5),
    metric: str = Query("amount"),
    limit: int = Query(30, ge=1, le=100),
):
    """최근 N거래일 외국인+기관+프로그램 기간 순매수 랭킹 조회."""
    valid_days = (1, 3, 5, 10, 20)
    valid_metrics = ("amount", "qty")
    if days not in valid_days:
        raise HTTPException(status_code=400, detail=f"days는 {', '.join(map(str, valid_days))} 중 하나여야 합니다.")
    if metric not in valid_metrics:
        raise HTTPException(status_code=400, detail="metric은 amount 또는 qty 여야 합니다.")

    ctx = _get_ctx()
    ranking_task = getattr(ctx, "ranking_task", None)
    if not ranking_task:
        return {"rt_cd": ErrorCode.API_ERROR.value, "msg1": "RankingTask 미설정", "data": []}

    resp = await ranking_task.get_period_investor_program_net_buy_ranking(
        days=days,
        metric=metric,
        limit=limit,
    )
    if resp and resp.rt_cd == ErrorCode.SUCCESS.value:
        return {
            "rt_cd": resp.rt_cd,
            "msg1": resp.msg1,
            "data": _serialize_list_items(resp.data),
        }
    return _serialize_response(resp)


@router.get("/ranking/ytd")
async def get_ytd_return_ranking(limit: int = Query(100, ge=1, le=500), market: Optional[str] = Query(None)):
    """저장된 일별 스냅샷으로 연초 대비 수익률 랭킹을 반환한다."""
    ctx = _get_ctx()
    repository = getattr(ctx, "stock_repository", None)
    if not repository:
        return {"rt_cd": ErrorCode.API_ERROR.value, "msg1": "StockRepository 미설정", "data": []}

    data = await repository.get_ytd_return_ranking(limit=limit, market=market)
    return {
        "rt_cd": ErrorCode.SUCCESS.value,
        "msg1": "YTD 상승률 랭킹 조회 성공" if data else "YTD 비교 데이터가 없습니다.",
        "data": _serialize_list_items(data),
    }


# 히트맵 기간 등락률: 달력일로 물러난 뒤 그 이하 가장 가까운 거래일을 기준일로 삼는다.
# "1d" 는 스냅샷에 이미 들어 있는 일간 등락률이라 추가 조회가 없다.
_HEATMAP_PERIOD_DAYS = {"1d": 0, "1w": 7, "1m": 30, "3m": 91, "6m": 182, "1y": 365}
_HEATMAP_PERIOD_LABELS = {"1d": "1일", "1w": "1주", "1m": "1개월", "3m": "3개월", "6m": "6개월", "1y": "1년"}


def _heatmap_period_change_rate(row: dict, closes: dict):
    """기간 등락률(%). 기준종가가 없는 종목은 None (화면에서 회색 타일)."""
    base_close = closes.get(row.get("code"))
    current_price = row.get("current_price")
    if not base_close or not current_price:
        return None
    try:
        return round((float(current_price) / float(base_close) - 1.0) * 100, 2)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


@router.get("/heatmap/domestic")
async def get_domestic_heatmap(
    limit: int = Query(300, ge=1, le=1000),
    market: Optional[str] = Query(None),
    period: str = Query("1d"),
):
    """국내 히트맵용 시가총액 스냅샷.

    실시간 전종목 시세 소스가 없어 장마감 후 수집된 daily_prices 스냅샷을 쓴다.
    기준일(trade_date)을 함께 반환해 화면이 장중에도 기준 시점을 표시할 수 있게 한다.
    period 를 주면 등락률을 그 기간 수익률로 바꿔 반환한다(면적=시가총액은 그대로).
    """
    if period not in _HEATMAP_PERIOD_DAYS:
        raise HTTPException(
            status_code=400,
            detail=f"period는 {', '.join(_HEATMAP_PERIOD_DAYS)} 중 하나여야 합니다.",
        )

    ctx = _get_ctx()
    repository = getattr(ctx, "stock_repository", None)
    if not repository:
        return {"rt_cd": ErrorCode.API_ERROR.value, "msg1": "StockRepository 미설정", "data": None}

    rows = await repository.get_market_cap_snapshot(limit=limit, market=market)

    base_date = None
    closes = {}
    if period != "1d" and rows:
        base = await repository.get_period_base_closes(period_days=_HEATMAP_PERIOD_DAYS[period])
        base_date = (base or {}).get("base_date")
        closes = (base or {}).get("closes") or {}

    items = [
        {
            "code": row.get("code") or "",
            "name": row.get("name") or "",
            "change_rate": (
                row.get("change_rate") if period == "1d"
                else _heatmap_period_change_rate(row, closes)
            ),
            "market_cap": row.get("market_cap"),
            "market": row.get("market") or "",
        }
        for row in rows
    ]

    if not items:
        msg1 = "저장된 시가총액 스냅샷이 없습니다."
    elif period != "1d" and not base_date:
        msg1 = f"{_HEATMAP_PERIOD_LABELS[period]} 비교 데이터가 없습니다."
    else:
        msg1 = "국내 히트맵 조회 성공"

    return {
        "rt_cd": ErrorCode.SUCCESS.value,
        "msg1": msg1,
        "data": {
            "trade_date": rows[0].get("trade_date") if rows else None,
            "items": items,
            "period": period,
            "base_date": base_date,
        },
    }


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
