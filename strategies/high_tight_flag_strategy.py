# strategies/high_tight_flag_strategy.py
from __future__ import annotations

import asyncio
import logging
import os
import json
from dataclasses import asdict
from typing import List, Optional, Dict, Tuple

from interfaces.live_strategy import LiveStrategy
from common.types import TradeSignal
from services.stock_query_service import StockQueryService
from core.market_clock import MarketClock
from strategies.oneil_common_types import HTFConfig, HTFPositionState
from services.oneil_universe_service import OneilUniverseService
from core.logger import get_strategy_logger
from utils.async_concurrency import bounded_gather
from utils.strategy_state_io import StrategyStateIO
from utils.transaction_cost_utils import TransactionCostUtils


# 청산/exit 동시성 상한. entry chunk_size(10)보다 높게 두어 손절/청산이 entry scan 보다
# 빠르게 마무리되도록 우선순위를 부여한다.
_EXIT_CONCURRENCY = 15


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
        market_clock: MarketClock,
        config: Optional[HTFConfig] = None,
        logger: Optional[logging.Logger] = None,
        state_file: Optional[str] = None,
    ):
        self._sqs = stock_query_service
        self._universe = universe_service
        self._tm = market_clock
        self._cfg = config or HTFConfig()
        if logger:
            self._logger = logger
        else:
            self._logger = get_strategy_logger("HighTightFlag", sub_dir="oneil")

        self._position_state: Dict[str, HTFPositionState] = {}
        self._cooldown: Dict[str, str] = {}
        if state_file is not None:
            self.STATE_FILE = str(state_file)
        self._load_state()

    @property
    def name(self) -> str:
        return "하이타이트플래그"

    @property
    def strategy_id(self) -> str:
        return "high_tight_flag"

    def _log_entry_rejected(self, code: str, item, reason: str, **metrics) -> None:
        payload = {
            "event": "entry_rejected",
            "code": code,
            "name": getattr(item, "name", code),
            "reason": reason,
        }
        payload.update(metrics)
        self._logger.info(payload)

    # ── scan ──────────────────────────────────────────────────────────

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
        for i in range(0, len(candidates), 10):
            chunk = candidates[i:i + 10]
            results = await asyncio.gather(
                *[self._check_htf_setup(code, item, market_progress, market_timing) for code, item in chunk],
                return_exceptions=True,
            )
            for result in results:
                if isinstance(result, Exception):
                    self._logger.error(f"Scan error: {result}")
                elif result:
                    signals.append(result)

        self._logger.info({"event": "scan_finished", "signals_found": len(signals)})
        return signals

    async def _check_htf_setup(self, code, item, progress, market_timing_cache=None) -> Optional[TradeSignal]:
        """HTF 패턴 감지 + 실시간 돌파 확인."""
        # 1. OHLCV 조회 (깃대 40일 + 깃발 최대 25일 = 65일)
        ohlcv_resp = await self._sqs.get_recent_daily_ohlcv(code, limit=65)
        ohlcv = ohlcv_resp.data if ohlcv_resp and ohlcv_resp.rt_cd == "0" else []
        if not ohlcv or len(ohlcv) < self._cfg.pole_lookback_days:
            self._log_entry_rejected(
                code,
                item,
                "ohlcv_unavailable",
                ohlcv_count=len(ohlcv) if ohlcv else 0,
                threshold=self._cfg.pole_lookback_days,
            )
            return None

        # 2. Phase 1+2: 깃대·깃발 패턴 감지 (순수 계산)
        pattern = self._detect_pole_and_flag(ohlcv)
        if not pattern:
            self._log_entry_rejected(code, item, "pattern_not_detected")
            return None

        self._logger.info({
            "event": "htf_pattern_detected",
            "code": code, "name": item.name,
            "surge_ratio": round(pattern["surge_ratio"], 2),
            "flag_days": pattern["flag_days"],
            "drawdown_pct": round(pattern["drawdown_pct"], 1),
        })

        # 3. Phase 3: 실시간 돌파 확인
        return await self._check_breakout(code, item, pattern, ohlcv, progress, market_timing_cache)

    def _detect_pole_and_flag(self, ohlcv: list) -> Optional[dict]:
        """Phase 1+2: 깃대 폭등 + 깃발 횡보 패턴 감지 (순수 계산, API 호출 없음).

        Returns:
            dict with pole_high, surge_ratio, flag_days, drawdown_pct or None
        """
        highs = [r.get("high", 0) for r in ohlcv]
        lows = [r.get("low", 0) for r in ohlcv]
        volumes = [r.get("volume", 0) for r in ohlcv]
        n = len(ohlcv)

        # 최고점 탐색: flag_min_days 이전 범위로 제한 (flag 구간 내 오탐 방지)
        search_end = n - self._cfg.flag_min_days
        if search_end <= 0:
            return None
        peak_high = max(highs[:search_end])
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

        # VCP 타이트함: 깃발 최근 N일 평균 변동폭이 깃대 평균 변동폭 대비 충분히 축소되었는지
        pole_highs = highs[pole_start:peak_idx + 1]
        pole_range_avg = (
            sum(h - l for h, l in zip(pole_highs, pole_lows)) / len(pole_lows)
            if pole_lows else 0.0
        )
        recent_n = min(self._cfg.vcp_recent_flag_days, flag_days)
        flag_highs = highs[peak_idx + 1:]
        flag_lows = lows[peak_idx + 1:]
        recent_flag_range_avg = (
            sum(h - l for h, l in zip(flag_highs[-recent_n:], flag_lows[-recent_n:])) / recent_n
            if recent_n > 0 else 0.0
        )
        vcp_tightness_ratio = (
            recent_flag_range_avg / pole_range_avg if pole_range_avg > 0 else 0.0
        )
        if (
            self._cfg.vcp_tightness_check_enabled
            and pole_range_avg > 0
            and vcp_tightness_ratio > self._cfg.vcp_max_tightness_ratio
        ):
            return None

        # 깃발 기간 20MA 지지: 깃발 각 일의 종가가 그 시점 20MA 대비 min_pct 이하로 떨어진 적이 없는지
        ma20_min_deviation_pct = 0.0
        if self._cfg.ma20_support_check_enabled:
            for flag_idx in range(peak_idx + 1, n):
                window_start = flag_idx - 19
                if window_start < 0:
                    continue
                window = closes[window_start:flag_idx + 1]
                ma20 = sum(window) / len(window)
                if ma20 <= 0:
                    continue
                deviation_pct = (closes[flag_idx] - ma20) / ma20 * 100
                if deviation_pct < ma20_min_deviation_pct:
                    ma20_min_deviation_pct = deviation_pct
                if deviation_pct < self._cfg.ma20_support_min_pct:
                    return None

        return {
            "pole_high": peak_high,
            "pole_low": pole_low,
            "surge_ratio": surge_ratio,
            "flag_days": flag_days,
            "drawdown_pct": drawdown_pct,
            "avg_vol_50d": avg_vol_50d,
            "flag_avg_vol": flag_avg_vol,
            "pole_range_avg": pole_range_avg,
            "recent_flag_range_avg": recent_flag_range_avg,
            "vcp_tightness_ratio": vcp_tightness_ratio,
            "ma20_min_deviation_pct": ma20_min_deviation_pct,
        }

    async def _check_breakout(self, code, item, pattern, ohlcv, progress, market_timing_cache=None) -> Optional[TradeSignal]:
        """Phase 3: 실시간 돌파 확인 (가격 + 거래량 + 체결강도)."""
        # 1. 현재가 조회
        resp = await self._sqs.get_current_price(code, caller=self.name)
        if not resp or resp.rt_cd != "0":
            self._log_entry_rejected(code, item, "price_unavailable")
            return None

        out = resp.data.get("output") if isinstance(resp.data, dict) else None
        if not out:
            self._log_entry_rejected(code, item, "price_unavailable")
            return None

        if isinstance(out, dict):
            current = int(out.get("stck_prpr", 0))
            vol = int(out.get("acml_vol", 0))
            pg_buy = int(out.get("pgtr_ntby_qty", 0))
            trade_value = int(out.get("acml_tr_pbmn", 0))
            day_high = int(out.get("stck_hgpr", 0))
            day_low = int(out.get("stck_lwpr", 0))
        else:
            current = int(getattr(out, "stck_prpr", 0) or 0)
            vol = int(getattr(out, "acml_vol", 0) or 0)
            pg_buy = int(getattr(out, "pgtr_ntby_qty", 0) or 0)
            trade_value = int(getattr(out, "acml_tr_pbmn", 0) or 0)
            day_high = int(getattr(out, "stck_hgpr", 0) or 0)
            day_low = int(getattr(out, "stck_lwpr", 0) or 0)

        if current <= 0:
            self._log_entry_rejected(code, item, "invalid_current_price", current=current)
            return None

        # 장 초반 15분 이내: proj_vol 뻥튀기로 인한 가짜 돌파 시그널 방지
        if progress * 390 < 15:
            self._logger.debug({
                "event": "breakout_skipped",
                "code": code,
                "name": item.name,
                "reason": "early_morning_guard",
                "retry_after_guard": True,
            })
            self._log_entry_rejected(code, item, "early_morning_guard")
            return None

        # 2. 가격 돌파: pole_high 기준 진입 밴드 확인 (옵션 A + 과확장 방어)
        pole_high = pattern["pole_high"]
        min_entry = pole_high * (1 + self._cfg.breakout_min_buffer_pct / 100)
        max_entry = pole_high * (1 + self._cfg.breakout_max_extension_pct / 100)
        if current < min_entry or current > max_entry:
            self._logger.debug({
                "event": "breakout_rejected", "code": code,
                "reason": "out_of_entry_band",
                "current": current, "band": [int(min_entry), int(max_entry)],
            })
            self._log_entry_rejected(
                code,
                item,
                "out_of_entry_band",
                current=current,
                band=[int(min_entry), int(max_entry)],
            )
            return None

        # 2-1. 돌파 확인 지연: pole_high 위 임계가에 분봉 종가가 N분 연속 유지되는지
        if self._cfg.breakout_hold_check_enabled:
            minutes = await self._sqs.get_day_intraday_minutes_list(code, session="REGULAR")
            n_check = self._cfg.breakout_hold_minutes
            if not minutes or len(minutes) < n_check:
                self._log_entry_rejected(
                    code, item, "breakout_hold_insufficient_data",
                    minute_count=len(minutes) if minutes else 0,
                    required=n_check,
                )
                return None
            hold_threshold = pole_high * (1 + self._cfg.breakout_hold_min_pct / 100)
            recent_prices = [int(m.get("stck_prpr", 0) or 0) for m in minutes[-n_check:]]
            min_recent = min(recent_prices) if recent_prices else 0
            if min_recent < hold_threshold:
                self._log_entry_rejected(
                    code, item, "breakout_hold_failed",
                    threshold=int(hold_threshold),
                    min_recent_price=min_recent,
                    window_minutes=n_check,
                )
                return None

        # 🚨 [관문 2] 캔들 품질 검증 (Strict Quality!)
        day_range = day_high - day_low
        # 상대적 위치 계산: (현재가 - 저가) / (고가 - 저가)
        relative_pos = (current - day_low) / day_range if day_range > 0 else 1.0
        
        if relative_pos < self._cfg.min_candle_relative_pos:
            self._logger.debug({
                "event": "breakout_rejected", 
                "code": code, 
                "reason": "poor_candle_quality", 
                "pos": round(relative_pos, 2),
                "threshold": self._cfg.min_candle_relative_pos
            })
            self._log_entry_rejected(
                code,
                item,
                "poor_candle_quality",
                pos=round(relative_pos, 2),
                threshold=self._cfg.min_candle_relative_pos,
            )
            return None
        
        # 3. 거래량 돌파: 예상거래량 >= 50일 평균 * 200%
        effective_progress = max(progress, 0.05)
        proj_vol = vol / effective_progress

        volumes = [r.get("volume", 0) for r in ohlcv if r.get("volume")]
        vol_count = min(50, len(volumes))
        if vol_count < 20:
            self._logger.debug({"event": "breakout_rejected", "code": code, "reason": "insufficient_volume_data"})
            self._log_entry_rejected(code, item, "insufficient_volume_data", vol_count=vol_count)
            return None
        avg_vol_50d = sum(volumes[-vol_count:]) / vol_count

        now_hour = self._tm.get_current_kst_time().hour
        multiplier = (
            self._cfg.afternoon_volume_multiplier
            if now_hour >= self._cfg.afternoon_cutoff_hour
            else self._cfg.volume_breakout_multiplier
        )
        vol_threshold = avg_vol_50d * multiplier
        if proj_vol < vol_threshold:
            vol_ratio_pct = (proj_vol / avg_vol_50d * 100) if avg_vol_50d > 0 else 0.0
            self._logger.debug({
                "event": "breakout_rejected", "code": code,
                "reason": "insufficient_projected_volume",
                "proj_vol": int(proj_vol), "threshold": int(vol_threshold),
                "vol_ratio_pct": round(vol_ratio_pct, 1),
            })
            self._log_entry_rejected(
                code,
                item,
                "insufficient_projected_volume",
                proj_vol=int(proj_vol),
                threshold=int(vol_threshold),
                vol_ratio_pct=round(vol_ratio_pct, 1),
            )
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
            self._log_entry_rejected(code, item, "cgld_check_failed", error=str(e))
            return None

        if cgld_val < self._cfg.execution_strength_min:
            self._logger.info({
                "event": "breakout_rejected", "code": code, "name": item.name,
                "reason": "low_execution_strength",
                "cgld": cgld_val, "threshold": self._cfg.execution_strength_min
            })
            self._log_entry_rejected(
                code,
                item,
                "low_execution_strength",
                cgld=cgld_val,
                threshold=self._cfg.execution_strength_min,
            )
            return None

        # ✅ [신규 관문] 스마트 머니 유연 판정 (수급은 유연하게!)
        # 체결강도 조회 후 _is_smart_money_ok 호출 (OSB 전략 로직 재사용)
        sm_ok, sm_metrics = self._is_smart_money_ok(code, current, pg_buy, trade_value, item.market_cap, cgld_val)
        if not sm_ok:
            self._logger.info({"event": "breakout_rejected", "code": code, "name": item.name, "reason": "smart_money_filter_failed"})
            self._log_entry_rejected(code, item, "smart_money_filter_failed", **sm_metrics)
            return None
        
        # ========= 모든 관문 통과! 매수 시그널 생성 =========

        # 1. sm_metrics에서 상세 수치 추출 (로깅 및 분석용)
        pass_type = sm_metrics.get("pass_type", "알수없음")
        pg_ratio = sm_metrics.get("pg_to_tv_pct", 0.0)
        pg_mc_ratio = sm_metrics.get("pg_to_mc_pct", 0.0)
        pg_buy_amount = sm_metrics.get("pg_buy_amount", 0)

        # 이격도: 매수 시점 가격이 pole_high 대비 몇 % 이격되었는지 (과확장 사후 분석용)
        deviation_from_pole_high_pct = (current / pole_high - 1.0) * 100 if pole_high > 0 else 0.0

        # 2. 포지션 상태 저장
        self._position_state[code] = HTFPositionState(
            entry_price=current,
            entry_date=self._tm.get_current_kst_time().strftime("%Y%m%d"),
            peak_price=current,
            pole_high=pole_high,
        )
        self._save_state() #

        # 3. 상세 사유 메시지 구성 (가독성 중심)
        # OSB 전략과 일관성을 유지하면서 HTF 특유의 패턴 정보(폭등비율, 깃발기간)를 포함합니다.
        vol_ratio = (proj_vol / avg_vol_50d * 100) if avg_vol_50d > 0 else 0.0

        reason_msg = (
            f"HTF돌파({pass_type}|{current:,}>{pole_high:,}, "
            f"예상거래 {vol_ratio:.0f}%, "
            f"PG {pg_ratio:.1f}%/시총 {pg_mc_ratio:.2f}%, "
            f"강도 {cgld_val:.1f}%, "
            f"위치 {relative_pos:.2f}, "  # 캔들 품질 추가
            f"폭등 {pattern['surge_ratio']:.1f}x, "
            f"깃발 {pattern['flag_days']}일)"
        )
        stop_loss_price = current * (1 + self._cfg.stop_loss_pct / 100)
        target_price = current * (1 + self._cfg.partial_profit_trigger_pct / 100)

        # 4. 정보성 로그 출력 (구조화된 metrics 데이터)
        self._logger.info({
            "event": "buy_signal_generated",
            "code": code, "name": item.name,
            "metrics": {
                "price": current,
                "pole_high": pole_high,
                "pass_type": pass_type,           # 정석 vs 유연
                "vol_ratio_pct": round(vol_ratio, 1),
                "pg_participation_pct": round(pg_ratio, 2),
                "pg_market_cap_pct": round(pg_mc_ratio, 3),
                "execution_strength": cgld_val,
                "candle_relative_pos": round(relative_pos, 2),
                "deviation_from_pole_high_pct": round(deviation_from_pole_high_pct, 2),
                "vcp_tightness_ratio": round(pattern.get("vcp_tightness_ratio", 0.0), 3),
                "ma20_min_deviation_pct": round(pattern.get("ma20_min_deviation_pct", 0.0), 2),
                "surge_ratio": round(pattern["surge_ratio"], 2),
                "flag_days": pattern["flag_days"],
                "drawdown_pct": round(pattern["drawdown_pct"], 1),
                "rs_score": getattr(item, "rs_score", 0.0), #
                "rs_rating": getattr(item, "rs_rating", 0), #
                "total_score": getattr(item, "total_score", 0.0),
                "market_timing": market_timing_cache.get(item.market) if market_timing_cache else None, #
                "volatility_20d_annualized": getattr(item, "volatility_20d_annualized", None),
            },
            "reason": reason_msg,
        })

        return TradeSignal(
            code=code, name=item.name, action="BUY", price=current,
            reason=reason_msg, strategy_name=self.name,
            stop_loss_pct=self._cfg.stop_loss_pct,
            entry_reason="high_tight_flag_breakout",
            invalidation_price=round(stop_loss_price, 2),
            stop_loss_price=round(stop_loss_price, 2),
            target_price=round(target_price, 2),
            trailing_rule=f"{self._cfg.trailing_ma_period}ma_or_peak_drop",
            expected_holding_period_days=max(self._cfg.flag_min_days, self._cfg.trailing_ma_period),
            confidence=min(1.0, max(0.0, cgld_val / max(self._cfg.execution_strength_min, 1.0))),
            required_data=[
                "current_price",
                "daily_ohlcv",
                "pole_and_flag_pattern",
                "projected_volume",
                "program_buy",
                "execution_strength",
            ],
            volatility_20d_annualized=getattr(item, "volatility_20d_annualized", None),
        )

    # ── check_exits ──────────────────────────────────────────────────

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

        buy_price = float(buy_price_raw)

        state = self._position_state.get(code)
        if not state:
            state = HTFPositionState(int(buy_price), "", int(buy_price), int(buy_price))
            self._position_state[code] = state

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

        # 가격 변동 비율 (gross) — MFE/MAE/log 추적용. backtest 의 _bar_excursion 과 동일 기준.
        pnl = float((current - buy_price) / buy_price * 100)
        # P0 0-9: stop/익절 trigger 비교는 비용 반영 net 기준 — backtest 와 동일.
        pnl_net = TransactionCostUtils.net_return_pct(buy_price, current)

        # MFE / MAE 갱신 (가격 변동 = gross)
        if pnl > state.mfe_pct:
            state.mfe_pct = round(pnl, 2)
            state_dirty = True
        if pnl < state.mae_pct:
            state.mae_pct = round(pnl, 2)
            state_dirty = True

        reason = ""

        # 1. 칼손절 (net, P0 0-9)
        if pnl_net <= self._cfg.stop_loss_pct:
            reason = f"칼손절(net {pnl_net:.1f}%)"

        # 2. 부분 익절: ref_price 대비 +20% 도달 시 반복 실행 (net, P0 0-9)
        if not reason:
            ref_price = float(state.last_partial_sell_price if state.last_partial_sell_price > 0 else buy_price)
            pnl_from_ref = TransactionCostUtils.net_return_pct(ref_price, current)
            if pnl_from_ref >= self._cfg.partial_profit_trigger_pct:
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
                })
                state.last_partial_sell_price = current
                state.breakeven_armed = True
                state_dirty = True
                signals.append(TradeSignal(
                    code=code, name=hold.get("name", code), action="SELL",
                    price=current, qty=sell_qty,
                    reason=sell_reason, strategy_name=self.name,
                ))
                return signals, state_dirty

        # 3. 본절스탑: 부분익절 후 진입가 하회 시 잔량 전량 청산 (가격 기준 trigger, log 는 net 표시)
        if not reason and state.breakeven_armed and current < buy_price:
            reason = f"본절스탑(부분익절 후 진입가 {buy_price:,} 하회, net {pnl_net:.1f}%)"

        # 4. 5일 MA 트레일링스탑 + 고점 낙폭 -8%
        if not reason:
            is_break, break_reason = await self._check_trailing_ma_stop(code, current, state)
            if is_break:
                reason = break_reason

        # 매도 시그널 생성 (전량 매도)
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
                from datetime import date, timedelta
                unblock = (date.today() + timedelta(days=self._cfg.cooldown_days)).strftime("%Y%m%d")
                self._cooldown[code] = unblock
            state_dirty = True
            signals.append(TradeSignal(
                code=code, name=hold.get("name", code), action="SELL",
                price=current, qty=holding_qty, reason=reason, strategy_name=self.name,
            ))

        return signals, state_dirty

    async def _check_trailing_ma_stop(self, code: str, current_price: int, state: HTFPositionState) -> tuple:
        """5일 MA 또는 고점 대비 -8% 트레일링스탑 체크 (둘 중 하나라도 해당 시 청산)."""
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
            if state.pole_high > 0 and current_price >= state.pole_high * 0.99:
                return False, ""
            return True, f"트레일링스탑({period}MA {ma:,.0f} 하향이탈)"

        if state.peak_price > 0:
            peak_drop_pct = (current_price - state.peak_price) / state.peak_price * 100
            if peak_drop_pct <= self._cfg.trailing_peak_drop_pct:
                return True, f"고점낙폭스탑(고점{state.peak_price:,} 대비 {peak_drop_pct:.1f}%)"

        return False, ""

    # ── 헬퍼 ─────────────────────────────────────────────────────────
    def _is_smart_money_ok(self, code: str, current: int, pg_buy: int, trade_value: int, market_cap: int, cgld_val: float) -> Tuple[bool, dict]:
        """
        HTF 전략용 스마트 머니(프로그램 수급) 필터.
        품질은 엄격하게 보되, 압도적인 에너지가 확인되면 수급 수치를 유연하게 적용합니다.
        """
        if pg_buy <= 0:
            return False, {}

        pg_buy_amount = pg_buy * current
        pg_to_tv_pct = (pg_buy_amount / trade_value * 100) if trade_value > 0 else 0
        pg_to_mc_pct = (pg_buy_amount / market_cap * 100) if market_cap > 0 else 0

        # 1. 시가총액별 동적 허들 (OSB 전략 기준 준용)
        if market_cap >= 10 * 10**12:      # 10조 이상
            mc_threshold = 0.1
        elif market_cap >= 1 * 10**12:     # 1조 이상
            mc_threshold = 0.2
        else:                              # 1조 미만 (중소형주)
            mc_threshold = self._cfg.program_to_market_cap_pct # 기본값 0.3~0.5%

        # 판정 로직 — HTF는 정석 판정만 인정 (유연화 제거)
        # 정석: 비중 10% 이상 & 시총 허들 통과
        is_standard = (pg_to_tv_pct >= 10.0 and pg_to_mc_pct >= mc_threshold)

        metrics = {
            "pg_buy_amount": pg_buy_amount,
            "pg_to_tv_pct": pg_to_tv_pct,
            "pg_to_mc_pct": pg_to_mc_pct,
            "mc_threshold": mc_threshold,
            "pass_type": "정석",
        }

        return is_standard, metrics
    
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
                            self._position_state[k] = HTFPositionState(**v)
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
                self._position_state[k] = HTFPositionState(**v)

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
                os.makedirs(os.path.dirname(self.STATE_FILE), exist_ok=True)
                data = {"positions": {k: asdict(v) for k, v in self._position_state.items()}, "cooldown": self._cooldown}
                with open(self.STATE_FILE, "w") as f:
                    json.dump(data, f, indent=2)
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
