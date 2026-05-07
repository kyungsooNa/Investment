from unittest.mock import AsyncMock, MagicMock

import pytest

from common.types import ErrorCode, ResCommonResponse
from services.opening_position_reconcile_service import OpeningPositionReconcileService


@pytest.mark.asyncio
async def test_opening_reconcile_detect_only_builds_quantity_plan_without_orders():
    virtual_trade_service = MagicMock()
    virtual_trade_service.get_holds.return_value = [
        {"code": "005930", "strategy": "S1", "qty": 3},
        {"code": "000660", "strategy": "S2", "qty": 1},
    ]
    broker = MagicMock()
    broker.get_account_balance = AsyncMock(
        return_value=ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value,
            msg1="OK",
            data={"output1": [{"pdno": "005930", "hldg_qty": "1"}, {"pdno": "035420", "hldg_qty": "2"}]},
        )
    )
    order_execution_service = MagicMock()
    order_execution_service.handle_place_buy_order = AsyncMock()
    order_execution_service.handle_place_sell_order = AsyncMock()
    service = OpeningPositionReconcileService(
        broker=broker,
        order_execution_service=order_execution_service,
        virtual_trade_service=virtual_trade_service,
        logger=MagicMock(),
    )

    result = await service.reconcile_once()

    assert result["detect_only"] is True
    assert result["planned_buys"] == [
        {"code": "000660", "qty": 1, "local_qty": 1, "broker_qty": 0, "strategy": "S2"},
        {"code": "005930", "qty": 2, "local_qty": 3, "broker_qty": 1, "strategy": "S1"}
    ]
    assert result["planned_sells"] == []
    assert result["skipped"] == [
        {
            "code": "035420",
            "qty": 2,
            "local_qty": 0,
            "broker_qty": 2,
            "reason": "unknown_broker_holding",
        }
    ]
    order_execution_service.handle_place_buy_order.assert_not_awaited()
    order_execution_service.handle_place_sell_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_opening_reconcile_can_buy_missing_local_quantity_when_enabled():
    virtual_trade_service = MagicMock()
    virtual_trade_service.get_holds.return_value = [{"code": "005930", "strategy": "S1", "qty": 3}]
    broker = MagicMock()
    broker.get_account_balance = AsyncMock(
        return_value=ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value,
            msg1="OK",
            data={"output1": [{"pdno": "005930", "hldg_qty": "1"}]},
        )
    )
    order_execution_service = MagicMock()
    order_execution_service.handle_place_buy_order = AsyncMock(
        return_value=ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data=None)
    )
    service = OpeningPositionReconcileService(
        broker=broker,
        order_execution_service=order_execution_service,
        virtual_trade_service=virtual_trade_service,
        detect_only=False,
        auto_buy_missing_local=True,
        logger=MagicMock(),
    )

    result = await service.reconcile_once()

    order_execution_service.handle_place_buy_order.assert_awaited_once_with(
        "005930",
        0,
        2,
        source="reconcile:opening",
        finalize_immediately=False,
    )
    assert result["executed"] == [
        {"action": "BUY", "code": "005930", "qty": 2, "success": True, "message": "OK"}
    ]


@pytest.mark.asyncio
async def test_opening_reconcile_sells_only_managed_excess_by_default():
    virtual_trade_service = MagicMock()
    virtual_trade_service.get_holds.return_value = [{"code": "005930", "strategy": "S1", "qty": 1}]
    broker = MagicMock()
    broker.get_account_balance = AsyncMock(
        return_value=ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value,
            msg1="OK",
            data={
                "output1": [
                    {"pdno": "005930", "hldg_qty": "3"},
                    {"pdno": "035420", "hldg_qty": "2"},
                ]
            },
        )
    )
    order_execution_service = MagicMock()
    order_execution_service.handle_place_sell_order = AsyncMock(
        return_value=ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data=None)
    )
    service = OpeningPositionReconcileService(
        broker=broker,
        order_execution_service=order_execution_service,
        virtual_trade_service=virtual_trade_service,
        detect_only=False,
        auto_sell_extra_broker=True,
        logger=MagicMock(),
    )

    result = await service.reconcile_once()

    order_execution_service.handle_place_sell_order.assert_awaited_once_with(
        "005930",
        0,
        2,
        source="reconcile:opening",
        finalize_immediately=False,
    )
    assert result["skipped"] == [
        {
            "code": "035420",
            "qty": 2,
            "local_qty": 0,
            "broker_qty": 2,
            "reason": "unknown_broker_holding",
        }
    ]
    assert result["executed"] == [
        {"action": "SELL", "code": "005930", "qty": 2, "success": True, "message": "OK"}
    ]


@pytest.mark.asyncio
async def test_opening_reconcile_allows_unknown_broker_sell_only_when_explicitly_enabled():
    virtual_trade_service = MagicMock()
    virtual_trade_service.get_holds.return_value = []
    broker = MagicMock()
    broker.get_account_balance = AsyncMock(
        return_value=ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value,
            msg1="OK",
            data={"output1": [{"pdno": "035420", "hldg_qty": "2"}]},
        )
    )
    order_execution_service = MagicMock()
    order_execution_service.handle_place_sell_order = AsyncMock(
        return_value=ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data=None)
    )
    service = OpeningPositionReconcileService(
        broker=broker,
        order_execution_service=order_execution_service,
        virtual_trade_service=virtual_trade_service,
        detect_only=False,
        auto_sell_extra_broker=True,
        allow_sell_unknown_broker=True,
        logger=MagicMock(),
    )

    result = await service.reconcile_once()

    order_execution_service.handle_place_sell_order.assert_awaited_once()
    assert result["planned_sells"] == [
        {
            "code": "035420",
            "qty": 2,
            "local_qty": 0,
            "broker_qty": 2,
            "reason": "unknown_broker_holding",
        }
    ]
    assert result["skipped"] == []


@pytest.mark.asyncio
async def test_opening_reconcile_reports_balance_failure_without_orders():
    broker = MagicMock()
    broker.get_account_balance = AsyncMock(
        return_value=ResCommonResponse(rt_cd=ErrorCode.API_ERROR.value, msg1="denied", data=None)
    )
    order_execution_service = MagicMock()
    order_execution_service.handle_place_buy_order = AsyncMock()
    service = OpeningPositionReconcileService(
        broker=broker,
        order_execution_service=order_execution_service,
        virtual_trade_service=MagicMock(),
        logger=MagicMock(),
    )

    result = await service.reconcile_once()

    assert result["error"] == "denied"
    order_execution_service.handle_place_buy_order.assert_not_awaited()
