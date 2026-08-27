from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from interfaces.schedulable_task import TaskPriority, TaskState
from view.web import api_common
from view.web.routes.scheduler import get_scheduler_status


class _Task:
    task_name = "overseas_intraday"
    priority = TaskPriority.NORMAL
    state = TaskState.RUNNING

    def get_progress(self):
        return {"running": True, "watch_count": 10}


@pytest.mark.asyncio
async def test_scheduler_status_includes_overseas_market_task_without_domestic_scheduler():
    background_scheduler = MagicMock()
    background_scheduler.get_task.side_effect = (
        lambda name: _Task() if name == "overseas_intraday" else None
    )
    ctx = SimpleNamespace(
        scheduler=None,
        strategy_schedulers={"domestic": None, "overseas_us": None},
        background_scheduler=background_scheduler,
        enabled_market_modes=["domestic", "overseas_us"],
    )
    api_common.set_ctx(ctx)
    try:
        status = await get_scheduler_status()
    finally:
        api_common.set_ctx(None)

    assert status["running"] is False
    assert status["strategies"] == []
    assert [item["market"] for item in status["schedulers"]] == ["domestic", "overseas_us"]
    assert status["market_tasks"] == [
        {
            "name": "overseas_intraday",
            "display_name": "미국장 장중 전략",
            "market": "overseas_us",
            "market_label": "미국장",
            "mode": "paper",
            "live_trading": False,
            "state": "running",
            "running": True,
            "priority": 50,
            "progress": {"running": True, "watch_count": 10},
        }
    ]
