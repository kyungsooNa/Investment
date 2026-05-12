"""Market snapshot dataclass contracts.

WebSocket / REST / backtest replay 세 경로에서 동일 타입으로 정규화.
- MarketSnapshot: 현재가·거래량·거래대금·고가·저가·시가·데이터 시각
- ConclusionSnapshot: 체결강도 (별도 API 경로)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class MarketSnapshot:
    """시세 snapshot contract.

    source 필드:
      "websocket"       — WebSocket 체결가 틱 수신
      "rest"            — REST 응답을 backfill
      "backtest_replay" — 백테스트 재현 replay
    high/low/open 은 WebSocket 틱에서만 채워진다. REST backfill 경로에서는 None.
    """

    code: str
    price: float
    change: float
    rate: float
    sign: str  # "1"~"5" (상한/상승/보합/하락/하한)
    acml_vol: int
    acml_tr_pbmn: int
    high: Optional[float]
    low: Optional[float]
    open: Optional[float]
    received_at: float  # epoch seconds
    latency_sec: float
    quality_status: str  # "ok" | severity
    quality_reason: str  # "websocket" | "rest_snapshot" | etc.
    source: str

    def to_legacy_dict(self) -> dict:
        """기존 PriceStreamService._latest_prices dict 포맷으로 변환."""
        return {
            "price": str(self.price),
            "change": str(self.change),
            "rate": str(self.rate),
            "sign": self.sign,
            "acml_vol": self.acml_vol,
            "acml_tr_pbmn": self.acml_tr_pbmn,
            "high": self.high,
            "low": self.low,
            "open": self.open,
            "received_at": self.received_at,
            "latency_sec": self.latency_sec,
            "quality_status": self.quality_status,
            "quality_reason": self.quality_reason,
        }

    @classmethod
    def from_legacy_dict(cls, code: str, d: dict, source: str = "websocket") -> "MarketSnapshot":
        """PriceStreamService._latest_prices dict → MarketSnapshot."""

        def _float(v, default: float = 0.0) -> float:
            try:
                return float(v) if v is not None and v != "N/A" else default
            except (ValueError, TypeError):
                return default

        def _int(v, default: int = 0) -> int:
            try:
                return int(v) if v is not None and v != "N/A" else default
            except (ValueError, TypeError):
                return default

        return cls(
            code=code,
            price=_float(d.get("price")),
            change=_float(d.get("change")),
            rate=_float(d.get("rate")),
            sign=str(d.get("sign") or "3"),
            acml_vol=_int(d.get("acml_vol")),
            acml_tr_pbmn=_int(d.get("acml_tr_pbmn")),
            high=_float(d["high"]) if d.get("high") is not None else None,
            low=_float(d["low"]) if d.get("low") is not None else None,
            open=_float(d["open"]) if d.get("open") is not None else None,
            received_at=_float(d.get("received_at")),
            latency_sec=_float(d.get("latency_sec")),
            quality_status=str(d.get("quality_status") or "ok"),
            quality_reason=str(d.get("quality_reason") or ""),
            source=source,
        )


@dataclass(frozen=True)
class ConclusionSnapshot:
    """체결강도 snapshot contract.

    source 필드:
      "rest"            — REST get_stock_conclusion API 결과
      "backtest_replay" — 백테스트 재현 replay
    """

    code: str
    execution_strength_pct: float  # cgld / tday_rltv
    received_at: float
    source: str
