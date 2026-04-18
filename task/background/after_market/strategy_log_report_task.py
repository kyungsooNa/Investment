"""장 마감 후 당일 전략 로그를 분석하여 매수/미매수 요약 리포트를 발송하는 태스크."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional, TYPE_CHECKING

from task.background.after_market.after_market_task_base import AfterMarketTask
from services.notification_service import NotificationService, NotificationCategory, NotificationLevel

if TYPE_CHECKING:
    from services.strategy_log_report_service import StrategyLogReportService
    from core.market_clock import MarketClock
    from services.market_calendar_service import MarketCalendarService
    from scheduler.worker.worker_pool import WorkerPool


class StrategyLogReportTask(AfterMarketTask):
    """장 마감 후 당일 전략 로그를 분석하여 매수/미매수 요약 리포트를 발송한다."""

    def __init__(
        self,
        report_service: "StrategyLogReportService",
        notification_service: Optional[NotificationService] = None,
        telegram_reporter=None,
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
        self._report_service = report_service
        self._notification_service = notification_service
        self._telegram_reporter = telegram_reporter

    @property
    def task_name(self) -> str:
        return "strategy_log_report"

    @property
    def _scheduler_label(self) -> str:
        return "StrategyLogReportTask"

    async def _on_market_closed(self, latest_trading_date: str) -> None:
        self._logger.info(f"전략 로그 리포트 생성 시작: {latest_trading_date}")
        try:
            report_html = await self._report_service.generate_report(latest_trading_date)
        except Exception as e:
            self._logger.error(f"전략 로그 리포트 생성 실패: {e}", exc_info=True)
            return

        if self._notification_service:
            await self._notification_service.emit(
                NotificationCategory.BACKGROUND,
                NotificationLevel.INFO,
                "전략 로그 리포트",
                report_html,
            )

        if self._telegram_reporter:
            try:
                await self._telegram_reporter.send_strategy_log_report(report_html, latest_trading_date)
            except Exception as e:
                self._logger.warning(f"Telegram 리포트 전송 실패: {e}")

        self._logger.info("전략 로그 리포트 발송 완료")

    async def force_run(self) -> None:
        today = datetime.now().strftime('%Y%m%d')
        self._logger.info(f"StrategyLogReportTask 강제 실행: {today}")
        async with self._running_state():
            await self._on_market_closed(today)
