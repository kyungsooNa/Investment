from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


MarketMode = Literal["domestic", "overseas_us"]


class OverseasExchange(str, Enum):
    NASD = "NASD"
    NYSE = "NYSE"
    AMEX = "AMEX"


class OverseasPriceSummary(BaseModel):
    symbol: str
    exchange: OverseasExchange
    currency: str = "USD"
    price: float = 0.0
    change_rate: float = 0.0
    volume: int = 0
    timestamp: str = ""
    raw: dict = Field(default_factory=dict)

    def to_dict(self):
        return self.model_dump()


class OverseasOrderRequest(BaseModel):
    symbol: str
    exchange: OverseasExchange
    side: Literal["buy", "sell"]
    qty: int
    limit_price: float
    currency: Literal["USD"] = "USD"
    real_order_confirmation: str | None = None

    def to_dict(self):
        return self.model_dump()


class OverseasOrderReport(BaseModel):
    symbol: str
    exchange: OverseasExchange
    side: Literal["buy", "sell"]
    qty: int
    limit_price: str
    broker_order_no: str = ""
    raw: dict = Field(default_factory=dict)

    def to_dict(self):
        return self.model_dump()
