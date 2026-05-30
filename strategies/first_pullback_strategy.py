# strategies/first_pullback_strategy.py
from __future__ import annotations

import asyncio
import logging
import os
import json
from dataclasses import asdict
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Tuple

from interfaces.live_strategy import LiveStrategy
from common.date_utils import previous_trading_day_str
from common.types import TradeSignal
from services.stock_query_service import StockQueryService
from core.market_clock import MarketClock
from strategies.first_pullback_types import FirstPullbackConfig, FPPositionState
from services.oneil_universe_service import OneilUniverseService
from core.logger import get_strategy_logger
from utils.async_concurrency import bounded_gather
from utils.strategy_state_io import StrategyStateIO
from utils.transaction_cost_utils import TransactionCostUtils
from utils.atomic_json import write_json_atomic


# 청산/exit 동시성 상한. entry chunk_size(10)보다 높게 두어 손절/청산이 entry scan 보다
# 빠르게 마무리되도록 우선순위를 부여한다.
_EXIT_CONCURRENCY = 15


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
        market_clock: MarketClock,
        config: Optional[FirstPullbackConfig] = None,
        logger: Optional[logging.Logger] = None,
        state_file: Optional[str] = None,
    ):
        self._sqs = stock_query_service
        self._universe = universe_service
        self._tm = market_clock
        self._cfg = config or FirstPullbackConfig()
        if logger:
            self._logger = logger
        else:
            self._logger = get_strategy_logger("FirstPullback", sub_dir="oneil")

        self._position_state: Dict[str, FPPositionState] = {}
        self._cooldown: Dict[str, str] = {}  # code → unblock_date (YYYYMMDD)
        if state_file is not None:
            self.STATE_FILE = str(state_file)
        self._load_state()

    @property
    def name(self) -> str:
        return "첫눌림목"

    @property
    def strategy_id(self) -> str:
        return "first_pullback"

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

        # 마켓 타이밍 사전 체크
        market_timing = {
            "KOSPI": await self._universe.is_market_timing_ok("KOSPI", caller=self.name, logger=self._logger),
            "KOSDAQ": await self._universe.is_market_timing_ok("KOSDAQ", caller=self.name, logger=self._logger)
        }
        if not any(market_timing.values()):
            self._logger.info({"event": "scan_skipped", "reason": "Bad market timing for both markets"})
            return signals

        today_str = self._tm.get_current_kst_time().strftime("%Y%m%d")
        candidates = [
            (code, item) for code, item in watchlist.items()
            if code not in self._position_state
            and market_timing.get(item.market, False)
            and today_str >= self._cooldown.get(code, "")
        ]
        await self._sqs.prefetch_prices([code for code, _ in candidates])
        for i in range(0, len(candidates), 10):
            chunk = candidates[i:i + 10]
            results = await asyncio.gather(
                *[self._check_entry(code, item, market_progress, market_timing) for code, item in chunk],
                return_exceptions=True,
            )
            for result in results:
                if isinstance(result, Exception):
                    self._logger.error(f"Scan error: {result}")
                elif result:
                    signals.append(result)

        self._logger.info({"event": "scan_finished", "signals_found": len(signals)})
        return signals

    async def _check_entry(self, code, item, progress, market_timing_cache=None) -> Optional[TradeSignal]:
        """진입 조건 검사: Phase 1 → 2 → 3 순서로 필터링."""
        # ── 현재가 데이터 선행 조회 (OHLCV 캐시 활용 위해) ──
        resp = await self._sqs.get_current_price(code, caller=self.name)
        if not resp or resp.rt_cd != "0":
            return None

        out = resp.data.get("output") if isinstance(resp.data, dict) else None
        if not out:
            return None

        if isinstance(out, dict):
            current = int(out.get("stck_prpr", 0))
            today_open = int(out.get("stck_oprc", 0))
            today_high = int(out.get("stck_hgpr", 0))
            today_low = int(out.get("stck_lwpr", 0))
            prdy_vrss = int(out.get("prdy_vrss", 0))
            prdy_vrss_sign = str(out.get("prdy_vrss_sign", "3"))
        else:
            current = int(getattr(out, "stck_prpr", 0) or 0)
            today_open = int(getattr(out, "stck_oprc", 0) or 0)
            today_high = int(getattr(out, "stck_hgpr", 0) or 0)
            today_low = int(getattr(out, "stck_lwpr", 0) or 0)
            prdy_vrss = int(getattr(out, "prdy_vrss", 0) or 0)
            prdy_vrss_sign = str(getattr(out, "prdy_vrss_sign", "3") or "3")

        # 전일 종가 계산 (현재가와 전일대비를 이용해 역산)
        if prdy_vrss_sign in ("1", "2"):  # 상한, 상승
            prev_close = current - prdy_vrss
        elif prdy_vrss_sign in ("4", "5"):  # 하한, 하락
            prev_close = current + prdy_vrss
        else:  # 보합
            prev_close = current

        if current <= 0 or today_low <= 0:
            return None

        # ── Phase 1: Setup (로켓 발사) ── 어제까지 확정 OHLCV(캐시)
        now = self._tm.get_current_kst_time()
        yesterday_str = previous_trading_day_str(now)
        ohlcv_resp = await self._sqs.get_recent_daily_ohlcv(code, limit=30, end_date=yesterday_str)
        ohlcv = ohlcv_resp.data if ohlcv_resp and ohlcv_resp.rt_cd == "0" else []
        if not ohlcv or len(ohlcv) < 25:
            return None

        surge_result = self._check_surge_history(ohlcv)
        if not surge_result:
            self._logger.info({"event": "entry_rejected", "code": code, "reason": "no_surge_history"})
            return None

        surge_volume, surge_day_high = surge_result

        if not self._check_ma_uptrend(ohlcv):
            self._logger.info({"event": "entry_rejected", "code": code, "reason": "ma_not_uptrending"})
            return None

        # ── Phase 2: Pullback (건전한 숨 고르기) ──
        # 20MA 계산 (어제까지 확정 OHLCV 기준)
        closes = [r.get("close", 0) for r in ohlcv if r.get("close")]
        ma_20d = sum(closes[-self._cfg.ma_period:]) / self._cfg.ma_period if len(closes) >= self._cfg.ma_period else 0
        if ma_20d <= 0:
            return None

        if not self._check_pullback_to_ma(today_low, ma_20d):
            pullback_pct = (today_low - ma_20d) / ma_20d * 100 if ma_20d > 0 else 0.0
            self._logger.info({
                "event": "entry_rejected", "code": code, "reason": "pullback_out_of_range",
                "pullback_pct": round(pullback_pct, 2),
                "allowed_range": f"{self._cfg.pullback_lower_pct}% ~ {self._cfg.pullback_upper_pct}%",
                "today_low": today_low, "ma_20d": round(ma_20d, 0),
            })
            return None

        if not self._check_volume_dryup(ohlcv, surge_volume):
            days = self._cfg.volume_dryup_days
            recent_vols = [r.get("volume", 0) for r in ohlcv[-days:]]
            avg_vol = sum(recent_vols) / len(recent_vols) if recent_vols else 0
            vol_dryup_pct = (avg_vol / surge_volume * 100) if surge_volume > 0 else 0.0
            self._logger.info({
                "event": "entry_rejected", "code": code, "reason": "volume_not_dry",
                "vol_dryup_pct": round(vol_dryup_pct, 2), "threshold_pct": self._cfg.volume_dryup_ratio * 100,
                "avg_vol": int(avg_vol), "surge_volume": surge_volume,
            })
            return None

        # ── Phase 3: Trigger (매수 방아쇠) ──
        prev_high = ohlcv[-1].get("high", 0) if ohlcv else 0
        if not self._check_bullish_reversal(current, today_open, today_high, today_low, prev_close, prev_high):
            self._logger.info({
                "event": "entry_rejected", "code": code, "name": item.name, "reason": "no_bullish_reversal",
                "current": current, "today_open": today_open, "prev_high": prev_high,
                "today_high": today_high, "today_low": today_low, "prev_close": prev_close,
            })
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
            self._logger.info({
                "event": "entry_rejected", "code": code, "name": item.name, "reason": "low_execution_strength",
                "cgld": cgld_val, "threshold": self._cfg.execution_strength_min
            })
            return None

        # ========= 모든 관문 통과! 매수 시그널 생성 =========

        self._position_state[code] = FPPositionState(
            entry_price=current,
            entry_date=self._tm.get_current_kst_time().strftime("%Y%m%d"),
            peak_price=current,
            surge_day_high=surge_day_high,
        )
        self._save_state()

        # 상세 근거 계산
        recent_vols = [r.get("volume", 0) for r in ohlcv[-self._cfg.volume_dryup_days:]]
        avg_vol = float(sum(recent_vols) / len(recent_vols)) if recent_vols else 0.0
        vol_dryup_pct = float((avg_vol / surge_volume * 100)) if surge_volume > 0 else 0.0
        pullback_pct = float((today_low - ma_20d) / ma_20d * 100) if ma_20d > 0 else 0.0

        reason_msg = (
            f"첫눌림목(20MA {ma_20d:,.0f} 지지({pullback_pct:+.1f}%), "
            f"거래고갈 {vol_dryup_pct:.0f}%(급등대비), "
            f"반등확인 {today_open:,}->{current:,}, "
            f"체결강도 {cgld_val:.1f}%)"
        )
        stop_loss_price = ma_20d * (1 + self._cfg.stop_loss_below_ma_pct / 100)
        target_price = current * (1 + self._cfg.take_profit_lower_pct / 100)

        self._logger.info({
            "event": "buy_signal_generated",
            "code": code, "name": item.name,
            "metrics": {
                "price": current,
                "ma_20d": round(ma_20d, 0),
                "pullback_pct": round(pullback_pct, 2),
                "vol_dryup_pct": round(vol_dryup_pct, 1),
                "execution_strength": cgld_val,
                "rs_score": getattr(item, "rs_score", 0.0),
                "rs_rating": getattr(item, "rs_rating", 0),
                "total_score": getattr(item, "total_score", 0.0),
                "market_timing": market_timing_cache.get(item.market) if market_timing_cache else None,
                "volatility_20d_annualized": getattr(item, "volatility_20d_annualized", None),
            },
            "reason": reason_msg,
        })

        return TradeSignal(
            code=code, name=item.name, action="BUY", price=current,
            reason=reason_msg, strategy_name=self.name,
            entry_reason="first_pullback_bullish_reversal",
            invalidation_price=round(stop_loss_price, 2),
            stop_loss_price=round(stop_loss_price, 2),
            target_price=round(target_price, 2),
            trailing_rule=f"{self._cfg.ma_period}ma_break_with_grace",
            expected_holding_period_days=self._cfg.ma_period,
            confidence=min(1.0, max(0.0, cgld_val / max(self._cfg.execution_strength_min, 1.0))),
            required_data=[
                "current_price",
                "daily_ohlcv",
                "moving_average_20d",
                "surge_history",
                "volume_dryup",
                "execution_strength",
            ],
            volatility_20d_annualized=getattr(item, "volatility_20d_annualized", None),
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
        """20일 이동평균선이 전반적인 우상향 추세인지 확인."""
        closes = [r.get("close", 0) for r in ohlcv if r.get("close")]
        period = self._cfg.ma_period
        needed = period + self._cfg.ma_rising_days
        
        if len(closes) < needed:
            return False

        # 최근 (ma_rising_days + 1)일의 20MA 계산 (기존과 동일)
        ma_values = []
        for i in range(self._cfg.ma_rising_days + 1):
            end_idx = len(closes) - i
            start_idx = end_idx - period
            ma = sum(closes[start_idx:end_idx]) / period
            ma_values.append(ma)

        ma_values.reverse()  # [5일전, ..., 오늘]

        # --- 유연한 카운팅 로직 적용 ---
        # 1. 실제로 상승한 횟수 계산
        actual_rising_count = sum(1 for i in range(1, len(ma_values)) if ma_values[i] > ma_values[i - 1])
        
        # 2. 설정값(4일)과 비교
        if actual_rising_count < self._cfg.ma_rising_min_count:
            return False
            
        # 3. 추가 안전장치: 중간에 횡보하더라도 '오늘'이 '5일전' 보다는 무조건 높아야 함
        if ma_values[-1] <= ma_values[0]:
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

    def _check_bullish_reversal(self, current: int, today_open: int, today_high: int, today_low: int, prev_close: int, prev_high: int) -> bool:
        """양봉 전환(current > open) 또는 전일 고가 돌파 확인. 갭하락 가짜양봉 방어."""
        # 갭하락 가짜양봉 방어 1: 전일종가 대비 floor 미달
        if prev_close > 0 and current < prev_close * (1 + self._cfg.reversal_prev_close_floor_pct / 100):
            return False
        # 갭하락 가짜양봉 방어 2: 캔들 내 현재가가 하단에 위치 (윗꼬리 양봉)
        if today_high > today_low:
            rel_pos = (current - today_low) / (today_high - today_low)
            if rel_pos < self._cfg.reversal_min_relative_pos:
                return False
        if today_open > 0 and current > today_open:
            return True
        if prev_high > 0 and current > prev_high:
            return True
        return False

    # ── check_exits ────────────────────────────────────────────────

    async def check_exits(self, holdings: List[dict]) -> List[TradeSignal]:
        if not holdings:
            return []

        results = await bounded_gather(
            [self._check_single_exit(hold) for hold in holdings],
            limit=_EXIT_CONCURRENCY,
            return_exceptions=True,
        )

        signals: List[TradeSignal] = []
        state_dirty = False
        for result in results:
            if isinstance(result, Exception):
                self._logger.error({"event": "exit_check_error", "error": str(result)})
            elif result:
                s_list, dirty = result
                signals.extend(s_list)
                if dirty:
                    state_dirty = True

        if state_dirty:
            await self._save_state_async()
        return signals

    async def _check_single_exit(self, hold: dict) -> tuple:
        """단일 보유 종목 청산 조건 검사.

        Returns: (List[TradeSignal], state_dirty: bool)
        """
        signals: List[TradeSignal] = []
        state_dirty = False

        code = hold.get("code")
        buy_price_raw = hold.get("buy_price")
        if not code or not buy_price_raw:
            return signals, state_dirty

        buy_price = float(buy_price_raw)  # numpy.float64 타입 제거 (순수 float 형변환)

        state = self._position_state.get(code)
        if not state:
            state = FPPositionState(
                entry_price=int(buy_price),
                entry_date="",
                peak_price=int(buy_price),
                surge_day_high=0,
            )
            self._position_state[code] = state

        # 현재가 조회
        resp = await self._sqs.get_current_price(code, caller=self.name)
        if not resp or resp.rt_cd != "0":
            return signals, state_dirty

        output = resp.data.get("output") if isinstance(resp.data, dict) else None
        if not output:
            return signals, state_dirty

        if isinstance(output, dict):
            current = int(output.get("stck_prpr", 0))
        else:
            current = int(getattr(output, "stck_prpr", 0) or 0)

        if current <= 0:
            return signals, state_dirty

        # 최고가 갱신 (dirty flag)
        if current > state.peak_price:
            state.peak_price = current
            state_dirty = True

        pnl = float((current - buy_price) / buy_price * 100)

        # P0 0-9: stop/익절 trigger 비교는 비용 반영 net 기준 — backtest 와 동일.
        pnl_net = TransactionCostUtils.net_return_pct(buy_price, current)

        # MFE / MAE 갱신 (가격 변동 = gross — backtest 의 _bar_excursion 과 동일 기준)
        if pnl > state.mfe_pct:
            state.mfe_pct = round(pnl, 2)
            state_dirty = True
        if pnl < state.mae_pct:
            state.mae_pct = round(pnl, 2)
            state_dirty = True

        # 20MA 동적 계산 (매일 변하는 최신 MA)
        ohlcv_resp = await self._sqs.get_recent_daily_ohlcv(code, limit=self._cfg.ma_period)
        ohlcv = ohlcv_resp.data if ohlcv_resp and ohlcv_resp.rt_cd == "0" else []
        closes = [r.get("close", 0) for r in ohlcv if r.get("close")]

        reason = ""

        # 🚨 손절: 20MA -2% 이탈 → 10분 유예 후 확정 (14:50 이후는 즉시) — 가격 기준 trigger, log 는 net 표시
        if len(closes) >= self._cfg.ma_period:
            ma_20d = float(sum(closes[-self._cfg.ma_period:]) / self._cfg.ma_period)
            threshold = ma_20d * (1 + self._cfg.stop_loss_below_ma_pct / 100)
            if current < threshold:
                now = self._tm.get_current_kst_time()
                eod = now.replace(
                    hour=self._cfg.ma_break_eod_hour,
                    minute=self._cfg.ma_break_eod_minute,
                    second=0, microsecond=0,
                )
                if now >= eod:
                    reason = f"손절(20MA {ma_20d:,.0f} 장마감전 이탈, net {pnl_net:.1f}%)"
                elif not state.ma_break_since_ts:
                    state.ma_break_since_ts = now.strftime("%Y%m%d%H%M%S")
                    state_dirty = True
                else:
                    break_dt = datetime.strptime(state.ma_break_since_ts, "%Y%m%d%H%M%S").replace(tzinfo=now.tzinfo)
                    if (now - break_dt).total_seconds() / 60 >= self._cfg.ma_break_grace_minutes:
                        reason = f"손절(20MA {ma_20d:,.0f} {self._cfg.ma_break_grace_minutes}분 이탈유지, net {pnl_net:.1f}%)"
            else:
                if state.ma_break_since_ts:
                    state.ma_break_since_ts = None
                    state_dirty = True

        # 🌟 부분 익절: 직전 익절가(또는 진입가) 대비 +10% 도달 시 반복 실행 (net, P0 0-9)
        if not reason:
            ref_price = float(state.last_partial_sell_price if state.last_partial_sell_price > 0 else buy_price)
            pnl_from_ref = TransactionCostUtils.net_return_pct(ref_price, current)
            if pnl_from_ref >= self._cfg.take_profit_lower_pct:
                holding_qty = int(hold.get("qty", 1))
                sell_qty = max(1, int(holding_qty * self._cfg.partial_sell_ratio))

                if sell_qty >= holding_qty:
                    sell_qty = holding_qty
                    sell_reason = f"전량익절({pnl_from_ref:.1f}%, 잔고 {holding_qty}주)"
                else:
                    sell_reason = f"부분익절({pnl_from_ref:.1f}%, {sell_qty}주/{holding_qty}주)"

                self._logger.info({
                    "event": "partial_profit_signal",
                    "code": code, "pnl": round(pnl_from_ref, 2),
                    "sell_qty": sell_qty, "holding_qty": holding_qty,
                    "mfe_pct": state.mfe_pct, "mae_pct": state.mae_pct,
                })

                state.last_partial_sell_price = current
                state.breakeven_armed = True
                state_dirty = True

                signals.append(TradeSignal(
                    code=code, name=hold.get("name", code), action="SELL",
                    price=current, qty=sell_qty,
                    reason=sell_reason, strategy_name=self.name
                ))
                return signals, state_dirty  # 부분 매도 후 손절 체크하지 않음

        # 🛡️ 본절스탑: 부분익절 후 진입가 하회 시 잔량 전량 청산 (가격 기준, log 는 net 표시)
        if not reason and state.breakeven_armed and current < buy_price:
            reason = f"본절스탑(부분익절 후 진입가 {buy_price:,} 하회, net {pnl_net:.1f}%)"

        # 매도 시그널 생성 (손절)
        if reason:
            holding_qty = int(hold.get("qty", 1))
            self._logger.info({
                "event": "exit_signal_generated",
                "code": code, "name": hold.get("name", code),
                "reason": reason,
                "pnl_pct": round(pnl, 2),       # gross (가격 변동) — backtest MFE/MAE 와 동일 기준
                "pnl_net_pct": round(pnl_net, 2),  # P0 0-9: net (비용 반영) — trigger 비교 기준
                "pnl_basis": "net_trigger_gross_log",
                "mfe_pct": state.mfe_pct,
                "mae_pct": state.mae_pct,
            })
            self._position_state.pop(code, None)
            if "손절" in reason or "스탑" in reason:
                from datetime import date
                unblock = (date.today() + timedelta(days=self._cfg.cooldown_days)).strftime("%Y%m%d")
                self._cooldown[code] = unblock
            state_dirty = True
            signals.append(TradeSignal(
                code=code, name=hold.get("name", code), action="SELL",
                price=current, qty=holding_qty, reason=reason, strategy_name=self.name
            ))

        return signals, state_dirty

    # ── 헬퍼 ──────────────────────────────────────────────────────

    def _get_market_progress_ratio(self) -> float:
        now = self._tm.get_current_kst_time()
        open_t = self._tm.get_market_open_time()
        close_t = self._tm.get_market_close_time()
        total = (close_t - open_t).total_seconds()
        elapsed = (now - open_t).total_seconds()
        return min(elapsed / total, 1.0) if total > 0 else 0.0

    def _load_state(self):
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # 이벤트 루프 없음 → 동기 로드 (초기화 시 안전한 경로)
            if os.path.exists(self.STATE_FILE):
                try:
                    with open(self.STATE_FILE, "r") as f:
                        data = json.load(f)
                    positions = data.get("positions", data) if isinstance(data, dict) else {}
                    self._cooldown = data.get("cooldown", {}) if isinstance(data, dict) and "positions" in data else {}
                    for k, v in positions.items():
                        if k not in self._position_state:
                            self._position_state[k] = FPPositionState(**v)
                except Exception as e:
                    self._logger.error(f"Failed to load state for {self.name}: {e}")
            return
        # 이벤트 루프가 실행 중이면 비동기 태스크로 읽기
        loop.create_task(self._load_state_async())

    async def _load_state_async(self):
        try:
            data = await StrategyStateIO.load(self.STATE_FILE)
        except Exception as e:
            self._logger.error(f"Failed to load state async for {self.name}: {e}")
            return
        if data is None:
            return
        positions = data.get("positions", data) if isinstance(data, dict) else {}
        self._cooldown = data.get("cooldown", {}) if isinstance(data, dict) and "positions" in data else {}
        for k, v in positions.items():
            if k not in self._position_state:
                self._position_state[k] = FPPositionState(**v)

    async def load_state(self):
        """초기화 직후 scan 전에 호출. _load_state_async() 를 명시적으로 await.

        Idempotent: `if k not in self._position_state` 가드로 중복 호출 안전.
        """
        await self._load_state_async()

    def _save_state(self):
        """백워드 호환성 있는 동기-스케줄러 래퍼."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            # 이벤트 루프 없음 → 동기 저장
            try:
                data = {"positions": {k: asdict(v) for k, v in self._position_state.items()}, "cooldown": self._cooldown}
                # P0 0-11: atomic write (truncate-write 대체)
                write_json_atomic(self.STATE_FILE, data, indent=2, ensure_ascii=False)
            except Exception as e:
                self._logger.error(f"Failed to save state for {self.name}: {e}")
            return
        # 이벤트 루프가 존재하면 StrategyStateIO.schedule_save 로 background task
        # 등록(_pending 추적). flush_pending() 으로 graceful shutdown 시 await 가능.
        data = {"positions": {k: asdict(v) for k, v in self._position_state.items()}, "cooldown": self._cooldown}
        StrategyStateIO.schedule_save(self.STATE_FILE, data)

    async def _save_state_async(self):
        """StrategyStateIO 로 atomic write + per-file lock 저장."""
        data = {"positions": {k: asdict(v) for k, v in self._position_state.items()}, "cooldown": self._cooldown}
        try:
            await StrategyStateIO.save_atomic(self.STATE_FILE, data)
        except Exception as e:
            self._logger.error(f"Failed to save state async for {self.name}: {e}")
