"""시장 국면 판정 서비스.

KOSPI / KOSDAQ 지수 프록시 ETF의 단기 MA 추세를 4-state로 분류하고
전략 게이트 및 성과 리포트가 공통으로 참조할 bull / bear / sideways 라벨로 매핑한다.

분류 로직은 OneilUniverseService._check_etf_ma_rising() 에서 이관되었다.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from common.types import ErrorCode
from core.market_clock import MarketClock
from services.stock_query_service import StockQueryService


_TREND_STATUS_TO_LABEL = {
    "rising": "bull",
    "hard_decline": "bear",
    "weak_trend": "bear",
    "uptrend_under_pressure": "sideways",
}


@dataclass
class MarketRegimeConfig:
    kospi_etf_code: str = "069500"   # KODEX 200
    kosdaq_etf_code: str = "229200"  # KODEX 코스닥150
    ma_period: int = 20
    rising_days: int = 3
    min_net_change_pct: float = -0.10
    daily_dip_tolerance_pct: float = -0.20
    hard_decline_pct: float = -0.50


@dataclass
class RegimeSnapshot:
    market: str               # "KOSPI" | "KOSDAQ"
    trend_status: str         # rising / hard_decline / weak_trend / uptrend_under_pressure
    regime_label: str         # bull / bear / sideways
    snapshot_date: str        # YYYYMMDD
    is_rising: bool
    net_change_pct: float
    max_daily_drop_pct: float
    ma_values: List[float] = field(default_factory=list)
    fail_detail: str = ""


class MarketRegimeService:
    """시장(KOSPI/KOSDAQ) ETF MA 추세를 분류한다."""

    def __init__(
        self,
        stock_query_service: StockQueryService,
        market_clock: MarketClock,
        config: Optional[MarketRegimeConfig] = None,
        logger: Optional[logging.Logger] = None,
    ):
        self._sqs = stock_query_service
        self._tm = market_clock
        self._cfg = config or MarketRegimeConfig()
        self._logger = logger or logging.getLogger(__name__)
        self._cache: Dict[str, RegimeSnapshot] = {}
        self._cache_date: str = ""

    def _etf_code_for(self, market: str) -> str:
        if market == "KOSPI":
            return self._cfg.kospi_etf_code
        if market == "KOSDAQ":
            return self._cfg.kosdaq_etf_code
        raise ValueError(f"Unknown market: {market}")

    async def classify(self, market: str, logger: Optional[logging.Logger] = None) -> RegimeSnapshot:
        """오늘 기준 캐시된 분류를 반환. 캐시가 비어 있으면 SQS로 재계산."""
        today = self._tm.get_current_kst_time().strftime("%Y%m%d")
        if self._cache_date != today:
            self._cache = {}
            self._cache_date = today
        if market in self._cache:
            return self._cache[market]
        snap = await self._compute(market, snapshot_date=today, as_of_date=None, logger=logger or self._logger)
        self._cache[market] = snap
        return snap

    def get_cached_snapshot(self, market: str) -> Optional[RegimeSnapshot]:
        return self._cache.get(market)

    async def snapshot_both(self, logger: Optional[logging.Logger] = None) -> Dict[str, RegimeSnapshot]:
        return {
            "KOSPI": await self.classify("KOSPI", logger=logger),
            "KOSDAQ": await self.classify("KOSDAQ", logger=logger),
        }

    async def is_bull(self, market: str, logger: Optional[logging.Logger] = None) -> bool:
        """기존 OneilUniverseService.is_market_timing_ok() 와 동치."""
        return (await self.classify(market, logger=logger)).is_rising

    async def classify_on_date(
        self,
        market: str,
        date: str,
        logger: Optional[logging.Logger] = None,
    ) -> RegimeSnapshot:
        """백테스트용 히스토리컬 분류 (캐시 미사용)."""
        return await self._compute(market, snapshot_date=date, as_of_date=date, logger=logger or self._logger)

    async def _compute(
        self,
        market: str,
        *,
        snapshot_date: str,
        as_of_date: Optional[str],
        logger: logging.Logger,
    ) -> RegimeSnapshot:
        etf_code = self._etf_code_for(market)
        period = self._cfg.ma_period
        days = self._cfg.rising_days

        ohlcv_resp = await self._sqs.get_recent_daily_ohlcv(etf_code, limit=period + days + 5)
        ohlcv = ohlcv_resp.data if ohlcv_resp and ohlcv_resp.rt_cd == ErrorCode.SUCCESS.value else []
        if as_of_date:
            ohlcv = [r for r in ohlcv if r.get("date", "") <= as_of_date]

        if not ohlcv or len(ohlcv) < period + days:
            snap = RegimeSnapshot(
                market=market,
                trend_status="weak_trend",
                regime_label=_TREND_STATUS_TO_LABEL["weak_trend"],
                snapshot_date=snapshot_date,
                is_rising=False,
                net_change_pct=0.0,
                max_daily_drop_pct=0.0,
                ma_values=[],
                fail_detail="insufficient data",
            )
            logger.debug({
                "event": "market_regime_check",
                "market": market,
                "etf_code": etf_code,
                "is_rising": False,
                "trend_status": snap.trend_status,
                "fail_detail": snap.fail_detail,
            })
            return snap

        closes = [r.get("close", 0) for r in ohlcv]
        ma_values: List[float] = []
        for i in range(days + 1):
            end = len(closes) - days + i
            ma_values.append(sum(closes[end - period:end]) / period)

        daily_changes_pct: List[float] = []
        for j in range(1, len(ma_values)):
            prev = ma_values[j - 1]
            curr = ma_values[j]
            daily_changes_pct.append(((curr - prev) / prev * 100) if prev else 0.0)

        first_ma = ma_values[0]
        last_ma = ma_values[-1]
        net_change_pct = ((last_ma - first_ma) / first_ma * 100) if first_ma else 0.0
        max_daily_drop_pct = min(daily_changes_pct) if daily_changes_pct else 0.0

        is_rising = True
        fail_detail = ""
        trend_status = "rising"
        if max_daily_drop_pct < self._cfg.hard_decline_pct:
            is_rising = False
            worst_idx = daily_changes_pct.index(max_daily_drop_pct) + 1
            fail_detail = (
                f"MA hard decline: {max_daily_drop_pct:.2f}% < {self._cfg.hard_decline_pct:.2f}% "
                f"(idx {worst_idx}, {ma_values[worst_idx-1]:.2f} -> {ma_values[worst_idx]:.2f})"
            )
            trend_status = "hard_decline"
        elif net_change_pct < self._cfg.min_net_change_pct:
            is_rising = False
            fail_detail = (
                f"MA trend weak: net {net_change_pct:.2f}% < {self._cfg.min_net_change_pct:.2f}% "
                f"({first_ma:.2f} -> {last_ma:.2f})"
            )
            trend_status = "weak_trend"
        elif max_daily_drop_pct < self._cfg.daily_dip_tolerance_pct:
            trend_status = "uptrend_under_pressure"

        snap = RegimeSnapshot(
            market=market,
            trend_status=trend_status,
            regime_label=_TREND_STATUS_TO_LABEL[trend_status],
            snapshot_date=snapshot_date,
            is_rising=is_rising,
            net_change_pct=round(net_change_pct, 3),
            max_daily_drop_pct=round(max_daily_drop_pct, 3),
            ma_values=[round(v, 2) for v in ma_values],
            fail_detail=fail_detail,
        )
        logger.debug({
            "event": "market_regime_check",
            "market": market,
            "etf_code": etf_code,
            "is_rising": is_rising,
            "trend_status": trend_status,
            "regime_label": snap.regime_label,
            "net_change_pct": snap.net_change_pct,
            "max_daily_drop_pct": snap.max_daily_drop_pct,
        })
        return snap
