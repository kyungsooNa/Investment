"""장 마감 후 신고가 종목 전략 커버리지 백테스트 태스크."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional, TYPE_CHECKING

from task.background.after_market.after_market_task_base import AfterMarketTask

if TYPE_CHECKING:
    from core.market_clock import MarketClock
    from services.market_calendar_service import MarketCalendarService
    from scheduler.worker.worker_pool import WorkerPool


class NewHighStrategyCoverageBacktestTask(AfterMarketTask):
    def __init__(
        self,
        coverage_service,
        mcs: Optional["MarketCalendarService"] = None,
        market_clock: Optional["MarketClock"] = None,
        logger=None,
        worker_pool: Optional["WorkerPool"] = None,
    ) -> None:
        super().__init__(
            mcs=mcs,
            market_clock=market_clock,
            logger=logger or logging.getLogger(__name__),
            worker_pool=worker_pool,
        )
        self._coverage_service = coverage_service
        self._last_result = None

    @property
    def task_name(self) -> str:
        return "newhigh_strategy_coverage_backtest"

    @property
    def _scheduler_label(self) -> str:
        return "NewHighStrategyCoverageBacktestTask"

    async def _on_market_closed(self, latest_trading_date: str) -> None:
        self._logger.info(f"newhigh strategy coverage backtest 시작: {latest_trading_date}")
        try:
            self._last_result = await self._coverage_service.run(latest_trading_date)
        except Exception as e:
            self._logger.error(f"newhigh strategy coverage backtest 실패: {e}", exc_info=True)
            return
        self._logger.info(f"newhigh strategy coverage backtest 완료: {latest_trading_date}")

    async def force_run(self) -> None:
        self._logger.info("NewHighStrategyCoverageBacktestTask 강제 실행 요청")
        async with self._running_state():
            target_date: Optional[str] = None
            if self._mcs:
                try:
                    target_date = await self._mcs.get_latest_trading_date()
                except Exception as e:
                    self._logger.warning(f"최근 거래일 조회 실패 — 오늘 날짜로 대체: {e}")
            if not target_date:
                target_date = datetime.now().strftime("%Y%m%d")
            await self._on_market_closed(target_date)

    def get_progress(self) -> dict:
        last_result = None
        if self._last_result is not None:
            last_result = {
                "target_date": getattr(self._last_result, "target_date", ""),
                "skipped": bool(getattr(self._last_result, "skipped", False)),
                "skip_reason": getattr(self._last_result, "skip_reason", ""),
                "newhigh_count": int(getattr(self._last_result, "newhigh_count", 0)),
                "strategy_count": int(getattr(self._last_result, "strategy_count", 0)),
                "all_strategy_missed_count": int(
                    getattr(self._last_result, "all_strategy_missed_count", 0)
                ),
                "all_strategy_missed_rate": float(
                    getattr(self._last_result, "all_strategy_missed_rate", 0.0)
                ),
            }
        return {
            "running": self._state.value == "running",
            "last_result": last_result,
        }
