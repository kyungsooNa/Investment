"""Replay adapters for period backtests."""
from __future__ import annotations

from typing import Any, Sequence

from common.types import TradeSignal
from services.backtest_execution_simulator import BacktestBar


class StockQueryIntradayReplayBarProvider:
    """Convert StockQueryService intraday minute rows into BacktestBar.

    The provider chooses the first minute bar that can touch a signal's limit
    price. If no bar reaches the price, it returns the last valid bar so the
    execution simulator can produce an unfilled report.
    """

    def __init__(self, stock_query_service: Any, *, session: str = "REGULAR") -> None:
        self._stock_query_service = stock_query_service
        self._session = session
        self._cache: dict[tuple[str, str, str], list[BacktestBar]] = {}

    async def get_bar(self, *, signal: TradeSignal, date_ymd: str, side: str) -> BacktestBar:
        bars = await self._get_bars(signal.code, date_ymd)
        if not bars:
            raise ValueError(f"intraday rows not found: {signal.code} {date_ymd}")

        target_price = float(signal.price or 0)
        normalized_side = str(side or signal.action or "").upper()
        for bar in bars:
            if self._price_reached(bar, target_price, normalized_side):
                return bar
        return bars[-1]

    async def _get_bars(self, code: str, date_ymd: str) -> list[BacktestBar]:
        key = (code, date_ymd, self._session)
        if key in self._cache:
            return self._cache[key]

        rows = await self._stock_query_service.get_day_intraday_minutes_list(
            code,
            date_ymd=date_ymd,
            session=self._session,
        )
        if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
            rows = []

        bars = [
            bar for bar in (self._row_to_bar(row, default_date=date_ymd) for row in rows)
            if bar is not None
        ]
        bars.sort(key=lambda bar: bar.timestamp)
        self._cache[key] = bars
        return bars

    def _row_to_bar(self, row: Any, *, default_date: str) -> BacktestBar | None:
        if not isinstance(row, dict):
            return None

        close = self._to_float(self._first(row, "stck_prpr", "prpr", "close", "price"))
        if close is None:
            return None

        open_price = self._to_float(self._first(row, "stck_oprc", "oprc", "open")) or close
        high = self._to_float(self._first(row, "stck_hgpr", "hgpr", "high")) or max(open_price, close)
        low = self._to_float(self._first(row, "stck_lwpr", "lwpr", "low")) or min(open_price, close)
        volume = self._to_int(self._first(row, "cntg_vol", "acml_vol", "volume"))
        date = str(self._first(row, "stck_bsop_date", "bsop_date", "date") or default_date)
        time = str(self._first(row, "stck_cntg_hour", "cntg_hour", "time") or "000000").zfill(6)

        return BacktestBar(
            timestamp=f"{date} {time}",
            open=open_price,
            high=high,
            low=low,
            close=close,
            volume=volume,
        )

    @staticmethod
    def _price_reached(bar: BacktestBar, target_price: float, side: str) -> bool:
        if target_price <= 0:
            return False
        if side == "BUY":
            return bar.low <= target_price <= bar.high
        if side == "SELL":
            return bar.low <= target_price <= bar.high
        return bar.low <= target_price <= bar.high

    @staticmethod
    def _first(row: dict, *keys: str):
        for key in keys:
            value = row.get(key)
            if value not in (None, "", "-"):
                return value
        return None

    @staticmethod
    def _to_float(value) -> float | None:
        try:
            if value in (None, "", "-"):
                return None
            return float(str(value).replace(",", ""))
        except (TypeError, ValueError):
            return None

    @classmethod
    def _to_int(cls, value) -> int | None:
        result = cls._to_float(value)
        return int(result) if result is not None else None
