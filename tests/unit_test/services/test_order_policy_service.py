from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from common.types import ErrorCode, Exchange, OrderSide, ResCommonResponse
from config.config_loader import OrderPolicyConfig
from services.execution_flow_service import ExecutionFlowSnapshot
from services.order_policy_service import OrderPolicyService


def _service(*, config=None, quote_provider=None, security_info_provider=None, trade_flow_provider=None, logger=None):
    cfg = config or OrderPolicyConfig()
    if security_info_provider is None:
        cfg = cfg.model_copy(update={"security_status_checks_enabled": False})
    if trade_flow_provider is None:
        cfg = cfg.model_copy(update={"trade_flow_checks_enabled": False})
    return OrderPolicyService(
        config=cfg,
        quote_provider=quote_provider,
        security_info_provider=security_info_provider,
        trade_flow_provider=trade_flow_provider,
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


def _stock_info(
    *,
    market_cap=500_000_000_000,
    iscd_stat_cls_code="",
    mang_issu_cls_code="",
    mrkt_warn_cls_code="",
    invt_caful_yn="N",
):
    return ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value,
        msg1="OK",
        data={
            "output": {
                "hts_avls": str(market_cap),
                "iscd_stat_cls_code": iscd_stat_cls_code,
                "mang_issu_cls_code": mang_issu_cls_code,
                "mrkt_warn_cls_code": mrkt_warn_cls_code,
                "invt_caful_yn": invt_caful_yn,
            }
        },
    )


def _flow_snapshot(**kwargs):
    data = {
        "code": "005930",
        "measured_at": datetime.now(),
        "source": "rest",
        "execution_strength_pct": 105.0,
        "recent_trade_count": 3,
        "recent_trade_volume": 30,
        "recent_trade_value_won": 2_100_000,
        "trade_velocity_per_min": 3.0,
        "volume_velocity_per_min": 30.0,
        "last_trade_age_sec": 10.0,
        "data_age_sec": 10.0,
        "sample_window_sec": 60,
    }
    data.update(kwargs)
    return ExecutionFlowSnapshot(**data)


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


@pytest.mark.asyncio
async def test_security_status_blocks_managed_issue():
    provider = AsyncMock()
    provider.get_current_price.return_value = _stock_info(mang_issu_cls_code="1")
    svc = _service(security_info_provider=provider)

    decision = await svc.validate_order(
        stock_code="005930",
        price=70_000,
        qty=1,
        side=OrderSide.BUY,
        exchange=Exchange.KRX,
    )

    assert decision.blocked is True
    assert decision.rule == "managed_issue_stock"
    assert decision.context["mang_issu_cls_code"] == "1"


@pytest.mark.asyncio
async def test_security_status_blocks_investment_warning_code():
    provider = AsyncMock()
    provider.get_current_price.return_value = _stock_info(mrkt_warn_cls_code="2")
    svc = _service(security_info_provider=provider)

    decision = await svc.validate_order(
        stock_code="005930",
        price=70_000,
        qty=1,
        side=OrderSide.BUY,
        exchange=Exchange.KRX,
    )

    assert decision.blocked is True
    assert decision.rule == "investment_warning_stock"


@pytest.mark.asyncio
async def test_security_status_blocks_configured_stock_status_code():
    provider = AsyncMock()
    provider.get_current_price.return_value = _stock_info(iscd_stat_cls_code="53")
    svc = _service(security_info_provider=provider)

    decision = await svc.validate_order(
        stock_code="005930",
        price=70_000,
        qty=1,
        side=OrderSide.BUY,
        exchange=Exchange.KRX,
    )

    assert decision.blocked is True
    assert decision.rule == "investment_warning_stock"


@pytest.mark.asyncio
async def test_security_status_blocks_investment_caution_when_enabled():
    provider = AsyncMock()
    provider.get_current_price.return_value = _stock_info(invt_caful_yn="Y")
    svc = _service(
        config=OrderPolicyConfig(block_investment_caution=True),
        security_info_provider=provider,
    )

    decision = await svc.validate_order(
        stock_code="005930",
        price=70_000,
        qty=1,
        side=OrderSide.BUY,
        exchange=Exchange.KRX,
    )

    assert decision.blocked is True
    assert decision.rule == "investment_caution_stock"


@pytest.mark.asyncio
async def test_security_status_blocks_small_market_cap():
    provider = AsyncMock()
    provider.get_current_price.return_value = _stock_info(market_cap=50_000_000_000)
    svc = _service(
        config=OrderPolicyConfig(min_market_cap_won=100_000_000_000),
        security_info_provider=provider,
    )

    decision = await svc.validate_order(
        stock_code="005930",
        price=70_000,
        qty=1,
        side=OrderSide.BUY,
        exchange=Exchange.KRX,
    )

    assert decision.blocked is True
    assert decision.rule == "market_cap_too_low"
    assert decision.context["market_cap_won"] == 50_000_000_000


@pytest.mark.asyncio
async def test_security_status_context_is_included_when_allowed():
    provider = AsyncMock()
    provider.get_current_price.return_value = _stock_info(market_cap=500_000_000_000)
    svc = _service(
        config=OrderPolicyConfig(order_book_checks_enabled=False, min_market_cap_won=100_000_000_000),
        security_info_provider=provider,
    )

    decision = await svc.validate_order(
        stock_code="005930",
        price=70_000,
        qty=1,
        side=OrderSide.BUY,
        exchange=Exchange.KRX,
    )

    assert decision.allowed is True
    assert decision.context["market_cap_won"] == 500_000_000_000


