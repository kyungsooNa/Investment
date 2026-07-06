"""
가상 매매 관련 API 엔드포인트 (virtual.html).
"""
import asyncio
import logging
import math
import time
from fastapi import APIRouter, Body
from common.trade_journal_comparison import compare_trade_journals
from repositories.backtest_journal_repository import BacktestJournalRepository
from view.web.api_common import _get_ctx, _PRICE_CACHE
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta

router = APIRouter()
logger = logging.getLogger(__name__)


def _sync_virtual_trade_state(ctx):
    vm = getattr(ctx, "virtual_trade_service", None)
    if not vm or not hasattr(vm, "sync_live_strategy_positions"):
        return vm

    try:
        vm.sync_live_strategy_positions()
    except Exception as e:
        logger.error(f"[WebAPI] virtual sync 오류: {e}")
    return vm


@router.get("/virtual/summary")
async def get_virtual_summary(apply_cost: bool = True):
    """가상 매매 요약 정보 조회"""
    ctx = _get_ctx()
    t_start = ctx.pm.start_timer()
    # ctx에 virtual_trade_service가 초기화되어 있어야 합니다.
    vm = _sync_virtual_trade_state(ctx)
    if vm is None:
        return {"total_trades": 0, "win_rate": 0, "avg_return": 0}

    result = vm.get_summary(apply_cost=apply_cost)
    ctx.pm.log_timer("get_virtual_summary", t_start)
    return result


@router.get("/virtual/strategies")
async def get_strategies():
    """등록된 모든 전략 목록 반환 (UI 탭 생성용)"""
    ctx = _get_ctx()
    vm = _sync_virtual_trade_state(ctx)
    return vm.get_all_strategies() if vm else []


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


def _build_reference_history(histories: dict[str, list], strategy_names: list[str]) -> list[dict[str, str]]:
    """선택 전략들의 실제 날짜 union을 벤치마크 기준축으로 사용한다."""
    date_set = {
        entry["date"]
        for name in strategy_names
        for entry in histories.get(name, [])
        if entry.get("date")
    }
    return [{"date": date} for date in sorted(date_set)]


def _date_prefix(value) -> str | None:
    text = str(value or "")[:10]
    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        return text
    return None


def _build_chart_trade_counts(vm, histories: dict[str, list], strategy_names: list[str]) -> dict[str, dict[str, int]]:
    """각 전략 차트 날짜 범위 안에 포함된 매수/매도 횟수를 계산한다."""
    try:
        trades = vm.get_all_trades(apply_cost=False)
    except TypeError:
        trades = vm.get_all_trades()
    except Exception as e:
        logger.error(f"[WebAPI] virtual/chart 거래 카운트 조회 오류: {e}")
        return {}

    if not isinstance(trades, list):
        return {}

    selected_names = set(strategy_names or [])
    counts = {}
    for name, history in histories.items():
        dates = sorted(entry.get("date") for entry in history if entry.get("date"))
        if not dates:
            continue

        start_date, end_date = dates[0], dates[-1]
        buy_count = 0
        sell_count = 0
        for trade in trades:
            if not isinstance(trade, dict) or trade.get("status") == "FAILED":
                continue

            trade_strategy = trade.get("strategy")
            if name == "ALL":
                if selected_names and trade_strategy not in selected_names:
                    continue
            elif trade_strategy != name:
                continue

            buy_date = _date_prefix(trade.get("buy_date"))
            if buy_date and start_date <= buy_date <= end_date:
                buy_count += 1

            sell_date = _date_prefix(trade.get("sell_date"))
            if sell_date and start_date <= sell_date <= end_date:
                sell_count += 1

        counts[name] = {"buy": buy_count, "sell": sell_count}

    return counts


