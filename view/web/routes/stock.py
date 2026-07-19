"""
종목 조회 관련 API 엔드포인트 (index.html).
현재가, 차트(OHLCV), 기술 지표, 시장 상태, 환경 전환.
"""
import asyncio
import time
from datetime import datetime
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from common.overseas_types import OverseasExchange
from common.types import Exchange
from services.ai_usage_limiter import AiUsageLimitExceeded
from repositories.streaming_stock_repo import StreamingType
from services.market_cap_gap_service import MarketCapGapService
from services.price_subscription_service import SubscriptionPriority
from view.web.api_common import _get_ctx, _serialize_response, EnvironmentRequest
import view.web.api_common as api_common
from view.web.market_mode_utils import enabled_market_modes_of, is_market_enabled, market_mode_of

router = APIRouter()

# /api/status 결과를 5초간 캐시하여 페이지 전환 시 broker API 반복 호출 방지
_status_cache = None
_status_cache_ts = 0.0
_STATUS_CACHE_TTL = 5.0


class MarketModeRequest(BaseModel):
    market_mode: str


@router.get("/status")
async def get_status():
    """시장 상태 및 환경 정보."""
    global _status_cache, _status_cache_ts

    ctx = _get_ctx()
    if ctx is None:
        return {
            "market_open": False,
            "env_type": "미설정",
            "is_paper_trading": True,
            "market_mode": "domestic",
            "enabled_market_modes": ["domestic"],
            "current_time": "",
            "initialized": False,
        }

    now = time.monotonic()
    if _status_cache is not None and (now - _status_cache_ts) < _STATUS_CACHE_TTL:
        # 캐시된 결과 반환 (현재 시각만 갱신)
        _status_cache["current_time"] = ctx.get_current_time_str()
        _status_cache["market_mode"] = market_mode_of(ctx)
        _status_cache["enabled_market_modes"] = enabled_market_modes_of(ctx)
        return _status_cache

    result = {
        "market_open": await ctx.is_market_open_now(),
        "env_type": ctx.get_env_type(),
        "is_paper_trading": bool(getattr(getattr(ctx, "env", None), "is_paper_trading", True)),
        "market_mode": market_mode_of(ctx),
        "enabled_market_modes": enabled_market_modes_of(ctx),
        "current_time": ctx.get_current_time_str(),
        "initialized": ctx.initialized
    }
    _status_cache = result
    _status_cache_ts = now
    return result


@router.get("/market-mode")
def get_market_mode():
    ctx = _get_ctx()
    mode = market_mode_of(ctx)
    return {
        "success": True,
        "market_mode": mode,
        "enabled_market_modes": enabled_market_modes_of(ctx),
        "requires_reinitialize": False,
    }


@router.post("/market-mode")
def change_market_mode(req: MarketModeRequest):
    global _status_cache, _status_cache_ts
    ctx = _get_ctx()
    mode = str(req.market_mode or "").strip().lower()
    if mode not in ("domestic", "overseas_us"):
        raise HTTPException(status_code=400, detail="market_mode는 domestic 또는 overseas_us만 지원합니다.")

    current = market_mode_of(ctx)
    ctx.market_mode = mode
    enabled_modes = enabled_market_modes_of(ctx)
    if mode not in enabled_modes:
        enabled_modes.append(mode)
    ctx.enabled_market_modes = enabled_modes
    full_config = getattr(ctx, "full_config", None)
    if full_config is not None and hasattr(full_config, "market_mode"):
        full_config.market_mode = mode
    if full_config is not None and hasattr(full_config, "enabled_market_modes"):
        full_config.enabled_market_modes = enabled_modes

    _status_cache = None
    _status_cache_ts = 0.0
    return {
        "success": True,
        "market_mode": mode,
        "enabled_market_modes": enabled_modes,
        "previous_market_mode": current,
        "requires_reinitialize": current != mode,
    }


@router.get("/stocks/list")
async def get_stocks_list():
    """전 종목 리스트 반환 (클라이언트 자동완성용, localStorage 캐싱 대상)."""
    ctx = _get_ctx()
    stock_list = [
        {"c": code, "n": name}
        for name, code in ctx.stock_code_repository.name_to_code.items()
    ]
    return {"stocks": stock_list, "count": len(stock_list)}


