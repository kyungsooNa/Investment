# strategies/oneil/breakout_strategy.py
from __future__ import annotations

import asyncio
import logging
import os
import json
from dataclasses import dataclass, asdict
from typing import List, Optional, Dict, Tuple

from interfaces.live_strategy import LiveStrategy
from common.date_utils import normalize_yyyymmdd
from common.types import TradeSignal, ErrorCode
from services.stock_query_service import StockQueryService
from core.market_clock import MarketClock
from strategies.oneil_common_types import OneilBreakoutConfig, OSBPositionState
from services.oneil_universe_service import OneilUniverseService
from core.logger import get_strategy_logger
from utils.volatility_utils import annualized_return_std
from utils.strategy_state_io import StrategyStateIO
from utils.async_concurrency import bounded_gather


# 청산/exit 동시성 상한. entry chunk_size(10)보다 높게 두어 손절/청산이 entry scan 보다
# 빠르게 마무리되도록 우선순위를 부여한다.
_EXIT_CONCURRENCY = 15


class OneilSqueezeBreakoutStrategy(LiveStrategy):
    """오닐식 스퀴즈 주도주 돌파매매 (O'Neil Squeeze Breakout).

    핵심: 시장 주도주 중 볼린저 밴드가 극도로 수축(스퀴즈)된 종목이
        거래량을 동반하며 20일 최고가를 돌파할 때 매수.
        프로그램 순매수 필터(2중 스마트 머니)로 기관 수급 확인.

    특징:
      - 유니버스 관리(종목 발굴)는 OneilUniverseService에 위임.
      - 이 클래스는 '언제 살까(돌파)'와 '언제 팔까(청산)'에만 집중.

    [v1 범위]
    - 유니버스: get_top_trading_value_stocks() → 기본 필터(거래대금/52주고가/정배열)
    - 매수/매도 뼈대 구축 및 스케줄러 연동 완료

    [v2 완료 사항]
    - 아키텍처: OneilUniverseService 완전 분리 및 메모리 캐싱 (API 중복 호출 방지)
    - 유니버스: Pool A(장 마감 후 배치) / Pool B(장중 3중 그물망 실시간 발굴) 병합
    - 스코어링: RS(3개월 상대강도 상위10% → +30점), 영업이익 25% 이상 증가 → +20점 적용
    - 마켓타이밍: ETF 프록시(KODEX 200/코스닥150) 20일선 3일 연속 우상향 로직 적용

    [v3 예정 (TODO)]
    - 스코어링 고도화: 업종 소분류 주도 (테마 대장주) 키워드 매칭 스코어링 (+20점) 추가
    - 마켓타이밍 고도화: 코스닥/코스피 지수 직접 조회 API 연동 (ETF 프록시 대체)
    - 실시간 호가창(Websocket) 연동을 통한 초단위 고래(>=5000만원) 탐지 (현재는 부하 이슈로 보류)
    """
    STATE_FILE = os.path.join("data", "osb_position_state.json")

    def __init__(
        self,
        stock_query_service: StockQueryService,
        universe_service: OneilUniverseService,
        market_clock: MarketClock,
        config: Optional[OneilBreakoutConfig] = None,
        logger: Optional[logging.Logger] = None,
        state_file: Optional[str] = None,
    ):
        self._sqs = stock_query_service
        self._universe = universe_service
        self._tm = market_clock
        self._cfg = config or OneilBreakoutConfig()
        if logger:
            self._logger = logger
        else:
            self._logger = get_strategy_logger("OneilSqueezeBreakout")

        self._position_state: Dict[str, OSBPositionState] = {}
        self._cooldown: Dict[str, str] = {}
        if state_file is not None:
            self.STATE_FILE = str(state_file)
        self._load_state()

    @property
    def name(self) -> str:
        return "오닐스퀴즈돌파"

    @property
    def strategy_id(self) -> str:
        return "oneil_squeeze_breakout"

    async def scan(self) -> List[TradeSignal]:
        signals: List[TradeSignal] = []
        self._logger.info({"event": "scan_started", "strategy_name": self.name})

        # 1. 유니버스 서비스로부터 완성된 워치리스트 획득 (캐싱됨)
        watchlist = await self._universe.get_watchlist(logger=self._logger)
        if not watchlist:
            self._logger.info({"event": "scan_skipped", "reason": "Watchlist is empty"})
            return signals

        self._logger.info({"event": "scan_with_watchlist", "count": len(watchlist)})

        # 2. 장중 경과 비율 (거래량 환산용)
        market_progress = self._get_market_progress_ratio()
        if market_progress <= 0:
            self._logger.info({"event": "scan_skipped", "reason": "Market not open or just started"})
            return signals

        # 3. 마켓 타이밍 사전 체크 (루프 내 중복 await 방지)
        market_timing = {
            "KOSPI": await self._universe.is_market_timing_ok("KOSPI", caller=self.name, logger=self._logger),
            "KOSDAQ": await self._universe.is_market_timing_ok("KOSDAQ", caller=self.name, logger=self._logger)
        }
        if not any(market_timing.values()):
            self._logger.info({"event": "scan_skipped", "reason": "Bad market timing for both markets"})
            return signals

        # 4. 종목별 돌파 체크 (청크 기반 병렬 처리, TPS 제한 대응)
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
                *[self._check_breakout(code, item, market_progress, market_timing) for code, item in chunk],
                return_exceptions=True,
            )
            for result in results:
                if isinstance(result, Exception):
                    self._logger.error(f"Scan error: {result}")
                elif result:
                    signals.append(result)

        self._logger.info({"event": "scan_finished", "signals_found": len(signals)})
        return signals

    async def _check_breakout(self, code, item, progress, market_timing_cache=None) -> Optional[TradeSignal]:
        # 1. 기본 시세 조회
        resp = await self._sqs.get_current_price(code, caller=self.name)
        if not resp or resp.rt_cd != "0": return None
        out = resp.data.get("output")
        if not out: return None

        # 데이터 추출 (dict/object 호환)
        def get_val(key, default=0):
            return int(out.get(key, default)) if isinstance(out, dict) else int(getattr(out, key, default) or 0)

        current = get_val("stck_prpr")
        vol = get_val("acml_vol")
        pg_buy = get_val("pgtr_ntby_qty")
        trade_value = get_val("acml_tr_pbmn")
        day_high = get_val("stck_hgpr", current)
        day_low = get_val("stck_lwpr", current)

        # 전 시간대 절대 하한 (baseline_min_vol_ratio, 기존 0.3 하드코딩 config화)
        if vol < item.avg_vol_20d * self._cfg.baseline_min_vol_ratio:
            return None

        # 🚨 [관문 0] Pool A 스퀴즈 런타임 검증 (Pool B 급등주는 변동성 확장 단계라 skip)
        if item.source == "pool_a":
            if item.prev_bb_width > item.bb_width_min_20d * self._cfg.osb_runtime_squeeze_tolerance:
                self._logger.debug({
                    "event": "breakout_rejected", "code": code, "reason": "not_in_squeeze",
                    "prev_bb_width": item.prev_bb_width,
                    "bb_min": item.bb_width_min_20d,
                    "tolerance": self._cfg.osb_runtime_squeeze_tolerance,
                })
                self._logger.info({
                    "event": "entry_rejected", "code": code, "name": item.name, "reason": "not_in_squeeze",
                    "prev_bb_width": item.prev_bb_width,
                    "bb_min": item.bb_width_min_20d,
                    "tolerance": self._cfg.osb_runtime_squeeze_tolerance,
                })
                return None

        # 🚨 [관문 1] 가격 돌파 — 안착 버퍼 적용 (int 캐스팅으로 호가 단위 미스매치 방지)
        breakout_threshold = int(item.high_20d * (1 + self._cfg.breakout_min_buffer_pct / 100))
        if current < breakout_threshold:
            self._logger.info({
                "event": "entry_rejected",
                "code": code,
                "name": item.name,
                "reason": "below_breakout_buffer",
                "current": current,
                "threshold": breakout_threshold,
                "high_20d": item.high_20d,
            })
            return None

        # 장 초반 15분 이내: proj_vol 뻥튀기로 인한 가짜 돌파 시그널 방지
        if progress * 390 < 15:
            self._logger.debug({"event": "breakout_skipped", "code": code, "reason": "early_morning_guard"})
            return None

        # 과확장 방어: 고점 대비 2% 이상 올라간 종목은 추격 포기
        max_entry = item.high_20d * (1 + self._cfg.osb_max_extension_pct / 100)
        if current > max_entry:
            self._logger.debug({
                "event": "breakout_rejected", "code": code, "reason": "over_extended",
                "current": current, "max_entry": int(max_entry),
            })
            self._logger.info({
                "event": "entry_rejected", "code": code, "name": item.name, "reason": "over_extended",
                "current": current, "max_entry": int(max_entry),
            })
            return None

        # 🚨 [신규 관문] 캔들 품질: 윗꼬리가 너무 길면 가짜 돌파 (상단 70% 유지 필수)
        day_range = day_high - day_low
        relative_pos = 1.0
        if day_range > 0:
            relative_pos = (current - day_low) / day_range
            if relative_pos < self._cfg.osb_min_candle_relative_pos: # 0.7 권장
                self._logger.info({"event": "entry_rejected", "code": code, "name": item.name, "reason": "poor_candle_quality", "pos": round(relative_pos, 2), "threshold": self._cfg.osb_min_candle_relative_pos})
                return None

        # 🚨 [관문 2] 다이내믹 거래량 돌파 (시간대별 허들 차등 적용)
        effective_progress = max(progress, 0.05)
        proj_vol = vol / effective_progress
        current_hour = self._tm.get_current_kst_time().hour
        if current_hour < self._cfg.morning_cutoff_hour:
            # 오전장: 예상 거래량 뻥튀기 방지 — 실거래량 절대 하한 추가 적용
            if vol < item.avg_vol_20d * self._cfg.morning_min_vol_ratio:
                self._logger.debug({
                    "event": "breakout_rejected", "code": code, "reason": "morning_low_vol",
                    "vol": vol, "threshold": int(item.avg_vol_20d * self._cfg.morning_min_vol_ratio),
                })
                return None
            vol_threshold = item.avg_vol_20d * self._cfg.volume_breakout_multiplier
        elif current_hour >= self._cfg.afternoon_cutoff_hour:
            # 오후장: 가짜 돌파 방지 — multiplier 가산
            vol_threshold = item.avg_vol_20d * (
                self._cfg.volume_breakout_multiplier + self._cfg.afternoon_volume_boost
            )
        else:
            vol_threshold = item.avg_vol_20d * self._cfg.volume_breakout_multiplier
        if proj_vol < vol_threshold:
            return None

        # 🚨 [선행 조회] 체결강도 스냅샷 (수급 판정의 재료로 사용)
        cgld_val = 0.0
        try:
            ccnl_resp = await self._sqs.get_stock_conclusion(code)
            if ccnl_resp and ccnl_resp.rt_cd == "0":
                ccnl_output = ccnl_resp.data.get("output", [])
                if ccnl_output and len(ccnl_output) > 0:
                    cgld_val = float(ccnl_output[0].get("tday_rltv") or 0.0)
        except Exception as e:
            self._logger.warning({"event": "cgld_check_failed", "code": code, "error": str(e)})

        if cgld_val < self._cfg.execution_strength_min:
            self._logger.info({
                "event": "entry_rejected", "code": code, "name": item.name,
                "reason": "low_execution_strength",
                "cgld": cgld_val, "threshold": self._cfg.execution_strength_min
            })
            return None # 🌟 여기서 걸러져야 테스트가 통과됩니다.

        # 🚨 [관문 3] 스마트 머니 + 시총 가변 허들 + 체결강도 유연화 판정
        sm_ok, sm_metrics = self._is_smart_money_ok(code, current, pg_buy, trade_value, item.market_cap, cgld_val)
        
        if not sm_ok:
            return None

        # ========= 모든 관문 통과! 매수 시그널 생성 =========
        # 1. 판정 유형 및 상세 수치 재계산 (로깅용)
        # 이제 sm_metrics에서 필요한 값을 꺼내서 씁니다.
        pass_type = sm_metrics["pass_type"]
        pg_buy_amount = sm_metrics["pg_buy_amount"]
        pg_ratio = sm_metrics["pg_to_tv_pct"]
        pg_mc_ratio = sm_metrics["pg_to_mc_pct"]
        
        vol_ratio = (proj_vol / item.avg_vol_20d * 100) if item.avg_vol_20d > 0 else 0.0
        
        # 2. 포지션 상태 저장
        self._position_state[code] = OSBPositionState(
            entry_price=current,
            entry_date=self._tm.get_current_kst_time().strftime("%Y%m%d"),
            peak_price=current,
            breakout_level=item.high_20d
        )
        self._save_state()

        # 3. 상세 사유 메시지 구성 (복기 핵심 데이터 포함)
        reason_msg = (
            f"OSB돌파({pass_type}|{current:,}>{item.high_20d:,}, "
            f"예상거래 {vol_ratio:.0f}%, "
            f"PG {pg_ratio:.1f}%/시총 {pg_mc_ratio:.2f}%, "
            f"강도 {cgld_val:.1f}%, "
            f"위치 {relative_pos:.2f})"
        )

        # 4. 정보성 로그 출력 (metrics 확장)
        self._logger.info({
            "event": "buy_signal_generated",
            "code": code, "name": item.name,
            "metrics": {
                "price": current,
                "breakout_level": item.high_20d,
                "pass_type": pass_type,
                "vol_ratio_pct": round(vol_ratio, 1),
                "pg_participation_pct": round(pg_ratio, 2),
                "pg_market_cap_pct": round(pg_mc_ratio, 3),
                "execution_strength": cgld_val,
                "candle_relative_pos": round(relative_pos, 2),
                "rs_score": item.rs_score,
                "rs_rating": item.rs_rating,
                "total_score": item.total_score,
                "market_timing": market_timing_cache.get(item.market) if market_timing_cache else None,
                "volatility_20d_annualized": item.volatility_20d_annualized,
            },
            "reason": reason_msg,
        })

        return TradeSignal(
            code=code, name=item.name, action="BUY", price=current,
            reason=reason_msg, strategy_name=self.name,
            stop_loss_pct=self._cfg.stop_loss_pct,
            volatility_20d_annualized=item.volatility_20d_annualized,
        )

    def _is_smart_money_ok(self, code: str, current: int, pg_buy: int, trade_value: int, market_cap: int, cgld_val: float) -> Tuple[bool, dict]:
        """수급 판정 결과와 계산된 메트릭을 함께 반환."""
        if pg_buy <= 0:
            return False, {}

        pg_buy_amount = pg_buy * current
        pg_to_tv_pct = (pg_buy_amount / trade_value * 100) if trade_value > 0 else 0
        pg_to_mc_pct = (pg_buy_amount / market_cap * 100) if market_cap > 0 else 0

        # 시가총액별 동적 허들 계산
        if market_cap >= 10 * 10**12: mc_threshold = 0.1
        elif market_cap >= 1 * 10**12: mc_threshold = 0.2
        else: mc_threshold = self._cfg.program_to_market_cap_pct

        is_standard = (pg_to_tv_pct >= self._cfg.program_to_trade_value_pct and pg_to_mc_pct >= mc_threshold)
        is_flexible = (pg_to_tv_pct >= self._cfg.sm_flexible_pg_ratio and 
                       cgld_val >= self._cfg.sm_flexible_execution_strength and
                       pg_to_mc_pct >= (mc_threshold * 0.7))

        # 계산된 모든 값을 딕셔너리에 담습니다.
        metrics = {
            "pg_buy_amount": pg_buy_amount,
            "pg_to_tv_pct": pg_to_tv_pct,
            "pg_to_mc_pct": pg_to_mc_pct,
            "mc_threshold": mc_threshold,
            "pass_type": "정석" if is_standard else "유연"
        }

        return (is_standard or is_flexible), metrics

    def _check_trend_break(self, code: str, current_price: int, current_vol: int, ohlcv: list) -> tuple[bool, str]:
        """추세 이탈 검사 (10일선 붕괴 + 대량 거래량 동반). ohlcv는 호출자가 미리 조회해서 전달."""
        period = self._cfg.trend_exit_ma_period  # 10일

        if not ohlcv or len(ohlcv) < period:
            return False, ""

        closes = [r.get("close", 0) for r in ohlcv if r.get("close")]
        volumes = [r.get("volume", 0) for r in ohlcv if r.get("volume")]

        # 2. 10일 이동평균선 계산
        ma_10d = sum(closes[-period:]) / period

        # 🚨 가격 조건: 현재가가 10일선을 깼는가? (안 깼으면 안전하므로 바로 리턴)
        if current_price >= ma_10d:
            return False, ""

        # 3. 거래량 조건 검증 (현재가가 10일선을 깬 상태에서만 계산)
        avg_vol_20d = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else sum(volumes) / len(volumes)

        progress = self._get_market_progress_ratio()
        effective_progress = max(progress, 0.05) # 뻥튀기 방어 (최소 5% 진행 보장)
        proj_vol = current_vol / effective_progress

        # 🚨 거래량 조건: 장중 환산(예상) 거래량이 평소 20일 평균보다 많은가? (기관 매도 징후)
        if proj_vol > avg_vol_20d:
            reason = f"추세이탈(10MA {ma_10d:,.0f} 붕괴+대량거래)"
            self._logger.warning({
                "event": "trend_break_triggered",
                "code": code,
                "price": current_price,
                "ma_10d": round(ma_10d, 0),
                "proj_vol": int(proj_vol),
                "avg_vol": int(avg_vol_20d)
            })
            return True, reason

        return False, ""

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
        ohlcv_limit = max(self._cfg.time_stop_days + 20, max(self._cfg.trend_exit_ma_period, 20))

        code = hold.get("code")
        buy_price_raw = hold.get("buy_price")
        if not code or not buy_price_raw:
            return signals, state_dirty

        buy_price = float(buy_price_raw)

        state = self._position_state.get(code)
        if not state:
            state = OSBPositionState(int(buy_price), "", int(buy_price), int(buy_price))
            self._position_state[code] = state

        resp = await self._sqs.get_current_price(code, caller=self.name)
        if not resp or resp.rt_cd != "0":
            return signals, state_dirty

        output = resp.data.get("output") if isinstance(resp.data, dict) else None
        if not output:
            return signals, state_dirty

        if isinstance(output, dict):
            current = int(output.get("stck_prpr", 0))
            current_vol = int(output.get("acml_vol", 0))
        else:
            current = int(getattr(output, "stck_prpr", 0) or 0)
            current_vol = int(getattr(output, "acml_vol", 0) or 0)

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
        exit_volatility: float | None = None  # OHLCV 조회 경로에서만 채워짐 (시간손절/추세이탈)

        # 0. 조기 부분익절: ref_price 대비 +7% 도달 시 30% 매도
        ref_price = float(state.last_partial_sell_price if state.last_partial_sell_price > 0 else buy_price)
        pnl_from_ref = float((current - ref_price) / ref_price * 100)
        if pnl_from_ref >= self._cfg.early_partial_profit_pct:
            holding_qty = int(hold.get("qty", 1))
            sell_qty = max(1, int(holding_qty * self._cfg.early_partial_sell_ratio))
            if sell_qty >= holding_qty:
                sell_qty = holding_qty
                sell_reason = f"전량익절({pnl_from_ref:.1f}%, 잔고 {holding_qty}주)"
            else:
                sell_reason = f"조기부분익절({pnl_from_ref:.1f}%, {sell_qty}주/{holding_qty}주)"
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
                reason=sell_reason, strategy_name=self.name,
            ))
            return signals, state_dirty

        # 1. 손절
        if pnl <= self._cfg.stop_loss_pct:
            reason = f"손절({pnl:.1f}%)"
        # 2. 트레일링 스탑 — 수익 게이트 적용 (peak_pnl >= trailing_min_peak_profit_pct 이후에만 발동)
        elif state.peak_price > 0:
            peak_pnl = float((state.peak_price - buy_price) / buy_price * 100)
            if peak_pnl >= self._cfg.trailing_min_peak_profit_pct:
                drop = float((current - state.peak_price) / state.peak_price * 100)
                if drop <= -self._cfg.trailing_stop_pct:
                    reason = f"트레일링스탑(고점수익 {peak_pnl:.1f}%, 낙폭 {drop:.1f}%)"

        # 2.5. 본절스탑: 부분익절 후 진입가 하회
        if not reason and state.breakeven_armed and current < buy_price:
            reason = f"본절스탑(부분익절 후 진입가 {buy_price:,} 하회 {pnl:.1f}%)"

        # 3·4. 시간손절 + 추세이탈 — OHLCV 1회 조회 후 양쪽에 전달
        if not reason:
            ohlcv_resp = await self._sqs.get_recent_daily_ohlcv(code, limit=ohlcv_limit)
            ohlcv = ohlcv_resp.data if ohlcv_resp and ohlcv_resp.rt_cd == ErrorCode.SUCCESS.value else []

            if ohlcv:
                exit_volatility = annualized_return_std(
                    [r.get("close") for r in ohlcv]
                )

            if self._check_time_stop(state, current, ohlcv):
                reason = f"시간손절({self._cfg.time_stop_days}일 횡보)"
            elif ohlcv:
                is_break, break_reason = self._check_trend_break(code, current, current_vol, ohlcv)
                if is_break:
                    reason = break_reason

        # 매도 시그널 생성
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
            if "손절" in reason or "스탑" in reason:
                from datetime import date, timedelta
                unblock = (date.today() + timedelta(days=self._cfg.cooldown_days)).strftime("%Y%m%d")
                self._cooldown[code] = unblock
            state_dirty = True
            signals.append(TradeSignal(
                code=code, name=hold.get("name", code), action="SELL",
                price=current, qty=holding_qty, reason=reason, strategy_name=self.name,
                volatility_20d_annualized=exit_volatility,
            ))

        return signals, state_dirty

    def _check_time_stop(self, state: OSBPositionState, current_price: int, ohlcv: list) -> bool:
        """시간 손절 조건 체크. ohlcv는 호출자가 미리 조회해서 전달.

        조건:
          1. 진입 후 N거래일(time_stop_days) 경과
          2. 현재가가 진입가 대비 박스권(time_stop_box_range_pct) 이내 횡보
          3. 진입 후 시세 분출 이력(peak_price 급등)이 없어야 함
        """
        if not state.entry_date or state.entry_price <= 0:
            return False

        today_str = self._tm.get_current_kst_time().strftime("%Y%m%d")
        safe_entry_date = normalize_yyyymmdd(state.entry_date)
        if not safe_entry_date:
            return False

        if safe_entry_date == today_str:
            return False

        if not ohlcv:
            return False

        trading_days = 0

        # 🌟 버그 수정: == 대신 >= 를 사용하여 하이픈 제거 및 진입일 이후 데이터 필터링
        for candle in ohlcv:
            date_str = normalize_yyyymmdd(candle.get('date', ''))
            if date_str > safe_entry_date: # 진입일 '다음 날'부터 1일로 카운트
                trading_days += 1

        # 설정된 거래일이 안 지났으면 패스
        if trading_days < self._cfg.time_stop_days:
            return False

        # 2. 횡보 또는 하락 조건 확인 (현재가가 박스권 상단 이상으로 치고 나가지 못했는가?)
        pnl_pct = float((current_price - state.entry_price) / state.entry_price * 100)

        # 🌟 버그 수정: abs() 제거. 2% 이상 '상승'한 게 아니라면 다 잘라버림 (하락 포함)
        if pnl_pct > self._cfg.time_stop_box_range_pct:
            return False

        # 3. '찍고 내려온 놈' 제외 (최고가가 진입가 대비 크게 오르지 않았어야 함)
        peak_pnl_pct = float((state.peak_price - state.entry_price) / state.entry_price * 100)
        if peak_pnl_pct > (self._cfg.time_stop_box_range_pct * 2.5):
            return False

        self._logger.info({
            "event": "time_stop_triggered",
            "entry_date": state.entry_date,
            "trading_days": trading_days,
            "pnl_pct": round(pnl_pct, 2)
        })
        return True

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
                            self._position_state[k] = OSBPositionState(**v)
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
                self._position_state[k] = OSBPositionState(**v)

    async def load_state(self):
        """초기화 직후 scan 전에 호출. _load_state_async() 를 명시적으로 await.

        Idempotent: `if k not in self._position_state` 가드로 중복 호출 안전.
        """
        await self._load_state_async()

    def _save_state(self):
        """백워드 호환성 있는 동기-스케줄러 래퍼."""
        try:
            loop = asyncio.get_running_loop()
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
        # 이벤트 루프가 존재하면 백그라운드에서 비동기 저장
        loop.create_task(self._save_state_async())

    async def _save_state_async(self):
        """StrategyStateIO 로 atomic write + per-file lock 저장."""
        data = {"positions": {k: asdict(v) for k, v in self._position_state.items()}, "cooldown": self._cooldown}
        try:
            await StrategyStateIO.save_atomic(self.STATE_FILE, data)
        except Exception as e:
            self._logger.error(f"Failed to save state async for {self.name}: {e}")
