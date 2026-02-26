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

import asyncio
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
    max_watchlist: int = 60                           # 최대 감시 종목 수

    # 워치리스트 갱신 시각 (장 시작 후 경과 분)
    # 프로그램 시작 시 최초 1회 + 아래 시각에 미갱신이면 갱신
    watchlist_refresh_minutes: tuple = (10, 30, 60, 90)

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

    # V2 스코어링
    rs_period_days: int = 63                   # RS 계산 기간 (~3개월, 약 63거래일)
    rs_top_percentile: float = 10.0            # 상위 10%에 RS 점수 부여
    rs_score_points: float = 30.0              # RS 점수
    profit_growth_threshold_pct: float = 25.0  # 영업이익 성장률 임계값 (%)
    profit_growth_score_points: float = 20.0   # 영업이익 점수
    score_top_n: int = 15                      # 스코어 상위 N종목만 감시
    api_chunk_size: int = 10                   # TPS 방어용 API 청크 크기

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
    market_cap: int = 0         # 시가총액 (stck_llam)

    # V2 스코어링
    rs_return_3m: float = 0.0         # 3개월 수익률 (RS 원시값, %)
    rs_score: float = 0.0             # RS 점수 (0 or 30)
    profit_growth_score: float = 0.0  # 영업이익 성장 점수 (0 or 20)
    total_score: float = 0.0          # 합산 점수


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
      1. 워치리스트 빌드/갱신 (첫 호출 + 장시작 후 10분/30분/1시간/1시간30분)
         3가지 랭킹(거래대금/상승률/거래량 상위30) 병합 → 필터 → 회전율 정렬
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
        self._watchlist_refresh_done: set = set()  # 당일 완료된 갱신 시각(분)
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

        # 1) 워치리스트 빌드/갱신
        today = self._tm.get_current_kst_time().strftime("%Y%m%d")
        if self._watchlist_date != today:
            # 새 날: 초기화 후 첫 빌드
            self._watchlist_refresh_done = set()
            await self._build_watchlist()
            self._watchlist_date = today
        elif self._should_refresh_watchlist():
            # 장중 갱신 시점 도래 (기존 워치리스트에 신규 종목 추가)
            await self._build_watchlist()

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
            self._logger.info({
                "event": "scan_skipped",
                "reason": f"장중 경과 비율 0 이하 (market_progress={market_progress:.3f})",
            })
            return signals
        self._logger.info({
            "event": "scan_market_progress",
            "market_progress": round(market_progress, 3),
            "market_timing": dict(self._market_timing_cache),
        })

        # 4) 종목별 돌파 조건 체크
        for code, item in self._watchlist.items():
            # 이미 보유중인 종목은 매수 대상에서 제외
            if code in self._position_state:
                self._logger.debug({"event": "scan_skipped_already_holding", "code": code, "name": item.name})
                continue

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
        market_ok = self._market_timing_cache.get(item.market, False)
        if not market_ok:
            self._logger.info({
                "event": "buy_condition_rejected",
                "condition": "마켓타이밍",
                "reason": f"{item.market} 시장 MA 상승 추세 아님",
                **log_data,
            })
            return None

        # ── 조건 2: 스퀴즈 (전일 BB 폭이 20일 최소폭의 1.2배 이내) ──
        if item.bb_width_min_20d <= 0:
            self._logger.info({
                "event": "buy_condition_rejected",
                "condition": "스퀴즈",
                "reason": f"BB 최소폭 데이터 없음 (bb_width_min_20d={item.bb_width_min_20d})",
                **log_data,
            })
            return None
        squeeze_threshold = item.bb_width_min_20d * self._cfg.squeeze_tolerance
        if item.prev_bb_width > squeeze_threshold:
            self._logger.info({
                "event": "buy_condition_rejected",
                "condition": "스퀴즈",
                "reason": (
                    f"BB폭 수축 부족: 전일BB폭={item.prev_bb_width:.1f} > "
                    f"기준={squeeze_threshold:.1f} "
                    f"(최소폭{item.bb_width_min_20d:.1f} × {self._cfg.squeeze_tolerance})"
                ),
                **log_data,
            })
            return None
        self._logger.debug({
            "event": "buy_condition_passed",
            "condition": "스퀴즈",
            "prev_bb_width": round(item.prev_bb_width, 1),
            "squeeze_threshold": round(squeeze_threshold, 1),
            **log_data,
        })

        # ── 현재가 조회 ──
        full_resp = await self._ts.get_current_stock_price(code)
        if not full_resp or full_resp.rt_cd != ErrorCode.SUCCESS.value:
            self._logger.warning({
                "event": "buy_condition_rejected",
                "condition": "현재가조회",
                "reason": "현재가 API 응답 실패",
                **log_data,
            })
            return None

        output = self._extract_output(full_resp)
        if output is None:
            self._logger.warning({
                "event": "buy_condition_rejected",
                "condition": "현재가조회",
                "reason": "API 응답에서 output 추출 실패",
                **log_data,
            })
            return None

        current = self._get_int_field(output, "stck_prpr")
        acml_vol = self._get_int_field(output, "acml_vol")
        acml_tr_pbmn = self._get_int_field(output, "acml_tr_pbmn")
        pgtr_ntby = self._get_int_field(output, "pgtr_ntby_qty")
        stck_llam = self._get_int_field(output, "stck_llam")

        if current <= 0:
            self._logger.warning({
                "event": "buy_condition_rejected",
                "condition": "현재가조회",
                "reason": f"현재가 비정상: {current}",
                **log_data,
            })
            return None

        log_data.update({
            "current_price": current, "acml_vol": acml_vol,
            "pgtr_ntby": pgtr_ntby, "stck_llam": stck_llam,
        })

        # ── 조건 3: 가격 돌파 (현재가 > 20일 최고가) ──
        if current <= item.high_20d:
            self._logger.info({
                "event": "buy_condition_rejected",
                "condition": "가격돌파",
                "reason": f"현재가({current:,}) <= 20일최고가({item.high_20d:,})",
                **log_data,
            })
            return None
        self._logger.debug({
            "event": "buy_condition_passed",
            "condition": "가격돌파",
            "current": current, "high_20d": item.high_20d,
            **log_data,
        })

        # ── 조건 4: 거래량 돌파 (환산 거래량 >= 20일 평균 × 1.5) ──
        projected_vol = acml_vol / market_progress
        vol_threshold = item.avg_vol_20d * self._cfg.volume_breakout_multiplier
        if projected_vol < vol_threshold:
            self._logger.info({
                "event": "buy_condition_rejected",
                "condition": "거래량돌파",
                "reason": f"환산거래량({projected_vol:,.0f}) < 기준({vol_threshold:,.0f})",
                "market_progress": round(market_progress, 3),
                "acml_vol": acml_vol,
                "avg_vol_20d": round(item.avg_vol_20d),
                **log_data,
            })
            return None
        self._logger.debug({
            "event": "buy_condition_passed",
            "condition": "거래량돌파",
            "projected_vol": round(projected_vol),
            "vol_threshold": round(vol_threshold),
            **log_data,
        })

        # ── 조건 5: 프로그램 필터 (2중 스마트 머니) ──
        if pgtr_ntby <= self._cfg.program_net_buy_min:
            self._logger.info({
                "event": "buy_condition_rejected",
                "condition": "프로그램필터",
                "reason": f"프로그램 순매수 부족: {pgtr_ntby}주 <= {self._cfg.program_net_buy_min}주",
                **log_data,
            })
            return None

        pgtr_value = pgtr_ntby * current
        # 프로그램순매수금 / 누적거래대금 >= 10%
        if acml_tr_pbmn > 0:
            pgtr_to_tv = (pgtr_value / acml_tr_pbmn) * 100
            if pgtr_to_tv < self._cfg.program_to_trade_value_pct:
                self._logger.info({
                    "event": "buy_condition_rejected",
                    "condition": "프로그램/거래대금비율",
                    "reason": f"비율 부족: {pgtr_to_tv:.1f}% < {self._cfg.program_to_trade_value_pct}%",
                    "pgtr_value": pgtr_value, "acml_tr_pbmn": acml_tr_pbmn,
                    **log_data,
                })
                return None

        # 프로그램순매수금 / 시가총액 >= 0.5%
        if stck_llam > 0:
            pgtr_to_mc = (pgtr_value / stck_llam) * 100
            if pgtr_to_mc < self._cfg.program_to_market_cap_pct:
                self._logger.info({
                    "event": "buy_condition_rejected",
                    "condition": "프로그램/시총비율",
                    "reason": f"비율 부족: {pgtr_to_mc:.2f}% < {self._cfg.program_to_market_cap_pct}%",
                    "pgtr_value": pgtr_value, "stck_llam": stck_llam,
                    **log_data,
                })
                return None
        self._logger.debug({
            "event": "buy_condition_passed",
            "condition": "프로그램필터",
            "pgtr_ntby": pgtr_ntby, "pgtr_value": pgtr_value,
            **log_data,
        })

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

                self._logger.info({
                    "event": "exit_evaluation_started",
                    "code": code, "name": stock_name,
                    "buy_price": buy_price, "current_price": current,
                    "pnl_pct": round(pnl_pct, 2),
                    "peak_price": state.peak_price,
                    "entry_date": state.entry_date,
                })

                reason = ""
                should_sell = False

                # 1) 손절: -5%
                if pnl_pct <= self._cfg.stop_loss_pct:
                    reason = f"손절: 매수가({buy_price:,}) 대비 {pnl_pct:.1f}%"
                    should_sell = True
                    self._logger.info({
                        "event": "exit_condition_triggered",
                        "condition": "손절",
                        "code": code, "name": stock_name,
                        "pnl_pct": round(pnl_pct, 2),
                        "threshold": self._cfg.stop_loss_pct,
                    })
                else:
                    self._logger.debug({
                        "event": "exit_condition_not_met",
                        "condition": "손절",
                        "code": code,
                        "pnl_pct": round(pnl_pct, 2),
                        "threshold": self._cfg.stop_loss_pct,
                    })

                # 2) 시간 손절: 5거래일 박스권 횡보
                if not should_sell:
                    is_sideways = await self._check_time_stop(code, state)
                    if is_sideways:
                        reason = (
                            f"시간청산: {self._cfg.time_stop_days}거래일 박스권 횡보 "
                            f"(폭 < {self._cfg.time_stop_box_range_pct}%)"
                        )
                        should_sell = True
                        self._logger.info({
                            "event": "exit_condition_triggered",
                            "condition": "시간손절",
                            "code": code, "name": stock_name,
                            "entry_date": state.entry_date,
                            "time_stop_days": self._cfg.time_stop_days,
                        })
                    else:
                        self._logger.debug({
                            "event": "exit_condition_not_met",
                            "condition": "시간손절",
                            "code": code,
                            "entry_date": state.entry_date,
                        })

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
                        self._logger.info({
                            "event": "exit_condition_triggered",
                            "condition": "트레일링스탑",
                            "code": code, "name": stock_name,
                            "peak_price": state.peak_price,
                            "drop_from_peak_pct": round(drop_from_peak, 2),
                            "threshold": -self._cfg.trailing_stop_pct,
                        })
                    else:
                        self._logger.debug({
                            "event": "exit_condition_not_met",
                            "condition": "트레일링스탑",
                            "code": code,
                            "peak_price": state.peak_price,
                            "drop_from_peak_pct": round(drop_from_peak, 2),
                            "threshold": -self._cfg.trailing_stop_pct,
                        })

                # 4) 추세 이탈: 현재가 < 10일 MA + 대량 거래량 확인
                if not should_sell:
                    ma_10d = await self._get_current_ma(code, self._cfg.trend_exit_ma_period)
                    if ma_10d and current < ma_10d:
                        self._logger.info({
                            "event": "exit_trend_break_detected",
                            "code": code, "name": stock_name,
                            "current": current, "ma_10d": round(ma_10d),
                            "detail": "현재가 < 10일MA, 대량거래량 확인 중",
                        })
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
                                    self._logger.info({
                                        "event": "exit_condition_triggered",
                                        "condition": "추세이탈",
                                        "code": code, "name": stock_name,
                                        "current": current, "ma_10d": round(ma_10d),
                                        "projected_vol": round(projected_vol),
                                        "avg_vol_20d": round(avg_vol),
                                    })
                                else:
                                    self._logger.info({
                                        "event": "exit_condition_not_met",
                                        "condition": "추세이탈",
                                        "code": code,
                                        "detail": (
                                            f"MA 하회하나 거래량 부족: "
                                            f"환산{projected_vol:,.0f} <= 평균{avg_vol:,.0f}"
                                        ),
                                    })
                    elif ma_10d:
                        self._logger.debug({
                            "event": "exit_condition_not_met",
                            "condition": "추세이탈",
                            "code": code,
                            "current": current, "ma_10d": round(ma_10d),
                            "detail": "현재가 >= 10일MA",
                        })

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
        """3가지 랭킹(거래대금/상승률/거래량) 소스를 병합하여 워치리스트 구성.

        1) 거래대금 상위 30 + 상승률 상위 30 + 거래량 상위 30 → 중복 제거 후 최대 ~90개 후보
        2) 각 후보를 _analyze_candidate()로 필터 (정배열/52주고가/BB)
        3) 거래대금/시총 비율(회전율) 내림차순 정렬 → 상위 max_watchlist개 선택
        """
        self._logger.info({"event": "build_watchlist_started"})

        # 1) 3가지 랭킹 소스 병렬 조회
        trading_val_resp, rise_resp, volume_resp = await asyncio.gather(
            self._ts.get_top_trading_value_stocks(),
            self._ts.get_top_rise_fall_stocks(rise=True),
            self._ts.get_top_volume_stocks(),
            return_exceptions=True,
        )

        # 종목코드 → {code, name} 중복 제거 병합
        candidate_map: Dict[str, str] = {}  # code → name

        for resp in [trading_val_resp, rise_resp, volume_resp]:
            if isinstance(resp, Exception) or not resp or resp.rt_cd != ErrorCode.SUCCESS.value:
                continue
            for stock in (resp.data or []):
                if isinstance(stock, dict):
                    code = stock.get("mksc_shrn_iscd") or stock.get("stck_shrn_iscd") or ""
                    name = stock.get("hts_kor_isnm", "")
                else:
                    code = getattr(stock, "mksc_shrn_iscd", "") or getattr(stock, "stck_shrn_iscd", "")
                    name = getattr(stock, "hts_kor_isnm", "")
                if code and code not in candidate_map:
                    candidate_map[code] = name or self._mapper.get_name_by_code(code) or code

        self._logger.info({
            "event": "watchlist_candidates_merged",
            "total_unique": len(candidate_map),
        })

        # 2) 각 후보 분석 (기존 워치리스트에 있으면 스킵)
        items: List[OSBWatchlistItem] = []
        existing_codes = set(self._watchlist.keys())

        for code, stock_name in candidate_map.items():
            if code in existing_codes:
                # 이미 워치리스트에 있는 종목은 유지
                items.append(self._watchlist[code])
                continue

            try:
                item = await self._analyze_candidate(code, stock_name)
                if item:
                    items.append(item)
            except Exception as e:
                self._logger.error({
                    "event": "build_watchlist_error", "code": code, "error": str(e),
                }, exc_info=True)

        # 3) V2 스코어링: RS 퍼센타일 + 영업이익 성장률 + 합산 + 2단계 정렬
        self._compute_rs_scores(items)
        await self._compute_profit_growth_scores(items)
        self._compute_total_scores(items)

        # 1차: total_score 내림차순, 2차: 회전율 내림차순 (동점자 해소)
        items.sort(
            key=lambda x: (x.total_score, self._calc_turnover_ratio(x)),
            reverse=True,
        )

        self._watchlist = {
            item.code: item for item in items[:self._cfg.score_top_n]
        }
        self._logger.info({
            "event": "build_watchlist_finished",
            "total_candidates_analyzed": len(candidate_map),
            "final_watchlist_count": len(self._watchlist),
            "watchlist_codes": list(self._watchlist.keys()),
            "top_scores": [
                {"code": item.code, "name": item.name, "total_score": item.total_score,
                 "rs_score": item.rs_score, "profit_score": item.profit_growth_score,
                 "rs_return_3m": round(item.rs_return_3m, 1)}
                for item in items[:self._cfg.score_top_n]
            ],
        })

    async def _analyze_candidate(self, code: str, name: str) -> Optional[OSBWatchlistItem]:
        """종목의 OHLCV + BB 분석. 조건 충족 시 OSBWatchlistItem 반환.

        V2: RS(상대강도) 원시 수익률도 함께 계산하여 rs_return_3m에 저장.
        퍼센타일 기반 점수 부여는 _build_watchlist()에서 전체 후보 대상으로 수행.
        """
        # 50일 MA + 3개월 RS 계산을 위해 충분한 데이터 필요 (90일)
        ohlcv = await self._ts.get_recent_daily_ohlcv(code, limit=90)
        if not ohlcv or len(ohlcv) < 50:
            self._logger.info({
                "event": "watchlist_filter_rejected",
                "filter": "OHLCV데이터",
                "code": code, "name": name,
                "reason": f"OHLCV 데이터 부족: {len(ohlcv) if ohlcv else 0}개 < 50개",
            })
            return None

        period = self._cfg.high_breakout_period  # 20
        closes = [r.get("close", 0) for r in ohlcv if r.get("close")]
        highs = [r.get("high", 0) for r in ohlcv[-period:] if r.get("high")]
        volumes = [r.get("volume", 0) for r in ohlcv[-period:] if r.get("volume")]

        if len(closes) < 50 or len(highs) < period or len(volumes) < period:
            self._logger.info({
                "event": "watchlist_filter_rejected",
                "filter": "OHLCV데이터",
                "code": code, "name": name,
                "reason": f"유효 데이터 부족: closes={len(closes)}, highs={len(highs)}, volumes={len(volumes)}",
            })
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
            self._logger.info({
                "event": "watchlist_filter_rejected",
                "filter": "거래대금",
                "reason": (
                    f"5일평균거래대금({avg_trading_value_5d / 1e8:,.0f}억) < "
                    f"기준({self._cfg.min_avg_trading_value_5d / 1e8:,.0f}억)"
                ),
                **log_data,
            })
            return None

        # 필터 2: 정배열 (종가 > 20일 MA > 50일 MA)
        if not (prev_close > ma_20d > ma_50d):
            self._logger.info({
                "event": "watchlist_filter_rejected",
                "filter": "정배열",
                "reason": (
                    f"종가({prev_close:,}) > 20MA({ma_20d:,.0f}) > 50MA({ma_50d:,.0f}) "
                    f"조건 불충족"
                ),
                **log_data,
            })
            return None

        # 필터 3: 52주 최고가 대비 20% 이내
        full_resp = await self._ts.get_current_stock_price(code)
        if not full_resp or full_resp.rt_cd != ErrorCode.SUCCESS.value:
            self._logger.warning({
                "event": "watchlist_filter_rejected",
                "filter": "52주고가",
                "reason": "현재가 API 응답 실패",
                **log_data,
            })
            return None
        output = self._extract_output(full_resp)
        if output is None:
            return None

        w52_hgpr = self._get_int_field(output, "w52_hgpr")
        stck_llam = self._get_int_field(output, "stck_llam")  # 시가총액
        if w52_hgpr > 0:
            distance_pct = ((w52_hgpr - prev_close) / w52_hgpr) * 100
            if distance_pct > self._cfg.near_52w_high_pct:
                self._logger.info({
                    "event": "watchlist_filter_rejected",
                    "filter": "52주고가",
                    "reason": (
                        f"52주고가 거리 {distance_pct:.1f}% > "
                        f"기준 {self._cfg.near_52w_high_pct}%"
                    ),
                    "w52_hgpr": w52_hgpr, "prev_close": prev_close,
                    **log_data,
                })
                return None

        # BB 스퀴즈 계산 (ohlcv_data 재사용 → 추가 API 호출 없음)
        bb_resp = await self._indicator.get_bollinger_bands(
            code, period=self._cfg.bb_period, std_dev=self._cfg.bb_std_dev,
            ohlcv_data=ohlcv,
        )
        bb_widths = self._extract_bb_widths(bb_resp)
        if len(bb_widths) < period:
            self._logger.info({
                "event": "watchlist_filter_rejected",
                "filter": "BB스퀴즈",
                "reason": f"BB 데이터 부족: {len(bb_widths)}개 < {period}개",
                **log_data,
            })
            return None

        recent_bb_widths = bb_widths[-period:]
        bb_width_min_20d = min(recent_bb_widths)
        prev_bb_width = bb_widths[-1]

        market = "KOSDAQ" if self._mapper.is_kosdaq(code) else "KOSPI"

        self._logger.info({
            "event": "watchlist_filter_passed",
            "bb_width_min_20d": round(bb_width_min_20d, 1),
            "prev_bb_width": round(prev_bb_width, 1),
            "squeeze_ratio": round(prev_bb_width / bb_width_min_20d, 2) if bb_width_min_20d > 0 else 0,
            "market": market,
            "w52_hgpr": w52_hgpr,
            "market_cap_억": round(stck_llam / 1e8) if stck_llam > 0 else 0,
            **log_data,
        })

        # V2: RS(3개월 수익률) 계산 (ohlcv 재사용 → 추가 API 호출 없음)
        rs_return_3m = 0.0
        rs_resp = await self._indicator.get_relative_strength(
            code, period_days=self._cfg.rs_period_days, ohlcv_data=ohlcv,
        )
        if rs_resp and rs_resp.rt_cd == ErrorCode.SUCCESS.value and rs_resp.data:
            rs_return_3m = rs_resp.data.return_pct

        return OSBWatchlistItem(
            code=code, name=name, market=market,
            high_20d=high_20d, ma_20d=ma_20d, ma_50d=ma_50d,
            avg_vol_20d=avg_vol_20d,
            bb_width_min_20d=bb_width_min_20d, prev_bb_width=prev_bb_width,
            w52_hgpr=w52_hgpr, avg_trading_value_5d=avg_trading_value_5d,
            market_cap=stck_llam,
            rs_return_3m=rs_return_3m,
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
                self._logger.warning({
                    "event": "etf_ma_data_insufficient",
                    "etf_code": etf_code,
                    "data_count": len(ohlcv) if ohlcv else 0,
                    "required": period + days,
                })
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
            is_rising = True
            for j in range(1, len(ma_values)):
                if ma_values[j] <= ma_values[j - 1]:
                    is_rising = False
                    break

            self._logger.info({
                "event": "etf_ma_check_result",
                "etf_code": etf_code,
                "ma_values": [round(v, 2) for v in ma_values],
                "is_rising": is_rising,
                "period": period, "rising_days": days,
            })
            return is_rising

        except Exception as e:
            self._logger.warning({"event": "etf_ma_check_error", "etf_code": etf_code, "error": str(e)})
            return False

    # ════════════════════════════════════════════════════════
    # 워치리스트 갱신 판정
    # ════════════════════════════════════════════════════════

    def _should_refresh_watchlist(self) -> bool:
        """장중 갱신 시점이 도래했고 아직 갱신하지 않았으면 True.

        이미 지난 시점은 한꺼번에 done 처리하여, 장 중간에 시작해도
        과거 시점마다 반복 빌드되지 않도록 한다.
        """
        now = self._tm.get_current_kst_time()
        open_time = self._tm.get_market_open_time()
        elapsed_minutes = (now - open_time).total_seconds() / 60

        triggered = False
        for target_min in self._cfg.watchlist_refresh_minutes:
            if elapsed_minutes >= target_min and target_min not in self._watchlist_refresh_done:
                self._watchlist_refresh_done.add(target_min)
                if not triggered:
                    # 실제 갱신은 1회만
                    triggered = True
                    self._logger.info({
                        "event": "watchlist_refresh_triggered",
                        "elapsed_minutes": round(elapsed_minutes, 1),
                        "target_minutes": target_min,
                    })
                else:
                    # 이미 지난 시점은 done 처리만
                    self._logger.info({
                        "event": "watchlist_refresh_skipped_past",
                        "target_minutes": target_min,
                    })
        return triggered

    @staticmethod
    def _calc_turnover_ratio(item: OSBWatchlistItem) -> float:
        """거래대금/시총 비율(회전율). 높을수록 시장 관심 집중."""
        if item.market_cap > 0:
            return item.avg_trading_value_5d / item.market_cap
        # 시총 정보 없으면 거래대금 절대값으로 폴백
        return item.avg_trading_value_5d

    # ════════════════════════════════════════════════════════
    # V2 스코어링
    # ════════════════════════════════════════════════════════

    def _compute_rs_scores(self, items: List[OSBWatchlistItem]) -> None:
        """RS 수익률 퍼센타일 → 상위 10%에 30점 부여 (in-place).

        주의: 오닐의 원래 RS 지수는 전 상장 종목 대비 상대 순위이나,
        여기서는 API 제약상 '1차 필터 통과 워치리스트 내부(30~60개)에서의 상위 10%'로
        산출하는 Proxy 방식을 사용한다. 전종목 대상이 아님을 인지할 것.
        """
        if not items:
            return

        sorted_returns = sorted(item.rs_return_3m for item in items)
        # 상위 10% 컷오프: 예) 20개 중 상위 10% = 상위 2개 → cutoff_idx = 18
        cutoff_idx = min(
            max(1, int(len(sorted_returns) * (1 - self._cfg.rs_top_percentile / 100))),
            len(sorted_returns) - 1,
        )
        cutoff_value = sorted_returns[cutoff_idx]

        for item in items:
            item.rs_score = self._cfg.rs_score_points if item.rs_return_3m >= cutoff_value else 0.0

        rs_count = sum(1 for item in items if item.rs_score > 0)
        self._logger.info({
            "event": "rs_scores_computed",
            "total_items": len(items),
            "cutoff_value": round(cutoff_value, 2),
            "rs_scored_count": rs_count,
        })

    async def _compute_profit_growth_scores(self, items: List[OSBWatchlistItem]) -> None:
        """영업이익 성장률 API 조회 → 25%↑이면 20점 (in-place).

        TPS 20 제한 방어: api_chunk_size개씩 청크 호출 + 1.1초 딜레이.
        API 실패 시 graceful skip (0점).
        """
        chunk_size = self._cfg.api_chunk_size
        scored_count = 0

        for i in range(0, len(items), chunk_size):
            chunk = items[i:i + chunk_size]
            tasks = [self._fetch_profit_growth(item) for item in chunk]
            await asyncio.gather(*tasks, return_exceptions=True)
            # TPS 방어: 다음 청크가 있으면 대기
            if i + chunk_size < len(items):
                await asyncio.sleep(1.1)

        scored_count = sum(1 for item in items if item.profit_growth_score > 0)
        self._logger.info({
            "event": "profit_growth_scores_computed",
            "total_items": len(items),
            "scored_count": scored_count,
        })

    async def _fetch_profit_growth(self, item: OSBWatchlistItem) -> None:
        """단일 종목 영업이익 성장률 조회 → 점수 부여."""
        try:
            resp = await self._ts.get_financial_ratio(item.code)
            if resp and resp.rt_cd == ErrorCode.SUCCESS.value and resp.data:
                growth = self._extract_op_profit_growth(resp.data)
                if growth >= self._cfg.profit_growth_threshold_pct:
                    item.profit_growth_score = self._cfg.profit_growth_score_points
                    self._logger.debug({
                        "event": "profit_growth_scored",
                        "code": item.code, "name": item.name,
                        "growth_pct": round(growth, 1),
                    })
        except Exception as e:
            self._logger.warning({
                "event": "profit_growth_fetch_failed",
                "code": item.code, "error": str(e),
            })

    @staticmethod
    def _extract_op_profit_growth(data) -> float:
        """재무비율 API 응답에서 영업이익 증가율(%) 추출.

        KIS API 응답 구조에 따라 필드명이 다를 수 있음.
        응답이 리스트인 경우 최근 분기의 영업이익 증가율을 반환.
        """
        try:
            # API 응답이 dict인 경우
            if isinstance(data, dict):
                # 가능한 필드명: bsop_prti_icdc (영업이익증감률) 등
                for key in ("bsop_prti_icdc", "sale_totl_prfi_icdc", "op_profit_growth"):
                    val = data.get(key)
                    if val is not None:
                        return float(val)
                # output 안에 있을 수 있음
                output = data.get("output") or data.get("output1")
                if isinstance(output, list) and output:
                    latest = output[0]
                    for key in ("bsop_prti_icdc", "sale_totl_prfi_icdc", "op_profit_growth"):
                        val = latest.get(key)
                        if val is not None:
                            return float(val)
                elif isinstance(output, dict):
                    for key in ("bsop_prti_icdc", "sale_totl_prfi_icdc", "op_profit_growth"):
                        val = output.get(key)
                        if val is not None:
                            return float(val)
            # API 응답이 리스트인 경우
            elif isinstance(data, list) and data:
                latest = data[0]
                if isinstance(latest, dict):
                    for key in ("bsop_prti_icdc", "sale_totl_prfi_icdc", "op_profit_growth"):
                        val = latest.get(key)
                        if val is not None:
                            return float(val)
        except (ValueError, TypeError, IndexError, AttributeError):
            pass
        return 0.0

    def _compute_total_scores(self, items: List[OSBWatchlistItem]) -> None:
        """합산 점수 계산 (in-place)."""
        for item in items:
            item.total_score = item.rs_score + item.profit_growth_score

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
