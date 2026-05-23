"""RiskGateService — P3-4 Phase 2 PR 2b strategy_id 정규화.

RiskGateService 는 validate_order(strategy_name=...) 진입 시 입력값을 strategy_id
로 normalize 한 뒤 downstream consumer (kill_switch, virtual_trade_provider,
config) 에 전달한다. config 의 strategy_limits 키도 dual-key (한국어/id) 로
조회한다.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from common.types import Exchange, OrderSide
from config.config_loader import RiskGateConfig, RiskGateStrategyLimitConfig
from core.account_snapshot import AccountSnapshot
from services.risk_gate_service import RiskGateService


def _service(
    *,
    config: RiskGateConfig | None = None,
    kill_switch=None,
    snapshot: AccountSnapshot | None = None,
    strategy_provider=None,
):
    if kill_switch is None:
        kill_switch = AsyncMock()
        kill_switch.check_orders_allowed = AsyncMock(return_value=(True, None))
        kill_switch.is_strategy_tripped = MagicMock(return_value=None)
    cache = MagicMock()
    cache.get = AsyncMock(return_value=snapshot or AccountSnapshot(
        total_equity=100_000_000,
        available_cash=50_000_000,
        positions={},
    ))
    return RiskGateService(
        config=config or RiskGateConfig(),
        kill_switch_service=kill_switch,
        account_snapshot_cache=cache,
        strategy_risk_provider=strategy_provider,
        logger=MagicMock(),
        env=None,
    ), kill_switch


# ───────────── strategy_name → strategy_id 정규화 ─────────────


@pytest.mark.asyncio
async def test_validate_order_normalizes_korean_to_id_for_kill_switch():
    """RiskGate 가 한국어 strategy_name 을 받으면 kill_switch 에는 strategy_id 로 전달."""
    svc, kill_switch = _service()
    await svc.validate_order(
        stock_code="005930",
        price=70000,
        qty=1,
        side=OrderSide.BUY,
        exchange=Exchange.KRX,
        active_order_count=0,
        strategy_name="거래량돌파",
    )
    # is_strategy_tripped 는 strategy_id 로 호출되어야 함
    kill_switch.is_strategy_tripped.assert_called_with("volume_breakout_live")


@pytest.mark.asyncio
async def test_validate_order_normalizes_id_input_idempotent():
    svc, kill_switch = _service()
    await svc.validate_order(
        stock_code="005930",
        price=70000,
        qty=1,
        side=OrderSide.BUY,
        exchange=Exchange.KRX,
        active_order_count=0,
        strategy_name="volume_breakout_live",
    )
    kill_switch.is_strategy_tripped.assert_called_with("volume_breakout_live")


@pytest.mark.asyncio
async def test_validate_order_passes_unknown_strategy_through():
    svc, kill_switch = _service()
    await svc.validate_order(
        stock_code="005930",
        price=70000,
        qty=1,
        side=OrderSide.BUY,
        exchange=Exchange.KRX,
        active_order_count=0,
        strategy_name="custom_research_001",
    )
    kill_switch.is_strategy_tripped.assert_called_with("custom_research_001")


# ───────────── _get_strategy_limit dual-key ─────────────


def test_get_strategy_limit_finds_korean_config_with_id_query():
    custom = RiskGateStrategyLimitConfig(max_loss_pct=10.0)
    cfg = RiskGateConfig(strategy_limits={"거래량돌파": custom})
    svc, _ = _service(config=cfg)
    limit = svc._get_strategy_limit("volume_breakout_live")
    assert limit is not None
    assert limit.max_loss_pct == 10.0


def test_get_strategy_limit_finds_id_config_with_korean_query():
    custom = RiskGateStrategyLimitConfig(max_loss_pct=10.0)
    cfg = RiskGateConfig(strategy_limits={"volume_breakout_live": custom})
    svc, _ = _service(config=cfg)
    limit = svc._get_strategy_limit("거래량돌파")
    assert limit is not None
    assert limit.max_loss_pct == 10.0


def test_get_strategy_limit_falls_back_to_default_for_unknown():
    default = RiskGateStrategyLimitConfig(max_loss_pct=20.0)
    cfg = RiskGateConfig(default_strategy_limit=default)
    svc, _ = _service(config=cfg)
    limit = svc._get_strategy_limit("custom_xyz")
    assert limit is default


# ───────────── strategy_provider 위임 시에도 strategy_id 전달 ─────────────


@pytest.mark.asyncio
async def test_strategy_provider_receives_strategy_id_for_duplicate_check():
    """duplicate_strategy_position 검증 경로에서 provider 에는 strategy_id 가 전달돼야 한다."""
    provider = MagicMock()
    provider.is_holding = MagicMock(return_value=False)
    provider.get_strategy_return_history = MagicMock(return_value=[])
    provider.get_holds_by_strategy = MagicMock(return_value=[])

    cfg = RiskGateConfig(
        enabled=True,
        block_duplicate_strategy_position=True,
        strategy_limits={"volume_breakout_live": RiskGateStrategyLimitConfig(
            block_duplicate_position=True,
        )},
    )
    svc, _ = _service(config=cfg, strategy_provider=provider)
    await svc.validate_order(
        stock_code="005930",
        price=70000,
        qty=1,
        side=OrderSide.BUY,
        exchange=Exchange.KRX,
        active_order_count=0,
        strategy_name="거래량돌파",
    )
    # provider 에는 strategy_id 로 전달돼야 함
    provider.is_holding.assert_called_with("volume_breakout_live", "005930")
