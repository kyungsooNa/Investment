from unittest.mock import AsyncMock, MagicMock

import pytest

from common.types import ErrorCode, Exchange, OrderSide, ResCommonResponse
from config.config_loader import OrderPolicyConfig
from services.order_policy_service import OrderPolicyService


def _service(*, config=None, quote_provider=None, logger=None):
    return OrderPolicyService(
        config=config or OrderPolicyConfig(),
        quote_provider=quote_provider,
        logger=logger or MagicMock(),
    )


def _quote(ask=70_100, bid=70_000, ask_qty=100, bid_qty=100, current=70_000, trading_value=1_000_000_000):
    return ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value,
        msg1="OK",
        data={
            "askp1": str(ask),
            "bidp1": str(bid),
            "askp_rsqn1": str(ask_qty),
            "bidp_rsqn1": str(bid_qty),
            "stck_prpr": str(current),
            "acml_tr_pbmn": str(trading_value),
        },
    )


@pytest.mark.asyncio
async def test_limit_tick_size_adjusts_by_default():
    logger = MagicMock()
    svc = _service(config=OrderPolicyConfig(order_book_checks_enabled=False), logger=logger)

    decision = await svc.validate_order(
        stock_code="005930",
        price=70_051,
        qty=1,
        side=OrderSide.BUY,
        exchange=Exchange.KRX,
    )

    assert decision.allowed is True
    assert decision.rule == "tick_size_adjusted"
    assert decision.adjusted_price == 70_000
    logger.info.assert_called()


@pytest.mark.asyncio
async def test_limit_tick_size_block_mode_blocks():
    svc = _service(config=OrderPolicyConfig(tick_size_policy="block"))

    decision = await svc.validate_order(
        stock_code="005930",
        price=70_051,
        qty=1,
        side=OrderSide.BUY,
        exchange=Exchange.KRX,
    )

    assert decision.blocked is True
    response = decision.to_response()
    assert response.rt_cd == ErrorCode.ORDER_POLICY_BLOCKED.value
    assert response.data["rule"] == "invalid_tick_size"


@pytest.mark.asyncio
async def test_nxt_market_order_blocks_by_default():
    svc = _service()

    decision = await svc.validate_order(
        stock_code="005930",
        price=0,
        qty=1,
        side=OrderSide.BUY,
        exchange=Exchange.NXT,
    )

    assert decision.blocked is True
    assert decision.rule == "nxt_market_order_not_supported"


@pytest.mark.asyncio
async def test_market_order_spread_blocks_when_order_book_enabled():
    provider = AsyncMock()
    provider.get_asking_price.return_value = _quote(ask=71_000, bid=70_000, current=70_500)
    svc = _service(config=OrderPolicyConfig(order_book_checks_enabled=True, max_spread_pct=1.0),
                   quote_provider=provider)

    decision = await svc.validate_order(
        stock_code="005930",
        price=0,
        qty=1,
        side=OrderSide.BUY,
        exchange=Exchange.KRX,
    )

    assert decision.blocked is True
    assert decision.rule == "spread_too_wide"


@pytest.mark.asyncio
async def test_market_order_slippage_blocks_when_order_book_enabled():
    provider = AsyncMock()
    provider.get_asking_price.return_value = _quote(ask=71_000, bid=70_900, current=70_000)
    svc = _service(config=OrderPolicyConfig(order_book_checks_enabled=True, max_market_slippage_pct=1.0),
                   quote_provider=provider)

    decision = await svc.validate_order(
        stock_code="005930",
        price=0,
        qty=1,
        side=OrderSide.BUY,
        exchange=Exchange.KRX,
    )

    assert decision.blocked is True
    assert decision.rule == "market_slippage_too_high"


@pytest.mark.asyncio
async def test_market_order_empty_book_blocks_when_enabled():
    provider = AsyncMock()
    provider.get_asking_price.return_value = _quote(ask=0, bid=70_000)
    svc = _service(config=OrderPolicyConfig(order_book_checks_enabled=True),
                   quote_provider=provider)

    decision = await svc.validate_order(
        stock_code="005930",
        price=0,
        qty=1,
        side=OrderSide.BUY,
        exchange=Exchange.KRX,
    )

    assert decision.blocked is True
    assert decision.rule == "empty_order_book"


