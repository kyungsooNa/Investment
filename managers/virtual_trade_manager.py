# managers/virtual_trade_manager.py
import bisect
import numpy as np
import pandas as pd
import asyncio
import threading
import os
import json
import math
import logging
from datetime import datetime, timedelta

from core.time_manager import TimeManager
logger = logging.getLogger(__name__)

COLUMNS = ["strategy", "code", "buy_date", "buy_price", "qty", "sell_date", "sell_price", "return_rate", "status"]
SNAPSHOT_FILENAME = "portfolio_snapshots.json"


def _is_weekday(date_str: str) -> bool:
    """날짜 문자열(YYYY-MM-DD)이 평일인지 확인"""
    return datetime.strptime(date_str, "%Y-%m-%d").weekday() < 5


def _strategy_values(snapshot: dict) -> dict:
    """스냅샷에서 개별 전략 값만 추출 (ALL 제외). ALL은 파생 값이라 비교에서 제외."""
    return {k: v for k, v in snapshot.items() if k != "ALL"}


def _get_trading_dates(daily: dict) -> list[str]:
    """스냅샷 dict에서 실제 거래일만 추출 (평일 + 개별 전략 값이 변한 날짜). 오름차순 반환."""
    weekday_dates = sorted(d for d in daily if _is_weekday(d))
    if not weekday_dates:
        return []
    trading = [weekday_dates[0]]  # 첫 날은 항상 포함
    for d in weekday_dates[1:]:
        if _strategy_values(daily[d]) != _strategy_values(daily[trading[-1]]):
            trading.append(d)
    return trading
PRICE_CACHE_FILENAME = "close_price_cache.json"


