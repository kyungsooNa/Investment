from __future__ import annotations

import logging
from collections import Counter
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
from utils.async_concurrency import bounded_gather
from utils.transaction_cost_utils import TransactionCostUtils
from utils.volatility_utils import annualized_return_std

if TYPE_CHECKING:
    from services.oneil_universe_service import OneilUniverseService


async def _fetch_volatility_for_signal(sqs: StockQueryService, code: str) -> Optional[float]:
    """매수 신호 발화 직전 1회 호출: 21일 종가로 연환산 변동성 계산."""
    try:
        resp = await sqs.get_recent_daily_ohlcv(code, limit=21)
        if not resp or resp.rt_cd != ErrorCode.SUCCESS.value or not resp.data:
            return None
        return annualized_return_std([r.get("close") for r in resp.data])
    except Exception:
        return None

_ENTRY_START = time(9, 10)
_ENTRY_CUTOFF = time(14, 0)
_EOD_FLATTEN = time(15, 20)

_RANGE_CACHE_CONCURRENCY = 10
_EXIT_CONCURRENCY = 15


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
        # P2 2-4: event-driven shadow 평가용 — scan() 직후 후보 종목 집합을 보존
        self._current_candidate_codes_set: set[str] = set()
        # P2 2-4 진단: evaluate_single 게이트별 탈락/통과 카운터 (code -> Counter).
        # scan() 종료 시 1회 요약 로깅, 날짜 변경 시 초기화.
        self._shadow_eval_stats: Dict[str, Counter] = {}

    @property
    def name(self) -> str:
        return "래리윌리엄스VBO"

    @property
    def strategy_id(self) -> str:
        return "larry_williams_vbo"

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
            self._shadow_eval_stats.clear()
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

        # 2-1) 시장 국면 게이트 — universe 가 있을 때만 수행 (state 변경 전)
        market_timing: Dict[str, bool] = {}
        if self._universe is not None:
            market_timing = {
                "KOSPI": await self._universe.is_market_timing_ok("KOSPI", caller=self.name, logger=self._logger),
                "KOSDAQ": await self._universe.is_market_timing_ok("KOSDAQ", caller=self.name, logger=self._logger),
            }
            if not any(market_timing.values()):
                self._logger.info({"event": "scan_skipped", "reason": "market_timing_off_both"})
                return signals

        candidate_codes = [c["code"] for c in candidates if c.get("code")]
        await self._sqs.prefetch_prices(candidate_codes)

        # 3) 전일 Range 캐시 갱신 (당일 1회)
        await self._refresh_range_cache(today, candidate_codes)

        # P2 2-4: event-driven shadow 구독 대상 — 본 scan 의 pool B 멤버십 = 구독 후보.
        # evaluate_single 내부에서 range/time/bought_today 등 세부 게이트를 다시 확인한다.
        self._current_candidate_codes_set = set(candidate_codes)

        for stock in candidates:
            code: str = stock.get("code", "")
            name: str = stock.get("name", code)
            log_data = {"code": code, "name": name}

            if not code:
                continue
            if not self._cfg.allow_reentry and code in self._bought_today:
                continue

            # 시장 국면 게이트 (종목별) — bear regime 이면 state/주문 영향 없이 skip
            if market_timing:
                stock_market = stock.get("market", "")
                if stock_market and not market_timing.get(stock_market, False):
                    self._log_entry_rejected(log_data, "market_timing_off", f"{stock_market} 마켓 타이밍 차단")
                    continue

            try:
                # 4) 유동성·규모 필터 (OSBWatchlistItem 내장값 우선 사용)
                if not self._passes_validity_filter(stock, log_data):
                    continue

                # 5) 현재가/시가 조회
                price_resp = await self._sqs.handle_get_current_stock_price(
                    code,
                    caller=self.name,
                    allow_snapshot=False,
                )
                if not price_resp or price_resp.rt_cd != ErrorCode.SUCCESS.value:
                    self._log_entry_rejected(log_data, "price_unavailable", "현재가 조회 실패")
                    continue

                data = price_resp.data or {}
                current = int(data.get("price", "0") or "0")
                open_price = int(data.get("open", "0") or "0")
                if current > 0 and open_price <= 0:
                    open_price = await self._fetch_intraday_open_price(code, now)
                    if open_price > 0:
                        log_data["open_price_source"] = "intraday_minutes"
                if current <= 0 or open_price <= 0:
                    log_data.update({"current": current, "open": open_price})
                    self._log_entry_rejected(log_data, "invalid_price", "시가/현재가 0")
                    continue

                # 6) Target = Open + Range × K
                rng = self._range_cache.ranges.get(code, 0.0)
                if rng <= 0:
                    self._log_entry_rejected(
                        log_data,
                        "range_unavailable",
                        "Range 미확보 (전일 일봉 조회 실패)",
                    )
                    continue

                target = open_price + rng * self._cfg.k_value
                log_data.update({"open": open_price, "range": rng, "target": round(target), "current": current})

                if current < target:
                    self._log_entry_rejected(
                        log_data,
                        "below_target",
                        f"현재가({current}) < Target({round(target)})",
                    )
                    continue

                # 7) 스냅샷 체결강도 >= 120%
                cgld = await self._get_execution_strength(code)
                log_data["execution_strength"] = cgld
                if cgld < self._cfg.confidence_threshold:
                    self._log_entry_rejected(
                        log_data,
                        "low_execution_strength",
                        f"체결강도({cgld:.1f}%) < {self._cfg.confidence_threshold}%",
                    )
                    continue

                # 8) 프로그램 순매수 >= 거래대금 × 10% AND 양수(+)
                if not self._passes_program_buy_filter(data, log_data):
                    continue

                # BUY 신호 생성
                reason = (
                    f"VBO돌파: Open({open_price:,})+Range({rng:.0f})×K{self._cfg.k_value}"
                    f"=Target({round(target):,}) / 현재({current:,}) / 체결강도({cgld:.1f}%)"
                )
                volatility = await _fetch_volatility_for_signal(self._sqs, code)
                signals.append(TradeSignal(
                    code=code, name=name, action="BUY", price=current,
                    reason=reason, strategy_name=self.name,
                    stop_loss_pct=self._cfg.stop_loss_pct,
                    entry_reason="larry_williams_vbo_breakout",
                    invalidation_price=round(current * (1 + self._cfg.stop_loss_pct / 100), 2),
                    stop_loss_price=round(current * (1 + self._cfg.stop_loss_pct / 100), 2),
                    trailing_rule="same_day_eod_or_stop",
                    expected_holding_period_days=1,
                    confidence=min(1.0, max(0.0, cgld / max(self._cfg.confidence_threshold, 1.0))),
                    required_data=[
                        "pool_b_candidate",
                        "current_price",
                        "previous_day_range",
                        "execution_strength",
                        "program_buy",
                        "liquidity_filter",
                    ],
                    volatility_20d_annualized=volatility,
                ))
                self._bought_today.add(code)
                self._logger.info({
                    "event": "buy_signal_generated", "strategy_name": self.name,
                    "code": code, "name": name, "price": current, "reason": reason,
                    "metrics": {"volatility_20d_annualized": volatility},
                })

            except Exception as e:
                self._logger.error({
                    "event": "scan_error", "strategy_name": self.name,
                    "code": code, "error": str(e),
                }, exc_info=True)

        self._logger.info({"event": "scan_finished", "signals_found": len(signals)})
        # P2 2-4 진단: shadow fast-path 게이트 통계 요약 (틱 미수신 vs 게이트 탈락 구분용)
        if self._shadow_eval_stats:
            self._logger.info({
                "event": "shadow_eval_stats",
                "strategy_name": self.name,
                "stats": {c: dict(ctr) for c, ctr in self._shadow_eval_stats.items()},
            })
        return signals

    # ------------------------------------------------------------------
    # evaluate_single — event-driven shadow fast-path (P2 2-4)
    # ------------------------------------------------------------------

    async def evaluate_single(self, code: str, snapshot: dict) -> Optional[TradeSignal]:
        """단일 종목 fast-path 평가. 게이트:

        1. 진입 시간대 (09:10–14:00)
        2. code 가 본 scan 의 pool B 후보에 포함
        3. _bought_today 미포함
        4. _range_cache 에 range 존재
        5. snapshot.open / snapshot.price 둘 다 양수
        6. current >= open + range × K  →  BUY signal

        execution_strength / program_buy 필터는 shadow 한정으로 생략한다. 폴링
        경로가 동일 시점에 안전망으로 재검증한다.

        진단(P2 2-4): 각 게이트 탈락/통과 사유를 `_shadow_eval_stats[code]` 에
        누적한다. scan() 종료 시 `shadow_eval_stats` 로그로 요약하며, 다음 장에서
        틱 미수신(evaluated≈0) vs 게이트 탈락(reject_*) 을 구분한다.
        """
        if not code:
            return None

        stats = self._shadow_eval_stats.setdefault(code, Counter())
        stats["evaluated"] += 1

        if code not in self._current_candidate_codes_set:
            stats["reject_not_candidate"] += 1
            return None

        now = self._tm.get_current_kst_time()
        current_time = now.time()
        if not (_ENTRY_START <= current_time <= _ENTRY_CUTOFF):
            stats["reject_outside_window"] += 1
            return None

        if code in self._bought_today:
            stats["reject_already_bought"] += 1
            return None

        rng = self._range_cache.ranges.get(code, 0.0)
        if rng <= 0:
            stats["reject_range_missing"] += 1
            return None

        try:
            open_price = float(snapshot.get("open") or 0)
            price_raw = snapshot.get("price")
            current = int(price_raw) if price_raw not in (None, "", "0") else 0
        except (TypeError, ValueError):
            stats["reject_bad_snapshot"] += 1
            return None

        if open_price <= 0:
            stats["reject_invalid_open"] += 1
            return None
        if current <= 0:
            stats["reject_invalid_price"] += 1
            return None

        target = open_price + rng * self._cfg.k_value
        if current < target:
            stats["reject_below_target"] += 1
            return None

        stats["signal"] += 1
        reason = (
            f"VBO돌파(shadow): Open({int(open_price):,})+Range({rng:.0f})×K{self._cfg.k_value}"
            f"=Target({round(target):,}) / 현재({current:,})"
        )
        return TradeSignal(
            code=code,
            name=code,
            action="BUY",
            price=current,
            reason=reason,
            strategy_name=self.name,
            stop_loss_pct=self._cfg.stop_loss_pct,
            entry_reason="larry_williams_vbo_shadow_breakout",
            invalidation_price=round(current * (1 + self._cfg.stop_loss_pct / 100), 2),
            stop_loss_price=round(current * (1 + self._cfg.stop_loss_pct / 100), 2),
            trailing_rule="same_day_eod_or_stop",
            expected_holding_period_days=1,
            confidence=0.5,
            required_data=[
                "event_snapshot",
                "previous_day_range",
                "current_candidate_codes",
            ],
        )

    async def evaluate_exit_single(self, code: str, snapshot: dict, holding: dict) -> Optional[TradeSignal]:
        """보유 종목 손절(net) 조건을 snapshot 기반으로 평가 (P2 2-4 exit shadow).

        check_exits 의 손절 트리거(net_return_pct ≤ stop_loss_pct)만 복제한다. 오버나이트/EOD
        같은 시간 기반 청산은 latency 무관이라 제외한다. 결과는 shadow 기록 전용 (실 주문 미발생).
        """
        try:
            buy_price = float(holding.get("buy_price", 0) or 0)
            price_raw = snapshot.get("price")
            current = int(price_raw) if price_raw not in (None, "", "0") else 0
        except (TypeError, ValueError):
            return None

        if buy_price <= 0 or current <= 0:
            return None

        # P0 0-9: check_exits 와 동일하게 net 수익률 기준 손절.
        pnl_pct = TransactionCostUtils.net_return_pct(buy_price, current)
        if pnl_pct > self._cfg.stop_loss_pct:
            return None

        return TradeSignal(
            code=code,
            name=holding.get("name", code),
            action="SELL",
            price=current,
            qty=int(holding.get("qty", 1) or 1),
            reason=f"칼손절(net,shadow): 매수가대비 {pnl_pct:.1f}%",
            strategy_name=self.name,
        )

    def current_candidate_codes(self) -> List[str]:
        return list(self._current_candidate_codes_set)

    # ------------------------------------------------------------------
    # check_exits
    # ------------------------------------------------------------------

    async def check_exits(self, holdings: List[dict]) -> List[TradeSignal]:
        now = self._tm.get_current_kst_time()
        today = now.strftime("%Y%m%d")
        is_eod = now.time() >= _EOD_FLATTEN
        self._logger.info({"event": "check_exits_started", "holdings_count": len(holdings), "is_eod": is_eod})

        results = await bounded_gather(
            [self._check_single_exit(hold, today, is_eod) for hold in holdings],
            limit=_EXIT_CONCURRENCY,
            return_exceptions=True,
        )

        signals: List[TradeSignal] = []
        for result in results:
            if isinstance(result, Exception):
                self._logger.error({"event": "check_exits_error", "strategy_name": self.name,
                                    "error": str(result)}, exc_info=True)
                continue
            if result is not None:
                signals.append(result)

        self._logger.info({"event": "check_exits_finished", "signals_found": len(signals)})
        return signals

    async def _check_single_exit(self, hold: dict, today: str, is_eod: bool) -> Optional[TradeSignal]:
        code = str(hold.get("code", ""))
        buy_price_raw = hold.get("buy_price", 0)
        buy_date_raw = hold.get("buy_date", "")
        buy_date = normalize_yyyymmdd(buy_date_raw)
        stock_name = hold.get("name", code)

        if not code or not buy_price_raw:
            return None

        buy_price = float(buy_price_raw)
        log_data = {"code": code, "name": stock_name, "buy_price": buy_price}

        try:
            # 오버나이트 방어: 매수일 ≠ 오늘이면 즉시 시장가 청산
            if buy_date and buy_date != today:
                qty = int(hold.get("qty", 1))
                reason = f"오버나이트방어: 매수일({buy_date}) ≠ 오늘({today})"
                signal = TradeSignal(
                    code=code, name=stock_name, action="SELL", price=0, qty=qty,
                    reason=reason, strategy_name=self.name,
                )
                self._logger.info({"event": "sell_signal_generated", "strategy_name": self.name,
                                   "code": code, "reason": reason})
                return signal

            price_resp = await self._sqs.handle_get_current_stock_price(code, caller=self.name)
            if not price_resp or price_resp.rt_cd != ErrorCode.SUCCESS.value:
                self._logger.warning({"event": "check_exits_price_fail", **log_data})
                return None

            data = price_resp.data or {}
            current = int(data.get("price", "0") or "0")
            if current <= 0:
                return None

            # P0 0-9: 비용 반영 net 수익률 — backtest 와 동일 기준으로 stop trigger.
            pnl_pct = TransactionCostUtils.net_return_pct(buy_price, current)
            log_data.update({"current": current, "pnl_pct": round(pnl_pct, 2), "pnl_basis": "net"})

            reason = ""
            should_sell = False

            # 칼손절: 진입가 대비 -3% (net, P0 0-9)
            if pnl_pct <= self._cfg.stop_loss_pct:
                reason = f"칼손절(net): 매수가대비 {pnl_pct:.1f}%"
                should_sell = True

            # EOD 강제청산: 15:20
            if not should_sell and is_eod:
                reason = f"EOD청산: {_EOD_FLATTEN.strftime('%H:%M')} 전량 시장가"
                should_sell = True

            if should_sell:
                qty = int(hold.get("qty", 1))
                signal = TradeSignal(
                    code=code, name=stock_name, action="SELL", price=current, qty=qty,
                    reason=reason, strategy_name=self.name,
                )
                self._logger.info({"event": "sell_signal_generated", "strategy_name": self.name,
                                   "code": code, "reason": reason, "data": log_data})
                return signal

            self._logger.info({"event": "hold_checked", "code": code,
                               "reason": "청산 조건 미충족", "data": log_data})
            return None

        except Exception as e:
            self._logger.error({"event": "check_exits_error", "strategy_name": self.name,
                                "code": code, "error": str(e)}, exc_info=True)
            return None

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
                        "market": getattr(item, "market", ""),
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
                    "avg_5d_tv": 0,  # fallback 시 미제공 → validity filter에서 fail-closed reject
                })
        return result

    async def _refresh_range_cache(self, today: str, codes: List[str]) -> None:
        """전일 고저 Range를 일봉 API로 선취하여 캐시에 저장한다 (당일 1회).

        get_recent_daily_ohlcv(limit=2) 반환:
          rows[0] = 가장 최근 완성된 일봉 (장중에는 전일)
          각 row: {date, open, high, low, close, volume}
        """
        if self._range_cache.date != today:
            self._range_cache.date = today
            self._range_cache.ranges.clear()

        # 성공한 Range만 당일 캐시한다. 실패/빈 응답 종목은 다음 스캔에서 재시도한다.
        codes = [code for code in codes if code not in self._range_cache.ranges]

        if not codes:
            return

        results = await bounded_gather(
            [self._sqs.get_recent_daily_ohlcv(code, limit=2) for code in codes],
            limit=_RANGE_CACHE_CONCURRENCY,
            return_exceptions=True,
        )

        for code, resp in zip(codes, results):
            if isinstance(resp, Exception):
                self._logger.warning({"event": "range_cache_error", "code": code, "error": str(resp)})
                continue
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

    def _passes_validity_filter(self, stock: dict, log_data: dict) -> bool:
        """시가총액 / 5일 평균 거래대금 필터.

        OSBWatchlistItem 기반(universe_service 사용 시): market_cap, avg_5d_tv 직접 사용.
        fallback 시: avg_5d_tv 미제공이면 fail-closed로 차단한다.
        """
        market_cap = stock.get("market_cap", 0) or 0
        avg_5d_tv = stock.get("avg_5d_tv", 0) or 0

        if market_cap > 0 and market_cap < self._cfg.min_market_cap:
            self._log_entry_rejected(
                log_data,
                "market_cap_below_min",
                f"시총({market_cap:,}) < {self._cfg.min_market_cap:,}",
            )
            return False

        if avg_5d_tv <= 0:
            self._log_entry_rejected(
                log_data,
                "avg_trading_value_unknown",
                "5일평균거래대금 미제공 (fallback 경로)",
            )
            return False

        if avg_5d_tv < self._cfg.min_5d_trading_value:
            self._log_entry_rejected(
                log_data,
                "avg_trading_value_below_min",
                f"5일평균거래대금({avg_5d_tv:,.0f}) < {self._cfg.min_5d_trading_value:,}",
            )
            return False

        return True

    async def _fetch_intraday_open_price(self, code: str, now) -> int:
        """상세 현재가 시가가 비어 있을 때 당일 첫 분봉 시가로 보강한다."""
        try:
            rows = await self._sqs.get_day_intraday_minutes_list(
                code,
                session="REGULAR",
                start_hhmmss="090000",
                end_hhmmss=now.strftime("%H%M%S"),
            )
        except Exception as e:
            self._logger.debug({"event": "intraday_open_fallback_failed", "code": code, "error": str(e)})
            return 0

        for row in rows or []:
            if not isinstance(row, dict):
                continue
            for key in ("stck_oprc", "open", "oprc", "stck_prpr", "price"):
                try:
                    value = int(float(row.get(key) or 0))
                except (TypeError, ValueError):
                    value = 0
                if value > 0:
                    return value
        return 0

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
                self._log_entry_rejected(
                    log_data,
                    "program_buy_unavailable",
                    "프로그램 순매수 계산 불가 (거래대금/현재가 0)",
                )
                return False

            ntby_amt = ntby_qty * prpr  # 순매수 금액 (부호 유지)
            ratio = ntby_amt / acml_tv

            log_data.update({"program_ntby_qty": ntby_qty, "program_ratio": round(ratio * 100, 2)})

            if ntby_amt <= 0:
                self._log_entry_rejected(
                    log_data,
                    "negative_program_buy",
                    f"프로그램 순매수 음수 ({ntby_amt:,}원)",
                )
                return False

            if ratio < self._cfg.program_buy_ratio:
                self._log_entry_rejected(
                    log_data,
                    "low_program_buy_ratio",
                    f"프로그램비율({ratio*100:.1f}%) < {self._cfg.program_buy_ratio*100:.0f}%",
                )
                return False

        except Exception as e:
            self._logger.warning({"event": "program_filter_error", "code": log_data.get("code"), "error": str(e)})
            return False

        return True

    def _log_entry_rejected(self, log_data: dict, reason: str, message: str) -> None:
        payload = {**log_data, "reason": reason, "message": message}
        self._logger.info({"event": "candidate_rejected", **payload})
        self._logger.info({"event": "entry_rejected", **payload})
