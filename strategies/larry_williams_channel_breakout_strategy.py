# strategies/larry_williams_channel_breakout_strategy.py
from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import asdict
from datetime import timedelta
from typing import Dict, List, Optional, Tuple

from interfaces.live_strategy import LiveStrategy
from common.types import TradeSignal
from services.stock_query_service import StockQueryService
from services.indicator_service import IndicatorService
from services.oneil_universe_service import OneilUniverseService
from core.market_clock import MarketClock
from core.logger import get_strategy_logger
from strategies.larry_williams_cb_types import LarryWilliamsCBConfig, LarryWilliamsCBPositionState


class LarryWilliamsChannelBreakoutStrategy(LiveStrategy):
    """래리 윌리엄스 / 브렌트 펜볼드 돈천 채널 돌파 전략.

    핵심: RS Rating 상위 + ADX 유효 추세 종목이 20일 신고가를 거래량 동반 돌파할 때
         종가 베팅 진입, 10일 채널 하단 trailing stop으로 추세를 끝까지 추종.

    진입 조건 (15:10 이후 종가 베팅):
      - WatchList: OneilUniverseService.get_watchlist() Pool A
      - RS Rating ≥ 80
      - ADX(14) ≥ 25 이며 우상향
      - 당일 종가 > 20일 채널 상단(high_20d)
      - 당일 거래량 ≥ 20일 평균 거래량 × 1.5

    청산 조건 (우선순위):
      1. 칼손절: 현재가 ≤ hard_stop_price (진입 직후 확정, 불변)
      2. trailing stop: 현재가 < channel_low_10d (매 장마감 후 상향 갱신)
    """
    STATE_FILE = os.path.join("data", "lwcb_position_state.json")

    def __init__(
        self,
        stock_query_service: StockQueryService,
        universe_service: OneilUniverseService,
        indicator_service: IndicatorService,
        market_clock: MarketClock,
        config: Optional[LarryWilliamsCBConfig] = None,
        logger: Optional[logging.Logger] = None,
        state_file: Optional[str] = None,
    ):
        self._sqs = stock_query_service
        self._universe = universe_service
        self._indicator = indicator_service
        self._tm = market_clock
        self._cfg = config or LarryWilliamsCBConfig()
        self._logger = logger or get_strategy_logger("LarryWilliamsCB", sub_dir="oneil")

        self._position_state: Dict[str, LarryWilliamsCBPositionState] = {}
        self._cooldown: Dict[str, str] = {}  # code → unblock_date (YYYYMMDD)
        if state_file is not None:
            self.STATE_FILE = str(state_file)
        self._load_state()

    @property
    def name(self) -> str:
        return "LarryWilliamsCB"

    # ── scan ────────────────────────────────────────────────────────

    async def scan(self) -> List[TradeSignal]:
        signals: List[TradeSignal] = []
        self._logger.info({"event": "scan_started", "strategy_name": self.name})

        now = self._tm.get_current_kst_time()
        cutoff_minutes = self._cfg.entry_cutoff_hour * 60 + self._cfg.entry_cutoff_minute
        if now.hour * 60 + now.minute < cutoff_minutes:
            self._logger.info({"event": "scan_skipped", "reason": "Before entry cutoff (15:10)"})
            return signals

        watchlist = await self._universe.get_watchlist(logger=self._logger)
        if not watchlist:
            self._logger.info({"event": "scan_skipped", "reason": "Watchlist is empty"})
            return signals

        self._logger.info({"event": "scan_with_watchlist", "count": len(watchlist)})

        today_str = now.strftime("%Y%m%d")
        candidates = [
            (code, item) for code, item in watchlist.items()
            if code not in self._position_state
            and today_str >= self._cooldown.get(code, "")
            and item.rs_rating >= self._cfg.rs_rating_min
        ]

        for i in range(0, len(candidates), 10):
            chunk = candidates[i:i + 10]
            results = await asyncio.gather(
                *[self._check_entry(code, item) for code, item in chunk],
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

    async def _check_entry(self, code: str, item) -> Optional[TradeSignal]:
        """진입 조건 검사: RS → ADX → 채널 상단 돌파 → 거래량 → 신호 생성."""
        # OHLCV 한 번 조회 후 ADX/채널 계산에 공유
        ohlcv_resp = await self._sqs.get_ohlcv(code, period="D", caller=self.name)
        if not ohlcv_resp or ohlcv_resp.rt_cd != "0" or not ohlcv_resp.data:
            return None
        ohlcv = ohlcv_resp.data

        # Phase 1-1: ADX(14) ≥ threshold + 우상향
        adx_result = self._indicator.calc_adx_sync(
            ohlcv, period=self._cfg.adx_period, slope_lookback=self._cfg.adx_slope_lookback
        )
        if not adx_result:
            return None
        if adx_result["adx"] < self._cfg.adx_threshold or not adx_result["adx_rising"]:
            self._logger.debug({
                "event": "entry_skip", "code": code, "reason": "ADX",
                "adx": adx_result.get("adx"), "rising": adx_result.get("adx_rising"),
            })
            return None

        # Phase 2-1: 현재가 조회 (종가 대용)
        cp_resp = await self._sqs.get_current_price(code, caller=self.name)
        current = self._extract_current_price(cp_resp)
        if current <= 0:
            return None

        # Phase 2-2: 20일 채널 상단 돌파
        # item.high_20d = 어제까지의 20일 최고가 → 오늘 종가가 이를 초과하면 신고가 돌파
        if current <= item.high_20d:
            return None

        # Phase 2-3: 거래량 확인 (누적 거래량 ≥ avg_vol_20d × multiplier)
        today_vol = self._extract_today_volume(cp_resp)
        if today_vol > 0 and item.avg_vol_20d > 0:
            if today_vol < item.avg_vol_20d * self._cfg.volume_multiplier:
                self._logger.debug({
                    "event": "entry_skip", "code": code, "reason": "Volume",
                    "today_vol": today_vol,
                    "required": int(item.avg_vol_20d * self._cfg.volume_multiplier),
                })
                return None

        # 칼손절가 계산: max(20일 채널 하단, 진입가 × (1 + hard_stop_pct/100))
        channel_low_20d = self._calc_channel_low(ohlcv, period=self._cfg.channel_high_period)
        price_stop = int(current * (1 + self._cfg.hard_stop_pct / 100))
        hard_stop_price = max(channel_low_20d, price_stop)

        # trailing stop 초기값: 10일 채널 하단
        channel_low_10d = self._calc_channel_low(ohlcv, period=self._cfg.channel_low_period)

        qty = 1 if self._cfg.use_fixed_qty else max(1, int(500_000 / current))

        # 포지션 등록
        today_str = self._tm.get_current_kst_time().strftime("%Y%m%d")
        self._position_state[code] = LarryWilliamsCBPositionState(
            entry_price=current,
            entry_date=today_str,
            hard_stop_price=hard_stop_price,
            channel_low_10d=channel_low_10d,
            entry_adx=adx_result["adx"],
        )

        reason_msg = (
            f"20일 채널 돌파: {current} > {item.high_20d}, "
            f"ADX={adx_result['adx']:.1f}, "
            f"stop={hard_stop_price}"
        )
        self._logger.info({
            "event": "entry_signal_generated",
            "code": code, "name": item.name,
            "current": current, "high_20d": item.high_20d,
            "adx": adx_result["adx"],
            "hard_stop": hard_stop_price, "channel_low_10d": channel_low_10d,
            "qty": qty,
        })
        # PositionSizingService(Fixed Fractional)에 동적 손절폭을 전달:
        # hard_stop은 종목별로 max(channel_low_20d, price_stop)이라 종목마다 다름.
        # stop_loss_pct는 음수 규약 (예: -7.0). 스케줄러가 abs() 처리.
        stop_loss_pct = (hard_stop_price - current) / current * 100.0

        return TradeSignal(
            code=code, name=item.name, action="BUY", price=current, qty=qty,
            reason=reason_msg, strategy_name=self.name,
            stop_loss_pct=stop_loss_pct,
        )

    # ── check_exits ─────────────────────────────────────────────────

    async def check_exits(self, holdings: List[dict]) -> List[TradeSignal]:
        signals: List[TradeSignal] = []
        if not holdings:
            return signals

        results = await asyncio.gather(
            *[self._check_single_exit(hold) for hold in holdings],
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

        state = self._position_state.get(code)

        cp_resp = await self._sqs.get_current_price(code, caller=self.name)
        current = self._extract_current_price(cp_resp)
        if current <= 0:
            return None

        buy_price = int(hold.get("buy_price", 0) or 0) or (state.entry_price if state else current)
        holding_qty = int(hold.get("qty", 1) or 1)

        reason: Optional[str] = None

        # 1) 칼손절: 현재가 ≤ hard_stop_price (불변)
        if state and current <= state.hard_stop_price:
            reason = f"칼손절: {current} ≤ hard_stop {state.hard_stop_price}"
        # 2) trailing stop: 현재가 < 10일 채널 하단
        elif state and current < state.channel_low_10d:
            reason = f"트레일링 스탑: {current} < channel_low_10d {state.channel_low_10d}"

        pnl_pct = (current - buy_price) / buy_price * 100.0 if buy_price > 0 else 0.0

        if reason:
            self._logger.info({
                "event": "exit_signal_generated",
                "code": code, "name": hold.get("name", code),
                "reason": reason, "pnl_pct": round(pnl_pct, 2),
            })
            self._position_state.pop(code, None)
            unblock = (self._tm.get_current_kst_time().date() + timedelta(days=self._cfg.cooldown_days)).strftime("%Y%m%d")
            self._cooldown[code] = unblock
            return (
                TradeSignal(
                    code=code, name=hold.get("name", code), action="SELL",
                    price=current, qty=holding_qty, reason=reason, strategy_name=self.name,
                ),
                True,
            )

        # 청산 없음 → channel_low_10d 상향 갱신
        if state:
            ohlcv_resp = await self._sqs.get_ohlcv(code, period="D", caller=self.name)
            if ohlcv_resp and ohlcv_resp.rt_cd == "0" and ohlcv_resp.data:
                new_low = self._calc_channel_low(ohlcv_resp.data, period=self._cfg.channel_low_period)
                if new_low > state.channel_low_10d:
                    state.channel_low_10d = new_low
                    self._logger.debug({
                        "event": "trailing_stop_raised",
                        "code": code, "channel_low_10d": new_low,
                    })
                    return (None, True)

        return (None, False)

    # ── helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _extract_current_price(resp) -> int:
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
    def _extract_today_volume(resp) -> int:
        """당일 누적 거래량(acml_vol) 추출. 실패 시 0."""
        if not resp or getattr(resp, "rt_cd", "") != "0":
            return 0
        data = getattr(resp, "data", None)
        out = data.get("output") if isinstance(data, dict) else data
        if not out:
            return 0
        try:
            if isinstance(out, dict):
                return int(out.get("acml_vol", 0) or 0)
            return int(getattr(out, "acml_vol", 0) or 0)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _calc_channel_low(ohlcv: list, period: int) -> int:
        """최근 period 봉의 저가 최솟값(채널 하단)을 반환. 실패 시 0."""
        try:
            recent = ohlcv[-period:] if len(ohlcv) >= period else ohlcv
            lows = [int(c.get("low", 0) or 0) for c in recent if int(c.get("low", 0) or 0) > 0]
            return min(lows) if lows else 0
        except Exception:
            return 0

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
                self._position_state[k] = LarryWilliamsCBPositionState(**v)
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
