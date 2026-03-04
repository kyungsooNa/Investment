# strategies/oneil/pocket_pivot_strategy.py
from __future__ import annotations

import logging
import os
import json
from dataclasses import dataclass, asdict
from typing import List, Optional, Dict, Tuple

from interfaces.live_strategy import LiveStrategy
from common.types import TradeSignal, ErrorCode
from services.trading_service import TradingService
from core.time_manager import TimeManager
from strategies.oneil_common_types import OneilPocketPivotConfig, PPPositionState
from services.oneil_universe_service import OneilUniverseService


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
        trading_service: TradingService,
        universe_service: OneilUniverseService,
        time_manager: TimeManager,
        config: Optional[OneilPocketPivotConfig] = None,
        logger: Optional[logging.Logger] = None,
    ):
        self._ts = trading_service
        self._universe = universe_service
        self._tm = time_manager
        self._cfg = config or OneilPocketPivotConfig()
        self._logger = logger or logging.getLogger(__name__)

        self._position_state: Dict[str, PPPositionState] = {}
        self._load_state()

    @property
    def name(self) -> str:
        return "오닐PP/BGU"

    # ── scan ────────────────────────────────────────────────────────

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
                signal = await self._check_entry(code, item, market_progress)
                if signal:
                    signals.append(signal)
            except Exception as e:
                self._logger.error(f"Scan error {code}: {e}")

        self._logger.info({"event": "scan_finished", "signals_found": len(signals)})
        return signals

    async def _check_entry(self, code, item, progress) -> Optional[TradeSignal]:
        """진입 조건 검사: PP 또는 BGU → 스마트머니 → 체결강도."""
        # 1. 현재가 데이터 조회
        resp = await self._ts.get_current_stock_price(code)
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
            today_low = int(out.get("stck_lwpr", 0))
            prev_close = int(out.get("stck_prdy_clpr", 0))
        else:
            current = int(getattr(out, "stck_prpr", 0) or 0)
            vol = int(getattr(out, "acml_vol", 0) or 0)
            pg_buy = int(getattr(out, "pgtr_ntby_qty", 0) or 0)
            trade_value = int(getattr(out, "acml_tr_pbmn", 0) or 0)
            today_open = int(getattr(out, "stck_oprc", 0) or 0)
            today_low = int(getattr(out, "stck_lwpr", 0) or 0)
            prev_close = int(getattr(out, "stck_prdy_clpr", 0) or 0)

        if current <= 0 or prev_close <= 0:
            return None

        # 2. OHLCV 조회 (MA 계산 + 하락일 거래량 분석용)
        ohlcv = await self._ts.get_recent_daily_ohlcv(code, limit=60)
        if not ohlcv or len(ohlcv) < 10:
            return None

        # 3. 조건 A (Pocket Pivot) 시도
        entry_result = self._check_pocket_pivot(
            code, current, vol, progress, ohlcv, item, prev_close
        )

        # 4. 조건 B (BGU) 시도
        if not entry_result:
            entry_result = self._check_bgu(
                code, current, vol, progress, ohlcv, today_open, today_low, prev_close
            )

        if not entry_result:
            return None

        entry_type, supporting_ma, gap_day_low = entry_result

        # 5. ★ 공통 스마트 머니 필터 (기술적 조건 통과 후에만 호출)
        if not self._check_smart_money(current, pg_buy, trade_value, item.market_cap):
            self._logger.debug({"event": "smart_money_rejected", "code": code, "entry_type": entry_type})
            return None

        # 6. ★ 체결강도 스냅샷 (>=120%)
        cgld_val = 0.0
        try:
            ccnl_resp = await self._ts.get_stock_conclusion(code)
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
        pg_buy_amount = pg_buy * current

        self._position_state[code] = PPPositionState(
            entry_type=entry_type,
            entry_price=current,
            entry_date=self._tm.get_current_kst_time().strftime("%Y%m%d"),
            peak_price=current,
            supporting_ma=supporting_ma,
            gap_day_low=gap_day_low,
            partial_sold=False,
            holding_start_date="",
        )
        self._save_state()

        reason_msg = (
            f"{entry_type}진입(체결강도 {cgld_val:.1f}%, "
            f"PG매수 {pg_buy_amount // 100_000_000}억)"
        )
        if entry_type == "PP":
            reason_msg = f"PP진입({supporting_ma}MA지지, 체결강도 {cgld_val:.1f}%)"
        elif entry_type == "BGU":
            gap_ratio = (today_open - prev_close) / prev_close * 100
            reason_msg = f"BGU진입(갭 {gap_ratio:.1f}%, 체결강도 {cgld_val:.1f}%)"

        self._logger.info({
            "event": "buy_signal_generated",
            "code": code, "name": item.name,
            "entry_type": entry_type,
            "price": current,
            "reason": reason_msg,
        })

        return TradeSignal(
            code=code, name=item.name, action="BUY", price=current, qty=qty,
            reason=reason_msg, strategy_name=self.name
        )

    # ── 조건 A: Pocket Pivot ──────────────────────────────────────

    def _check_pocket_pivot(
        self, code, current, vol, progress, ohlcv, item, prev_close
    ) -> Optional[Tuple[str, str, int]]:
        """Pocket Pivot 조건 검사.

        Returns: ("PP", supporting_ma, 0) 또는 None
        """
        closes = [r.get("close", 0) for r in ohlcv if r.get("close")]
        if len(closes) < 10:
            return None

        # 1. MA 계산 (10일은 직접 계산, 20/50일은 item에서)
        ma_10d = sum(closes[-10:]) / 10
        ma_candidates = [
            (ma_10d, "10"),
            (item.ma_20d, "20"),
            (item.ma_50d, "50"),
        ]

        # 2. 이평선 근접성 체크 (-2% ~ +4%)
        supporting_ma = ""
        for ma_val, ma_name in ma_candidates:
            if ma_val <= 0:
                continue
            lower = ma_val * (1 + self._cfg.pp_ma_proximity_lower_pct / 100)
            upper = ma_val * (1 + self._cfg.pp_ma_proximity_upper_pct / 100)
            if lower <= current <= upper:
                supporting_ma = ma_name
                break

        if not supporting_ma:
            return None

        # 3. 당일 상승일 확인 (현재가 > 전일 종가)
        if current <= prev_close:
            return None

        # 4. 과거 10일 하락일(close < open) 거래량 중 MAX 산출
        lookback = min(self._cfg.pp_down_day_lookback, len(ohlcv))
        recent = ohlcv[-lookback:]
        down_day_volumes = []
        for candle in recent:
            c = candle.get("close", 0)
            o = candle.get("open", 0)
            v = candle.get("volume", 0)
            if c and o and c < o and v:
                down_day_volumes.append(v)

        max_down_vol = max(down_day_volumes) if down_day_volumes else 0

        # 5. 거래량 우위: 환산 거래량 > 하락일 최대 거래량
        effective_progress = max(progress, 0.05)
        proj_vol = vol / effective_progress

        if proj_vol <= max_down_vol:
            return None

        self._logger.debug({
            "event": "pocket_pivot_matched", "code": code,
            "supporting_ma": supporting_ma,
            "proj_vol": int(proj_vol), "max_down_vol": int(max_down_vol),
        })

        return ("PP", supporting_ma, 0)

    # ── 조건 B: BGU ───────────────────────────────────────────────

    def _check_bgu(
        self, code, current, vol, progress, ohlcv, today_open, today_low, prev_close
    ) -> Optional[Tuple[str, str, int]]:
        """BGU(Buyable Gap-Up) 조건 검사.

        Returns: ("BGU", "", gap_day_low) 또는 None
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

        return ("BGU", "", today_low)

    # ── 스마트 머니 필터 ──────────────────────────────────────────

    def _check_smart_money(self, current, pg_buy, trade_value, market_cap) -> bool:
        """스마트 머니(프로그램 수급) 필터."""
        if pg_buy <= 0:
            return False

        pg_buy_amount = pg_buy * current

        # 거래대금의 10% 이상 개입
        if trade_value > 0:
            pg_to_tv_pct = pg_buy_amount / trade_value * 100
            if pg_to_tv_pct < self._cfg.program_to_trade_value_pct:
                return False

        # 시가총액의 0.3% 이상 개입
        if market_cap > 0:
            pg_to_mc_pct = pg_buy_amount / market_cap * 100
            if pg_to_mc_pct < self._cfg.program_to_market_cap_pct:
                return False

        return True

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
                state = PPPositionState(
                    entry_type="PP", entry_price=buy_price,
                    entry_date="", peak_price=buy_price,
                    supporting_ma="20", gap_day_low=0,
                    partial_sold=False, holding_start_date="",
                )
                self._position_state[code] = state

            resp = await self._ts.get_current_stock_price(code)
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
            today_str = self._tm.get_current_kst_time().strftime("%Y%m%d")

            # 수익 안착 추적 (+5% 돌파 시 1회만 기록)
            if pnl >= self._cfg.holding_profit_anchor_pct and state.holding_start_date == "":
                state.holding_start_date = today_str
                self._save_state()

            # OHLCV (MA 기반 체크용)
            ohlcv = await self._ts.get_recent_daily_ohlcv(code, limit=60)

            reason = ""

            # 🚨 우선순위 1: 하드 스탑 (마켓타이밍 악화 OR 고점 대비 -10%)
            market = hold.get("market", "KOSPI")
            hard_reason = await self._check_hard_stop(state, current, market)
            if hard_reason:
                reason = hard_reason

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

            # 🌟 우선순위 3: 부분 익절 (+15% & 미실행)
            if not reason and not state.partial_sold:
                partial_signal = self._check_partial_profit(code, state, current, buy_price, hold)
                if partial_signal:
                    signals.append(partial_signal)
                    state.partial_sold = True
                    self._save_state()
                    continue  # 부분 매도 후 전량 청산하지 않음

            # 🌟 우선순위 4: 7주 룰 만료 (수익 안착 후 35거래일 & 50MA 이탈)
            if not reason and state.holding_start_date:
                week7_reason = self._check_7week_hold(state, current, ohlcv)
                if week7_reason:
                    reason = week7_reason

            # 매도 시그널 생성
            if reason:
                self._position_state.pop(code, None)
                self._save_state()
                signals.append(TradeSignal(
                    code=code, name=hold.get("name", code), action="SELL",
                    price=current, qty=1, reason=reason, strategy_name=self.name
                ))

        return signals

    async def _check_hard_stop(self, state: PPPositionState, current: int, market: str) -> Optional[str]:
        """하드 스탑: 마켓타이밍 악화 또는 고점 대비 -10%."""
        # 마켓 타이밍 악화
        if not await self._universe.is_market_timing_ok(market):
            return "하드스탑(마켓타이밍 악화)"

        # 고점 대비 폭락
        if state.peak_price > 0:
            drop = (current - state.peak_price) / state.peak_price * 100
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
        pnl = (current - buy_price) / buy_price * 100
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

        safe_date = state.holding_start_date.replace("-", "")
        trading_days = sum(
            1 for candle in ohlcv
            if str(candle.get("date", "")).replace("-", "") > safe_date
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
        if os.path.exists(self.STATE_FILE):
            try:
                with open(self.STATE_FILE, "r") as f:
                    data = json.load(f)
                for k, v in data.items():
                    self._position_state[k] = PPPositionState(**v)
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
