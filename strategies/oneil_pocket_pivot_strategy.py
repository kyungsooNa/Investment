# strategies/oneil/pocket_pivot_strategy.py
from __future__ import annotations

import asyncio
import logging
import os
import json
from dataclasses import dataclass, asdict
from datetime import timedelta
from typing import List, Optional, Dict, Tuple

from interfaces.live_strategy import LiveStrategy
from common.date_utils import normalize_yyyymmdd, previous_trading_day_str
from common.types import TradeSignal, ErrorCode
from services.stock_query_service import StockQueryService
from core.market_clock import MarketClock
from strategies.oneil_common_types import OneilPocketPivotConfig, PPPositionState
from services.oneil_universe_service import OneilUniverseService
from core.logger import get_strategy_logger
from utils.strategy_state_io import StrategyStateIO


class OneilPocketPivotStrategy(LiveStrategy):
    """오닐식 포켓 피봇 & BGU 매매 (O'Neil Pocket Pivot & Buyable Gap-Up).

    핵심: 시장 주도주 중 이동평균선 근처에서 기관의 숨은 매집(포켓 피봇)을 포착해
        선취매하거나, 강력한 호재로 인한 폭발적 갭상승(BGU) 초입에 올라탄다.

    진입 조건:
      [공통 필터] 스마트머니(PG순매수 비율) + 체결강도(>=120%) 스냅샷
      [조건 A] Pocket Pivot: MA 근접(-2%~+4%) + 환산 거래량 > 하락일 최대 거래량
      [조건 B] BGU: 갭 >=4% + 환산 거래량 >= 50일 평균 300% + 09:10 이후 시가 지지

    청산 조건 (우선순위):
      1. 하드 스탑: 마켓타이밍 악화 OR 고점 대비 -10%
      2. PP 손절: 지지MA -2% 이탈 / BGU 손절: 갭업 당일 저가 이탈
      3. 부분 익절: +15% 시 50% 매도 (잔고 1주면 전량)
      4. 7주 룰: +5% 안착 후 35거래일 경과 & 50MA 이탈 시 전량 청산
    """
    STATE_FILE = os.path.join("data", "pp_position_state.json")

    def __init__(
        self,
        stock_query_service: StockQueryService,
        universe_service: OneilUniverseService,
        market_clock: MarketClock,
        config: Optional[OneilPocketPivotConfig] = None,
        logger: Optional[logging.Logger] = None,
        state_file: Optional[str] = None,
    ):
        self._sqs = stock_query_service
        self._universe = universe_service
        self._tm = market_clock
        self._cfg = config or OneilPocketPivotConfig()
        if logger:
            self._logger = logger
        else:
            self._logger = get_strategy_logger("OneilPocketPivot")

        self._position_state: Dict[str, PPPositionState] = {}
        self._cooldown: Dict[str, str] = {}
        if state_file is not None:
            self.STATE_FILE = str(state_file)
        self._load_state()

    @property
    def name(self) -> str:
        return "오닐PP/BGU"

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

        # 3. 마켓 타이밍 사전 체크
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
        """진입 조건 검사: PP 또는 BGU → 스마트머니 → 체결강도."""
        # 1. 현재가 데이터 조회
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
            today_open = int(out.get("stck_oprc", 0))
            today_high = int(out.get("stck_hgpr", 0))
            today_low = int(out.get("stck_lwpr", 0))
            prdy_vrss = int(out.get("prdy_vrss", 0))
            prdy_vrss_sign = str(out.get("prdy_vrss_sign", "3"))
        else:
            current = int(getattr(out, "stck_prpr", 0) or 0)
            vol = int(getattr(out, "acml_vol", 0) or 0)
            pg_buy = int(getattr(out, "pgtr_ntby_qty", 0) or 0)
            trade_value = int(getattr(out, "acml_tr_pbmn", 0) or 0)
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

        if current <= 0 or prev_close <= 0:
            return None

        # 2. OHLCV: 어제까지 확정 데이터(캐시) + 오늘 캔들(현재가로 합성)
        now = self._tm.get_current_kst_time()
        yesterday_str = previous_trading_day_str(now)
        ohlcv_resp = await self._sqs.get_recent_daily_ohlcv(code, limit=60, end_date=yesterday_str)
        ohlcv = ohlcv_resp.data if ohlcv_resp and ohlcv_resp.rt_cd == "0" else []

        today_str = now.strftime("%Y%m%d")
        today_candle = {
            "date": today_str,
            "open": float(today_open),
            "high": float(today_high),
            "low": float(today_low),
            "close": float(current),
            "volume": vol,
        }
        if ohlcv and ohlcv[-1].get("date") == today_str:
            ohlcv[-1] = today_candle
        else:
            ohlcv.append(today_candle)

        if len(ohlcv) < 10:
            return None

        # 3. 조건 A (Pocket Pivot) 시도
        entry_result = self._check_pocket_pivot(
            code, current, vol, progress, ohlcv, item, prev_close
        )

        # 4. 조건 B (BGU) 시도
        if not entry_result:
            entry_result = self._check_bgu(
                code, current, vol, progress, ohlcv, today_open, today_low, prev_close,
                pg_buy=pg_buy, trade_value=trade_value,
            )

        if not entry_result:
            return None

        entry_type, supporting_ma, gap_day_low, extra_info = entry_result

        # 5. ★ 체결강도 스냅샷 (>=120%) — 스마트머니 유연 조건(cgld 연동)에 필요해 먼저 조회
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
            self._logger.info({"event": "entry_rejected", "code": code, "name": item.name, "reason": "low_execution_strength", "entry_type": entry_type, "cgld": cgld_val, "threshold": self._cfg.execution_strength_min})
            return None

        # 6. ★ 공통 스마트 머니 필터 (cgld_val 전달로 유연 조건 활성화)
        if not self._check_smart_money(code, current, pg_buy, trade_value, item.market_cap, cgld_val):
            self._logger.debug({"event": "entry_rejected_by_smart_money", "code": code, "entry_type": entry_type})
            return None

        # ========= 모든 관문 통과! 매수 시그널 생성 =========
        qty = self._calculate_qty(current)
        pg_buy_amount = pg_buy * current
        pg_ratio = (pg_buy_amount / trade_value * 100) if trade_value > 0 else 0.0

        entry_pg_buy_amount = pg_buy * current
        self._position_state[code] = PPPositionState(
            entry_type=entry_type,
            entry_price=current,
            entry_date=self._tm.get_current_kst_time().strftime("%Y%m%d"),
            peak_price=current,
            supporting_ma=supporting_ma,
            gap_day_low=gap_day_low,
            entry_pg_buy_amount=entry_pg_buy_amount,
            entry_cgld=cgld_val,
        )
        # 즉시 파일을 보장하려면 await 가능한 호출자가 _save_state_async 를 사용합니다.
        try:
            await self._save_state_async()
        except Exception as e:
            self._logger.error(f"Failed to save state async for {self.name}: {e}")

        if entry_type == "PP":
            proj_vol = extra_info.get("proj_vol", 0)
            max_down_vol = extra_info.get("max_down_vol", 0)
            vol_ratio = (proj_vol / max_down_vol * 100) if max_down_vol > 0 else 0.0
            reason_msg = (
                f"PP진입({supporting_ma}MA지지, "
                f"예상거래 {vol_ratio:.0f}%(하락최대대비), "
                f"PG매수 {pg_buy_amount // 100_000_000:,}억({pg_ratio:.1f}%), "
                f"체결강도 {cgld_val:.1f}%)"
            )
        elif entry_type == "BGU":
            gap_ratio = extra_info.get("gap_ratio", 0.0)
            proj_vol = extra_info.get("proj_vol", 0)
            avg_vol_50d = extra_info.get("avg_vol_50d", 0)
            vol_ratio = (proj_vol / avg_vol_50d * 100) if avg_vol_50d > 0 else 0.0
            reason_msg = (
                f"BGU진입(갭 {gap_ratio:.1f}%, "
                f"예상거래 {vol_ratio:.0f}%(50일평균대비), "
                f"PG매수 {pg_buy_amount // 100_000_000:,}억({pg_ratio:.1f}%), "
                f"체결강도 {cgld_val:.1f}%)"
            )
        else:
            reason_msg = (
                f"{entry_type}진입(체결강도 {cgld_val:.1f}%, "
                f"PG매수 {pg_buy_amount // 100_000_000:,}억({pg_ratio:.1f}%))"
            )

        self._logger.info({
            "event": "buy_signal_generated",
            "code": code, "name": item.name,
            "metrics": {
                "price": current,
                "entry_type": entry_type,
                "pg_participation_pct": round(pg_ratio, 2),
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
            volatility_20d_annualized=getattr(item, "volatility_20d_annualized", None),
        )

    # ── 조건 A: Pocket Pivot ──────────────────────────────────────

    def _check_pocket_pivot(
        self, code, current, vol, progress, ohlcv, item, prev_close
    ) -> Optional[Tuple[str, str, int, dict]]:
        """Pocket Pivot 조건 검사 (개선 버전)."""
        closes = [r.get("close", 0) for r in ohlcv if r.get("close")]
        if len(closes) < 10:
            return None

        # 1. 이동평균선 근접성 체크 (기존 로직 동일)
        ma_10d = sum(closes[-10:]) / 10
        ma_candidates = [(ma_10d, "10"), (item.ma_20d, "20"), (item.ma_50d, "50")]
        supporting_ma = ""
        ma_proximity_debug = {}
        for ma_val, ma_name in ma_candidates:
            if ma_val <= 0: continue
            pct_from_ma = (current - ma_val) / ma_val * 100
            ma_proximity_debug[f"ma{ma_name}_pct"] = round(pct_from_ma, 2)
            if ma_val * (1 + self._cfg.pp_ma_proximity_lower_pct/100) <= current <= ma_val * (1 + self._cfg.pp_ma_proximity_upper_pct/100):
                supporting_ma = ma_name
                break
        if not supporting_ma:
            closest_ma_pct = min(ma_proximity_debug.values(), key=abs) if ma_proximity_debug else None
            self._logger.info({
                "event": "pp_rejected", "code": code, "name": item.name, "reason": "no_ma_proximity",
                "closest_ma_pct": closest_ma_pct, **ma_proximity_debug,
            })
            return None

        # 2. 캔들 품질 체크 (추가): 윗꼬리가 너무 길어 밀리는 종목 배제
        today_ohlcv = ohlcv[-1]
        day_high = today_ohlcv.get("high", 0)
        day_low = today_ohlcv.get("low", 0)
        day_range = day_high - day_low
        if day_range > 0:
            # 저가 대비 현재 위치 (0.0: 저가, 1.0: 고가)
            relative_pos = (current - day_low) / day_range
            if relative_pos < self._cfg.pp_min_candle_relative_pos:
                self._logger.debug({"event": "pp_rejected", "code": code, "reason": "poor_candle_quality", "pos": round(relative_pos, 2), "threshold": self._cfg.pp_min_candle_relative_pos})
                return None

        # 3. 당일 상승일 확인 (양봉 및 전일비 상승)
        if current <= prev_close:
            return None

        # 4. 과거 10일 하락일 거래량 분석 및 노이즈 제거 (개선)
        lookback = min(self._cfg.pp_down_day_lookback, len(ohlcv))
        recent = ohlcv[-lookback:]
        down_day_volumes = [c.get("volume", 0) for c in recent if c.get("close", 0) < c.get("open", 0)]

        if not down_day_volumes:
            return None
        
        # [수정] 100%가 아닌 설정된 비율(예: 90%)만 넘어도 통과하도록 유연화
        max_down_vol = max(down_day_volumes)
        threshold_vol = max_down_vol * self._cfg.pp_down_vol_threshold_ratio

        # 5. 거래량 우위 확인
        effective_progress = max(progress, 0.05)
        proj_vol = vol / effective_progress

        if proj_vol <= threshold_vol:
            self._logger.info({
                "event": "pp_rejected", "code": code, "name": item.name, "reason": "insufficient_volume",
                "proj_vol": int(proj_vol), "threshold": int(threshold_vol)
            })
            return None

        return ("PP", supporting_ma, 0, {"proj_vol": proj_vol, "max_down_vol": max_down_vol})

    # ── 조건 B: BGU ───────────────────────────────────────────────

    def _check_bgu(
        self, code, current, vol, progress, ohlcv, today_open, today_low, prev_close,
        pg_buy: int = 0, trade_value: int = 0,
    ) -> Optional[Tuple[str, str, int, dict]]:
        """BGU(Buyable Gap-Up) 조건 검사.

        Returns: ("BGU", "", gap_day_low, extra_info) 또는 None
        """
        if today_open <= 0 or prev_close <= 0:
            return None

        # 1. 갭 비율 체크 (시가 >= 전일 종가 + 4%)
        gap_ratio = (today_open - prev_close) / prev_close * 100
        if gap_ratio < self._cfg.bgu_gap_pct:
            return None

        # 2. 휩소 필터: 장 시작 후 10분 경과 확인
        now = self._tm.get_current_kst_time()
        open_time = self._tm.get_market_open_time()
        elapsed_minutes = (now - open_time).total_seconds() / 60
        if elapsed_minutes < self._cfg.bgu_whipsaw_after_minutes:
            return None

        # 3. 가격 지지 확인 (현재가 >= 시가)
        if current < today_open:
            return None

        # 3.5. BGU 스마트머니 최소 비중 확인
        if pg_buy <= 0 or trade_value <= 0:
            return None
        pg_buy_amount = pg_buy * current
        pg_to_tv_pct = pg_buy_amount / trade_value * 100
        if pg_to_tv_pct < self._cfg.bgu_min_pg_tv_pct:
            self._logger.debug({
                "event": "bgu_rejected", "code": code, "reason": "low_pg_ratio",
                "pg_to_tv_pct": round(pg_to_tv_pct, 2), "threshold": self._cfg.bgu_min_pg_tv_pct,
            })
            return None

        # 4. 상대 거래량 체크 (환산 거래량 >= 50일 평균 × 300%)
        volumes = [r.get("volume", 0) for r in ohlcv if r.get("volume")]
        vol_50_count = min(50, len(volumes))
        if vol_50_count < 20:
            return None
        avg_vol_50d = sum(volumes[-vol_50_count:]) / vol_50_count

        effective_progress = max(progress, 0.05)
        proj_vol = vol / effective_progress

        if proj_vol < avg_vol_50d * self._cfg.bgu_volume_multiplier:
            return None

        self._logger.debug({
            "event": "bgu_matched", "code": code,
            "gap_ratio": round(gap_ratio, 2),
            "proj_vol": int(proj_vol), "avg_vol_50d": int(avg_vol_50d),
            "today_low": today_low,
        })

        return ("BGU", "", today_low, {"gap_ratio": gap_ratio, "proj_vol": proj_vol, "avg_vol_50d": avg_vol_50d})

    # ── 스마트 머니 필터 ──────────────────────────────────────────

    def _check_smart_money(self, code: str, current: int, pg_buy: int, trade_value: int, market_cap: int, cgld_val: float = 0.0) -> bool:
        """스마트 머니(프로그램 수급) 필터 — 시가총액 규모별 가변 기준 적용."""
        if pg_buy <= 0:
            return False

        pg_buy_amount = pg_buy * current
        
        # 1. 거래대금 대비 비중 (%)
        pg_to_tv_pct = (pg_buy_amount / trade_value * 100) if trade_value > 0 else 0
        
        # 2. 시가총액 대비 비중 (%)
        pg_to_mc_pct = (pg_buy_amount / market_cap * 100) if market_cap > 0 else 0

        # [개선] 시가총액 규모에 따른 '동적 허들' 설정
        # 덩치가 큰 종목일수록 시총 대비 0.3%를 채우기 매우 어렵기 때문입니다.
        # program_to_market_cap_pct=0.0 이면 mc 필터 비활성화 (슬라이딩 스케일 미적용)
        if self._cfg.program_to_market_cap_pct <= 0:
            mc_threshold = 0.0
        elif market_cap >= 10 * 10**12:    # 10조 이상 (초대형주: 삼성전자 등)
            mc_threshold = 0.1             # 0.1%만 들어와도 인정
        elif market_cap >= 1 * 10**12:     # 1조 이상 (대형주)
            mc_threshold = 0.2             # 0.2%
        else:                              # 1조 미만 (중소형주)
            mc_threshold = self._cfg.program_to_market_cap_pct  # 기본값 (0.3%)

        # --- 판정 로직 ---
        
        # 조건 A: 정석적인 수급 (거래대금 10% 이상 AND 시총 대비 동적 허들 돌파)
        is_standard_ok = (pg_to_tv_pct >= self._cfg.program_to_trade_value_pct and 
                          pg_to_mc_pct >= mc_threshold)
        
        # 조건 B: 유연한 수급 (수급은 약간 부족해도 '체결강도'라는 에너지가 뒷받침될 때)
        # 예: 프로그램 비중 7% + 체결강도 140% + 시총 대비 비중은 절반만 채워도 인정
        is_flexible_ok = (pg_to_tv_pct >= self._cfg.sm_flexible_pg_ratio and 
                          cgld_val >= self._cfg.sm_flexible_execution_strength and
                          pg_to_mc_pct >= (mc_threshold * 0.7))

        if not (is_standard_ok or is_flexible_ok):
            self._logger.debug({
                "event": "smart_money_rejected", "code": code, 
                "reason": "low_pg_metrics", 
                "pg_tv_pct": round(pg_to_tv_pct, 2), 
                "pg_mc_pct": round(pg_to_mc_pct, 3),
                "mc_threshold": mc_threshold,
                "cgld": cgld_val
            })
            return False

        self._logger.debug({
            "event": "smart_money_passed", "code": code, 
            "pg_tv_pct": round(pg_to_tv_pct, 2), 
            "pg_mc_pct": round(pg_to_mc_pct, 3)
        })
        return True

    # ── check_exits ────────────────────────────────────────────────

    async def check_exits(self, holdings: List[dict]) -> List[TradeSignal]:
        if not holdings:
            return []

        # 캐시된 마켓 타이밍을 한 번만 조회 (N+1 호출 방지)
        market_timing_cache = {
            "KOSPI": await self._universe.is_market_timing_ok("KOSPI", caller=self.name, logger=self._logger),
            "KOSDAQ": await self._universe.is_market_timing_ok("KOSDAQ", caller=self.name, logger=self._logger),
        }

        results = await asyncio.gather(
            *[self._check_single_exit(hold, market_timing_cache) for hold in holdings],
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
            try:
                await self._save_state_async()
            except Exception as e:
                self._logger.error(f"Failed to save state async for {self.name}: {e}")
        return signals

    async def _check_single_exit(self, hold: dict, market_timing_cache: dict) -> tuple:
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
            state = PPPositionState(
                entry_type="PP", entry_price=int(buy_price),
                entry_date="", peak_price=int(buy_price),
                supporting_ma="20", gap_day_low=0,
            )
            self._position_state[code] = state

        resp = await self._sqs.get_current_price(code, caller=self.name)
        if not resp or resp.rt_cd != "0":
            return signals, state_dirty

        output = resp.data.get("output") if isinstance(resp.data, dict) else None
        if not output:
            return signals, state_dirty

        if isinstance(output, dict):
            current = int(output.get("stck_prpr", 0))
            cur_pg_buy = int(output.get("pgtr_ntby_qty", 0))
            cur_trade_value = int(output.get("acml_tr_pbmn", 0))
        else:
            current = int(getattr(output, "stck_prpr", 0) or 0)
            cur_pg_buy = int(getattr(output, "pgtr_ntby_qty", 0) or 0)
            cur_trade_value = int(getattr(output, "acml_tr_pbmn", 0) or 0)

        if current <= 0:
            return signals, state_dirty

        # 최고가 갱신 (dirty flag)
        if current > state.peak_price:
            state.peak_price = current
            state_dirty = True

        pnl = float((current - buy_price) / buy_price * 100)
        today_str = self._tm.get_current_kst_time().strftime("%Y%m%d")

        # MFE / MAE 갱신
        if pnl > state.mfe_pct:
            state.mfe_pct = round(pnl, 2)
            state_dirty = True
        if pnl < state.mae_pct:
            state.mae_pct = round(pnl, 2)
            state_dirty = True

        # 수익 안착 추적 (+5% 돌파 시 1회만 기록)
        if pnl >= self._cfg.holding_profit_anchor_pct and state.holding_start_date == "":
            state.holding_start_date = today_str
            state_dirty = True

        # OHLCV (MA 기반 체크용)
        ohlcv_resp = await self._sqs.get_recent_daily_ohlcv(code, limit=60)
        ohlcv = ohlcv_resp.data if ohlcv_resp and ohlcv_resp.rt_cd == "0" else []

        reason = ""

        # 🚨 우선순위 1: 하드 스탑 (마켓타이밍 악화 OR 고점 대비 -10%)
        market = hold.get("market", "KOSPI")  # type: ignore
        hard_reason = await self._check_hard_stop(state, current, market, market_timing_cache.get(market, False))
        if hard_reason:
            reason = hard_reason

        # 🚨 우선순위 1.5: 수급이탈 조기 청산 (진입 시점 대비 PG/체결강도 급감 + 손실 중)
        if not reason and pnl <= 0 and state.entry_pg_buy_amount > 0 and state.entry_cgld > 0:
            cur_pg_amount = cur_pg_buy * current
            try:
                ccnl_resp = await self._sqs.get_stock_conclusion(code)
                if ccnl_resp and ccnl_resp.rt_cd == "0":
                    ccnl_output = ccnl_resp.data.get("output") if isinstance(ccnl_resp.data, dict) else None
                    cur_cgld = float(ccnl_output[0].get("tday_rltv") or 0.0) if ccnl_output else 0.0
                else:
                    cur_cgld = 0.0
            except Exception:
                cur_cgld = 0.0
            pg_ratio = cur_pg_amount / state.entry_pg_buy_amount if state.entry_pg_buy_amount > 0 else 1.0
            cgld_ratio = cur_cgld / state.entry_cgld if state.entry_cgld > 0 else 1.0
            if pg_ratio < self._cfg.smart_money_exit_pg_ratio and cgld_ratio < self._cfg.smart_money_exit_cgld_ratio:
                reason = f"수급이탈(PG {pg_ratio:.0%}/체결강도 {cgld_ratio:.0%} {pnl:.1f}%)"

        # 🚨 우선순위 2: 엔트리별 손절
        if not reason:
            if state.entry_type == "PP":
                pp_reason = self._check_pp_stop_loss(state, current, ohlcv)
                if pp_reason:
                    reason = pp_reason
            elif state.entry_type == "BGU":
                bgu_reason = self._check_bgu_stop_loss(state, current)
                if bgu_reason:
                    reason = bgu_reason

        # 🌟 우선순위 3: 부분 익절 (직전 익절가 대비 +15% 시 반복 실행)
        if not reason:
            ref_price = float(state.last_partial_sell_price if state.last_partial_sell_price > 0 else buy_price)
            partial_signal = self._check_partial_profit(code, state, current, ref_price, hold)
            if partial_signal:
                signals.append(partial_signal)
                state.last_partial_sell_price = current
                state.breakeven_armed = True
                state_dirty = True
                return signals, state_dirty  # 부분 매도 후 전량 청산하지 않음

        # 🛡️ 우선순위 3.5: 본절스탑 (부분익절 후 진입가 하회)
        if not reason and state.breakeven_armed and current < buy_price:
            reason = f"본절스탑(부분익절 후 진입가 {buy_price:,} 하회 {pnl:.1f}%)"

        # 🌟 우선순위 4: 7주 룰 만료 (수익 안착 후 35거래일 & 50MA 이탈)
        if not reason and state.holding_start_date:
            week7_reason = self._check_7week_hold(state, current, ohlcv)
            if week7_reason:
                reason = week7_reason

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
            if "손절" in reason or "스탑" in reason or "수급이탈" in reason:
                from datetime import date, timedelta
                unblock = (date.today() + timedelta(days=self._cfg.cooldown_days)).strftime("%Y%m%d")
                self._cooldown[code] = unblock
            state_dirty = True
            signals.append(TradeSignal(
                code=code, name=hold.get("name", code), action="SELL",
                price=current, qty=holding_qty, reason=reason, strategy_name=self.name
            ))

        return signals, state_dirty

    async def _check_hard_stop(self, state: PPPositionState, current: int, market: str, market_timing_ok: Optional[bool] = None) -> Optional[str]:
        """하드 스탑: 마켓타이밍 악화 또는 고점 대비 -10%.

        Args:
            market_timing_ok: 사전 계산된 마켓 타이밍 불리언 (None이면 내부에서 조회)
        """
        # 마켓 타이밍 악화 (사전 계산값이 있으면 사용)
        if market_timing_ok is None:
            if not await self._universe.is_market_timing_ok(market, caller=self.name, logger=self._logger):
                return "하드스탑(마켓타이밍 악화)"
        else:
            if not market_timing_ok:
                return "하드스탑(마켓타이밍 악화)"

        # 고점 대비 폭락
        if state.peak_price > 0:
            drop = float((current - state.peak_price) / state.peak_price * 100)
            if drop <= self._cfg.hard_stop_from_peak_pct:
                return f"하드스탑(고점대비 {drop:.1f}%)"

        return None

    def _check_pp_stop_loss(self, state: PPPositionState, current: int, ohlcv) -> Optional[str]:
        """PP 손절: 지지 MA를 -2% 이상 하향 이탈."""
        if not ohlcv or not state.supporting_ma:
            return None

        closes = [r.get("close", 0) for r in ohlcv if r.get("close")]
        ma_period = int(state.supporting_ma)
        if len(closes) < ma_period:
            return None

        ma_value = sum(closes[-ma_period:]) / ma_period
        threshold = ma_value * (1 + self._cfg.pp_stop_loss_below_ma_pct / 100)

        if current < threshold:
            return f"PP손절({state.supporting_ma}MA {ma_value:,.0f} 하향이탈)"

        return None

    def _check_bgu_stop_loss(self, state: PPPositionState, current: int) -> Optional[str]:
        """BGU 손절: 갭업 당일 장중 저가 이탈."""
        if state.gap_day_low > 0 and current < state.gap_day_low:
            return f"BGU손절(갭업저가 {state.gap_day_low:,} 이탈)"
        return None

    def _check_partial_profit(
        self, code: str, state: PPPositionState, current: int, buy_price: int, hold: dict
    ) -> Optional[TradeSignal]:
        """부분 익절: +15% 시 50% 매도. 잔고 1주면 전량."""
        pnl = float((current - buy_price) / buy_price * 100)
        if pnl < self._cfg.partial_profit_trigger_pct:
            return None

        holding_qty = int(hold.get("qty", 1))
        sell_qty = max(1, int(holding_qty * self._cfg.partial_sell_ratio))

        if sell_qty >= holding_qty:
            sell_qty = holding_qty
            reason = f"전량익절({pnl:.1f}%, 잔고 {holding_qty}주)"
        else:
            reason = f"부분익절({pnl:.1f}%, {sell_qty}주/{holding_qty}주)"

        self._logger.info({
            "event": "partial_profit_signal",
            "code": code, "pnl": round(pnl, 2),
            "sell_qty": sell_qty, "holding_qty": holding_qty,
        })

        return TradeSignal(
            code=code, name=hold.get("name", code), action="SELL",
            price=current, qty=sell_qty,
            reason=reason, strategy_name=self.name
        )

    def _check_7week_hold(self, state: PPPositionState, current: int, ohlcv) -> Optional[str]:
        """7주 룰: 수익 안착(+5%) 후 35거래일 경과 & 50MA 이탈 시 청산."""
        if not state.holding_start_date or not ohlcv:
            return None

        safe_date = normalize_yyyymmdd(state.holding_start_date)
        if not safe_date:
            return None

        trading_days = sum(
            1 for candle in ohlcv
            if normalize_yyyymmdd(candle.get("date", "")) > safe_date
        )

        if trading_days < self._cfg.holding_rule_days:
            return None

        # 50MA 이탈 체크
        closes = [r.get("close", 0) for r in ohlcv if r.get("close")]
        ma_period = self._cfg.holding_rule_ma_period
        if len(closes) < ma_period:
            return None

        ma_50 = sum(closes[-ma_period:]) / ma_period

        if current < ma_50:
            return f"7주룰(50MA {ma_50:,.0f} 이탈, {trading_days}일 보유)"

        return None

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
        """초기화 시 상태 파일을 비동기(가능하면)로 로드합니다.

        - 이벤트 루프가 실행 중이면 백그라운드 태스크로 로드합니다.
        - 그렇지 않으면 기존 동기 방식으로 안전하게 로드합니다.
        """
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
                            self._position_state[k] = PPPositionState(**v)
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
                self._position_state[k] = PPPositionState(**v)

    async def load_state(self):
        """초기화 직후 scan 전에 호출. _load_state_async() 를 명시적으로 await.

        Idempotent: `if k not in self._position_state` 가드로 중복 호출 안전.
        """
        await self._load_state_async()

    def _save_state(self):
        """백워드 호환성 있는 동기-스케줄러 래퍼: 이벤트 루프가 있으면 백그라운드에 저장 태스크를 생성,
        루프가 없으면 동기적으로 파일에 저장합니다.
        """
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
