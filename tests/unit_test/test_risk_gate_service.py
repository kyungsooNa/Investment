# tests/unit_test/test_risk_gate_service.py
"""RiskGateService 단위 테스트 — 전략별 자본 캡 (capital_allocation_pct) 중심."""
import pytest
from dataclasses import dataclass
from typing import Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

from core.account_snapshot import AccountSnapshot, AccountSnapshotCache
from config.config_loader import RiskGateConfig, RiskGateStrategyLimitConfig
from common.types import ErrorCode, Exchange, OrderSide, ResCommonResponse
from services.risk_gate_service import RiskGateService, StrategyRiskDataProvider


# ── 헬퍼 ──────────────────────────────────────────────────────────────────────


def _make_snapshot(total_equity: int, available_cash: int = 0) -> AccountSnapshot:
    return AccountSnapshot(
        total_equity=total_equity,
        available_cash=available_cash,
        positions={},
    )


def _make_cache(snapshot: AccountSnapshot) -> AccountSnapshotCache:
    cache = MagicMock(spec=AccountSnapshotCache)
    cache.get = AsyncMock(return_value=snapshot)
    return cache


def _make_provider(
    is_holding: bool = False,
    holds: List[dict] | None = None,
    return_history: List[dict] | None = None,
) -> StrategyRiskDataProvider:
    provider = MagicMock(spec=StrategyRiskDataProvider)
    provider.is_holding = MagicMock(return_value=is_holding)
    provider.get_holds_by_strategy = MagicMock(return_value=holds or [])
    provider.get_strategy_return_history = MagicMock(return_value=return_history or [])
    return provider


def _make_kill_switch(allowed: bool = True) -> MagicMock:
    ks = MagicMock()
    ks.check_orders_allowed = AsyncMock(return_value=(allowed, "ok" if allowed else "tripped"))
    ks.is_strategy_tripped = MagicMock(return_value=None)  # 기본: 전략 KS 미트립
    return ks


def _make_svc(
    config: RiskGateConfig | None = None,
    total_equity: int = 10_000_000,
    provider_holds: List[dict] | None = None,
    kill_switch_allowed: bool = True,
) -> RiskGateService:
    snapshot = _make_snapshot(total_equity=total_equity)
    cfg = config or RiskGateConfig()
    return RiskGateService(
        config=cfg,
        kill_switch_service=_make_kill_switch(kill_switch_allowed),
        account_snapshot_cache=_make_cache(snapshot),
        strategy_risk_provider=_make_provider(holds=provider_holds),
    )


async def _validate(svc: RiskGateService, price: int, qty: int, strategy_name: str = "test") -> ResCommonResponse | None:
    return await svc.validate_order(
        stock_code="000001",
        price=price,
        qty=qty,
        side=OrderSide.BUY,
        exchange=Exchange.KRX,
        active_order_count=0,
        strategy_name=strategy_name,
    )


# ── 전략별 자본 캡 테스트 ─────────────────────────────────────────────────────


async def test_strategy_capital_cap_blocks_when_over_allocation():
    """현재 보유 + 주문 금액이 capital_allocation_pct × total_equity 초과 시 차단."""
    # total_equity=10M, capital_allocation_pct=10% → budget=1M
    # 현재 보유 없음, 주문 2M → 차단
    cfg = RiskGateConfig(
        strategy_limits={
            "test": RiskGateStrategyLimitConfig(capital_allocation_pct=10.0)
        }
    )
    svc = _make_svc(config=cfg, total_equity=10_000_000, provider_holds=[])

    result = await _validate(svc, price=10_000, qty=200, strategy_name="test")  # 200 * 10,000 = 2,000,000

    assert result is not None
    assert result.rt_cd == ErrorCode.RISK_GATE_BLOCKED.value
    assert "capital_allocation_cap" in (result.data or {}).get("rule", "")


async def test_strategy_capital_cap_allows_within_budget():
    """현재 보유 + 주문 금액이 capital_allocation_pct × total_equity 이내면 허용."""
    # total_equity=10M, capital_allocation_pct=30% → budget=3M
    # 주문 1M → 허용
    cfg = RiskGateConfig(
        strategy_limits={
            "test": RiskGateStrategyLimitConfig(capital_allocation_pct=30.0)
        }
    )
    svc = _make_svc(config=cfg, total_equity=10_000_000, provider_holds=[])

    result = await _validate(svc, price=10_000, qty=100, strategy_name="test")  # 1,000,000 < 3,000,000

    assert result is None


async def test_strategy_capital_cap_accounts_for_existing_holdings():
    """현재 보유 금액이 이미 있으면 그 차이만큼만 추가 허용한다."""
    # total_equity=10M, capital_allocation_pct=20% → budget=2M
    # 현재 보유 1.5M → 남은 한도 0.5M. 주문 0.6M → 차단
    cfg = RiskGateConfig(
        strategy_limits={
            "test": RiskGateStrategyLimitConfig(capital_allocation_pct=20.0)
        }
    )
    existing_hold = {"buy_price": 15_000, "qty": 100}  # 1.5M
    svc = _make_svc(config=cfg, total_equity=10_000_000, provider_holds=[existing_hold])

    result = await _validate(svc, price=6_000, qty=100, strategy_name="test")  # 0.6M > 0.5M

    assert result is not None
    assert "capital_allocation_cap" in (result.data or {}).get("rule", "")


async def test_strategy_capital_cap_skipped_when_not_configured():
    """capital_allocation_pct 설정 없으면 캡 검증을 건너뛴다."""
    cfg = RiskGateConfig(
        strategy_limits={
            "test": RiskGateStrategyLimitConfig()  # capital_allocation_pct=None
        }
    )
    svc = _make_svc(config=cfg, total_equity=10_000_000, provider_holds=[])

    result = await _validate(svc, price=10_000, qty=500, strategy_name="test")

    # capital_allocation_cap 으로 차단되지 않아야 함 (max_order_amount 등 다른 룰은 제외)
    if result is not None:
        assert (result.data or {}).get("rule") != "capital_allocation_cap"


async def test_strategy_capital_cap_skipped_when_no_strategy_name():
    """strategy_name 없으면 캡 검증을 건너뛴다."""
    cfg = RiskGateConfig(
        strategy_limits={
            "test": RiskGateStrategyLimitConfig(capital_allocation_pct=1.0)  # 매우 작은 한도
        }
    )
    svc = _make_svc(config=cfg, total_equity=10_000_000, provider_holds=[])

    # strategy_name="" → source도 없음 → strategy_name 추출 불가
    result = await svc.validate_order(
        stock_code="000001",
        price=10_000,
        qty=1000,
        side=OrderSide.BUY,
        exchange=Exchange.KRX,
        active_order_count=0,
        strategy_name=None,
        source="",
    )

    if result is not None:
        assert (result.data or {}).get("rule") != "capital_allocation_cap"
