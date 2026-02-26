"""
FastAPI 라우터: 웹 뷰용 API 엔드포인트.
서비스 레이어(StockQueryService, OrderExecutionService)를 직접 호출한다.
"""
import asyncio
import json
import time
import os
from fastapi import APIRouter, HTTPException, Request, Form, Response, Depends, WebSocket, WebSocketDisconnect
from fastapi.responses import RedirectResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel
from common.types import ErrorCode

router = APIRouter(prefix="/api")
_ctx = None  # 전역 변수로 선언

# API 호출 실패 시 대처를 위한 전역 가격 캐시 (메모리)
_PRICE_CACHE = {}  # {code: (price, rate, timestamp)}

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


class ProgramTradingRequest(BaseModel):
    code: str


class ProgramTradingUnsubscribeRequest(BaseModel):
    code: str | None = None

class ProgramTradingDataModel(BaseModel):
    chartData: dict
    subscribedCodes: list
    codeNameMap: dict
    savedAt: str | None = None


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

@router.get("/chart/{code}")
async def get_stock_chart(code: str, period: str = "D", indicators: bool = False):
    """종목의 OHLCV 차트 데이터 조회 (기본 일봉). indicators=true 시 MA+BB 지표 포함."""
    ctx = _get_ctx()
    if indicators:
        resp = await ctx.stock_query_service.get_ohlcv_with_indicators(code, period)
    else:
        resp = await ctx.stock_query_service.get_ohlcv(code, period)
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

                # 시장가 주문(price=0)인 경우 현재가를 조회하여 사용
                if price_val == 0 and getattr(ctx, 'stock_query_service', None):
                    try:
                        price_resp = await ctx.stock_query_service.handle_get_current_stock_price(req.code)
                        if price_resp and price_resp.rt_cd == "0" and isinstance(price_resp.data, dict):
                            price_str = str(price_resp.data.get('price', '0'))
                            price_val = int(price_str) if price_str.isdigit() else 0
                    except Exception:
                        pass

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


@router.get("/indicator/bollinger/{code}")
async def get_bollinger_bands(code: str, period: int = 20, std_dev: float = 2.0):
    """볼린저 밴드 조회 (기본: 20일, 2표준편차)"""
    ctx = _get_ctx()
    resp = await ctx.indicator_service.get_bollinger_bands(code, period, std_dev)
    return _serialize_response(resp)

@router.get("/indicator/rsi/{code}")
async def get_rsi(code: str, period: int = 14):
    """RSI 조회 (기본: 14일)"""
    ctx = _get_ctx()
    resp = await ctx.indicator_service.get_rsi(code, period)
    return _serialize_response(resp)

@router.get("/indicator/ma/{code}")
async def get_moving_average(code: str, period: int = 20, method: str = "sma"):
    """이동평균선 조회 (기본: 20일, sma)"""
    ctx = _get_ctx()
    resp = await ctx.indicator_service.get_moving_average(code, period, method)
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

@router.get("/virtual/strategies")
async def get_strategies():
    """등록된 모든 전략 목록 반환 (UI 탭 생성용)"""
    ctx = _get_ctx()
    return ctx.virtual_manager.get_all_strategies()

@router.get("/virtual/chart/{strategy_name}")
async def get_strategy_chart(strategy_name: str):
    """특정 전략의 수익률 히스토리(차트용) 반환 + 벤치마크(KOSPI200) 포함"""
    ctx = _get_ctx()
    vm = ctx.virtual_manager
    
    # 1. 히스토리 데이터 수집
    if strategy_name == "ALL":
        strategies = vm.get_all_strategies()
        histories = {s: vm.get_strategy_return_history(s) for s in strategies}
    else:
        histories = {strategy_name: vm.get_strategy_return_history(strategy_name)}
    
    # 벤치마크 계산을 위한 기준 히스토리 (날짜 범위 추출용)
    ref_history = histories.get(strategy_name) or histories.get("ALL") or (next(iter(histories.values())) if histories else [])
    
    if not ref_history:
        return {"histories": {}, "benchmark": []}
    
    # 벤치마크 데이터 (KODEX 200: 069500)를 사용하여 시장 흐름 표시
    benchmark_history = []
    try:
        start_date = ref_history[0]['date'].replace('-', '')
        end_date = ref_history[-1]['date'].replace('-', '')
        
        # KODEX 200 일봉 데이터 조회
        resp = await ctx.stock_query_service.trading_service.get_ohlcv_range(
            "069500", period="D", start_date=start_date, end_date=end_date
        )
        
        if resp and resp.rt_cd == "0" and resp.data:
            ohlcv = resp.data
            base_price = ohlcv[0]['close']
            ohlcv_map = {item['date']: item['close'] for item in ohlcv}
            
            last_price = base_price
            for h in ref_history:
                date_key = h['date'].replace('-', '')
                price = ohlcv_map.get(date_key, last_price)
                last_price = price
                
                bench_return = round(((price - base_price) / base_price) * 100, 2) if base_price else 0
                benchmark_history.append({"date": h['date'], "return_rate": bench_return})
        else:
            benchmark_history = [{"date": h['date'], "return_rate": 0} for h in ref_history]
    except Exception:
        benchmark_history = [{"date": h['date'], "return_rate": 0} for h in ref_history]

    return {"histories": histories, "benchmark": benchmark_history}

