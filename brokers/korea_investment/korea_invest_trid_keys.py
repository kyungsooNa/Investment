from __future__ import annotations
from enum import Enum

class TrIdLeaf(str, Enum):
    # ── 시세(quotations) ─────────────────────────────────────────
    SEARCH_INFO = "search_info"
    INQUIRE_PRICE = "inquire_price"
    MARKET_CAP = "market_cap"
    ASKING_PRICE = "asking_price"
    TIME_CONCLUDE = "time_conclude"
    RANKING_FLUCTUATION = "ranking_fluctuation"
    RANKING_VOLUME = "ranking_volume"
    ETF_INFO = "etf_info"
    MULTI_PRICE = "multi_price"

    DAILY_ITEMCHARTPRICE =      "inquire_daily_itemchartprice"           # 기간별 시세 (일/주/월/년)
    TIME_ITEMCHARTPRICE =       "inquire_time_itemchartprice"             # 당일 분봉 조회
    TIME_DAILY_ITEMCHARTPRICE = "inquire_time_daily_itemchartprice" # 일별 분봉 조회

    # ── 계좌(account) ───────────────────────────────────────────
    INQUIRE_BALANCE_REAL = "inquire_balance_real"
    INQUIRE_BALANCE_PAPER = "inquire_balance_paper"

    # ── 주문(trading) ───────────────────────────────────────────
    ORDER_CASH_BUY_REAL = "order_cash_buy_real"
    ORDER_CASH_BUY_PAPER = "order_cash_buy_paper"
    ORDER_CASH_SELL_REAL = "order_cash_sell_real"
    ORDER_CASH_SELL_PAPER = "order_cash_sell_paper"


class TrId(str, Enum):
    """
    논리 키(모드에 따라 leaf가 달라지는 항목을 하나의 키로 표현)
    """
    # account
    INQUIRE_BALANCE = "inquire_balance"
    # trading
    ORDER_CASH_BUY = "order_cash_buy"
    ORDER_CASH_SELL = "order_cash_sell"
