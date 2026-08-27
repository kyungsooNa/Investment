"""해외 주문 리스크 게이트 테스트 (P0-3).

국내 RiskGateService 는 원화·국내 Exchange·국내 AccountSnapshotCache 에 결합돼 있어
해외에 그대로 못 쓴다. USD 주문금액을 환율로 원화 환산해 **같은 RiskGateConfig 한도**
(canary overlay 포함)를 적용하는 해외 전용 게이트.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from common.types import ErrorCode
from config.config_loader import RiskGateConfig
from services.overseas_risk_gate_service import OverseasRiskGateService


def _gate(*, profile="canary", is_real=True, fx=1300.0, cfg=None, kill_switch=None):
    fx_provider = AsyncMock(return_value=fx)
    return OverseasRiskGateService(
        config=cfg or RiskGateConfig(),
        fx_provider=fx_provider,
        operating_profile=profile,
        is_real_mode_provider=lambda: is_real,
        kill_switch=kill_switch,
        logger=MagicMock(),
    ), fx_provider


async def _buy(gate, *, price=100.0, qty=5, open_positions=0):
    return await gate.validate_order(
        symbol="AAA", side="buy", qty=qty, limit_price_usd=price,
        open_position_count=open_positions, strategy_name="CB_overseas",
    )


# ── 주문 금액 한도 (환산) ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_usd_notional_is_converted_to_krw_before_limit_check():
    """USD 를 그대로 원화 한도와 비교하면 게이트가 사실상 무력화된다."""
    gate, fx = _gate(fx=1300.0)
    # $100 x 10 = $1,000 → 1,300,000원 > canary 한도 1,000,000원
    blocked = await _buy(gate, price=100.0, qty=10)

    assert blocked is not None
    assert blocked.rt_cd == ErrorCode.RISK_GATE_BLOCKED.value
    fx.assert_awaited()


@pytest.mark.asyncio
async def test_order_within_canary_limit_passes():
    gate, _ = _gate(fx=1300.0)
    # $50 x 10 = $500 → 650,000원 < 1,000,000원
    assert await _buy(gate, price=50.0, qty=10) is None


@pytest.mark.asyncio
async def test_canary_profile_uses_tighter_limit_than_base():
    """canary 한도 1M vs base 2M — 프로파일에 따라 결과가 갈려야 한다."""
    # 1,500,000원 상당 주문
    canary, _ = _gate(profile="canary", fx=1500.0)
    assert await canary.validate_order(
        symbol="AAA", side="buy", qty=10, limit_price_usd=100.0,
        open_position_count=0,
    ) is not None

    full, _ = _gate(profile="real_full", fx=1500.0)
    assert await full.validate_order(
        symbol="AAA", side="buy", qty=10, limit_price_usd=100.0,
        open_position_count=0,
    ) is None


@pytest.mark.asyncio
async def test_paper_mode_uses_base_limits():
    """모의 모드는 canary overlay 를 적용하지 않는다(국내와 동일)."""
    gate, _ = _gate(profile="canary", is_real=False, fx=1500.0)
    assert await _buy(gate, price=100.0, qty=10) is None  # 1.5M < base 2M


# ── 동시 보유 한도 ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_max_pending_orders_blocks_new_entry():
    """canary 동시 보유 2종."""
    gate, _ = _gate()
    assert await _buy(gate, open_positions=1) is None
    blocked = await _buy(gate, open_positions=2)
    assert blocked is not None
    assert "보유" in blocked.msg1 or "한도" in blocked.msg1


# ── 일일 누적 한도 ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_daily_cumulative_amount_is_capped():
    cfg = RiskGateConfig(max_daily_order_amount_won=2_000_000)
    gate, _ = _gate(cfg=cfg, fx=1000.0)

    # 900,000원 주문 2건은 통과, 3건째에서 누적 2.7M > 2M
    assert await _buy(gate, price=90.0, qty=10) is None
    gate.record_filled(notional_krw=900_000, trade_date="20260818")
    assert await _buy(gate, price=90.0, qty=10) is None
    gate.record_filled(notional_krw=900_000, trade_date="20260818")

    blocked = await _buy(gate, price=90.0, qty=10)
    assert blocked is not None


@pytest.mark.asyncio
async def test_daily_cumulative_resets_on_new_date():
    cfg = RiskGateConfig(max_daily_order_amount_won=2_000_000)
    gate, _ = _gate(cfg=cfg, fx=1000.0)
    gate.record_filled(notional_krw=1_900_000, trade_date="20260818")

    gate.record_filled(notional_krw=0, trade_date="20260819")  # 날짜 전환
    assert await _buy(gate, price=90.0, qty=10) is None


# ── 청산은 막지 않는다 ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_sell_is_never_blocked():
    """청산을 막으면 포지션이 갇혀 손실이 커진다 — 리스크를 줄이는 주문은 통과."""
    gate, _ = _gate()
    sell = await gate.validate_order(
        symbol="AAA", side="sell", qty=1000, limit_price_usd=9999.0,
        open_position_count=99,
    )
    assert sell is None


# ── FX / kill switch fail-closed ────────────────────────────────────────

@pytest.mark.asyncio
async def test_missing_fx_rate_is_fail_closed():
    """환율을 모르면 한도 검증이 불가능하다 — 통과시키면 게이트가 없는 것과 같다."""
    gate, _ = _gate(fx=None)
    blocked = await _buy(gate)
    assert blocked is not None
    assert "환율" in blocked.msg1


@pytest.mark.asyncio
async def test_fx_provider_failure_is_fail_closed():
    gate, fx = _gate()
    fx.side_effect = RuntimeError("조회 실패")
    assert await _buy(gate) is not None


@pytest.mark.asyncio
async def test_kill_switch_blocks_buy():
    ks = MagicMock()
    ks.check_orders_allowed = AsyncMock(return_value=(False, "연속 API 오류"))
    gate, _ = _gate(kill_switch=ks)

    blocked = await _buy(gate)
    assert blocked is not None
    assert blocked.rt_cd == ErrorCode.KILL_SWITCH_BLOCKED.value


@pytest.mark.asyncio
async def test_kill_switch_does_not_block_sell():
    ks = MagicMock()
    ks.check_orders_allowed = AsyncMock(return_value=(False, "연속 API 오류"))
    gate, _ = _gate(kill_switch=ks)

    assert await gate.validate_order(
        symbol="AAA", side="sell", qty=1, limit_price_usd=100.0, open_position_count=1,
    ) is None


@pytest.mark.asyncio
async def test_disabled_gate_passes_everything():
    gate, _ = _gate(cfg=RiskGateConfig(enabled=False), fx=None)
    assert await _buy(gate, price=99999.0, qty=999) is None


@pytest.mark.asyncio
async def test_invalid_quantity_or_price_is_blocked():
    gate, _ = _gate()
    assert await _buy(gate, qty=0) is not None
    assert await _buy(gate, price=0.0) is not None