@router.get("/virtual/chart/{strategy_name}")
async def get_strategy_chart(strategy_name: str, strategies: str | None = None):
    """특정 전략의 수익률 히스토리(차트용) 반환 + 벤치마크(KOSPI200, KOSDAQ150) 포함"""
    ctx = _get_ctx()
    async with ctx.pm.profile_async(f"get_strategy_chart({strategy_name})"):
        t_start = ctx.pm.start_timer()
        vm = _sync_virtual_trade_state(ctx)
        if vm is None:
            return {"histories": {}, "benchmarks": {}, "chart_counts": {}}

        # 1. 히스토리 데이터 수집
        if strategy_name == "ALL":
            selected_strategy_names = (
                [name.strip() for name in strategies.split(",") if name.strip()]
                if strategies
                else vm.get_all_strategies()
            )
            histories = {
                name: vm.get_strategy_return_history(name)
                for name in selected_strategy_names
            }
            # ALL 합산 히스토리 생성: 전 전략의 날짜별 평균 수익률
            all_dates_map: dict[str, list[float]] = {}
            for hist in histories.values():
                for entry in hist:
                    all_dates_map.setdefault(entry['date'], []).append(entry['return_rate'])
            if all_dates_map and not strategies:
                histories["ALL"] = [
                    {"date": d, "return_rate": sum(vals) / len(vals)}
                    for d, vals in sorted(all_dates_map.items())
                ]
        else:
            selected_strategy_names = [strategy_name]
            histories = {strategy_name: vm.get_strategy_return_history(strategy_name)}

        # 벤치마크 계산을 위한 기준 히스토리 (날짜 범위 추출용)
        ref_history = (
            histories.get("ALL")
            or _build_reference_history(histories, selected_strategy_names)
            or (next(iter(histories.values())) if histories else [])
        )

        if not ref_history:
            return {"histories": {}, "benchmarks": {}, "chart_counts": {}}

        start_date = ref_history[0]['date'].replace('-', '')
        end_date = ref_history[-1]['date'].replace('-', '')
        chart_counts = _build_chart_trade_counts(vm, histories, selected_strategy_names)

        # 벤치마크 데이터 (KOSPI 200, KOSDAQ 150)
        kospi_benchmark, kosdaq_benchmark = await asyncio.gather(
            _calculate_benchmark(ctx, "069500", ref_history, start_date, end_date),
            _calculate_benchmark(ctx, "229200", ref_history, start_date, end_date),
        )

        benchmarks = {
            "KOSPI200": kospi_benchmark,
            "KOSDAQ150": kosdaq_benchmark,
        }

        ctx.pm.log_timer(f"get_strategy_chart({strategy_name})", t_start)
        return {"histories": histories, "benchmarks": benchmarks, "chart_counts": chart_counts}


def _sanitize_for_json(obj):
    """NaN / Infinity → 0.0 으로 치환하여 JSON 직렬화 안전성 보장."""
    if isinstance(obj, float):
        return 0.0 if (math.isnan(obj) or math.isinf(obj)) else obj
    if isinstance(obj, dict):
        return {k: _sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_for_json(v) for v in obj]
    return obj


def _empty_divergence_report(backtest_records: list[dict] | None = None) -> dict:
    """VirtualTradeService가 없을 때도 비교 API contract를 유지한다."""
    backtest_records = backtest_records or []
    return {
        "summary": {
            "backtest_count": len(backtest_records),
            "live_count": 0,
            "matched_count": 0,
            "unmatched_backtest_count": len(backtest_records),
            "unmatched_live_count": 0,
            "avg_net_return_diff": None,
            "avg_abs_net_return_diff": None,
            "avg_fill_price_diff_pct": None,
            "total_net_pnl_diff": None,
        },
        "matches": [],
        "unmatched_backtest": backtest_records,
        "unmatched_live": [],
    }


def _get_backtest_journal_repository(ctx):
    repo = getattr(ctx, "__dict__", {}).get("backtest_journal_repository")
    return repo if repo is not None else BacktestJournalRepository()


_DEBUG_JOURNAL_DETAIL_FIELDS = ("entry_type", "stage", "cgld", "threshold")
_EXECUTION_JOURNAL_DETAIL_FIELDS = (
    "order_id",
    "order_type",
    "requested_qty",
    "filled_qty",
    "remaining_qty",
    "gross_amount",
    "slippage_amount_won",
    "slippage_pct",
    "priority",
)
_JOURNAL_DETAIL_FIELDS = _DEBUG_JOURNAL_DETAIL_FIELDS + _EXECUTION_JOURNAL_DETAIL_FIELDS


