from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import time
from typing import TYPE_CHECKING, Dict, List, Optional

from common.date_utils import normalize_yyyymmdd
from common.types import ErrorCode, TradeSignal
from core.logger import get_strategy_logger
from core.market_clock import MarketClock
from interfaces.live_strategy import LiveStrategy
from services.stock_query_service import StockQueryService
from strategies.base_strategy_config import BaseStrategyConfig

if TYPE_CHECKING:
    from services.oneil_universe_service import OneilUniverseService

_ENTRY_START = time(9, 10)
_ENTRY_CUTOFF = time(14, 0)
_EOD_FLATTEN = time(15, 20)


@dataclass
class LarryWilliamsVBOConfig(BaseStrategyConfig):
    k_value: float = 0.5                          # Range 승수 (0.3~0.7 권장)
    min_5d_trading_value: int = 10_000_000_000    # 5일 평균 거래대금 하한 (100억)
    min_market_cap: int = 200_000_000_000         # 시가총액 하한 (2,000억)
    confidence_threshold: float = 120.0           # 스냅샷 체결강도 하한 (%)
    program_buy_ratio: float = 0.10               # 프로그램 순매수 / 거래대금 하한
    stop_loss_pct: float = -3.0                   # 칼손절 기준 (%)
    allow_reentry: bool = False                   # 당일 동일 종목 재진입 금지


@dataclass
class _RangeCache:
    """전일 고저 Range 종목별 캐시 (장 시작 전 1회 선취)."""
    date: str = ""
    ranges: Dict[str, float] = field(default_factory=dict)  # code -> Range


