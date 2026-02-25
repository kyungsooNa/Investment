# strategies/oneil_squeeze_breakout_strategy.py
"""오닐식 스퀴즈 주도주 돌파매매 (O'Neil Squeeze Breakout).

핵심: 시장 주도주 중 볼린저 밴드가 극도로 수축(스퀴즈)된 종목이
      거래량을 동반하며 20일 최고가를 돌파할 때 매수.
      프로그램 순매수 필터(2중 스마트 머니)로 기관 수급 확인.

v1 범위:
  - 유니버스: get_top_trading_value_stocks() → 기본 필터(거래대금/52주고가/정배열)
  - 매수: 마켓타이밍 + 스퀴즈 + 가격돌파 + 거래량돌파 + 프로그램필터
  - 매도: 손절(-5%) / 시간손절(5일 박스권 횡보) / 트레일링(-8%) / 추세이탈(10MA)

v2 예정 (TODO):
  - Pool A/B 분리 유니버스 (Pool A: 전일 기준 30종목, Pool B: 장중 거래대금 30종목)
  - 스코어링 시스템: RS(3개월 상대강도 상위10% → +30점), 업종 소분류 주도 → +20점
  - 분기 영업이익 25% 이상 증가 → +20점: /uapi/domestic-stock/v1/finance/financial-ratio
  - 스코어 상위 10~15종목만 집중 감시 (스코어링 갱신: 08:50, 10:00, 12:00, 14:00)
  - 체결강도(>=120%), 고래 탐지(>=5000만원): REST inquire-ccnl 또는 WS H0STOUP0
  - 코스닥/코스피 지수 직접 조회 (ETF 프록시 대체)
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional

from interfaces.live_strategy import LiveStrategy
from common.types import TradeSignal, ErrorCode
from services.trading_service import TradingService
from services.stock_query_service import StockQueryService
from services.indicator_service import IndicatorService
from market_data.stock_code_mapper import StockCodeMapper
from core.time_manager import TimeManager
from strategies.base_strategy_config import BaseStrategyConfig


# ── 설정 ────────────────────────────────────────────────

@dataclass
class OneilSqueezeConfig(BaseStrategyConfig):
    """오닐 스퀴즈 돌파 전략 설정."""

    # 유니버스 필터
    min_avg_trading_value_5d: int = 10_000_000_000  # 5일 평균 거래대금 100억 원
    near_52w_high_pct: float = 20.0                  # 52주 최고가 대비 20% 이내
    max_watchlist: int = 50                           # 최대 감시 종목 수

    # 매수 조건 — 볼린저 밴드 스퀴즈
    bb_period: int = 20
    bb_std_dev: float = 2.0
    squeeze_tolerance: float = 1.2  # BB 폭이 20일 최소폭의 1.2배 이내여야 함

    # 매수 조건 — 돌파
    high_breakout_period: int = 20                # 20일 최고가 돌파
    volume_breakout_multiplier: float = 1.5       # 20일 평균 거래량의 150%

    # 매수 조건 — 프로그램 필터
    program_net_buy_min: int = 0                  # pgtr_ntby_qty > 0
    program_to_trade_value_pct: float = 10.0      # (프로그램순매수금/거래대금) >= 10%
    program_to_market_cap_pct: float = 0.5        # (프로그램순매수금/시총) >= 0.5%

    # 마켓 타이밍 — 시장별 ETF 프록시
    kosdaq_etf_code: str = "229200"   # KODEX 코스닥150
    kospi_etf_code: str = "069500"    # KODEX 200
    market_ma_period: int = 20
    market_ma_rising_days: int = 3    # MA가 3일 연속 상승

    # 매도 조건
    stop_loss_pct: float = -5.0                   # 진입가 대비 -5% 손절
    trailing_stop_pct: float = 8.0                # 최고가 대비 -8% 트레일링 스탑
    time_stop_days: int = 5                       # 횡보 판정 기간 (거래일)
    time_stop_box_range_pct: float = 2.0          # (5일 최고-최저)/5일 평균종가 < 2%
    trend_exit_ma_period: int = 10                # 10일 MA 하향 이탈

    # 자금 관리
    total_portfolio_krw: int = 10_000_000
    position_size_pct: float = 5.0
    min_qty: int = 1

    # TODO [v2] 체결강도/고래 탐지 임계값
    # execution_strength_min: float = 120.0      # REST inquire-ccnl 또는 WS H0STOUP0
    # whale_order_min_krw: int = 50_000_000      # REST inquire-ccnl 또는 WS H0STOUP0


# ── 워치리스트 아이템 ────────────────────────────────────

@dataclass
class OSBWatchlistItem:
    """감시 종목 정보."""
    code: str
    name: str
    market: str             # "KOSPI" or "KOSDAQ"
    high_20d: int           # 20일 최고가 (돌파 기준)
    ma_20d: float           # 20일 이동평균
    ma_50d: float           # 50일 이동평균
    avg_vol_20d: float      # 20일 평균 거래량
    bb_width_min_20d: float # 최근 20일간 BB 밴드폭 최소값
    prev_bb_width: float    # 전일 BB 밴드폭
    w52_hgpr: int           # 52주 최고가
    avg_trading_value_5d: float  # 5일 평균 거래대금


# ── 포지션 상태 ──────────────────────────────────────────

@dataclass
class OSBPositionState:
    """보유 포지션 추적 상태."""
    entry_price: int        # 진입가
    entry_date: str         # 진입일 (YYYYMMDD)
    peak_price: int         # 진입 후 최고가 (트레일링 스탑용)
    breakout_level: int     # 진입 시 20일 최고가


# ── 전략 본체 ────────────────────────────────────────────

class OneilSqueezeBreakoutStrategy(LiveStrategy):
    """오닐식 스퀴즈 주도주 돌파매매 전략.

    scan():
      1. 당일 첫 호출 시 워치리스트 빌드
         (거래대금 상위 → 정배열·52주고가 필터 → BB 스퀴즈 계산)
      2. 마켓 타이밍 확인 (시장별 ETF 20일 MA 3일 연속 상승)
      3. 워치리스트 종목별 돌파 조건 체크
         (스퀴즈 + 가격돌파 + 거래량돌파 + 프로그램필터)

    check_exits():
      - 손절: 진입가 대비 -5%
      - 시간손절: 5거래일 박스권 횡보 (박스폭/평균종가 < 2%)
      - 트레일링 스탑: 최고가 대비 -8%
      - 추세이탈: 현재가 < 10일 MA + 대량 거래량 확인
    """

    STATE_FILE = os.path.join("data", "osb_position_state.json")

    def __init__(
        self,
        trading_service: TradingService,
        stock_query_service: StockQueryService,
        indicator_service: IndicatorService,
        stock_code_mapper: StockCodeMapper,
        time_manager: TimeManager,
        config: Optional[OneilSqueezeConfig] = None,
        logger: Optional[logging.Logger] = None,
    ):
        self._ts = trading_service
        self._sqs = stock_query_service
        self._indicator = indicator_service
        self._mapper = stock_code_mapper
        self._tm = time_manager
        self._cfg = config or OneilSqueezeConfig()
        self._logger = logger or logging.getLogger(__name__)

        # 내부 상태
        self._watchlist: Dict[str, OSBWatchlistItem] = {}
        self._watchlist_date: str = ""
        self._position_state: Dict[str, OSBPositionState] = {}

        # 마켓 타이밍 캐시 (일 1회 계산)
        self._market_timing_cache: Dict[str, bool] = {}  # "KOSPI" -> True/False
        self._market_timing_date: str = ""

        self._load_state()

    @property
    def name(self) -> str:
        return "오닐스퀴즈돌파"

    # ════════════════════════════════════════════════════════
    # 매수 스캔
    # ════════════════════════════════════════════════════════

    async def scan(self) -> List[TradeSignal]:
        signals: List[TradeSignal] = []
        self._logger.info({"event": "scan_started", "strategy_name": self.name})

        # 1) 워치리스트 빌드 (당일 1회)
        today = self._tm.get_current_kst_time().strftime("%Y%m%d")
        if self._watchlist_date != today:
            await self._build_watchlist()
            self._watchlist_date = today

        if not self._watchlist:
            self._logger.info({"event": "scan_skipped", "reason": "워치리스트 비어있음"})
            return signals

        self._logger.info({"event": "scan_with_watchlist", "count": len(self._watchlist)})

        # 2) 마켓 타이밍 캐시 갱신 (당일 1회)
        if self._market_timing_date != today:
            await self._update_market_timing()
            self._market_timing_date = today

        # 3) 장중 경과 비율
        market_progress = self._get_market_progress_ratio()
        if market_progress <= 0:
            return signals

        # 4) 종목별 돌파 조건 체크
        for code, item in self._watchlist.items():
            try:
                signal = await self._check_buy_conditions(code, item, market_progress)
                if signal:
                    signals.append(signal)
            except Exception as e:
                self._logger.error({
                    "event": "scan_error", "code": code, "error": str(e),
                }, exc_info=True)

        self._logger.info({"event": "scan_finished", "signals_found": len(signals)})
        return signals

    async def _check_buy_conditions(
        self, code: str, item: OSBWatchlistItem, market_progress: float,
    ) -> Optional[TradeSignal]:
        """개별 종목의 매수 조건을 ALL AND로 확인."""

        log_data = {"code": code, "name": item.name, "market": item.market}

        # ── 조건 1: 마켓 타이밍 ──
        if not self._market_timing_cache.get(item.market, False):
            return None

        # ── 조건 2: 스퀴즈 (전일 BB 폭이 20일 최소폭의 1.2배 이내) ──
        if item.bb_width_min_20d <= 0:
            return None
        if item.prev_bb_width > item.bb_width_min_20d * self._cfg.squeeze_tolerance:
            return None

        # ── 현재가 조회 ──
        full_resp = await self._ts.get_current_stock_price(code)
        if not full_resp or full_resp.rt_cd != ErrorCode.SUCCESS.value:
            return None

        output = self._extract_output(full_resp)
        if output is None:
            return None

        current = self._get_int_field(output, "stck_prpr")
        acml_vol = self._get_int_field(output, "acml_vol")
        acml_tr_pbmn = self._get_int_field(output, "acml_tr_pbmn")
        pgtr_ntby = self._get_int_field(output, "pgtr_ntby_qty")
        stck_llam = self._get_int_field(output, "stck_llam")

        if current <= 0:
            return None

        log_data.update({
            "current_price": current, "acml_vol": acml_vol,
            "pgtr_ntby": pgtr_ntby, "stck_llam": stck_llam,
        })

        # ── 조건 3: 가격 돌파 (현재가 > 20일 최고가) ──
        if current <= item.high_20d:
            return None

        # ── 조건 4: 거래량 돌파 (환산 거래량 >= 20일 평균 × 1.5) ──
        projected_vol = acml_vol / market_progress
        vol_threshold = item.avg_vol_20d * self._cfg.volume_breakout_multiplier
        if projected_vol < vol_threshold:
            log_data["reason"] = f"거래량 부족: 환산 {projected_vol:,.0f} < 기준 {vol_threshold:,.0f}"
            self._logger.info({"event": "candidate_rejected", **log_data})
            return None

        # ── 조건 5: 프로그램 필터 (2중 스마트 머니) ──
        if pgtr_ntby <= self._cfg.program_net_buy_min:
            log_data["reason"] = f"프로그램 순매수 부족: {pgtr_ntby}"
            self._logger.info({"event": "candidate_rejected", **log_data})
            return None

        pgtr_value = pgtr_ntby * current
        # 프로그램순매수금 / 누적거래대금 >= 10%
        if acml_tr_pbmn > 0:
            pgtr_to_tv = (pgtr_value / acml_tr_pbmn) * 100
            if pgtr_to_tv < self._cfg.program_to_trade_value_pct:
                log_data["reason"] = f"프로그램/거래대금 비율 부족: {pgtr_to_tv:.1f}% < {self._cfg.program_to_trade_value_pct}%"
                self._logger.info({"event": "candidate_rejected", **log_data})
                return None

        # 프로그램순매수금 / 시가총액 >= 0.5%
        if stck_llam > 0:
            pgtr_to_mc = (pgtr_value / stck_llam) * 100
            if pgtr_to_mc < self._cfg.program_to_market_cap_pct:
                log_data["reason"] = f"프로그램/시총 비율 부족: {pgtr_to_mc:.2f}% < {self._cfg.program_to_market_cap_pct}%"
                self._logger.info({"event": "candidate_rejected", **log_data})
                return None

        # TODO [v2] 체결강도 >= 120%
        # REST: /uapi/domestic-stock/v1/quotations/inquire-ccnl
        # WebSocket: H0STOUP0 (장내체결)
        # 현재는 건너뜀 (graceful degradation)

        # TODO [v2] 고래 탐지 (건당 5,000만원 이상 대량 시장가 매수)
        # 동일 API에서 체결량 × 체결가로 판정 가능
        # 현재는 건너뜀

        # ── 모든 조건 통과 → 매수 시그널 ──
        self._logger.info({"event": "breakout_detected", **log_data})

        qty = self._calculate_qty(current)

        self._position_state[code] = OSBPositionState(
            entry_price=current,
            entry_date=self._tm.get_current_kst_time().strftime("%Y%m%d"),
            peak_price=current,
            breakout_level=item.high_20d,
        )
        self._save_state()

        reason_msg = (
            f"스퀴즈돌파: 20일고가({item.high_20d:,})돌파, "
            f"BB폭={item.prev_bb_width:.1f}(최소{item.bb_width_min_20d:.1f}), "
            f"환산거래량 {projected_vol:,.0f}, 프로그램순매수 {pgtr_ntby:,}주"
        )
        signal = TradeSignal(
            code=code, name=item.name, action="BUY", price=current, qty=qty,
            reason=reason_msg, strategy_name=self.name,
        )
        self._logger.info({
            "event": "buy_signal_generated",
            "code": code, "name": item.name, "price": current, "qty": qty,
            "reason": reason_msg, "data": log_data,
        })
        return signal

    # ════════════════════════════════════════════════════════
    # 매도 체크
    # ════════════════════════════════════════════════════════

    async def check_exits(self, holdings: List[dict]) -> List[TradeSignal]:
        signals: List[TradeSignal] = []
        self._logger.info({"event": "check_exits_started", "holdings_count": len(holdings)})

        for hold in holdings:
            code = str(hold.get("code", ""))
            buy_price = hold.get("buy_price", 0)
            stock_name = hold.get("name", code)
            log_data = {"code": code, "name": stock_name, "buy_price": buy_price}

            if not code or not buy_price:
                continue

            try:
                full_resp = await self._ts.get_current_stock_price(code)
                if not full_resp or full_resp.rt_cd != ErrorCode.SUCCESS.value:
                    continue

                output = self._extract_output(full_resp)
                if output is None:
                    continue

                current = self._get_int_field(output, "stck_prpr")
                acml_vol = self._get_int_field(output, "acml_vol")
                if current <= 0:
                    continue

                log_data["current_price"] = current

                # 포지션 상태
                state = self._position_state.get(code)
                if not state:
                    today = self._tm.get_current_kst_time().strftime("%Y%m%d")
                    state = OSBPositionState(
                        entry_price=buy_price, entry_date=today,
                        peak_price=buy_price, breakout_level=buy_price,
                    )
                    self._position_state[code] = state

                log_data["position_state"] = asdict(state)

                # 최고가 갱신
                if current > state.peak_price:
                    state.peak_price = current
                    self._save_state()

                pnl_pct = ((current - buy_price) / buy_price) * 100
                log_data["pnl_pct"] = round(pnl_pct, 2)

                reason = ""
                should_sell = False

                # 1) 손절: -5%
                if pnl_pct <= self._cfg.stop_loss_pct:
                    reason = f"손절: 매수가({buy_price:,}) 대비 {pnl_pct:.1f}%"
                    should_sell = True

                # 2) 시간 손절: 5거래일 박스권 횡보
                if not should_sell:
                    is_sideways = await self._check_time_stop(code, state)
                    if is_sideways:
                        reason = (
                            f"시간청산: {self._cfg.time_stop_days}거래일 박스권 횡보 "
                            f"(폭 < {self._cfg.time_stop_box_range_pct}%)"
                        )
                        should_sell = True

                # 3) 트레일링 스탑: 최고가 대비 -8%
                if not should_sell and state.peak_price > 0:
                    drop_from_peak = ((current - state.peak_price) / state.peak_price) * 100
                    log_data["drop_from_peak_pct"] = round(drop_from_peak, 2)
                    if drop_from_peak <= -self._cfg.trailing_stop_pct:
                        reason = (
                            f"트레일링스탑: 최고가({state.peak_price:,}) 대비 "
                            f"{drop_from_peak:.1f}%"
                        )
                        should_sell = True

                # 4) 추세 이탈: 현재가 < 10일 MA + 대량 거래량 확인
                if not should_sell:
                    ma_10d = await self._get_current_ma(code, self._cfg.trend_exit_ma_period)
                    if ma_10d and current < ma_10d:
                        # 대량 거래량 동반 여부 확인
                        market_progress = self._get_market_progress_ratio()
                        if market_progress > 0:
                            ohlcv = await self._ts.get_recent_daily_ohlcv(code, limit=20)
                            if ohlcv and len(ohlcv) >= 20:
                                avg_vol = sum(r.get("volume", 0) for r in ohlcv[-20:]) / 20
                                projected_vol = acml_vol / market_progress
                                if projected_vol > avg_vol:
                                    reason = (
                                        f"추세이탈: 현재가({current:,}) < "
                                        f"10일MA({ma_10d:,.0f}), 대량거래량 확인"
                                    )
                                    should_sell = True

                if should_sell:
                    self._position_state.pop(code, None)
                    self._save_state()
                    api_name = self._get_str_field(output, "hts_kor_isnm") or stock_name
                    signals.append(TradeSignal(
                        code=code, name=api_name, action="SELL", price=current, qty=1,
                        reason=reason, strategy_name=self.name,
                    ))
                    self._logger.info({
                        "event": "sell_signal_generated",
                        "code": code, "name": api_name, "price": current,
                        "reason": reason, "data": log_data,
                    })
                else:
                    self._logger.info({
                        "event": "hold_checked", "code": code,
                        "reason": "매도 조건 없음", "data": log_data,
                    })

            except Exception as e:
                self._logger.error({
                    "event": "check_exits_error", "code": code, "error": str(e),
                }, exc_info=True)

        self._logger.info({"event": "check_exits_finished", "signals_found": len(signals)})
        return signals

    async def _check_time_stop(self, code: str, state: OSBPositionState) -> bool:
        """시간 손절: 진입 후 N거래일이 경과했고, 박스권 횡보 중인지 판정.

        박스권 = (N일 최고가 - N일 최저가) / N일 평균종가 < time_stop_box_range_pct
        """
        entry_date = state.entry_date
        today = self._tm.get_current_kst_time().strftime("%Y%m%d")
        if entry_date == today:
            return False  # 당일 진입은 횡보 판정 불가

        n = self._cfg.time_stop_days
        try:
            ohlcv = await self._ts.get_recent_daily_ohlcv(code, limit=n + 5)
            if not ohlcv:
                return False

            # 진입일 이후 데이터만 필터
            post_entry = [r for r in ohlcv if str(r.get("date", "")) >= entry_date]
            if len(post_entry) < n:
                return False  # 아직 N거래일 미경과

            recent_n = post_entry[-n:]
            highs = [r.get("high", 0) for r in recent_n if r.get("high")]
            lows = [r.get("low", 0) for r in recent_n if r.get("low")]
            closes = [r.get("close", 0) for r in recent_n if r.get("close")]

            if not highs or not lows or not closes:
                return False

            box_high = max(highs)
            box_low = min(lows)
            avg_close = sum(closes) / len(closes)

            if avg_close <= 0:
                return False

            box_range_pct = ((box_high - box_low) / avg_close) * 100
            is_sideways = box_range_pct < self._cfg.time_stop_box_range_pct

            if is_sideways:
                self._logger.info({
                    "event": "time_stop_triggered",
                    "code": code, "box_high": box_high, "box_low": box_low,
                    "avg_close": avg_close, "box_range_pct": round(box_range_pct, 2),
                })

            return is_sideways

        except Exception as e:
            self._logger.warning({"event": "time_stop_check_error", "code": code, "error": str(e)})
            return False

    # ════════════════════════════════════════════════════════
    # 워치리스트 빌드
    # ════════════════════════════════════════════════════════

    async def _build_watchlist(self):
        """거래대금 상위 → 정배열/52주고가 필터 → BB 스퀴즈 계산."""
        self._watchlist.clear()
        self._logger.info({"event": "build_watchlist_started"})

        # 1) 거래대금 상위 종목 조회
        resp = await self._ts.get_top_trading_value_stocks()
        if not resp or resp.rt_cd != ErrorCode.SUCCESS.value:
            self._logger.warning({"event": "build_watchlist_failed", "reason": "거래대금 상위 조회 실패"})
            return

        candidates = resp.data or []
        self._logger.info({"event": "watchlist_candidates_fetched", "count": len(candidates)})

        items: List[OSBWatchlistItem] = []

        for stock in candidates:
            code = stock.get("mksc_shrn_iscd") or stock.get("stck_shrn_iscd") or ""
            if not code:
                continue
            stock_name = stock.get("hts_kor_isnm", "") or self._mapper.get_name_by_code(code) or code

            try:
                item = await self._analyze_candidate(code, stock_name)
                if item:
                    items.append(item)
            except Exception as e:
                self._logger.error({
                    "event": "build_watchlist_error", "code": code, "error": str(e),
                }, exc_info=True)

        # TODO [v2] 스코어링: RS(3개월 상대강도 상위10% +30점), 업종 소분류 주도(+20점),
        #   분기 영업이익 25%↑(+20점, /uapi/domestic-stock/v1/finance/financial-ratio)
        #   스코어 상위 10~15종목만 집중 감시
        # TODO [v1.1] 우선순위 정렬: 거래대금/시총 비율 또는 이격도 순으로 정렬 후 상위 선택
        self._watchlist = {
            item.code: item for item in items[:self._cfg.max_watchlist]
        }
        self._logger.info({
            "event": "build_watchlist_finished",
            "initial_candidates": len(candidates),
            "final_watchlist_count": len(self._watchlist),
            "watchlist_codes": list(self._watchlist.keys()),
        })

    async def _analyze_candidate(self, code: str, name: str) -> Optional[OSBWatchlistItem]:
        """종목의 OHLCV + BB 분석. 조건 충족 시 OSBWatchlistItem 반환.

        TODO [v2] RS(상대강도) 계산: 3개월 수익률을 구해 전체 워치리스트 내 상위10%에 +30점
        TODO [v2] 스코어 필드를 OSBWatchlistItem에 추가하고, 여기서 산출
        """
        # 50일 MA 계산을 위해 충분한 데이터 필요
        ohlcv = await self._ts.get_recent_daily_ohlcv(code, limit=60)
        if not ohlcv or len(ohlcv) < 50:
            return None

        period = self._cfg.high_breakout_period  # 20
        closes = [r.get("close", 0) for r in ohlcv if r.get("close")]
        highs = [r.get("high", 0) for r in ohlcv[-period:] if r.get("high")]
        volumes = [r.get("volume", 0) for r in ohlcv[-period:] if r.get("volume")]

        if len(closes) < 50 or len(highs) < period or len(volumes) < period:
            return None

        # 이동평균
        ma_20d = sum(closes[-20:]) / 20
        ma_50d = sum(closes[-50:]) / 50
        high_20d = int(max(highs))
        avg_vol_20d = sum(volumes) / len(volumes)
        prev_close = closes[-1]

        # 5일 평균 거래대금
        recent_5 = ohlcv[-5:]
        trading_values = [
            (r.get("volume", 0) or 0) * (r.get("close", 0) or 0) for r in recent_5
        ]
        avg_trading_value_5d = sum(trading_values) / len(trading_values) if trading_values else 0

        log_data = {
            "code": code, "name": name,
            "ma_20d": ma_20d, "ma_50d": ma_50d, "high_20d": high_20d,
            "avg_trading_value_5d": avg_trading_value_5d, "prev_close": prev_close,
        }

        # 필터 1: 5일 평균 거래대금 >= 100억
        if avg_trading_value_5d < self._cfg.min_avg_trading_value_5d:
            self._logger.debug({"event": "filter_rejected", **log_data, "reason": "거래대금 부족"})
            return None

        # 필터 2: 정배열 (종가 > 20일 MA > 50일 MA)
        if not (prev_close > ma_20d > ma_50d):
            self._logger.debug({"event": "filter_rejected", **log_data, "reason": "정배열 아님"})
            return None

        # 필터 3: 52주 최고가 대비 20% 이내
        full_resp = await self._ts.get_current_stock_price(code)
        if not full_resp or full_resp.rt_cd != ErrorCode.SUCCESS.value:
            return None
        output = self._extract_output(full_resp)
        if output is None:
            return None

        w52_hgpr = self._get_int_field(output, "w52_hgpr")
        if w52_hgpr > 0:
            distance_pct = ((w52_hgpr - prev_close) / w52_hgpr) * 100
            if distance_pct > self._cfg.near_52w_high_pct:
                self._logger.debug({
                    "event": "filter_rejected", **log_data,
                    "reason": f"52주 고가 거리 {distance_pct:.1f}% > {self._cfg.near_52w_high_pct}%",
                })
                return None

        # BB 스퀴즈 계산 (ohlcv_data 재사용 → 추가 API 호출 없음)
        bb_resp = await self._indicator.get_bollinger_bands(
            code, period=self._cfg.bb_period, std_dev=self._cfg.bb_std_dev,
            ohlcv_data=ohlcv,
        )
        bb_widths = self._extract_bb_widths(bb_resp)
        if len(bb_widths) < period:
            return None

        recent_bb_widths = bb_widths[-period:]
        bb_width_min_20d = min(recent_bb_widths)
        prev_bb_width = bb_widths[-1]

        market = "KOSDAQ" if self._mapper.is_kosdaq(code) else "KOSPI"

        self._logger.debug({
            "event": "filter_passed", **log_data,
            "bb_width_min_20d": bb_width_min_20d, "prev_bb_width": prev_bb_width,
            "market": market,
        })

        return OSBWatchlistItem(
            code=code, name=name, market=market,
            high_20d=high_20d, ma_20d=ma_20d, ma_50d=ma_50d,
            avg_vol_20d=avg_vol_20d,
            bb_width_min_20d=bb_width_min_20d, prev_bb_width=prev_bb_width,
            w52_hgpr=w52_hgpr, avg_trading_value_5d=avg_trading_value_5d,
        )

    # ════════════════════════════════════════════════════════
    # 마켓 타이밍
    # ════════════════════════════════════════════════════════

    async def _update_market_timing(self):
        """시장별 ETF의 20일 MA가 3일 연속 상승인지 확인."""
        for market, etf_code in [
            ("KOSDAQ", self._cfg.kosdaq_etf_code),
            ("KOSPI", self._cfg.kospi_etf_code),
        ]:
            rising = await self._check_etf_ma_rising(etf_code)
            self._market_timing_cache[market] = rising
            self._logger.info({
                "event": "market_timing_updated",
                "market": market, "etf_code": etf_code, "ma_rising": rising,
            })

    async def _check_etf_ma_rising(self, etf_code: str) -> bool:
        """ETF의 N일 MA가 M일 연속 상승 중인지 확인."""
        period = self._cfg.market_ma_period   # 20
        days = self._cfg.market_ma_rising_days  # 3

        try:
            ohlcv = await self._ts.get_recent_daily_ohlcv(etf_code, limit=period + days + 2)
            if not ohlcv or len(ohlcv) < period + days:
                return False

            closes = [r.get("close", 0) for r in ohlcv if r.get("close")]
            if len(closes) < period + days:
                return False

            # 최근 (days+1)일의 MA 값 계산
            ma_values = []
            for i in range(days + 1):
                end_idx = len(closes) - days + i
                if end_idx < period:
                    return False
                window = closes[end_idx - period:end_idx]
                ma_values.append(sum(window) / period)

            # 연속 상승 확인
            for j in range(1, len(ma_values)):
                if ma_values[j] <= ma_values[j - 1]:
                    return False

            return True

        except Exception as e:
            self._logger.warning({"event": "etf_ma_check_error", "etf_code": etf_code, "error": str(e)})
            return False

    # ════════════════════════════════════════════════════════
    # 유틸리티
    # ════════════════════════════════════════════════════════

    def _extract_bb_widths(self, bb_resp) -> List[float]:
        """BB 응답에서 밴드폭 (upper - lower) 시리즈 추출."""
        if not bb_resp or bb_resp.rt_cd != ErrorCode.SUCCESS.value:
            return []
        widths = []
        for band in (bb_resp.data or []):
            upper = getattr(band, "upper", None)
            lower = getattr(band, "lower", None)
            if upper is not None and lower is not None:
                widths.append(upper - lower)
        return widths

    @staticmethod
    def _extract_output(resp):
        """API 응답에서 output 객체 추출."""
        data = resp.data
        if isinstance(data, dict):
            return data.get("output")
        return data

    @staticmethod
    def _get_int_field(output, field_name: str) -> int:
        if isinstance(output, dict):
            val = output.get(field_name, "0")
        else:
            val = getattr(output, field_name, "0")
        try:
            return int(val or "0")
        except (ValueError, TypeError):
            return 0

    @staticmethod
    def _get_str_field(output, field_name: str) -> str:
        if isinstance(output, dict):
            return str(output.get(field_name, "") or "")
        return str(getattr(output, field_name, "") or "")

    def _calculate_qty(self, price: int) -> int:
        """포트폴리오 비중 기반 주문 수량 계산."""
        if self._cfg.use_fixed_qty:
            return 1
        if price <= 0:
            return self._cfg.min_qty
        budget = self._cfg.total_portfolio_krw * (self._cfg.position_size_pct / 100)
        qty = int(budget / price)
        return max(qty, self._cfg.min_qty)

    def _get_market_progress_ratio(self) -> float:
        """장 시작 이후 경과 비율 (0.0 ~ 1.0)."""
        now = self._tm.get_current_kst_time()
        open_time = self._tm.get_market_open_time()
        close_time = self._tm.get_market_close_time()

        total_seconds = (close_time - open_time).total_seconds()
        elapsed_seconds = (now - open_time).total_seconds()

        if total_seconds <= 0 or elapsed_seconds <= 0:
            return 0.0
        return min(elapsed_seconds / total_seconds, 1.0)

    async def _get_current_ma(self, code: str, period: int) -> Optional[float]:
        """종목의 현재 N일 이동평균."""
        try:
            ohlcv = await self._ts.get_recent_daily_ohlcv(code, limit=period)
            if not ohlcv or len(ohlcv) < period:
                return None
            closes = [r.get("close", 0) for r in ohlcv[-period:] if r.get("close")]
            if len(closes) < period:
                return None
            return sum(closes) / len(closes)
        except Exception:
            return None

    # ── 상태 저장/복원 ──

    def _load_state(self):
        """파일에서 포지션 상태 복원."""
        if not os.path.exists(self.STATE_FILE):
            return
        try:
            with open(self.STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            for code, state_dict in data.items():
                self._position_state[code] = OSBPositionState(**state_dict)
            if self._position_state:
                self._logger.info({
                    "event": "position_state_loaded",
                    "count": len(self._position_state),
                    "codes": list(self._position_state.keys()),
                })
        except (json.JSONDecodeError, IOError, KeyError, TypeError) as e:
            self._logger.warning({"event": "load_state_failed", "error": str(e)})

    def _save_state(self):
        """포지션 상태를 파일에 저장."""
        try:
            os.makedirs(os.path.dirname(self.STATE_FILE), exist_ok=True)
            data = {code: asdict(state) for code, state in self._position_state.items()}
            with open(self.STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except (IOError, OSError) as e:
            self._logger.warning({"event": "save_state_failed", "error": str(e)})
