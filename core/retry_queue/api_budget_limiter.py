import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator, Mapping


DEFAULT_API_BUDGET_LIMITS = {
    "quotation": 8,
    "account": 2,
    "default": 8,
}


@dataclass
class _CategoryBudget:
    limit: int
    semaphore: asyncio.Semaphore
    active: int = 0
    acquired_total: int = 0
    max_observed_active: int = 0


class ApiBudgetLimiter:
    """전략/서비스가 공유하는 broker API 동시성 budget limiter."""

    def __init__(
        self,
        limits: Mapping[str, int] | None = None,
        *,
        default_limit: int = 8,
    ) -> None:
        self._default_limit = self._validate_limit(default_limit)
        configured = dict(DEFAULT_API_BUDGET_LIMITS if limits is None else limits)
        self._budgets: dict[str, _CategoryBudget] = {
            category: self._new_budget(limit)
            for category, limit in configured.items()
        }

    @asynccontextmanager
    async def acquire(self, category: str | None) -> AsyncIterator[None]:
        actual_category = category if category is not None else "default"
        budget = self._budget_for(actual_category)
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
                "active": budget.active,
                "acquired_total": budget.acquired_total,
                "max_observed_active": budget.max_observed_active,
            }
            for category, budget in self._budgets.items()
        }

    def _budget_for(self, category: str) -> _CategoryBudget:
        budget = self._budgets.get(category)
        if budget is None:
            budget = self._new_budget(self._default_limit)
            self._budgets[category] = budget
        return budget

    def _new_budget(self, limit: int) -> _CategoryBudget:
        validated = self._validate_limit(limit)
        return _CategoryBudget(
            limit=validated,
            semaphore=asyncio.Semaphore(validated),
        )

    @staticmethod
    def _validate_limit(limit: int) -> int:
        value = int(limit)
        if value < 1:
            raise ValueError("API budget limit must be >= 1")
        return value
