# strategies/high_tight_flag_strategy.py
from __future__ import annotations

import logging
import os
import json
from dataclasses import asdict
from typing import List, Optional, Dict

from interfaces.live_strategy import LiveStrategy
from common.types import TradeSignal
from services.stock_query_service import StockQueryService
from core.time_manager import TimeManager
from strategies.oneil_common_types import HTFConfig, HTFPositionState
from services.oneil_universe_service import OneilUniverseService
from core.logger import get_strategy_logger


class HighTightFlagStrategy(LiveStrategy):
    """하이 타이트 플래그 (High Tight Flag) 전략.

    핵심: 40거래일 내 90%+ 폭등(깃대) 후 고점 대비 20% 이내 횡보(깃발)하는 종목이
         거래량·체결강도를 동반하며 신고가를 돌파하는 순간 매수.

    Phase 1 (깃대): min→max 90%+ 급등 확인
    Phase 2 (깃발): 고점 대비 <=20% 하락 횡보 + 거래량 건조
    Phase 3 (돌파): 현재가 > 40일 최고가 + 예상거래량 200%+ + 체결강도 120%+
    Phase 4 (청산): 칼손절 -5% / 10일 MA 트레일링스탑
    """

    STATE_FILE = os.path.join("data", "htf_position_state.json")

    def __init__(
        self,
        stock_query_service: StockQueryService,
        universe_service: OneilUniverseService,
        time_manager: TimeManager,
        config: Optional[HTFConfig] = None,
        logger: Optional[logging.Logger] = None,
    ):
        self._sqs = stock_query_service
        self._universe = universe_service
        self._tm = time_manager
        self._cfg = config or HTFConfig()
        if logger:
            self._logger = logger
        else:
            self._logger = get_strategy_logger("HighTightFlag", sub_dir="oneil")

        self._position_state: Dict[str, HTFPositionState] = {}
        self._load_state()

    @property
    def name(self) -> str:
        return "하이타이트플래그"

    # ── scan ──────────────────────────────────────────────────────────

    async def scan(self) -> List[TradeSignal]:
        signals: List[TradeSignal] = []
        self._logger.info({"event": "scan_started", "strategy_name": self.name})

        watchlist = await self._universe.get_watchlist()
        if not watchlist:
            self._logger.info({"event": "scan_skipped", "reason": "Watchlist is empty"})
            return signals

        self._logger.info({"event": "scan_with_watchlist", "count": len(watchlist)})

        market_progress = self._get_market_progress_ratio()
        if market_progress <= 0:
            self._logger.info({"event": "scan_skipped", "reason": "Market not open or just started"})
            return signals

        for code, item in watchlist.items():
            if code in self._position_state:
                continue

            if not await self._universe.is_market_timing_ok(item.market):
                continue

            try:
                signal = await self._check_htf_setup(code, item, market_progress)
                if signal:
                    signals.append(signal)
            except Exception as e:
                self._logger.error(f"Scan error {code}: {e}")

        self._logger.info({"event": "scan_finished", "signals_found": len(signals)})
        return signals

    async def _check_htf_setup(self, code, item, progress) -> Optional[TradeSignal]:
        """HTF 패턴 감지 + 실시간 돌파 확인."""
        # 1. OHLCV 조회 (깃대 40일 + 깃발 최대 25일 = 65일)
        ohlcv_resp = await self._sqs.get_recent_daily_ohlcv(code, limit=65)
        ohlcv = ohlcv_resp.data if ohlcv_resp and ohlcv_resp.rt_cd == "0" else []
        if not ohlcv or len(ohlcv) < self._cfg.pole_lookback_days:
            return None

        # 2. Phase 1+2: 깃대·깃발 패턴 감지 (순수 계산)
        pattern = self._detect_pole_and_flag(ohlcv)
        if not pattern:
            return None

        self._logger.info({
            "event": "htf_pattern_detected",
            "code": code, "name": item.name,
            "surge_ratio": round(pattern["surge_ratio"], 2),
            "flag_days": pattern["flag_days"],
            "drawdown_pct": round(pattern["drawdown_pct"], 1),
        })

        # 3. Phase 3: 실시간 돌파 확인
        return await self._check_breakout(code, item, pattern, ohlcv, progress)

    def _detect_pole_and_flag(self, ohlcv: list) -> Optional[dict]:
        """Phase 1+2: 깃대 폭등 + 깃발 횡보 패턴 감지 (순수 계산, API 호출 없음).

        Returns:
            dict with pole_high, surge_ratio, flag_days, drawdown_pct or None
        """
        highs = [r.get("high", 0) for r in ohlcv]
        lows = [r.get("low", 0) for r in ohlcv]
        volumes = [r.get("volume", 0) for r in ohlcv]
        n = len(ohlcv)

        # 전체 구간에서 최고점 찾기
        peak_high = max(highs)
        peak_idx = highs.index(peak_high)

        # Phase 1: 깃대 (peak 이전 최대 40일 구간)
        pole_start = max(0, peak_idx - self._cfg.pole_lookback_days + 1)
        pole_lows = lows[pole_start:peak_idx + 1]
        if not pole_lows:
            return None

        pole_low = min(pole_lows)
        if pole_low <= 0:
            return None

        surge_ratio = peak_high / pole_low
        if surge_ratio < self._cfg.pole_min_surge_ratio:
            return None

        # Phase 2: 깃발 (peak 이후 구간)
        flag_days = n - peak_idx - 1
        if flag_days < self._cfg.flag_min_days:
            return None
        if flag_days > self._cfg.flag_max_days:
            return None

        # 깃발 구간 하락폭 체크 (종가 기준 — 장중 꼬리는 용인)
        closes = [r.get("close", 0) for r in ohlcv]
        flag_closes = closes[peak_idx + 1:]
        flag_min_close = min(flag_closes)
        drawdown_pct = (peak_high - flag_min_close) / peak_high * 100
        if drawdown_pct > self._cfg.flag_max_drawdown_pct:
            return None

        # 거래량 감소 확인 (깃발 평균 vs 50일 평균 거래량)
        flag_volumes = volumes[peak_idx + 1:]
        flag_avg_vol = sum(flag_volumes) / len(flag_volumes) if flag_volumes else 0

        vol_count = min(50, n)
        avg_vol_50d = sum(volumes[-vol_count:]) / vol_count if vol_count > 0 else 0
        if avg_vol_50d <= 0:
            return None
        if flag_avg_vol > avg_vol_50d * self._cfg.flag_volume_shrink_ratio:
            return None

        return {
            "pole_high": peak_high,
            "pole_low": pole_low,
            "surge_ratio": surge_ratio,
            "flag_days": flag_days,
            "drawdown_pct": drawdown_pct,
            "avg_vol_50d": avg_vol_50d,
            "flag_avg_vol": flag_avg_vol,
        }

    async def _check_breakout(self, code, item, pattern, ohlcv, progress) -> Optional[TradeSignal]:
        """Phase 3: 실시간 돌파 확인 (가격 + 거래량 + 체결강도)."""
        # 1. 현재가 조회
        resp = await self._sqs.get_current_price(code)
        if not resp or resp.rt_cd != "0":
            return None

        out = resp.data.get("output") if isinstance(resp.data, dict) else None
        if not out:
            return None

        if isinstance(out, dict):
            current = int(out.get("stck_prpr", 0))
            vol = int(out.get("acml_vol", 0))
        else:
            current = int(getattr(out, "stck_prpr", 0) or 0)
            vol = int(getattr(out, "acml_vol", 0) or 0)

        if current <= 0:
            return None

        # 2. 가격 돌파: 현재가 > 40일 최고가 (옵션 A)
        pole_high = pattern["pole_high"]
        if current <= pole_high:
            return None

        # 3. 거래량 돌파: 예상거래량 >= 50일 평균 * 200%
        effective_progress = max(progress, 0.05)
        proj_vol = vol / effective_progress

        volumes = [r.get("volume", 0) for r in ohlcv if r.get("volume")]
        vol_count = min(50, len(volumes))
        if vol_count < 20:
            return None
        avg_vol_50d = sum(volumes[-vol_count:]) / vol_count

        if proj_vol < avg_vol_50d * self._cfg.volume_breakout_multiplier:
            return None

        # 4. 체결강도 >= 120%
        cgld_val = 0.0
        try:
            ccnl_resp = await self._sqs.get_stock_conclusion(code)
            if ccnl_resp and ccnl_resp.rt_cd == "0":
                ccnl_output = ccnl_resp.data.get("output") if isinstance(ccnl_resp.data, dict) else None
                if ccnl_output and isinstance(ccnl_output, list) and len(ccnl_output) > 0:
                    val = ccnl_output[0].get("tday_rltv")
                    cgld_val = float(val) if val else 0.0
        except Exception as e:
            self._logger.warning({"event": "cgld_check_failed", "code": code, "error": str(e)})
            return None

        if cgld_val < self._cfg.execution_strength_min:
            self._logger.debug({
                "event": "breakout_rejected", "code": code,
                "reason": "low_execution_strength", "cgld": cgld_val,
            })
            return None

        # ========= 모든 관문 통과! 매수 시그널 생성 =========
        qty = self._calculate_qty(current)
        self._position_state[code] = HTFPositionState(
            entry_price=current,
            entry_date=self._tm.get_current_kst_time().strftime("%Y%m%d"),
            peak_price=current,
            pole_high=pole_high,
        )
        self._save_state()

        reason_msg = (
            f"HTF돌파(폭등 {pattern['surge_ratio']:.0%}, "
            f"깃발 {pattern['flag_days']}일, "
            f"체결강도 {cgld_val:.1f}%)"
        )

        self._logger.info({
            "event": "buy_signal_generated",
            "code": code, "name": item.name,
            "price": current, "reason": reason_msg,
        })

        return TradeSignal(
            code=code, name=item.name, action="BUY", price=current, qty=qty,
            reason=reason_msg, strategy_name=self.name,
        )

    # ── check_exits ──────────────────────────────────────────────────

    async def check_exits(self, holdings: List[dict]) -> List[TradeSignal]:
        signals = []
        for hold in holdings:
            code = hold.get("code")
            buy_price = hold.get("buy_price")
            if not code or not buy_price:
                continue

            state = self._position_state.get(code)
            if not state:
                state = HTFPositionState(buy_price, "", buy_price, buy_price)
                self._position_state[code] = state

            resp = await self._sqs.get_current_price(code)
            if not resp or resp.rt_cd != "0":
                continue

            output = resp.data.get("output") if isinstance(resp.data, dict) else None
            if not output:
                continue

            if isinstance(output, dict):
                current = int(output.get("stck_prpr", 0))
            else:
                current = int(getattr(output, "stck_prpr", 0) or 0)

            if current <= 0:
                continue

            # 최고가 갱신
            if current > state.peak_price:
                state.peak_price = current
                self._save_state()

            pnl = (current - buy_price) / buy_price * 100
            reason = ""

            # 1. 칼손절
            if pnl <= self._cfg.stop_loss_pct:
                reason = f"칼손절({pnl:.1f}%)"

            # 2. 10일 MA 트레일링스탑
            if not reason:
                is_break, break_reason = await self._check_trailing_ma_stop(code, current)
                if is_break:
                    reason = break_reason

            # 매도 시그널 생성
            if reason:
                self._position_state.pop(code, None)
                self._save_state()
                signals.append(TradeSignal(
                    code=code, name=hold.get("name", code), action="SELL",
                    price=current, qty=1, reason=reason, strategy_name=self.name,
                ))

        return signals

    async def _check_trailing_ma_stop(self, code: str, current_price: int) -> tuple:
        """10일 MA 트레일링스탑 체크."""
        period = self._cfg.trailing_ma_period
        ohlcv_resp = await self._sqs.get_recent_daily_ohlcv(code, limit=period)
        ohlcv = ohlcv_resp.data if ohlcv_resp and ohlcv_resp.rt_cd == "0" else []
        if not ohlcv or len(ohlcv) < period:
            return False, ""

        closes = [r.get("close", 0) for r in ohlcv if r.get("close")]
        if len(closes) < period:
            return False, ""

        ma = sum(closes[-period:]) / period
        if current_price < ma:
            return True, f"트레일링스탑(10MA {ma:,.0f} 하향이탈)"
        return False, ""

    # ── 헬퍼 ─────────────────────────────────────────────────────────

    def _calculate_qty(self, price: int) -> int:
        if price <= 0:
            return self._cfg.min_qty
        budget = self._cfg.total_portfolio_krw * (self._cfg.position_size_pct / 100)
        return max(int(budget / price), self._cfg.min_qty)

    def _get_market_progress_ratio(self) -> float:
        now = self._tm.get_current_kst_time()
        open_t = self._tm.get_market_open_time()
        close_t = self._tm.get_market_close_time()
        total = (close_t - open_t).total_seconds()
        elapsed = (now - open_t).total_seconds()
        return min(elapsed / total, 1.0) if total > 0 else 0.0

    def _load_state(self):
        if os.path.exists(self.STATE_FILE):
            try:
                with open(self.STATE_FILE, "r") as f:
                    data = json.load(f)
                for k, v in data.items():
                    self._position_state[k] = HTFPositionState(**v)
            except Exception:
                pass

    def _save_state(self):
        try:
            os.makedirs(os.path.dirname(self.STATE_FILE), exist_ok=True)
            data = {k: asdict(v) for k, v in self._position_state.items()}
            with open(self.STATE_FILE, "w") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass
