import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
import time
from typing import AsyncIterator, Awaitable, Callable, Mapping, Optional

from core.api_priority import PRIORITY_EMERGENCY, PRIORITY_NORMAL


DEFAULT_API_BUDGET_LIMITS = {
    "quotation_price": 4,
    "quotation_ohlcv": 2,
    "quotation_conclusion": 3,
    "quotation": 4,
    "account_balance": 1,
    "account_reconciliation": 1,
    "account": 1,
    "order_submit": 1,
    "order_cancel": 1,
    "websocket_connect": 1,
    "websocket_subscribe": 1,
    "default": 4,
}

DEFAULT_API_RATE_LIMITS_PER_SEC = {
    "quotation_price": 8.0,
    "quotation_ohlcv": 3.0,
    "quotation_conclusion": 5.0,
    "quotation": 8.0,
    "account_balance": 2.0,
    "account_reconciliation": 2.0,
    "account": 2.0,
    "order_submit": 2.0,
    "order_cancel": 2.0,
    "websocket_connect": 1.0,
    "websocket_subscribe": 5.0,
    "default": 8.0,
}

# 청산/킬스위치 등 긴급 경로 전용 lane.
# 일반 lane 과 semaphore/rate bucket 이 독립이므로, normal lane 이 점유된 상태에서도
# emergency 호출이 별도 슬롯으로 진입할 수 있다.
# emergency lane 이 정의되지 않은 카테고리는 normal lane 을 그대로 사용한다.
DEFAULT_API_EMERGENCY_LIMITS = {
    "order_submit": 1,
    "order_cancel": 1,
}

DEFAULT_API_EMERGENCY_RATE_LIMITS_PER_SEC = {
    "order_submit": 2.0,
    "order_cancel": 2.0,
}

# KIS 공개/운영 문서의 호출 한도는 계정/환경별로 차이가 있어 운영 전 재확인이 필요하다.
# 기본값은 개인 실전 10/s 가능성을 기준으로 normal 8/s + emergency 2/s 를 분리한 보수값.
DEFAULT_API_GLOBAL_RATE_LIMIT_PER_SEC = 8.0
DEFAULT_API_EMERGENCY_GLOBAL_RATE_LIMIT_PER_SEC = 2.0


@dataclass
class _LaneState:
    limit: int
    rate_limit_per_sec: float
    semaphore: asyncio.Semaphore
    rate_lock: asyncio.Lock
    next_available_at: float = 0.0
    active: int = 0
    acquired_total: int = 0
    max_observed_active: int = 0
    rate_wait_total: int = 0
    rate_wait_seconds_total: float = 0.0


@dataclass
class _CategoryBudget:
    normal: _LaneState
    emergency: Optional[_LaneState] = None


