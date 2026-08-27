"""해외 장중 RSI2 Pullback (라이브 paper 경로).

RSI2 는 거래량을 쓰지 않고 **종가**만으로 판정한다. 장중에는 종가가 없으므로
마감 직전 구간(기본 마감 15분 전부터)의 폴링가를 종가 대용으로 삼아 판정한다.
그 이전 틱은 무시한다 — 장중 가격으로 RSI 를 돌리면 종가 기준 신호와 달라진다.

Minervini Stage 2 대체로 `close > 200MA` 를 쓰는 것은 dry-run 과 동일하다.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Dict, List, Optional

from common.overseas_types import OverseasExchange
from services.overseas_intraday_strategy_base import OverseasIntradayStrategyBase


@dataclass
class OverseasIntradayRSI2Config:
    rsi_period: int = 2
    rsi_threshold: float = 10.0
    trend_ma_period: int = 200
    hard_stop_pct: float = -5.0
    min_history_days: int = 202
    # 마감 N분 전부터 폴링가를 종가 대용으로 사용한다.
    close_proxy_window_min: int = 15


class OverseasIntradayRSI2Service(OverseasIntradayStrategyBase):
    STRATEGY_NAME = "RSI2Pullback_overseas_intraday"
    EVENT_PREFIX = "overseas_intraday_rsi2"
    HISTORY_LIMIT = 210

    def __init__(self, *args, config: Optional[OverseasIntradayRSI2Config] = None,
                 us_market_calendar_service=None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._cfg = config or OverseasIntradayRSI2Config()
        self._calendar = us_market_calendar_service

    def _build_setup(self, code, history, today_bar, trade_date) -> Optional[Dict[str, Any]]:
        closes = [self._f(r.get("close")) for r in history]
        if len(closes) < self._cfg.min_history_days - 1 or any(v <= 0 for v in closes[-200:]):
            return None
        return {"closes": closes[-(self._cfg.trend_ma_period + self._cfg.rsi_period):]}

    def _is_close_window(self, now, trade_date: str) -> bool:
        """마감 직전 구간인지. 캘린더가 있으면 조기폐장(13:00 ET)을 반영한다."""
        if now is None:
            return False
        close_str = "16:00"
        if self._calendar is not None:
            try:
                close_str = self._calendar.get_close_time_str(trade_date) or "16:00"
            except Exception:
                close_str = "16:00"
        hh, _, mm = close_str.partition(":")
        try:
            close_t = now.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
        except (TypeError, ValueError):
            return False
        return now >= close_t - timedelta(minutes=self._cfg.close_proxy_window_min)

    def _should_enter(self, setup, price, volume, now) -> bool:
        if not self._is_close_window(now, self._session_date or ""):
            return False
        # 폴링가를 당일 종가로 가정해 히스토리에 붙인다.
        closes = list(setup["closes"]) + [price]
        ma = sum(closes[-self._cfg.trend_ma_period:]) / self._cfg.trend_ma_period
        if price <= ma:
            return False
        rsi = self._rsi(closes, self._cfg.rsi_period)
        return rsi is not None and rsi <= self._cfg.rsi_threshold

    def _stop_price(self, setup, price) -> float:
        return price * (1 + self._cfg.hard_stop_pct / 100.0)

    def _entry_reason(self) -> str:
        return "rsi2_intraday_pullback"

    @staticmethod
    def _rsi(closes: List[float], period: int) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        gains, losses = 0.0, 0.0
        for i in range(len(closes) - period, len(closes)):
            diff = closes[i] - closes[i - 1]
            if diff >= 0:
                gains += diff
            else:
                losses -= diff
        avg_gain, avg_loss = gains / period, losses / period
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))
