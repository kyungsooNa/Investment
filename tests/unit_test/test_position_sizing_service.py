# tests/unit_test/test_position_sizing_service.py
"""PositionSizingService 단위 테스트 — 전략별 자본 캡 (capital_allocation_pct) 중심."""
import math
import pytest
from dataclasses import dataclass
from typing import Dict
from unittest.mock import AsyncMock, MagicMock

from core.account_snapshot import AccountSnapshot, AccountSnapshotCache
from config.config_loader import PositionSizingConfig, RiskGateConfig, RiskGateStrategyLimitConfig
from common.types import Exchange, TradeSignal
from services.position_sizing_service import PositionSizingService


# ── 픽스처 헬퍼 ────────────────────────────────────────────────────────────────


def _make_snapshot(
    total_equity: int = 10_000_000,
    available_cash: int = 5_000_000,
    positions: Dict[str, int] | None = None,
) -> AccountSnapshot:
    return AccountSnapshot(
        total_equity=total_equity,
        available_cash=available_cash,
        positions=positions or {},
    )


def _make_cache(snapshot: AccountSnapshot) -> AccountSnapshotCache:
    cache = MagicMock(spec=AccountSnapshotCache)
    cache.get = AsyncMock(return_value=snapshot)
    return cache


def _make_indicator_svc() -> MagicMock:
    """ATR 조회 항상 실패 → per_share_risk 는 stop_loss_pct 기반으로 계산된다."""
    svc = MagicMock()
    svc.calculate_atr = AsyncMock(return_value=None)
    return svc


def _base_cfg(**overrides) -> PositionSizingConfig:
    defaults = dict(
        enabled=True,
        per_trade_risk_pct=1.5,
        max_per_position_pct=10.0,
        default_stop_loss_pct=-5.0,
        atr_period=14,
        atr_multiplier=2.0,
        min_stop_distance_pct=1.0,
    )
    defaults.update(overrides)
    return PositionSizingConfig(**defaults)


def _make_signal(
    code: str = "005930",
    price: int = 10_000,
    qty: int = 100,
    strategy_name: str = "",
) -> TradeSignal:
    return TradeSignal(
        code=code,
        name="테스트종목",
        action="BUY",
        price=price,
        qty=qty,
        strategy_name=strategy_name,
    )


# ── 전략별 자본 캡 테스트 ─────────────────────────────────────────────────────


async def test_strategy_cap_reduces_qty_when_allocation_exceeded():
    """capital_allocation_pct 한도가 signal.qty × price 보다 낮으면 qty를 줄인다."""
    # total_equity = 10,000,000. capital_allocation_pct = 10.0 → budget = 1,000,000
    # price = 10,000 → alloc_qty = 100. signal.qty = 200 → final 100
    snapshot = _make_snapshot(total_equity=10_000_000, available_cash=5_000_000)
    risk_cfg = RiskGateConfig(
        strategy_limits={
            "my_strategy": RiskGateStrategyLimitConfig(capital_allocation_pct=10.0)
        }
    )

    svc = PositionSizingService(
        account_snapshot_cache=_make_cache(snapshot),
        indicator_service=_make_indicator_svc(),
        config=_base_cfg(),
        risk_gate_config=risk_cfg,
    )
    signal = _make_signal(price=10_000, qty=200, strategy_name="my_strategy")

    final_qty, reason = await svc.adjust_buy_qty(signal, exchange=Exchange.KRX)

    # alloc_budget = 1,000,000 → alloc_qty = 100
    assert final_qty <= 100
    assert "strategy_capital_cap" in reason


async def test_strategy_cap_not_applied_when_within_budget():
    """주문 금액이 capital_allocation_pct 한도 이내면 캡이 작동하지 않는다."""
    # total_equity = 10,000,000. capital_allocation_pct = 50.0 → budget = 5,000,000
    # price = 10,000, qty = 10 → order = 100,000 → 한도 이내
    snapshot = _make_snapshot(total_equity=10_000_000, available_cash=5_000_000)
    risk_cfg = RiskGateConfig(
        strategy_limits={
            "my_strategy": RiskGateStrategyLimitConfig(capital_allocation_pct=50.0)
        }
    )

    svc = PositionSizingService(
        account_snapshot_cache=_make_cache(snapshot),
        indicator_service=_make_indicator_svc(),
        config=_base_cfg(per_trade_risk_pct=100.0),  # risk_qty 충분히 크게
        risk_gate_config=risk_cfg,
    )
    signal = _make_signal(price=10_000, qty=10, strategy_name="my_strategy")

    final_qty, reason = await svc.adjust_buy_qty(signal, exchange=Exchange.KRX)

    assert "strategy_capital_cap" not in reason


async def test_strategy_cap_not_applied_when_no_risk_config():
    """risk_gate_config 미주입 시 캡이 동작하지 않는다."""
    snapshot = _make_snapshot(total_equity=10_000_000, available_cash=5_000_000)

    svc = PositionSizingService(
        account_snapshot_cache=_make_cache(snapshot),
        indicator_service=_make_indicator_svc(),
        config=_base_cfg(per_trade_risk_pct=100.0),
    )
    signal = _make_signal(price=10_000, qty=200, strategy_name="my_strategy")

    final_qty, reason = await svc.adjust_buy_qty(signal, exchange=Exchange.KRX)

    assert "strategy_capital_cap" not in reason


async def test_strategy_cap_not_applied_when_strategy_name_absent():
    """strategy_name 이 빈 문자열이면 캡이 동작하지 않는다."""
    snapshot = _make_snapshot(total_equity=10_000_000, available_cash=5_000_000)
    risk_cfg = RiskGateConfig(
        strategy_limits={
            "my_strategy": RiskGateStrategyLimitConfig(capital_allocation_pct=10.0)
        }
    )

    svc = PositionSizingService(
        account_snapshot_cache=_make_cache(snapshot),
        indicator_service=_make_indicator_svc(),
        config=_base_cfg(per_trade_risk_pct=100.0),
        risk_gate_config=risk_cfg,
    )
    signal = _make_signal(price=10_000, qty=200, strategy_name="")  # 전략명 없음

    final_qty, reason = await svc.adjust_buy_qty(signal, exchange=Exchange.KRX)

    assert "strategy_capital_cap" not in reason


async def test_strategy_cap_uses_default_limit_when_strategy_not_in_limits():
    """strategy_limits에 없는 전략명이면 default_strategy_limit 사용."""
    snapshot = _make_snapshot(total_equity=10_000_000, available_cash=5_000_000)
    # default_strategy_limit에 capital_allocation_pct 없음 → 캡 미동작
    risk_cfg = RiskGateConfig()

    svc = PositionSizingService(
        account_snapshot_cache=_make_cache(snapshot),
        indicator_service=_make_indicator_svc(),
        config=_base_cfg(per_trade_risk_pct=100.0),
        risk_gate_config=risk_cfg,
    )
    signal = _make_signal(price=10_000, qty=200, strategy_name="unknown_strategy")

    final_qty, reason = await svc.adjust_buy_qty(signal, exchange=Exchange.KRX)

    assert "strategy_capital_cap" not in reason
