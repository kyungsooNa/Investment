from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, time
from typing import Dict, List, Optional

from common.types import ErrorCode, TradeSignal
from core.logger import get_strategy_logger
from core.market_clock import MarketClock
from interfaces.live_strategy import LiveStrategy
from services.stock_query_service import StockQueryService
from strategies.base_strategy_config import BaseStrategyConfig

# 진입 가능 시간대: 09:10 ~ 14:00
_ENTRY_START = time(9, 10)
_ENTRY_CUTOFF = time(14, 0)

# EOD 강제 청산 시각: 15:20
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
      2. Pool B (당일 거래대금 급등주) 로드
      3. 유동성/규모 필터 (5일 평균 거래대금 100억, 시총 2,000억)
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
        config: Optional[LarryWilliamsVBOConfig] = None,
        logger: Optional[logging.Logger] = None,
    ):
        self._sqs = stock_query_service
        self._tm = market_clock
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

        # 1) 진입 시간대 가드
        current_time = now.time()
        if not (_ENTRY_START <= current_time <= _ENTRY_CUTOFF):
            self._logger.info({"event": "scan_skipped", "reason": f"진입 시간대 외 ({current_time})"})
            return signals

        # 2) Pool B 로드
        candidates = await self._load_pool_b()
        if not candidates:
            return signals
        self._logger.info({"event": "pool_b_loaded", "count": len(candidates)})

        # 3) Range 캐시 갱신 (당일 1회)
        await self._refresh_range_cache(today, [c.get("code", "") for c in candidates])

        for stock in candidates:
            code: str = stock.get("code", "")
            name: str = stock.get("name", code)
            log_data = {"code": code, "name": name}

            if not code:
                continue
            if not self._cfg.allow_reentry and code in self._bought_today:
                continue

            try:
                # 4a) 유동성·규모 필터
                if not await self._passes_validity_filter(code, stock, log_data):
                    continue

                # 4b) 현재가/시가 조회
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

                # 5) Target = Open + Range × K
                rng = self._range_cache.ranges.get(code, 0.0)
                if rng <= 0:
                    log_data["reason"] = "Range 미확보"
                    self._logger.info({"event": "candidate_rejected", **log_data})
                    continue

                target = open_price + rng * self._cfg.k_value
                log_data.update({"open": open_price, "range": rng, "target": round(target), "current": current})

                if current < target:
                    log_data["reason"] = f"현재가({current}) < Target({round(target)})"
                    self._logger.info({"event": "candidate_rejected", **log_data})
                    continue

                # 6) 스냅샷 체결강도 필터
                if not self._passes_confidence_filter(data, log_data):
                    continue

                # 7) 프로그램 순매수 필터
                if not self._passes_program_buy_filter(data, log_data):
                    continue

                # BUY 신호 생성
                reason = (
                    f"VBO돌파: 시가({open_price:,})+Range({rng:.0f})×K({self._cfg.k_value})"
                    f"=Target({round(target):,}) / 현재가({current:,})"
                )
                signals.append(TradeSignal(
                    code=code, name=name, action="BUY", price=current, qty=1,
                    reason=reason, strategy_name=self.name,
                ))
                self._bought_today.add(code)
                self._logger.info({"event": "buy_signal_generated", "strategy_name": self.name,
                                   "code": code, "name": name, "price": current, "reason": reason})

            except Exception as e:
                self._logger.error({"event": "scan_error", "strategy_name": self.name,
                                    "code": code, "error": str(e)}, exc_info=True)

        self._logger.info({"event": "scan_finished", "signals_found": len(signals)})
        return signals

    # ------------------------------------------------------------------
    # check_exits
    # ------------------------------------------------------------------

    async def check_exits(self, holdings: List[dict]) -> List[TradeSignal]:
        signals: List[TradeSignal] = []
        now = self._tm.get_current_kst_time()
        today = now.strftime("%Y%m%d")
        current_time = now.time()
        is_eod = current_time >= _EOD_FLATTEN
        self._logger.info({"event": "check_exits_started", "holdings_count": len(holdings), "is_eod": is_eod})

        for hold in holdings:
            code = str(hold.get("code", ""))
            buy_price_raw = hold.get("buy_price", 0)
            buy_date = str(hold.get("buy_date", ""))
            stock_name = hold.get("name", code)

            if not code or not buy_price_raw:
                continue

            buy_price = float(buy_price_raw)
            log_data = {"code": code, "name": stock_name, "buy_price": buy_price}

            try:
                # 오버나이트 방어: 전일 매수건 즉시 청산
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

                # EOD 강제 청산: 15:20
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
        """Pool B(당일 거래대금 급등주)를 반환한다.

        TODO: OneilUniverseService 의존성을 주입받아 get_pool_b() 호출로 교체.
              현재는 StockQueryService.get_top_trading_value_stocks() 로 대체.
        """
        resp = await self._sqs.get_top_trading_value_stocks()
        if not resp or resp.rt_cd != ErrorCode.SUCCESS.value:
            self._logger.warning({"event": "pool_b_load_failed"})
            return []
        raw = resp.data or []
        result = []
        for item in raw:
            code = item.get("mksc_shrn_iscd") or item.get("stck_shrn_iscd") or ""
            if code:
                result.append({"code": code, "name": item.get("hts_kor_isnm", code), "_raw": item})
        return result

    async def _refresh_range_cache(self, today: str, codes: List[str]) -> None:
        """전일 고저 Range를 종목별로 선취하여 캐시에 저장한다.

        TODO: stock_query_service 의 일봉 조회 API(get_daily_ohlcv 등)로
              각 종목의 전일 high/low를 가져와 range_cache.ranges[code] 에 기록.
              현재는 placeholder — scan() 에서 Range 미확보로 처리됨.
        """
        if self._range_cache.date == today:
            return
        self._range_cache.date = today
        self._range_cache.ranges.clear()
        # TODO: 일봉 API로 전일 고저 조회 후 self._range_cache.ranges[code] = high - low 저장

    async def _passes_validity_filter(self, code: str, stock: dict, log_data: dict) -> bool:
        """5일 평균 거래대금 / 시총 유효성 필터.

        TODO: stock_query_service 에서 5일 평균 거래대금 및 시총을 조회하여 비교.
              Pool B API 응답에 해당 컬럼이 포함되어 있으면 _raw 에서 직접 파싱.
        """
        # TODO: 실제 필터 구현 (현재는 통과)
        return True

    def _passes_confidence_filter(self, data: dict, log_data: dict) -> bool:
        """스냅샷 체결강도 120% 이상 확인.

        data: handle_get_current_stock_price 응답의 .data dict.
        체결강도 필드명: TODO — API 응답 필드 확인 후 키 지정.
        """
        # TODO: data 에서 체결강도 필드 파싱 후 self._cfg.confidence_threshold 와 비교
        # 예시: strength = float(data.get("체결강도_키", "0") or "0")
        #       if strength < self._cfg.confidence_threshold: ...
        return True

    def _passes_program_buy_filter(self, data: dict, log_data: dict) -> bool:
        """프로그램 누적 순매수 >= 거래대금 × 10% AND 양수(+) 확인.

        TODO: 프로그램 순매수 금액 및 거래대금 필드명 확인 후 구현.
              현재가 API가 해당 필드를 제공하지 않으면 별도 API 호출 필요.
        """
        # TODO: 프로그램 순매수 / 거래대금 비율 계산 및 양수 여부 확인
        return True
