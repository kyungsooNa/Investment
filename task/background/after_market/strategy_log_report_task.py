"""장 마감 후 당일 전략 로그를 분석하여 매수/미매수 요약 리포트를 발송하는 태스크."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional, TYPE_CHECKING

from common.operator_alert_types import AlertSource
from task.background.after_market.after_market_task_base import AfterMarketTask
from services.notification_service import NotificationCategory, NotificationLevel, NotificationService

if TYPE_CHECKING:
    from services.strategy_log_report_service import StrategyLogReportService
    from services.rejection_distribution_service import RejectionDistributionService
    from core.market_clock import MarketClock
    from services.market_calendar_service import MarketCalendarService
    from scheduler.worker.worker_pool import WorkerPool
    from services.kill_switch_service import KillSwitchService
    from services.operator_alert_service import OperatorAlertService


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
        rejection_distribution_service: Optional["RejectionDistributionService"] = None,
        operator_alert_service: Optional["OperatorAlertService"] = None,
        kill_switch_service: Optional["KillSwitchService"] = None,
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
        self._rejection_distribution_service = rejection_distribution_service
        self._operator_alert_service = operator_alert_service
        self._kill_switch_service = kill_switch_service

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

        await self._emit_execution_quality_candidate_alert(latest_trading_date)
        await self._emit_strategy_degradation_candidate_alert(latest_trading_date)

        if self._rejection_distribution_service:
            try:
                self._rejection_distribution_service.flush_to_file(latest_trading_date)
            except Exception as e:
                self._logger.warning(f"거절 사유 분포 파일 저장 실패: {e}")

        if self._telegram_reporter:
            try:
                await self._telegram_reporter.send_strategy_log_report(report_html, latest_trading_date)
            except Exception as e:
                self._logger.warning(f"Telegram 리포트 전송 실패: {e}")

        self._logger.info("전략 로그 리포트 발송 완료")

    async def _emit_execution_quality_candidate_alert(self, report_date: str) -> None:
        if not self._notification_service:
            return
        getter = getattr(self._report_service, "get_last_execution_quality_candidates", None)
        if not callable(getter):
            return
        candidates = getter() or []
        if not candidates:
            return

        shown = []
        for item in candidates[:3]:
            strategy = item.get("strategy", "미분류")
            reason = item.get("reason", "")
            period = item.get("period", "")
            period_text = f"{period} " if period else ""
            shown.append(f"{strategy}({period_text}{reason})")
        extra = f" 외 {len(candidates) - 3}개" if len(candidates) > 3 else ""
        await self._notification_service.emit(
            NotificationCategory.STRATEGY,
            NotificationLevel.WARNING,
            "체결 품질 비활성화 후보",
            f"{report_date}: {', '.join(shown)}{extra}",
            metadata={
                "alert_type": "execution_quality_candidate",
                "report_date": report_date,
                "candidates": candidates,
            },
        )

    async def _emit_strategy_degradation_candidate_alert(self, report_date: str) -> None:
        getter = getattr(self._report_service, "get_last_strategy_degradation_candidates", None)
        if not callable(getter):
            return
        candidates = getter() or []
        if not candidates:
            return

        if self._operator_alert_service:
            for item in candidates:
                strategy = str(item.get("strategy") or "미분류")
                status = str(item.get("status") or "degraded")
                severity = "critical" if status == "critical_candidate" else "warning"
                ks_trip = None
                if self._kill_switch_service and strategy:
                    try:
                        ks_trip = self._kill_switch_service.is_strategy_tripped(strategy)
                    except Exception:
                        ks_trip = None
                reasons = ", ".join(str(reason) for reason in item.get("reasons") or [])
                metadata = {
                    "alert_type": "strategy_degradation_candidate",
                    "report_date": report_date,
                    "strategy": strategy,
                    "candidate": item,
                    "already_blocked_by_kill_switch": bool(ks_trip),
                    "kill_switch_trip": ks_trip,
                }
                await self._operator_alert_service.report(
                    AlertSource.STRATEGY_PERF,
                    f"strategy_perf:{strategy}",
                    severity,
                    f"전략 성과 저하 후보: {strategy}",
                    f"{report_date}: {status}" + (f" ({reasons})" if reasons else ""),
                    metadata=metadata,
                )
            return

        if not self._notification_service:
            return

        shown = []
        for item in candidates[:3]:
            strategy = item.get("strategy", "미분류")
            status = item.get("status", "")
            shown.append(f"{strategy}({status})" if status else str(strategy))
        extra = f" 외 {len(candidates) - 3}개" if len(candidates) > 3 else ""
        await self._notification_service.emit(
            NotificationCategory.STRATEGY,
            NotificationLevel.WARNING,
            "전략 성과 저하 후보",
            f"{report_date}: {', '.join(shown)}{extra}",
            metadata={
                "alert_type": "strategy_degradation_candidate",
                "report_date": report_date,
                "candidates": candidates,
            },
        )

    def get_progress(self) -> dict:
        return {"running": self._state.value == "running"}

    async def force_run(self) -> None:
        """skip/delay 를 우회하고 즉시 리포트를 생성·발송한다."""
        self._logger.info("StrategyLogReportTask 강제 실행 요청")
        async with self._running_state():
            target_date: Optional[str] = None
            if self._mcs:
                try:
                    target_date = await self._mcs.get_latest_trading_date()
                except Exception as e:
                    self._logger.warning(f"최근 거래일 조회 실패 — 오늘 날짜로 대체: {e}")
            if not target_date:
                target_date = datetime.now().strftime('%Y%m%d')
            await self._on_market_closed(target_date)
