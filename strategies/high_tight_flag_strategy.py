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
        self._load_state()

    @property
    def name(self) -> str:
        return "하이타이트플래그"

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

        candidates = [
            (code, item) for code, item in watchlist.items()
            if code not in self._position_state
            and market_timing.get(item.market, False)
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

    async def _check_breakout(self, code, item, pattern, ohlcv, progress, market_timing_cache=None) -> Optional[TradeSignal]:
        """Phase 3: 실시간 돌파 확인 (가격 + 거래량 + 체결강도)."""
        # 1. 현재가 조회
        resp = await self._sqs.get_current_price(code, caller=self.name)
        if not resp or resp.rt_cd != "0":
            return None

        out = resp.data.get("output") if isinstance(resp.data, dict) else None
        if not out:
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
            return None

        # 2. 가격 돌파: 현재가 > 40일 최고가 (옵션 A)
        pole_high = pattern["pole_high"]
        if current <= pole_high:
            # 너무 많은 로그를 피하기 위해 가격 미달 단계는 로그 생략
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
            return None
        
        # 3. 거래량 돌파: 예상거래량 >= 50일 평균 * 200%
        effective_progress = max(progress, 0.05)
        proj_vol = vol / effective_progress

        volumes = [r.get("volume", 0) for r in ohlcv if r.get("volume")]
        vol_count = min(50, len(volumes))
        if vol_count < 20:
            self._logger.debug({"event": "breakout_rejected", "code": code, "reason": "insufficient_volume_data"})
            return None
        avg_vol_50d = sum(volumes[-vol_count:]) / vol_count

        vol_threshold = avg_vol_50d * self._cfg.volume_breakout_multiplier
        if proj_vol < vol_threshold:
            vol_ratio_pct = (proj_vol / avg_vol_50d * 100) if avg_vol_50d > 0 else 0.0
            self._logger.debug({
                "event": "breakout_rejected", "code": code,
                "reason": "insufficient_projected_volume",
                "proj_vol": int(proj_vol), "threshold": int(vol_threshold),
                "vol_ratio_pct": round(vol_ratio_pct, 1),
            })
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
                "reason": "low_execution_strength",
                "cgld": cgld_val, "threshold": self._cfg.execution_strength_min
            })
            return None

        # ✅ [신규 관문] 스마트 머니 유연 판정 (수급은 유연하게!)
        # 체결강도 조회 후 _is_smart_money_ok 호출 (OSB 전략 로직 재사용)
        sm_ok, sm_metrics = self._is_smart_money_ok(code, current, pg_buy, trade_value, item.market_cap, cgld_val)
        if not sm_ok:
            self._logger.debug({"event": "breakout_rejected", "code": code, "reason": "smart_money_filter_failed"})
            return None
        
        # ========= 모든 관문 통과! 매수 시그널 생성 =========
        qty = self._calculate_qty(current)

        # 1. sm_metrics에서 상세 수치 추출 (로깅 및 분석용)
        pass_type = sm_metrics.get("pass_type", "알수없음")
        pg_ratio = sm_metrics.get("pg_to_tv_pct", 0.0)
        pg_mc_ratio = sm_metrics.get("pg_to_mc_pct", 0.0)
        pg_buy_amount = sm_metrics.get("pg_buy_amount", 0)

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
                "surge_ratio": round(pattern["surge_ratio"], 2),
                "flag_days": pattern["flag_days"],
                "drawdown_pct": round(pattern["drawdown_pct"], 1),
                "rs_score": getattr(item, "rs_score", 0.0), #
                "rs_rating": getattr(item, "rs_rating", 0), #
                "total_score": getattr(item, "total_score", 0.0),
                "market_timing": market_timing_cache.get(item.market) if market_timing_cache else None, #
            },
            "reason": reason_msg,
        })

        return TradeSignal(
            code=code, name=item.name, action="BUY", price=current, qty=qty,
            reason=reason_msg, strategy_name=self.name,
        )

    # ── check_exits ──────────────────────────────────────────────────

    async def check_exits(self, holdings: List[dict]) -> List[TradeSignal]:
        if not holdings:
            return []

        results = await asyncio.gather(
            *[self._check_single_exit(hold) for hold in holdings],
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

        pnl = float((current - buy_price) / buy_price * 100)

        # MFE / MAE 갱신
        if pnl > state.mfe_pct:
            state.mfe_pct = round(pnl, 2)
            state_dirty = True
        if pnl < state.mae_pct:
            state.mae_pct = round(pnl, 2)
            state_dirty = True

        reason = ""

        # 1. 칼손절
        if pnl <= self._cfg.stop_loss_pct:
            reason = f"칼손절({pnl:.1f}%)"

        # 2. 10일 MA 트레일링스탑
        if not reason:
            is_break, break_reason = await self._check_trailing_ma_stop(code, current)
            if is_break:
                reason = break_reason

        # 매도 시그널 생성 (전량 매도)
        if reason:
            holding_qty = int(hold.get("qty", 1))
            self._logger.info({
                "event": "exit_signal_generated",
                "code": code, "name": hold.get("name", code),
                "reason": reason,
                "pnl_pct": round(pnl, 2),
                "mfe_pct": state.mfe_pct,
                "mae_pct": state.mae_pct,
            })
            self._position_state.pop(code, None)
            state_dirty = True
            signals.append(TradeSignal(
                code=code, name=hold.get("name", code), action="SELL",
                price=current, qty=holding_qty, reason=reason, strategy_name=self.name,
            ))

        return signals, state_dirty

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

        # 2. 판정 로직
        # 정석: 비중 10% 이상 & 시총 허들 통과
        is_standard = (pg_to_tv_pct >= 10.0 and pg_to_mc_pct >= mc_threshold)
        
        # 유연: 비중 7% 이상 & 체결강도가 압도적(150%↑) & 시총 허들의 70% 통과
        is_flexible = (pg_to_tv_pct >= self._cfg.sm_flexible_pg_ratio and 
                    cgld_val >= self._cfg.sm_flexible_execution_strength and
                    pg_to_mc_pct >= (mc_threshold * 0.7))

        metrics = {
            "pg_buy_amount": pg_buy_amount,
            "pg_to_tv_pct": pg_to_tv_pct,
            "pg_to_mc_pct": pg_to_mc_pct,
            "mc_threshold": mc_threshold,
            "pass_type": "정석" if is_standard else "유연"
        }

        return (is_standard or is_flexible), metrics
    
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
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # 이벤트 루프 없음 → 동기 로드 (초기화 시 안전한 경로)
            if os.path.exists(self.STATE_FILE):
                try:
                    with open(self.STATE_FILE, "r") as f:
                        data = json.load(f)
                    for k, v in data.items():
                        if k not in self._position_state:
                            self._position_state[k] = HTFPositionState(**v)
                except Exception as e:
                    self._logger.error(f"Failed to load state for {self.name}: {e}")
            return
        # 이벤트 루프가 실행 중이면 비동기 태스크로 읽기
        loop.create_task(self._load_state_async())

    async def _load_state_async(self):
        if not os.path.exists(self.STATE_FILE):
            return
        try:
            def _read_file():
                with open(self.STATE_FILE, "r") as f:
                    return json.load(f)

            data = await asyncio.to_thread(_read_file)
            for k, v in data.items():
                if k not in self._position_state:
                    self._position_state[k] = HTFPositionState(**v)
        except Exception as e:
            self._logger.error(f"Failed to load state async for {self.name}: {e}")

    def _save_state(self):
        """백워드 호환성 있는 동기-스케줄러 래퍼."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # 이벤트 루프 없음 → 동기 저장
            try:
                os.makedirs(os.path.dirname(self.STATE_FILE), exist_ok=True)
                data = {k: asdict(v) for k, v in self._position_state.items()}
                with open(self.STATE_FILE, "w") as f:
                    json.dump(data, f, indent=2)
            except Exception as e:
                self._logger.error(f"Failed to save state for {self.name}: {e}")
            return
        # 이벤트 루프가 존재하면 백그라운드에서 비동기 저장
        loop.create_task(self._save_state_async())

    async def _save_state_async(self):
        """비동기 방식으로 상태 파일을 저장합니다 (파일 I/O는 스레드로 오프로드)."""
        try:
            def _write_file(data):
                os.makedirs(os.path.dirname(self.STATE_FILE), exist_ok=True)
                with open(self.STATE_FILE, "w") as f:
                    json.dump(data, f, indent=2)

            data = {k: asdict(v) for k, v in self._position_state.items()}
            await asyncio.to_thread(_write_file, data)
        except Exception as e:
            self._logger.error(f"Failed to save state async for {self.name}: {e}")
