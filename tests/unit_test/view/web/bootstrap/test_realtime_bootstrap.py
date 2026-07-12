from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from view.web.bootstrap.realtime_bootstrap import RealtimeBootstrap


def test_realtime_bootstrap_builds_streaming_chain():
    ctx = SimpleNamespace(
        broker=MagicMock(), logger=MagicMock(), market_clock=MagicMock(),
        market_data_service=MagicMock(), streaming_event_logger=MagicMock(),
        data_quality_service=MagicMock(), _mcs=MagicMock(),
        kill_switch_service=MagicMock(), stock_repository=MagicMock(),
        notification_service=MagicMock(), operator_alert_service=MagicMock(),
        program_trading_stream_service=MagicMock(), pm=MagicMock(),
    )
    ctx.program_trading_stream_service.load_snapshot.return_value = {}

    with patch("view.web.bootstrap.realtime_bootstrap.StreamingService") as streaming, \
         patch("view.web.bootstrap.realtime_bootstrap.PriceStreamService") as price_stream, \
         patch("view.web.bootstrap.realtime_bootstrap.PriceSubscriptionService") as subscriptions, \
         patch("view.web.bootstrap.realtime_bootstrap.WebSocketWatchdogTask") as watchdog:
        RealtimeBootstrap(ctx).run(config={}, needs_realtime=True)

    assert ctx.streaming_service is streaming.return_value
    assert ctx.price_stream_service is price_stream.return_value
    assert ctx.price_subscription_service is subscriptions.return_value
    assert ctx.websocket_watchdog_task is watchdog.return_value
