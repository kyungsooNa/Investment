from unittest.mock import MagicMock

from interfaces.schedulable_task import TaskPriority, TaskState


class _OverseasIntradayVBOTask:
    task_name = "overseas_intraday_vbo"
    priority = TaskPriority.NORMAL
    state = TaskState.RUNNING

    def get_progress(self):
        return {"running": True, "watch_count": 10}


def test_it_scheduler_status_exposes_overseas_market_tasks(paper_client, mock_paper_ctx):
    mock_paper_ctx.scheduler = None
    mock_paper_ctx.strategy_schedulers = {"domestic": None, "overseas_us": None}
    mock_paper_ctx.enabled_market_modes = ["domestic", "overseas_us"]
    mock_paper_ctx.background_scheduler = MagicMock()
    mock_paper_ctx.background_scheduler.get_task.side_effect = (
        lambda name: _OverseasIntradayVBOTask()
        if name == "overseas_intraday_vbo"
        else None
    )

    response = paper_client.get("/api/scheduler/status")

    assert response.status_code == 200
    body = response.json()
    assert body["running"] is False
    assert body["strategies"] == []
    assert [item["market"] for item in body["schedulers"]] == ["domestic", "overseas_us"]
    assert body["market_tasks"][0]["name"] == "overseas_intraday_vbo"
    assert body["market_tasks"][0]["market"] == "overseas_us"
    assert body["market_tasks"][0]["running"] is True
    assert body["market_tasks"][0]["live_trading"] is False
