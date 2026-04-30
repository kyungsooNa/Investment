from unittest.mock import AsyncMock
import pytest

import brokers.broker_api_wrapper as wrapper_module
from brokers.korea_investment.korea_invest_client import KoreaInvestApiClient


@pytest.fixture
def raw_mock_client(mock_env, mock_logger, mock_market_clock, mocker):
    """RetryQueue/Cache 래핑 없이 KoreaInvestApiClient mock을 직접 주입한 BrokerAPIWrapper를 반환.

    retry/cache 계층을 우회하므로 단위 테스트에서 API 위임 로직만 검증할 때 사용한다.
    반환값: (wrapper, mock_client)
    """
    from brokers.broker_api_wrapper import BrokerAPIWrapper

    mocker.patch(f"{wrapper_module.__name__}.StockCodeRepository")
    mock_client_class = mocker.patch(f"{wrapper_module.__name__}.KoreaInvestApiClient")
    mocker.patch(f"{wrapper_module.__name__}.cache_wrap_client", side_effect=lambda c, *a, **kw: c)
    mocker.patch(f"{wrapper_module.__name__}.retry_queue_wrap_client", side_effect=lambda c, *a, **kw: c)

    mock_client = AsyncMock(spec=KoreaInvestApiClient)
    mock_client_class.return_value = mock_client

    wrapper = BrokerAPIWrapper(
        broker="korea_investment",
        env=mock_env,
        logger=mock_logger,
        market_clock=mock_market_clock,
    )
    return wrapper, mock_client
