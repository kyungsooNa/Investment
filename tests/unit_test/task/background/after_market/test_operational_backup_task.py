from unittest.mock import AsyncMock, MagicMock

import pytest

from task.background.after_market.operational_backup_task import OperationalBackupTask


@pytest.mark.asyncio
async def test_operational_backup_task_runs_backup_and_reports_success():
    service = MagicMock()
    service.create_backup.return_value = {
        "status": "passed",
        "backup_dir": "data/operational_backups/20261001_163000",
        "files": [{"source": "state.json"}],
    }
    logger = MagicMock()
    task = OperationalBackupTask(backup_service=service, mcs=None, market_clock=None, logger=logger)

    await task._on_market_closed("20261001")

    service.create_backup.assert_called_once_with()
    logger.info.assert_called()


@pytest.mark.asyncio
async def test_operational_backup_task_logs_failed_restore_verification():
    service = MagicMock()
    service.create_backup.return_value = {
        "status": "failed",
        "backup_dir": "data/operational_backups/20261001_163000",
        "restore_verification": {"passed": False, "errors": [{"reason": "checksum_mismatch"}]},
    }
    logger = MagicMock()
    notification_service = MagicMock()
    notification_service.emit = AsyncMock()
    task = OperationalBackupTask(
        backup_service=service,
        notification_service=notification_service,
        mcs=None,
        market_clock=None,
        logger=logger,
    )

    await task._on_market_closed("20261001")

    logger.error.assert_called_once()
    notification_service.emit.assert_awaited_once()
    assert notification_service.emit.await_args.args[2] == "운영 백업 검증 실패"