@router.get("/overseas/stocks/list")
async def get_overseas_stocks_list():
    """해외(미국) 전 심볼 리스트 반환 (클라이언트 자동완성용, localStorage 캐싱 대상)."""
    ctx = _get_ctx()
    repo = getattr(ctx, "overseas_stock_code_repository", None)
    if repo is None:
        return {"stocks": [], "count": 0}
    stock_list = repo.all_symbols()
    return {"stocks": stock_list, "count": len(stock_list)}


@router.get("/stock/search")
async def search_stock_by_name(q: str = ""):
    """종목명 부분 일치 검색 (자동완성용)."""
    ctx = _get_ctx()
    if not q or len(q.strip()) == 0:
        return {"results": []}
    results = ctx.stock_code_repository.search_by_name(q.strip())
    return {"results": results}


@router.get("/stock/{code}/rs_rating")
async def get_stock_rs_rating(code: str):
    """종목의 RS Rating 조회"""
    ctx = _get_ctx()
    if not getattr(ctx, "rs_rating_service", None):
        return {"rt_cd": "1", "msg1": "RS Rating 서비스가 비활성화되어 있습니다.", "data": None}

    resp = await ctx.rs_rating_service.get_rating(code)
    return _serialize_response(resp)


@router.get("/stock/{code}/stage")
async def get_stock_stage(code: str):
    """종목의 Minervini Stage 조회 (1~4단계, 0=미계산)"""
    ctx = _get_ctx()
    if not getattr(ctx, "minervini_stage_service", None):
        return {"rt_cd": "1", "msg1": "MinerviniStageService가 비활성화되어 있습니다.", "data": None}
    stage, reason = await ctx.minervini_stage_service.get_stage_for_code(code)
    return {"rt_cd": "0", "msg1": "성공", "data": {"code": code, "stage": stage, "reason": reason}}


@router.get("/stock/{code}/detail")
async def get_stock_detail(code: str, exchange: str = Query("KRX")):
    """증권사 API 직접 호출로 PER/PBR/EPS/BPS/업종/52주 등 상세 정보 반환 (캐시·DB 우회)."""
    ctx = _get_ctx()
    try:
        exchange_enum = Exchange(exchange.upper())
    except ValueError:
        exchange_enum = Exchange.KRX
    try:
        resp = await asyncio.wait_for(
            ctx.stock_query_service.handle_get_current_stock_price(
                code, caller="stock.py - get_stock_detail", exchange=exchange_enum,
                force_fresh=True,
            ),
            timeout=12.0,
        )
    except asyncio.TimeoutError:
        ctx.logger.warning(f"[stock] 상세 정보 조회 타임아웃 ({code}, 12s 초과)")
        return {"rt_cd": "1", "msg1": "API 응답 시간이 초과되었습니다.", "data": None}
    return _serialize_response(resp)