class LarryWilliamsVBOStrategy(LiveStrategy):
    """래리 윌리엄스 변동성 돌파 전략 (Volatility Breakout, VBO).

    scan():
      1. 시간대 가드 (09:10 ~ 14:00)
      2. Pool B 로드 (OneilUniverseService 사용 시 get_watchlist(), 없으면 거래대금 상위 fallback)
      3. 유동성/규모 필터 (5일 평균 거래대금 100억, 시총 2,000억)
         — OSBWatchlistItem의 avg_trading_value_5d / market_cap 직접 활용
      4. Target = Today_Open + (Range × K) 계산
      5. 현재가 >= Target 확인
      6. 스냅샷 체결강도 >= 120%
      7. 프로그램 순매수 >= 거래대금 × 10% AND 양수(+)
      8. BUY TradeSignal 반환

    check_exits():
      - 오버나이트 방어: 전일 매수건 즉시 청산
      - 칼손절: 진입가 대비 -3%
      - EOD 강제청산: 15:20 전량 시장가 매도
    """

    def __init__(
        self,
        stock_query_service: StockQueryService,
        market_clock: MarketClock,
        universe_service: Optional["OneilUniverseService"] = None,
        config: Optional[LarryWilliamsVBOConfig] = None,
        logger: Optional[logging.Logger] = None,
    ):
        self._sqs = stock_query_service
        self._tm = market_clock
        self._universe = universe_service
        self._cfg = config or LarryWilliamsVBOConfig()
        self._logger = logger or get_strategy_logger("LarryWilliamsVBO")

        self._bought_today: set[str] = set()
        self._last_date: str = ""
        self._range_cache = _RangeCache()

    @property
    def name(self) -> str:
        return "래리윌리엄스VBO"

    # ------------------------------------------------------------------
    # scan
    # ------------------------------------------------------------------

    async def scan(self) -> List[TradeSignal]:
        signals: List[TradeSignal] = []
        now = self._tm.get_current_kst_time()
        today = now.strftime("%Y%m%d")
        self._logger.info({"event": "scan_started", "strategy_name": self.name, "time": str(now)})

        # 날짜 변경 시 당일 매수 기록 초기화
        if self._last_date != today:
            self._bought_today.clear()
            self._last_date = today

        # 1) 진입 시간대 가드 (09:10 ~ 14:00)
        current_time = now.time()
        if not (_ENTRY_START <= current_time <= _ENTRY_CUTOFF):
            self._logger.info({"event": "scan_skipped", "reason": f"진입 시간대 외 ({current_time})"})
            return signals

        # 2) Pool B 로드 — OSBWatchlistItem의 avg_trading_value_5d / market_cap 포함
        candidates = await self._load_pool_b()
        if not candidates:
            return signals
        self._logger.info({"event": "pool_b_loaded", "count": len(candidates)})

        # 3) 전일 Range 캐시 갱신 (당일 1회)
        await self._refresh_range_cache(today, [c["code"] for c in candidates if c.get("code")])

        for stock in candidates:
            code: str = stock.get("code", "")
            name: str = stock.get("name", code)
            log_data = {"code": code, "name": name}

            if not code:
                continue
            if not self._cfg.allow_reentry and code in self._bought_today:
                continue

            try:
                # 4) 유동성·규모 필터 (OSBWatchlistItem 내장값 우선 사용)
                if not self._passes_validity_filter(stock, log_data):
                    continue

                # 5) 현재가/시가 조회
                price_resp = await self._sqs.handle_get_current_stock_price(code, caller=self.name)
                if not price_resp or price_resp.rt_cd != ErrorCode.SUCCESS.value:
                    log_data["reason"] = "현재가 조회 실패"
                    self._logger.info({"event": "candidate_rejected", **log_data})
                    continue

                data = price_resp.data or {}
                current = int(data.get("price", "0") or "0")
                open_price = int(data.get("open", "0") or "0")
                if current <= 0 or open_price <= 0:
                    log_data["reason"] = "시가/현재가 0"
                    self._logger.info({"event": "candidate_rejected", **log_data})
                    continue

                # 6) Target = Open + Range × K
                rng = self._range_cache.ranges.get(code, 0.0)
                if rng <= 0:
                    log_data["reason"] = "Range 미확보 (전일 일봉 조회 실패)"
                    self._logger.info({"event": "candidate_rejected", **log_data})
                    continue

                target = open_price + rng * self._cfg.k_value
                log_data.update({"open": open_price, "range": rng, "target": round(target), "current": current})

                if current < target:
                    log_data["reason"] = f"현재가({current}) < Target({round(target)})"
                    self._logger.info({"event": "candidate_rejected", **log_data})
                    continue

                # 7) 스냅샷 체결강도 >= 120%
                cgld = await self._get_execution_strength(code)
                log_data["execution_strength"] = cgld
                if cgld < self._cfg.confidence_threshold:
                    log_data["reason"] = f"체결강도({cgld:.1f}%) < {self._cfg.confidence_threshold}%"
                    self._logger.info({"event": "candidate_rejected", **log_data})
                    continue

                # 8) 프로그램 순매수 >= 거래대금 × 10% AND 양수(+)
                if not self._passes_program_buy_filter(data, log_data):
                    continue

                # BUY 신호 생성
                reason = (
                    f"VBO돌파: Open({open_price:,})+Range({rng:.0f})×K{self._cfg.k_value}"
                    f"=Target({round(target):,}) / 현재({current:,}) / 체결강도({cgld:.1f}%)"
                )
                signals.append(TradeSignal(
                    code=code, name=name, action="BUY", price=current, qty=1,
                    reason=reason, strategy_name=self.name,
                    stop_loss_pct=self._cfg.stop_loss_pct,
                ))
                self._bought_today.add(code)
                self._logger.info({
                    "event": "buy_signal_generated", "strategy_name": self.name,
                    "code": code, "name": name, "price": current, "reason": reason,
                })

            except Exception as e:
                self._logger.error({
                    "event": "scan_error", "strategy_name": self.name,
                    "code": code, "error": str(e),
                }, exc_info=True)

        self._logger.info({"event": "scan_finished", "signals_found": len(signals)})
        return signals

    # ------------------------------------------------------------------
    # check_exits
    # ------------------------------------------------------------------

    async def check_exits(self, holdings: List[dict]) -> List[TradeSignal]:
        signals: List[TradeSignal] = []
        now = self._tm.get_current_kst_time()
        today = now.strftime("%Y%m%d")
        is_eod = now.time() >= _EOD_FLATTEN
        self._logger.info({"event": "check_exits_started", "holdings_count": len(holdings), "is_eod": is_eod})

        for hold in holdings:
            code = str(hold.get("code", ""))
            buy_price_raw = hold.get("buy_price", 0)
            buy_date_raw = hold.get("buy_date", "")
            buy_date = normalize_yyyymmdd(buy_date_raw)
            stock_name = hold.get("name", code)

            if not code or not buy_price_raw:
                continue

            buy_price = float(buy_price_raw)
            log_data = {"code": code, "name": stock_name, "buy_price": buy_price}

            try:
                # 오버나이트 방어: 매수일 ≠ 오늘이면 즉시 시장가 청산
                if buy_date and buy_date != today:
                    qty = int(hold.get("qty", 1))
                    reason = f"오버나이트방어: 매수일({buy_date}) ≠ 오늘({today})"
                    signals.append(TradeSignal(
                        code=code, name=stock_name, action="SELL", price=0, qty=qty,
                        reason=reason, strategy_name=self.name,
                    ))
                    self._logger.info({"event": "sell_signal_generated", "strategy_name": self.name,
                                       "code": code, "reason": reason})
                    continue

                price_resp = await self._sqs.handle_get_current_stock_price(code, caller=self.name)
                if not price_resp or price_resp.rt_cd != ErrorCode.SUCCESS.value:
                    self._logger.warning({"event": "check_exits_price_fail", **log_data})
                    continue

                data = price_resp.data or {}
                current = int(data.get("price", "0") or "0")
                if current <= 0:
                    continue

                pnl_pct = (current - buy_price) / buy_price * 100
                log_data.update({"current": current, "pnl_pct": round(pnl_pct, 2)})

                reason = ""
                should_sell = False

                # 칼손절: 진입가 대비 -3%
                if pnl_pct <= self._cfg.stop_loss_pct:
                    reason = f"칼손절: 매수가대비 {pnl_pct:.1f}%"
                    should_sell = True

                # EOD 강제청산: 15:20
                if not should_sell and is_eod:
                    reason = f"EOD청산: {_EOD_FLATTEN.strftime('%H:%M')} 전량 시장가"
                    should_sell = True

                if should_sell:
                    qty = int(hold.get("qty", 1))
                    signals.append(TradeSignal(
                        code=code, name=stock_name, action="SELL", price=current, qty=qty,
                        reason=reason, strategy_name=self.name,
                    ))
                    self._logger.info({"event": "sell_signal_generated", "strategy_name": self.name,
                                       "code": code, "reason": reason, "data": log_data})
                else:
                    self._logger.info({"event": "hold_checked", "code": code,
                                       "reason": "청산 조건 미충족", "data": log_data})

            except Exception as e:
                self._logger.error({"event": "check_exits_error", "strategy_name": self.name,
                                    "code": code, "error": str(e)}, exc_info=True)

        self._logger.info({"event": "check_exits_finished", "signals_found": len(signals)})
        return signals

    # ------------------------------------------------------------------
    # 내부 헬퍼
    # ------------------------------------------------------------------

    async def _load_pool_b(self) -> List[dict]:
        """Pool B 로드.

        OneilUniverseService가 주입된 경우: get_watchlist()로 OSBWatchlistItem 목록 반환.
          - avg_trading_value_5d, market_cap이 이미 계산된 상태로 제공됨.
        없는 경우: get_top_trading_value_stocks() fallback.
        """
        if self._universe is not None:
            try:
                watchlist = await self._universe.get_watchlist(logger=self._logger)
                result = []
                for item in watchlist.values():
                    result.append({
                        "code": item.code,
                        "name": item.name,
                        "market_cap": item.market_cap,          # 원 단위
                        "avg_5d_tv": item.avg_trading_value_5d, # 원 단위
                    })
                return result
            except Exception as e:
                self._logger.warning({"event": "pool_b_universe_error", "error": str(e)})
                return []

        # fallback: 거래대금 상위 종목
        resp = await self._sqs.get_top_trading_value_stocks()
        if not resp or resp.rt_cd != ErrorCode.SUCCESS.value:
            self._logger.warning({"event": "pool_b_load_failed"})
            return []
        result = []
        for item in (resp.data or []):
            code = item.get("mksc_shrn_iscd") or item.get("stck_shrn_iscd") or ""
            if code:
                result.append({
                    "code": code,
                    "name": item.get("hts_kor_isnm", code),
                    "market_cap": int(item.get("stck_avls", "0") or "0"),
                    "avg_5d_tv": 0,  # fallback 시 미제공 → 필터 스킵
                })
        return result

    async def _refresh_range_cache(self, today: str, codes: List[str]) -> None:
        """전일 고저 Range를 일봉 API로 선취하여 캐시에 저장한다 (당일 1회).

        get_recent_daily_ohlcv(limit=2) 반환:
          rows[0] = 가장 최근 완성된 일봉 (장중에는 전일)
          각 row: {date, open, high, low, close, volume}
        """
        if self._range_cache.date == today:
            return
        self._range_cache.date = today
        self._range_cache.ranges.clear()

        for code in codes:
            try:
                resp = await self._sqs.get_recent_daily_ohlcv(code, limit=2)
                if not resp or resp.rt_cd != ErrorCode.SUCCESS.value:
                    continue
                rows: list = resp.data or []
                if not rows:
                    continue

                yesterday = rows[0]  # 가장 최근 완성된 일봉
                high = int(yesterday.get("high", 0) or 0)
                low = int(yesterday.get("low", 0) or 0)
                if high > low > 0:
                    self._range_cache.ranges[code] = float(high - low)

            except Exception as e:
                self._logger.warning({"event": "range_cache_error", "code": code, "error": str(e)})

    def _passes_validity_filter(self, stock: dict, log_data: dict) -> bool:
        """시가총액 / 5일 평균 거래대금 필터.

        OSBWatchlistItem 기반(universe_service 사용 시): market_cap, avg_5d_tv 직접 사용.
        fallback 시: market_cap만 체크 (avg_5d_tv=0이면 스킵).
        """
        market_cap = stock.get("market_cap", 0) or 0
        avg_5d_tv = stock.get("avg_5d_tv", 0) or 0

        if market_cap > 0 and market_cap < self._cfg.min_market_cap:
            log_data["reason"] = f"시총({market_cap:,}) < {self._cfg.min_market_cap:,}"
            self._logger.info({"event": "candidate_rejected", **log_data})
            return False

        if avg_5d_tv > 0 and avg_5d_tv < self._cfg.min_5d_trading_value:
            log_data["reason"] = f"5일평균거래대금({avg_5d_tv:,.0f}) < {self._cfg.min_5d_trading_value:,}"
            self._logger.info({"event": "candidate_rejected", **log_data})
            return False

        return True

    async def _get_execution_strength(self, code: str) -> float:
        """체결강도(%) 스냅샷 1회 조회.

        get_stock_conclusion() → data["output"][0]["tday_rltv"]
        """
        try:
            resp = await self._sqs.get_stock_conclusion(code)
            if resp and resp.rt_cd == ErrorCode.SUCCESS.value:
                output = resp.data.get("output") if isinstance(resp.data, dict) else None
                if output and isinstance(output, list) and output:
                    val = output[0].get("tday_rltv")
                    return float(val) if val else 0.0
        except Exception as e:
            self._logger.warning({"event": "execution_strength_error", "code": code, "error": str(e)})
        return 0.0

    def _passes_program_buy_filter(self, data: dict, log_data: dict) -> bool:
        """프로그램 누적 순매수 >= 거래대금 × 10% AND 양수(+) 확인.

        ResStockFullInfoApiOutput 필드:
          pgtr_ntby_qty : 프로그램 순매수 수량 (주, 음수 가능)
          stck_prpr     : 현재가 (원)
          acml_tr_pbmn  : 누적 거래대금 (원)
        """
        try:
            ntby_qty = int(data.get("pgtr_ntby_qty") or "0")
            prpr = int(data.get("stck_prpr") or data.get("price") or "0")
            acml_tv = int(data.get("acml_tr_pbmn") or "0")

            if acml_tv <= 0 or prpr <= 0:
                log_data["reason"] = "프로그램 순매수 계산 불가 (거래대금/현재가 0)"
                self._logger.info({"event": "candidate_rejected", **log_data})
                return False

            ntby_amt = ntby_qty * prpr  # 순매수 금액 (부호 유지)
            ratio = ntby_amt / acml_tv

            log_data.update({"program_ntby_qty": ntby_qty, "program_ratio": round(ratio * 100, 2)})

            if ntby_amt <= 0:
                log_data["reason"] = f"프로그램 순매수 음수 ({ntby_amt:,}원)"
                self._logger.info({"event": "candidate_rejected", **log_data})
                return False

            if ratio < self._cfg.program_buy_ratio:
                log_data["reason"] = f"프로그램비율({ratio*100:.1f}%) < {self._cfg.program_buy_ratio*100:.0f}%"
                self._logger.info({"event": "candidate_rejected", **log_data})
                return False

        except Exception as e:
            self._logger.warning({"event": "program_filter_error", "code": log_data.get("code"), "error": str(e)})
            return False

        return True
