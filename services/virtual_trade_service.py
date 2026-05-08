# services/virtual_trade_service.py
import logging
from typing import List, Dict, Tuple, Optional
import pandas as pd
import bisect
from functools import lru_cache
from datetime import datetime, timedelta

from repositories.virtual_trade_repository import VirtualTradeRepository
from core.market_clock import MarketClock
from common.trade_journal_comparison import compare_trade_journals
from utils.transaction_cost_utils import TransactionCostUtils

logger = logging.getLogger(__name__)

@lru_cache(maxsize=1024)
def _is_weekday(date_str: str) -> bool:
    return datetime.strptime(date_str, "%Y-%m-%d").weekday() < 5

def _strategy_values(snapshot: dict) -> dict:
    return {k: v for k, v in snapshot.items() if k != "ALL"}

def _get_trading_dates(daily: dict) -> list[str]:
    weekday_dates = sorted(d for d in daily if _is_weekday(d))
    if not weekday_dates: return []
    trading = [weekday_dates[0]]
    for d in weekday_dates[1:]:
        if _strategy_values(daily[d]) != _strategy_values(daily[trading[-1]]):
            trading.append(d)
    return trading


def _build_strategy_return_history(daily: dict, strategy_name: str) -> list[dict]:
    if not daily:
        return []

    df = pd.DataFrame.from_dict(daily, orient='index')
    if strategy_name not in df.columns:
        return []

    series = df[strategy_name].sort_index()
    first_valid = series.first_valid_index()
    if first_valid is None:
        return []

    series = series.loc[first_valid:].ffill()
    return [
        {"date": date, "return_rate": float(val)}
        for date, val in series.items()
        if _is_weekday(date) and pd.notna(val)
    ]