def _has_value(value) -> bool:
    return value not in (None, "")


def _expand_debug_journal_fields(record: dict) -> dict:
    """표준 journal metadata 안의 세부 필드를 운영 UI용 top-level 필드로 노출한다."""
    if not isinstance(record, dict):
        return record

    expanded = dict(record)
    metadata = expanded.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    for field in _JOURNAL_DETAIL_FIELDS:
        if not _has_value(expanded.get(field)) and _has_value(metadata.get(field)):
            expanded[field] = metadata.get(field)
    return expanded


def _first_debug_field(field: str, *records: dict):
    for record in records:
        if not isinstance(record, dict):
            continue
        value = record.get(field)
        if _has_value(value):
            return value
        metadata = record.get("metadata")
        if isinstance(metadata, dict) and _has_value(metadata.get(field)):
            return metadata.get(field)
    return None


def _expand_debug_comparison_row(row: dict) -> dict:
    if not isinstance(row, dict):
        return row

    expanded = dict(row)
    backtest = _expand_debug_journal_fields(expanded.get("backtest") or {})
    live = _expand_debug_journal_fields(expanded.get("live") or {})
    if "backtest" in expanded:
        expanded["backtest"] = backtest
    if "live" in expanded:
        expanded["live"] = live

    for field in _JOURNAL_DETAIL_FIELDS:
        if not _has_value(expanded.get(field)):
            value = _first_debug_field(field, backtest, live)
            if _has_value(value):
                expanded[field] = value
    return expanded


def _expand_debug_divergence_report(report: dict) -> dict:
    if not isinstance(report, dict):
        return report

    expanded = dict(report)
    expanded["matches"] = [
        _expand_debug_comparison_row(row)
        for row in expanded.get("matches", [])
    ]
    expanded["unmatched_backtest"] = [
        _expand_debug_journal_fields(row)
        for row in expanded.get("unmatched_backtest", [])
    ]
    expanded["unmatched_live"] = [
        _expand_debug_journal_fields(row)
        for row in expanded.get("unmatched_live", [])
    ]
    return expanded


@router.get("/virtual/journal")
async def get_virtual_standard_journal(limit: int | None = 500):
    """실거래/모의거래 원장을 백테스트 비교용 표준 schema로 반환한다."""
    ctx = _get_ctx()
    vm = _sync_virtual_trade_state(ctx)
    if vm is None or not hasattr(vm, "get_standard_journal_records"):
        return {"records": [], "count": 0, "total_count": 0}

    records = [_expand_debug_journal_fields(record) for record in vm.get_standard_journal_records()]
    total_count = len(records)
    if limit is not None and limit > 0:
        records = records[-limit:]

    return _sanitize_for_json({
        "records": records,
        "count": len(records),
        "total_count": total_count,
    })


@router.post("/virtual/backtest-divergence")
async def post_virtual_backtest_divergence(
    backtest_records: list[dict] | None = Body(default=None),
):
    """백테스트 journal payload와 현재 실거래/모의거래 표준 원장을 비교한다."""
    backtest_records = backtest_records or []
    ctx = _get_ctx()
    vm = _sync_virtual_trade_state(ctx)

    if vm is None:
        return _sanitize_for_json(_expand_debug_divergence_report(_empty_divergence_report(backtest_records)))

    if hasattr(vm, "compare_with_backtest_journal"):
        return _sanitize_for_json(_expand_debug_divergence_report(vm.compare_with_backtest_journal(backtest_records)))

    if hasattr(vm, "get_standard_journal_records"):
        live_records = vm.get_standard_journal_records()
        return _sanitize_for_json(_expand_debug_divergence_report(compare_trade_journals(backtest_records, live_records)))

    return _sanitize_for_json(_expand_debug_divergence_report(_empty_divergence_report(backtest_records)))


