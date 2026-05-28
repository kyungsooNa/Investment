class TransactionCostUtils:
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
    def calculate_net_pnl_won(cls, buy_price: float, sell_price: float, qty: int) -> int:
        """수수료/거래세 반영 후 순수익 (KRW 정수). 음수 = 손실."""
        buy_cost = cls.calculate_cost(buy_price, qty, is_sell=False)
        sell_cost = cls.calculate_cost(sell_price, qty, is_sell=True)
        net = (sell_price * qty - sell_cost) - (buy_price * qty + buy_cost)
        return int(net)

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

    @classmethod
    def net_return_pct(cls, buy_price: float, sell_price: float) -> float:
        """수수료/세금 반영 후 net 수익률 (%).

        P0 0-9: 라이브 stop/take_profit trigger 가 backtest 와 동일하게 net 기준으로
        평가되도록 한다. qty 와 무관 (비율은 1주 기준과 동일).

        Returns:
            buy_price == 0 이면 0.0 (분모 보호).
        """
        return cls.get_return_rate(buy_price, sell_price, qty=1, apply_cost=True)