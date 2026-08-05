"""시장 국면 판정 서비스.

KOSPI / KOSDAQ 실제 지수의 단기 MA 추세를 4-state로 분류하고
전략 게이트 및 성과 리포트가 공통으로 참조할 bull / bear / sideways 라벨로 매핑한다.

분류 로직은 OneilUniverseService._check_etf_ma_rising() 에서 이관되었다.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from common.date_utils import previous_trading_day_str
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
    kospi_index_code: str = "0001"   # KOSPI
    kosdaq_index_code: str = "1001"  # KOSDAQ
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
    data_date: str = ""       # 판정에 사용한 마지막 확정 일봉 날짜 (YYYYMMDD)
    current_close: Optional[float] = None  # 판정 기준일 지수 종가
    recovery_earliest_days: Optional[int] = None  # 기존 급락 MA가 판정창에서 빠지는 최소 거래일
    recovery_target_ma: Optional[float] = None    # 해당 시점 3일 순변화 조건의 MA(20) 하한
    recovery_target_close: Optional[float] = None  # 최초 전환일 종가 목표(그전 종가 현재 수준 유지 가정)
    next_close_floor: Optional[float] = None      # 다음 MA(20)의 hard decline 방지 종가 하한


class MarketRegimeService:
    """시장(KOSPI/KOSDAQ) 지수 MA 추세를 분류한다."""

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

    def _index_code_for(self, market: str) -> str:
        if market == "KOSPI":
            return self._cfg.kospi_index_code
        if market == "KOSDAQ":
            return self._cfg.kosdaq_index_code
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
        index_code = self._index_code_for(market)
        period = self._cfg.ma_period
        days = self._cfg.rising_days

        query_end_date = as_of_date
        if as_of_date is None:
            now = self._tm.get_current_kst_time()
            query_end_date = (
                previous_trading_day_str(now)
                if self._tm.is_market_operating_hours()
                else now.strftime("%Y%m%d")
            )

        ohlcv_resp = await self._sqs.get_recent_daily_index_ohlcv(
            index_code,
            limit=period + days + 5,
            end_date=query_end_date,
        )
        ohlcv = ohlcv_resp.data if ohlcv_resp and ohlcv_resp.rt_cd == ErrorCode.SUCCESS.value else []
        if as_of_date:
            ohlcv = [r for r in ohlcv if r.get("date", "") <= as_of_date]
        data_date = ohlcv[-1].get("date", "") if ohlcv else ""

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
                data_date=data_date,
            )
            logger.debug({
                "event": "market_regime_check",
                "market": market,
                "index_code": index_code,
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

        recovery_earliest_days: Optional[int] = None
        recovery_target_ma: Optional[float] = None
        recovery_target_close: Optional[float] = None
        next_close_floor: Optional[float] = None
        if not is_rising:
            # 다음 MA는 가장 오래된 20일 창 종가가 빠지고 다음 확정 종가가 들어온다.
            # 하한은 새 hard decline을 만들지 않기 위한 값이며, 기존 급락은 판정창에서
            # 자연스럽게 빠질 때까지 별도 거래일이 필요하다.
            current_ma = ma_values[-1]
            outgoing_close = closes[-period]
            allowed_next_ma = current_ma * (1 + self._cfg.hard_decline_pct / 100)
            next_close_floor = outgoing_close + period * (allowed_next_ma - current_ma)

            hard_decline_indexes = [
                index + 1
                for index, change in enumerate(daily_changes_pct)
                if change < self._cfg.hard_decline_pct
            ]
            recovery_earliest_days = max(hard_decline_indexes, default=1)
            # recovery_earliest_days 뒤의 4개 MA 창은 기존 MA 중 해당 index부터
            # 시작한다. 마지막 MA가 이 값 이상이면 3일 순변화 조건을 충족한다.
            recovery_reference_ma = ma_values[recovery_earliest_days]
            recovery_target_ma = recovery_reference_ma * (1 + self._cfg.min_net_change_pct / 100)
            # 최초 전환일까지 이전 종가가 현재 종가와 같다고 가정하고, 마지막 종가 하나로
            # 목표 MA를 충족시키는 데 필요한 종가를 계산한다.
            replacement_count = recovery_earliest_days
            outgoing_closes = closes[-period:-period + replacement_count]
            assumed_intermediate_closes = closes[-1] * (replacement_count - 1)
            recovery_target_close = (
                period * recovery_target_ma
                - (sum(closes[-period:]) - sum(outgoing_closes))
                - assumed_intermediate_closes
            )

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
            data_date=data_date,
            current_close=round(closes[-1], 2),
            recovery_earliest_days=recovery_earliest_days,
            recovery_target_ma=round(recovery_target_ma, 2) if recovery_target_ma is not None else None,
            recovery_target_close=round(recovery_target_close, 2) if recovery_target_close is not None else None,
            next_close_floor=round(next_close_floor, 2) if next_close_floor is not None else None,
        )
        logger.debug({
            "event": "market_regime_check",
            "market": market,
            "index_code": index_code,
            "is_rising": is_rising,
            "trend_status": trend_status,
            "regime_label": snap.regime_label,
            "net_change_pct": snap.net_change_pct,
            "max_daily_drop_pct": snap.max_daily_drop_pct,
        })
        return snap
