from __future__ import annotations

import logging
from typing import Optional

from interfaces.schedulable_task import TaskPriority
from services.notification_service import NotificationCategory, NotificationLevel
from task.background.after_market.after_market_task_base import AfterMarketTask


class MarketCapGapReportTask(AfterMarketTask):
    """삼성전자/SK하이닉스와 미국 주요 기업 시총갭을 장마감 후 전송한다."""

    _SESSION_LABELS = {
        "kr_close": "한국장 마감",
        "us_close": "미국장 마감",
    }

    def __init__(
        self,
        market_cap_gap_service,
        telegram_reporter=None,
        notification_service=None,
        session: str = "kr_close",
        market_calendar_service=None,
        market_clock=None,
        scheduler_store=None,
        logger=None,
    ):
        if session not in self._SESSION_LABELS:
            raise ValueError("session은 kr_close 또는 us_close 이어야 합니다.")
        super().__init__(
            mcs=market_calendar_service,
            market_clock=market_clock,
            logger=logger or logging.getLogger(__name__),
            worker_pool=None,
        )
        self._service = market_cap_gap_service
        self._telegram_reporter = telegram_reporter
        self._notification_service = notification_service
        self._session = session
        # 재시작 시 중복 전송 방지를 위해 "마지막 전송 날짜"를 영속화한다.
        self._scheduler_store = scheduler_store
        self._state_key = f"market_cap_gap_last_sent_{self._session}"
        self._last_reported_date: Optional[str] = self._load_last_reported_date()
        self._progress = {
            "running": False,
            "last_reported_date": self._last_reported_date,
            "last_result": None,
        }

    @property
    def task_name(self) -> str:
        suffix = "kr" if self._session == "kr_close" else "us"
        return f"market_cap_gap_report_{suffix}"

    @property
    def _scheduler_label(self) -> str:
        return f"MarketCapGapReport:{self._session}"

    @property
    def _loop_timezone(self) -> str:
        return "America/New_York" if self._session == "us_close" else "Asia/Seoul"

    @property
    def _loop_cron_hour(self) -> int:
        return 16 if self._session == "us_close" else 15

    @property
    def _loop_cron_minute(self) -> int:
        return 30 if self._session == "us_close" else 50

    @property
    def priority(self) -> TaskPriority:
        return TaskPriority.LOW

    def get_progress(self) -> dict:
        return dict(self._progress)

    def _load_last_reported_date(self) -> Optional[str]:
        if self._scheduler_store is None:
            return None
        try:
            return self._scheduler_store.load_keyed(self._state_key)
        except Exception as exc:
            self._logger.warning(f"{self.task_name}: 마지막 전송 날짜 로드 실패 — {exc}")
            return None

    def _save_last_reported_date(self, date_str: str) -> None:
        if self._scheduler_store is None:
            return
        try:
            self._scheduler_store.save_keyed(self._state_key, date_str)
        except Exception as exc:
            self._logger.warning(f"{self.task_name}: 마지막 전송 날짜 저장 실패 — {exc}")

    async def _on_market_closed(self, latest_trading_date: str) -> None:
        if self._last_reported_date == latest_trading_date:
            self._logger.info(f"{self.task_name}: {latest_trading_date} 이미 전송 완료 — 스킵")
            return

        self._progress["running"] = True
        try:
            report = await self._service.build_report(
                report_date=latest_trading_date,
                trigger=self._session,
            )
            if self._telegram_reporter is not None:
                await self._telegram_reporter.send_market_cap_gap_report(
                    report,
                    latest_trading_date,
                    self._SESSION_LABELS[self._session],
                )
            self._last_reported_date = latest_trading_date
            self._save_last_reported_date(latest_trading_date)
            self._progress["last_reported_date"] = latest_trading_date
            self._progress["last_result"] = {
                "korean": len(report.get("korean") or []),
                "us": len(report.get("us") or []),
                "comparisons": len(report.get("comparisons") or []),
            }
            if self._notification_service:
                await self._notification_service.emit(
                    NotificationCategory.BACKGROUND,
                    NotificationLevel.INFO,
                    "시총갭 리포트 전송 완료",
                    f"{self._SESSION_LABELS[self._session]} 기준 {latest_trading_date}",
                )
        except Exception as exc:
            self._logger.error(f"시총갭 리포트 전송 실패: {exc}", exc_info=True)
            if self._notification_service:
                await self._notification_service.emit(
                    NotificationCategory.BACKGROUND,
                    NotificationLevel.ERROR,
                    "시총갭 리포트 전송 실패",
                    str(exc),
                )
        finally:
            self._progress["running"] = False
