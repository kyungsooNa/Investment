from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from common.types import ErrorCode, Exchange, OrderSide
from config.config_loader import RiskGateConfig, RiskGateStrategyLimitConfig
from core.account_snapshot import AccountSnapshot
from services.risk_gate_service import RiskGateService


def _service(
    *,
    config: RiskGateConfig | None = None,
    kill_switch=None,
    snapshot: AccountSnapshot | None = None,
    strategy_provider=None,
    logger=None,
    env=None,
    market_buy_reference_price_provider=None,
    operating_profile: str = "real_limited",
):
    # NOTE: P0 0-7 — 기존 테스트 대다수가 real_mode_overrides (real_limited overlay)
    # 동작을 검증하므로 helper default 를 "real_limited" 로 둔다. canary 분기 테스트는
    # 별도 헬퍼 `_profile_service` 또는 명시적 operating_profile 인자를 사용한다.
    if kill_switch is None:
        kill_switch = AsyncMock()
        kill_switch.check_orders_allowed = AsyncMock(return_value=(True, None))
        kill_switch.is_strategy_tripped = MagicMock(return_value=None)  # 전략 KS 미트립
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
        strategy_risk_provider=strategy_provider,
        logger=logger or MagicMock(),
        env=env,
        market_buy_reference_price_provider=market_buy_reference_price_provider,
        operating_profile=operating_profile,
    ), kill_switch, cache


class _RealEnv:
    is_paper_trading = False
    _base_url = "https://openapi.koreainvestment.com:9443"
    active_config = {"stock_account_number": "12345678"}
    stock_account_number = "12345678"
    paper_stock_account_number = None


class _PaperEnv:
    is_paper_trading = True
    _base_url = "https://openapivts.koreainvestment.com:29443"
    active_config = {"stock_account_number": "98765432"}
    stock_account_number = None
    paper_stock_account_number = "98765432"


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
async def test_buy_order_blocks_when_price_is_below_invalidation_price():
    svc, _, _ = _service()

    result = await svc.validate_order(
        stock_code="005930",
        price=9_900,
        qty=1,
        side=OrderSide.BUY,
        exchange=Exchange.KRX,
        active_order_count=0,
        source="strategy:모멘텀",
        invalidation_price=10_000,
    )

    assert result is not None
    assert result.rt_cd == ErrorCode.RISK_GATE_BLOCKED.value
    assert result.data["rule"] == "signal_invalidated"


@pytest.mark.asyncio
async def test_buy_order_blocks_when_stop_loss_price_is_not_below_entry_price():
    svc, _, _ = _service()

    result = await svc.validate_order(
        stock_code="005930",
        price=10_000,
        qty=1,
        side=OrderSide.BUY,
        exchange=Exchange.KRX,
        active_order_count=0,
        source="strategy:모멘텀",
        stop_loss_price=10_100,
    )

    assert result is not None
    assert result.rt_cd == ErrorCode.RISK_GATE_BLOCKED.value
    assert result.data["rule"] == "invalid_stop_loss_price"


@pytest.mark.asyncio
async def test_kill_switch_blocks_with_compatible_error_code():
    kill_switch = AsyncMock()
    kill_switch.check_orders_allowed = AsyncMock(return_value=(False, "daily loss"))
    svc, _, _ = _service(kill_switch=kill_switch)

    result = await svc.validate_order("005930", 70_000, 10, OrderSide.BUY, Exchange.KRX, 0)

    assert result.rt_cd == ErrorCode.KILL_SWITCH_BLOCKED.value
    assert "daily loss" in result.msg1


@pytest.mark.asyncio
async def test_kill_switch_allows_force_exit_sell():
    kill_switch = AsyncMock()
    kill_switch.check_orders_allowed = AsyncMock(return_value=(False, "abnormal fill"))
    svc, _, _ = _service(kill_switch=kill_switch)

    result = await svc.validate_order(
        "005930",
        70_000,
        10,
        OrderSide.SELL,
        Exchange.KRX,
        0,
        source="strategy_force_exit:래리윌리엄스VBO",
    )

    assert result is None


@pytest.mark.asyncio
async def test_kill_switch_check_exception_fails_closed():
    kill_switch = AsyncMock()
    kill_switch.check_orders_allowed = AsyncMock(side_effect=RuntimeError("ks down"))
    svc, _, _ = _service(kill_switch=kill_switch)

    result = await svc.validate_order("005930", 70_000, 10, OrderSide.BUY, Exchange.KRX, 0)

    assert result.rt_cd == ErrorCode.RISK_GATE_BLOCKED.value
    assert "Kill Switch 확인 실패" in result.msg1


# === 전략 Kill Switch side-aware: 성과 저하 자동 차단 (block_side="buy") ===


def _ks_with_strategy_trip(*, block_side: str = "all", reason: str = "strategy_perf:test"):
    """전략 트립 mock 헬퍼 — block_side metadata 포함."""
    kill_switch = AsyncMock()
    kill_switch.check_orders_allowed = AsyncMock(return_value=(True, None))
    kill_switch.is_strategy_tripped = MagicMock(
        return_value={"trip_reason": reason, "block_side": block_side}
    )
    return kill_switch


@pytest.mark.asyncio
async def test_strategy_kill_switch_block_side_all_blocks_buy():
    """block_side='all' (기존 동작) — BUY 차단."""
    svc, _, _ = _service(kill_switch=_ks_with_strategy_trip(block_side="all"))

    result = await svc.validate_order(
        "005930", 70_000, 10, OrderSide.BUY, Exchange.KRX, 0, strategy_name="my_strategy"
    )

    assert result is not None
    assert result.rt_cd == ErrorCode.KILL_SWITCH_BLOCKED.value
    assert "전략 Kill Switch 차단" in result.msg1


