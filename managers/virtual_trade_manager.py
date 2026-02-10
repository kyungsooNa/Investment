# managers/virtual_trade_manager.py (신규 생성)
import pandas as pd
import os
from datetime import datetime

class VirtualTradeManager:
    def __init__(self, filename="trade_journal.csv"):
        self.filename = filename
        # 파일이 없으면 헤더 생성
        if not os.path.exists(self.filename):
            df = pd.DataFrame(columns=["strategy", "code", "buy_date", "buy_price", "sell_date", "sell_price", "return_rate", "status"])
            df.to_csv(self.filename, index=False)

    def log_buy(self, strategy_name, code, current_price):
        """가상 매수 기록"""
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
        df = pd.read_csv(self.filename)
        # 이미 보유 중인지 체크 (중복 매수 방지 로직 필요 시 추가)
        df = pd.concat([df, pd.DataFrame([new_trade])], ignore_index=True)
        df.to_csv(self.filename, index=False)
        print(f"[가상매매] {code} 매수 기록 완료 (가격: {current_price})")

    def log_sell(self, code, current_price):
        """가상 매도 기록 및 수익률 계산"""
        df = pd.read_csv(self.filename)
        
        # 해당 종목의 'HOLD' 상태인 가장 최근 기록 찾기
        mask = (df['code'] == code) & (df['status'] == 'HOLD')
        if not df.loc[mask].empty:
            idx = df.loc[mask].index[-1] # 가장 최근 매수 건
            buy_price = df.loc[idx, 'buy_price']
            
            # 수익률 계산: (매도 - 매수) / 매수 * 100
            return_rate = ((current_price - buy_price) / buy_price) * 100
            
            # 업데이트
            df.loc[idx, 'sell_date'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            df.loc[idx, 'sell_price'] = current_price
            df.loc[idx, 'return_rate'] = round(return_rate, 2)
            df.loc[idx, 'status'] = 'SOLD'
            
            df.to_csv(self.filename, index=False)
            print(f"[가상매매] {code} 매도 기록 완료 (수익률: {return_rate:.2f}%)")
        else:
            print(f"[가상매매] {code} 매도 실패: 보유 중인 내역이 없습니다.")

    def get_summary(self):
            df = pd.read_csv(self.filename)
            sold_df = df[df['status'] == 'SOLD']
            
            total_trades = len(sold_df)
            win_trades = len(sold_df[sold_df['return_rate'] > 0])
            win_rate = (win_trades / total_trades * 100) if total_trades > 0 else 0
            avg_return = sold_df['return_rate'].mean()
            
            print(f"=== {self.filename} 분석 결과 ===")
            print(f"총 거래 수: {total_trades}회")
            print(f"승률: {win_rate:.1f}%")
            print(f"평균 수익률: {avg_return:.2f}%")