class VirtualTradeService:
    """모의매매 통계 계산 및 성과 분석을 담당하는 비즈니스 서비스 계층"""
    def __init__(self, repository: VirtualTradeRepository, market_clock: MarketClock = None):
        self._repo = repository
        self.tm = market_clock or getattr(repository, "tm", None) or MarketClock()

    # ── 비즈니스 & 통계 계산 로직 ──

    def calculate_return(self, buy_price, sell_price, qty=1, apply_cost=False) -> float:
        return round(TransactionCostUtils.get_return_rate(buy_price, sell_price, qty, apply_cost), 2)

    def get_trade_amount(self, price, qty=1, is_sell=False, apply_cost=False) -> float:
        base_amount = price * qty
        if not apply_cost: return base_amount
        cost = TransactionCostUtils.calculate_cost(price, qty, is_sell)
        return base_amount - cost if is_sell else base_amount + cost

    def get_all_trades(self, apply_cost: bool = True) -> list:
        df = self._repo._read()
        records = [dict(r) for r in self._repo._to_json_records(df)]
        if apply_cost:
            for r in records:
                if r.get('status') == 'SOLD' and r.get('buy_price') and r.get('sell_price'):
                    if 'gross_return' not in r:
                        r['gross_return'] = r.get('return_rate')
                    r['return_rate'] = self.calculate_return(r['buy_price'], r['sell_price'], r.get('qty', 1), True)
                    r['net_return'] = r['return_rate']
        return records

    def get_summary(self, apply_cost: bool = True) -> dict:
        df = self._repo._read()
        total_trades = len(df)
        sold_df = df[df['status'] == 'SOLD']
        
        if sold_df.empty:
            return {"total_trades": total_trades, "win_rate": 0, "avg_return": 0}

        if apply_cost:
            returns = sold_df.apply(lambda row: self.calculate_return(row['buy_price'], row['sell_price'], row['qty'], True), axis=1)
        else:
            returns = sold_df['return_rate']

        win_trades = len(returns[returns > 0])
        win_rate = (win_trades / len(sold_df) * 100)
        avg_return = returns.mean()

        return {
            "total_trades": total_trades,
            "win_rate": round(win_rate, 1),
            "avg_return": round(avg_return, 2)
        }

    def get_daily_change(self, strategy: str, current_return: float, *, _data: dict | None = None) -> tuple[float | None, str | None]:
        data = _data or self._repo._load_data()
        daily = data.get("daily", {})
        if not daily: return None, None
        today = self.tm.get_current_kst_time().strftime("%Y-%m-%d")
        all_trading = _get_trading_dates(daily)
        trading = [d for d in all_trading if d <= today]
        if len(trading) < 2: return None, None
        latest_date = trading[-1]
        prev_date = trading[-2]
        latest_val = daily[latest_date].get(strategy)
        prev_val = daily[prev_date].get(strategy)
        if latest_val is None or prev_val is None: return None, None
        return round(latest_val - prev_val, 2), prev_date

    def get_weekly_change(self, strategy: str, current_return: float, *, _data: dict | None = None) -> tuple[float | None, str | None]:
        data = _data or self._repo._load_data()
        daily = data.get("daily", {})
        if not daily: return None, None
        today = self.tm.get_current_kst_time().strftime("%Y-%m-%d")
        target = (self.tm.get_current_kst_time() - timedelta(days=7)).strftime("%Y-%m-%d")
        sorted_dates = sorted(daily.keys())
        candidates = [d for d in sorted_dates if d <= target and d != today]
        if not candidates: return None, None
        ref_date = candidates[-1]
        ref_val = daily[ref_date].get(strategy)
        if ref_val is None: return None, None
        return round(current_return - ref_val, 2), ref_date

    def get_strategy_return_history(self, strategy_name: str) -> list[dict]:
        data = self._repo._load_data()
        daily = data.get("daily", {})
        return _build_strategy_return_history(daily, strategy_name)

    def get_all_strategies(self) -> list[str]:
        data = self._repo._load_data()
        daily = data.get("daily", {})
        if not daily: return []
        strategies = set()
        recent_dates = sorted(daily.keys(), reverse=True)[:5]
        for date in recent_dates: strategies.update(daily[date].keys())
        if "ALL" in strategies: strategies.remove("ALL")
        return sorted(list(strategies))

    def compare_with_backtest_journal(self, backtest_records: list[dict]) -> dict:
        """현재 원장과 백테스트 journal records 간 괴리 리포트를 생성한다."""
        return compare_trade_journals(
            backtest_records,
            self.get_standard_journal_records(),
        )

    # ── 데이터 영속성 위임 (Facade) ──
    # 기존 코드 호환성을 위해 순수 I/O 요청을 Repository로 그대로 전달합니다.
    def log_buy(self, *args, **kwargs): return self._repo.log_buy(*args, **kwargs)
    async def log_buy_async(self, *args, **kwargs): return await self._repo.log_buy_async(*args, **kwargs)
    def log_sell(self, *args, **kwargs): return self._repo.log_sell(*args, **kwargs)
    async def log_sell_async(self, *args, **kwargs): return await self._repo.log_sell_async(*args, **kwargs)
    async def log_sell_async_with_result(self, *args, **kwargs): return await self._repo.log_sell_async_with_result(*args, **kwargs)
    def log_sell_by_strategy(self, *args, **kwargs): return self._repo.log_sell_by_strategy(*args, **kwargs)
    async def log_sell_by_strategy_async(self, *args, **kwargs): return await self._repo.log_sell_by_strategy_async(*args, **kwargs)
    async def log_sell_by_strategy_async_with_result(self, *args, **kwargs): return await self._repo.log_sell_by_strategy_async_with_result(*args, **kwargs)
    def get_holds(self): return self._repo.get_holds()
    def get_solds(self, apply_cost: bool = True): return self._repo.get_solds(apply_cost=apply_cost)
    def get_holds_by_strategy(self, strategy_name: str): return self._repo.get_holds_by_strategy(strategy_name)
    def is_holding(self, strategy_name: str, code: str): return self._repo.is_holding(strategy_name, code)
    def log_order_failure(self, *args, **kwargs): return self._repo.log_order_failure(*args, **kwargs)
    async def log_order_failure_async(self, *args, **kwargs): return await self._repo.log_order_failure_async(*args, **kwargs)
    def fix_sell_price(self, *args, **kwargs): return self._repo.fix_sell_price(*args, **kwargs)
    def backfill_snapshots(self): return self._repo.backfill_snapshots()
    def save_daily_snapshot(self, strategy_returns: dict): return self._repo.save_daily_snapshot(strategy_returns)
    def sync_live_strategy_positions(self): return self._repo.sync_live_strategy_positions()
    def get_standard_journal_records(self): return self._repo.get_standard_journal_records()
    def _load_data(self): return self._repo._load_data()
    def _save_data(self, data: dict): return self._repo._save_data(data)

    async def reconcile_with_broker(self, actual_holdings: list, logger=None) -> dict:
        """실제 증권사 잔고와 로컬 DB를 비교하여 불일치를 처리한다.

        - 로컬 HOLD인데 실제 잔고 없음 → log_sell_async(code, 0, reason="reconciled_force_close") 강제 종결 + 경고
        - 실제 잔고 있는데 로컬 없음 → 경고 로그만 (전략명 불명확 → 자동 insert 불가)

        Args:
            actual_holdings: broker get_account_balance() resp.data["output1"] 리스트
            logger: 선택적 logger

        Returns:
            {"force_closed": [codes], "unknown_in_broker": [codes]}
        """
        _log = logger or __import__('logging').getLogger(__name__)

        actual_codes = {
            str(h.get("pdno", "")).strip()
            for h in actual_holdings
            if str(h.get("hldg_qty", "0") or "0").strip() not in ("0", "", "00")
        }

        local_holds = self.get_holds()
        local_codes = {str(h.get("code", "")).strip() for h in local_holds if h.get("code")}

        force_closed = []
        for hold in local_holds:
            code = str(hold.get("code", "")).strip()
            if code and code not in actual_codes:
                _log.warning(
                    f"[Reconciliation] 로컬 HOLD이나 실제 잔고 없음 → 강제 종결: "
                    f"{code} (strategy={hold.get('strategy')})"
                )
                await self.log_sell_async(code, 0, reason="reconciled_force_close")
                force_closed.append(code)

        unknown_in_broker = sorted(actual_codes - local_codes)
        if unknown_in_broker:
            _log.warning(
                f"[Reconciliation] 실제 보유 중이나 로컬 DB 없음 (수동 확인 필요): {unknown_in_broker}"
            )

        return {"force_closed": force_closed, "unknown_in_broker": unknown_in_broker}
