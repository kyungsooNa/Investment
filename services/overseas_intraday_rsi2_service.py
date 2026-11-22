"""해외 장중 RSI2 Pullback (라이브 paper 경로).

RSI2 는 거래량을 쓰지 않고 **종가**만으로 판정한다. 장중에는 종가가 없으므로
마감 직전 구간(기본 마감 15분 전부터)의 폴링가를 종가 대용으로 삼아 판정한다.
그 이전 틱은 무시한다 — 장중 가격으로 RSI 를 돌리면 종가 기준 신호와 달라진다.

Minervini Stage 2 대체로 `close > 추세MA` 를 쓰는 것은 dry-run 과 동일하다.
추세MA 200 을 채우려면 `MarketDataService` 의 해외 일봉 분할 수집이 필요하다
(1회 응답은 ~100봉). 그래서 HISTORY_LIMIT 이 한 페이지를 넘는다 — 세션 준비에서
심볼당 여러 번 조회한다는 뜻이므로 top_n 을 키울 때 예산을 함께 봐야 한다.

**멀티데이 보유 (v2)**: 진입 창(마감 15분 전)이 태스크의 EOD 청산 창(마감 10분 전)과
붙어 있어 v1 은 최대 5분 보유 후 청산됐다 — 장중 표본이 구조적으로 '0 − 비용' 이라
엣지 근거(dry-run 멀티데이 재구성)를 검증할 수 없었다. v2 는 EOD 청산에서 빠지고
`scripts/analyze_overseas_dryrun.py::reconstruct_multiday_exit` 와 같은 규칙으로
청산한다: hard stop → trailing(고점 대비, trail 이 hard stop 위일 때만) → time stop
(진입 후 N 거래일, 마감 창의 폴링가). 보유는 `state_file` 로 날을 넘긴다.
"""
from __future__ import annotations

import logging
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta
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
    # 멀티데이 청산 — 분석기 재구성 기본값(--trailing-stop-pct / --time-stop-days)과 같다.
    trailing_stop_pct: float = 8.0
    time_stop_days: int = 20


class OverseasIntradayRSI2Service(OverseasIntradayStrategyBase):
    STRATEGY_NAME = "RSI2Pullback_overseas_intraday"
    STRATEGY_VERSION = "2"
    EVENT_PREFIX = "overseas_intraday_rsi2"
    HISTORY_LIMIT = 210
    EOD_EXIT = False

    def __init__(self, *args, config: Optional[OverseasIntradayRSI2Config] = None,
                 us_market_calendar_service=None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._cfg = config or OverseasIntradayRSI2Config()
        self._calendar = us_market_calendar_service

    def _build_setup(self, code, history, today_bar, trade_date) -> Optional[Dict[str, Any]]:
        closes = [self._f(r.get("close")) for r in history]
        if len(closes) < self._cfg.min_history_days - 1 or any(
                v <= 0 for v in closes[-self._cfg.trend_ma_period:]):
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

    def _held_exit_reason(self, held, price, now) -> Optional[str]:
        peak = max(self._f(held.get("peak_price")), price)
        if peak > self._f(held.get("peak_price")):
            held["peak_price"] = peak
            self._persist_state()
        if self._cfg.trailing_stop_pct:
            trail = peak * (1 - self._cfg.trailing_stop_pct / 100.0)
            if trail > held["stop_price"] and price <= trail:
                return "trailing"
        if (self._cfg.time_stop_days
                and self._is_close_window(now, self._session_date or "")
                and self._trading_days_held(held) >= self._cfg.time_stop_days):
            return "time"
        return None

    def _trading_days_held(self, held) -> int:
        """진입일 다음 날부터 오늘까지의 거래일 수(진입일=0, 분석기와 같은 기준)."""
        try:
            start = datetime.strptime(str(held.get("entry_date") or ""), "%Y%m%d").date()
            end = datetime.strptime(str(self._session_date or ""), "%Y%m%d").date()
        except ValueError:
            return 0
        days, d = 0, start
        while d < end:
            d += timedelta(days=1)
            ymd = d.strftime("%Y%m%d")
            if self._calendar is not None:
                if self._calendar.is_trading_day(ymd):
                    days += 1
            elif d.weekday() < 5:
                days += 1
        return days

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


class OverseasIntradayRSI2ShadowService(OverseasIntradayRSI2Service):
    """동일 후보·틱으로 국면 게이트만 제외한 독립 Paper 관찰 경로."""

    STRATEGY_NAME = "RSI2Pullback_overseas_intraday_no_regime"
    EVENT_PREFIX = "overseas_intraday_rsi2_shadow"

    def __init__(self, *, primary_service: OverseasIntradayRSI2Service, **kwargs) -> None:
        kwargs["market_timing_gate"] = False
        super().__init__(**kwargs)
        self._primary_service = primary_service

    async def prepare_session(self, trade_date: str, exchange=None) -> int:
        if self._session_date == trade_date and self._state_loaded and self._watch:
            return len(self._watch)
        if self._restored_date != trade_date:
            await self._restore_state(trade_date)
            self._restored_date = trade_date
        if self._primary_service._session_date != trade_date:
            return 0
        self._session_date = trade_date
        self._watch = deepcopy(self._primary_service._watch)
        self._persist_state()
        return len(self._watch)
