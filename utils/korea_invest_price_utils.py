# utils/korea_invest_price_utils.py


def get_tick_size(price: int) -> int:
    """KRX 공식 호가단위 반환 (KOSPI/KOSDAQ 공통)"""
    if price < 2_000:
        return 1
    if price < 5_000:
        return 5
    if price < 20_000:
        return 10
    if price < 50_000:
        return 50
    if price < 200_000:
        return 100
    if price < 500_000:
        return 500
    return 1_000


def adjust_price(price: int) -> int:
    """가격을 KRX 호가단위에 맞게 내림 보정.

    시장가 주문(price=0 또는 음수)은 그대로 반환.
    """
    if price <= 0:
        return price
    tick = get_tick_size(price)
    return (price // tick) * tick