@router.get("/virtual/backtest-journals")
async def get_virtual_backtest_journal_runs(limit: int | None = 50):
    """저장된 백테스트 journal run 목록을 반환한다."""
    ctx = _get_ctx()
    repo = _get_backtest_journal_repository(ctx)
    runs = repo.list_runs(limit=limit)
    return _sanitize_for_json({"runs": runs, "count": len(runs)})


@router.get("/virtual/backtest-journals/{run_id}")
async def get_virtual_backtest_journal_records(run_id: str):
    """저장된 백테스트 journal run의 records를 반환한다."""
    ctx = _get_ctx()
    repo = _get_backtest_journal_repository(ctx)
    records = [_expand_debug_journal_fields(record) for record in repo.load_records(run_id)]
    return _sanitize_for_json({
        "run_id": run_id,
        "records": records,
        "count": len(records),
    })


def _aggregate_virtual_data(trades, vm, apply_cost):
    """Pandas 기반 집계 (CPU-bound) — thread pool에서 실행되는 순수 동기 함수."""
    if not trades:
        return {
            "summary_agg": {}, "cumulative_returns": {},
            "daily_changes": {}, "weekly_changes": {},
            "daily_ref_dates": {}, "weekly_ref_dates": {},
            "first_dates": {}, "counts": {},
            "profit_factors": {}, "expectancies": {},
        }

    try:
        df = pd.DataFrame(trades)

        for col, default in [('status', 'HOLD'), ('sell_date', None)]:
            if col not in df.columns:
                df[col] = default
        for col, default in [('qty', 1), ('buy_price', 0), ('current_price', 0), ('sell_price', 0)]:
            if col not in df.columns:
                df[col] = default
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(default)

        df['eval_price'] = np.where(
            df['status'] == 'HOLD', df['current_price'],
            np.where(df['sell_price'] > 0, df['sell_price'], df['current_price'])
        )
        df['eval_price'] = np.where(df['eval_price'] <= 0, df['buy_price'], df['eval_price'])

        df['buy_amt']  = df.apply(lambda x: vm.get_trade_amount(x['buy_price'],  x['qty'], is_sell=False, apply_cost=apply_cost), axis=1)
        df['eval_amt'] = df.apply(lambda x: vm.get_trade_amount(x['eval_price'], x['qty'], is_sell=True,  apply_cost=apply_cost), axis=1)
        df['pnl'] = df['eval_amt'] - df['buy_amt']

        strategies = [s for s in df['strategy'].dropna().unique() if s]

        # 전략별 누적수익률
        summary_agg = {"ALL": {"buy_sum": float(df['buy_amt'].sum()), "eval_sum": float(df['eval_amt'].sum())}}
        for strat in strategies:
            mask = df['strategy'] == strat
            summary_agg[strat] = {
                "buy_sum":  float(df.loc[mask, 'buy_amt'].sum()),
                "eval_sum": float(df.loc[mask, 'eval_amt'].sum()),
            }
        strategy_returns = {
            k: (round(((v["eval_sum"] - v["buy_sum"]) / v["buy_sum"]) * 100, 2) if v["buy_sum"] > 0 else 0.0)
            for k, v in summary_agg.items()
        }

        # 스냅샷 저장 및 변화율
        daily_changes, weekly_changes, daily_ref_dates, weekly_ref_dates = {}, {}, {}, {}
        try:
            vm.save_daily_snapshot(strategy_returns)
            snapshot_data = vm._load_data()
            for key in ["ALL"] + list(strategies):
                d_val, d_date = vm.get_daily_change(key, strategy_returns.get(key, 0), _data=snapshot_data)
                w_val, w_date = vm.get_weekly_change(key, strategy_returns.get(key, 0), _data=snapshot_data)
                daily_changes[key], weekly_changes[key] = d_val, w_val
                if d_date: daily_ref_dates[key] = d_date
                if w_date: weekly_ref_dates[key] = w_date
        except Exception as e:
            logger.error(f"[WebAPI] virtual/history 스냅샷 처리 오류: {e}")

        # 최초 매매일
        df['buy_date_str'] = df['buy_date'].astype(str).str[:10]
        valid_mask = df['buy_date_str'].str.match(r'^\d{4}-\d{2}-\d{2}$', na=False)
        valid_df_dates = df[valid_mask]
        all_min = valid_df_dates['buy_date_str'].min()
        first_dates = {}
        if pd.notna(all_min) and all_min:
            first_dates["ALL"] = str(all_min)
        for strat in strategies:
            mn = valid_df_dates[valid_df_dates['strategy'] == strat]['buy_date_str'].min()
            if pd.notna(mn) and mn:
                first_dates[strat] = str(mn)

        # 상태별 카운트
        KST = timezone(timedelta(hours=9))
        today_str = datetime.now(KST).strftime("%Y-%m-%d")
        try:
            snap = vm._load_data()
            if isinstance(snap, dict) and snap.get('daily'):
                daily_keys = sorted(snap['daily'].keys())
                if daily_keys and today_str > daily_keys[-1]:
                    today_str = daily_keys[-1]
        except Exception:
            pass

        df['is_hold']       = df['status'] == 'HOLD'
        df['is_today_buy']  = df['buy_date'].astype(str).str.startswith(today_str)
        df['is_today_sell'] = (df['status'] == 'SOLD') & df['sell_date'].astype(str).str.startswith(today_str)

        counts = {"ALL": {
            "hold":       int(df['is_hold'].sum()),
            "today_buy":  int(df['is_today_buy'].sum()),
            "today_sell": int(df['is_today_sell'].sum()),
        }}
        for strat in strategies:
            mask = df['strategy'] == strat
            counts[strat] = {
                "hold":       int(df.loc[mask, 'is_hold'].sum()),
                "today_buy":  int(df.loc[mask, 'is_today_buy'].sum()),
                "today_sell": int(df.loc[mask, 'is_today_sell'].sum()),
            }

        # Profit Factor & Expectancy
        def calc_metrics(sub_df):
            gains  = sub_df[sub_df['pnl'] >= 0]['pnl']
            losses = sub_df[sub_df['pnl'] <  0]['pnl']
            tot_g, tot_l = float(gains.sum()), abs(float(losses.sum()))
            wins, loss_c = len(gains), len(losses)
            tot_c = wins + loss_c
            pf = {
                "value": (round(tot_g / tot_l, 2) if tot_l > 0 else (None if tot_g > 0 else 0.0)),
                "total_gain": round(tot_g), "total_loss": round(tot_l),
            }
            exp = {"value": 0.0, "win_rate": 0.0, "avg_gain": 0, "avg_loss": 0, "wins": 0, "losses": 0}
            if tot_c > 0:
                w_rate, l_rate = wins / tot_c, loss_c / tot_c
                avg_g = tot_g / wins      if wins   > 0 else 0
                avg_l = tot_l / loss_c   if loss_c  > 0 else 0
                exp.update({
                    "value": round((w_rate * avg_g) - (l_rate * avg_l), 0),
                    "win_rate": round(w_rate * 100, 1),
                    "avg_gain": round(avg_g), "avg_loss": round(avg_l),
                    "wins": wins, "losses": loss_c,
                })
            return pf, exp

        valid_df = df[df['buy_price'] > 0]
        profit_factors, expectancies = {}, {}
        pf_all, exp_all = calc_metrics(valid_df)
        profit_factors["ALL"], expectancies["ALL"] = pf_all, exp_all
        for strat in strategies:
            pf_s, exp_s = calc_metrics(valid_df[valid_df['strategy'] == strat])
            profit_factors[strat], expectancies[strat] = pf_s, exp_s

    except Exception as e:
        logger.error(f"[WebAPI] virtual/history Pandas 집계 오류: {e}")
        summary_agg = {}
        strategy_returns = {}
        daily_changes = weekly_changes = daily_ref_dates = weekly_ref_dates = {}
        first_dates = counts = profit_factors = expectancies = {}

    return {
        "summary_agg":       summary_agg,
        "cumulative_returns": strategy_returns,
        "daily_changes":     daily_changes,
        "weekly_changes":    weekly_changes,
        "daily_ref_dates":   daily_ref_dates,
        "weekly_ref_dates":  weekly_ref_dates,
        "first_dates":       first_dates,
        "counts":            counts,
        "profit_factors":    profit_factors,
        "expectancies":      expectancies,
    }


