"""
KillSwitch 손익 hook 통합 테스트.

fake broker + 실제 VirtualTradeRepository + 실제 KillSwitchService 조합으로
"매도 체결 → KS trip → 다음 BUY 차단" 흐름을 검증한다.
외부 네트워크 없이 service-level integration 만 테스트한다.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime

from common.types import (
    OrderContext, OrderExecutionReport, OrderSide, OrderState, Exchange,
    ResCommonResponse, ErrorCode,
)
from config.config_loader import KillSwitchConfig
from core.market_clock import MarketClock
from repositories.virtual_trade_repository import VirtualTradeRepository
from services.kill_switch_service import KillSwitchService
from services.notification_service import NotificationService
from services.order_execution_service import OrderExecutionService
from services.virtual_trade_service import VirtualTradeService


# ── 픽스처 ────────────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_db(tmp_path):
    db_dir = tmp_path / "data" / "VirtualTradeRepository"
    db_dir.mkdir(parents=True)
    return str(db_dir / "virtual_trade.db")


@pytest.fixture
def ks_state_file(tmp_path):
    return str(tmp_path / "kill_switch_state.json")


@pytest.fixture
def market_clock():
    mc = MagicMock(spec=MarketClock)
    mc.get_current_kst_time.return_value = datetime(2026, 5, 8, 10, 0, 0)
    mc.is_market_operating_hours.return_value = True
    mc.async_sleep = AsyncMock()
    return mc


@pytest.fixture
def mock_notification():
    svc = AsyncMock(spec=NotificationService)
    svc.emit = AsyncMock()
    return svc


@pytest.fixture
def ks_service(ks_state_file, mock_notification):
    cfg = KillSwitchConfig(
        enabled=True,
        max_consecutive_losses=2,   # 낮게 설정 → 빠른 트립
        daily_loss_threshold_won=100_000,
        state_file_path=ks_state_file,
    )
    return KillSwitchService(config=cfg, notification_service=mock_notification)


@pytest.fixture
def virtual_trade_repo(tmp_db, market_clock):
    return VirtualTradeRepository(db_path=tmp_db, market_clock=market_clock)


@pytest.fixture
def virtual_trade_svc(virtual_trade_repo):
    return VirtualTradeService(repository=virtual_trade_repo)


@pytest.fixture
def fake_broker():
    broker = AsyncMock()
    broker.place_stock_order.return_value = ResCommonResponse(
        rt_cd="0", msg1="주문 성공", data={"output": {"KRX_FWDG_ORD_ORGNO": "", "ODNO": "ORD001", "ORD_TMD": "100000"}}
    )
    broker.env = MagicMock(is_paper_trading=True)
    return broker


@pytest.fixture
def service(fake_broker, market_clock, mock_notification, ks_service, virtual_trade_svc):
    logger = MagicMock()
    logger.info = MagicMock()
    logger.debug = MagicMock()
    logger.warning = MagicMock()
    logger.error = MagicMock()
    logger.critical = MagicMock()
    logger.exception = MagicMock()
    return OrderExecutionService(
        broker_api_wrapper=fake_broker,
        logger=logger,
        market_clock=market_clock,
        notification_service=mock_notification,
        kill_switch_service=ks_service,
        virtual_trade_service=virtual_trade_svc,
    )


def _sell_context(source="strategy:MomentumStrategy", filled_qty=1, price=70000, state=OrderState.FILLED):
    return OrderContext(
        order_key=f"sell_005930_{source}",
        stock_code="005930",
        side=OrderSide.SELL,
        state=state,
        price=price,
        qty=filled_qty,
        filled_qty=filled_qty,
        virtual_recorded_qty=0,
        source=source,
    )


def _fill_report(fill_price=60000, fill_qty=1, state=OrderState.FILLED):
    return OrderExecutionReport(
        broker_order_no="ORD_IT_001",
        stock_code="005930",
        side=OrderSide.SELL,
        event_state=state,
        fill_qty=fill_qty,
        fill_price=fill_price,
        cumulative_filled_qty=fill_qty,
        remaining_qty=0,
        event_time="20260508100000",
    )


# ── 테스트 ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_sell_fill_updates_kill_switch_consecutive_loss(
    service, ks_service, virtual_trade_repo,
):
    """매도 체결 손실 1회 → KS consecutive_losses 증가."""
    virtual_trade_repo.log_buy("MomentumStrategy", "005930", 70000)

    ctx = _sell_context(filled_qty=1, price=70000)
    report = _fill_report(fill_price=60000)  # 손실 매도

    await service._persist_virtual_trade_for_terminal_report(ctx, report)

    status = ks_service.get_status()
    assert status["consecutive_losses"] == 1
    assert not ks_service._is_tripped


@pytest.mark.asyncio
async def test_sell_fill_to_kill_switch_trip_blocks_orders(
    service, ks_service, virtual_trade_repo,
):
    """연속 손실 2회 → KS trip → check_orders_allowed() 차단."""
    # 1번째 매도 손실
    virtual_trade_repo.log_buy("MomentumStrategy", "005930_1", 70000)
    ctx1 = OrderContext(
        order_key="sell_005930_1",
        stock_code="005930_1",
        side=OrderSide.SELL,
        state=OrderState.FILLED,
        price=70000,
        qty=1,
        filled_qty=1,
        virtual_recorded_qty=0,
        source="strategy:MomentumStrategy",
    )
    report1 = OrderExecutionReport(
        broker_order_no="ORD_IT_001",
        stock_code="005930_1",
        side=OrderSide.SELL,
        event_state=OrderState.FILLED,
        fill_qty=1,
        fill_price=60000,
        cumulative_filled_qty=1,
        remaining_qty=0,
        event_time="20260508100000",
    )
    await service._persist_virtual_trade_for_terminal_report(ctx1, report1)

    # 아직 트립 아님
    allowed, _ = await ks_service.check_orders_allowed()
    assert allowed

    # 2번째 매도 손실 → max_consecutive_losses=2 초과 → trip
    virtual_trade_repo.log_buy("MomentumStrategy", "000660", 80000)
    ctx2 = OrderContext(
        order_key="sell_000660",
        stock_code="000660",
        side=OrderSide.SELL,
        state=OrderState.FILLED,
        price=80000,
        qty=1,
        filled_qty=1,
        virtual_recorded_qty=0,
        source="strategy:MomentumStrategy",
    )
    report2 = OrderExecutionReport(
        broker_order_no="ORD_IT_002",
        stock_code="000660",
        side=OrderSide.SELL,
        event_state=OrderState.FILLED,
        fill_qty=1,
        fill_price=70000,
        cumulative_filled_qty=1,
        remaining_qty=0,
        event_time="20260508100100",
    )
    await service._persist_virtual_trade_for_terminal_report(ctx2, report2)

    # KS 트립 확인
    assert ks_service._is_tripped

    # 후속 주문 차단 확인
    allowed, reason = await ks_service.check_orders_allowed()
    assert not allowed
    assert reason is not None


@pytest.mark.asyncio
async def test_profit_sell_resets_consecutive_loss_counter(
    service, ks_service, virtual_trade_repo,
):
    """이익 매도 → consecutive_losses 카운터 0 리셋."""
    virtual_trade_repo.log_buy("MomentumStrategy", "005930", 60000)

    ctx = _sell_context(filled_qty=1, price=60000)
    report = _fill_report(fill_price=70000)  # 이익 매도

    await service._persist_virtual_trade_for_terminal_report(ctx, report)

    status = ks_service.get_status()
    assert status["consecutive_losses"] == 0
    assert not ks_service._is_tripped
