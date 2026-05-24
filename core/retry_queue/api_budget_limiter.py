import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
import time
from typing import AsyncIterator, Awaitable, Callable, Mapping


DEFAULT_API_BUDGET_LIMITS = {
    "quotation_price": 4,
    "quotation_ohlcv": 2,
    "quotation_conclusion": 3,
    "quotation": 4,
    "account_balance": 1,
    "account_reconciliation": 1,
    "account": 1,
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
    "default": 8.0,
}


@dataclass
class _CategoryBudget:
    limit: int
    rate_limit_per_sec: float
    semaphore: asyncio.Semaphore
    rate_lock: asyncio.Lock
    next_available_at: float = 0.0
    active: int = 0
    acquired_total: int = 0
    max_observed_active: int = 0


class ApiBudgetLimiter:
    """전략/서비스가 공유하는 broker API 동시성/rate budget limiter."""

    def __init__(
        self,
        limits: Mapping[str, int] | None = None,
        *,
        rate_limits_per_sec: Mapping[str, float] | None = None,
        default_limit: int = 4,
        default_rate_limit_per_sec: float = 8.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._default_limit = self._validate_limit(default_limit)
        self._default_rate_limit_per_sec = self._validate_rate_limit(default_rate_limit_per_sec)
        self._sleep = sleep
        self._monotonic = monotonic
        configured = dict(DEFAULT_API_BUDGET_LIMITS if limits is None else limits)
        configured_rates = dict(
            DEFAULT_API_RATE_LIMITS_PER_SEC
            if rate_limits_per_sec is None
            else rate_limits_per_sec
        )
        self._budgets: dict[str, _CategoryBudget] = {
            category: self._new_budget(
                limit,
                configured_rates.get(category, self._default_rate_limit_per_sec),
            )
            for category, limit in configured.items()
        }

    @asynccontextmanager
    async def acquire(self, category: str | None) -> AsyncIterator[None]:
        actual_category = category if category is not None else "default"
        budget = self._budget_for(actual_category)
        await self._wait_for_rate_slot(budget)
        await budget.semaphore.acquire()
        budget.active += 1
        budget.acquired_total += 1
        budget.max_observed_active = max(budget.max_observed_active, budget.active)
        try:
            yield
        finally:
            budget.active -= 1
            budget.semaphore.release()

    def snapshot(self) -> dict[str, dict[str, int]]:
        return {
            category: {
                "limit": budget.limit,
                "rate_limit_per_sec": budget.rate_limit_per_sec,
                "active": budget.active,
                "acquired_total": budget.acquired_total,
                "max_observed_active": budget.max_observed_active,
            }
            for category, budget in self._budgets.items()
        }

    def _budget_for(self, category: str) -> _CategoryBudget:
        budget = self._budgets.get(category)
        if budget is None:
            budget = self._new_budget(
                self._default_limit,
                self._default_rate_limit_per_sec,
            )
            self._budgets[category] = budget
        return budget

    def _new_budget(self, limit: int, rate_limit_per_sec: float) -> _CategoryBudget:
        validated = self._validate_limit(limit)
        validated_rate = self._validate_rate_limit(rate_limit_per_sec)
        return _CategoryBudget(
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

    async def _wait_for_rate_slot(self, budget: _CategoryBudget) -> None:
        interval_sec = 1.0 / budget.rate_limit_per_sec
        async with budget.rate_lock:
            now = self._monotonic()
            wait_sec = max(0.0, budget.next_available_at - now)
            scheduled_at = max(now, budget.next_available_at)
            budget.next_available_at = scheduled_at + interval_sec
        if wait_sec > 0:
            await self._sleep(wait_sec)