@router.get("/virtual/history")
async def get_virtual_history(force_code: str = None):
    """가상 매매 전체 기록 조회 (force_code 지정 시 해당 종목은 캐시 무시)"""
    ctx = _get_ctx()
    if not hasattr(ctx, 'virtual_manager'):
        return {"trades": [], "weekly_changes": {}}

    trades = ctx.virtual_manager.get_all_trades()

    # enrichment: 실패해도 기본 trades는 반환
    try:
        # 1. 종목명 enrichment
        mapper = getattr(ctx, 'stock_code_mapper', None)
        for trade in trades:
            code = str(trade.get('code', ''))
            trade['stock_name'] = mapper.get_name_by_code(code) if mapper else ''

        # 2. HOLD + SOLD 종목 현재가 조회 (숫자 코드만, 병렬)
        hold_codes = list(set(
            str(t['code']) for t in trades
            if str(t['code']).strip()
        ))
        price_map = {}
        if hold_codes and getattr(ctx, 'stock_query_service', None):
            sem = asyncio.Semaphore(5)  # 동시 요청 5개 (API 초당 20건 허용)

            async def _fetch(code):
                # 캐시가 존재하고 1분(60초) 이내라면 캐시 반환 (단, force_code인 경우 무시)
                now = time.time()
                if code != force_code and code in _PRICE_CACHE:
                    c_price, c_rate, c_ts = _PRICE_CACHE[code]
                    if now - c_ts < 60:  # 1분(60초)으로 단축
                        # 1분 이내의 신선한 데이터인 경우, API 호출을 건너뛰더라도 
                        # 사용자에게 '실패/캐시' 아이콘을 보여주지 않기 위해 False 반환
                        return code, c_price, c_rate, False, c_ts
                elif code == force_code:
                    print(f"[WebAPI] 종목 {code} 강제 업데이트: 캐시를 무시하고 API를 호출합니다.")

                async with sem:
                    await asyncio.sleep(0.05)  # API rate limit 보호
                    try:
                        resp = await ctx.stock_query_service.handle_get_current_stock_price(code)
                        if not resp:
                            print(f"[WebAPI] 현재가 조회 실패 ({code}): 응답 None")
                        elif resp.rt_cd != "0":
                            print(f"[WebAPI] 현재가 조회 실패 ({code}): rt_cd={resp.rt_cd}, msg={resp.msg1}")
                        elif not isinstance(resp.data, dict):
                            print(f"[WebAPI] 현재가 조회 실패 ({code}): data 타입={type(resp.data)}, data={resp.data}")
                        else:
                            price_str = str(resp.data.get('price', '0'))
                            try:
                                price_val = int(float(price_str))
                            except (ValueError, TypeError):
                                price_val = 0
                            # 전일대비 등락률 추출
                            rate_str = str(resp.data.get('rate', '0'))
                            try:
                                rate_val = float(rate_str) if rate_str not in ('N/A', '', 'None') else 0.0
                            except ValueError:
                                rate_val = 0.0
                            if price_val > 0:
                                # 성공 시 캐시 업데이트
                                _PRICE_CACHE[code] = (price_val, rate_val, time.time())
                                return code, price_val, rate_val, False, time.time()
                            else:
                                print(f"[WebAPI] 현재가 조회 실패 ({code}): price='{price_str}'")
                    except Exception as e:
                        print(f"[WebAPI] 현재가 조회 예외 ({code}): {e}")
                    
                    # 실패 시 캐시된 값이 있다면 반환
                    if code in _PRICE_CACHE:
                        cached_price, cached_rate, cached_time = _PRICE_CACHE[code]
                        return code, cached_price, cached_rate, True, cached_time
                    return code, None, 0.0, False, 0

            results = await asyncio.gather(*[_fetch(c) for c in hold_codes])
            price_map = {code: (price, rate, cached, ts) for code, price, rate, cached, ts in results if price is not None}

        # 3. 전체 종목에 현재가 반영 (HOLD는 수익률도 재계산)
        for trade in trades:
            if trade['code'] in price_map:
                cur, daily_rate, cached, ts = price_map[trade['code']]
                trade['current_price'] = cur
                trade['is_cached'] = cached
                trade['cache_ts'] = ts
                if trade['status'] == 'HOLD':
                    trade['daily_change_rate'] = daily_rate
                    bp = trade.get('buy_price', 0) or 0
                    trade['return_rate'] = round(((cur - bp) / bp) * 100, 2) if bp else 0
                elif trade['status'] == 'SOLD':
                    # sell_price가 0(시장가 매도)이면 CSV도 현재가로 보정
                    sp = trade.get('sell_price') or 0
                    if sp == 0 or (isinstance(sp, float) and sp == 0.0):
                        trade['sell_price'] = cur
                        bp = trade.get('buy_price', 0) or 0
                        trade['return_rate'] = round(((cur - bp) / bp) * 100, 2) if bp else 0
                        # CSV 원본도 수정
                        try:
                            ctx.virtual_manager.fix_sell_price(trade['code'], trade.get('buy_date', ''), cur)
                        except Exception:
                            pass
    except Exception as e:
        print(f"[WebAPI] virtual/history enrichment 오류: {e}")

    # 4. 전략별 누적수익률 계산 + 스냅샷 저장 + 전일/전주대비 조회
    daily_changes = {}
    weekly_changes = {}
    try:
        strategies = list(set(t['strategy'] for t in trades if t.get('strategy')))
        strategy_returns = {}

        # ALL 누적수익률
        all_rates = [t['return_rate'] for t in trades if t.get('return_rate') is not None]
        strategy_returns["ALL"] = round(sum(all_rates) / len(all_rates), 2) if all_rates else 0

        # 전략별 누적수익률
        for strat in strategies:
            rates = [t['return_rate'] for t in trades if t.get('strategy') == strat and t.get('return_rate') is not None]
            strategy_returns[strat] = round(sum(rates) / len(rates), 2) if rates else 0

        # 스냅샷 저장 + 전일/전주대비 조회 (JSON 1회만 로드)
        vm = ctx.virtual_manager
        vm.save_daily_snapshot(strategy_returns)
        snapshot_data = vm._load_data()
        for key in ["ALL"] + strategies:
            cur = strategy_returns.get(key, 0)
            daily_changes[key] = vm.get_daily_change(key, cur, _data=snapshot_data)
            weekly_changes[key] = vm.get_weekly_change(key, cur, _data=snapshot_data)
    except Exception as e:
        print(f"[WebAPI] virtual/history 스냅샷 처리 오류: {e}")

    return {"trades": trades, "daily_changes": daily_changes, "weekly_changes": weekly_changes}


