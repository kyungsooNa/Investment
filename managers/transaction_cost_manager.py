class TransactionCostManager:
    """거래 비용(수수료 및 세금) 관리 클래스"""
    # 수수료율 (KRX/NXT 구분 없이 기본값 적용: 0.0140527%)
    FEE_RATE = 0.000140527
    # 증권거래세 + 농어촌특별세 (0.20%)
    TAX_RATE = 0.002

    @classmethod
    def calculate_cost(cls, price: float, qty: float, is_sell: bool = False) -> float:
        """거래 비용 계산 (매수: 수수료, 매도: 수수료 + 세금)"""
        amount = price * qty
        fee = amount * cls.FEE_RATE
        tax = amount * cls.TAX_RATE if is_sell else 0.0
        return fee + tax

    @classmethod
    def get_return_rate(cls, buy_price: float, sell_price: float, qty: float = 1, apply_cost: bool = False) -> float:
        """수익률 계산 (비용 적용 옵션)"""
        if buy_price == 0:
            return 0.0

        if not apply_cost:
            return ((sell_price - buy_price) / buy_price) * 100

        buy_cost = cls.calculate_cost(buy_price, qty, is_sell=False)
        sell_cost = cls.calculate_cost(sell_price, qty, is_sell=True)
        
        total_invest = (buy_price * qty) + buy_cost
        total_retrieve = (sell_price * qty) - sell_cost
        
        return ((total_retrieve - total_invest) / total_invest) * 100