@pytest.mark.asyncio
async def test_strategy_kill_switch_block_side_all_blocks_sell():
    """block_side='all' — normal SELL 도 차단 (force-exit 가 아닌 경우)."""
    svc, _, _ = _service(kill_switch=_ks_with_strategy_trip(block_side="all"))

    result = await svc.validate_order(
        "005930", 70_000, 10, OrderSide.SELL, Exchange.KRX, 0, strategy_name="my_strategy"
    )

    assert result is not None
    assert result.rt_cd == ErrorCode.KILL_SWITCH_BLOCKED.value


@pytest.mark.asyncio
async def test_strategy_kill_switch_block_side_buy_blocks_buy_only():
    """block_side='buy' — BUY 만 차단, normal SELL 은 통과시켜 graceful 청산 허용."""
    svc, _, _ = _service(kill_switch=_ks_with_strategy_trip(block_side="buy"))

    buy_result = await svc.validate_order(
        "005930", 70_000, 10, OrderSide.BUY, Exchange.KRX, 0, strategy_name="my_strategy"
    )
    sell_result = await svc.validate_order(
        "005930", 70_000, 10, OrderSide.SELL, Exchange.KRX, 0, strategy_name="my_strategy"
    )

    assert buy_result is not None
    assert buy_result.rt_cd == ErrorCode.KILL_SWITCH_BLOCKED.value
    assert sell_result is None  # SELL 통과


@pytest.mark.asyncio
async def test_strategy_kill_switch_missing_block_side_defaults_to_all():
    """legacy trip metadata 에 block_side 가 없으면 'all' 로 동작 (backward compat)."""
    kill_switch = AsyncMock()
    kill_switch.check_orders_allowed = AsyncMock(return_value=(True, None))
    kill_switch.is_strategy_tripped = MagicMock(
        return_value={"trip_reason": "legacy"}  # block_side 키 누락
    )
    svc, _, _ = _service(kill_switch=kill_switch)

    sell_result = await svc.validate_order(
        "005930", 70_000, 10, OrderSide.SELL, Exchange.KRX, 0, strategy_name="my_strategy"
    )

    assert sell_result is not None
    assert sell_result.rt_cd == ErrorCode.KILL_SWITCH_BLOCKED.value


@pytest.mark.asyncio
async def test_strategy_kill_switch_force_exit_sell_bypasses_block_all():
    """force-exit SELL 은 strategy KS 체크 자체를 건너뛴다 (block_side 무관)."""
    svc, _, _ = _service(kill_switch=_ks_with_strategy_trip(block_side="all"))

    result = await svc.validate_order(
        "005930",
        70_000,
        10,
        OrderSide.SELL,
        Exchange.KRX,
        0,
        strategy_name="my_strategy",
        source="strategy_force_exit:my_strategy",
    )

    assert result is None


@pytest.mark.asyncio
async def test_order_amount_over_limit_blocks_buy():
    svc, _, _ = _service(config=RiskGateConfig(max_order_amount_won=100_000))

    result = await svc.validate_order("005930", 70_000, 2, OrderSide.BUY, Exchange.KRX, 0)

    assert result.rt_cd == ErrorCode.RISK_GATE_BLOCKED.value
    assert "주문 금액 초과" in result.msg1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stock_code", "price", "qty", "strategy_name"),
    [
        ("033160", 33_200, 69, "오닐PP/BGU"),
        ("382800", 8_970, 236, "RSI2눌림목"),
    ],
)
async def test_strategy_sell_over_order_amount_limit_is_allowed(
    stock_code,
    price,
    qty,
    strategy_name,
):
    """전략 손절/익절 SELL 은 포지션 축소이므로 1회 주문 금액 한도에 막히면 안 된다."""
    svc, _, _ = _service(config=RiskGateConfig(max_order_amount_won=2_000_000))

    result = await svc.validate_order(
        stock_code,
        price,
        qty,
        OrderSide.SELL,
        Exchange.KRX,
        0,
        source=f"strategy:{strategy_name}",
        strategy_name=strategy_name,
    )

    assert result is None


@pytest.mark.asyncio
async def test_force_exit_sell_bypasses_order_amount_cap():
    svc, _, _ = _service(config=RiskGateConfig(max_order_amount_won=2_000_000))

    result = await svc.validate_order(
        "053610",
        96_500,
        21,
        OrderSide.SELL,
        Exchange.KRX,
        0,
        source="strategy_force_exit:래리윌리엄스VBO",
    )

    assert result is None


@pytest.mark.asyncio
async def test_negative_price_blocks():
    svc, _, _ = _service()

    result = await svc.validate_order("005930", -1, 10, OrderSide.BUY, Exchange.KRX, 0)

    assert result.rt_cd == ErrorCode.RISK_GATE_BLOCKED.value
    assert result.data["rule"] == "negative_price"


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
    assert buy_result.data["rule"] == "max_total_exposure"
    assert sell_result is None


@pytest.mark.asyncio
async def test_duplicate_strategy_position_blocks_same_strategy_only():
    provider = MagicMock()
    provider.is_holding.side_effect = lambda strategy, code: strategy == "모멘텀"
    svc, _, _ = _service(strategy_provider=provider)

    blocked = await svc.validate_order(
        "005930", 70_000, 10, OrderSide.BUY, Exchange.KRX, 0,
        source="strategy:모멘텀",
    )
    allowed = await svc.validate_order(
        "005930", 70_000, 10, OrderSide.BUY, Exchange.KRX, 0,
        source="strategy:눌림목",
    )

    assert blocked.rt_cd == ErrorCode.RISK_GATE_BLOCKED.value
    assert blocked.data["rule"] == "duplicate_strategy_position"
    assert blocked.data["strategy_name"] == "모멘텀"
    assert allowed is None


