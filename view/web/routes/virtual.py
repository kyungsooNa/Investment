"""
가상 매매 관련 API 엔드포인트 (virtual.html).
"""
import asyncio
import time
from fastapi import APIRouter
from view.web.api_common import _get_ctx, _PRICE_CACHE

router = APIRouter()


@router.get("/virtual/summary")
async def get_virtual_summary(apply_cost: bool = False):
    """가상 매매 요약 정보 조회"""
    ctx = _get_ctx()
    t_start = ctx.pm.start_timer()
    # ctx에 virtual_manager가 초기화되어 있어야 합니다.
    if not hasattr(ctx, 'virtual_manager'):
        return {"total_trades": 0, "win_rate": 0, "avg_return": 0}

    result = ctx.virtual_manager.get_summary(apply_cost=apply_cost)
    ctx.pm.log_timer("get_virtual_summary", t_start)
    return result


@router.get("/virtual/strategies")
async def get_strategies():
    """등록된 모든 전략 목록 반환 (UI 탭 생성용)"""
    ctx = _get_ctx()
    return ctx.virtual_manager.get_all_strategies()


async def _calculate_benchmark(ctx, code: str, ref_history: list, start_date: str, end_date: str) -> list:
    """Helper to calculate benchmark history for a given ETF code."""
    try:
        resp = await ctx.stock_query_service.get_ohlcv_range(
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
    t_start = ctx.pm.start_timer()
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

    ctx.pm.log_timer(f"get_strategy_chart({strategy_name})", t_start)
    return {"histories": histories, "benchmarks": benchmarks}


@router.get("/virtual/history")
async def get_virtual_history(force_code: str = None, apply_cost: bool = False):
    """가상 매매 전체 기록 조회 (force_code 지정 시 해당 종목은 캐시 무시)"""
    ctx = _get_ctx()
    t_start = ctx.pm.start_timer()
    if not hasattr(ctx, 'virtual_manager'):
        return {"trades": [], "weekly_changes": {}}

    trades = ctx.virtual_manager.get_all_trades(apply_cost=apply_cost)

    # enrichment: 실패해도 기본 trades는 반환
    try:
        # 1. 종목명 enrichment
        mapper = getattr(ctx, 'stock_code_mapper', None)
        for trade in trades:
            code = str(trade.get('code', ''))
            trade['stock_name'] = mapper.get_name_by_code(code) if mapper else ''

        # 2. HOLD + SOLD 종목 현재가 조회 (복수종목 API 활용)
        hold_codes = list(set(
            str(t['code']) for t in trades
            if str(t['code']).strip()
        ))
        price_map = {}
        if hold_codes and getattr(ctx, 'stock_query_service', None):
            now = time.time()

            # 2-1. 캐시 유효한 종목 분리 (force_code는 캐시 무시)
            cached_codes = {}
            fetch_codes = []
            for code in hold_codes:
                if code != force_code and code in _PRICE_CACHE:
                    c_price, c_rate, c_ts = _PRICE_CACHE[code]
                    if now - c_ts < 60:
                        cached_codes[code] = (c_price, c_rate, False, c_ts)
                        continue
                if code == force_code:
                    print(f"[WebAPI] 종목 {code} 강제 업데이트: 캐시를 무시하고 API를 호출합니다.")
                fetch_codes.append(code)

            price_map.update(cached_codes)

            # 2-2. 캐시 미스 종목은 복수종목 API로 배치 조회 (30개씩)
            if fetch_codes:
                for batch_start in range(0, len(fetch_codes), 30):
                    batch = fetch_codes[batch_start:batch_start + 30]
                    try:
                        resp = await ctx.stock_query_service.get_multi_price(batch)
                        if resp and resp.rt_cd == "0" and isinstance(resp.data, list):
                            for item in resp.data:
                                if not isinstance(item, dict):
                                    continue
                                code = item.get("stck_shrn_iscd", "")
                                if not code:
                                    continue
                                price_str = item.get("stck_prpr", "0")
                                rate_str = item.get("prdy_ctrt", "0")
                                try:
                                    price_val = int(float(price_str))
                                except (ValueError, TypeError):
                                    price_val = 0
                                try:
                                    rate_val = float(rate_str) if rate_str not in ('N/A', '', 'None') else 0.0
                                except (ValueError, TypeError):
                                    rate_val = 0.0
                                if price_val > 0:
                                    _PRICE_CACHE[code] = (price_val, rate_val, time.time())
                                    price_map[code] = (price_val, rate_val, False, time.time())
                        else:
                            print(f"[WebAPI] 복수종목 현재가 조회 실패: rt_cd={resp.rt_cd if resp else 'None'}, msg={resp.msg1 if resp else ''}")
                    except Exception as e:
                        print(f"[WebAPI] 복수종목 현재가 조회 예외: {e}")

                # 2-3. API에서 못 가져온 종목은 기존 캐시로 폴백
                for code in fetch_codes:
                    if code not in price_map and code in _PRICE_CACHE:
                        cached_price, cached_rate, cached_time = _PRICE_CACHE[code]
                        price_map[code] = (cached_price, cached_rate, True, cached_time)

        # 3. 전체 종목에 현재가 반영 (HOLD는 수익률도 재계산)
        vm = ctx.virtual_manager
        for trade in trades:
            # 현재가 정보가 있으면 업데이트
            if trade['code'] in price_map:
                cur, daily_rate, cached, ts = price_map[trade['code']]
                trade['current_price'] = cur
                trade['is_cached'] = cached
                trade['cache_ts'] = ts
                
                if trade['status'] == 'HOLD':
                    trade['daily_change_rate'] = daily_rate
                    bp = trade.get('buy_price', 0) or 0
                    qty = float(trade.get('qty', 1) or 1)
                    trade['return_rate'] = vm.calculate_return(bp, cur, qty, apply_cost=apply_cost)

            # SOLD 상태 처리 (현재가 조회 여부와 무관하게 매도가 기준 데이터 정제)
            if trade['status'] == 'SOLD':
                try:
                    sp = float(trade.get('sell_price') or 0)
                    bp = float(trade.get('buy_price', 0) or 0)
                except (ValueError, TypeError):
                    sp = 0.0
                    bp = 0.0

                if sp == 0.0:
                    # 매도가가 0(미확정)인 경우, 현재가가 조회되었을 때만 보정 가능
                    if 'current_price' in trade and trade['current_price']:
                        cur = trade['current_price']
                        trade['sell_price'] = cur
                        qty = float(trade.get('qty', 1) or 1)
                        trade['return_rate'] = vm.calculate_return(bp, cur, qty, apply_cost=apply_cost)
                        # CSV 원본도 수정
                        try:
                            ctx.virtual_manager.fix_sell_price(trade['code'], trade.get('buy_date', ''), cur)
                        except Exception:
                            pass
                else:
                    # 매도 완료된 종목은 매도가 기준으로 수익률 고정 (get_all_trades에서 이미 계산되었으나 안전장치)
                    qty = float(trade.get('qty', 1) or 1)
                    trade['return_rate'] = vm.calculate_return(bp, sp, qty, apply_cost=apply_cost)
                    trade['sell_price'] = sp
    except Exception as e:
        print(f"[WebAPI] virtual/history enrichment 오류: {e}")

    # 4. 전략별 누적수익률 계산 + 스냅샷 저장 + 전일/전주대비 조회
    daily_changes = {}
    weekly_changes = {}
    daily_ref_dates = {}
    weekly_ref_dates = {}
    try:
        strategies = list(set(t['strategy'] for t in trades if t.get('strategy')))
        strategy_returns = {}

        # 집계용 딕셔너리 초기화
        summary_agg = {"ALL": {"buy_sum": 0.0, "eval_sum": 0.0}}
        for strat in strategies:
            summary_agg[strat] = {"buy_sum": 0.0, "eval_sum": 0.0}

        for t in trades:
            strat = t.get('strategy')
            if not strat:
                continue
            
            try:
                qty = float(t.get('qty', 1) or 1)
                bp = float(t.get('buy_price', 0) or 0)
                
                # 평가금액 결정
                # HOLD: 현재가(current_price)
                # SOLD: 매도가(sell_price). 단, 0이면 현재가 사용.
                ep = 0.0
                if t.get('status') == 'HOLD':
                    ep = float(t.get('current_price', 0) or 0)
                else:
                    ep = float(t.get('sell_price', 0) or 0)
                    if ep == 0:
                        ep = float(t.get('current_price', 0) or 0)
                
                # 가격 정보가 없으면 매수가를 사용하여 수익률 0%로 처리 (왜곡 방지)
                if ep == 0:
                    ep = bp

                buy_amt = vm.get_trade_amount(bp, qty, is_sell=False, apply_cost=apply_cost)
                eval_amt = vm.get_trade_amount(ep, qty, is_sell=True, apply_cost=apply_cost)

                # ALL 집계
                summary_agg["ALL"]["buy_sum"] += buy_amt
                summary_agg["ALL"]["eval_sum"] += eval_amt

                # 전략별 집계
                if strat in summary_agg:
                    summary_agg[strat]["buy_sum"] += buy_amt
                    summary_agg[strat]["eval_sum"] += eval_amt

            except (ValueError, TypeError):
                continue

        # 수익률 계산
        for key, val in summary_agg.items():
            buy_sum = val["buy_sum"]
            eval_sum = val["eval_sum"]
            if buy_sum > 0:
                ror = ((eval_sum - buy_sum) / buy_sum) * 100
                strategy_returns[key] = round(ror, 2)
            else:
                strategy_returns[key] = 0.0

        # 스냅샷 저장 + 전일/전주대비 조회 (JSON 1회만 로드)
        vm.save_daily_snapshot(strategy_returns)
        snapshot_data = vm._load_data()
        for key in ["ALL"] + strategies:
            cur = strategy_returns.get(key, 0)
            d_val, d_date = vm.get_daily_change(key, cur, _data=snapshot_data)
            w_val, w_date = vm.get_weekly_change(key, cur, _data=snapshot_data)
            daily_changes[key] = d_val
            weekly_changes[key] = w_val
            if d_date:
                daily_ref_dates[key] = d_date
            if w_date:
                weekly_ref_dates[key] = w_date
    except Exception as e:
        print(f"[WebAPI] virtual/history 스냅샷 처리 오류: {e}")

    # 5. 최초매매일 계산 (전략별 + ALL)
    first_dates = {}
    try:
        for t in trades:
            buy_date = t.get('buy_date', '')
            strat = t.get('strategy', '')
            if buy_date and strat:
                date_only = buy_date[:10]  # "2025-02-13 ..." → "2025-02-13"
                if strat not in first_dates or date_only < first_dates[strat]:
                    first_dates[strat] = date_only
                if "ALL" not in first_dates or date_only < first_dates["ALL"]:
                    first_dates["ALL"] = date_only
    except Exception:
        pass

    # 6. 전략별 카운트 집계 (보유, 금일매수, 금일청산)
    counts = {"ALL": {"hold": 0, "today_buy": 0, "today_sell": 0}}
    for strat in strategies:
        counts[strat] = {"hold": 0, "today_buy": 0, "today_sell": 0}

    try:
        # 오늘 날짜 (KST 기준) - TimeManager 의존성 없이 안전하게 계산
        from datetime import datetime, timezone, timedelta
        KST = timezone(timedelta(hours=9))
        today_str = datetime.now(KST).strftime("%Y-%m-%d")

        # [수정] 장이 열리지 않는 날(주말/휴장)에는 마지막 개장일 기준
        local_snapshot = locals().get('snapshot_data')
        if not local_snapshot and hasattr(ctx, 'virtual_manager'):
            try:
                local_snapshot = ctx.virtual_manager._load_data()
            except Exception:
                pass
        
        if local_snapshot and local_snapshot.get('daily'):
            daily_keys = sorted(local_snapshot['daily'].keys())
            if daily_keys:
                last_date = daily_keys[-1]
                if today_str > last_date:
                    today_str = last_date

        for t in trades:
            strat = t.get('strategy')
            if not strat: continue

            # HOLD 카운트
            if t.get('status') == 'HOLD':
                counts["ALL"]["hold"] += 1
                if strat in counts:
                    counts[strat]["hold"] += 1

            # 금일 매수 (buy_date가 오늘 날짜로 시작)
            b_date = str(t.get('buy_date', ''))
            if b_date.startswith(today_str):
                counts["ALL"]["today_buy"] += 1
                if strat in counts:
                    counts[strat]["today_buy"] += 1

            # 금일 청산 (status=SOLD and sell_date가 오늘 날짜로 시작)
            if t.get('status') == 'SOLD':
                s_date = str(t.get('sell_date', ''))
                if s_date.startswith(today_str):
                    counts["ALL"]["today_sell"] += 1
                    if strat in counts:
                        counts[strat]["today_sell"] += 1
    except Exception as e:
        print(f"[WebAPI] virtual/history counts error: {e}")

    # 7. Profit Factor & Expectancy 계산 (전략별 + ALL)
    profit_factors = {}
    expectancies = {}
    try:
        # 전략별 수익/손실 집계용
        pf_agg = {"ALL": {"gains": [], "losses": []}}
        for strat in strategies:
            pf_agg[strat] = {"gains": [], "losses": []}

        for t in trades:
            strat = t.get('strategy')
            if not strat:
                continue
            try:
                qty = float(t.get('qty', 1) or 1)
                bp = float(t.get('buy_price', 0) or 0)
                if bp <= 0:
                    continue

                # 평가금액 결정
                if t.get('status') == 'HOLD':
                    ep = float(t.get('current_price', 0) or 0)
                else:
                    ep = float(t.get('sell_price', 0) or 0)
                    if ep == 0:
                        ep = float(t.get('current_price', 0) or 0)
                if ep <= 0:
                    continue

                buy_amt = vm.get_trade_amount(bp, qty, is_sell=False, apply_cost=apply_cost)
                eval_amt = vm.get_trade_amount(ep, qty, is_sell=True, apply_cost=apply_cost)
                pnl = eval_amt - buy_amt

                bucket = "gains" if pnl >= 0 else "losses"
                pf_agg["ALL"][bucket].append(pnl)
                if strat in pf_agg:
                    pf_agg[strat][bucket].append(pnl)

            except (ValueError, TypeError):
                continue

        for key, val in pf_agg.items():
            total_gain = sum(val["gains"])
            total_loss = abs(sum(val["losses"]))
            wins = len(val["gains"])
            losses_count = len(val["losses"])
            total_count = wins + losses_count

            # Profit Factor
            if total_loss > 0:
                profit_factors[key] = {
                    "value": round(total_gain / total_loss, 2),
                    "total_gain": round(total_gain),
                    "total_loss": round(total_loss),
                }
            elif total_gain > 0:
                profit_factors[key] = {
                    "value": None,  # 손실 없음 (무한대)
                    "total_gain": round(total_gain),
                    "total_loss": 0,
                }
            else:
                profit_factors[key] = {
                    "value": 0.0,
                    "total_gain": 0,
                    "total_loss": 0,
                }

            # Expectancy: (승률 × 평균수익금) - (패배율 × 평균손실금)
            if total_count > 0:
                win_rate = wins / total_count
                loss_rate = losses_count / total_count
                avg_gain = (total_gain / wins) if wins > 0 else 0
                avg_loss = (total_loss / losses_count) if losses_count > 0 else 0
                exp_value = (win_rate * avg_gain) - (loss_rate * avg_loss)
                expectancies[key] = {
                    "value": round(exp_value, 0),
                    "win_rate": round(win_rate * 100, 1),
                    "avg_gain": round(avg_gain),
                    "avg_loss": round(avg_loss),
                    "wins": wins,
                    "losses": losses_count,
                }
            else:
                expectancies[key] = {
                    "value": 0.0,
                    "win_rate": 0, "avg_gain": 0, "avg_loss": 0,
                    "wins": 0, "losses": 0,
                }

    except Exception as e:
        print(f"[WebAPI] virtual/history PF/Expectancy error: {e}")

    ctx.pm.log_timer("get_virtual_history", t_start)
    return {
        "trades": trades,
        "summary_agg": summary_agg,
        "cumulative_returns": strategy_returns,
        "daily_changes": daily_changes,
        "weekly_changes": weekly_changes,
        "daily_ref_dates": daily_ref_dates,
        "weekly_ref_dates": weekly_ref_dates,
        "first_dates": first_dates,
        "counts": counts,
        "profit_factors": profit_factors,
        "expectancies": expectancies,
    }
