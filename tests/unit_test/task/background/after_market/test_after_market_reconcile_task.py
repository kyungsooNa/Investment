from unittest.mock import AsyncMock, MagicMock

import pytest

from task.background.after_market.after_market_reconcile_task import AfterMarketReconcileTask


@pytest.mark.asyncio
async def test_after_market_reconcile_success_no_notification():
    oes = MagicMock()
    oes.reconcile_orders_with_broker = AsyncMock(return_value=0)
    ns = AsyncMock()
    task = AfterMarketReconcileTask(
        order_execution_service=oes,
        notification_service=ns,
        logger=MagicMock(),
    )

    result = await task.run_once("20260430")

    assert result["mismatch_count"] == 0
    ns.emit.assert_awaited_once()
    assert task.get_history()[0]["mismatch_count"] == 0
    assert task.get_progress()["history_count"] == 1


@pytest.mark.asyncio
async def test_after_market_reconcile_mismatch_notifies():
    oes = MagicMock()
    oes.reconcile_orders_with_broker = AsyncMock(return_value=2)
    ns = AsyncMock()
    task = AfterMarketReconcileTask(
        order_execution_service=oes,
        notification_service=ns,
        logger=MagicMock(),
    )

    result = await task.run_once("20260430")

    assert result["mismatch_count"] == 2
    ns.emit.assert_awaited_once()
    assert task.get_history()[0]["mismatch_count"] == 2


@pytest.mark.asyncio
async def test_after_market_reconcile_error_notifies():
    oes = MagicMock()
    oes.reconcile_orders_with_broker = AsyncMock(side_effect=Exception("boom"))
    ns = AsyncMock()
    task = AfterMarketReconcileTask(
        order_execution_service=oes,
        notification_service=ns,
        logger=MagicMock(),
    )

    result = await task.run_once("20260430")

    assert result["error"] == "boom"
    ns.emit.assert_awaited_once()
    assert task.get_history()[0]["error"] == "boom"


@pytest.mark.asyncio
async def test_scheduler_label_and_on_market_closed_delegate_to_run_once():
    oes = MagicMock()
    oes.reconcile_orders_with_broker = AsyncMock(return_value=0)
    task = AfterMarketReconcileTask(
        order_execution_service=oes,
        logger=MagicMock(),
    )

    await task._on_market_closed("20260430")

    assert task.task_name == "after_market_reconcile"
    assert task._scheduler_label == "after_market_reconcile"
    assert task.get_history()[0]["date"] == "20260430"


@pytest.mark.asyncio
async def test_run_once_without_notification_service_covers_no_emit_paths():
    success_oes = MagicMock()
    success_oes.reconcile_orders_with_broker = AsyncMock(return_value=2)
    success_task = AfterMarketReconcileTask(order_execution_service=success_oes, logger=MagicMock())

    error_oes = MagicMock()
    error_oes.reconcile_orders_with_broker = AsyncMock(side_effect=RuntimeError("boom"))
    error_task = AfterMarketReconcileTask(order_execution_service=error_oes, logger=MagicMock())

    success = await success_task.run_once("20260430")
    error = await error_task.run_once("20260430")

    assert success["mismatch_count"] == 2
    assert error["error"] == "boom"


@pytest.mark.asyncio
async def test_force_run_uses_running_state_and_empty_date():
    oes = MagicMock()
    oes.reconcile_orders_with_broker = AsyncMock(return_value=0)
    task = AfterMarketReconcileTask(order_execution_service=oes, logger=MagicMock())

    await task.force_run()

    assert task.get_history()[0]["date"] == ""
    assert task.get_progress()["running"] is False


def test_record_history_keeps_max_history():
    task = AfterMarketReconcileTask(
        order_execution_service=MagicMock(),
        logger=MagicMock(),
    )

    for idx in range(task.MAX_HISTORY + 1):
        task._record_history({"idx": idx})

    assert len(task._history) == task.MAX_HISTORY
    assert task._history[0]["idx"] == 1
