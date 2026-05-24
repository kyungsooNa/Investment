# tests/unit_test/test_kill_switch_service.py
"""KillSwitchService 단위 테스트 — 전략별 Kill Switch 중심."""
import pytest
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from config.config_loader import KillSwitchConfig, RiskGateConfig, RiskGateStrategyLimitConfig
from services.kill_switch_service import KillSwitchService


# ── 픽스처 헬퍼 ────────────────────────────────────────────────────────────────


def _make_notif() -> MagicMock:
    notif = MagicMock()
    notif.emit = AsyncMock()
    return notif


def _make_ks(
    tmp_path: Path,
    risk_gate_config: RiskGateConfig | None = None,
    ks_cfg_overrides: dict | None = None,
) -> KillSwitchService:
    cfg_kwargs = dict(
        enabled=True,
        daily_loss_threshold_won=5_000_000,
        daily_loss_threshold_pct=10.0,
        max_consecutive_losses=5,         # 계좌 KS 임계값은 높게 설정
        max_consecutive_api_errors=20,
        state_file_path=str(tmp_path / "ks_state.json"),
    )
    if ks_cfg_overrides:
        cfg_kwargs.update(ks_cfg_overrides)
    return KillSwitchService(
        config=KillSwitchConfig(**cfg_kwargs),
        notification_service=_make_notif(),
        risk_gate_config=risk_gate_config,
    )


# ── 전략별 Kill Switch: 기본 동작 ─────────────────────────────────────────────


async def test_strategy_not_tripped_initially(tmp_path):
    """초기 상태에서 전략 KS 는 트립되지 않는다."""
    ks = _make_ks(tmp_path)
    assert ks.is_strategy_tripped("test_strategy") is None


async def test_manual_trip_strategy(tmp_path):
    """trip_strategy() 호출 시 해당 전략이 차단된다."""
    ks = _make_ks(tmp_path)
    await ks.trip_strategy("my_strategy", "테스트 차단")

    info = ks.is_strategy_tripped("my_strategy")
    assert info is not None
    assert "my_strategy" in info.get("strategy_name", "")


async def test_strategy_kill_does_not_block_other_strategies(tmp_path):
    """한 전략이 트립되어도 다른 전략은 영향받지 않는다."""
    ks = _make_ks(tmp_path)
    await ks.trip_strategy("bad_strategy", "손실 초과")

    assert ks.is_strategy_tripped("bad_strategy") is not None
    assert ks.is_strategy_tripped("good_strategy") is None


async def test_reset_strategy_clears_trip(tmp_path):
    """reset_strategy() 가 해당 전략의 트립 상태를 해제한다."""
    ks = _make_ks(tmp_path)
    await ks.trip_strategy("bad_strategy", "테스트")
    assert ks.is_strategy_tripped("bad_strategy") is not None

    await ks.reset_strategy("bad_strategy", operator="admin")
    assert ks.is_strategy_tripped("bad_strategy") is None


# ── 전략 트립 side awareness (성과 저하 자동 차단 지원) ──────────────────────


async def test_trip_strategy_default_block_side_is_all(tmp_path):
    """block_side 미지정 시 기본값 'all' 이 metadata 에 저장된다 (backward compat)."""
    ks = _make_ks(tmp_path)
    await ks.trip_strategy("s", "사유")

    info = ks.is_strategy_tripped("s")
    assert info is not None
    assert info.get("block_side") == "all"


async def test_trip_strategy_block_side_buy_recorded(tmp_path):
    """block_side='buy' 가 trip metadata 에 보존된다."""
    ks = _make_ks(tmp_path)
    await ks.trip_strategy(
        "s",
        "strategy_perf:consecutive_losses",
        metadata={"alert_type": "strategy_degradation_candidate"},
        block_side="buy",
    )

    info = ks.is_strategy_tripped("s")
    assert info is not None
    assert info.get("block_side") == "buy"
    assert info.get("alert_type") == "strategy_degradation_candidate"