@pytest.mark.asyncio
async def test_strategy_exposure_limit_blocks():
    provider = MagicMock()
    provider.is_holding.return_value = False
    provider.get_strategy_return_history.return_value = []
    provider.get_holds_by_strategy.return_value = [
        {"code": "000660", "buy_price": 10_000, "qty": 50},
    ]
    cfg = RiskGateConfig(
        strategy_limits={
            "모멘텀": RiskGateStrategyLimitConfig(max_exposure_pct=1.0),
        },
    )
    svc, _, _ = _service(config=cfg, strategy_provider=provider)

    result = await svc.validate_order(
        "005930", 70_000, 10, OrderSide.BUY, Exchange.KRX, 0,
        source="strategy:모멘텀",
    )

    assert result.rt_cd == ErrorCode.RISK_GATE_BLOCKED.value
    assert result.data["rule"] == "strategy_exposure_limit"
    assert result.data["next_exposure_pct"] == 1.2


@pytest.mark.asyncio
async def test_strategy_loss_limit_blocks():
    provider = MagicMock()
    provider.is_holding.return_value = False
    provider.get_strategy_return_history.return_value = [
        {"date": "2026-04-24", "return_rate": -6.1},
    ]
    provider.get_holds_by_strategy.return_value = []
    cfg = RiskGateConfig(
        strategy_limits={
            "모멘텀": RiskGateStrategyLimitConfig(max_loss_pct=5.0),
        },
    )
    svc, _, _ = _service(config=cfg, strategy_provider=provider)

    result = await svc.validate_order(
        "005930", 70_000, 1, OrderSide.BUY, Exchange.KRX, 0,
        source="strategy:모멘텀",
    )

    assert result.rt_cd == ErrorCode.RISK_GATE_BLOCKED.value
    assert result.data["rule"] == "strategy_loss_limit"
    assert result.data["latest_return_pct"] == -6.1


@pytest.mark.asyncio
async def test_block_logs_structured_context():
    logger = MagicMock()
    svc, _, _ = _service(
        config=RiskGateConfig(max_order_amount_won=100_000),
        logger=logger,
    )

    result = await svc.validate_order("005930", 70_000, 2, OrderSide.BUY, Exchange.KRX, 0)

    assert result.data["rule"] == "max_order_amount"
    logger.warning.assert_called()
    assert "[RiskGate][BLOCK]" in logger.warning.call_args.args[0]


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


def _env(*, is_paper: bool, base_url: str, real_account="50000000-01", paper_account="50123456-01", active_account=None):
    env = MagicMock()
    env.is_paper_trading = is_paper
    env._base_url = base_url
    env.stock_account_number = real_account
    env.paper_stock_account_number = paper_account
    env.active_config = {
        "stock_account_number": active_account if active_account is not None else (paper_account if is_paper else real_account),
    }
    return env


@pytest.mark.asyncio
async def test_env_consistency_paper_with_real_url_blocks():
    env = _env(is_paper=True, base_url="https://openapi.koreainvestment.com:9443")
    svc, _, _ = _service()
    svc._env = env

    result = await svc.validate_order("005930", 70_000, 10, OrderSide.BUY, Exchange.KRX, 0)

    assert result is not None
    assert result.rt_cd == ErrorCode.RISK_GATE_BLOCKED.value
    assert result.data["rule"] == "env_mismatch_url"


@pytest.mark.asyncio
async def test_env_consistency_real_with_paper_url_blocks():
    env = _env(is_paper=False, base_url="https://openapivts.koreainvestment.com:29443")
    svc, _, _ = _service()
    svc._env = env

    result = await svc.validate_order("005930", 70_000, 10, OrderSide.BUY, Exchange.KRX, 0)

    assert result is not None
    assert result.data["rule"] == "env_mismatch_url"


@pytest.mark.asyncio
async def test_env_consistency_account_mismatch_blocks():
    env = _env(
        is_paper=False,
        base_url="https://openapi.koreainvestment.com:9443",
        active_account="50123456-01",  # paper account in real-mode
    )
    svc, _, _ = _service()
    svc._env = env

    result = await svc.validate_order("005930", 70_000, 10, OrderSide.BUY, Exchange.KRX, 0)

    assert result is not None
    assert result.data["rule"] == "env_mismatch_account"


@pytest.mark.asyncio
async def test_env_consistency_consistent_passes():
    env = _env(is_paper=False, base_url="https://openapi.koreainvestment.com:9443")
    svc, _, _ = _service()
    svc._env = env

    result = await svc.validate_order("005930", 70_000, 10, OrderSide.BUY, Exchange.KRX, 0)

    assert result is None


@pytest.mark.asyncio
async def test_daily_cap_blocks_when_accumulated_amount_exceeds_limit():
    cfg = RiskGateConfig(
        max_order_amount_won=100_000_000,
        max_daily_order_amount_won=2_000_000,
    )
    svc, _, _ = _service(config=cfg)

    # 1차: 1,000,000 (통과, 누적 1,000,000)
    r1 = await svc.validate_order("005930", 1_000, 1_000, OrderSide.SELL, Exchange.KRX, 0)
    assert r1 is None
    # 2차: 1,000,000 (통과, 누적 2,000,000)
    r2 = await svc.validate_order("005930", 1_000, 1_000, OrderSide.SELL, Exchange.KRX, 0)
    assert r2 is None
    # 3차: 1 (누적 2,000,001 > 2,000,000 → 차단)
    r3 = await svc.validate_order("005930", 1, 1, OrderSide.SELL, Exchange.KRX, 0)
    assert r3 is not None
    assert r3.data["rule"] == "max_daily_order_amount"


