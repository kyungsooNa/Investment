from unittest.mock import MagicMock, patch

from brokers.broker_api_wrapper import BrokerAPIWrapper


def test_it_kiwoom_broker_wrapper_initializes_without_network():
    env = MagicMock()
    env.is_paper_trading = True

    with patch("brokers.broker_api_wrapper.StockCodeRepository"), \
         patch("brokers.broker_api_wrapper.KiwoomApiClient") as client_class, \
         patch("brokers.broker_api_wrapper.cache_wrap_client", side_effect=lambda c, *a, **kw: c), \
         patch("brokers.broker_api_wrapper.retry_queue_wrap_client", side_effect=lambda c, *a, **kw: c):
        wrapper = BrokerAPIWrapper(
            broker="kiwoom",
            env=env,
            logger=MagicMock(),
            market_clock=MagicMock(),
        )

    assert wrapper._broker == "kiwoom"
    assert wrapper._client is client_class.return_value
