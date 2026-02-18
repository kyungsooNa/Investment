# managers/virtual_trade_manager.py
import pandas as pd
import os
import math
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

COLUMNS = ["strategy", "code", "buy_date", "buy_price", "sell_date", "sell_price", "return_rate", "status"]


class VirtualTradeManager:
    def __init__(self, filename="data/trade_journal.csv"):
        self.filename = filename
        if not os.path.exists(self.filename):
            pd.DataFrame(columns=COLUMNS).to_csv(self.filename, index=False)

    def _read(self) -> pd.DataFrame:
        df = pd.read_csv(self.filename, dtype={'code': str})
        df['return_rate'] = df['return_rate'].fillna(0.0)
        return df

    def _write(self, df: pd.DataFrame):
        df.to_csv(self.filename, index=False)

    # ---- 매수/매도 ----

    def log_buy(self, strategy_name: str, code: str, current_price):
        """가상 매수 기록. 동일 전략+종목 중복 매수 방지."""
        df = self._read()
        if self.is_holding(strategy_name, code):
            logger.info(f"[가상매매] {strategy_name}/{code} 이미 보유 중 — 매수 스킵")
            return
        new_trade = {
            "strategy": strategy_name,
            "code": code,
            "buy_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "buy_price": current_price,
            "sell_date": None,
            "sell_price": None,
            "return_rate": 0.0,
            "status": "HOLD"
        }
        df = pd.concat([df, pd.DataFrame([new_trade])], ignore_index=True)
        self._write(df)
        logger.info(f"[가상매매] {strategy_name}/{code} 매수 기록 (가격: {current_price})")

    def log_sell(self, code: str, current_price):
        """가상 매도 — 해당 종목 가장 최근 HOLD 건."""
        df = self._read()
        mask = (df['code'] == code) & (df['status'] == 'HOLD')
        if df.loc[mask].empty:
            logger.warning(f"[가상매매] {code} 매도 실패: 보유 내역 없음")
            return
        idx = df.loc[mask].index[-1]
        buy_price = df.loc[idx, 'buy_price']
        return_rate = ((current_price - buy_price) / buy_price) * 100 if buy_price else 0
        df.loc[idx, 'sell_date'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        df.loc[idx, 'sell_price'] = current_price
        df.loc[idx, 'return_rate'] = round(return_rate, 2)
        df.loc[idx, 'status'] = 'SOLD'
        self._write(df)
        logger.info(f"[가상매매] {code} 매도 기록 (수익률: {return_rate:.2f}%)")

    def log_sell_by_strategy(self, strategy_name: str, code: str, current_price):
        """전략+종목 매칭 매도."""
        df = self._read()
        mask = (df['strategy'] == strategy_name) & (df['code'] == code) & (df['status'] == 'HOLD')
        if df.loc[mask].empty:
            logger.warning(f"[가상매매] {strategy_name}/{code} 매도 실패: 보유 내역 없음")
            return
        idx = df.loc[mask].index[-1]
        buy_price = df.loc[idx, 'buy_price']
        return_rate = ((current_price - buy_price) / buy_price) * 100 if buy_price else 0
        df.loc[idx, 'sell_date'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        df.loc[idx, 'sell_price'] = current_price
        df.loc[idx, 'return_rate'] = round(return_rate, 2)
        df.loc[idx, 'status'] = 'SOLD'
        self._write(df)
        logger.info(f"[가상매매] {strategy_name}/{code} 매도 기록 (수익률: {return_rate:.2f}%)")

    # ---- 조회 ----

    def get_all_trades(self) -> list:
        """전체 거래 기록 반환 (웹 API용). NaN→None 변환으로 JSON 직렬화 보장."""
        df = self._read()
        records = df.to_dict(orient='records')
        for record in records:
            for key, value in record.items():
                if isinstance(value, float) and math.isnan(value):
                    record[key] = None
        return records

    def get_holds(self) -> list:
        """전체 HOLD 포지션 반환."""
        df = self._read()
        return df.loc[df['status'] == 'HOLD'].to_dict(orient='records')

    def get_holds_by_strategy(self, strategy_name: str) -> list:
        """전략별 HOLD 포지션 반환."""
        df = self._read()
        mask = (df['strategy'] == strategy_name) & (df['status'] == 'HOLD')
        return df.loc[mask].to_dict(orient='records')

    def is_holding(self, strategy_name: str, code: str) -> bool:
        """해당 전략에서 종목 보유 중인지 확인."""
        df = self._read()
        mask = (df['strategy'] == strategy_name) & (df['code'] == code) & (df['status'] == 'HOLD')
        return not df.loc[mask].empty

    def get_summary(self) -> dict:
        """전체 매매 요약 통계."""
        df = self._read()
        sold_df = df[df['status'] == 'SOLD']
        total_trades = len(sold_df)
        win_trades = len(sold_df[sold_df['return_rate'] > 0])
        win_rate = (win_trades / total_trades * 100) if total_trades > 0 else 0
        avg_return = sold_df['return_rate'].mean() if total_trades > 0 else 0
        return {
            "total_trades": total_trades,
            "win_rate": round(win_rate, 1),
            "avg_return": round(avg_return, 2)
        }