@router.get("/virtual/history")
async def get_virtual_history(force_code: str = None, apply_cost: bool = True):
    """가상 매매 전체 기록 조회 (force_code 지정 시 해당 종목은 캐시 무시)"""
    ctx = _get_ctx()
    async with ctx.pm.profile_async("get_virtual_history"):
        return await _get_virtual_history_impl(ctx, force_code, apply_cost)


async def _get_virtual_history_impl(ctx, force_code, apply_cost):
    """get_virtual_history의 실제 구현 (Pandas 고속 집계 적용 버전)"""
    t_start = ctx.pm.start_timer()
    vm = _sync_virtual_trade_state(ctx)
    if vm is None:
        return {"trades": [], "weekly_changes": {}}

    trades = [t for t in vm.get_all_trades(apply_cost=apply_cost) if t.get('status') != 'FAILED']

    # ---------------------------------------------------------
    # 1~3단계: 종목명 및 현재가 Enrichment (기존 로직 유지 - API 통신)
    # ---------------------------------------------------------
    try:
        mapper = getattr(ctx, 'stock_code_repository', None)
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
                        logger.error(f"[WebAPI] 복수종목 조회 예외: {e}")

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

                # 미매도 가정 수익률 (매수가 → 현재가)
                cur = trade.get('current_price')
                if cur:
                    trade['hold_return_rate'] = vm.calculate_return(bp, float(cur), qty, apply_cost=apply_cost)

    except Exception as e:
        logger.error(f"[WebAPI] virtual/history enrichment 오류: {e}")

    # ---------------------------------------------------------
    # 4~7단계: Pandas 고속 집계 — CPU-bound 작업을 thread pool로 위임
    # (이벤트 루프 차단 방지: 집계 중에도 다른 요청 처리 가능)
    # ---------------------------------------------------------
    loop = asyncio.get_event_loop()
    agg = await loop.run_in_executor(None, _aggregate_virtual_data, trades, vm, apply_cost)

    ctx.pm.log_timer("get_virtual_history", t_start)
    return _sanitize_for_json({"trades": trades, **agg})


