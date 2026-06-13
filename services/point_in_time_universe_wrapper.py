"""PointInTimeAugmentedUniverse — 백테스트 universe 에 상폐 종목 후보 합류 (R-1 생존편향 3b).

라이브 universe 서비스(`OneilUniverseService` 등)는 건드리지 않는 **백테스트 전용**
wrapper. base 의 `get_watchlist()` 결과에, `PointInTimeUniverseProvider` 가 알려주는
"그 날 상장돼 있던 상폐 종목"을 추가한다.

상폐 종목은 과거 시가총액 데이터가 없으므로 base 의 시총 필터를 적용할 수 없다.
대신 백필 OHLCV(3a fallback 으로 sqs 에서 조회) 기반 5일 평균 거래대금 게이트만
적용한다(`min_avg_trading_value_5d`). 현재 상장 종목과 비대칭(시총 면제)이라는 점은
의도된 trade-off 다 — 사용자 결정(거래대금 게이트만).

watchlist item 타입은 universe 마다 다르므로 `item_factory(code, name, market,
avg_trading_value_5d)` 로 주입받아 결합한다. base 의 나머지 메서드
(`is_market_timing_ok` 등)는 `__getattr__` 으로 위임한다.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from common.types import ErrorCode, ResCommonResponse


def _safe_int(value: Any) -> int:
    try:
        if value in (None, "", "-"):
            return 0
        return int(float(str(value).replace(",", "")))
    except (TypeError, ValueError):
        return 0


class PointInTimeAugmentedUniverse:
    def __init__(
        self,
        base: Any,
        *,
        pit_provider: Any,
        sqs: Any,
        clock: Any,
        item_factory: Callable[..., Any],
        min_avg_trading_value_5d: int = 0,
        trading_value_lookback: int = 5,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._base = base
        self._pit = pit_provider
        self._sqs = sqs
        self._clock = clock
        self._item_factory = item_factory
        self._min_tv = int(min_avg_trading_value_5d)
        self._lookback = int(trading_value_lookback)
        self._logger = logger or logging.getLogger(__name__)
        self._excluded: set[str] = set()

    async def get_watchlist(self, logger: Optional[logging.Logger] = None) -> dict:
        base_wl = await self._base.get_watchlist(logger) if logger is not None else await self._base.get_watchlist()
        base_wl = dict(base_wl or {})

        as_of = self._clock.get_current_kst_time().strftime("%Y%m%d")
        merged = dict(base_wl)
        for code in sorted(self._pit.delisted_codes_as_of(as_of)):
            if code in base_wl or code in self._excluded:
                continue
            avg_tv = await self._avg_trading_value(code, as_of)
            if avg_tv is None or avg_tv < self._min_tv:
                continue
            record = self._pit.record_for(code)
            merged[code] = self._item_factory(
                code=code,
                name=(record.name if record else code),
                market=(record.market if record else ""),
                avg_trading_value_5d=avg_tv,
            )
        return merged

    async def _avg_trading_value(self, code: str, as_of: str) -> Optional[float]:
        resp = await self._sqs.get_recent_daily_ohlcv(code, limit=self._lookback, end_date=as_of)
        if isinstance(resp, ResCommonResponse):
            if resp.rt_cd != ErrorCode.SUCCESS.value:
                return None
            rows = resp.data or []
        else:
            rows = resp or []
        values = [
            _safe_int(r.get("close")) * _safe_int(r.get("volume"))
            for r in rows
            if isinstance(r, dict)
        ]
        valid = [v for v in values if v > 0]
        if not valid:
            return None
        return sum(valid) / len(valid)

    def exclude_code_for_today(self, code: str, *args, **kwargs) -> None:
        self._excluded.add(code)
        base_exclude = getattr(self._base, "exclude_code_for_today", None)
        if callable(base_exclude):
            base_exclude(code, *args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        # _base 등 자체 속성은 __getattribute__ 가 먼저 처리하므로 여기 도달하지 않음.
        return getattr(self._base, name)
