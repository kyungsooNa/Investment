from __future__ import annotations
from enum import Enum

class EndpointKey(str, Enum):
    # ── 시세 ─────────────────────────────
    SEARCH_INFO = "search_info"
    INQUIRE_PRICE = "inquire_price"
    MARKET_CAP = "market_cap"                 # ← YAML이 top_market_cap이면 값만 바꿔주세요
    INQUIRE_DAILY_ITEMCHARTPRICE = "inquire_daily_itemchartprice"
    ASKING_PRICE = "asking_price"
    TIME_CONCLUDE = "time_conclude"
    RANKING_FLUCTUATION = "ranking_fluctuation"             # ← YAML이 ranking_fluctuation이면 값만 바꿔주세요
    RANKING_VOLUME = "ranking_volume"
    ETF_INFO = "etf_info"

    # ── 계좌/주문 ────────────────────────
    INQUIRE_BALANCE = "inquire_balance"
    ORDER_CASH = "order_cash"
    HASHKEY = "hashkey"                       # YAML에 없으면 리터럴(/uapi/hashkey) 써도 됨