async def test_trip_strategy_invalid_block_side_falls_back_to_all(tmp_path):
    """알 수 없는 block_side 값은 'all' 로 보수 fallback."""
    ks = _make_ks(tmp_path)
    await ks.trip_strategy("s", "사유", block_side="invalid")  # type: ignore[arg-type]

    info = ks.is_strategy_tripped("s")
    assert info is not None
    assert info.get("block_side") == "all"


# ── 연속 손실 기반 자동 트립 ──────────────────────────────────────────────────


async def test_strategy_consecutive_loss_trips_only_that_strategy(tmp_path):
    """max_consecutive_losses_for_kill 초과 시 해당 전략만 트립된다."""
    risk_cfg = RiskGateConfig(
        strategy_limits={
            "fragile_strategy": RiskGateStrategyLimitConfig(max_consecutive_losses_for_kill=2)
        }
    )
    ks = _make_ks(tmp_path, risk_gate_config=risk_cfg)

    # fragile_strategy: 연속 손실 2회 → 트립
    await ks.record_strategy_trade_result("fragile_strategy", pnl_won=-10_000)
    await ks.record_strategy_trade_result("fragile_strategy", pnl_won=-10_000)

    assert ks.is_strategy_tripped("fragile_strategy") is not None

    # 다른 전략은 영향 없음
    assert ks.is_strategy_tripped("stable_strategy") is None

    # 계좌 KS 도 트립되지 않음 (계좌 KS 임계값 5회 이상이므로)
    allowed, _ = await ks.check_orders_allowed()
    assert allowed is True


async def test_strategy_consecutive_loss_resets_on_profit(tmp_path):
    """수익 거래가 발생하면 연속 손실 카운터가 초기화된다."""
    risk_cfg = RiskGateConfig(
        strategy_limits={
            "test": RiskGateStrategyLimitConfig(max_consecutive_losses_for_kill=3)
        }
    )
    ks = _make_ks(tmp_path, risk_gate_config=risk_cfg)

    await ks.record_strategy_trade_result("test", pnl_won=-5_000)
    await ks.record_strategy_trade_result("test", pnl_won=-5_000)
    await ks.record_strategy_trade_result("test", pnl_won=+10_000)  # 수익 → 리셋
    await ks.record_strategy_trade_result("test", pnl_won=-5_000)  # 1회 → 트립 안 됨

    assert ks.is_strategy_tripped("test") is None


# ── 일일 손실 기반 자동 트립 ──────────────────────────────────────────────────


async def test_strategy_daily_loss_trips_strategy(tmp_path):
    """daily_loss_won_for_kill 초과 시 전략이 트립된다."""
    risk_cfg = RiskGateConfig(
        strategy_limits={
            "test": RiskGateStrategyLimitConfig(daily_loss_won_for_kill=100_000)
        }
    )
    ks = _make_ks(tmp_path, risk_gate_config=risk_cfg)

    await ks.record_strategy_trade_result("test", pnl_won=-60_000)
    await ks.record_strategy_trade_result("test", pnl_won=-60_000)  # 누적 -120,000 > -100,000

    assert ks.is_strategy_tripped("test") is not None

    # 계좌 KS 는 유지
    allowed, _ = await ks.check_orders_allowed()
    assert allowed is True


async def test_reset_strategy_daily_counters(tmp_path):
    """reset_strategy_daily_counters() 가 모든 전략의 일별 손실 카운터를 초기화한다."""
    risk_cfg = RiskGateConfig(
        strategy_limits={
            "test": RiskGateStrategyLimitConfig(daily_loss_won_for_kill=100_000)
        }
    )
    ks = _make_ks(tmp_path, risk_gate_config=risk_cfg)

    await ks.record_strategy_trade_result("test", pnl_won=-60_000)
    # 아직 트립 안 된 상태에서 일별 카운터 초기화
    await ks.reset_strategy_daily_counters()

    await ks.record_strategy_trade_result("test", pnl_won=-60_000)  # 초기화 후 첫 손실 → 미트립

    assert ks.is_strategy_tripped("test") is None
