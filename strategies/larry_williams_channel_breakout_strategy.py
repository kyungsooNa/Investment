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
from utils.async_concurrency import bounded_gather
from utils.strategy_state_io import StrategyStateIO
from utils.transaction_cost_utils import TransactionCostUtils
from utils.atomic_json import write_json_atomic


# 청산/exit 동시성 상한. entry chunk_size(10)보다 높게 두어 손절/청산이 entry scan 보다
# 빠르게 마무리되도록 우선순위를 부여한다.
_EXIT_CONCURRENCY = 15


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

    @property
    def strategy_id(self) -> str:
        return "larry_williams_cb"

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

        # 시장 국면 게이트 — state 변경 전 차단
        market_timing = {
            "KOSPI": await self._universe.is_market_timing_ok("KOSPI", caller=self.name, logger=self._logger),
            "KOSDAQ": await self._universe.is_market_timing_ok("KOSDAQ", caller=self.name, logger=self._logger),
        }
        if not any(market_timing.values()):
            self._logger.info({"event": "scan_skipped", "reason": "market_timing_off_both"})
            return signals

        today_str = now.strftime("%Y%m%d")
        candidates = []
        for code, item in watchlist.items():
            if code in self._position_state:
                continue
            if today_str < self._cooldown.get(code, ""):
                continue
            if not market_timing.get(item.market, False):
                self._logger.info({
                    "event": "entry_rejected",
                    "code": code,
                    "name": item.name,
                    "reason": "market_timing_off",
                    "market": item.market,
                })
                continue
            if item.rs_rating < self._cfg.rs_rating_min:
                self._logger.info({
                    "event": "entry_rejected",
                    "code": code,
                    "name": item.name,
                    "reason": "rs_rating_below_min",
                    "rs_rating": item.rs_rating,
                    "threshold": self._cfg.rs_rating_min,
                })
                continue
            candidates.append((code, item))

        await self._sqs.prefetch_prices([code for code, _ in candidates])
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
            self._logger.info({
                "event": "entry_rejected",
                "code": code,
                "name": item.name,
                "reason": "ohlcv_unavailable",
            })
            return None
        # P0 0-8: get_ohlcv는 장중 당일 미확정 봉을 붙이므로 ADX/채널 baseline에서 제외한다.
        ohlcv = self._confirmed_bars(ohlcv_resp.data)

        # Phase 1-1: ADX(14) ≥ threshold + 우상향
        adx_result = self._indicator.calc_adx_sync(
            ohlcv, period=self._cfg.adx_period, slope_lookback=self._cfg.adx_slope_lookback
        )
        if not adx_result:
            self._logger.info({
                "event": "entry_rejected",
                "code": code,
                "name": item.name,
                "reason": "adx_unavailable",
            })
            return None
        if adx_result["adx"] < self._cfg.adx_threshold or not adx_result["adx_rising"]:
            reason = (
                "adx_below_threshold"
                if adx_result["adx"] < self._cfg.adx_threshold
                else "adx_not_rising"
            )
            self._logger.info({
                "event": "entry_rejected",
                "code": code,
                "name": item.name,
                "reason": reason,
                "adx": adx_result.get("adx"),
                "threshold": self._cfg.adx_threshold,
                "rising": adx_result.get("adx_rising"),
            })
            return None

        # Phase 2-1: 현재가 조회 (종가 대용)
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

        # Phase 2-2: 20일 채널 상단 돌파
        # item.high_20d = 어제까지의 20일 최고가 → 오늘 종가가 이를 초과하면 신고가 돌파
        if current <= item.high_20d:
            self._logger.info({
                "event": "entry_rejected",
                "code": code,
                "name": item.name,
                "reason": "no_channel_breakout",
                "current": current,
                "high_20d": item.high_20d,
            })
            return None

        # Phase 2-3: 거래량 확인 (누적 거래량 ≥ avg_vol_20d × multiplier)
        today_vol = self._extract_today_volume(cp_resp)
        if today_vol > 0 and item.avg_vol_20d > 0:
            if today_vol < item.avg_vol_20d * self._cfg.volume_multiplier:
                self._logger.info({
                    "event": "entry_rejected",
                    "code": code,
                    "name": item.name,
                    "reason": "insufficient_volume",
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
            code=code, name=item.name, action="BUY", price=current,
            reason=reason_msg, strategy_name=self.name,
            stop_loss_pct=stop_loss_pct,
            entry_reason="larry_williams_channel_breakout",
            invalidation_price=float(hard_stop_price),
            stop_loss_price=float(hard_stop_price),
            trailing_rule=f"channel_low_{self._cfg.channel_low_period}d",
            expected_holding_period_days=self._cfg.channel_high_period,
            confidence=min(1.0, max(0.0, float(adx_result["adx"]) / max(self._cfg.adx_threshold, 1.0))),
            required_data=[
                "daily_ohlcv",
                "adx",
                "current_price",
                "channel_high",
                "channel_low",
                "volume",
            ],
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

        state = self._position_state.get(code)
        recovered_dirty = False

        cp_resp = await self._sqs.get_current_price(code, caller=self.name)
        current = self._extract_current_price(cp_resp)
        if current <= 0:
            return None

        if state is None:
            state = await self._recover_missing_position_state(code, hold)
            if state is None:
                self._logger.warning({
                    "event": "position_state_missing",
                    "code": code,
                    "name": hold.get("name", code),
                    "reason": "missing_state_and_policy_metadata",
                })
                return None
            recovered_dirty = True

        buy_price = int(hold.get("buy_price", 0) or 0) or (state.entry_price if state else current)
        holding_qty = int(hold.get("qty", 1) or 1)

        reason: Optional[str] = None

        # 1) 칼손절: 현재가 ≤ hard_stop_price (불변)
        if state and current <= state.hard_stop_price:
            reason = f"칼손절: {current} ≤ hard_stop {state.hard_stop_price}"
        # 2) trailing stop: 현재가 < 10일 채널 하단
        elif state and current < state.channel_low_10d:
            reason = f"트레일링 스탑: {current} < channel_low_10d {state.channel_low_10d}"

        # P0 0-9: log 의 pnl 표시도 net 기준 (trigger 자체는 가격 기반이라 net 무관).
        pnl_pct = TransactionCostUtils.net_return_pct(buy_price, current) if buy_price > 0 else 0.0

        if reason:
            self._logger.info({
                "event": "exit_signal_generated",
                "code": code, "name": hold.get("name", code),
                "reason": reason, "pnl_pct": round(pnl_pct, 2), "pnl_basis": "net",
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
                # P0 0-8: 당일 미확정 봉 저가가 trailing stop 갱신을 막지 않도록 확정봉만 사용.
                new_low = self._calc_channel_low(self._confirmed_bars(ohlcv_resp.data), period=self._cfg.channel_low_period)
                if new_low > state.channel_low_10d:
                    state.channel_low_10d = new_low
                    self._logger.debug({
                        "event": "trailing_stop_raised",
                        "code": code, "channel_low_10d": new_low,
                    })
                    return (None, True)

        return (None, recovered_dirty)

    # ── helpers ─────────────────────────────────────────────────────

    def _confirmed_bars(self, ohlcv: list) -> list:
        """P0 0-8: 라이브 호출 시 get_ohlcv가 장중 붙이는 당일 미확정 봉을 제외한다.
        마지막 행 date가 오늘(KST)이면 그 행을 떼고 확정봉만 반환한다."""
        if not ohlcv:
            return ohlcv
        today_str = self._tm.get_current_kst_time().strftime("%Y%m%d")
        if str(ohlcv[-1].get("date", "")) == today_str:
            return ohlcv[:-1]
        return ohlcv

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

    async def _recover_missing_position_state(
        self,
        code: str,
        hold: dict,
    ) -> Optional[LarryWilliamsCBPositionState]:
        """DB HOLD는 있으나 전략 state가 유실된 경우 journal 메타로 복구한다."""
        hard_stop_price = self._extract_positive_int(
            hold,
            "stop_loss_price",
            "invalidation_price",
        )
        if hard_stop_price <= 0:
            return None

        buy_price = self._extract_positive_int(hold, "buy_price") or hard_stop_price
        channel_low_10d = 0
        ohlcv_resp = await self._sqs.get_ohlcv(code, period="D", caller=self.name)
        if ohlcv_resp and ohlcv_resp.rt_cd == "0" and ohlcv_resp.data:
            channel_low_10d = self._calc_channel_low(
                self._confirmed_bars(ohlcv_resp.data),
                period=self._cfg.channel_low_period,
            )
        if channel_low_10d <= 0:
            channel_low_10d = hard_stop_price

        state = LarryWilliamsCBPositionState(
            entry_price=buy_price,
            entry_date=self._normalize_entry_date(hold.get("buy_date")),
            hard_stop_price=hard_stop_price,
            channel_low_10d=channel_low_10d,
        )
        self._position_state[code] = state
        self._logger.warning({
            "event": "position_state_recovered",
            "code": code,
            "name": hold.get("name", code),
            "hard_stop_price": hard_stop_price,
            "channel_low_10d": channel_low_10d,
            "source": "journal_policy_metadata",
        })
        return state

    @staticmethod
    def _extract_positive_int(mapping: dict, *keys: str) -> int:
        for key in keys:
            try:
                value = int(float(mapping.get(key) or 0))
            except (TypeError, ValueError):
                value = 0
            if value > 0:
                return value
        return 0

    @staticmethod
    def _normalize_entry_date(value) -> str:
        raw = str(value or "").strip()
        digits = "".join(ch for ch in raw[:10] if ch.isdigit())
        if len(digits) >= 8:
            return digits[:8]
        return raw

    # ── state persistence ───────────────────────────────────────────

    def _apply_loaded_state(self, data) -> None:
        if not isinstance(data, dict):
            return
        positions = data.get("positions", {}) or {}
        cooldown = data.get("cooldown", {}) or {}
        for k, v in positions.items():
            self._position_state[k] = LarryWilliamsCBPositionState(**v)
        self._cooldown = dict(cooldown)

    def _load_state(self):
        """sync entry. 이벤트 루프 안이면 async 태스크로, 밖이면 동기 경로."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            if not os.path.exists(self.STATE_FILE):
                return
            try:
                with open(self.STATE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._apply_loaded_state(data)
            except Exception as e:
                self._logger.error(f"Failed to load state for {self.name}: {e}")
            return
        asyncio.create_task(self._load_state_async())

    async def _load_state_async(self):
        try:
            data = await StrategyStateIO.load(self.STATE_FILE)
        except Exception as e:
            self._logger.error(f"Failed to load state async for {self.name}: {e}")
            return
        if data is None:
            return
        self._apply_loaded_state(data)

    async def load_state(self):
        """초기화 직후 scan 전에 호출. _load_state_async() 를 명시적으로 await."""
        await self._load_state_async()

    def _save_state(self):
        """sync entry. 이벤트 루프 안이면 StrategyStateIO.schedule_save 로
        background task 등록(flush_pending 추적 대상), 밖이면 동기 경로."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            try:
                payload = {
                    "positions": {k: asdict(v) for k, v in self._position_state.items()},
                    "cooldown": dict(self._cooldown),
                }
                # P0 0-11: atomic write (truncate-write 대체)
                write_json_atomic(self.STATE_FILE, payload, indent=2, ensure_ascii=False)
            except Exception as e:
                self._logger.error(f"Failed to save state for {self.name}: {e}")
            return
        payload = {
            "positions": {k: asdict(v) for k, v in self._position_state.items()},
            "cooldown": dict(self._cooldown),
        }
        StrategyStateIO.schedule_save(self.STATE_FILE, payload)

    async def _save_state_async(self):
        """StrategyStateIO 로 atomic write + per-file lock 저장."""
        payload = {
            "positions": {k: asdict(v) for k, v in self._position_state.items()},
            "cooldown": dict(self._cooldown),
        }
        try:
            await StrategyStateIO.save_atomic(self.STATE_FILE, payload)
        except Exception as e:
            self._logger.error(f"Failed to save state async for {self.name}: {e}")