@router.get("/virtual/stage3-alerts")
async def get_stage3_alerts():
    """보유 종목 중 Minervini Stage 3(고점)에 진입한 종목 목록을 반환한다.

    VirtualTradeService에서 HOLD 포지션을 가져온 뒤,
    MinerviniStageService로 각 종목의 현재 Stage를 병렬 조회한다.
    Stage 3 종목에는 "Trailing Stop 강화 권장" 알림이 포함된다.

    MinerviniStageService가 미초기화된 경우 Stage 데이터 없이
    보유 종목 목록만 반환한다 (graceful degradation).
    """
    ctx = _get_ctx()
    vm = getattr(ctx, "virtual_trade_service", None)
    minervini_svc = getattr(ctx, "minervini_stage_service", None)

    if not vm:
        return {"alerts": [], "error": "VirtualTradeService 미초기화"}

    holds = vm.get_holds()
    if not holds:
        return {"alerts": [], "count": 0}

    async def _check_stage(hold: dict):
        code = hold.get("code")
        if not code:
            return None
        stage = 0
        if minervini_svc:
            try:
                result = await asyncio.wait_for(
                    minervini_svc.get_stage_for_code(code), timeout=5.0
                )
                stage = result[0] if isinstance(result, tuple) else int(result)
            except Exception:
                stage = 0
        if stage == 3:
            return {
                "code": code,
                "strategy": hold.get("strategy"),
                "buy_price": hold.get("buy_price"),
                "buy_date": hold.get("buy_date"),
                "stage": stage,
                "alert": "Stage 3(고점) 진입 — Trailing Stop 강화 권장",
            }
        return None

    results = await asyncio.gather(*[_check_stage(h) for h in holds])
    alerts = [r for r in results if r is not None]
    return {"alerts": alerts, "count": len(alerts)}