@pytest.mark.asyncio
async def test_force_exit_sell_bypasses_daily_order_amount_cap():
    cfg = RiskGateConfig(
        max_order_amount_won=100_000_000,
        max_daily_order_amount_won=1_000_000,
    )
    svc, _, _ = _service(config=cfg)

    result = await svc.validate_order(
        "053610",
        96_500,
        21,
        OrderSide.SELL,
        Exchange.KRX,
        0,
        source="strategy_force_exit:래리윌리엄스VBO",
    )

    assert result is None


@pytest.mark.asyncio
async def test_daily_cap_disabled_when_zero():
    cfg = RiskGateConfig(
        max_order_amount_won=100_000_000,
        max_daily_order_amount_won=0,
    )
    svc, _, _ = _service(config=cfg)

    for _ in range(5):
        r = await svc.validate_order("005930", 100_000, 100, OrderSide.SELL, Exchange.KRX, 0)
        assert r is None


@pytest.mark.asyncio
async def test_env_none_skips_consistency_check():
    """env 미주입 시 fail-open. 기존 테스트 회귀 방지."""
    svc, _, _ = _service()
    assert svc._env is None

    result = await svc.validate_order("005930", 70_000, 10, OrderSide.BUY, Exchange.KRX, 0)

    assert result is None


@pytest.mark.asyncio
async def test_missing_kill_switch_and_snapshot_cache_skip_optional_checks():
    svc = RiskGateService(
        config=RiskGateConfig(max_total_exposure_pct=1.0),
        kill_switch_service=None,
        account_snapshot_cache=None,
        logger=MagicMock(),
    )

    result = await svc.validate_order("005930", 70_000, 1, OrderSide.BUY, Exchange.KRX, 0)

    assert result is None


def test_record_daily_amount_prunes_stale_entries():
    svc, _, _ = _service(config=RiskGateConfig(max_daily_order_amount_won=10_000_000))
    stale_day = date.today() - timedelta(days=8)
    svc._daily_total[stale_day] = 1

    svc._record_daily_amount(100)

    assert stale_day not in svc._daily_total
    assert svc._daily_total[date.today()] == 100


@pytest.mark.asyncio
async def test_duplicate_strategy_position_async_and_error_paths():
    async_provider = MagicMock()
    async_provider.is_holding = AsyncMock(return_value=True)
    async_provider.get_strategy_return_history.return_value = []
    async_provider.get_holds_by_strategy.return_value = []
    async_svc, _, _ = _service(strategy_provider=async_provider)

    error_provider = MagicMock()
    error_provider.is_holding.side_effect = RuntimeError("hold check down")
    error_svc, _, _ = _service(strategy_provider=error_provider)

    blocked = await async_svc.validate_order(
        "005930", 70_000, 1, OrderSide.BUY, Exchange.KRX, 0, source="strategy:모멘텀"
    )
    allowed = await error_svc.validate_order(
        "005930", 70_000, 1, OrderSide.BUY, Exchange.KRX, 0, source="strategy:모멘텀"
    )

    assert blocked.data["rule"] == "duplicate_strategy_position"
    assert allowed is None


@pytest.mark.asyncio
async def test_strategy_loss_limit_async_empty_exception_and_non_blocking_loss():
    cfg = RiskGateConfig(
        strategy_limits={
            "모멘텀": RiskGateStrategyLimitConfig(max_loss_pct=5.0),
        },
    )
    empty_provider = MagicMock()
    empty_provider.is_holding.return_value = False
    empty_provider.get_strategy_return_history = AsyncMock(return_value=[])
    empty_provider.get_holds_by_strategy.return_value = []
    empty_svc, _, _ = _service(config=cfg, strategy_provider=empty_provider)

    error_provider = MagicMock()
    error_provider.is_holding.return_value = False
    error_provider.get_strategy_return_history.side_effect = RuntimeError("history down")
    error_svc, _, _ = _service(config=cfg, strategy_provider=error_provider)

    ok_provider = MagicMock()
    ok_provider.is_holding.return_value = False
    ok_provider.get_strategy_return_history.return_value = [{"date": "2026-04-30", "return_rate": -1.5}]
    ok_provider.get_holds_by_strategy.return_value = []
    ok_svc, _, _ = _service(config=cfg, strategy_provider=ok_provider)

    assert await empty_svc.validate_order(
        "005930", 70_000, 1, OrderSide.BUY, Exchange.KRX, 0, source="strategy:모멘텀"
    ) is None
    assert await error_svc.validate_order(
        "005930", 70_000, 1, OrderSide.BUY, Exchange.KRX, 0, source="strategy:모멘텀"
    ) is None
    assert await ok_svc.validate_order(
        "005930", 70_000, 1, OrderSide.BUY, Exchange.KRX, 0, source="strategy:모멘텀"
    ) is None