# --- 프로그램매매 실시간 스트리밍 ---

@router.post("/program-trading/subscribe")
async def subscribe_program_trading(req: ProgramTradingRequest):
    """프로그램매매 실시간 구독 시작 (다중 종목 추가 구독)."""
    ctx = _get_ctx()
    success = await ctx.start_program_trading(req.code)
    if not success:
        raise HTTPException(status_code=500, detail="WebSocket 연결 실패")
    mapper = getattr(ctx, 'stock_code_mapper', None)
    stock_name = mapper.get_name_by_code(req.code) if mapper else ''
    return {"success": True, "code": req.code, "stock_name": stock_name, "codes": sorted(ctx._pt_codes)}


@router.get("/program-trading/history/{code}")
async def get_program_trading_history(code: str):
    """프로그램 매매 추이 히스토리 조회 (차트용)."""
    ctx = _get_ctx()
    resp = await ctx.stock_query_service.handle_get_program_trading_history(code)
    result = _serialize_response(resp)
    
    if result.get("rt_cd") == "0" and isinstance(result.get("data"), dict):
        mapper = getattr(ctx, 'stock_code_mapper', None)
        result["data"]["name"] = mapper.get_name_by_code(code) if mapper else ""
    return result


@router.post("/program-trading/unsubscribe")
async def unsubscribe_program_trading(req: ProgramTradingUnsubscribeRequest = None):
    """프로그램매매 구독 해지. code 지정 시 개별 해지, 미지정 시 전체 해지."""
    ctx = _get_ctx()
    if req and req.code:
        await ctx.stop_program_trading(req.code)
    else:
        await ctx.stop_all_program_trading()
    return {"success": True, "codes": sorted(ctx._pt_codes)}


