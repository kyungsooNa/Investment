from datetime import datetime
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
    virtual_trade_service.get_holds_by_strategy.return_value = []
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
        "stale_broker_reconciled": [],
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
    virtual_trade_service.get_holds_by_strategy.return_value = []
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
    assert result["stale_broker_reconciled"] == []


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


@pytest.mark.asyncio
async def test_opening_reconcile_flags_broker_reconciled_holds_older_than_threshold():
    broker = MagicMock()
    broker.get_account_balance = AsyncMock(
        return_value=ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data={"output1": []})
    )
    virtual_trade_service = MagicMock()
    virtual_trade_service.reconcile_with_broker = AsyncMock(
        return_value={"force_closed": [], "unknown_in_broker": [], "quantity_mismatches": []}
    )
    virtual_trade_service.get_holds_by_strategy.return_value = [
        {"code": "006110", "buy_date": "2026-05-14 15:14:05"},  # 54일 경과 → stale
        {"code": "084370", "buy_date": "2026-07-02 09:13:53"},  # 5일 경과 → 임계값 미만
    ]
    market_clock = MagicMock()
    market_clock.get_current_kst_time.return_value = datetime(2026, 7, 7, 9, 5)
    service = OpeningPositionReconcileService(
        broker=broker,
        virtual_trade_service=virtual_trade_service,
        market_clock=market_clock,
        logger=MagicMock(),
    )

    result = await service.reconcile_once()

    virtual_trade_service.get_holds_by_strategy.assert_called_once_with("broker_reconciled")
    assert result["stale_broker_reconciled"] == [{"code": "006110", "days_held": 54}]
