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