@pytest.mark.asyncio
async def test_strategy_exposure_limit_skip_and_async_holds_paths():
    cfg = RiskGateConfig(
        strategy_limits={
            "모멘텀": RiskGateStrategyLimitConfig(max_exposure_pct=10.0),
        },
    )
    provider = MagicMock()
    provider.is_holding.return_value = False
    provider.get_strategy_return_history.return_value = []
    provider.get_holds_by_strategy = AsyncMock(return_value=[{"current_value": "1,000,000"}])
    svc, _, _ = _service(config=cfg, strategy_provider=provider)

    result = await svc.validate_order(
        "005930", 70_000, 1, OrderSide.BUY, Exchange.KRX, 0, source="strategy:모멘텀"
    )

    assert result is None
    assert RiskGateService._position_value({"evlu_amt": "1,234"}) == 1234


@pytest.mark.asyncio
async def test_strategy_exposure_limit_fails_open_on_zero_equity_and_hold_error():
    cfg = RiskGateConfig(
        strategy_limits={
            "모멘텀": RiskGateStrategyLimitConfig(max_exposure_pct=10.0),
        },
    )
    zero_provider = MagicMock()
    zero_provider.is_holding.return_value = False
    zero_provider.get_strategy_return_history.return_value = []
    zero_provider.get_holds_by_strategy.return_value = [{"buy_price": 10_000, "qty": 1}]
    zero_svc, _, _ = _service(
        config=cfg,
        snapshot=AccountSnapshot(total_equity=0, available_cash=0, positions={}),
        strategy_provider=zero_provider,
    )

    error_provider = MagicMock()
    error_provider.is_holding.return_value = False
    error_provider.get_strategy_return_history.return_value = []
    error_provider.get_holds_by_strategy.side_effect = RuntimeError("holds down")
    error_svc, _, _ = _service(config=cfg, strategy_provider=error_provider)

    assert await zero_svc.validate_order(
        "005930", 70_000, 1, OrderSide.BUY, Exchange.KRX, 0, source="strategy:모멘텀"
    ) is None
    assert await error_svc.validate_order(
        "005930", 70_000, 1, OrderSide.BUY, Exchange.KRX, 0, source="strategy:모멘텀"
    ) is None


# --- 시장가 매수(price==0) RiskGate 우회 차단 ---

@pytest.mark.asyncio
async def test_market_buy_blocked_in_real_mode_without_reference_price_provider():
    """실전 모드 + price=0 + BUY: reference_price provider 미주입 시 차단."""
    svc, _, _ = _service(env=_RealEnv())

    result = await svc.validate_order("005930", 0, 10, OrderSide.BUY, Exchange.KRX, 0)

    assert result is not None
    assert result.rt_cd == ErrorCode.RISK_GATE_BLOCKED.value
    assert result.data["rule"] == "market_buy_no_reference_price"


@pytest.mark.asyncio
async def test_market_buy_blocked_in_real_mode_when_reference_price_returns_none():
    """실전 모드 + price=0 + BUY: provider가 None 반환 시 차단."""
    provider = AsyncMock(return_value=None)
    svc, _, _ = _service(env=_RealEnv(), market_buy_reference_price_provider=provider)

    result = await svc.validate_order("005930", 0, 10, OrderSide.BUY, Exchange.KRX, 0)

    assert result is not None
    assert result.data["rule"] == "market_buy_no_reference_price"


@pytest.mark.asyncio
async def test_market_buy_in_real_mode_amount_limit_enforced_via_reference_price():
    """실전 모드 + price=0 + BUY: reference_price * qty가 한도 초과면 차단."""
    provider = AsyncMock(return_value=1_000_000)  # 1M * 10 = 10M > 2M default
    svc, _, _ = _service(env=_RealEnv(), market_buy_reference_price_provider=provider)

    result = await svc.validate_order("005930", 0, 10, OrderSide.BUY, Exchange.KRX, 0)

    assert result is not None
    assert result.data["rule"] == "max_order_amount"


@pytest.mark.asyncio
async def test_market_buy_allowed_in_real_mode_when_reference_price_within_limits():
    """실전 모드 + price=0 + BUY: 한도 내면 통과."""
    provider = AsyncMock(return_value=70_000)
    svc, _, _ = _service(env=_RealEnv(), market_buy_reference_price_provider=provider)

    result = await svc.validate_order("005930", 0, 10, OrderSide.BUY, Exchange.KRX, 0)

    assert result is None


@pytest.mark.asyncio
async def test_market_buy_allowed_in_paper_mode_without_reference_price():
    """Paper 모드: 기존 fail-open 동작 유지 (price=0 BUY 통과)."""
    svc, _, _ = _service(env=_PaperEnv())

    result = await svc.validate_order("005930", 0, 10, OrderSide.BUY, Exchange.KRX, 0)

    assert result is None


@pytest.mark.asyncio
async def test_market_buy_allowed_when_env_not_injected():
    """env=None: 기존 fail-open 유지 (회귀 방지)."""
    svc, _, _ = _service()  # env=None

    result = await svc.validate_order("005930", 0, 10, OrderSide.BUY, Exchange.KRX, 0)

    assert result is None


@pytest.mark.asyncio
async def test_market_sell_in_real_mode_still_passes_without_reference_price():
    """SELL 시장가는 청산 우선 정책으로 기존대로 통과."""
    svc, _, _ = _service(env=_RealEnv())

    result = await svc.validate_order("005930", 0, 10, OrderSide.SELL, Exchange.KRX, 0)

    assert result is None


@pytest.mark.asyncio
async def test_market_buy_uses_reference_price_for_daily_cap_check():
    """실전 모드 + price=0 + BUY: 일일 누적 한도 검증에도 reference_price가 사용된다."""
    provider = AsyncMock(return_value=50_000)
    cfg = RiskGateConfig(
        max_order_amount_won=10_000_000,
        max_daily_order_amount_won=400_000,  # 50_000 * 10 = 500_000 > 400_000
    )
    svc, _, _ = _service(
        config=cfg, env=_RealEnv(), market_buy_reference_price_provider=provider
    )

    result = await svc.validate_order("005930", 0, 10, OrderSide.BUY, Exchange.KRX, 0)

    assert result is not None
    assert result.data["rule"] == "max_daily_order_amount"