@router.get("/program-trading/status")
async def get_program_trading_status():
    """프로그램매매 구독 상태 확인."""
    ctx = _get_ctx()
    return {
        "subscribed": len(ctx._pt_codes) > 0,
        "codes": sorted(ctx._pt_codes),
    }


@router.get("/program-trading/stream")
async def stream_program_trading(request: Request):
    """SSE 스트리밍: 프로그램매매 실시간 데이터를 브라우저에 전달."""
    ctx = _get_ctx()
    queue = asyncio.Queue(maxsize=200)
    ctx._pt_queues.append(queue)

    async def event_generator():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    data = await asyncio.wait_for(queue.get(), timeout=0.1)
                    if data is None:  # 테스트 종료 신호 (Poison Pill)
                        break
                    yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    if await request.is_disconnected():
                        break
                    yield ": keepalive\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            if queue in ctx._pt_queues:
                ctx._pt_queues.remove(queue)

    return StreamingResponse(event_generator(), media_type="text/event-stream")

@router.post("/program-trading/save-data")
async def save_pt_data(data: ProgramTradingDataModel):
    """프로그램 매매 데이터를 서버 파일(data/pt_data.json)에 저장"""
    try:
        file_path = "data/pt_data.json"
        os.makedirs("data", exist_ok=True)
        # Pydantic 모델을 dict로 변환하여 저장
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data.dict(), f, ensure_ascii=False, indent=2)
        return {"success": True}
    except Exception as e:
        print(f"[WebAPI] PT Data Save Error: {e}")
        return {"success": False, "msg": str(e)}

@router.get("/program-trading/load-data")
async def load_pt_data():
    """서버 파일에서 프로그램 매매 데이터 로드"""
    file_path = "data/pt_data.json"
    if not os.path.exists(file_path):
        return {"success": False, "msg": "File not found"}
    
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {"success": True, "data": data}
    except Exception as e:
        return {"success": False, "msg": str(e)}

@router.websocket("/ws/echo")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket 테스트용 에코 엔드포인트."""
    await websocket.accept()
    try:
        while True:
            data = await websocket.receive_text()
            await websocket.send_text(f"Message text was: {data}")
    except WebSocketDisconnect:
        pass

# ── 전략 스케줄러 제어 ──

@router.get("/scheduler/status")
async def get_scheduler_status():
    """스케줄러 상태 조회."""
    ctx = _get_ctx()
    if not ctx.scheduler:
        return {"running": False, "strategies": []}
    return ctx.scheduler.get_status()


@router.post("/scheduler/start")
async def start_scheduler():
    """스케줄러 시작."""
    ctx = _get_ctx()
    if not ctx.scheduler:
        raise HTTPException(status_code=503, detail="스케줄러가 초기화되지 않았습니다")
    await ctx.scheduler.start()
    return {"success": True, "status": ctx.scheduler.get_status()}


@router.post("/scheduler/stop")
async def stop_scheduler():
    """스케줄러 정지."""
    ctx = _get_ctx()
    if not ctx.scheduler:
        raise HTTPException(status_code=503, detail="스케줄러가 초기화되지 않았습니다")
    await ctx.scheduler.stop()
    return {"success": True, "status": ctx.scheduler.get_status()}


@router.post("/scheduler/strategy/{name}/start")
async def start_strategy(name: str):
    """개별 전략 활성화."""
    ctx = _get_ctx()
    if not ctx.scheduler:
        raise HTTPException(status_code=503, detail="스케줄러가 초기화되지 않았습니다")
    if not await ctx.scheduler.start_strategy(name):
        raise HTTPException(status_code=404, detail=f"전략 '{name}'을 찾을 수 없습니다")
    return {"success": True, "status": ctx.scheduler.get_status()}


@router.post("/scheduler/strategy/{name}/stop")
async def stop_strategy(name: str):
    """개별 전략 비활성화."""
    ctx = _get_ctx()
    if not ctx.scheduler:
        raise HTTPException(status_code=503, detail="스케줄러가 초기화되지 않았습니다")
    if not ctx.scheduler.stop_strategy(name):
        raise HTTPException(status_code=404, detail=f"전략 '{name}'을 찾을 수 없습니다")
    return {"success": True, "status": ctx.scheduler.get_status()}


@router.get("/scheduler/history")
async def get_scheduler_history(strategy: str = None):
    """스케줄러 시그널 실행 이력 조회. ?strategy=전략명 으로 필터 가능."""
    ctx = _get_ctx()
    if not ctx.scheduler:
        return {"history": []}
    return {"history": ctx.scheduler.get_signal_history(strategy)}
