"""장 마감 후 양·음봉 및 전일 대비 종가 방향 예측을 각각 채점해 알린다."""
from __future__ import annotations

from task.background.after_market.after_market_task_base import AfterMarketTask


class PremarketDirectionResultTask(AfterMarketTask):
    def __init__(self, *, premarket_direction_service, mcs=None, market_clock=None, logger=None, worker_pool=None):
        super().__init__(mcs=mcs, market_clock=market_clock, logger=logger, worker_pool=worker_pool)
        self._service = premarket_direction_service
        self._progress = {"running": False, "last_result": None}

    @property
    def task_name(self) -> str:
        return "premarket_direction_result"

    @property
    def _scheduler_label(self) -> str:
        return "PremarketDirectionResult"

    def get_progress(self) -> dict:
        return dict(self._progress)

    async def _on_market_closed(self, latest_trading_date: str) -> None:
        d = latest_trading_date
        self._progress["running"] = True
        try:
            result = await self._service.report_close(f"{d[:4]}-{d[4:6]}-{d[6:8]}", notify=True)
        finally:
            self._progress["running"] = False
        self._progress["last_result"] = result