# --- 실전 모드 fail-close 정책 ---


@pytest.mark.asyncio
async def test_real_mode_fail_closes_when_snapshot_cache_missing():
    """실전 모드 BUY: account snapshot cache 미주입 시 차단."""
    svc = RiskGateService(
        config=RiskGateConfig(),
        kill_switch_service=None,
        account_snapshot_cache=None,
        strategy_risk_provider=None,
        logger=MagicMock(),
        env=_RealEnv(),
    )

    result = await svc.validate_order(
        "005930", 70_000, 10, OrderSide.BUY, Exchange.KRX, 0
    )

    assert result is not None
    assert result.rt_cd == ErrorCode.RISK_GATE_BLOCKED.value
    assert result.data["rule"] == "fail_close_no_snapshot_cache"


@pytest.mark.asyncio
async def test_real_mode_fail_closes_on_zero_equity_buy_exposure():
    """실전 모드 BUY: total_equity<=0 시 노출 검증 차단."""
    snapshot = AccountSnapshot(total_equity=0, available_cash=0, positions={})
    svc, _, _ = _service(env=_RealEnv(), snapshot=snapshot)

    result = await svc.validate_order(
        "005930", 70_000, 10, OrderSide.BUY, Exchange.KRX, 0
    )

    assert result is not None
    assert result.data["rule"] == "fail_close_zero_equity"


@pytest.mark.asyncio
async def test_real_mode_sell_not_blocked_by_fail_close_on_zero_equity():
    """실전 모드 SELL: total_equity<=0 이어도 차단되지 않는다 (강제 청산 보존)."""
    snapshot = AccountSnapshot(total_equity=0, available_cash=0, positions={})
    svc, _, _ = _service(env=_RealEnv(), snapshot=snapshot)

    result = await svc.validate_order(
        "005930", 70_000, 10, OrderSide.SELL, Exchange.KRX, 0
    )

    assert result is None


@pytest.mark.asyncio
async def test_paper_mode_preserves_fail_open_on_zero_equity():
    """paper 모드: total_equity<=0 시 기존 fail-open 동작 유지."""
    snapshot = AccountSnapshot(total_equity=0, available_cash=0, positions={})
    svc, _, _ = _service(env=_PaperEnv(), snapshot=snapshot)

    result = await svc.validate_order(
        "005930", 70_000, 10, OrderSide.BUY, Exchange.KRX, 0
    )

    assert result is None


@pytest.mark.asyncio
async def test_env_none_preserves_fail_open_on_zero_equity():
    """env=None: 기존 fail-open 동작 유지 (회귀 방지)."""
    snapshot = AccountSnapshot(total_equity=0, available_cash=0, positions={})
    svc, _, _ = _service(snapshot=snapshot)

    result = await svc.validate_order(
        "005930", 70_000, 10, OrderSide.BUY, Exchange.KRX, 0
    )

    assert result is None


@pytest.mark.asyncio
async def test_real_mode_fail_closes_on_strategy_exposure_provider_error():
    """실전 모드 BUY: strategy_risk_provider holds 조회 예외 시 차단."""
    cfg = RiskGateConfig(
        strategy_limits={
            "모멘텀": RiskGateStrategyLimitConfig(max_exposure_pct=10.0),
        },
    )
    provider = MagicMock()
    provider.is_holding.return_value = False
    provider.get_strategy_return_history.return_value = []
    provider.get_holds_by_strategy.side_effect = RuntimeError("holds down")
    svc, _, _ = _service(env=_RealEnv(), config=cfg, strategy_provider=provider)

    result = await svc.validate_order(
        "005930", 70_000, 1, OrderSide.BUY, Exchange.KRX, 0, source="strategy:모멘텀"
    )

    assert result is not None
    assert result.data["rule"] == "fail_close_strategy_exposure_provider_error"


@pytest.mark.asyncio
async def test_real_mode_fail_closes_on_strategy_capital_cap_provider_error():
    """실전 모드 BUY: capital_allocation_pct 검증 시 holds 예외 → 차단."""
    cfg = RiskGateConfig(
        strategy_limits={
            "모멘텀": RiskGateStrategyLimitConfig(capital_allocation_pct=20.0),
        },
    )
    provider = MagicMock()
    provider.is_holding.return_value = False
    provider.get_strategy_return_history.return_value = []
    provider.get_holds_by_strategy.side_effect = RuntimeError("holds down")
    svc, _, _ = _service(env=_RealEnv(), config=cfg, strategy_provider=provider)

    result = await svc.validate_order(
        "005930", 70_000, 1, OrderSide.BUY, Exchange.KRX, 0, source="strategy:모멘텀"
    )

    assert result is not None
    assert result.data["rule"] == "fail_close_strategy_capital_cap_provider_error"


@pytest.mark.asyncio
async def test_real_mode_fail_closes_on_strategy_loss_limit_provider_error():
    """실전 모드 BUY: max_loss_pct 검증 시 history 예외 → 차단."""
    cfg = RiskGateConfig(
        strategy_limits={
            "모멘텀": RiskGateStrategyLimitConfig(max_loss_pct=5.0),
        },
    )
    provider = MagicMock()
    provider.is_holding.return_value = False
    provider.get_strategy_return_history.side_effect = RuntimeError("history down")
    provider.get_holds_by_strategy.return_value = []
    svc, _, _ = _service(env=_RealEnv(), config=cfg, strategy_provider=provider)

    result = await svc.validate_order(
        "005930", 70_000, 1, OrderSide.BUY, Exchange.KRX, 0, source="strategy:모멘텀"
    )

    assert result is not None
    assert result.data["rule"] == "fail_close_strategy_loss_provider_error"


