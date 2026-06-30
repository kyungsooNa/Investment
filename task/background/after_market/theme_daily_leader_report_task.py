"""장 마감 후 당일 주도 테마 리포트를 별도 발송하는 태스크."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional, TYPE_CHECKING

from common.types import ErrorCode
from task.background.after_market.after_market_task_base import AfterMarketTask
from services.notification_service import NotificationCategory, NotificationLevel, NotificationService

if TYPE_CHECKING:
    from core.market_clock import MarketClock
    from services.market_calendar_service import MarketCalendarService
    from scheduler.worker.worker_pool import WorkerPool


class ThemeDailyLeaderReportTask(AfterMarketTask):
    """RankingTask가 수집한 당일 랭킹 캐시로 주도 테마 리포트를 생성·전송한다."""

    def __init__(
        self,
        ranking_task,
        theme_daily_leader_service,
        telegram_reporter=None,
        notification_service: Optional[NotificationService] = None,
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
        self._ranking_task = ranking_task
        self._theme_daily_leader_service = theme_daily_leader_service
        self._telegram_reporter = telegram_reporter
        self._notification_service = notification_service
        self._progress = {
            "running": False,
            "last_report_date": None,
            "sent_count": 0,
            "last_error": None,
        }

    @property
    def task_name(self) -> str:
        return "daily_theme_leader_report"

    @property
    def _scheduler_label(self) -> str:
        return "ThemeDailyLeaderReportTask"

    async def _on_market_closed(self, latest_trading_date: str) -> None:
        await self._send_report(latest_trading_date)

    async def _send_report(self, report_date: str) -> None:
        self._progress["running"] = True
        self._progress["last_error"] = None
        self._logger.info(f"당일 주도 테마 리포트 생성 시작: {report_date}")
        try:
            rankings = self._ranking_task.get_daily_theme_report_rankings()
            if not rankings or not rankings.get("all_stocks"):
                self._progress["last_error"] = "ranking_cache_empty"
                message = "랭킹 캐시가 비어 있어 당일 주도 테마 리포트를 건너뜁니다."
                self._logger.warning(message)
                if self._notification_service:
                    await self._notification_service.emit(
                        NotificationCategory.BACKGROUND,
                        NotificationLevel.WARNING,
                        "당일 주도 테마 리포트 스킵",
                        f"{report_date}: {message}",
                    )
                return

            theme_resp = await self._theme_daily_leader_service.build_daily_theme_report(
                rankings,
                report_date=report_date,
            )
            if not theme_resp or theme_resp.rt_cd != ErrorCode.SUCCESS.value:
                msg = getattr(theme_resp, "msg1", "") if theme_resp else "응답 없음"
                self._progress["last_error"] = f"theme_service_error:{msg}"
                self._logger.warning(f"당일 주도 테마 리포트 생성 실패: {msg}")
                return

            theme_data = theme_resp.data or []
            if self._telegram_reporter:
                await self._telegram_reporter.send_daily_theme_report(
                    theme_data,
                    report_date=report_date,
                )
            self._progress["last_report_date"] = report_date
            self._progress["sent_count"] = len(theme_data)
            self._logger.info(f"당일 주도 테마 리포트 발송 완료: {len(theme_data)}개 테마")
        except Exception as e:
            self._progress["last_error"] = str(e)
            self._logger.error(f"당일 주도 테마 리포트 발송 실패: {e}", exc_info=True)
        finally:
            self._progress["running"] = False

    def get_progress(self) -> dict:
        return dict(self._progress)

    async def force_run(self) -> None:
        """skip/delay를 우회하고 즉시 당일 주도 테마 리포트를 생성·발송한다."""
        target_date: Optional[str] = None
        if self._mcs:
            try:
                target_date = await self._mcs.get_latest_trading_date()
            except Exception as e:
                self._logger.warning(f"최근 거래일 조회 실패 — 오늘 날짜로 대체: {e}")
        if not target_date:
            target_date = datetime.now().strftime("%Y%m%d")
        async with self._running_state():
            await self._send_report(target_date)
