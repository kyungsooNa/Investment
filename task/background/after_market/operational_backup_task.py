"""장 마감 후 운영 원장·상태를 백업하고 복원 리허설을 수행한다."""
from __future__ import annotations

import asyncio

from interfaces.schedulable_task import TaskPriority
from services.notification_service import NotificationCategory, NotificationLevel
from services.operational_backup_service import OperationalBackupService
from task.background.after_market.after_market_task_base import AfterMarketTask


class OperationalBackupTask(AfterMarketTask):
    def __init__(
        self,
        *,
        backup_service=None,
        notification_service=None,
        mcs=None,
        market_clock=None,
        logger=None,
        worker_pool=None,
    ):
        super().__init__(mcs=mcs, market_clock=market_clock, logger=logger, worker_pool=worker_pool)
        self._backup_service = backup_service or OperationalBackupService()
        self._notification_service = notification_service
        self._progress = {"running": False, "last_result": None}

    @property
    def task_name(self) -> str:
        return "operational_backup"

    @property
    def _scheduler_label(self) -> str:
        return "OperationalBackup"

    @property
    def priority(self) -> TaskPriority:
        return TaskPriority.MAINTENANCE

    def get_progress(self) -> dict:
        return dict(self._progress)

    async def _on_market_closed(self, latest_trading_date: str) -> None:
        self._progress["running"] = True
        try:
            result = await asyncio.to_thread(self._backup_service.create_backup)
        finally:
            self._progress["running"] = False
        self._progress["last_result"] = result
        if result.get("status") != "passed":
            self._logger.error(f"OperationalBackupTask 복원 검증 실패: {result}")
            if self._notification_service:
                await self._notification_service.emit(
                    NotificationCategory.BACKGROUND,
                    NotificationLevel.ERROR,
                    "운영 백업 검증 실패",
                    f"백업 {result.get('backup_dir') or '-'}의 복원 검증에 실패했습니다.",
                    {"backup_dir": result.get("backup_dir"), "result": result},
                )
            return
        self._logger.info(
            f"OperationalBackupTask 완료: {result.get('backup_dir')} "
            f"({len(result.get('files') or [])}개 파일)"
        )

    async def force_run(self) -> None:
        async with self._running_state():
            await self._on_market_closed("")
