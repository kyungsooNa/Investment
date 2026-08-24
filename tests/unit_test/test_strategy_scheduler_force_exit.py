from unittest.mock import AsyncMock, Mock

import pytest

from common.types import ErrorCode, ResCommonResponse
from scheduler.strategy_scheduler import StrategyScheduler, StrategySchedulerConfig


class _StrategyStub:
    name = "larry_williams_vbo"


def _make_scheduler(*, broker_positions: dict[str, int]) -> StrategyScheduler:
    scheduler = StrategyScheduler.__new__(StrategyScheduler)
    scheduler._logger = Mock()
    scheduler._signal_history = []
    scheduler.stock_code_repository = Mock()
    scheduler.stock_code_repository.get_name_by_code.side_effect = lambda code: code
    scheduler._get_strategy_holdings = Mock(return_value=[
        {
            "strategy": "larry_williams_vbo",
            "code": "011200",
            "name": "HMM",
            "buy_price": 22550,
            "qty": 45,
            "status": "HOLD",
        }
    ])
    scheduler._get_broker_position_map_for_force_exit = AsyncMock(return_value=broker_positions)
    return scheduler


@pytest.mark.asyncio
async def test_force_liquidation_skips_local_hold_when_broker_has_no_position():
    scheduler = _make_scheduler(broker_positions={"011200": 0})
    cfg = StrategySchedulerConfig(strategy=_StrategyStub(), force_exit_on_close=True)

    holdings = await scheduler._get_force_liquidation_holdings(cfg)

    assert holdings == []


@pytest.mark.asyncio
async def test_force_liquidation_caps_local_hold_to_broker_position_qty():
    scheduler = _make_scheduler(broker_positions={"011200": 44})
    cfg = StrategySchedulerConfig(strategy=_StrategyStub(), force_exit_on_close=True)

    holdings = await scheduler._get_force_liquidation_holdings(cfg)

    assert len(holdings) == 1
    assert holdings[0]["code"] == "011200"
    assert holdings[0]["qty"] == 44


@pytest.mark.asyncio
async def test_force_liquidation_uses_local_hold_when_broker_lookup_fails():
    scheduler = _make_scheduler(broker_positions={})
    scheduler._get_broker_position_map_for_force_exit = AsyncMock(return_value=None)
    cfg = StrategySchedulerConfig(strategy=_StrategyStub(), force_exit_on_close=True)

    holdings = await scheduler._get_force_liquidation_holdings(cfg)

    assert len(holdings) == 1
    assert holdings[0]["code"] == "011200"
    assert holdings[0]["qty"] == 45


@pytest.mark.asyncio
async def test_force_exit_broker_position_lookup_returns_none_on_balance_failure():
    scheduler = StrategyScheduler.__new__(StrategyScheduler)
    scheduler._logger = Mock()
    broker = Mock()
    broker.get_account_balance = AsyncMock(
        return_value=ResCommonResponse(
            rt_cd=ErrorCode.API_ERROR.value,
            msg1="모의투자 잔고내역이 없습니다.",
            data=None,
        )
    )
    scheduler._oes = Mock(broker_api_wrapper=broker)

    positions = await scheduler._get_broker_position_map_for_force_exit()

    assert positions is None
