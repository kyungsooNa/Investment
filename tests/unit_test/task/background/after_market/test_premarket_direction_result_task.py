"""장 마감 후 종가 예측 채점·결과 알림 태스크 테스트."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from task.background.after_market.premarket_direction_result_task import PremarketDirectionResultTask


def _task():
    service = MagicMock()
    service.report_close = AsyncMock(return_value={"ok": True, "date": "2026-11-20", "targets": []})
    task = PremarketDirectionResultTask(
        premarket_direction_service=service, mcs=MagicMock(), market_clock=MagicMock(), logger=MagicMock(),
    )
    return task, service


def test_task_name():
    task, _ = _task()
    assert task.task_name == "premarket_direction_result"


@pytest.mark.asyncio
async def test_market_closed_scores_latest_trading_date_with_notification():
    task, service = _task()

    await task._on_market_closed("20261120")

    service.report_close.assert_awaited_once_with("2026-11-20", notify=True)
    assert task.get_progress()["last_result"]["date"] == "2026-11-20"
