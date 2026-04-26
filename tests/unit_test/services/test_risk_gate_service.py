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
        strategy_risk_provider=strategy_provider,
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
async def test_env_none_skips_consistency_check():
    """env 미주입 시 fail-open. 기존 테스트 회귀 방지."""
    svc, _, _ = _service()
    assert svc._env is None

    result = await svc.validate_order("005930", 70_000, 10, OrderSide.BUY, Exchange.KRX, 0)

    assert result is None
