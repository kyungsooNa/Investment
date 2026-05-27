from unittest.mock import AsyncMock, MagicMock, patch

from interfaces.schedulable_task import TaskState
from task.background.after_market.post_market_replay_audit_task import PostMarketReplayAuditTask


def _make_task(service=None, mcs=None):
    service = service or MagicMock()
    service.run = AsyncMock()
    return PostMarketReplayAuditTask(
        audit_service=service,
        mcs=mcs,
        market_clock=MagicMock(),
        logger=MagicMock(),
    )


async def test_on_market_closed_runs_audit_service():
    service = MagicMock()
    service.run = AsyncMock()
    task = _make_task(service=service)

    await task._on_market_closed("20260505")

    service.run.assert_awaited_once_with("20260505")


async def test_force_run_uses_market_calendar_date():
    service = MagicMock()
    service.run = AsyncMock()
    mcs = MagicMock()
    mcs.get_latest_trading_date = AsyncMock(return_value="20260502")
    task = _make_task(service=service, mcs=mcs)

    await task.force_run()

    service.run.assert_awaited_once_with("20260502")
    assert task.state == TaskState.IDLE


async def test_force_run_falls_back_to_today_when_calendar_fails():
    service = MagicMock()
    service.run = AsyncMock()
    mcs = MagicMock()
    mcs.get_latest_trading_date = AsyncMock(side_effect=RuntimeError("calendar down"))
    task = _make_task(service=service, mcs=mcs)

    with patch("task.background.after_market.post_market_replay_audit_task.datetime") as mock_dt:
        mock_dt.now.return_value.strftime.return_value = "20260505"
        await task.force_run()

    service.run.assert_awaited_once_with("20260505")
    task._logger.warning.assert_called_once()
