"""해외 장중 전략 공통 베이스 (라이브 paper 경로).

미국장은 웹소켓/분봉이 없어 REST 폴링이 유일한 장중 틱 소스다. 폴링 패스는
`OverseasIntradayTask` 가 심볼당 1회만 돌려 여러 전략에 fan-out 하며, 각 전략은
본 베이스를 상속해 **세션 상수 산출(`_build_setup`)과 진입 판정(`_should_enter`)
두 가지만** 구현한다. 감시목록·포지션·주문·청산·마켓타이밍 게이트·사이징은
전 전략이 동일하므로 여기서 한 번만 다룬다.

장중 판정에 필요한 당일 값(고/저/현재종가/누적거래량)은 해외 일봉 엔드포인트가
주는 **당일 미완성 봉**과 폴링 틱에서 얻는다. 국내가 `acml_vol` 을 장중 경과
비율로 환산하는 것과 같은 방식으로, 거래량 조건은 `USSessionVolumeService` 가
환산·허들을 담당한다.

**실주문 잠금은 `OverseasOrderExecutionService` 가 단독으로 관장한다.** 본 계층은
broker 의존을 갖지 않으며, live_enabled=False 인 주문 서비스를 주입받으면
would-be 주문만 저널에 남는다.

한계: 포지션 상태는 in-memory 다. 장중 재시작 시 보유가 소실돼 EOD 청산 기록이
누락될 수 있다(paper 경로라 실포지션 영향은 없음).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from common.overseas_types import OverseasExchange
from common.types import ErrorCode


class OverseasIntradayStrategyBase:
    STRATEGY_NAME = "OverseasIntradayStrategy"
    MARKET = "US"
    HISTORY_LIMIT = 60
    EVENT_PREFIX = "overseas_intraday"

    def __init__(
        self,
        candidate_service,
        stock_query_service,
        order_execution_service,
        session_volume_service=None,
        market_clock=None,
        position_sizing_service=None,
        market_regime_service=None,
        logger: Optional[logging.Logger] = None,
        *,
        top_n: int = 20,
        max_positions: int = 5,
        exchange: OverseasExchange = OverseasExchange.NASD,
        market_timing_gate: bool = True,
    ) -> None:
        self._candidate_service = candidate_service
        self._sqs = stock_query_service
        self._orders = order_execution_service
        self._session = session_volume_service
        self._clock = market_clock
        self._sizing_service = position_sizing_service
        self._regime = market_regime_service
        self._market_timing_gate = market_timing_gate
        self._logger = logger or logging.getLogger(__name__)
        self._top_n = top_n
        self._max_positions = max_positions
        self._default_exchange = exchange

        self._session_date: Optional[str] = None
        self._watch: Dict[str, Dict[str, Any]] = {}
        self._positions: Dict[str, Dict[str, Any]] = {}
        self._entered_today: set[str] = set()

    # ── 전략별 구현부 ───────────────────────────────────────────────────

    def _build_setup(
        self,
        code: str,
        history: List[Dict[str, Any]],
        today_bar: Optional[Dict[str, Any]],
        trade_date: str,
    ) -> Optional[Dict[str, Any]]:
        """세션 상수를 산출한다. 감시 대상이 아니면 None.

        `history` 는 당일 봉을 제외한 완성봉(오름차순), `today_bar` 는 당일
        미완성 봉(없으면 None)이다.
        """
        raise NotImplementedError

    def _should_enter(
        self, setup: Dict[str, Any], price: float, volume: Optional[float], now,
    ) -> bool:
        """폴링 틱으로 진입 조건을 판정한다."""
        raise NotImplementedError

    def _stop_price(self, setup: Dict[str, Any], price: float) -> float:
        raise NotImplementedError

    def _entry_reason(self) -> str:
        return f"{self.EVENT_PREFIX}_entry"

    def _signal_extras(self, setup: Dict[str, Any], price: float) -> Dict[str, Any]:
        """진입 신호에 실을 전략별 근거값(선택)."""
        return {}

    # ── 세션 ────────────────────────────────────────────────────────────

    async def prepare_session(
        self, trade_date: str, exchange: Optional[OverseasExchange] = None,
    ) -> int:
        """당일 감시 목록을 만든다. 같은 거래일 재호출은 no-op. 반환: 감시 종목 수."""
        if self._session_date == trade_date:
            return len(self._watch)

        ex = exchange or self._default_exchange
        candidates = await self._candidate_service.get_candidates(ex, top_n=self._top_n)

        watch: Dict[str, Dict[str, Any]] = {}
        for cand in candidates or []:
            code = cand.get("code")
            if not code:
                continue
            setup = await self._prepare_one(code, trade_date, ex)
            if setup:
                setup["exchange"] = ex
                setup["name"] = cand.get("name", code)
                watch[code] = setup

        self._session_date = trade_date
        self._watch = watch
        self._positions = {}
        self._entered_today = set()
        self._logger.info({
            "event": f"{self.EVENT_PREFIX}_session", "strategy": self.STRATEGY_NAME,
            "trade_date": trade_date, "exchange": ex.value,
            "candidates": len(candidates or []), "watch": len(watch),
        })
        return len(watch)

    async def _prepare_one(
        self, code: str, trade_date: str, exchange: OverseasExchange,
    ) -> Optional[Dict[str, Any]]:
        try:
            resp = await self._sqs.get_recent_daily_ohlcv(
                code, limit=self.HISTORY_LIMIT, exchange=exchange,
            )
        except Exception as e:
            self._logger.warning({"event": f"{self.EVENT_PREFIX}_ohlcv_error",
                                  "strategy": self.STRATEGY_NAME, "code": code, "error": str(e)})
            return None
        if not resp or resp.rt_cd != ErrorCode.SUCCESS.value or not resp.data:
            return None
        history, today_bar = self._split_today(resp.data, trade_date)
        try:
            setup = self._build_setup(code, history, today_bar, trade_date)
        except Exception as e:
            self._logger.warning({"event": f"{self.EVENT_PREFIX}_setup_error",
                                  "strategy": self.STRATEGY_NAME, "code": code, "error": str(e)})
            return None
        if setup is not None:
            self._seed_session_range(setup, today_bar)
        return setup

    def _seed_session_range(self, setup: Dict[str, Any], today_bar) -> None:
        """당일 미완성 봉으로 세션 고/저를 시드한다(없으면 첫 틱에서 시작)."""
        high = self._f((today_bar or {}).get("high"))
        low = self._f((today_bar or {}).get("low"))
        setup["session_high"] = high if high > 0 else None
        setup["session_low"] = low if low > 0 else None

    def _update_session_range(self, setup: Dict[str, Any], price: float) -> None:
        """폴링 틱으로 세션 고/저를 갱신한다.

        한계: 폴링 간격(기본 60초) 사이의 극값은 관측되지 않는다. 캔들 상대위치를
        쓰는 전략(PP/OSB)은 **관측된** 고/저 기준으로 판정하므로 dry-run(완성봉)
        결과와 완전히 일치하지 않는다 — 해외는 분봉이 없어 이보다 촘촘한 소스가 없다.
        """
        cur_high = setup.get("session_high")
        cur_low = setup.get("session_low")
        setup["session_high"] = price if cur_high is None else max(cur_high, price)
        setup["session_low"] = price if cur_low is None else min(cur_low, price)

    def _relative_position(self, setup: Dict[str, Any], price: float) -> float:
        """세션 레인지 내 현재가 위치 [0,1]. 레인지가 없으면 1.0(제약 없음)."""
        high = setup.get("session_high")
        low = setup.get("session_low")
        if high is None or low is None:
            return 1.0
        rng = high - low
        if rng <= 0:
            return 1.0
        return (price - low) / rng


    @staticmethod
    def _split_today(
        rows: List[Dict[str, Any]], trade_date: str,
    ) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
        """장중에는 마지막 행이 당일 미완성 봉이다 — 완성봉 계산에서 분리한다."""
        if rows and str(rows[-1].get("date") or "") == str(trade_date):
            return rows[:-1], rows[-1]
        return rows, None

    def watch_codes(self) -> List[str]:
        return list(self._watch.keys())

    def get_state(self) -> Dict[str, Any]:
        return {
            "strategy": self.STRATEGY_NAME,
            "session_date": self._session_date,
            "watch": self._watch,
            "positions": self._positions,
            "entered_today": sorted(self._entered_today),
        }

    # ── 틱 판정 ─────────────────────────────────────────────────────────

    async def on_price(
        self, code: str, price: float, volume: Optional[float] = None,
    ) -> Optional[Dict[str, Any]]:
        """폴링 틱 1건을 판정해 주문까지 수행한다. 주문이 없으면 None."""
        px = self._f(price)
        if px <= 0:
            return None

        setup = self._watch.get(code)
        if setup is not None:
            self._update_session_range(setup, px)

        held = self._positions.get(code)
        if held is not None:
            held["last_price"] = px
            if px <= held["stop_price"]:
                return await self._exit(code, px, reason="stop")
            return None

        if setup is None or code in self._entered_today:
            return None
        now = self._clock.get_current_kst_time() if self._clock is not None else None
        if not self._should_enter(setup, px, volume, now):
            return None
        if not await self._is_regime_ok(code):
            return None
        if len(self._positions) >= self._max_positions:
            self._logger.info({"event": f"{self.EVENT_PREFIX}_entry_blocked",
                               "strategy": self.STRATEGY_NAME, "code": code,
                               "reason": "max_positions", "max_positions": self._max_positions})
            return None
        return await self._enter(code, px, setup)

    async def _is_regime_ok(self, code: str) -> bool:
        """미국장 국면 게이트. 조회 실패는 fail-closed. 청산은 이 게이트를 거치지 않는다."""
        if self._regime is None or not self._market_timing_gate:
            return True
        try:
            snap = await self._regime.classify(self.MARKET)
        except Exception as e:
            self._logger.warning({"event": f"{self.EVENT_PREFIX}_regime_error",
                                  "strategy": self.STRATEGY_NAME, "code": code, "error": str(e)})
            return False
        if snap.is_rising:
            return True
        self._logger.info({
            "event": f"{self.EVENT_PREFIX}_entry_blocked", "strategy": self.STRATEGY_NAME,
            "code": code, "reason": "market_timing", "regime": snap.regime_label,
            "fail_detail": snap.fail_detail,
        })
        return False

    async def _enter(
        self, code: str, price: float, setup: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        qty = self._resolve_qty(price)
        if qty <= 0:
            return None
        stop_price = self._stop_price(setup, price)
        signal = {
            "strategy": self.STRATEGY_NAME,
            "code": code,
            "name": setup.get("name", code),
            "action": "BUY",
            "date": self._session_date,
            "entry_price": price,
            "stop_price": stop_price,
            "reason": self._entry_reason(),
        }
        signal.update(self._signal_extras(setup, price))
        resp = await self._orders.place_entry(
            code=code, qty=qty, limit_price=price,
            exchange=setup["exchange"], signal=signal,
        )
        if getattr(resp, "rt_cd", None) != ErrorCode.SUCCESS.value:
            self._logger.warning({"event": f"{self.EVENT_PREFIX}_entry_rejected",
                                  "strategy": self.STRATEGY_NAME, "code": code,
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
        entry = held["entry_price"]
        signal = {
            "strategy": self.STRATEGY_NAME,
            "code": code,
            "action": "SELL",
            "date": self._session_date,
            "entry_price": entry,
            "exit_price": price,
            "exit_reason": reason,
            "realized_pct": (price / entry - 1) * 100.0 if entry > 0 else 0.0,
        }
        resp = await self._orders.place_exit(
            code=code, qty=held["qty"], limit_price=price, reason=reason,
            exchange=held["exchange"], signal=signal,
        )
        if getattr(resp, "rt_cd", None) != ErrorCode.SUCCESS.value:
            self._logger.warning({"event": f"{self.EVENT_PREFIX}_exit_rejected",
                                  "strategy": self.STRATEGY_NAME, "code": code,
                                  "reason": reason, "msg": getattr(resp, "msg1", "")})
            return None
        self._positions.pop(code, None)
        return signal

    async def close_all(self, *, reason: str = "eod") -> List[Dict[str, Any]]:
        """보유 전량을 마지막 폴링가로 청산한다(마감 전 EOD 청산용)."""
        actions: List[Dict[str, Any]] = []
        for code in list(self._positions.keys()):
            held = self._positions[code]
            action = await self._exit(
                code, held.get("last_price") or held["entry_price"], reason=reason,
            )
            if action:
                actions.append(action)
        return actions

    # ── 보조 ────────────────────────────────────────────────────────────

    def _volume_ok(self, setup: Dict[str, Any], volume: Optional[float], now,
                   *, multiplier: float) -> bool:
        """환산 거래량 + 오전 실거래량 하한 판정. 재료가 없으면 진입하지 않는다."""
        if volume is None or self._session is None or now is None:
            return False
        return self._session.passes(
            actual_volume=volume,
            avg_volume=setup.get("avg_volume", 0.0),
            base_multiplier=multiplier,
            now=now,
            trade_date=self._session_date or "",
        )

    def _resolve_qty(self, price: float) -> int:
        """사이징 미주입 시 1주(최소 단위)로 진입한다."""
        if self._sizing_service is None:
            return 1
        try:
            sized = self._sizing_service.size(limit_price_usd=price)
        except Exception as e:
            self._logger.warning({"event": f"{self.EVENT_PREFIX}_sizing_error",
                                  "strategy": self.STRATEGY_NAME, "error": str(e)})
            return 0
        try:
            return int(sized.get("qty") or 0)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _f(x) -> float:
        try:
            return float(x or 0)
        except (TypeError, ValueError):
            return 0.0
