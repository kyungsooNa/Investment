from __future__ import annotations
from enum import Enum

class EndpointKey(str, Enum):
    # ── 시세 ─────────────────────────────
    SEARCH_INFO = "search_info"
    INQUIRE_PRICE = "inquire_price"
    MARKET_CAP = "market_cap"                 # ← YAML이 top_market_cap이면 값만 바꿔주세요
    ASKING_PRICE = "asking_price"
    TIME_CONCLUDE = "time_conclude"
    RANKING_FLUCTUATION = "ranking_fluctuation"             # ← YAML이 ranking_fluctuation이면 값만 바꿔주세요
    RANKING_VOLUME = "ranking_volume"
    ETF_INFO = "etf_info"
    MULTI_PRICE = "multi_price"
    DAILY_ITEMCHARTPRICE = "inquire_daily_itemchartprice"
    TIME_ITEMCHARTPRICE = "inquire_time_itemchartprice"
    TIME_DAILY_ITEMCHARTPRICE = "inquire_time_daily_itemchartprice"
    FINANCIAL_RATIO = "financial_ratio"
    INQUIRE_CONCLUSION = "inquire_conclusion"
    INVESTOR_TRADE_BY_STOCK_DAILY = "investor_trade_by_stock_daily"
    PROGRAM_TRADE_BY_STOCK_DAILY = "program_trade_by_stock_daily"
    CHK_HOLIDAY = "/uapi/domestic-stock/v1/quotations/chk-holiday"

    # ── 계좌/주문 ────────────────────────
    INQUIRE_BALANCE = "inquire_balance"
    INQUIRE_DAILY_CCLD = "inquire_daily_ccld"
    INQUIRE_PSBL_RVSECNCL = "inquire_psbl_rvsecncl"
    ORDER_CASH = "order_cash"
    ORDER_RVSECNCL = "order_rvsecncl"
    HASHKEY = "hashkey"                       # YAML에 없으면 리터럴(/uapi/hashkey) 써도 됨

    # ── 해외주식 v1 ─────────────────────
    OVERSEAS_STOCK_PRICE = "overseas_stock_price"
    OVERSEAS_STOCK_DAILYPRICE = "overseas_stock_dailyprice"
    OVERSEAS_STOCK_INQUIRE_BALANCE = "overseas_stock_inquire_balance"
    OVERSEAS_STOCK_INQUIRE_CCNL = "overseas_stock_inquire_ccnl"
    OVERSEAS_STOCK_INQUIRE_NCCS = "overseas_stock_inquire_nccs"
    OVERSEAS_STOCK_ORDER = "overseas_stock_order"
    OVERSEAS_STOCK_ORDER_RVSECNCL = "overseas_stock_order_rvsecncl"
