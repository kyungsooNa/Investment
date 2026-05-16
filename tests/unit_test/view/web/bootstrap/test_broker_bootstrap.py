"""BrokerBootstrap 단위 테스트.

`WebAppContext._bootstrap_broker()` 본문에서 추출된 BrokerBootstrap이
토큰 발급, BrokerAPIWrapper 생성, MarketCalendarService 동기화를
정상 수행하는지 검증한다.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_fake_context():
    """BrokerBootstrap.run 이 mutate / 참조 할 컨텍스트 stub."""
    ctx = SimpleNamespace()
    ctx.logger = MagicMock()
    ctx.env = MagicMock()
    ctx.env.get_access_token = AsyncMock(return_value=True)
    ctx.env.get_real_access_token = AsyncMock(return_value="real-token")
    ctx.market_clock = MagicMock()
    ctx.streaming_event_logger = MagicMock()
    ctx.stock_code_repository = MagicMock()
    ctx._mcs = MagicMock()
    ctx._mcs._sync_calendar_if_needed = AsyncMock()
    ctx.broker = None
    return ctx


@pytest.mark.asyncio
async def test_broker_bootstrap_returns_true_and_assigns_broker():
    from view.web.bootstrap.broker_bootstrap import BrokerBootstrap

    ctx = _make_fake_context()
    with patch("view.web.bootstrap.broker_bootstrap.BrokerAPIWrapper", autospec=True) as broker_cls:
        success = await BrokerBootstrap(ctx).run(is_paper_trading=True)

    assert success is True
    broker_cls.assert_called_once()
    assert ctx.broker is broker_cls.return_value
    ctx._mcs.set_broker.assert_called_once_with(broker_cls.return_value)
    ctx._mcs._sync_calendar_if_needed.assert_awaited_once()


@pytest.mark.asyncio
async def test_broker_bootstrap_returns_false_when_token_fails():
    from view.web.bootstrap.broker_bootstrap import BrokerBootstrap

    ctx = _make_fake_context()
    ctx.env.get_access_token = AsyncMock(return_value=False)

    with patch("view.web.bootstrap.broker_bootstrap.BrokerAPIWrapper", autospec=True) as broker_cls:
        success = await BrokerBootstrap(ctx).run(is_paper_trading=True)

    assert success is False
    broker_cls.assert_not_called()
    ctx._mcs.set_broker.assert_not_called()


@pytest.mark.asyncio
async def test_broker_bootstrap_paper_mode_acquires_real_token():
    from view.web.bootstrap.broker_bootstrap import BrokerBootstrap

    ctx = _make_fake_context()
    with patch("view.web.bootstrap.broker_bootstrap.BrokerAPIWrapper", autospec=True):
        await BrokerBootstrap(ctx).run(is_paper_trading=True)

    ctx.env.get_real_access_token.assert_awaited_once()


@pytest.mark.asyncio
async def test_broker_bootstrap_real_mode_skips_real_token_call():
    from view.web.bootstrap.broker_bootstrap import BrokerBootstrap

    ctx = _make_fake_context()
    with patch("view.web.bootstrap.broker_bootstrap.BrokerAPIWrapper", autospec=True):
        await BrokerBootstrap(ctx).run(is_paper_trading=False)

    ctx.env.get_real_access_token.assert_not_called()


@pytest.mark.asyncio
async def test_broker_bootstrap_returns_false_on_exception():
    from view.web.bootstrap.broker_bootstrap import BrokerBootstrap

    ctx = _make_fake_context()
    ctx.env.get_access_token = AsyncMock(side_effect=RuntimeError("network down"))

    success = await BrokerBootstrap(ctx).run(is_paper_trading=True)

    assert success is False
    ctx.logger.critical.assert_called()
