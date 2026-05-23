"""Generic liquidity-only universe service.

OneilUniverseService 와 동일한 contract (`get_watchlist`, `is_market_timing_ok`)
를 만족하는 최소 구현. Pool A premium 분석 / RS Rating / Minervini stage /
52w high 근접 등 O'Neil 고유 게이트 미적용.

용도: P2-2 universe 적합성 비교 ablation 의 비교군. 활성 전략을 swap-in 하여
실행했을 때 Oneil universe 대비 성과 차이를 측정한다.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from common.types import ErrorCode
from strategies.oneil_common_types import OSBWatchlistItem


def _safe_int(value: Any) -> int:
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


class GenericLiquidityUniverseService:
    """5일 평균 거래대금 + 시가총액 임계만 적용하는 universe."""

    def __init__(
        self,
        *,
        sqs: Any,
        time_manager: Any,
        market_regime_service: Optional[Any] = None,
        min_avg_trading_value_5d: int = 5_000_000_000,
        min_market_cap: int = 100_000_000_000,
        max_watchlist: int = 90,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._sqs = sqs
        self._tm = time_manager
        self._regime_svc = market_regime_service
        self._min_tv_5d = int(min_avg_trading_value_5d)
        self._min_cap = int(min_market_cap)
        self._max_watchlist = int(max_watchlist)
        self._logger = logger or logging.getLogger(__name__)

        self._watchlist: Dict[str, OSBWatchlistItem] = {}
        self._watchlist_date: str = ""
        self._excluded_codes: set[str] = set()

    async def get_watchlist(
        self, logger: Optional[logging.Logger] = None
    ) -> Dict[str, OSBWatchlistItem]:
        logger = logger or self._logger
        today = self._tm.get_current_kst_time().strftime("%Y%m%d")

        if self._watchlist_date == today and self._watchlist:
            return self._watchlist

        if self._watchlist_date != today:
            self._excluded_codes = set()

        resp = await self._sqs.get_top_trading_value_stocks()
        if not resp or resp.rt_cd != ErrorCode.SUCCESS.value:
            self._watchlist = {}
            self._watchlist_date = today
            return {}

        items: list[OSBWatchlistItem] = []
        for row in (resp.data or []):
            code = row.get("mksc_shrn_iscd") or row.get("stck_shrn_iscd") or ""
            if not code or code in self._excluded_codes:
                continue
            name = row.get("hts_kor_isnm", code)

            cap_billion = _safe_int(row.get("stck_avls") or row.get("hts_avls"))
            market_cap = cap_billion * 100_000_000  # 억 단위 → 원 단위
            if market_cap < self._min_cap:
                continue

            ohlcv_resp = await self._sqs.get_recent_daily_ohlcv(code, limit=5)
            if not ohlcv_resp or ohlcv_resp.rt_cd != ErrorCode.SUCCESS.value:
                continue
            rows = ohlcv_resp.data or []
            if not rows:
                continue

            trading_values = [
                _safe_int(r.get("close")) * _safe_int(r.get("volume"))
                for r in rows
            ]
            valid_values = [v for v in trading_values if v > 0]
            if not valid_values:
                continue
            avg_tv_5d = sum(valid_values) / len(valid_values)
            if avg_tv_5d < self._min_tv_5d:
                continue

            market = row.get("market") or ""
            items.append(
                OSBWatchlistItem(
                    code=code,
                    name=name,
                    market=market,
                    high_20d=0,
                    ma_20d=0.0,
                    ma_50d=0.0,
                    avg_vol_20d=0.0,
                    bb_width_min_20d=0.0,
                    prev_bb_width=0.0,
                    w52_hgpr=0,
                    avg_trading_value_5d=avg_tv_5d,
                    market_cap=market_cap,
                    source="generic_liquidity",
                )
            )

        items.sort(key=lambda it: it.avg_trading_value_5d, reverse=True)
        self._watchlist = {
            item.code: item for item in items[: self._max_watchlist]
        }
        self._watchlist_date = today
        return self._watchlist

    async def is_market_timing_ok(
        self,
        market: str,
        caller: str = "",
        logger: Optional[logging.Logger] = None,
    ) -> bool:
        if self._regime_svc is None:
            return True
        snap = await self._regime_svc.classify(market, logger=logger or self._logger)
        return bool(getattr(snap, "is_rising", False))

    def exclude_code_for_today(
        self,
        code: str,
        reason: str = "",
        logger: Optional[logging.Logger] = None,
    ) -> None:
        if not code:
            return
        self._excluded_codes.add(code)
        self._watchlist.pop(code, None)
