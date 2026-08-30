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

한계: 포지션 상태는 in-memory 다. 장중 재시작 시 보유가 소실돼 EOD 청산 기록이
누락될 수 있다(paper 경로라 실포지션 영향은 없음).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from common.overseas_types import OverseasExchange
from common.types import ErrorCode
from services.indicator_service import IndicatorService


class OverseasIntradayVBOService:
    STRATEGY_NAME = "LarryWilliamsVBO_overseas_intraday"
    MARKET = "US"

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
    ) -> None:
        self._candidate_service = candidate_service
        self._sqs = stock_query_service
        self._orders = order_execution_service
        self._sizing_service = position_sizing_service
        self._logger = logger or logging.getLogger(__name__)
        self._k = k_value
        self._stop_loss_pct = stop_loss_pct
        self._top_n = top_n
        self._max_positions = max_positions
        self._default_exchange = exchange
        # 미국장 국면 게이트. 미주입이면 게이트 없이 기존 동작을 유지한다.
        self._regime = market_regime_service
        self._market_timing_gate = market_timing_gate
        self._indicator = indicator_service or IndicatorService()
        self._macd_filter_enabled = macd_filter_enabled
        self._macd_fast_period = macd_fast_period
        self._macd_slow_period = macd_slow_period
        self._macd_signal_period = macd_signal_period
        self._macd_min_histogram = macd_min_histogram
        self._macd_histogram_rising_bars = macd_histogram_rising_bars

        self._session_date: Optional[str] = None
        self._watch: Dict[str, Dict[str, Any]] = {}
        self._positions: Dict[str, Dict[str, Any]] = {}
        self._entered_today: set[str] = set()

    # ── 세션 ────────────────────────────────────────────────────────────

    async def prepare_session(
        self,
        trade_date: str,
        exchange: Optional[OverseasExchange] = None,
    ) -> int:
        """당일 감시 목록(종목별 target)을 만든다. 반환: 감시 종목 수.

        당일 봉이 아직 없는 종목은 시가를 알 수 없으므로 감시에서 제외한다
        (fail-closed — 추정 시가로 진입하지 않는다). 같은 거래일에 두 번 호출되면
        두 번째부터는 no-op 이다.
        """
        if self._session_date == trade_date:
            return len(self._watch)

        ex = exchange or self._default_exchange
        candidates = await self._candidate_service.get_candidates(ex, top_n=self._top_n)

        watch: Dict[str, Dict[str, Any]] = {}
        for cand in candidates or []:
            code = cand.get("code")
            if not code:
                continue
            setup = await self._build_setup(code, trade_date, ex)
            if setup:
                watch[code] = setup

        self._session_date = trade_date
        self._watch = watch
        self._positions = {}
        self._entered_today = set()
        self._logger.info({
            "event": "overseas_intraday_vbo_session", "trade_date": trade_date,
            "exchange": ex.value, "candidates": len(candidates or []), "watch": len(watch),
        })
        return len(watch)

    async def _build_setup(
        self, code: str, trade_date: str, exchange: OverseasExchange,
    ) -> Optional[Dict[str, Any]]:
        try:
            resp = await self._sqs.get_recent_daily_ohlcv(code, limit=self._ohlcv_limit(), exchange=exchange)
        except Exception as e:
            self._logger.warning({"event": "overseas_intraday_vbo_ohlcv_error",
                                  "code": code, "error": str(e)})
            return None
        if not resp or resp.rt_cd != ErrorCode.SUCCESS.value or not resp.data:
            return None
        rows = resp.data
        if len(rows) < 2:
            return None
        prev, cur = rows[-2], rows[-1]
        if str(cur.get("date") or "") != str(trade_date):
            return None  # 당일 봉 미생성 → 시가 미확정
        prev_range = self._f(prev.get("high")) - self._f(prev.get("low"))
        open_ = self._f(cur.get("open"))
        if prev_range <= 0 or open_ <= 0:
            return None
        macd_metrics = self._evaluate_macd_filter(rows[:-1])
        if macd_metrics is None:
            return None
        setup = {
            "open": open_,
            "prev_range": prev_range,
            "target": open_ + self._k * prev_range,
            "exchange": exchange,
        }
        if macd_metrics:
            setup.update(macd_metrics)
        return setup

    def watch_codes(self) -> List[str]:
        return list(self._watch.keys())

    def get_state(self) -> Dict[str, Any]:
        return {
            "session_date": self._session_date,
            "watch": self._watch,
            "positions": self._positions,
            "entered_today": sorted(self._entered_today),
        }

    # ── 틱 판정 ─────────────────────────────────────────────────────────

    async def on_price(self, code: str, price: float, *, volume: Optional[float] = None) -> Optional[Dict[str, Any]]:
        """폴링가 1건을 판정해 주문까지 수행한다. 주문이 없으면 None."""
        px = self._f(price)
        if px <= 0:
            return None

        held = self._positions.get(code)
        if held is not None:
            held["last_price"] = px
            if px <= held["stop_price"]:
                return await self._exit(code, px, reason="stop")
            return None

        setup = self._watch.get(code)
        if setup is None or code in self._entered_today:
            return None
        if px < setup["target"]:
            return None
        if not await self._is_regime_ok(code):
            return None
        if len(self._positions) >= self._max_positions:
            self._logger.info({"event": "overseas_intraday_vbo_entry_blocked", "code": code,
                               "reason": "max_positions", "max_positions": self._max_positions})
            return None
        return await self._enter(code, px, setup)

    async def _is_regime_ok(self, code: str) -> bool:
        """미국장 국면이 신규 진입에 적합한지 판정한다.

        돌파가 확인된 뒤에만 호출된다(틱마다 판정을 돌리지 않기 위함). 국면 판정은
        `USMarketRegimeService` 가 거래일 단위로 캐시하므로 하루 1회만 조회가 나간다.
        조회 실패는 **fail-closed** — 국면을 모르는 채로 발사하지 않는다.
        청산(손절/EOD)은 이 게이트를 거치지 않는다.
        """
        if self._regime is None or not self._market_timing_gate:
            return True
        try:
            snap = await self._regime.classify(self.MARKET)
        except Exception as e:
            self._logger.warning({"event": "overseas_intraday_vbo_regime_error",
                                  "code": code, "error": str(e)})
            return False
        if snap.is_rising:
            return True
        self._logger.info({
            "event": "overseas_intraday_vbo_entry_blocked", "code": code,
            "reason": "market_timing", "regime": snap.regime_label,
            "fail_detail": snap.fail_detail,
        })
        return False

    async def _enter(self, code: str, price: float, setup: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        qty = self._resolve_qty(price)
        if qty <= 0:
            return None
        stop_price = price * (1 + self._stop_loss_pct / 100.0)
        signal = {
            "strategy": self.STRATEGY_NAME,
            "code": code,
            "action": "BUY",
            "date": self._session_date,
            "entry_price": price,
            "target": setup["target"],
            "stop_price": stop_price,
            "prev_range": setup["prev_range"],
            "reason": "vbo_intraday_breakout",
        }
        if setup.get("macd_filter_enabled"):
            signal.update({
                "macd_filter_enabled": True,
                "macd": setup.get("macd"),
                "macd_signal": setup.get("macd_signal"),
                "macd_histogram": setup.get("macd_histogram"),
            })
        resp = await self._orders.place_entry(
            code=code, qty=qty, limit_price=price,
            exchange=setup["exchange"], signal=signal,
        )
        if getattr(resp, "rt_cd", None) != ErrorCode.SUCCESS.value:
            self._logger.warning({"event": "overseas_intraday_vbo_entry_rejected", "code": code,
                                  "msg": getattr(resp, "msg1", "")})
            return None
        self._positions[code] = {
            "qty": qty, "entry_price": price, "stop_price": stop_price,
            "last_price": price, "exchange": setup["exchange"],
        }
        self._entered_today.add(code)
        return signal

    async def _exit(self, code: str, price: float, *, reason: str) -> Optional[Dict[str, Any]]:
        held = self._positions.get(code)
        if held is None:
            return None
        signal = {
            "strategy": self.STRATEGY_NAME,
            "code": code,
            "action": "SELL",
            "date": self._session_date,
            "entry_price": held["entry_price"],
            "exit_price": price,
            "exit_reason": reason,
            "realized_pct": (price / held["entry_price"] - 1) * 100.0 if held["entry_price"] > 0 else 0.0,
        }
        resp = await self._orders.place_exit(
            code=code, qty=held["qty"], limit_price=price, reason=reason,
            exchange=held["exchange"], signal=signal,
        )
        if getattr(resp, "rt_cd", None) != ErrorCode.SUCCESS.value:
            self._logger.warning({"event": "overseas_intraday_vbo_exit_rejected", "code": code,
                                  "reason": reason, "msg": getattr(resp, "msg1", "")})
            return None
        self._positions.pop(code, None)
        return signal

    async def close_all(self, *, reason: str = "eod") -> List[Dict[str, Any]]:
        """보유 전량을 마지막 폴링가로 청산한다(마감 전 EOD 청산용)."""
        actions: List[Dict[str, Any]] = []
        for code in list(self._positions.keys()):
            held = self._positions[code]
            action = await self._exit(code, held.get("last_price") or held["entry_price"], reason=reason)
            if action:
                actions.append(action)
        return actions

    # ── 보조 ────────────────────────────────────────────────────────────

    def _resolve_qty(self, price: float) -> int:
        """사이징 미주입 시 1주(최소 단위)로 진입한다."""
        if self._sizing_service is None:
            return 1
        try:
            sized = self._sizing_service.size(limit_price_usd=price)
        except Exception as e:
            self._logger.warning({"event": "overseas_intraday_vbo_sizing_error", "error": str(e)})
            return 0
        try:
            return int(sized.get("qty") or 0)
        except (TypeError, ValueError):
            return 0

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

    @staticmethod
    def _f(x) -> float:
        try:
            return float(x or 0)
        except (TypeError, ValueError):
            return 0.0
