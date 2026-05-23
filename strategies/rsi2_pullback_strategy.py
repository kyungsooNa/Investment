# strategies/rsi2_pullback_strategy.py
from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import asdict
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

from interfaces.live_strategy import LiveStrategy
from common.types import TradeSignal
from services.stock_query_service import StockQueryService
from services.indicator_service import IndicatorService
from services.oneil_universe_service import OneilUniverseService
from core.market_clock import MarketClock
from core.logger import get_strategy_logger
from strategies.rsi2_pullback_types import RSI2PullbackConfig, RSI2PositionState
from utils.async_concurrency import bounded_gather


_MINERVINI_STAGE_2 = 2  # services.minervini_stage_service.MinerviniStageService.STAGE_2_ADVANCING

# 청산/exit 동시성 상한. entry chunk_size(10)보다 높게 두어 손절/청산이 entry scan 보다
# 빠르게 마무리되도록 우선순위를 부여한다.
_EXIT_CONCURRENCY = 15


class RSI2PullbackStrategy(LiveStrategy):
    """래리 코너스 RSI(2) 눌림목 매매 전략.

    핵심: Stage 2 상승장의 주도주가 단기 과매도(RSI(2) ≤ 10)에 빠진
         종가 직전에 진입하여, 5MA 복귀 시 빠르게 평균 회귀 익절.

    진입 조건 (Phase 1 → 2):
      - WatchList: OneilUniverseService.get_watchlist() 결과 (Pool A 우량주 포함)
      - 장기 추세: OSBWatchlistItem.minervini_stage == 2
      - 단기 과매도: 일봉 RSI(2) 최신값 ≤ 10
      - 진입 시각: 15:10 이후 (종가 베팅)
      - 마켓 타이밍 🔴이면 비중 50%로 축소 진입

    청산 조건 (우선순위):
      1. 추세 붕괴: 종가 < 200MA → 즉시 전량 손절
      2. 하드 스탑: 진입가 대비 -5% 도달 → 즉시 전량 손절
      3. 빠른 복귀 익절: 종가가 5MA에 도달 → 전량 익절
    """
    STATE_FILE = os.path.join("data", "rsi2_position_state.json")

    def __init__(
        self,
        stock_query_service: StockQueryService,
        universe_service: OneilUniverseService,
        indicator_service: IndicatorService,
        market_clock: MarketClock,
        config: Optional[RSI2PullbackConfig] = None,
        logger: Optional[logging.Logger] = None,
        state_file: Optional[str] = None,
    ):
        self._sqs = stock_query_service
        self._universe = universe_service
        self._indicator = indicator_service
        self._tm = market_clock
        self._cfg = config or RSI2PullbackConfig()
        if logger:
            self._logger = logger
        else:
            self._logger = get_strategy_logger("RSI2Pullback", sub_dir="oneil")

        self._position_state: Dict[str, RSI2PositionState] = {}
        self._cooldown: Dict[str, str] = {}  # code → unblock_date (YYYYMMDD)
        if state_file is not None:
            self.STATE_FILE = str(state_file)
        self._load_state()

    @property
    def name(self) -> str:
        return "RSI2눌림목"

    @property
    def strategy_id(self) -> str:
        return "rsi2_pullback"

    # ── scan ────────────────────────────────────────────────────────

    async def scan(self) -> List[TradeSignal]:
        signals: List[TradeSignal] = []
        self._logger.info({"event": "scan_started", "strategy_name": self.name})

        # 시각 가드: 15:10 이전엔 종가 베팅 트리거 평가하지 않음
        now = self._tm.get_current_kst_time()
        cutoff_minutes = self._cfg.entry_cutoff_hour * 60 + self._cfg.entry_cutoff_minute
        now_minutes = now.hour * 60 + now.minute
        if now_minutes < cutoff_minutes:
            self._logger.info({"event": "scan_skipped", "reason": "Before entry cutoff (15:10)"})
            return signals

        watchlist = await self._universe.get_watchlist(logger=self._logger)
        if not watchlist:
            self._logger.info({"event": "scan_skipped", "reason": "Watchlist is empty"})
            return signals

        self._logger.info({"event": "scan_with_watchlist", "count": len(watchlist)})

        market_timing = {
            "KOSPI": await self._universe.is_market_timing_ok("KOSPI", caller=self.name, logger=self._logger),
            "KOSDAQ": await self._universe.is_market_timing_ok("KOSDAQ", caller=self.name, logger=self._logger),
        }

        today_str = now.strftime("%Y%m%d")
        candidates = [
            (code, item) for code, item in watchlist.items()
            if code not in self._position_state
            and today_str >= self._cooldown.get(code, "")
        ]

        for i in range(0, len(candidates), 10):
            chunk = candidates[i:i + 10]
            results = await asyncio.gather(
                *[self._check_entry(code, item, market_timing) for code, item in chunk],
                return_exceptions=True,
            )
            for result in results:
                if isinstance(result, Exception):
                    self._logger.error(f"Scan error: {result}")
                elif result:
                    signals.append(result)

        if signals:
            self._save_state()
        return signals

    async def _check_entry(self, code, item, market_timing) -> Optional[TradeSignal]:
        """진입 조건 검사: Stage 2 → RSI(2) ≤ 10 → 시각 → 비중 결정."""
        # Phase 1-1: Stage 2 확인 (universe가 사전 분류)
        if self._cfg.require_minervini_stage2 and item.minervini_stage != _MINERVINI_STAGE_2:
            self._logger.info({
                "event": "entry_rejected",
                "code": code,
                "name": item.name,
                "reason": "not_stage2",
                "minervini_stage": item.minervini_stage,
                "threshold": _MINERVINI_STAGE_2,
            })
            return None

        # Phase 1-2: 일봉 RSI(2) 조회
        rsi_resp = await self._indicator.get_rsi(code, period=self._cfg.rsi_period, candle_type="D")
        if not rsi_resp or rsi_resp.rt_cd != "0" or not rsi_resp.data:
            self._logger.info({
                "event": "entry_rejected",
                "code": code,
                "name": item.name,
                "reason": "rsi_unavailable",
            })
            return None
        latest_rsi = rsi_resp.data[-1].get("rsi")
        if latest_rsi is None or latest_rsi > self._cfg.rsi_threshold:
            self._logger.info({
                "event": "entry_rejected",
                "code": code,
                "name": item.name,
                "reason": "rsi_above_threshold",
                "rsi": latest_rsi,
                "threshold": self._cfg.rsi_threshold,
            })
            return None

        # Phase 2: 마켓 타이밍 기반 비중 결정
        risk_off = not market_timing.get(item.market, False)

        # 현재가 조회
        cp_resp = await self._sqs.get_current_price(code, caller=self.name)
        current = self._extract_current_price(cp_resp)
        if current <= 0:
            self._logger.info({
                "event": "entry_rejected",
                "code": code,
                "name": item.name,
                "reason": "invalid_current_price",
                "current": current,
            })
            return None

        qty = self._calculate_qty(current, risk_off=risk_off)
        if qty <= 0:
            self._logger.info({
                "event": "entry_rejected",
                "code": code,
                "name": item.name,
                "reason": "zero_qty",
                "current": current,
                "qty": qty,
            })
            return None

        # 포지션 등록
        self._position_state[code] = RSI2PositionState(
            entry_price=current,
            entry_date=self._tm.get_current_kst_time().strftime("%Y%m%d"),
            entry_rsi=float(latest_rsi),
            risk_off_entry=risk_off,
        )

        reason_msg = (
            f"RSI({self._cfg.rsi_period})={latest_rsi:.2f} ≤ {self._cfg.rsi_threshold}, "
            f"Stage 2, {'축소비중' if risk_off else '정상비중'} 진입"
        )
        self._logger.info({
            "event": "entry_signal_generated",
            "code": code, "name": item.name,
            "rsi": round(float(latest_rsi), 2),
            "current": current, "qty": qty,
            "risk_off": risk_off,
        })
        return TradeSignal(
            code=code, name=item.name, action="BUY", price=current,
            reason=reason_msg, strategy_name=self.name,
            volatility_20d_annualized=getattr(item, "volatility_20d_annualized", None),
        )

    # ── check_exits ─────────────────────────────────────────────────

    async def check_exits(self, holdings: List[dict]) -> List[TradeSignal]:
        signals: List[TradeSignal] = []
        if not holdings:
            return signals

        results = await bounded_gather(
            [self._check_single_exit(hold) for hold in holdings],
            limit=_EXIT_CONCURRENCY,
            return_exceptions=True,
        )
        state_dirty = False
        for result in results:
            if isinstance(result, Exception):
                self._logger.error(f"check_exits error: {result}")
                continue
            if result is None:
                continue
            sig, dirty = result
            if sig is not None:
                signals.append(sig)
            if dirty:
                state_dirty = True

        if state_dirty:
            self._save_state()
        return signals

    async def _check_single_exit(self, hold: dict) -> Optional[Tuple[Optional[TradeSignal], bool]]:
        """단일 보유 종목 청산 조건 검사. (signal, state_dirty) 반환."""
        code = hold.get("code")
        if not code:
            return None

        cp_resp = await self._sqs.get_current_price(code, caller=self.name)
        current = self._extract_current_price(cp_resp)
        if current <= 0:
            return None

        buy_price = int(hold.get("buy_price", 0) or 0)
        if buy_price <= 0:
            buy_price = current

        # 200MA / 5MA 조회 (병렬)
        ma_resps = await asyncio.gather(
            self._indicator.get_moving_average(code, period=self._cfg.trend_break_ma_period, candle_type="D"),
            self._indicator.get_moving_average(code, period=self._cfg.take_profit_ma_period, candle_type="D"),
            return_exceptions=True,
        )
        ma200 = self._latest_ma(ma_resps[0])
        ma5 = self._latest_ma(ma_resps[1])

        pnl_pct = (current - buy_price) / buy_price * 100.0
        reason: Optional[str] = None

        # 1) 추세 붕괴: 종가 < 200MA → 손절 (Stage 2 가정 무너짐)
        if ma200 is not None and current < ma200:
            reason = f"추세 붕괴 손절: 현재가 {current} < 200MA {ma200:.0f}"
        # 2) 하드 스탑: 진입가 대비 -5%
        elif pnl_pct <= self._cfg.hard_stop_pct:
            reason = f"하드 스탑 손절: PnL {pnl_pct:.2f}% ≤ {self._cfg.hard_stop_pct}%"
        # 3) 빠른 복귀 익절: 종가가 5MA 터치
        elif ma5 is not None and current >= ma5:
            reason = f"5MA 터치 익절: 현재가 {current} ≥ 5MA {ma5:.0f}"

        if not reason:
            return (None, False)

        holding_qty = int(hold.get("qty", 1) or 1)
        self._logger.info({
            "event": "exit_signal_generated",
            "code": code, "name": hold.get("name", code),
            "reason": reason,
            "pnl_pct": round(pnl_pct, 2),
        })

        # 손절성 청산은 쿨다운 등록
        state = self._position_state.pop(code, None)
        state_dirty = state is not None
        if "손절" in reason or "스탑" in reason:
            unblock = (date.today() + timedelta(days=self._cfg.cooldown_days)).strftime("%Y%m%d")
            self._cooldown[code] = unblock
            state_dirty = True

        return (
            TradeSignal(
                code=code, name=hold.get("name", code), action="SELL",
                price=current, qty=holding_qty, reason=reason, strategy_name=self.name
            ),
            state_dirty,
        )

    # ── helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _extract_current_price(resp) -> int:
        """get_current_price 응답에서 현재가 정수값을 추출. 실패 시 0."""
        if not resp or getattr(resp, "rt_cd", "") != "0":
            return 0
        data = getattr(resp, "data", None)
        out = data.get("output") if isinstance(data, dict) else data
        if not out:
            return 0
        try:
            if isinstance(out, dict):
                return int(out.get("stck_prpr", 0) or 0)
            return int(getattr(out, "stck_prpr", 0) or 0)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _latest_ma(resp) -> Optional[float]:
        """get_moving_average 응답에서 최신 MA 값을 추출. 실패 시 None."""
        if isinstance(resp, Exception):
            return None
        if not resp or getattr(resp, "rt_cd", "") != "0" or not getattr(resp, "data", None):
            return None
        try:
            ma = resp.data[-1].get("ma")
            return float(ma) if ma is not None else None
        except (TypeError, ValueError, IndexError):
            return None

    def _calculate_qty(self, price: int, risk_off: bool = False) -> int:
        """Fixed-fractional 사이징. risk_off=True면 절반 비중."""
        if price <= 0:
            return self._cfg.min_qty
        if self._cfg.use_fixed_qty:
            return self._cfg.min_qty
        ratio = self._cfg.position_size_pct / 100.0
        if risk_off:
            ratio *= self._cfg.risk_off_position_ratio
        budget = self._cfg.total_portfolio_krw * ratio
        return max(int(budget / price), self._cfg.min_qty)

    # ── state persistence ───────────────────────────────────────────

    def _load_state(self):
        if not os.path.exists(self.STATE_FILE):
            return
        try:
            with open(self.STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            positions = data.get("positions", {}) if isinstance(data, dict) else {}
            cooldown = data.get("cooldown", {}) if isinstance(data, dict) else {}
            for k, v in positions.items():
                self._position_state[k] = RSI2PositionState(**v)
            self._cooldown = dict(cooldown)
        except Exception as e:
            self._logger.error(f"Failed to load state for {self.name}: {e}")

    def _save_state(self):
        try:
            os.makedirs(os.path.dirname(self.STATE_FILE), exist_ok=True)
            payload = {
                "positions": {k: asdict(v) for k, v in self._position_state.items()},
                "cooldown": dict(self._cooldown),
            }
            with open(self.STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self._logger.error(f"Failed to save state for {self.name}: {e}")