@pytest.mark.asyncio
async def test_security_status_failure_blocks_by_default():
    provider = AsyncMock()
    provider.get_current_price.return_value = ResCommonResponse(rt_cd="1", msg1="API error")
    svc = _service(security_info_provider=provider)

    decision = await svc.validate_order(
        stock_code="005930",
        price=70_000,
        qty=1,
        side=OrderSide.BUY,
        exchange=Exchange.KRX,
    )

    assert decision.blocked is True
    assert decision.rule == "security_status_unavailable"


@pytest.mark.asyncio
async def test_security_status_failure_can_fail_open():
    provider = AsyncMock()
    provider.get_current_price.return_value = ResCommonResponse(rt_cd="1", msg1="API error")
    svc = _service(
        config=OrderPolicyConfig(
            order_book_checks_enabled=False,
            security_status_fail_policy="allow",
        ),
        security_info_provider=provider,
    )

    decision = await svc.validate_order(
        stock_code="005930",
        price=70_000,
        qty=1,
        side=OrderSide.BUY,
        exchange=Exchange.KRX,
    )

    assert decision.allowed is True
    assert decision.context["security_status_error"] == "API error"


@pytest.mark.asyncio
async def test_trade_flow_context_is_included_when_allowed():
    provider = AsyncMock()
    provider.get_snapshot.return_value = _flow_snapshot()
    svc = _service(
        config=OrderPolicyConfig(order_book_checks_enabled=False),
        trade_flow_provider=provider,
    )

    decision = await svc.validate_order(
        stock_code="005930",
        price=70_000,
        qty=1,
        side=OrderSide.BUY,
        exchange=Exchange.KRX,
    )

    assert decision.allowed is True
    assert decision.context["recent_trade_count"] == 3
    assert decision.context["execution_strength_pct"] == 105.0


@pytest.mark.asyncio
async def test_trade_flow_blocks_stale_recent_trade():
    provider = AsyncMock()
    provider.get_snapshot.return_value = _flow_snapshot(last_trade_age_sec=90.0)
    svc = _service(
        config=OrderPolicyConfig(order_book_checks_enabled=False, max_last_trade_age_sec=60.0),
        trade_flow_provider=provider,
    )

    decision = await svc.validate_order(
        stock_code="005930",
        price=70_000,
        qty=1,
        side=OrderSide.BUY,
        exchange=Exchange.KRX,
    )

    assert decision.blocked is True
    assert decision.rule == "trade_flow_stale"


@pytest.mark.asyncio
async def test_trade_flow_blocks_low_recent_trade_count():
    provider = AsyncMock()
    provider.get_snapshot.return_value = _flow_snapshot(recent_trade_count=0)
    svc = _service(
        config=OrderPolicyConfig(order_book_checks_enabled=False, min_recent_trade_count=1),
        trade_flow_provider=provider,
    )

    decision = await svc.validate_order(
        stock_code="005930",
        price=70_000,
        qty=1,
        side=OrderSide.BUY,
        exchange=Exchange.KRX,
    )

    assert decision.blocked is True
    assert decision.rule == "trade_flow_velocity_too_low"


@pytest.mark.asyncio
async def test_trade_flow_blocks_when_time_concluded_unavailable_for_velocity_check():
    provider = AsyncMock()
    provider.get_snapshot.return_value = _flow_snapshot(
        recent_trade_count=None,
        quality_flags=["time_concluded_unavailable"],
    )
    svc = _service(
        config=OrderPolicyConfig(order_book_checks_enabled=False, min_recent_trade_count=1),
        trade_flow_provider=provider,
    )

    decision = await svc.validate_order(
        stock_code="005930",
        price=70_000,
        qty=1,
        side=OrderSide.BUY,
        exchange=Exchange.KRX,
    )

    assert decision.blocked is True
    assert decision.rule == "trade_flow_unavailable"
    assert decision.context["trade_flow_error"] == "time_concluded_unavailable"


@pytest.mark.asyncio
async def test_trade_flow_blocks_low_trade_value_per_min():
    provider = AsyncMock()
    provider.get_snapshot.return_value = _flow_snapshot(recent_trade_value_won=500_000)
    svc = _service(
        config=OrderPolicyConfig(
            order_book_checks_enabled=False,
            min_trade_value_per_min_won=1_000_000,
        ),
        trade_flow_provider=provider,
    )

    decision = await svc.validate_order(
        stock_code="005930",
        price=70_000,
        qty=1,
        side=OrderSide.BUY,
        exchange=Exchange.KRX,
    )

    assert decision.blocked is True
    assert decision.rule == "trade_flow_value_velocity_too_low"


@pytest.mark.asyncio
async def test_trade_flow_failure_blocks_by_default():
    provider = AsyncMock()
    provider.get_snapshot.side_effect = RuntimeError("API error")
    svc = _service(
        config=OrderPolicyConfig(order_book_checks_enabled=False),
        trade_flow_provider=provider,
    )

    decision = await svc.validate_order(
        stock_code="005930",
        price=70_000,
        qty=1,
        side=OrderSide.BUY,
        exchange=Exchange.KRX,
    )

    assert decision.blocked is True
    assert decision.rule == "trade_flow_unavailable"


@pytest.mark.asyncio
async def test_trade_flow_failure_can_fail_open():
    provider = AsyncMock()
    provider.get_snapshot.side_effect = RuntimeError("API error")
    svc = _service(
        config=OrderPolicyConfig(
            order_book_checks_enabled=False,
            trade_flow_fail_policy="allow",
        ),
        trade_flow_provider=provider,
    )

    decision = await svc.validate_order(
        stock_code="005930",
        price=70_000,
        qty=1,
        side=OrderSide.BUY,
        exchange=Exchange.KRX,
    )

    assert decision.allowed is True
    assert decision.context["trade_flow_error"] == "API error"
