"""
가상 매매 관련 API 엔드포인트 (virtual.html).
"""
import asyncio
import time
from fastapi import APIRouter
from view.web.api_common import _get_ctx, _PRICE_CACHE

router = APIRouter()


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


async def _calculate_benchmark(ctx, code: str, ref_history: list, start_date: str, end_date: str) -> list:
    """Helper to calculate benchmark history for a given ETF code."""
    try:
        resp = await ctx.stock_query_service.trading_service.get_ohlcv_range(
            code, period="D", start_date=start_date, end_date=end_date
        )

        # API 실패 또는 데이터 없는 경우 0으로 채운 리스트 반환
        if not resp or resp.rt_cd != "0" or not resp.data:
            return [{"date": h['date'], "return_rate": 0} for h in ref_history]

        ohlcv = resp.data

        # 첫 거래일의 종가를 기준가로 설정
        base_price = ohlcv[0]['close']
        if not isinstance(base_price, (int, float)) or base_price <= 0:
             return [{"date": h['date'], "return_rate": 0} for h in ref_history]

        ohlcv_map = {item['date']: item['close'] for item in ohlcv}

        benchmark_history = []
        last_price = base_price
        for h in ref_history:
            date_key = h['date'].replace('-', '')
            price = ohlcv_map.get(date_key, last_price)

            # 가격 데이터가 없는 경우(get 실패), 마지막 가격 유지
            if not isinstance(price, (int, float)):
                price = last_price
            else:
                last_price = price

            bench_return = round(((price - base_price) / base_price) * 100, 2)
            benchmark_history.append({"date": h['date'], "return_rate": bench_return})

        return benchmark_history

    except Exception:
        # 예외 발생 시 로깅하고 0으로 채운 리스트 반환
        return [{"date": h['date'], "return_rate": 0} for h in ref_history]


@router.get("/virtual/chart/{strategy_name}")
async def get_strategy_chart(strategy_name: str):
    """특정 전략의 수익률 히스토리(차트용) 반환 + 벤치마크(KOSPI200, KOSDAQ150) 포함"""
    ctx = _get_ctx()
    vm = ctx.virtual_manager

    # 1. 히스토리 데이터 수집
    if strategy_name == "ALL":
        strategies = vm.get_all_strategies()
        histories = {s: vm.get_strategy_return_history(s) for s in strategies}
        # ALL 합산 히스토리 생성: 전 전략의 날짜별 평균 수익률
        all_dates_map: dict[str, list[float]] = {}
        for hist in histories.values():
            for entry in hist:
                all_dates_map.setdefault(entry['date'], []).append(entry['return_rate'])
        if all_dates_map:
            histories["ALL"] = [
                {"date": d, "return_rate": sum(vals) / len(vals)}
                for d, vals in sorted(all_dates_map.items())
            ]
    else:
        histories = {strategy_name: vm.get_strategy_return_history(strategy_name)}

    # 벤치마크 계산을 위한 기준 히스토리 (날짜 범위 추출용)
    ref_history = histories.get("ALL") or histories.get(strategy_name) or (next(iter(histories.values())) if histories else [])

    if not ref_history:
        return {"histories": {}, "benchmarks": {}}

    start_date = ref_history[0]['date'].replace('-', '')
    end_date = ref_history[-1]['date'].replace('-', '')

    # 벤치마크 데이터 (KOSPI 200, KOSDAQ 150)
    kospi_benchmark = await _calculate_benchmark(ctx, "069500", ref_history, start_date, end_date)
    kosdaq_benchmark = await _calculate_benchmark(ctx, "229200", ref_history, start_date, end_date)

    benchmarks = {
        "KOSPI200": kospi_benchmark,
        "KOSDAQ150": kosdaq_benchmark,
    }

    return {"histories": histories, "benchmarks": benchmarks}


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
