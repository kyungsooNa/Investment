from unittest.mock import AsyncMock, MagicMock

import pytest

from common.types import ErrorCode, ResCommonResponse
from services.opening_position_reconcile_service import OpeningPositionReconcileService


@pytest.mark.asyncio
async def test_opening_reconcile_delegates_to_virtual_trade_reconcile():
    broker = MagicMock()
    broker.get_account_balance = AsyncMock(
        return_value=ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value,
            msg1="OK",
            data={"output1": [{"pdno": "005930", "hldg_qty": "1"}]},
        )
    )
    virtual_trade_service = MagicMock()
    virtual_trade_service.reconcile_with_broker = AsyncMock(
        return_value={
            "force_closed": ["000660"],
            "unknown_in_broker": ["035420"],
            "quantity_mismatches": [{"code": "005930", "local_qty": 3, "broker_qty": 1}],
        }
    )
    logger = MagicMock()
    service = OpeningPositionReconcileService(
        broker=broker,
        virtual_trade_service=virtual_trade_service,
        logger=logger,
    )

    result = await service.reconcile_once()

    virtual_trade_service.reconcile_with_broker.assert_awaited_once_with(
        [{"pdno": "005930", "hldg_qty": "1"}],
        logger=logger,
    )
    assert result == {
        "force_closed": ["000660"],
        "unknown_in_broker": ["035420"],
        "quantity_mismatches": [{"code": "005930", "local_qty": 3, "broker_qty": 1}],
        "mismatch_count": 3,
        "error": None,
    }


@pytest.mark.asyncio
async def test_opening_reconcile_reports_zero_mismatch_when_positions_match():
    broker = MagicMock()
    broker.get_account_balance = AsyncMock(
        return_value=ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value,
            msg1="OK",
            data={"output1": [{"pdno": "005930", "hldg_qty": "3"}]},
        )
    )
    virtual_trade_service = MagicMock()
    virtual_trade_service.reconcile_with_broker = AsyncMock(
        return_value={"force_closed": [], "unknown_in_broker": [], "quantity_mismatches": []}
    )
    service = OpeningPositionReconcileService(
        broker=broker,
        virtual_trade_service=virtual_trade_service,
        logger=MagicMock(),
    )

    result = await service.reconcile_once()

    assert result["force_closed"] == []
    assert result["unknown_in_broker"] == []
    assert result["quantity_mismatches"] == []
    assert result["mismatch_count"] == 0
    assert result["error"] is None


@pytest.mark.asyncio
async def test_opening_reconcile_reports_balance_failure_without_delegation():
    broker = MagicMock()
    broker.get_account_balance = AsyncMock(
        return_value=ResCommonResponse(rt_cd=ErrorCode.API_ERROR.value, msg1="denied", data=None)
    )
    virtual_trade_service = MagicMock()
    virtual_trade_service.reconcile_with_broker = AsyncMock()
    service = OpeningPositionReconcileService(
        broker=broker,
        virtual_trade_service=virtual_trade_service,
        logger=MagicMock(),
    )

    result = await service.reconcile_once()

    assert result == {
        "force_closed": [],
        "unknown_in_broker": [],
        "quantity_mismatches": [],
        "mismatch_count": 0,
        "error": "denied",
    }
    virtual_trade_service.reconcile_with_broker.assert_not_awaited()
