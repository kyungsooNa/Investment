"""Minervini Stage 2 transition breakout strategy.

Stage 2 is the universe, entry regime, and holding regime.  A stock is eligible
only shortly after transitioning into Stage 2, and entry waits for a controlled
20-day breakout in a liquid large-cap name.  Profit and time exits deliberately
keep the strategy from becoming another low-hit-rate, long-tail breakout system.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, List, Optional

from common.date_utils import normalize_yyyymmdd, previous_trading_day_str
from common.types import TradeSignal
from interfaces.live_strategy import LiveStrategy


@dataclass(frozen=True)
class MinerviniStage2TransitionConfig:
    min_rs_rating: int = 80
    min_market_cap_100m: int = 30_000
    min_trading_value: int = 30_000_000_000
    liquidity_lookback_days: int = 20
    max_candidates: int = 30
    transition_lookback_days: int = 10
    breakout_lookback_days: int = 20
    max_breakout_extension_pct: float = 5.0
    take_profit_pct: float = 4.0
    hard_stop_pct: float = -5.0
    max_holding_days: int = 5
    ohlcv_lookback_days: int = 260


class MinerviniStage2TransitionStrategy(LiveStrategy):
    """Buy recent Stage 2 transitions on a 20-day high breakout."""

    def __init__(
        self,
        stock_query_service,
        minervini_stage_service,
        market_clock,
        config: Optional[MinerviniStage2TransitionConfig] = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._sqs = stock_query_service
        self._stage_service = minervini_stage_service
        self._tm = market_clock
        self._cfg = config or MinerviniStage2TransitionConfig()
        self._logger = logger or logging.getLogger(__name__)
        self._current_candidate_codes_set: set[str] = set()

    @property
    def name(self) -> str:
        return "미너비니Stage2전환"

    @property
    def strategy_id(self) -> str:
        return "minervini_stage2_transition"

    async def scan(self) -> List[TradeSignal]:
        self._current_candidate_codes_set.clear()
        if self._stage_service is None:
            self._logger.warning("Minervini Stage service unavailable; entry scan skipped")
            return []
        response = await self._stage_service.get_stage2_list()
        if not response or response.rt_cd != "0" or not response.data:
            return []

        candidates = [
            item for item in response.data
            if isinstance(item, dict)
            and int(item.get("stage", 2) or 0) == 2
            and self._number(item.get("rs_rating")) >= self._cfg.min_rs_rating
            and self._number(item.get("market_cap")) >= self._cfg.min_market_cap_100m
            and item.get("code")
        ]
        candidates.sort(key=lambda item: self._number(item.get("rs_rating")), reverse=True)
        candidates = candidates[:self._cfg.max_candidates]
        self._current_candidate_codes_set = {str(item["code"]) for item in candidates}

        prefetch = getattr(self._sqs, "prefetch_prices", None)
        if callable(prefetch):
            await prefetch([str(item["code"]) for item in candidates])

        results = await asyncio.gather(
            *(self._check_entry(item) for item in candidates),
            return_exceptions=True,
        )
        signals: List[TradeSignal] = []
        for result in results:
            if isinstance(result, Exception):
                self._logger.error("Minervini Stage2 entry scan error: %s", result)
            elif result is not None:
                signals.append(result)
        return signals

    async def _check_entry(self, item: dict) -> Optional[TradeSignal]:
        code = str(item["code"])
        current_response, ohlcv_response = await asyncio.gather(
            self._sqs.get_current_price(code, caller=self.name),
            self._sqs.get_recent_daily_ohlcv(
                code,
                limit=self._cfg.ohlcv_lookback_days,
                end_date=previous_trading_day_str(self._tm.get_current_kst_time()),
            ),
        )
        current, trading_value = self._extract_quote(current_response)
        rows = self._ascending_rows(ohlcv_response)
        if current <= 0:
            self._log_entry_rejected(item, "price_unavailable")
            return None
        if len(rows) < 200:
            self._log_entry_rejected(
                item,
                "insufficient_ohlcv",
                ohlcv_count=len(rows),
                min_ohlcv_count=200,
            )
            return None
        average_daily_trading_value = self._average_daily_trading_value(rows)
        if max(trading_value, average_daily_trading_value) < self._cfg.min_trading_value:
            self._log_entry_rejected(
                item,
                "low_trading_value",
                trading_value=trading_value,
                average_daily_trading_value=average_daily_trading_value,
                min_trading_value=self._cfg.min_trading_value,
            )
            return None

        closes = [self._price(row, "close", "stck_clpr") for row in rows]
        lows = [self._price(row, "low", "stck_lwpr") for row in rows]
        if any(value <= 0 for value in closes) or any(value <= 0 for value in lows):
            self._log_entry_rejected(item, "invalid_ohlcv")
            return None

        stages = self._stage_service.classify_stage_series(
            closes,
            lows,
            rs_rating=int(self._number(item.get("rs_rating"))),
        )
        if not self._has_recent_stage2_transition(stages):
            self._log_entry_rejected(
                item,
                "no_recent_stage2_transition",
                transition_lookback_days=self._cfg.transition_lookback_days,
            )
            return None

        recent = rows[-self._cfg.breakout_lookback_days:]
        pivot = max(self._price(row, "high", "stck_hgpr") for row in recent)
        if pivot <= 0:
            self._log_entry_rejected(item, "invalid_breakout_pivot")
            return None
        if current <= pivot:
            self._log_entry_rejected(
                item,
                "below_breakout_pivot",
                current=current,
                pivot=pivot,
                breakout_lookback_days=self._cfg.breakout_lookback_days,
            )
            return None
        extension_pct = (current / pivot - 1.0) * 100.0
        if extension_pct > self._cfg.max_breakout_extension_pct:
            self._log_entry_rejected(
                item,
                "breakout_over_extended",
                current=current,
                pivot=pivot,
                extension_pct=round(extension_pct, 2),
                max_extension_pct=self._cfg.max_breakout_extension_pct,
            )
            return None

        stop_price = current * (1.0 + self._cfg.hard_stop_pct / 100.0)
        target_price = current * (1.0 + self._cfg.take_profit_pct / 100.0)
        rs_rating = int(self._number(item.get("rs_rating")))
        reason = (
            f"최근 Stage 2 전환 + {self._cfg.breakout_lookback_days}일 고점 돌파 "
            f"(pivot={pivot:,.0f}, 확장={extension_pct:.2f}%, RS={rs_rating})"
        )
        return TradeSignal(
            code=code,
            name=str(item.get("name") or code),
            action="BUY",
            price=current,
            reason=reason,
            strategy_name=self.name,
            strategy_id=self.strategy_id,
            entry_reason="recent_stage2_transition_20d_breakout",
            invalidation_price=round(stop_price, 2),
            stop_loss_price=round(stop_price, 2),
            target_price=round(target_price, 2),
            stop_loss_pct=self._cfg.hard_stop_pct,
            trailing_rule=f"target_{self._cfg.take_profit_pct:g}pct_or_{self._cfg.max_holding_days}d_time_stop",
            expected_holding_period_days=self._cfg.max_holding_days,
            confidence=min(1.0, max(0.0, rs_rating / 100.0)),
            required_data=[
                "minervini_stage_series",
                "rs_rating",
                "daily_ohlcv",
                "current_price",
                "market_cap",
                "trading_value",
            ],
        )

    def current_candidate_codes(self) -> List[str]:
        return sorted(self._current_candidate_codes_set)

    def _log_entry_rejected(self, item: dict, reason: str, **metrics: Any) -> None:
        self._logger.info({
            "event": "entry_rejected",
            "code": str(item.get("code") or ""),
            "name": str(item.get("name") or item.get("code") or ""),
            "reason": reason,
            **metrics,
        })

    def _average_daily_trading_value(self, rows: list[dict]) -> float:
        values = []
        for row in rows[-self._cfg.liquidity_lookback_days:]:
            close = self._price(row, "close", "stck_clpr")
            volume = self._number(row.get("volume") or row.get("acml_vol"))
            if close > 0 and volume > 0:
                values.append(close * volume)
        return sum(values) / len(values) if values else 0.0

    async def check_exits(self, holdings: List[dict]) -> List[TradeSignal]:
        results = await asyncio.gather(
            *(self._check_exit(holding) for holding in holdings),
            return_exceptions=True,
        )
        signals: List[TradeSignal] = []
        for result in results:
            if isinstance(result, Exception):
                self._logger.error("Minervini Stage2 exit scan error: %s", result)
            elif result is not None:
                signals.append(result)
        return signals

    async def _check_exit(self, holding: dict) -> Optional[TradeSignal]:
        code = str(holding.get("code") or "")
        if not code:
            return None
        current_response = await self._sqs.get_current_price(code, caller=self.name)
        current, _ = self._extract_quote(current_response)
        buy_price = int(self._number(holding.get("buy_price")))
        if current <= 0 or buy_price <= 0:
            return None

        hard_stop_price = buy_price * (1.0 + self._cfg.hard_stop_pct / 100.0)
        reason: Optional[str] = None
        if current <= hard_stop_price:
            reason = (
                f"하드 스톱: 현재가 {current:,} ≤ 손절가 {hard_stop_price:,.0f} "
                f"({self._cfg.hard_stop_pct:.1f}%)"
            )
        elif current >= buy_price * (1.0 + self._cfg.take_profit_pct / 100.0):
            reason = (
                f"목표 수익: 현재가 {current:,} ≥ "
                f"목표가 {buy_price * (1.0 + self._cfg.take_profit_pct / 100.0):,.0f} "
                f"(+{self._cfg.take_profit_pct:.1f}%)"
            )
        else:
            if self._stage_service is not None:
                stage_result = await self._stage_service.get_stage_for_code(code)
                stage = stage_result[0] if isinstance(stage_result, tuple) else stage_result
                if stage != 2:
                    reason = f"Stage 2 이탈: 현재 Stage {stage}"

        if reason is None:
            ohlcv_response = await self._sqs.get_recent_daily_ohlcv(
                code,
                limit=self._cfg.max_holding_days + 5,
            )
            rows = self._ascending_rows(ohlcv_response)
            buy_date = normalize_yyyymmdd(holding.get("buy_date"))
            if buy_date:
                completed_days = sum(
                    normalize_yyyymmdd(row.get("date") or row.get("stck_bsop_date")) > buy_date
                    for row in rows
                )
                latest_date = max(
                    (normalize_yyyymmdd(row.get("date") or row.get("stck_bsop_date")) for row in rows),
                    default="",
                )
                today = self._tm.get_current_kst_time().strftime("%Y%m%d")
                holding_days = completed_days + int(latest_date < today)
                if holding_days >= self._cfg.max_holding_days:
                    reason = f"시간 청산: Stage 2 돌파 후 {holding_days}거래일 경과"

        if reason is None:
            return None
        return TradeSignal(
            code=code,
            name=str(holding.get("name") or code),
            action="SELL",
            price=current,
            qty=int(self._number(holding.get("qty")) or 1),
            reason=reason,
            strategy_name=self.name,
            strategy_id=self.strategy_id,
            required_data=["current_price", "minervini_stage", "holding_period"],
        )

    def _has_recent_stage2_transition(self, stages: list[int]) -> bool:
        if len(stages) < 2 or stages[-1] != 2:
            return False
        start = max(1, len(stages) - self._cfg.transition_lookback_days)
        return any(stages[index] == 2 and stages[index - 1] != 2 for index in range(start, len(stages)))

    @staticmethod
    def _extract_quote(response: Any) -> tuple[int, int]:
        if not response or getattr(response, "rt_cd", None) != "0":
            return 0, 0
        data = getattr(response, "data", None)
        output = data.get("output") if isinstance(data, dict) else None
        if isinstance(output, dict):
            return (
                int(MinerviniStage2TransitionStrategy._number(output.get("stck_prpr"))),
                int(MinerviniStage2TransitionStrategy._number(output.get("acml_tr_pbmn"))),
            )
        return (
            int(MinerviniStage2TransitionStrategy._number(getattr(output, "stck_prpr", 0))),
            int(MinerviniStage2TransitionStrategy._number(getattr(output, "acml_tr_pbmn", 0))),
        )

    @staticmethod
    def _ascending_rows(response: Any) -> list[dict]:
        if not response or getattr(response, "rt_cd", None) != "0":
            return []
        rows = getattr(response, "data", None)
        if not isinstance(rows, list):
            return []
        usable = [row for row in rows if isinstance(row, dict)]
        for key in ("date", "stck_bsop_date"):
            if usable and all(row.get(key) for row in usable):
                return sorted(usable, key=lambda row: str(row[key]))
        return usable

    @staticmethod
    def _price(row: dict, primary: str, fallback: str) -> float:
        return MinerviniStage2TransitionStrategy._number(row.get(primary) or row.get(fallback))

    @staticmethod
    def _number(value: Any) -> float:
        try:
            return float(value or 0)
        except (TypeError, ValueError):
            return 0.0
