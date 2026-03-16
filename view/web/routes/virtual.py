"""
가상 매매 관련 API 엔드포인트 (virtual.html).
"""
import time
from fastapi import APIRouter
from view.web.api_common import _get_ctx, _PRICE_CACHE
import time
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta

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
    async with ctx.pm.profile_async(f"get_strategy_chart({strategy_name})"):
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
    async with ctx.pm.profile_async("get_virtual_history"):
        return await _get_virtual_history_impl(ctx, force_code, apply_cost)


async def _get_virtual_history_impl(ctx, force_code, apply_cost):
    """get_virtual_history의 실제 구현 (Pandas 고속 집계 적용 버전)"""
    t_start = ctx.pm.start_timer()
    if not hasattr(ctx, 'virtual_manager'):
        return {"trades": [], "weekly_changes": {}}

    vm = ctx.virtual_manager
    trades = vm.get_all_trades(apply_cost=apply_cost)

    # ---------------------------------------------------------
    # 1~3단계: 종목명 및 현재가 Enrichment (기존 로직 유지 - API 통신)
    # ---------------------------------------------------------
    try:
        mapper = getattr(ctx, 'stock_code_mapper', None)
        hold_codes = set()
        
        for trade in trades:
            code = str(trade.get('code', ''))
            trade['stock_name'] = mapper.get_name_by_code(code) if mapper else ''
            if code.strip():
                hold_codes.add(code)

        hold_codes = list(hold_codes)
        price_map = {}
        
        if hold_codes and getattr(ctx, 'stock_query_service', None):
            now = time.time()
            cached_codes = {}
            fetch_codes = []
            
            # 캐시 확인
            for code in hold_codes:
                if code != force_code and code in _PRICE_CACHE:
                    c_price, c_rate, c_ts = _PRICE_CACHE[code]
                    if now - c_ts < 60:
                        cached_codes[code] = (c_price, c_rate, False, c_ts)
                        continue
                fetch_codes.append(code)

            price_map.update(cached_codes)

            # API 배치 조회
            if fetch_codes:
                for batch_start in range(0, len(fetch_codes), 30):
                    batch = fetch_codes[batch_start:batch_start + 30]
                    try:
                        resp = await ctx.stock_query_service.get_multi_price(batch)
                        if resp and resp.rt_cd == "0" and isinstance(resp.data, list):
                            for item in resp.data:
                                if not isinstance(item, dict): continue
                                code = item.get("stck_shrn_iscd", "")
                                if not code: continue
                                
                                price_val = int(float(item.get("stck_prpr", "0")))
                                rate_str = item.get("prdy_ctrt", "0")
                                rate_val = float(rate_str) if rate_str not in ('N/A', '', 'None') else 0.0
                                
                                if price_val > 0:
                                    _PRICE_CACHE[code] = (price_val, rate_val, time.time())
                                    price_map[code] = (price_val, rate_val, False, time.time())
                    except Exception as e:
                        print(f"[WebAPI] 복수종목 조회 예외: {e}")

                # API 실패 시 기존 캐시 폴백
                for code in fetch_codes:
                    if code not in price_map and code in _PRICE_CACHE:
                        cached_price, cached_rate, cached_time = _PRICE_CACHE[code]
                        price_map[code] = (cached_price, cached_rate, True, cached_time)

        # 현재가 반영 및 개별 수익률 재계산
        for trade in trades:
            if trade['code'] in price_map:
                cur, daily_rate, cached, ts = price_map[trade['code']]
                trade['current_price'] = cur
                trade['is_cached'] = cached
                trade['cache_ts'] = ts
                
                bp = float(trade.get('buy_price', 0) or 0)
                qty = float(trade.get('qty', 1) or 1)
                
                if trade['status'] == 'HOLD':
                    trade['daily_change_rate'] = daily_rate
                    trade['return_rate'] = vm.calculate_return(bp, cur, qty, apply_cost=apply_cost)
            
            if trade['status'] == 'SOLD':
                sp = float(trade.get('sell_price') or 0)
                bp = float(trade.get('buy_price', 0) or 0)
                qty = float(trade.get('qty', 1) or 1)
                
                if sp == 0.0 and trade.get('current_price'):
                    cur = trade['current_price']
                    trade['sell_price'] = cur
                    trade['return_rate'] = vm.calculate_return(bp, cur, qty, apply_cost=apply_cost)
                    try:
                        vm.fix_sell_price(trade['code'], trade.get('buy_date', ''), cur)
                    except Exception: pass
                else:
                    trade['return_rate'] = vm.calculate_return(bp, sp, qty, apply_cost=apply_cost)
                    trade['sell_price'] = sp

    except Exception as e:
        print(f"[WebAPI] virtual/history enrichment 오류: {e}")

    # ---------------------------------------------------------
    # 4~7단계: Pandas를 이용한 고속 데이터 집계 (기존 4개의 for문을 하나로 압축)
    # ---------------------------------------------------------
    if not trades:
        return {"trades": [], "summary_agg": {}, "cumulative_returns": {}, "daily_changes": {}, "weekly_changes": {}, "counts": {}}

    try:
        df = pd.DataFrame(trades)
        
        # 데이터 전처리
        df['qty'] = pd.to_numeric(df.get('qty', 1), errors='coerce').fillna(1)
        df['buy_price'] = pd.to_numeric(df.get('buy_price', 0), errors='coerce').fillna(0)
        df['current_price'] = pd.to_numeric(df.get('current_price', 0), errors='coerce').fillna(0)
        df['sell_price'] = pd.to_numeric(df.get('sell_price', 0), errors='coerce').fillna(0)

        # 평가가격(eval_price) 계산 벡터화
        df['eval_price'] = np.where(
            df['status'] == 'HOLD', df['current_price'],
            np.where(df['sell_price'] > 0, df['sell_price'], df['current_price'])
        )
        df['eval_price'] = np.where(df['eval_price'] <= 0, df['buy_price'], df['eval_price'])

        # 매수/평가 금액 계산 (수수료 로직 반영 위해 apply 사용)
        df['buy_amt'] = df.apply(lambda x: vm.get_trade_amount(x['buy_price'], x['qty'], False, apply_cost), axis=1)
        df['eval_amt'] = df.apply(lambda x: vm.get_trade_amount(x['eval_price'], x['qty'], True, apply_cost), axis=1)
        df['pnl'] = df['eval_amt'] - df['buy_amt']

        strategies = [s for s in df['strategy'].dropna().unique() if s]
        
        # 4. 전략별 누적수익률 집계
        summary_agg = {"ALL": {"buy_sum": float(df['buy_amt'].sum()), "eval_sum": float(df['eval_amt'].sum())}}
        strategy_returns = {}
        
        for strat in strategies:
            mask = df['strategy'] == strat
            summary_agg[strat] = {
                "buy_sum": float(df.loc[mask, 'buy_amt'].sum()),
                "eval_sum": float(df.loc[mask, 'eval_amt'].sum())
            }

        for key, val in summary_agg.items():
            if val["buy_sum"] > 0:
                strategy_returns[key] = round(((val["eval_sum"] - val["buy_sum"]) / val["buy_sum"]) * 100, 2)
            else:
                strategy_returns[key] = 0.0

        # 스냅샷 저장 및 변화율 계산 (I/O 최소화)
        vm.save_daily_snapshot(strategy_returns)
        snapshot_data = vm._load_data()  # 메모리 캐싱 적용 시 0초 소요
        
        daily_changes, weekly_changes, daily_ref_dates, weekly_ref_dates = {}, {}, {}, {}
        for key in ["ALL"] + strategies:
            d_val, d_date = vm.get_daily_change(key, strategy_returns.get(key, 0), _data=snapshot_data)
            w_val, w_date = vm.get_weekly_change(key, strategy_returns.get(key, 0), _data=snapshot_data)
            daily_changes[key], weekly_changes[key] = d_val, w_val
            if d_date: daily_ref_dates[key] = d_date
            if w_date: weekly_ref_dates[key] = w_date

        # 5. 최초매매일 계산
        df['buy_date_str'] = df['buy_date'].astype(str).str[:10]
        first_dates = {"ALL": str(df[df['buy_date_str'] != '']['buy_date_str'].min() or '')}
        for strat in strategies:
            min_date = df[(df['strategy'] == strat) & (df['buy_date_str'] != '')]['buy_date_str'].min()
            first_dates[strat] = str(min_date) if pd.notna(min_date) else ''

        # 6. 상태별 카운트 집계
        KST = timezone(timedelta(hours=9))
        today_str = datetime.now(KST).strftime("%Y-%m-%d")
        if snapshot_data and snapshot_data.get('daily'):
            last_date = sorted(snapshot_data['daily'].keys())[-1]
            if today_str > last_date: today_str = last_date

        df['is_hold'] = df['status'] == 'HOLD'
        df['is_today_buy'] = df['buy_date'].astype(str).str.startswith(today_str)
        df['is_today_sell'] = (df['status'] == 'SOLD') & df['sell_date'].astype(str).str.startswith(today_str)

        counts = {
            "ALL": {
                "hold": int(df['is_hold'].sum()),
                "today_buy": int(df['is_today_buy'].sum()),
                "today_sell": int(df['is_today_sell'].sum())
            }
        }
        for strat in strategies:
            mask = df['strategy'] == strat
            counts[strat] = {
                "hold": int(df.loc[mask, 'is_hold'].sum()),
                "today_buy": int(df.loc[mask, 'is_today_buy'].sum()),
                "today_sell": int(df.loc[mask, 'is_today_sell'].sum())
            }

        # 7. Profit Factor & Expectancy 계산
        def calc_metrics(sub_df):
            gains = sub_df[sub_df['pnl'] >= 0]['pnl']
            losses = sub_df[sub_df['pnl'] < 0]['pnl']
            tot_g, tot_l = float(gains.sum()), abs(float(losses.sum()))
            wins, loss_c = len(gains), len(losses)
            tot_c = wins + loss_c

            pf = {"value": round(tot_g/tot_l, 2) if tot_l > 0 else (None if tot_g > 0 else 0.0), 
                  "total_gain": round(tot_g), "total_loss": round(tot_l)}
            
            exp = {"value": 0.0, "win_rate": 0.0, "avg_gain": 0, "avg_loss": 0, "wins": 0, "losses": 0}
            if tot_c > 0:
                w_rate, l_rate = wins/tot_c, loss_c/tot_c
                avg_g = tot_g/wins if wins > 0 else 0
                avg_l = tot_l/loss_c if loss_c > 0 else 0
                exp.update({"value": round((w_rate*avg_g) - (l_rate*avg_l), 0), "win_rate": round(w_rate*100, 1),
                            "avg_gain": round(avg_g), "avg_loss": round(avg_l), "wins": wins, "losses": loss_c})
            return pf, exp

        valid_df = df[df['buy_price'] > 0]
        profit_factors, expectancies = {}, {}
        
        pf_all, exp_all = calc_metrics(valid_df)
        profit_factors["ALL"], expectancies["ALL"] = pf_all, exp_all
        
        for strat in strategies:
            pf_s, exp_s = calc_metrics(valid_df[valid_df['strategy'] == strat])
            profit_factors[strat], expectancies[strat] = pf_s, exp_s

    except Exception as e:
        print(f"[WebAPI] virtual/history Pandas 집계 오류: {e}")
        # 오류 발생 시 빈 값으로 안전하게 리턴
        summary_agg, strategy_returns, daily_changes, weekly_changes = {}, {}, {}, {}
        daily_ref_dates, weekly_ref_dates, first_dates, counts, profit_factors, expectancies = {}, {}, {}, {}, {}, {}

    ctx.pm.log_timer("get_virtual_history", t_start)
    raw_result = {
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

    # [추가된 안전 장치] 딕셔너리 내부를 순회하며 NaN이나 Infinity를 0.0으로 바꿉니다.
    import math
    def sanitize_for_json(obj):
        if isinstance(obj, float):
            if math.isnan(obj) or math.isinf(obj):
                return 0.0
            return obj
        elif isinstance(obj, dict):
            return {k: sanitize_for_json(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [sanitize_for_json(v) for v in obj]
        return obj

    # 정제된 데이터를 반환합니다.
    return sanitize_for_json(raw_result)