@router.post("/stock/{code}/ai-analysis")
async def get_ai_stock_analysis(code: str):
    """현재가·재무·추세·수급·공시를 모아 요청 시점에 종목 AI 분석을 생성한다."""
    ctx = _get_ctx()
    if not code.isdigit() or len(code) != 6:
        raise HTTPException(status_code=400, detail="국내 종목코드 6자리를 입력하세요.")
    analyzer = getattr(ctx, "ai_stock_analyzer", None)
    if analyzer is None:
        raise HTTPException(
            status_code=503,
            detail="AI 분석이 비활성화되어 있습니다. ai_analysis 설정을 확인하세요.",
        )

    async def _load_stage():
        service = getattr(ctx, "minervini_stage_service", None)
        if service is None:
            return None
        stage, reason = await service.get_stage_for_code(code)
        return {"stage": stage, "reason": reason}

    async def _load_rs_rating():
        service = getattr(ctx, "rs_rating_service", None)
        if service is None:
            return None
        return await service.get_rating(code)

    async def _load_disclosures():
        repository = getattr(ctx, "dart_disclosure_repository", None)
        if repository is None:
            return []
        rows = await repository.get_recent_by_stock_code(code, limit=5)
        return [
            {
                "report_name": row.disclosure.report_name,
                "receipt_date": row.disclosure.receipt_date,
                "importance_level": row.importance.level,
                "importance_score": row.importance.score,
                "reasons": row.importance.reasons,
                "viewer_url": row.disclosure.viewer_url,
            }
            for row in rows
        ]

    results = await asyncio.gather(
        ctx.stock_query_service.handle_get_current_stock_price(
            code,
            caller="stock.py - get_ai_stock_analysis",
            exchange=Exchange.KRX,
            force_fresh=True,
        ),
        ctx.stock_query_service.get_financial_ratio(code),
        _load_stage(),
        _load_rs_rating(),
        ctx.stock_query_service.get_investor_trade_daily_multi(code, days=5),
        _load_disclosures(),
        return_exceptions=True,
    )

    def _data_or_none(value):
        if isinstance(value, Exception):
            ctx.logger.warning(
                f"[stock-ai] {code} 컨텍스트 일부 조회 실패: "
                f"{type(value).__name__}: {value}"
            )
            return None
        if isinstance(value, (dict, list)) or value is None:
            return value
        serialized = _serialize_response(value)
        if serialized.get("rt_cd") != "0":
            return None
        return serialized.get("data")

    current, financial, stage, rs_rating, investor_flow, disclosures = (
        _data_or_none(value) for value in results
    )
    context = {
        "code": code,
        "name": ctx.stock_code_repository.get_name_by_code(code) or code,
        "current": current,
        "financial": financial,
        "stage": stage,
        "rs_rating": rs_rating,
        "investor_flow": investor_flow,
        "disclosures": disclosures,
    }
    try:
        analysis = await analyzer.analyze(context)
    except AiUsageLimitExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except Exception as exc:
        ctx.logger.warning(
            f"[stock-ai] {code} 분석 실패: {type(exc).__name__}: {exc}"
        )
        raise HTTPException(
            status_code=502, detail="AI 분석 요청에 실패했습니다. 잠시 후 다시 시도하세요."
        ) from exc
    if not analysis:
        raise HTTPException(status_code=502, detail="AI 분석 결과가 비어 있습니다.")

    sources = {
        key: bool(context[key])
        for key in (
            "current",
            "financial",
            "stage",
            "rs_rating",
            "investor_flow",
            "disclosures",
        )
    }
    return {
        "rt_cd": "0",
        "msg1": "성공",
        "data": {
            "code": code,
            "name": context["name"],
            "analysis": analysis,
            "sources": sources,
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        },
    }


_NEWS_LIMIT = 15


