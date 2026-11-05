"""미국장 레짐 기반 인버스 ETF dry-run 신호 서비스.

QQQ 프록시가 확정된 bear 국면일 때만 -1배 인버스 ETF(기본 PSQ)를 관찰한다.
인버스 ETF 자체의 완성 일봉도 상승 추세여야 BUY would-be 신호를 shadow 저널에
기록한다. 주문 실행 의존성은 두지 않아 실주문이 구조적으로 발생하지 않는다.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from common.overseas_types import OverseasExchange
from common.types import ErrorCode


@dataclass
class OverseasInverseEtfConfig:
    symbol: str = "PSQ"
    name: str = "ProShares Short QQQ"
    exchange: OverseasExchange = OverseasExchange.NASD
    trend_ma_period: int = 20
    hard_stop_pct: float = -5.0
    trailing_stop_pct: float = -8.0


class OverseasInverseEtfDryRunService:
    STRATEGY_NAME = "InverseETFRegime_overseas"
    SIGNAL_SOURCE = "overseas_inverse_etf_dryrun"

    def __init__(
        self,
        stock_query_service,
        market_regime_service,
        shadow_journal=None,
        logger: Optional[logging.Logger] = None,
        *,
        config: Optional[OverseasInverseEtfConfig] = None,
        position_sizing_service=None,
        fx_provider=None,
    ) -> None:
        self._sqs = stock_query_service
        self._regime = market_regime_service
        self._journal = shadow_journal
        self._logger = logger or logging.getLogger(__name__)
        self._cfg = config or OverseasInverseEtfConfig()
        self._sizing_service = position_sizing_service
        self._fx_provider = fx_provider

    async def scan_dry_run(
        self,
        exchange: Optional[OverseasExchange] = None,
        *,
        record: bool = True,
        **_ignored,
    ) -> List[Dict[str, Any]]:
        """bear 국면과 PSQ 추세를 확인해 다음 세션 BUY 후보를 기록한다."""
        market = getattr(self._regime, "MARKET", "US")
        try:
            regime = await self._regime.classify(market)
        except Exception as exc:
            self._logger.warning({
                "event": "overseas_inverse_etf_regime_error",
                "error": str(exc),
            })
            return []
        if getattr(regime, "regime_label", None) != "bear":
            return []

        # PSQ의 상장 거래소는 설정값으로 고정한다. suite의 exchange 인자는 다른
        # 전략 후보 유니버스용이며 단일 ETF의 거래소를 바꾸지 않는다.
        ex = self._cfg.exchange
        limit = self._cfg.trend_ma_period + 1
        try:
            response = await self._sqs.get_recent_daily_ohlcv(
                self._cfg.symbol,
                limit=limit,
                exchange=ex,
            )
        except Exception as exc:
            self._logger.warning({
                "event": "overseas_inverse_etf_ohlcv_error",
                "code": self._cfg.symbol,
                "error": str(exc),
            })
            return []
        if (
            not response
            or response.rt_cd != ErrorCode.SUCCESS.value
            or not response.data
        ):
            return []

        signal = self._evaluate(response.data, regime_label="bear")
        if signal is None:
            return []

        fx_rate = await self._resolve_fx_rate()
        if self._sizing_service is not None:
            sizing = self._sizing_service.size(
                limit_price_usd=signal["entry_price"],
                fx_krw_per_usd=fx_rate,
            )
            for key in ("qty", "notional_usd", "fx_krw_per_usd", "krw_exposure"):
                if sizing.get(key) is not None:
                    signal[key] = sizing[key]

        if record and self._journal is not None:
            self._journal.record(
                strategy_name=self.STRATEGY_NAME,
                code=self._cfg.symbol,
                signal=signal,
                snapshot={
                    "exchange": ex.value,
                    "market_regime": "bear",
                    "bar": self._bar_ohlc(response.data[-1]),
                },
                signal_source=self.SIGNAL_SOURCE,
            )
        self._logger.info({
            "event": "overseas_inverse_etf_dryrun_scan",
            "exchange": ex.value,
            "code": self._cfg.symbol,
            "signals": 1,
        })
        return [signal]

    def _evaluate(
        self,
        rows: List[Dict[str, Any]],
        *,
        regime_label: str,
    ) -> Optional[Dict[str, Any]]:
        period = self._cfg.trend_ma_period
        if len(rows) < period + 1:
            return None
        previous_closes = [self._f(row.get("close")) for row in rows[-period - 1:-1]]
        if len(previous_closes) != period or any(value <= 0 for value in previous_closes):
            return None
        current = rows[-1]
        open_price = self._f(current.get("open"))
        close = self._f(current.get("close"))
        trend_ma = sum(previous_closes) / period
        if open_price <= 0 or close <= open_price or close <= trend_ma:
            return None

        stop_price = close * (1 + self._cfg.hard_stop_pct / 100.0)
        return {
            "strategy": self.STRATEGY_NAME,
            "code": self._cfg.symbol,
            "name": self._cfg.name,
            "action": "BUY",
            "date": current.get("date"),
            "entry_price": close,
            "entry_reason": "us_bear_regime_inverse_etf",
            "market_regime": regime_label,
            "trend_ma": trend_ma,
            "trend_ma_period": period,
            "stop_price": stop_price,
            "trailing_stop_pct": self._cfg.trailing_stop_pct,
            "reason": (
                f"미국장 bear 국면 + {self._cfg.symbol} 종가({close:.2f}) > "
                f"{period}일 MA({trend_ma:.2f}), 종가 > 시가"
            ),
        }

    async def _resolve_fx_rate(self) -> Optional[float]:
        if self._sizing_service is None or self._fx_provider is None:
            return None
        try:
            rate = await self._fx_provider()
            rate = float(rate) if rate is not None else None
        except Exception as exc:
            self._logger.warning({
                "event": "overseas_inverse_etf_fx_error",
                "error": str(exc),
            })
            return None
        return rate if rate is not None and rate > 0 else None

    @staticmethod
    def _bar_ohlc(bar: Dict[str, Any]) -> Dict[str, Any]:
        return {key: bar.get(key) for key in ("date", "open", "high", "low", "close", "volume")}

    @staticmethod
    def _f(value) -> float:
        try:
            return float(value or 0)
        except (TypeError, ValueError):
            return 0.0