@pytest.mark.asyncio
async def test_real_mode_fail_closes_on_duplicate_strategy_provider_error():
    """실전 모드 BUY: is_holding 예외 → 차단."""
    cfg = RiskGateConfig()
    provider = MagicMock()
    provider.is_holding.side_effect = RuntimeError("dup down")
    provider.get_strategy_return_history.return_value = []
    provider.get_holds_by_strategy.return_value = []
    svc, _, _ = _service(env=_RealEnv(), config=cfg, strategy_provider=provider)

    result = await svc.validate_order(
        "005930", 70_000, 1, OrderSide.BUY, Exchange.KRX, 0, source="strategy:모멘텀"
    )

    assert result is not None
    assert result.data["rule"] == "fail_close_duplicate_strategy_provider_error"


@pytest.mark.asyncio
async def test_real_mode_fail_close_can_be_opted_out_via_config():
    """fail_open_allowed.real=True 로 설정하면 실전 모드도 기존 fail-open 유지."""
    from config.config_loader import RiskGateFailOpenConfig

    cfg = RiskGateConfig(
        fail_open_allowed=RiskGateFailOpenConfig(paper=True, real=True),
    )
    snapshot = AccountSnapshot(total_equity=0, available_cash=0, positions={})
    svc, _, _ = _service(env=_RealEnv(), config=cfg, snapshot=snapshot)

    result = await svc.validate_order(
        "005930", 70_000, 10, OrderSide.BUY, Exchange.KRX, 0
    )

    assert result is None


# ── P0 0-2: RiskGate real_mode_overrides 분기 ─────────────────────────

class _PaperEnv:
    is_paper_trading = True
    _base_url = "https://openapivts.koreainvestment.com:29443"
    active_config = {"stock_account_number": "98765432"}
    stock_account_number = None
    paper_stock_account_number = "98765432"


def test_risk_gate_effective_max_pending_orders_paper_uses_top_level():
    cfg = RiskGateConfig(max_pending_orders=10)
    svc, _, _ = _service(config=cfg, env=_PaperEnv())
    assert svc._effective_max_pending_orders() == 10


def test_risk_gate_effective_max_pending_orders_real_uses_override_default():
    cfg = RiskGateConfig(max_pending_orders=10)  # paper top-level
    svc, _, _ = _service(config=cfg, env=_RealEnv())
    # default canary overlay = 5
    assert svc._effective_max_pending_orders() == 5


def test_risk_gate_effective_max_total_exposure_paper_uses_top_level():
    cfg = RiskGateConfig(max_total_exposure_pct=95.0)
    svc, _, _ = _service(config=cfg, env=_PaperEnv())
    assert svc._effective_max_total_exposure_pct() == 95.0


def test_risk_gate_effective_max_total_exposure_real_uses_override_default():
    cfg = RiskGateConfig(max_total_exposure_pct=95.0)
    svc, _, _ = _service(config=cfg, env=_RealEnv())
    assert svc._effective_max_total_exposure_pct() == 30.0


def test_risk_gate_real_mode_overrides_user_yaml():
    cfg = RiskGateConfig(
        max_pending_orders=10,
        max_total_exposure_pct=95.0,
        real_mode_overrides={"max_total_exposure_pct": 20.0, "max_pending_orders": 3},
    )
    svc, _, _ = _service(config=cfg, env=_RealEnv())
    assert svc._effective_max_pending_orders() == 3
    assert svc._effective_max_total_exposure_pct() == 20.0


@pytest.mark.asyncio
async def test_risk_gate_real_mode_blocks_exposure_under_canary_threshold():
    """paper 에서 통과하던 50% 노출이 real canary overlay 30% 에서는 차단되어야 한다."""
    cfg = RiskGateConfig(max_total_exposure_pct=95.0)
    # 50M positions on 100M equity → 50% exposure
    snapshot = AccountSnapshot(
        total_equity=100_000_000,
        available_cash=50_000_000,
        positions={"000660": 50_000_000},
    )
    # paper: passes (50% < 95%)
    paper_svc, _, _ = _service(config=cfg, env=_PaperEnv(), snapshot=snapshot)
    paper_result = await paper_svc.validate_order(
        "005930", 70_000, 1, OrderSide.BUY, Exchange.KRX, 0
    )
    assert paper_result is None  # not blocked

    # real: blocked (50%+ > 30% canary overlay)
    real_svc, _, _ = _service(config=cfg, env=_RealEnv(), snapshot=snapshot)
    real_result = await real_svc.validate_order(
        "005930", 70_000, 1, OrderSide.BUY, Exchange.KRX, 0
    )
    assert real_result is not None
    assert real_result.data["rule"] == "max_total_exposure"
    assert real_result.data["max_total_exposure_pct"] == 30.0


