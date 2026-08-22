"""해외 Buyable Gap-Up dry-run 신호 서비스.

국내 `OneilPocketPivotStrategy` 의 BGU 판정 중 해외 일봉으로 재현 가능한
가격/거래량 기반 부분만 적용한다. 프로그램 순매수 필터와 장중 10분 휩소 필터는
국내 전용·장중 전용 데이터라 이 서비스에서는 쓰지 않는다. 주문 경로는 없고
shadow 저널에 would-be 신호만 기록한다.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from common.overseas_types import OverseasExchange
from common.types import ErrorCode


@dataclass
class OverseasBuyableGapUpConfig:
    bgu_gap_pct: float = 4.0
    bgu_volume_multiplier: float = 3.0
    bgu_avg_volume_period: int = 50
    bgu_min_avg_volume_count: int = 20
    partial_profit_trigger_pct: float = 15.0


class OverseasBuyableGapUpDryRunService:
    STRATEGY_NAME = "O'NeilBGU_overseas"
    SIGNAL_SOURCE = "overseas_bgu_dryrun"

    def __init__(
        self,
        candidate_service,
        stock_query_service,
        shadow_journal=None,
        logger: Optional[logging.Logger] = None,
        *,
        config: Optional[OverseasBuyableGapUpConfig] = None,
        exchange: OverseasExchange = OverseasExchange.NASD,
        position_sizing_service=None,
        fx_provider=None,
    ) -> None:
        self._candidate_service = candidate_service
        self._sqs = stock_query_service
        self._journal = shadow_journal
        self._logger = logger or logging.getLogger(__name__)
        self._cfg = config or OverseasBuyableGapUpConfig()
        self._default_exchange = exchange
        self._sizing_service = position_sizing_service
        self._fx_provider = fx_provider

    async def scan_dry_run(
        self,
        exchange: Optional[OverseasExchange] = None,
        *,
        top_n: Optional[int] = None,
        min_avg_trading_value: Optional[float] = None,
        record: bool = True,
    ) -> List[Dict[str, Any]]:
        """후보를 평가해 BGU BUY would-be 신호를 반환하고(선택) shadow 저널에 기록한다."""
        ex = exchange or self._default_exchange
        candidates = await self._candidate_service.get_candidates(
            ex, top_n=top_n, min_avg_trading_value=min_avg_trading_value
        )

        fx_rate = await self._resolve_fx_rate()
        signals: List[Dict[str, Any]] = []
        for cand in candidates or []:
            code = cand.get("code")
            if not code:
                continue
            try:
                resp = await self._sqs.get_recent_daily_ohlcv(code, limit=60, exchange=ex)
            except Exception as e:
                self._logger.warning({"event": "overseas_bgu_ohlcv_error", "code": code, "error": str(e)})
                continue
            if not resp or resp.rt_cd != ErrorCode.SUCCESS.value or not resp.data:
                continue

            sig = self._evaluate(code, cand, resp.data)
            if not sig:
                continue
            if self._sizing_service is not None:
                sizing = self._sizing_service.size(
                    limit_price_usd=sig["entry_price"], fx_krw_per_usd=fx_rate
                )
                sig["qty"] = sizing.get("qty")
                sig["notional_usd"] = sizing.get("notional_usd")
                if sizing.get("fx_krw_per_usd") is not None:
                    sig["fx_krw_per_usd"] = sizing.get("fx_krw_per_usd")
                if sizing.get("krw_exposure") is not None:
                    sig["krw_exposure"] = sizing.get("krw_exposure")
            signals.append(sig)
            if record and self._journal is not None:
                self._journal.record(
                    strategy_name=self.STRATEGY_NAME,
                    code=code,
                    signal=sig,
                    snapshot={
                        "exchange": ex.value,
                        "avg_trading_value": cand.get("avg_trading_value"),
                        "bar": self._bar_ohlc(resp.data[-1]),
                    },
                    signal_source=self.SIGNAL_SOURCE,
                )

        self._logger.info({
            "event": "overseas_bgu_dryrun_scan",
            "exchange": ex.value,
            "candidates": len(candidates or []),
            "signals": len(signals),
        })
        return signals

    async def _resolve_fx_rate(self) -> Optional[float]:
        if self._sizing_service is None or self._fx_provider is None:
            return None
        try:
            rate = await self._fx_provider()
        except Exception as e:
            self._logger.warning({"event": "overseas_bgu_fx_error", "error": str(e)})
            return None
        try:
            rate = float(rate) if rate is not None else None
        except (TypeError, ValueError):
            return None
        return rate if (rate is not None and rate > 0) else None

    def _evaluate(
        self,
        code: str,
        candidate: Dict[str, Any],
        rows: List[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        min_rows = self._cfg.bgu_min_avg_volume_count + 1
        if not rows or len(rows) < min_rows:
            return None

        cur = rows[-1]
        prev = rows[-2]
        open_ = self._f(cur.get("open"))
        high = self._f(cur.get("high"))
        low = self._f(cur.get("low"))
        close = self._f(cur.get("close"))
        volume = self._f(cur.get("volume"))
        prev_close = self._f(prev.get("close"))
        if min(open_, high, low, close, volume, prev_close) <= 0:
            return None

        gap_ratio = (open_ - prev_close) / prev_close * 100
        if gap_ratio < self._cfg.bgu_gap_pct:
            return None

        if close < open_:
            return None

        history = rows[:-1]
        period = min(self._cfg.bgu_avg_volume_period, len(history))
        volumes = [self._f(r.get("volume")) for r in history[-period:] if self._f(r.get("volume")) > 0]
        if len(volumes) < self._cfg.bgu_min_avg_volume_count:
            return None
        avg_vol = sum(volumes) / len(volumes)
        threshold_vol = avg_vol * self._cfg.bgu_volume_multiplier
        if volume < threshold_vol:
            return None

        volume_ratio = volume / avg_vol if avg_vol > 0 else 0.0
        target_price = close * (1 + self._cfg.partial_profit_trigger_pct / 100)
        return {
            "strategy": self.STRATEGY_NAME,
            "code": code,
            "name": candidate.get("name", code),
            "action": "BUY",
            "date": cur.get("date"),
            "entry_price": close,
            "entry_reason": "overseas_buyable_gap_up",
            "gap_ratio": gap_ratio,
            "open_price": open_,
            "prev_close": prev_close,
            "volume": volume,
            "avg_volume": avg_vol,
            "volume_ratio": volume_ratio,
            "stop_price": low,
            "target_price": target_price,
            "reason": (
                f"BGU진입(갭 {gap_ratio:.1f}%, "
                f"거래량 {volume_ratio:.2f}x 50일평균대비)"
            ),
        }

    @staticmethod
    def _bar_ohlc(bar: Dict[str, Any]) -> Dict[str, Any]:
        return {k: bar.get(k) for k in ("date", "open", "high", "low", "close", "volume")}

    @staticmethod
    def _f(x) -> float:
        try:
            return float(x or 0)
        except (TypeError, ValueError):
            return 0.0