class ApiBudgetLimiter:
    """전략/서비스가 공유하는 broker API 동시성/rate budget limiter.

    카테고리별로 normal lane(기본) 과 선택적 emergency lane 을 갖는다.
    emergency lane 은 청산/킬스위치 경로가 일반 traffic 과 분리된 별도 슬롯을
    확보하도록 한다. lane 간 semaphore 와 rate bucket 은 독립.
    """

    def __init__(
        self,
        limits: Mapping[str, int] | None = None,
        *,
        rate_limits_per_sec: Mapping[str, float] | None = None,
        emergency_limits: Mapping[str, int] | None = None,
        emergency_rate_limits_per_sec: Mapping[str, float] | None = None,
        global_rate_limit_per_sec: float = DEFAULT_API_GLOBAL_RATE_LIMIT_PER_SEC,
        emergency_global_rate_limit_per_sec: float = DEFAULT_API_EMERGENCY_GLOBAL_RATE_LIMIT_PER_SEC,
        default_limit: int = 4,
        default_rate_limit_per_sec: float = 8.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._default_limit = self._validate_limit(default_limit)
        self._default_rate_limit_per_sec = self._validate_rate_limit(default_rate_limit_per_sec)
        self._sleep = sleep
        self._monotonic = monotonic
        self._global_lane = self._new_lane(1, global_rate_limit_per_sec)
        self._emergency_global_lane = self._new_lane(1, emergency_global_rate_limit_per_sec)
        configured = dict(DEFAULT_API_BUDGET_LIMITS if limits is None else limits)
        configured_rates = dict(
            DEFAULT_API_RATE_LIMITS_PER_SEC
            if rate_limits_per_sec is None
            else rate_limits_per_sec
        )
        self._emergency_limits = dict(
            DEFAULT_API_EMERGENCY_LIMITS if emergency_limits is None else emergency_limits
        )
        self._emergency_rate_limits = dict(
            DEFAULT_API_EMERGENCY_RATE_LIMITS_PER_SEC
            if emergency_rate_limits_per_sec is None
            else emergency_rate_limits_per_sec
        )
        self._budgets: dict[str, _CategoryBudget] = {
            category: self._new_budget(
                category,
                limit,
                configured_rates.get(category, self._default_rate_limit_per_sec),
            )
            for category, limit in configured.items()
        }

    @asynccontextmanager
    async def acquire(
        self,
        category: str | None,
        *,
        priority: str = PRIORITY_NORMAL,
    ) -> AsyncIterator[None]:
        actual_category = category if category is not None else "default"
        budget = self._budget_for(actual_category)
        lane = self._lane_for(budget, priority)
        await self._wait_for_rate_slot(self._global_lane_for(priority))
        await self._wait_for_rate_slot(lane)
        await lane.semaphore.acquire()
        lane.active += 1
        lane.acquired_total += 1
        lane.max_observed_active = max(lane.max_observed_active, lane.active)
        try:
            yield
        finally:
            lane.active -= 1
            lane.semaphore.release()

    def snapshot(self) -> dict[str, dict]:
        result: dict[str, dict] = {
            "_global": self._lane_snapshot(self._global_lane),
        }
        result["_global"]["emergency"] = self._lane_snapshot(self._emergency_global_lane)
        for category, budget in self._budgets.items():
            entry: dict = self._lane_snapshot(budget.normal)
            if budget.emergency is not None:
                entry["emergency"] = self._lane_snapshot(budget.emergency)
            result[category] = entry
        return result

    def _lane_snapshot(self, lane: _LaneState) -> dict:
        return {
            "limit": lane.limit,
            "rate_limit_per_sec": lane.rate_limit_per_sec,
            "active": lane.active,
            "acquired_total": lane.acquired_total,
            "max_observed_active": lane.max_observed_active,
            "rate_wait_total": lane.rate_wait_total,
            "rate_wait_seconds_total": lane.rate_wait_seconds_total,
        }

    def _budget_for(self, category: str) -> _CategoryBudget:
        budget = self._budgets.get(category)
        if budget is None:
            budget = self._new_budget(
                category,
                self._default_limit,
                self._default_rate_limit_per_sec,
            )
            self._budgets[category] = budget
        return budget

    def _lane_for(self, budget: _CategoryBudget, priority: str) -> _LaneState:
        if priority == PRIORITY_EMERGENCY and budget.emergency is not None:
            return budget.emergency
        return budget.normal

    def _global_lane_for(self, priority: str) -> _LaneState:
        if priority == PRIORITY_EMERGENCY:
            return self._emergency_global_lane
        return self._global_lane

    def _new_budget(
        self,
        category: str,
        limit: int,
        rate_limit_per_sec: float,
    ) -> _CategoryBudget:
        normal = self._new_lane(limit, rate_limit_per_sec)
        emergency_limit = self._emergency_limits.get(category)
        emergency_rate = self._emergency_rate_limits.get(category)
        emergency: Optional[_LaneState] = None
        if emergency_limit is not None:
            rate = emergency_rate if emergency_rate is not None else rate_limit_per_sec
            emergency = self._new_lane(emergency_limit, rate)
        return _CategoryBudget(normal=normal, emergency=emergency)

    def _new_lane(self, limit: int, rate_limit_per_sec: float) -> _LaneState:
        validated = self._validate_limit(limit)
        validated_rate = self._validate_rate_limit(rate_limit_per_sec)
        return _LaneState(
            limit=validated,
            rate_limit_per_sec=validated_rate,
            semaphore=asyncio.Semaphore(validated),
            rate_lock=asyncio.Lock(),
        )

    @staticmethod
    def _validate_limit(limit: int) -> int:
        value = int(limit)
        if value < 1:
            raise ValueError("API budget limit must be >= 1")
        return value

    @staticmethod
    def _validate_rate_limit(rate_limit_per_sec: float) -> float:
        value = float(rate_limit_per_sec)
        if value <= 0:
            raise ValueError("API rate limit must be > 0")
        return value

    async def _wait_for_rate_slot(self, lane: _LaneState) -> None:
        interval_sec = 1.0 / lane.rate_limit_per_sec
        async with lane.rate_lock:
            now = self._monotonic()
            wait_sec = max(0.0, lane.next_available_at - now)
            scheduled_at = max(now, lane.next_available_at)
            lane.next_available_at = scheduled_at + interval_sec
        if wait_sec > 0:
            lane.rate_wait_total += 1
            lane.rate_wait_seconds_total += wait_sec
            await self._sleep(wait_sec)