@pytest.mark.asyncio
async def test_risk_gate_real_mode_blocks_pending_orders_under_canary_threshold():
    """paper 에서 통과하던 7개 pending 이 real canary overlay 5 에서는 차단되어야 한다."""
    cfg = RiskGateConfig(max_pending_orders=10)
    # paper: 7 pending < 10 → max_pending_orders rule 로는 차단되지 않아야 함
    paper_svc, _, _ = _service(config=cfg, env=_PaperEnv())
    paper_result = await paper_svc.validate_order(
        "005930", 70_000, 1, OrderSide.BUY, Exchange.KRX, active_order_count=7
    )
    if paper_result is not None:
        assert paper_result.data.get("rule") != "max_pending_orders"

    # real: 7 pending >= 5 (canary overlay) → 차단
    real_svc, _, _ = _service(config=cfg, env=_RealEnv())
    real_result = await real_svc.validate_order(
        "005930", 70_000, 1, OrderSide.BUY, Exchange.KRX, active_order_count=7
    )
    assert real_result is not None
    assert real_result.data["rule"] == "max_pending_orders"
    assert real_result.data["max_pending_orders"] == 5


# ── P0 0-7: operating_profile 분기 ────────────────────────────────────


def _profile_service(*, config=None, env=None, operating_profile="canary"):
    """operating_profile 키워드를 받는 RiskGateService 헬퍼."""
    kill_switch = AsyncMock()
    kill_switch.check_orders_allowed = AsyncMock(return_value=(True, None))
    kill_switch.is_strategy_tripped = MagicMock(return_value=None)
    cache = MagicMock()
    cache.get = AsyncMock(return_value=AccountSnapshot(
        total_equity=100_000_000,
        available_cash=50_000_000,
        positions={"000660": 10_000_000},
    ))
    return RiskGateService(
        config=config or RiskGateConfig(),
        kill_switch_service=kill_switch,
        account_snapshot_cache=cache,
        strategy_risk_provider=None,
        logger=MagicMock(),
        env=env,
        operating_profile=operating_profile,
    )


def test_risk_gate_canary_profile_uses_canary_overrides():
    """real + profile=canary → canary_overrides 적용 (5%, 2 pending)."""
    cfg = RiskGateConfig(max_total_exposure_pct=95.0, max_pending_orders=10)
    svc = _profile_service(config=cfg, env=_RealEnv(), operating_profile="canary")
    assert svc._effective_max_total_exposure_pct() == 5.0
    assert svc._effective_max_pending_orders() == 2


def test_risk_gate_real_limited_profile_uses_real_mode_overrides():
    """real + profile=real_limited → real_mode_overrides 적용 (30%, 5 pending)."""
    cfg = RiskGateConfig(max_total_exposure_pct=95.0, max_pending_orders=10)
    svc = _profile_service(config=cfg, env=_RealEnv(), operating_profile="real_limited")
    assert svc._effective_max_total_exposure_pct() == 30.0
    assert svc._effective_max_pending_orders() == 5


def test_risk_gate_real_full_profile_uses_base_values():
    """real + profile=real_full → overlay 미적용, base 사용."""
    cfg = RiskGateConfig(max_total_exposure_pct=95.0, max_pending_orders=10)
    svc = _profile_service(config=cfg, env=_RealEnv(), operating_profile="real_full")
    assert svc._effective_max_total_exposure_pct() == 95.0
    assert svc._effective_max_pending_orders() == 10


def test_risk_gate_paper_mode_ignores_profile():
    """paper 모드: profile 무시, base 사용."""
    cfg = RiskGateConfig(max_total_exposure_pct=95.0, max_pending_orders=10)
    svc = _profile_service(config=cfg, env=_PaperEnv(), operating_profile="canary")
    assert svc._effective_max_total_exposure_pct() == 95.0
    assert svc._effective_max_pending_orders() == 10


def test_risk_gate_default_profile_is_canary():
    """operating_profile 미지정 시 canary 기본값."""
    cfg = RiskGateConfig(max_total_exposure_pct=95.0, max_pending_orders=10)
    svc = _profile_service(config=cfg, env=_RealEnv())
    assert svc._effective_max_total_exposure_pct() == 5.0
    assert svc._effective_max_pending_orders() == 2


def test_risk_gate_canary_overrides_user_yaml():
    """yaml 에서 canary_overrides 명시 시 default 보다 우선."""
    cfg = RiskGateConfig(
        max_total_exposure_pct=95.0,
        max_pending_orders=10,
        canary_overrides={"max_total_exposure_pct": 3.0, "max_pending_orders": 1},
    )
    svc = _profile_service(config=cfg, env=_RealEnv(), operating_profile="canary")
    assert svc._effective_max_total_exposure_pct() == 3.0
    assert svc._effective_max_pending_orders() == 1


@pytest.mark.asyncio
async def test_risk_gate_canary_profile_blocks_exposure_above_5pct():
    """canary profile: 10% 노출 시도 → 5% 한도로 차단."""
    cfg = RiskGateConfig(max_total_exposure_pct=95.0)
    # 10M positions on 100M equity → 10% exposure
    snapshot = AccountSnapshot(
        total_equity=100_000_000,
        available_cash=80_000_000,
        positions={"000660": 10_000_000},
    )
    kill_switch = AsyncMock()
    kill_switch.check_orders_allowed = AsyncMock(return_value=(True, None))
    kill_switch.is_strategy_tripped = MagicMock(return_value=None)
    cache = MagicMock()
    cache.get = AsyncMock(return_value=snapshot)
    svc = RiskGateService(
        config=cfg, kill_switch_service=kill_switch,
        account_snapshot_cache=cache, strategy_risk_provider=None,
        logger=MagicMock(), env=_RealEnv(),
        operating_profile="canary",
    )
    result = await svc.validate_order(
        "005930", 70_000, 1, OrderSide.BUY, Exchange.KRX, active_order_count=0
    )
    assert result is not None
    assert result.data["rule"] == "max_total_exposure"
    assert result.data["max_total_exposure_pct"] == 5.0
