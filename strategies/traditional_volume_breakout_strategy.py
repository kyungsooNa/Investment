# strategies/traditional_volume_breakout_strategy.py
"""전통적 거래량 돌파매매 전략 (Traditional Volume Breakout).

핵심: 20일 최고가를 거래량 150% 동반 돌파 시 매수, 트레일링 스탑/손절/가짜돌파/추세종료로 매도.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional

from interfaces.live_strategy import LiveStrategy
from common.types import TradeSignal, ErrorCode
from services.trading_service import TradingService
from services.stock_query_service import StockQueryService
from market_data.stock_code_mapper import StockCodeMapper
from core.time_manager import TimeManager
from strategies.base_strategy_config import BaseStrategyConfig


@dataclass
class TraditionalVBConfig(BaseStrategyConfig):
    """전통적 거래량 돌파 전략 설정."""
    # 유니버스 필터
    min_avg_trading_value_5d: int = 10_000_000_000  # 5일 평균 거래대금 100억 원
    ma_period: int = 20                              # 이동평균 기간
    high_period: int = 20                            # 최고가 기간
    near_high_pct: float = 3.0                       # 20일 최고가 대비 거리 3% 이내
    max_watchlist: int = 50                           # 최대 감시 종목 수

    # 매수 조건
    volume_breakout_multiplier: float = 1.5  # 20일 평균 거래량의 150%

    # 매도 조건
    stop_loss_pct: float = -3.0       # 진입가 대비 -3% 손절
    trailing_stop_pct: float = 5.0    # 최고가 대비 -5% 트레일링 스탑

    # 자금 관리
    total_portfolio_krw: int = 10_000_000  # 전체 포트폴리오 금액 (원)
    position_size_pct: float = 5.0         # 1회 매수 비중 (%) — MAX 5%
    min_qty: int = 1                       # 최소 주문 수량


@dataclass
class WatchlistItem:
    """워치리스트 종목 정보."""
    code: str
    name: str
    high_20d: int          # 20일 최고가 (돌파 기준선)
    ma_20d: float          # 20일 이동평균
    avg_vol_20d: float     # 20일 평균 거래량
    avg_trading_value_5d: float  # 5일 평균 거래대금


@dataclass
class PositionState:
    """보유 포지션 추적 상태."""
    breakout_level: int    # 진입 시점 20일 최고가 (가짜 돌파 판정용)
    peak_price: int        # 진입 후 최고가 (트레일링 스탑용)


class TraditionalVolumeBreakoutStrategy(LiveStrategy):
    """전통적 거래량 돌파매매 전략.

    scan():
      1. 당일 첫 호출 시 워치리스트 빌드 (거래대금 상위 → 코스닥 필터 → 20일 OHLCV)
      2. 워치리스트 종목의 현재가/거래량으로 돌파 조건 검사
      3. 가격 돌파(20일 최고가) + 거래량 돌파(150%) AND 조건 충족 시 BUY

    check_exits():
      - 손절: 진입가 대비 -3%
      - 가짜돌파: 현재가 < 돌파 시 20일 최고가
      - 트레일링 스탑: 최고가 대비 -5%
      - 추세종료: 현재가 < 20일 MA
    """

    STATE_FILE = os.path.join("data", "tvb_position_state.json")

    def __init__(
        self,
        trading_service: TradingService,
        stock_query_service: StockQueryService,
        stock_code_mapper: StockCodeMapper,
        time_manager: TimeManager,
        config: Optional[TraditionalVBConfig] = None,
        logger: Optional[logging.Logger] = None,
    ):
        self._ts = trading_service
        self._sqs = stock_query_service
        self._mapper = stock_code_mapper
        self._tm = time_manager
        self._cfg = config or TraditionalVBConfig()
        self._logger = logger or logging.getLogger(__name__)

        # 내부 상태
        self._watchlist: Dict[str, WatchlistItem] = {}
        self._watchlist_date: str = ""  # "YYYYMMDD" 형식
        self._position_state: Dict[str, PositionState] = {}

        # 파일에서 포지션 상태 복원
        self._load_state()

    @property
    def name(self) -> str:
        return "거래량돌파(전통)"

    # ── 매수 스캔 ──

    async def scan(self) -> List[TradeSignal]:
        signals: List[TradeSignal] = []
        self._logger.info({"event": "scan_started", "strategy_name": self.name})

        # 1) 워치리스트 빌드 (당일 1회)
        today = self._tm.get_current_kst_time().strftime("%Y%m%d")
        if self._watchlist_date != today:
            await self._build_watchlist()
            self._watchlist_date = today

        if not self._watchlist:
            self._logger.info({"event": "scan_skipped", "reason": "Watchlist is empty"})
            return signals
        
        self._logger.info({"event": "scan_with_watchlist", "count": len(self._watchlist)})

        # 2) 장중 경과 비율 (거래량 환산용)
        market_progress = self._get_market_progress_ratio()
        if market_progress <= 0:
            return signals

        # 3) 각 종목 돌파 조건 체크
        for code, item in self._watchlist.items():
            log_data = {"code": code, "name": item.name, "watchlist_item": asdict(item)}
            try:
                price_resp = await self._sqs.handle_get_current_stock_price(code)
                if not price_resp or price_resp.rt_cd != ErrorCode.SUCCESS.value:
                    continue

                data = price_resp.data or {}
                current = int(data.get("price", "0") or "0")
                acml_vol = int(data.get("acml_vol", "0") or "0")
                log_data.update({"current_price": current, "accumulated_volume": acml_vol})

                if current <= 0:
                    continue

                # 가격 돌파: 현재가 > 20일 최고가
                if current <= item.high_20d:
                    # 너무 많은 로그를 피하기 위해 이 단계는 로그 생략
                    continue

                # 거래량 돌파: 예상 일 거래량 >= 20일 평균 × 1.5
                projected_vol = acml_vol / market_progress if market_progress > 0 else acml_vol
                vol_threshold = item.avg_vol_20d * self._cfg.volume_breakout_multiplier
                log_data.update({"projected_volume": projected_vol, "volume_threshold": vol_threshold})

                if projected_vol < vol_threshold:
                    log_data["reason"] = "Projected volume below threshold"
                    self._logger.info({"event": "candidate_rejected", **log_data})
                    continue
                
                self._logger.info({"event": "breakout_detected", **log_data})

                # 포지션 사이즈 계산
                qty = self._calculate_qty(current)

                # 포지션 상태 기록 + 저장
                self._position_state[code] = PositionState(
                    breakout_level=item.high_20d,
                    peak_price=current,
                )
                self._save_state()

                reason_msg = (
                    f"20일고가({item.high_20d:,}) 돌파, "
                    f"예상거래량 {projected_vol:,.0f} "
                    f"(기준 {vol_threshold:,.0f})"
                )
                signals.append(TradeSignal(
                    code=code, name=item.name, action="BUY", price=current, qty=qty,
                    reason=reason_msg, strategy_name=self.name,
                ))
                self._logger.info({
                    "event": "buy_signal_generated",
                    "code": code, "name": item.name, "price": current, "qty": qty,
                    "reason": reason_msg, "data": log_data,
                })

            except Exception as e:
                self._logger.error({
                    "event": "scan_error", "code": code, "error": str(e),
                }, exc_info=True)
        
        self._logger.info({"event": "scan_finished", "signals_found": len(signals)})
        return signals

    # ── 매도 체크 ──

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
                price_resp = await self._sqs.handle_get_current_stock_price(code)
                if not price_resp or price_resp.rt_cd != ErrorCode.SUCCESS.value:
                    continue

                data = price_resp.data or {}
                current = int(data.get("price", "0") or "0")
                if current <= 0:
                    continue
                
                log_data["current_price"] = current

                # 포지션 상태 가져오기
                state = self._position_state.get(code)
                if not state:
                    self._logger.warning({"event": "missing_position_state", **log_data})
                    state = PositionState(breakout_level=buy_price, peak_price=buy_price)
                    self._position_state[code] = state
                
                log_data["position_state"] = asdict(state)

                # 최고가 갱신
                if current > state.peak_price:
                    state.peak_price = current
                    self._save_state()
                    self._logger.info({"event": "peak_price_updated", "code": code, "new_peak": current})

                reason = ""
                should_sell = False
                pnl_pct = ((current - buy_price) / buy_price) * 100
                log_data["pnl_pct"] = round(pnl_pct, 2)

                # 1) 손절
                if pnl_pct <= self._cfg.stop_loss_pct:
                    reason = f"손절: 매수가({buy_price:,}) 대비 {pnl_pct:.1f}%"
                    should_sell = True

                # 2) 가짜돌파
                if not should_sell and current < state.breakout_level:
                    reason = f"가짜돌파: 현재가({current:,}) < 돌파기준({state.breakout_level:,})"
                    should_sell = True

                # 3) 트레일링 스탑
                if not should_sell and state.peak_price > 0:
                    drop_from_peak = ((current - state.peak_price) / state.peak_price) * 100
                    log_data["drop_from_peak_pct"] = round(drop_from_peak, 2)
                    if drop_from_peak <= -self._cfg.trailing_stop_pct:
                        reason = (
                            f"트레일링스탑: 최고가({state.peak_price:,}) 대비 "
                            f"{drop_from_peak:.1f}%"
                        )
                        should_sell = True

                # 4) 추세종료
                if not should_sell:
                    ma_20d = await self._get_current_ma(code, self._cfg.ma_period)
                    if ma_20d:
                        log_data["ma_20d"] = ma_20d
                        if current < ma_20d:
                            reason = f"추세종료: 현재가({current:,}) < 20일MA({ma_20d:,.0f})"
                            should_sell = True

                if should_sell:
                    self._position_state.pop(code, None)
                    self._save_state()
                    api_stock_name = data.get("name", "") or self._mapper.get_name_by_code(code) or code
                    signals.append(TradeSignal(
                        code=code, name=api_stock_name, action="SELL", price=current, qty=1,
                        reason=reason, strategy_name=self.name,
                    ))
                    self._logger.info({
                        "event": "sell_signal_generated",
                        "code": code, "name": api_stock_name, "price": current,
                        "reason": reason, "data": log_data,
                    })
                else:
                    self._logger.info({"event": "hold_checked", "code": code, "reason": "No exit condition met", "data": log_data})


            except Exception as e:
                self._logger.error({
                    "event": "check_exits_error", "code": code, "error": str(e),
                }, exc_info=True)
        
        self._logger.info({"event": "check_exits_finished", "signals_found": len(signals)})
        return signals

    # ── 워치리스트 빌드 ──

    async def _build_watchlist(self):
        """거래대금 상위 → 코스닥 필터 → 20일 OHLCV → 조건 필터."""
        self._watchlist.clear()
        self._logger.info({"event": "build_watchlist_started"})

        # 1) 거래대금 상위 종목 조회
        resp = await self._ts.get_top_trading_value_stocks()
        if not resp or resp.rt_cd != ErrorCode.SUCCESS.value:
            self._logger.warning({"event": "build_watchlist_failed", "reason": "Failed to get top trading stocks"})
            return

        candidates = resp.data or []
        self._logger.info({"event": "watchlist_candidates_fetched", "count": len(candidates)})
        
        watchlist_items: List[WatchlistItem] = []

        for stock in candidates:
            code = stock.get("mksc_shrn_iscd") or stock.get("stck_shrn_iscd") or ""
            if not code:
                continue

            stock_name = stock.get("hts_kor_isnm", "") or self._mapper.get_name_by_code(code) or code

            try:
                ohlcv = await self._ts.get_recent_daily_ohlcv(code, limit=self._cfg.high_period)
                if not ohlcv or len(ohlcv) < self._cfg.ma_period:
                    continue

                item = self._analyze_ohlcv(code, stock_name, ohlcv)
                if item:
                    watchlist_items.append(item)

            except Exception as e:
                self._logger.error({"event": "build_watchlist_error", "code": code, "error": str(e)}, exc_info=True)

        self._watchlist = {
            item.code: item for item in watchlist_items[:self._cfg.max_watchlist]
        }
        
        self._logger.info({
            "event": "build_watchlist_finished",
            "initial_candidates": len(candidates),
            "final_watchlist_count": len(self._watchlist),
            "watchlist_codes": list(self._watchlist.keys()),
        })

    def _analyze_ohlcv(self, code: str, name: str, ohlcv: List[dict]) -> Optional[WatchlistItem]:
        """20일 OHLCV를 분석하여 조건 충족 시 WatchlistItem 반환."""
        # ... (이하 로직은 로그 추가할 만한 부분이 적어 생략) ...
        if not ohlcv:
            return None

        period = self._cfg.ma_period
        closes = [row.get("close", 0) for row in ohlcv[-period:] if row.get("close")]
        highs = [row.get("high", 0) for row in ohlcv[-period:] if row.get("high")]
        volumes = [row.get("volume", 0) for row in ohlcv[-period:] if row.get("volume")]

        if len(closes) < period or len(highs) < period or len(volumes) < period:
            return None

        ma_20d = sum(closes) / len(closes)
        high_20d = int(max(highs))
        avg_vol_20d = sum(volumes) / len(volumes)
        
        recent_5 = ohlcv[-5:]
        trading_values = [
            (r.get("volume", 0) or 0) * (r.get("close", 0) or 0) for r in recent_5
        ]
        avg_trading_value_5d = sum(trading_values) / len(trading_values) if trading_values else 0
        prev_close = closes[-1]

        log_data = {
            "code": code, "name": name,
            "ma_20d": ma_20d, "high_20d": high_20d, "avg_vol_20d": avg_vol_20d,
            "avg_trading_value_5d": avg_trading_value_5d, "prev_close": prev_close
        }

        if avg_trading_value_5d < self._cfg.min_avg_trading_value_5d:
            self._logger.debug({"event": "ohlcv_filter_rejected", **log_data, "reason": "Avg trading value too low"})
            return None
        if prev_close <= ma_20d:
            self._logger.debug({"event": "ohlcv_filter_rejected", **log_data, "reason": "Not in uptrend (close <= MA20)"})
            return None
        if high_20d > 0:
            distance_pct = ((high_20d - prev_close) / high_20d) * 100
            if distance_pct > self._cfg.near_high_pct:
                self._logger.debug({"event": "ohlcv_filter_rejected", **log_data, "reason": f"Not near high ({distance_pct:.1f}% > {self._cfg.near_high_pct}%)"})
                return None
        
        self._logger.debug({"event": "ohlcv_filter_passed", **log_data})
        return WatchlistItem(
            code=code, name=name, high_20d=high_20d, ma_20d=ma_20d,
            avg_vol_20d=avg_vol_20d, avg_trading_value_5d=avg_trading_value_5d,
        )

    # ── 상태 저장/복원 ──

    def _load_state(self):
        """파일에서 포지션 상태를 복원한다."""
        if not os.path.exists(self.STATE_FILE):
            return
        try:
            with open(self.STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            for code, state_dict in data.items():
                self._position_state[code] = PositionState(**state_dict)
            if self._position_state:
                self._logger.info({
                    "event": "position_state_loaded",
                    "count": len(self._position_state),
                    "codes": list(self._position_state.keys()),
                })
        except (json.JSONDecodeError, IOError, KeyError, TypeError) as e:
            self._logger.warning({"event": "load_state_failed", "error": str(e)})

    def _save_state(self):
        """포지션 상태를 파일에 저장한다."""
        try:
            os.makedirs(os.path.dirname(self.STATE_FILE), exist_ok=True)
            data = {code: asdict(state) for code, state in self._position_state.items()}
            with open(self.STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except (IOError, OSError) as e:
            self._logger.warning({"event": "save_state_failed", "error": str(e)})
    
    # ... (이하 유틸리티 함수는 로깅 추가 불필요) ...
    def _calculate_qty(self, price: int) -> int:
        """포트폴리오 비중 기반 주문 수량 계산."""
        # [테스트용] 고정 수량 모드일 경우 무조건 1주 반환
        if self._cfg.use_fixed_qty:
            return 1

        if price <= 0:
            return self._cfg.min_qty
        budget = self._cfg.total_portfolio_krw * (self._cfg.position_size_pct / 100)
        qty = int(budget / price)
        return max(qty, self._cfg.min_qty)

    def _get_market_progress_ratio(self) -> float:
        """장 시작 이후 경과 비율 (0.0 ~ 1.0). 거래량 환산용."""
        now = self._tm.get_current_kst_time()
        open_time = self._tm.get_market_open_time()
        close_time = self._tm.get_market_close_time()

        total_seconds = (close_time - open_time).total_seconds()
        elapsed_seconds = (now - open_time).total_seconds()

        if total_seconds <= 0 or elapsed_seconds <= 0:
            return 0.0
        return min(elapsed_seconds / total_seconds, 1.0)

    async def _get_current_ma(self, code: str, period: int) -> Optional[float]:
        """종목의 현재 N일 이동평균을 계산."""
        try:
            ohlcv = await self._ts.get_recent_daily_ohlcv(code, limit=period)
            if not ohlcv or len(ohlcv) < period:
                return None
            closes = [row.get("close", 0) for row in ohlcv[-period:] if row.get("close")]
            if len(closes) < period:
                return None
            return sum(closes) / len(closes)
        except Exception:
            return None