class VirtualTradeManager:
    def __init__(self, filename="data/VirtualTradeManager/trade_journal.csv", time_manager: TimeManager = None):
        self.filename = filename
        self.tm = time_manager if time_manager else TimeManager()
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(self.filename), exist_ok=True)  # 데이터 디렉토리 생성
        if not os.path.exists(self.filename):
            pd.DataFrame(columns=COLUMNS).to_csv(self.filename, index=False)

    def _read(self) -> pd.DataFrame:
        df = pd.read_csv(self.filename, dtype={'code': str})
        df['return_rate'] = df['return_rate'].fillna(0.0)
        # 기존 파일 호환성: qty 컬럼이 없으면 기본값 1로 채움
        if 'qty' not in df.columns:
            df['qty'] = 1
        return df

    def _write(self, df: pd.DataFrame):
        df.to_csv(self.filename, index=False)

    # ---- 매수/매도 ----

    def log_buy(self, strategy_name: str, code: str, current_price, qty: int = 1):
        """가상 매수 기록. 동일 전략+종목 중복 매수 방지."""
        with self._lock:
            df = self._read()
            if self.is_holding(strategy_name, code):
                logger.info(f"[가상매매] {strategy_name}/{code} 이미 보유 중 — 매수 스킵")
                return
            new_trade = {
                "strategy": strategy_name,
                "code": code,
                "buy_date": self.tm.get_current_kst_time().strftime("%Y-%m-%d %H:%M:%S"),
                "buy_price": current_price,
                "qty": qty,
                "sell_date": None,
                "sell_price": None,
                "return_rate": 0.0,
                "status": "HOLD"
            }
            df = pd.concat([df, pd.DataFrame([new_trade])], ignore_index=True)
            self._write(df)
            logger.info(f"[가상매매] {strategy_name}/{code} 매수 기록 (가격: {current_price}, 수량: {qty})")

    async def log_buy_async(self, strategy_name: str, code: str, current_price, qty: int = 1):
        """log_buy의 비동기 래퍼 (스레드 실행)."""
        await asyncio.to_thread(self.log_buy, strategy_name, code, current_price, qty)

    def log_sell(self, code: str, current_price, qty: int = 1):
        """가상 매도 — 해당 종목 가장 최근 HOLD 건."""
        with self._lock:
            df = self._read()
            mask = (df['code'] == code) & (df['status'] == 'HOLD')
            if df.loc[mask].empty:
                logger.warning(f"[가상매매] {code} 매도 실패: 보유 내역 없음")
                return
            idx = df.loc[mask].index[-1]
            buy_price = df.loc[idx, 'buy_price']
            return_rate = ((current_price - buy_price) / buy_price) * 100 if buy_price else 0
            df.loc[idx, 'sell_date'] = self.tm.get_current_kst_time().strftime("%Y-%m-%d %H:%M:%S")
            df.loc[idx, 'sell_price'] = current_price
            df.loc[idx, 'return_rate'] = round(return_rate, 2)
            df.loc[idx, 'status'] = 'SOLD'
            self._write(df)
            logger.info(f"[가상매매] {code} 매도 기록 (수익률: {return_rate:.2f}%)")

    async def log_sell_async(self, code: str, current_price, qty: int = 1):
        """log_sell의 비동기 래퍼 (스레드 실행)."""
        await asyncio.to_thread(self.log_sell, code, current_price, qty)

    def log_sell_by_strategy(self, strategy_name: str, code: str, current_price, qty: int = 1):
        """전략+종목 매칭 매도."""
        with self._lock:
            df = self._read()
            mask = (df['strategy'] == strategy_name) & (df['code'] == code) & (df['status'] == 'HOLD')
            if df.loc[mask].empty:
                logger.warning(f"[가상매매] {strategy_name}/{code} 매도 실패: 보유 내역 없음")
                return
            idx = df.loc[mask].index[-1]
            buy_price = df.loc[idx, 'buy_price']
            return_rate = ((current_price - buy_price) / buy_price) * 100 if buy_price else 0
            df.loc[idx, 'sell_date'] = self.tm.get_current_kst_time().strftime("%Y-%m-%d %H:%M:%S")
            df.loc[idx, 'sell_price'] = current_price
            df.loc[idx, 'return_rate'] = round(return_rate, 2)
            df.loc[idx, 'status'] = 'SOLD'
            self._write(df)
            logger.info(f"[가상매매] {strategy_name}/{code} 매도 기록 (수익률: {return_rate:.2f}%)")

    async def log_sell_by_strategy_async(self, strategy_name: str, code: str, current_price, qty: int = 1):
        """log_sell_by_strategy의 비동기 래퍼 (스레드 실행)."""
        await asyncio.to_thread(self.log_sell_by_strategy, strategy_name, code, current_price, qty)

    # ---- 조회 ----

    def _to_json_records(self, df: pd.DataFrame) -> list:
        """DataFrame을 JSON 직렬화 가능한 dict 리스트로 변환 (NaN -> None)."""
        records = df.to_dict(orient='records')
        for record in records:
            for key, value in record.items():
                if isinstance(value, float) and math.isnan(value):
                    record[key] = None
        return records

    def get_all_trades(self) -> list:
        """전체 거래 기록 반환 (웹 API용)."""
        df = self._read()
        return self._to_json_records(df)

    def get_solds(self) -> list:
        """전체 SOLD 포지션 반환."""
        df = self._read()
        return self._to_json_records(df.loc[df['status'] == 'SOLD'])

    def get_holds(self) -> list:
        """전체 HOLD 포지션 반환."""
        df = self._read()
        return self._to_json_records(df.loc[df['status'] == 'HOLD'])

    def get_holds_by_strategy(self, strategy_name: str) -> list:
        """전략별 HOLD 포지션 반환."""
        df = self._read()
        mask = (df['strategy'] == strategy_name) & (df['status'] == 'HOLD')
        return self._to_json_records(df.loc[mask])

    def is_holding(self, strategy_name: str, code: str) -> bool:
        """해당 전략에서 종목 보유 중인지 확인."""
        df = self._read()
        mask = (df['strategy'] == strategy_name) & (df['code'] == code) & (df['status'] == 'HOLD')
        return not df.loc[mask].empty

    def fix_sell_price(self, code: str, buy_date: str, correct_price):
        """sell_price가 0인 SOLD 기록의 매도가/수익률을 보정합니다."""
        with self._lock:
            df = self._read()
            mask = (df['code'] == code) & (df['status'] == 'SOLD') & (df['sell_price'] == 0)
            if buy_date:
                mask = mask & (df['buy_date'] == buy_date)
            if df.loc[mask].empty:
                return
            for idx in df.loc[mask].index:
                bp = df.loc[idx, 'buy_price']
                df.loc[idx, 'sell_price'] = correct_price
                df.loc[idx, 'return_rate'] = round(((correct_price - bp) / bp) * 100, 2) if bp else 0
            self._write(df)
            logger.info(f"[가상매매] {code} sell_price 보정 완료 → {correct_price}")

    def get_summary(self) -> dict:
        """전체 매매 요약 통계 (HOLD + SOLD 모두 포함)."""
        df = self._read()
        total_trades = len(df)
        sold_df = df[df['status'] == 'SOLD']
        win_trades = len(sold_df[sold_df['return_rate'] > 0])
        win_rate = (win_trades / len(sold_df) * 100) if len(sold_df) > 0 else 0
        avg_return = sold_df['return_rate'].mean() if len(sold_df) > 0 else 0
        return {
            "total_trades": total_trades,
            "win_rate": round(win_rate, 1),
            "avg_return": round(avg_return, 2)
        }

    # ---- 종가 캐시 (backfill용) ----

    def _price_cache_path(self) -> str:
        return os.path.join(os.path.dirname(self.filename), PRICE_CACHE_FILENAME)

    def _load_price_cache(self) -> dict:
        """로컬 종가 캐시 로드. 구조: { "005930": {"2026-02-13": 56000, ...}, ... }"""
        path = self._price_cache_path()
        if not os.path.exists(path):
            return {}
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}

    def _save_price_cache(self, cache: dict):
        path = self._price_cache_path()
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)

    def _fetch_close_prices(self, codes: list[str], start_date: str, end_date: str) -> dict:
        """pykrx로 종가 조회 후 캐시에 병합. 캐시에 이미 있으면 API 스킵.
        Returns: { code: { "YYYY-MM-DD": close_price, ... }, ... }
        """
        from pykrx import stock as pykrx_stock

        cache = self._load_price_cache()
        start_fmt = start_date.replace('-', '')
        end_fmt = end_date.replace('-', '')
        fetched = 0

        for code in codes:
            # 캐시에 해당 종목+기간 데이터가 이미 있는지 확인
            if code in cache:
                cached_dates = set(cache[code].keys())
                # start~end 범위의 영업일 중 누락된 날짜가 없으면 스킵
                needed_dates = set(
                    d.strftime('%Y-%m-%d')
                    for d in pd.date_range(start_date, end_date, freq='B')
                )
                if needed_dates.issubset(cached_dates):
                    continue

            try:
                df = pykrx_stock.get_market_ohlcv_by_date(start_fmt, end_fmt, code)
                if df.empty:
                    continue

                if code not in cache:
                    cache[code] = {}

                for date_idx, row in df.iterrows():
                    day_str = date_idx.strftime('%Y-%m-%d')
                    cache[code][day_str] = int(row['종가'])

                fetched += 1
            except Exception as e:
                logger.warning(f"[가상매매] pykrx 종가 조회 실패 {code}: {e}")
                continue

        if fetched > 0:
            self._save_price_cache(cache)
            logger.info(f"[가상매매] 종가 캐시 업데이트: {fetched}개 종목 조회")

        return cache

    # ---- backfill ----

    def backfill_snapshots(self):
        """CSV 거래 기록을 기반으로 과거 일별 스냅샷을 역산하여 채웁니다.
        이미 스냅샷이 존재하는 날짜는 덮어쓰지 않습니다.

        계산 방식 (web_api.py의 save_daily_snapshot과 동일):
        - 해당 날짜 기준 '활성 거래' = 매수일 <= day인 모든 거래
          - SOLD: sell_day <= day → 확정 return_rate 사용
          - HOLD(당시 기준): 당일 종가 기준 수익률 (pykrx 조회, 로컬 캐시)
        - 전략별 평균 return_rate 저장
        """
        df = self._read()
        if df.empty:
            return

        data = self._load_data()
        daily = data["daily"]

        # 1. 날짜 전처리
        # itertuples 접근을 위해 underscore 없는 컬럼명 사용
        df['buy_day_str'] = pd.to_datetime(df['buy_date']).dt.strftime('%Y-%m-%d')
        sell_mask = df['sell_date'].notna() & (df['sell_date'] != '')
        df['sell_day_str'] = None
        df.loc[sell_mask, 'sell_day_str'] = pd.to_datetime(df.loc[sell_mask, 'sell_date']).dt.strftime('%Y-%m-%d')

        all_days = set(df['buy_day_str'].dropna().tolist())
        all_days |= set(df.loc[sell_mask, 'sell_day_str'].dropna().tolist())

        if not all_days:
            return

        min_day = min(all_days)
        max_day = max(all_days)

        # [수정] 현재 시점(어제)까지 backfill 범위 확장 (보유 중인 경우 등 고려)
        yesterday = (self.tm.get_current_kst_time() - timedelta(days=1)).strftime('%Y-%m-%d')
        if yesterday > max_day:
            max_day = yesterday

        # backfill이 필요한 날짜 확인
        date_range = pd.date_range(min_day, max_day, freq='D')
        date_strs = [d.strftime('%Y-%m-%d') for d in date_range]
        missing_days = [d for d in date_strs if d not in daily]

        if not missing_days:
            return  # backfill 불필요

        # 2. 종가 캐시 조회 (HOLD 포지션 수익률 계산용)
        all_codes = df['code'].unique().tolist()
        price_cache = self._fetch_close_prices(all_codes, min_day, max_day)

        # [성능 개선] 종가 데이터를 DataFrame으로 변환하고 전처리 (ffill)
        # _find_prev_close 반복 호출 제거를 위해 전체 기간 데이터를 미리 채움
        price_df = pd.DataFrame()
        if price_cache:
            try:
                price_df = pd.DataFrame(price_cache)
                # 인덱스(날짜)를 datetime으로 변환하여 정렬
                price_df.index = pd.to_datetime(price_df.index)
                price_df = price_df.sort_index()
                # 전체 기간 reindex & ffill (휴장일 데이터 채우기)
                full_idx = pd.date_range(start=min_day, end=max_day)
                price_df = price_df.reindex(full_idx).ffill()
            except Exception as e:
                logger.warning(f"[가상매매] 종가 데이터프레임 변환 실패: {e}")
                price_df = pd.DataFrame()

        # 3. 날짜별 스냅샷 생성 (Numpy Optimization)
        added = 0
        missing_days.sort()
        n_days = len(missing_days)
        
        strategies = sorted(df['strategy'].unique().tolist())
        strat_to_idx = {s: i for i, s in enumerate(strategies)}
        n_strats = len(strategies)
        
        # Arrays for aggregation
        buy_sums = np.zeros((n_days, n_strats), dtype=np.float64)
        eval_sums = np.zeros((n_days, n_strats), dtype=np.float64)
        
        # Prepare Price Matrix
        price_matrix = None
        code_to_idx = {}
        
        if not price_df.empty:
            md_dt = pd.to_datetime(missing_days)
            # Reindex to missing days only
            price_df_aligned = price_df.reindex(md_dt)
            
            codes = price_df_aligned.columns.tolist()
            code_to_idx = {c: i for i, c in enumerate(codes)}
            price_matrix = price_df_aligned.to_numpy(dtype=np.float64)

        # Iterate trades
        for row in df.itertuples():
            strat = row.strategy
            s_idx = strat_to_idx.get(strat)
            if s_idx is None: continue
            
            code = row.code
            qty = float(row.qty) if hasattr(row, 'qty') and pd.notna(row.qty) else 1.0
            bp = float(row.buy_price) if pd.notna(row.buy_price) else 0.0
            if bp == 0: continue
            
            buy_date = row.buy_day_str
            sell_date = row.sell_day_str if row.status == 'SOLD' else None
            
            # Find start index in missing_days
            start_idx = bisect.bisect_left(missing_days, buy_date)
            if start_idx >= n_days:
                continue
            
            # Determine end index (sell date)
            if sell_date:
                sell_idx = bisect.bisect_left(missing_days, sell_date)
                
                # Period 2: SOLD (from sell_idx onwards)
                if sell_idx < n_days:
                    sp = float(row.sell_price) if pd.notna(row.sell_price) else 0.0
                    val = sp if sp > 0 else bp
                    
                    # Apply to [max(start_idx, sell_idx) : ]
                    s_start = max(start_idx, sell_idx)
                    if s_start < n_days:
                        buy_sums[s_start:, s_idx] += (bp * qty)
                        eval_sums[s_start:, s_idx] += (val * qty)
                
                # Period 1: HOLD (from start_idx to sell_idx)
                h_end = min(sell_idx, n_days)
                if start_idx < h_end:
                    buy_sums[start_idx:h_end, s_idx] += (bp * qty)
                    
                    # Eval using market price
                    c_idx = code_to_idx.get(code)
                    if c_idx is not None and price_matrix is not None:
                        prices = price_matrix[start_idx:h_end, c_idx]
                        # Handle NaNs
                        if np.isnan(prices).any():
                            prices = prices.copy()
                            prices[np.isnan(prices)] = bp
                        eval_sums[start_idx:h_end, s_idx] += (prices * qty)
                    else:
                        eval_sums[start_idx:h_end, s_idx] += (bp * qty)
            else:
                # HOLD until end
                buy_sums[start_idx:, s_idx] += (bp * qty)
                
                c_idx = code_to_idx.get(code)
                if c_idx is not None and price_matrix is not None:
                    prices = price_matrix[start_idx:, c_idx]
                    if np.isnan(prices).any():
                        prices = prices.copy()
                        prices[np.isnan(prices)] = bp
                    eval_sums[start_idx:, s_idx] += (prices * qty)
                else:
                    eval_sums[start_idx:, s_idx] += (bp * qty)

        # Calculate Returns
        with np.errstate(divide='ignore', invalid='ignore'):
            returns = ((eval_sums - buy_sums) / buy_sums) * 100
        returns[~np.isfinite(returns)] = 0.0
        returns = np.round(returns, 2)
        
        # Calculate ALL
        total_buy = buy_sums.sum(axis=1)
        total_eval = eval_sums.sum(axis=1)
        with np.errstate(divide='ignore', invalid='ignore'):
            all_returns = ((total_eval - total_buy) / total_buy) * 100
        all_returns[~np.isfinite(all_returns)] = 0.0
        all_returns = np.round(all_returns, 2)
        
        # Build result dict
        for i, day_str in enumerate(missing_days):
            if total_buy[i] > 0:
                day_stats = {}
                for j, strat in enumerate(strategies):
                    if buy_sums[i, j] > 0:
                        day_stats[strat] = returns[i, j]
                
                day_stats['ALL'] = all_returns[i]
                daily[day_str] = day_stats
                added += 1

        if added > 0:
            cutoff = (self.tm.get_current_kst_time() - timedelta(days=30)).strftime("%Y-%m-%d")
            data["daily"] = {d: v for d, v in sorted(daily.items()) if d >= cutoff}
            self._save_data(data)
            logger.info(f"[가상매매] 스냅샷 backfill 완료: {added}일 추가")

    @staticmethod
    def _find_prev_close(price_cache: dict, code: str, day: str):
        """해당 날짜 이전 가장 가까운 종가를 찾습니다 (휴장일 대응)."""
        code_prices = price_cache.get(code)
        if not code_prices:
            return None
        prev_dates = sorted([d for d in code_prices if d < day], reverse=True)
        return code_prices[prev_dates[0]] if prev_dates else None

    # ---- 포트폴리오 스냅샷 (전일/전주대비 계산용) ----
    #
    # JSON 구조:
    # {
    #   "daily": {"2026-02-13": {"ALL": 2.5, "수동매매": 2.5}, ...},
    #   "prev_values": {"ALL": 0.0, "수동매매": 0.0}  ← 마지막 변동 전 기준값
    # }

    def _snapshot_path(self) -> str:
        return os.path.join(os.path.dirname(self.filename), SNAPSHOT_FILENAME)

    def _load_data(self) -> dict:
        path = self._snapshot_path()
        if not os.path.exists(path):
            return {"daily": {}, "prev_values": {}}
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            # 이전 포맷(날짜가 최상위 키) → 새 포맷 마이그레이션
            if "daily" not in data:
                data = {"daily": data, "prev_values": {}}
            return data
        except (json.JSONDecodeError, IOError):
            return {"daily": {}, "prev_values": {}}

    def _save_data(self, data: dict):
        path = self._snapshot_path()
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def save_daily_snapshot(self, strategy_returns: dict):
        """오늘 스냅샷 저장. 토/일 및 직전 스냅샷과 동일한 데이터(공휴일)는 건너뜀."""
        now = self.tm.get_current_kst_time()
        if now.weekday() >= 5:  # 토(5), 일(6)
            return
        today = now.strftime("%Y-%m-%d")
        data = self._load_data()
        daily = data["daily"]

        # 직전 평일 스냅샷과 개별 전략 값이 동일하면 공휴일로 간주하여 저장 안 함
        prev_dates = sorted([d for d in daily if d < today and _is_weekday(d)], reverse=True)
        if prev_dates:
            last_snapshot = daily[prev_dates[0]]
            if _strategy_values(last_snapshot) == _strategy_values(strategy_returns):
                return

        # 오늘 스냅샷 저장 (같은 날 여러 번 호출 시 최신값으로 덮어쓰기)
        daily[today] = strategy_returns

        # 30일 이전 데이터 정리
        cutoff = (self.tm.get_current_kst_time() - timedelta(days=30)).strftime("%Y-%m-%d")
        data["daily"] = {d: v for d, v in daily.items() if d >= cutoff}

        self._save_data(data)

    def get_daily_change(self, strategy: str, current_return: float, *, _data: dict | None = None) -> tuple[float | None, str | None]:
        """가장 최근 거래일 vs 직전 거래일 스냅샷 비교. (변동값, 기준날짜) 튜플 반환."""
        data = _data or self._load_data()
        daily = data.get("daily", {})
        today = self.tm.get_current_kst_time().strftime("%Y-%m-%d")

        all_trading = _get_trading_dates(daily)
        # 오늘 이하의 거래일만
        trading = [d for d in all_trading if d <= today]
        if len(trading) < 2:
            return None, None

        latest_date = trading[-1]   # 가장 최근 거래일
        prev_date = trading[-2]     # 직전 거래일

        latest_val = daily[latest_date].get(strategy)
        prev_val = daily[prev_date].get(strategy)
        if latest_val is None or prev_val is None:
            return None, None
        return round(latest_val - prev_val, 2), prev_date

    def get_weekly_change(self, strategy: str, current_return: float, *, _data: dict | None = None) -> tuple[float | None, str | None]:
        """7일 전 거래일 스냅샷 대비 변화. (변동값, 기준날짜) 튜플 반환."""
        data = _data or self._load_data()
        daily = data.get("daily", {})
        today = self.tm.get_current_kst_time().strftime("%Y-%m-%d")
        target = (self.tm.get_current_kst_time() - timedelta(days=7)).strftime("%Y-%m-%d")

        all_trading = _get_trading_dates(daily)
        candidates = [d for d in all_trading if d <= target and d != today]
        if not candidates:
            return None, None

        ref_date = candidates[-1]
        ref_val = daily[ref_date].get(strategy)
        if ref_val is None:
            return None, None
        return round(current_return - ref_val, 2), ref_date

    def get_strategy_return_history(self, strategy_name: str) -> list[dict]:
        """특정 전략의 누적 수익률 히스토리를 반환합니다 (그래프용). 공휴일/주말 제외."""
        data = self._load_data()
        daily = data.get("daily", {})
        all_dates = _get_trading_dates(daily)

        history = []
        last_val = None
        for date in all_dates:
            returns = daily[date]
            if strategy_name in returns:
                last_val = returns[strategy_name]
                history.append({"date": date, "return_rate": last_val})
            elif last_val is not None:
                # 해당 전략의 기록이 시작된 이후인데, 특정 날짜 스냅샷에 해당 전략이 누락된 경우
                # 마지막 수익률을 채워넣어(Forward Fill) 차트가 중간에 끊기지 않게 함
                history.append({"date": date, "return_rate": last_val})

        return history

    def get_all_strategies(self) -> list[str]:
        """저장된 스냅샷에 존재하는 모든 전략 이름을 반환합니다."""
        data = self._load_data()
        daily = data.get("daily", {})
        strategies = set()
        for returns in daily.values():
            strategies.update(returns.keys())
        return sorted(list(strategies))
