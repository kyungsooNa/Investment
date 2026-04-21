# core/pnl_utils.py
TAX_AND_FEE_PCT = 0.26  # 매도세(0.23%) + 수수료(~0.015%×2)


def calc_net_pnl(buy_price: float, current_price: float) -> float:
    """세금·수수료 차감 후 실질 손익률 (%)."""
    gross_pnl = (current_price - buy_price) / buy_price * 100
    return gross_pnl - TAX_AND_FEE_PCT
