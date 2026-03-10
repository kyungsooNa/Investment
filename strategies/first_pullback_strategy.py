# strategies/first_pullback_strategy.py
from __future__ import annotations

import logging
import os
import json
from dataclasses import asdict
from typing import List, Optional, Dict, Tuple

from interfaces.live_strategy import LiveStrategy
from common.types import TradeSignal
from services.stock_query_service import StockQueryService
from core.time_manager import TimeManager
from strategies.first_pullback_types import FirstPullbackConfig, FPPositionState
from services.oneil_universe_service import OneilUniverseService
from core.logger import get_strategy_logger


class FirstPullbackStrategy(LiveStrategy):
    """주도주 첫 눌림목(Holy Grail) 매매 전략.

    핵심: 급등 후 20MA까지 건전하게 조정받은 주도주가,
         거래량 고갈 상태에서 반등 양봉을 만드는 순간 진입.

    진입 조건 (4단계 필터):
      Phase 1 (Setup): PoolA 종목 중 급등 이력(상한가 or +30%) + 20MA 5일 연속 우상향
      Phase 2 (Pullback): 장중 저가가 20MA -1%~+3% 범위 + 거래량 ≤ 급등일의 50%
      Phase 3 (Trigger): 양봉 전환 or 전일고가 돌파 + 체결강도 >= 100%

    청산 조건:
      1. 손절: 현재가 < 20MA * (1 - 2%) → 잔량 전체 매도
      2. 부분 익절: PnL +10~15% 도달 & 미실행 → 50% 매도
    """
    STATE_FILE = os.path.join("data", "fp_position_state.json")

    def __init__(
        self,
        stock_query_service: StockQueryService,
        universe_service: OneilUniverseService,
        time_manager: TimeManager,
        config: Optional[FirstPullbackConfig] = None,
        logger: Optional[logging.Logger] = None,
    ):
        self._sqs = stock_query_service
        self._universe = universe_service
        self._tm = time_manager
        self._cfg = config or FirstPullbackConfig()
        if logger:
            self._logger = logger
        else:
            self._logger = get_strategy_logger("FirstPullback", sub_dir="oneil")

        self._position_state: Dict[str, FPPositionState] = {}
        self._load_state()

    @property
    def name(self) -> str:
        return "첫눌림목"

    # ── scan ────────────────────────────────────────────────────────

    async def scan(self) -> List[TradeSignal]:
        signals: List[TradeSignal] = []
        self._logger.info({"event": "scan_started", "strategy_name": self.name})

        watchlist = await self._universe.get_watchlist(logger=self._logger)
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

            if not await self._universe.is_market_timing_ok(item.market, logger=self._logger):
                continue

            try:
                signal = await self._check_entry(code, item, market_progress)
                if signal:
                    signals.append(signal)
            except Exception as e:
                self._logger.error(f"Scan error {code}: {e}")

        self._logger.info({"event": "scan_finished", "signals_found": len(signals)})
        return signals

    async def _check_entry(self, code, item, progress) -> Optional[TradeSignal]:
        """진입 조건 검사: Phase 1 → 2 → 3 순서로 필터링."""
        # ── Phase 1: Setup (로켓 발사) ──
        ohlcv_resp = await self._sqs.get_recent_daily_ohlcv(code, limit=30)
        ohlcv = ohlcv_resp.data if ohlcv_resp and ohlcv_resp.rt_cd == "0" else []
        if not ohlcv or len(ohlcv) < 25:
            return None

        surge_result = self._check_surge_history(ohlcv)
        if not surge_result:
            self._logger.debug({"event": "entry_rejected", "code": code, "reason": "no_surge_history"})
            return None

        surge_volume, surge_day_high = surge_result

        if not self._check_ma_uptrend(ohlcv):
            self._logger.debug({"event": "entry_rejected", "code": code, "reason": "ma_not_uptrending"})
            return None

        # ── Phase 2: Pullback (건전한 숨 고르기) ──
        resp = await self._sqs.get_current_price(code)
        if not resp or resp.rt_cd != "0":
            return None

        out = resp.data.get("output") if isinstance(resp.data, dict) else None
        if not out:
            return None

        if isinstance(out, dict):
            current = int(out.get("stck_prpr", 0))
            today_open = int(out.get("stck_oprc", 0))
            today_low = int(out.get("stck_lwpr", 0))
            prev_close = int(out.get("stck_prdy_clpr", 0))
        else:
            current = int(getattr(out, "stck_prpr", 0) or 0)
            today_open = int(getattr(out, "stck_oprc", 0) or 0)
            today_low = int(getattr(out, "stck_lwpr", 0) or 0)
            prev_close = int(getattr(out, "stck_prdy_clpr", 0) or 0)

        if current <= 0 or today_low <= 0:
            return None

        # 20MA 계산 (OHLCV의 최근 20일)
        closes = [r.get("close", 0) for r in ohlcv if r.get("close")]
        ma_20d = sum(closes[-self._cfg.ma_period:]) / self._cfg.ma_period if len(closes) >= self._cfg.ma_period else 0
        if ma_20d <= 0:
            return None

        if not self._check_pullback_to_ma(today_low, ma_20d):
            self._logger.debug({"event": "entry_rejected", "code": code, "reason": "pullback_out_of_range"})
            return None

        if not self._check_volume_dryup(ohlcv, surge_volume):
            self._logger.debug({"event": "entry_rejected", "code": code, "reason": "volume_not_dry"})
            return None

        # ── Phase 3: Trigger (매수 방아쇠) ──
        prev_high = ohlcv[-1].get("high", 0) if ohlcv else 0
        if not self._check_bullish_reversal(current, today_open, prev_high):
            self._logger.debug({"event": "entry_rejected", "code": code, "reason": "no_bullish_reversal"})
            return None

        # 체결강도 확인
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
            self._logger.debug({"event": "entry_rejected", "code": code, "reason": "low_execution_strength", "cgld": cgld_val})
            return None

        # ========= 모든 관문 통과! 매수 시그널 생성 =========
        qty = self._calculate_qty(current)

        self._position_state[code] = FPPositionState(
            entry_price=current,
            entry_date=self._tm.get_current_kst_time().strftime("%Y%m%d"),
            peak_price=current,
            surge_day_high=surge_day_high,
            partial_sold=False,
        )
        self._save_state()

        reason_msg = f"첫눌림목(체결강도 {cgld_val:.1f}%, 20MA {ma_20d:,.0f})"

        self._logger.info({
            "event": "buy_signal_generated",
            "code": code, "name": item.name,
            "price": current,
            "reason": reason_msg,
        })

        return TradeSignal(
            code=code, name=item.name, action="BUY", price=current, qty=qty,
            reason=reason_msg, strategy_name=self.name
        )

    # ── Phase 1 검사 메서드 ────────────────────────────────────────

    def _check_surge_history(self, ohlcv: list) -> Optional[Tuple[int, int]]:
        """최근 20거래일 내 급등 이력 확인.

        Returns: (surge_volume, surge_day_high) 또는 None
        """
        lookback = min(self._cfg.surge_lookback_days, len(ohlcv))
        recent = ohlcv[-lookback:]

        # 조건 A: 상한가 (종가 기준 전일 대비 +29%)
        for i in range(1, len(recent)):
            prev_close = recent[i - 1].get("close", 0)
            curr_close = recent[i].get("close", 0)
            if prev_close > 0 and curr_close > 0:
                change = (curr_close - prev_close) / prev_close * 100
                if change >= self._cfg.upper_limit_pct:
                    return (recent[i].get("volume", 0), recent[i].get("high", curr_close))

        # 조건 B: 단기간(5~10일) +30% 급등
        for window in range(self._cfg.rapid_surge_min_days, self._cfg.rapid_surge_max_days + 1):
            for start in range(len(recent) - window):
                end = start + window
                start_close = recent[start].get("close", 0)
                end_close = recent[end].get("close", 0)
                if start_close > 0 and end_close > 0:
                    change = (end_close - start_close) / start_close * 100
                    if change >= self._cfg.rapid_surge_pct:
                        # 기준봉: 구간 내 최대 거래량일
                        window_slice = recent[start:end + 1]
                        max_vol_day = max(window_slice, key=lambda r: r.get("volume", 0))
                        surge_high = max(r.get("high", 0) for r in window_slice)
                        return (max_vol_day.get("volume", 0), surge_high)

        return None

    def _check_ma_uptrend(self, ohlcv: list) -> bool:
        """20일 이동평균선이 최근 5일 연속 우상향인지 확인."""
        closes = [r.get("close", 0) for r in ohlcv if r.get("close")]
        period = self._cfg.ma_period
        needed = period + self._cfg.ma_rising_days
        if len(closes) < needed:
            return False

        # 최근 (ma_rising_days + 1)일의 20MA 계산
        ma_values = []
        for i in range(self._cfg.ma_rising_days + 1):
            end_idx = len(closes) - i
            start_idx = end_idx - period
            if start_idx < 0:
                return False
            ma = sum(closes[start_idx:end_idx]) / period
            ma_values.append(ma)

        # ma_values: [오늘MA, 어제MA, ..., 5일전MA] (역순)
        ma_values.reverse()  # [5일전MA, ..., 어제MA, 오늘MA]

        # 5일 연속 기울기 양수 확인
        for i in range(1, len(ma_values)):
            if ma_values[i] <= ma_values[i - 1]:
                return False

        return True

    # ── Phase 2 검사 메서드 ────────────────────────────────────────

    def _check_pullback_to_ma(self, today_low: int, ma_20d: float) -> bool:
        """장중 최저가가 20MA의 -1% ~ +3% 범위 안에 있는지 확인."""
        lower = ma_20d * (1 + self._cfg.pullback_lower_pct / 100)
        upper = ma_20d * (1 + self._cfg.pullback_upper_pct / 100)
        return lower <= today_low <= upper

    def _check_volume_dryup(self, ohlcv: list, surge_volume: int) -> bool:
        """최근 3일 평균 거래량이 급등일 거래량의 50% 이하인지 확인."""
        if surge_volume <= 0:
            return False

        days = self._cfg.volume_dryup_days
        recent_vols = [r.get("volume", 0) for r in ohlcv[-days:]]
        if not recent_vols:
            return False

        avg_vol = sum(recent_vols) / len(recent_vols)
        return avg_vol <= surge_volume * self._cfg.volume_dryup_ratio

    # ── Phase 3 검사 메서드 ────────────────────────────────────────

    def _check_bullish_reversal(self, current: int, today_open: int, prev_high: int) -> bool:
        """양봉 전환(current > open) 또는 전일 고가 돌파 확인."""
        if today_open > 0 and current > today_open:
            return True
        if prev_high > 0 and current > prev_high:
            return True
        return False

    # ── check_exits ────────────────────────────────────────────────

    async def check_exits(self, holdings: List[dict]) -> List[TradeSignal]:
        signals = []
        for hold in holdings:
            code = hold.get("code")
            buy_price = hold.get("buy_price")
            if not code or not buy_price:
                continue

            state = self._position_state.get(code)
            if not state:
                state = FPPositionState(
                    entry_price=buy_price,
                    entry_date="",
                    peak_price=buy_price,
                    surge_day_high=0,
                    partial_sold=False,
                )
                self._position_state[code] = state

            # 현재가 조회
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

            # 20MA 동적 계산 (매일 변하는 최신 MA)
            ohlcv_resp = await self._sqs.get_recent_daily_ohlcv(code, limit=self._cfg.ma_period)
            ohlcv = ohlcv_resp.data if ohlcv_resp and ohlcv_resp.rt_cd == "0" else []
            closes = [r.get("close", 0) for r in ohlcv if r.get("close")]

            pnl = (current - buy_price) / buy_price * 100
            reason = ""

            # 🚨 손절: 20MA -2% 이탈 → 잔량 전체 매도
            if len(closes) >= self._cfg.ma_period:
                ma_20d = sum(closes[-self._cfg.ma_period:]) / self._cfg.ma_period
                threshold = ma_20d * (1 + self._cfg.stop_loss_below_ma_pct / 100)
                if current < threshold:
                    reason = f"손절(20MA {ma_20d:,.0f} 하향이탈 {pnl:.1f}%)"

            # 🌟 부분 익절: +10~15% 도달 & 미실행
            if not reason and not state.partial_sold:
                if pnl >= self._cfg.take_profit_lower_pct:
                    holding_qty = int(hold.get("qty", 1))
                    sell_qty = max(1, int(holding_qty * self._cfg.partial_sell_ratio))

                    if sell_qty >= holding_qty:
                        sell_qty = holding_qty
                        sell_reason = f"전량익절({pnl:.1f}%, 잔고 {holding_qty}주)"
                    else:
                        sell_reason = f"부분익절({pnl:.1f}%, {sell_qty}주/{holding_qty}주)"

                    self._logger.info({
                        "event": "partial_profit_signal",
                        "code": code, "pnl": round(pnl, 2),
                        "sell_qty": sell_qty, "holding_qty": holding_qty,
                    })

                    state.partial_sold = True
                    self._save_state()

                    signals.append(TradeSignal(
                        code=code, name=hold.get("name", code), action="SELL",
                        price=current, qty=sell_qty,
                        reason=sell_reason, strategy_name=self.name
                    ))
                    continue  # 부분 매도 후 손절 체크하지 않음

            # 매도 시그널 생성 (손절)
            if reason:
                holding_qty = int(hold.get("qty", 1))
                self._position_state.pop(code, None)
                self._save_state()
                signals.append(TradeSignal(
                    code=code, name=hold.get("name", code), action="SELL",
                    price=current, qty=holding_qty, reason=reason, strategy_name=self.name
                ))

        return signals

    # ── 헬퍼 ──────────────────────────────────────────────────────

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
                    self._position_state[k] = FPPositionState(**v)
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