@router.post("/stock/{code}/ai-news")
async def get_ai_news_review(code: str):
    """종목 최신 뉴스를 수집해 요청 시점에 AI 검토 결과를 생성한다."""
    ctx = _get_ctx()
    if not code.isdigit() or len(code) != 6:
        raise HTTPException(status_code=400, detail="국내 종목코드 6자리를 입력하세요.")
    analyzer = getattr(ctx, "ai_news_analyzer", None)
    collector = getattr(ctx, "stock_news_collector", None)
    if analyzer is None or collector is None:
        raise HTTPException(
            status_code=503,
            detail="AI 분석이 비활성화되어 있습니다. ai_analysis 설정을 확인하세요.",
        )

    name = ctx.stock_code_repository.get_name_by_code(code) or code
    try:
        news = await collector.collect(code, limit=_NEWS_LIMIT)
    except Exception as exc:
        ctx.logger.warning(
            f"[stock-news] {code} 뉴스 수집 실패: {type(exc).__name__}: {exc}"
        )
        news = []

    # 수집 결과가 없으면 AI 를 호출하지 않는다(일일 사용량 낭비 방지).
    if not news:
        return {
            "rt_cd": "0",
            "msg1": "최근 뉴스를 찾지 못했습니다.",
            "data": {
                "code": code,
                "name": name,
                "analysis": None,
                "news": [],
                "news_count": 0,
                "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            },
        }

    try:
        analysis = await analyzer.analyze({"code": code, "name": name, "news": news})
    except AiUsageLimitExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except Exception as exc:
        ctx.logger.warning(
            f"[stock-news] {code} AI 검토 실패: {type(exc).__name__}: {exc}"
        )
        raise HTTPException(
            status_code=502, detail="AI 뉴스 검토 요청에 실패했습니다. 잠시 후 다시 시도하세요."
        ) from exc
    if not analysis:
        raise HTTPException(status_code=502, detail="AI 뉴스 검토 결과가 비어 있습니다.")

    return {
        "rt_cd": "0",
        "msg1": "성공",
        "data": {
            "code": code,
            "name": name,
            "analysis": analysis,
            "news": news,
            "news_count": len(news),
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        },
    }


@router.get("/stock/{code}")
async def get_stock_price(code: str, exchange: str = Query("KRX")):
    """현재가 조회. 종목명이 들어오면 종목코드로 변환 후 조회. exchange=KRX|NXT|UN 선택 가능."""
    ctx = _get_ctx()
    # 숫자가 아닌 입력(종목명)이면 코드로 변환
    if not code.isdigit():
        resolved = ctx.stock_code_repository.get_code_by_name(code)
        if not resolved:
            return {"rt_cd": "1", "msg1": f"종목명 '{code}'에 해당하는 종목코드를 찾을 수 없습니다.", "data": None}
        code = resolved
    try:
        exchange_enum = Exchange(exchange.upper())
    except ValueError:
        exchange_enum = Exchange.KRX
    t_start = ctx.pm.start_timer()
    try:
        resp = await asyncio.wait_for(
            ctx.stock_query_service.handle_get_current_stock_price(code, caller="stock.py - get_stock_price", exchange=exchange_enum),
            timeout=12.0,
        )
    except asyncio.TimeoutError:
        ctx.logger.warning(f"[stock] 현재가 조회 타임아웃 ({code}, 12s 초과)")
        return {"rt_cd": "1", "msg1": "API 응답 시간이 초과되었습니다. 잠시 후 다시 시도해주세요.", "data": None}
    result = _serialize_response(resp)
    is_success = result.get("rt_cd") == "0"
    if is_success and isinstance(result.get("data"), dict):
        result["data"]["market"] = ctx.stock_code_repository.get_market_by_code(code)

    ctx.pm.log_timer(f"get_stock_price({code})", t_start)

    # 현재가 조회 시 실시간 구독 등록 (LOW 우선순위)
    if ctx.price_subscription_service:
        async def _add_subscription_background(c: str):
            try:
                await ctx.price_subscription_service.add_subscription(c, SubscriptionPriority.LOW, "web_price_query", StreamingType.UNIFIED_PRICE)
            except Exception as e:
                ctx.logger.error(f"[stock] 실시간 구독 등록 실패 ({c}): {e}")
        asyncio.create_task(_add_subscription_background(code))

    # 성공한 현재가 조회 후 OHLCV 2년치 백그라운드 프리로드
    if is_success:
        async def _preload_ohlcv():
            try:
                await ctx.stock_query_service.get_ohlcv(code, caller="preload_on_price_query")
            except Exception:
                pass

        preload_task = asyncio.create_task(_preload_ohlcv())
        pending_preloads = getattr(ctx, "_pending_ohlcv_preload_tasks", None)
        if pending_preloads is not None:
            pending_preloads.add(preload_task)
            preload_task.add_done_callback(pending_preloads.discard)

    return result


@router.get("/overseas/stock/{symbol}")
async def get_overseas_stock_price(symbol: str, exchange: str = Query("NASD")):
    ctx = _get_ctx()
    if not is_market_enabled(ctx, "overseas_us"):
        raise HTTPException(status_code=400, detail="해외주식 조회는 overseas_us가 enabled된 run에서만 사용할 수 있습니다.")
    try:
        exchange_enum = OverseasExchange(exchange.upper())
    except ValueError:
        raise HTTPException(status_code=400, detail="exchange는 NASD, NYSE, AMEX 중 하나여야 합니다.")
    try:
        resp = await asyncio.wait_for(
            ctx.stock_query_service.get_overseas_price(symbol, exchange=exchange_enum),
            timeout=10.0,
        )
    except asyncio.TimeoutError:
        ctx.logger.warning(f"[stock] 해외 현재가 조회 타임아웃 ({symbol}, 10s 초과)")
        return {"rt_cd": "1", "msg1": "해외주식 API 응답 시간이 초과되었습니다. 잠시 후 다시 시도해주세요.", "data": None}
    return _serialize_response(resp)


@router.get("/overseas/chart/{symbol}")
async def get_overseas_stock_chart(
    symbol: str,
    exchange: str = Query("NASD"),
    period: str = Query("D"),
    start_date: str = Query(""),
    end_date: str = Query(""),
):
    ctx = _get_ctx()
    if not is_market_enabled(ctx, "overseas_us"):
        raise HTTPException(status_code=400, detail="해외주식 차트 조회는 overseas_us가 enabled된 run에서만 사용할 수 있습니다.")
    try:
        exchange_enum = OverseasExchange(exchange.upper())
    except ValueError:
        raise HTTPException(status_code=400, detail="exchange는 NASD, NYSE, AMEX 중 하나여야 합니다.")
    try:
        resp = await asyncio.wait_for(
            ctx.stock_query_service.get_overseas_dailyprice(
                symbol,
                exchange=exchange_enum,
                start_date=start_date,
                end_date=end_date,
                period=period,
            ),
            timeout=12.0,
        )
    except asyncio.TimeoutError:
        ctx.logger.warning(f"[stock] 해외 차트 조회 타임아웃 ({symbol}, 12s 초과)")
        return {"rt_cd": "1", "msg1": "해외주식 차트 API 응답 시간이 초과되었습니다. 잠시 후 다시 시도해주세요.", "data": None}
    return _serialize_response(resp)


@router.get("/overseas/market-cap")
async def get_overseas_market_cap():
    """기존 대형주 유니버스의 미국 시가총액 요약을 반환한다."""
    ctx = _get_ctx()
    if not is_market_enabled(ctx, "overseas_us"):
        raise HTTPException(status_code=400, detail="미국주식 시가총액은 overseas_us가 enabled된 run에서만 사용할 수 있습니다.")

    service = getattr(ctx, "market_cap_gap_service", None)
    if service is None:
        service = MarketCapGapService.build_default(broker=ctx.broker, logger=ctx.logger)
        ctx.market_cap_gap_service = service
    try:
        data = await asyncio.wait_for(service.get_us_market_caps(), timeout=12.0)
    except asyncio.TimeoutError:
        ctx.logger.warning("[stock] 미국 시가총액 조회 타임아웃 (12s 초과)")
        return {"rt_cd": "1", "msg1": "미국 시가총액 API 응답 시간이 초과되었습니다. 잠시 후 다시 시도해주세요.", "data": None}
    except Exception as exc:
        ctx.logger.warning(f"[stock] 미국 시가총액 조회 실패: {exc}")
        return {"rt_cd": "1", "msg1": "미국 시가총액을 조회하지 못했습니다. 잠시 후 다시 시도해주세요.", "data": None}

    return {"rt_cd": "0", "msg1": "미국 주요 대형주 시가총액 조회 성공", "data": data}


@router.get("/chart/{code}")
async def get_stock_chart(code: str, period: str = "D", indicators: bool = False, exchange: str = Query("KRX")):
    """종목의 OHLCV 차트 데이터 조회 (기본 일봉). indicators=true 시 MA+BB 지표 포함. exchange=KRX|NXT|UN 선택 가능."""
    ctx = _get_ctx()
    try:
        exchange_enum = Exchange(exchange.upper())
    except ValueError:
        exchange_enum = Exchange.KRX
    t_start = ctx.pm.start_timer()
    if indicators:
        resp = await ctx.stock_query_service.get_ohlcv_with_indicators(code, period, caller="stock.py - get_stock_chart")
    else:
        resp = await ctx.stock_query_service.get_ohlcv(code, period, caller="stock.py - get_stock_chart", exchange=exchange_enum)
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
    if not req.is_paper and req.real_mode_confirmation != "REAL":
        raise HTTPException(status_code=400, detail="실전 모드 전환 확인 문자열이 필요합니다.")
    success = await ctx.initialize_services(is_paper_trading=req.is_paper)
    # 환경 전환 시 상태 캐시 무효화
    _status_cache = None
    _status_cache_ts = 0.0
    if not success:
        raise HTTPException(status_code=500, detail="환경 전환 실패 (토큰 발급 오류)")
    ctx.start_background_tasks()
    return {"success": True, "env_type": ctx.get_env_type()}
