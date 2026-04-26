from unittest.mock import AsyncMock, MagicMock

import pytest

from common.types import ErrorCode, Exchange, OrderSide
from config.config_loader import RiskGateConfig
from core.account_snapshot import AccountSnapshot
from services.risk_gate_service import RiskGateService


def _service(
    *,
    config: RiskGateConfig | None = None,
    kill_switch=None,
    snapshot: AccountSnapshot | None = None,
    logger=None,
):
    if kill_switch is None:
        kill_switch = AsyncMock()
        kill_switch.check_orders_allowed = AsyncMock(return_value=(True, None))
    cache = MagicMock()
    cache.get = AsyncMock(return_value=snapshot or AccountSnapshot(
        total_equity=100_000_000,
        available_cash=50_000_000,
        positions={"000660": 10_000_000},
    ))
    return RiskGateService(
        config=config or RiskGateConfig(),
        kill_switch_service=kill_switch,
        account_snapshot_cache=cache,
        logger=logger or MagicMock(),
    ), kill_switch, cache


@pytest.mark.asyncio
async def test_validate_order_returns_none_when_allowed():
    svc, _, _ = _service()

    result = await svc.validate_order(
        stock_code="005930",
        price=70_000,
        qty=10,
        side=OrderSide.BUY,
        exchange=Exchange.KRX,
        active_order_count=0,
    )

    assert result is None


@pytest.mark.asyncio
async def test_kill_switch_blocks_with_compatible_error_code():
    kill_switch = AsyncMock()
    kill_switch.check_orders_allowed = AsyncMock(return_value=(False, "daily loss"))
    svc, _, _ = _service(kill_switch=kill_switch)

    result = await svc.validate_order("005930", 70_000, 10, OrderSide.BUY, Exchange.KRX, 0)

    assert result.rt_cd == ErrorCode.KILL_SWITCH_BLOCKED.value
    assert "daily loss" in result.msg1


@pytest.mark.asyncio
async def test_kill_switch_check_exception_fails_closed():
    kill_switch = AsyncMock()
    kill_switch.check_orders_allowed = AsyncMock(side_effect=RuntimeError("ks down"))
    svc, _, _ = _service(kill_switch=kill_switch)

    result = await svc.validate_order("005930", 70_000, 10, OrderSide.BUY, Exchange.KRX, 0)

    assert result.rt_cd == ErrorCode.RISK_GATE_BLOCKED.value
    assert "Kill Switch 확인 실패" in result.msg1


@pytest.mark.asyncio
async def test_order_amount_over_limit_blocks_buy():
    svc, _, _ = _service(config=RiskGateConfig(max_order_amount_won=100_000))

    result = await svc.validate_order("005930", 70_000, 2, OrderSide.BUY, Exchange.KRX, 0)

    assert result.rt_cd == ErrorCode.RISK_GATE_BLOCKED.value
    assert "주문 금액 초과" in result.msg1


@pytest.mark.asyncio
async def test_buy_with_non_positive_price_blocks():
    svc, _, _ = _service()

    result = await svc.validate_order("005930", 0, 10, OrderSide.BUY, Exchange.KRX, 0)

    assert result.rt_cd == ErrorCode.RISK_GATE_BLOCKED.value
    assert "0 이하 가격" in result.msg1


@pytest.mark.asyncio
async def test_sell_with_non_positive_price_skips_amount_and_exposure_checks():
    svc, _, cache = _service(config=RiskGateConfig(max_order_amount_won=1))

    result = await svc.validate_order("005930", 0, 10, OrderSide.SELL, Exchange.KRX, 0)

    assert result is None
    cache.get.assert_not_awaited()


@pytest.mark.asyncio
async def test_active_order_count_over_limit_blocks():
    svc, _, _ = _service(config=RiskGateConfig(max_pending_orders=2))

    result = await svc.validate_order("005930", 70_000, 1, OrderSide.SELL, Exchange.KRX, 2)

    assert result.rt_cd == ErrorCode.RISK_GATE_BLOCKED.value
    assert "진행 중 주문 수 초과" in result.msg1


@pytest.mark.asyncio
async def test_buy_exposure_over_limit_blocks_but_sell_passes():
    snapshot = AccountSnapshot(
        total_equity=100_000_000,
        available_cash=50_000_000,
        positions={"000660": 94_000_000},
    )
    svc, _, _ = _service(
        config=RiskGateConfig(max_total_exposure_pct=95.0),
        snapshot=snapshot,
    )

    buy_result = await svc.validate_order("005930", 2_000_000, 1, OrderSide.BUY, Exchange.KRX, 0)
    sell_result = await svc.validate_order("005930", 2_000_000, 1, OrderSide.SELL, Exchange.KRX, 0)

    assert buy_result.rt_cd == ErrorCode.RISK_GATE_BLOCKED.value
    assert "계좌 노출 한도 초과" in buy_result.msg1
    assert sell_result is None


@pytest.mark.asyncio
async def test_zero_equity_snapshot_fails_open_and_logs_warning():
    logger = MagicMock()
    snapshot = AccountSnapshot(total_equity=0, available_cash=0, positions={})
    svc, _, _ = _service(snapshot=snapshot, logger=logger)

    result = await svc.validate_order("005930", 70_000, 10, OrderSide.BUY, Exchange.KRX, 0)

    assert result is None
    logger.warning.assert_called()


@pytest.mark.asyncio
async def test_disabled_gate_allows_without_checks():
    svc, kill_switch, cache = _service(config=RiskGateConfig(enabled=False))

    result = await svc.validate_order("005930", 0, 10, OrderSide.BUY, Exchange.KRX, 99)

    assert result is None
    kill_switch.check_orders_allowed.assert_not_awaited()
    cache.get.assert_not_awaited()
