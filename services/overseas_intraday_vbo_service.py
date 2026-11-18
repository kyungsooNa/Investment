# services/overseas_intraday_vbo_service.py
"""해외 장중 VBO 라이브(paper) 신호·주문 경로.

dry-run(`OverseasVBODryRunService`)은 미국장 마감 후 *완성된* 일봉을 평가하는 사후
백테스트라, 신호 시점엔 이미 진입 기회가 지나 있어 발사 대상이 없다. 본 서비스는
장중 REST 폴링가로 진입/청산을 판정해 "전략이 실제로 도는" 최소 경로를 만든다.
(해외는 웹소켓/분봉이 없어 폴링이 유일한 장중 틱 소스 — `OverseasFavoritePriceAlertTask`
가 쓰는 것과 동일한 패턴.)

  세션 준비 : 후보(거래대금 상위) × 전일Range·당일시가 → target = 당일시가 + K×전일Range
  틱 판정   : 폴링가 >= target → 진입, 폴링가 <= 손절가 → 손절, 마감 전 → EOD 청산

**실주문 잠금은 `OverseasOrderExecutionService` 가 단독으로 관장한다.** 본 서비스는
broker 의존을 갖지 않으며, live_enabled=False 인 주문 서비스를 주입받으면 would-be
주문만 저널에 남는다(Phase 5 이전 기본값).

공통 베이스(`OverseasIntradayStrategyBase`)를 상속한다 — 감시목록·포지션 영속화(P0-1)·
청산 지정가 재시도(P0-4)·리스크 기반 사이징(P1-4)·마켓타이밍 게이트를 다른 장중 전략과
공유한다. 종전 독립 구현은 날이 바뀌면 보유를 지워 EOD 청산 실패 포지션이 조용히
사라졌다.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from common.overseas_types import OverseasExchange
from services.indicator_service import IndicatorService
from services.overseas_intraday_strategy_base import OverseasIntradayStrategyBase


class OverseasIntradayVBOService(OverseasIntradayStrategyBase):
    STRATEGY_NAME = "LarryWilliamsVBO_overseas_intraday"
    STRATEGY_VERSION = "1"
    EVENT_PREFIX = "overseas_intraday_vbo"

    def __init__(
        self,
        candidate_service,
        stock_query_service,
        order_execution_service,
        position_sizing_service=None,
        logger: Optional[logging.Logger] = None,
        *,
        k_value: float = 0.5,
        stop_loss_pct: float = -3.0,
        top_n: int = 20,
        max_positions: int = 5,
        exchange: OverseasExchange = OverseasExchange.NASD,
        market_regime_service=None,
        market_timing_gate: bool = True,
        indicator_service: Optional[IndicatorService] = None,
        macd_filter_enabled: bool = False,
        macd_fast_period: int = 12,
        macd_slow_period: int = 26,
        macd_signal_period: int = 9,
        macd_min_histogram: float = 0.0,
        macd_histogram_rising_bars: int = 2,
        state_file: Optional[str] = None,
        market_clock=None,
        account_equity_provider=None,
    ) -> None:
        super().__init__(
            candidate_service=candidate_service,
            stock_query_service=stock_query_service,
            order_execution_service=order_execution_service,
            market_clock=market_clock,
            position_sizing_service=position_sizing_service,
            market_regime_service=market_regime_service,
            logger=logger,
            top_n=top_n,
            max_positions=max_positions,
            exchange=exchange,
            market_timing_gate=market_timing_gate,
            state_file=state_file,
            account_equity_provider=account_equity_provider,
        )
        self._k = k_value
        self._stop_loss_pct = stop_loss_pct
        self._indicator = indicator_service or IndicatorService()
        self._macd_filter_enabled = macd_filter_enabled
        self._macd_fast_period = macd_fast_period
        self._macd_slow_period = macd_slow_period
        self._macd_signal_period = macd_signal_period
        self._macd_min_histogram = macd_min_histogram
        self._macd_histogram_rising_bars = macd_histogram_rising_bars
        # 베이스의 일봉 조회 개수 — MACD 필터 사용 여부에 따라 달라진다.
        self.HISTORY_LIMIT = self._ohlcv_limit()

    # ── 전략별 구현부 ───────────────────────────────────────────────────

    def _build_setup(self, code, history, today_bar, trade_date) -> Optional[Dict[str, Any]]:
        """당일 봉이 없는 종목은 시가를 알 수 없으므로 제외한다(fail-closed)."""
        if not today_bar or not history:
            return None
        prev = history[-1]
        prev_range = self._f(prev.get("high")) - self._f(prev.get("low"))
        open_ = self._f(today_bar.get("open"))
        if prev_range <= 0 or open_ <= 0:
            return None
        macd_metrics = self._evaluate_macd_filter(history)
        if macd_metrics is None:
            return None
        setup = {
            "open": open_,
            "prev_range": prev_range,
            "target": open_ + self._k * prev_range,
        }
        if macd_metrics:
            setup.update(macd_metrics)
        return setup

    def _should_enter(self, setup, price, volume, now) -> bool:
        return price >= setup["target"]

    def _stop_price(self, setup, price) -> float:
        return price * (1 + self._stop_loss_pct / 100.0)

    def _entry_reason(self) -> str:
        return "vbo_intraday_breakout"

    def _signal_extras(self, setup, price) -> Dict[str, Any]:
        extras = {"target": setup["target"], "prev_range": setup["prev_range"]}
        if setup.get("macd_filter_enabled"):
            extras.update({
                "macd_filter_enabled": True,
                "macd": setup.get("macd"),
                "macd_signal": setup.get("macd_signal"),
                "macd_histogram": setup.get("macd_histogram"),
            })
        return extras

    # ── 보조 ────────────────────────────────────────────────────────────

    def _evaluate_macd_filter(self, confirmed_rows: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not self._macd_filter_enabled:
            return {}
        metrics = self._indicator.calc_macd_sync(
            confirmed_rows,
            fast_period=self._macd_fast_period,
            slow_period=self._macd_slow_period,
            signal_period=self._macd_signal_period,
        )
        if not metrics:
            return None
        histogram = metrics.get("histogram")
        if histogram is None or histogram < self._macd_min_histogram:
            return None
        if self._macd_histogram_rising_bars > 0 and not self._histogram_is_rising(confirmed_rows):
            return None
        return {
            "macd_filter_enabled": True,
            "macd": metrics.get("macd"),
            "macd_signal": metrics.get("signal"),
            "macd_histogram": histogram,
        }

    def _histogram_is_rising(self, rows: List[Dict[str, Any]]) -> bool:
        needed = self._macd_histogram_rising_bars + 1
        min_len = self._macd_slow_period + self._macd_signal_period - 1 + self._macd_histogram_rising_bars
        if len(rows) < min_len:
            return False
        values = []
        for i in range(needed, 0, -1):
            metrics = self._indicator.calc_macd_sync(
                rows[:-i + 1] if i > 1 else rows,
                fast_period=self._macd_fast_period,
                slow_period=self._macd_slow_period,
                signal_period=self._macd_signal_period,
            )
            histogram = metrics.get("histogram") if metrics else None
            if histogram is None:
                return False
            values.append(histogram)
        return all(cur > prev for prev, cur in zip(values, values[1:]))

    def _ohlcv_limit(self) -> int:
        if not self._macd_filter_enabled:
            return 2
        return max(
            2,
            self._macd_slow_period + self._macd_signal_period + self._macd_histogram_rising_bars,
        )