@pytest.mark.asyncio
async def test_market_order_quote_failure_can_fail_open():
    provider = AsyncMock()
    provider.get_asking_price.return_value = ResCommonResponse(rt_cd="1", msg1="API error")
    svc = _service(config=OrderPolicyConfig(order_book_checks_enabled=True, quote_fail_policy="allow"),
                   quote_provider=provider)

    decision = await svc.validate_order(
        stock_code="005930",
        price=0,
        qty=1,
        side=OrderSide.BUY,
        exchange=Exchange.KRX,
    )

    assert decision.allowed is True
    assert decision.rule == "quote_unavailable"


@pytest.mark.asyncio
async def test_market_order_quote_failure_blocks_by_default_when_order_book_enabled():
    provider = AsyncMock()
    provider.get_asking_price.return_value = ResCommonResponse(rt_cd="1", msg1="API error")
    svc = _service(quote_provider=provider)

    decision = await svc.validate_order(
        stock_code="005930",
        price=0,
        qty=1,
        side=OrderSide.BUY,
        exchange=Exchange.KRX,
    )

    assert decision.blocked is True
    assert decision.rule == "quote_unavailable"


@pytest.mark.asyncio
async def test_market_order_trading_value_blocks_when_below_minimum():
    provider = AsyncMock()
    provider.get_asking_price.return_value = _quote(trading_value=50_000_000)
    svc = _service(
        config=OrderPolicyConfig(
            order_book_checks_enabled=True,
            min_trading_value_won=100_000_000,
        ),
        quote_provider=provider,
    )

    decision = await svc.validate_order(
        stock_code="005930",
        price=0,
        qty=1,
        side=OrderSide.BUY,
        exchange=Exchange.KRX,
    )

    assert decision.blocked is True
    assert decision.rule == "trading_value_too_low"


@pytest.mark.asyncio
async def test_limit_order_trading_value_blocks_when_below_minimum():
    provider = AsyncMock()
    provider.get_asking_price.return_value = _quote(trading_value=50_000_000)
    svc = _service(
        config=OrderPolicyConfig(
            order_book_checks_enabled=True,
            min_trading_value_won=100_000_000,
        ),
        quote_provider=provider,
    )

    decision = await svc.validate_order(
        stock_code="005930",
        price=70_000,
        qty=1,
        side=OrderSide.BUY,
        exchange=Exchange.KRX,
    )

    assert decision.blocked is True
    assert decision.rule == "trading_value_too_low"


@pytest.mark.asyncio
async def test_market_order_top_of_book_participation_blocks():
    provider = AsyncMock()
    provider.get_asking_price.return_value = _quote(ask_qty=100)
    svc = _service(
        config=OrderPolicyConfig(
            order_book_checks_enabled=True,
            max_top_of_book_participation_pct=50.0,
        ),
        quote_provider=provider,
    )

    decision = await svc.validate_order(
        stock_code="005930",
        price=0,
        qty=60,
        side=OrderSide.BUY,
        exchange=Exchange.KRX,
    )

    assert decision.blocked is True
    assert decision.rule == "top_of_book_participation_too_high"


@pytest.mark.asyncio
async def test_limit_order_top_of_book_participation_blocks():
    provider = AsyncMock()
    provider.get_asking_price.return_value = _quote(ask_qty=100)
    svc = _service(
        config=OrderPolicyConfig(
            order_book_checks_enabled=True,
            max_top_of_book_participation_pct=50.0,
        ),
        quote_provider=provider,
    )

    decision = await svc.validate_order(
        stock_code="005930",
        price=70_000,
        qty=60,
        side=OrderSide.BUY,
        exchange=Exchange.KRX,
    )

    assert decision.blocked is True
    assert decision.rule == "top_of_book_participation_too_high"
