# managers/virtual_trade_manager.py
import pandas as pd
import os
import json
import math
import logging
from datetime import datetime, timedelta

from core.time_manager import TimeManager
logger = logging.getLogger(__name__)

COLUMNS = ["strategy", "code", "buy_date", "buy_price", "qty", "sell_date", "sell_price", "return_rate", "status"]


class VirtualTradeManager:
    def __init__(self, filename="data/trade_journal.csv", time_manager: TimeManager = None):
        self.filename = filename
        self.tm = time_manager if time_manager else TimeManager()
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

    def log_sell(self, code: str, current_price, qty: int = 1):
        """가상 매도 — 해당 종목 가장 최근 HOLD 건."""
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

    def log_sell_by_strategy(self, strategy_name: str, code: str, current_price, qty: int = 1):
        """전략+종목 매칭 매도."""
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

    # ---- 포트폴리오 스냅샷 (전일/전주대비 계산용) ----
    #
    # JSON 구조:
    # {
    #   "daily": {"2026-02-13": {"ALL": 2.5, "수동매매": 2.5}, ...},
    #   "prev_values": {"ALL": 0.0, "수동매매": 0.0}  ← 마지막 변동 전 기준값
    # }

    def _snapshot_path(self) -> str:
        return os.path.join(os.path.dirname(self.filename), "portfolio_snapshots.json")

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
        """오늘 스냅샷 저장 + prev_values(전일대비 기준점) 갱신."""
        today = self.tm.get_current_kst_time().strftime("%Y-%m-%d")
        data = self._load_data()
        daily = data["daily"]
        prev_values = data.setdefault("prev_values", {})

        # 오늘 이전 가장 최근 스냅샷과 비교 → 값이 변했으면 prev_values를 그 스냅샷 값으로 갱신
        prev_dates = sorted([d for d in daily if d < today], reverse=True)
        if prev_dates:
            last_snapshot = daily[prev_dates[0]]
            for key, cur_val in strategy_returns.items():
                old_val = last_snapshot.get(key)
                if old_val is not None and abs(cur_val - old_val) >= 0.01:
                    prev_values[key] = old_val

        # 오늘 스냅샷 저장 (같은 날 여러 번 호출 시 최신값으로 덮어쓰기)
        daily[today] = strategy_returns

        # 30일 이전 데이터 정리
        cutoff = (self.tm.get_current_kst_time() - timedelta(days=30)).strftime("%Y-%m-%d")
        data["daily"] = {d: v for d, v in daily.items() if d >= cutoff}

        self._save_data(data)

    def get_daily_change(self, strategy: str, current_return: float, *, _data: dict | None = None) -> float:
        """마지막 변동일 기준 전일대비. prev_values 없으면 누적수익률 자체 반환."""
        data = _data or self._load_data()
        prev_val = data.get("prev_values", {}).get(strategy)
        if prev_val is None:
            return current_return
        return round(current_return - prev_val, 2)

    def get_weekly_change(self, strategy: str, current_return: float, *, _data: dict | None = None) -> float | None:
        """7일 전 스냅샷 대비 변화. 스냅샷 없으면 None."""
        data = _data or self._load_data()
        daily = data.get("daily", {})
        today = self.tm.get_current_kst_time().strftime("%Y-%m-%d")
        target = (self.tm.get_current_kst_time() - timedelta(days=7)).strftime("%Y-%m-%d")

        candidates = sorted([d for d in daily if d <= target and d != today], reverse=True)
        if not candidates:
            return None

        ref_val = daily[candidates[0]].get(strategy)
        if ref_val is None:
            return None
        return round(current_return - ref_val, 2)

    def get_strategy_return_history(self, strategy_name: str) -> list[dict]:
        """특정 전략의 누적 수익률 히스토리를 반환합니다 (그래프용)."""
        data = self._load_data()
        daily = data.get("daily", {})
        all_dates = sorted(daily.keys())

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
