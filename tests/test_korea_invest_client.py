import pytest
from unittest.mock import MagicMock, patch
from brokers.korea_investment.korea_invest_client import KoreaInvestApiClient


def test_korea_invest_api_client_initialization():
    # 1. mock env 구성
    mock_env = MagicMock()
    mock_env.get_full_config.return_value = {
        "access_token": "test-access-token",
        "api_key": "test-app-key",
        "api_secret_key": "test-app-secret",
        "base_url": "https://mock-base-url",
        "is_paper_trading": True
    }
    mock_env.my_agent = "mock-user-agent"

    # 2. 각 도메인 API 클래스들도 patch
    with patch("brokers.korea_investment.korea_invest_client.KoreaInvestApiQuotations") as mock_quotations, \
         patch("brokers.korea_investment.korea_invest_client.KoreaInvestApiAccount") as mock_account, \
         patch("brokers.korea_investment.korea_invest_client.KoreaInvestApiTrading") as mock_trading, \
         patch("brokers.korea_investment.korea_invest_client.KereaInvestWebSocketAPI") as mock_ws:

        client = KoreaInvestApiClient(env=mock_env)

        # 3. 인스턴스 존재 확인
        mock_quotations.assert_called_once()
        mock_account.assert_called_once()
        mock_trading.assert_called_once()
        mock_ws.assert_called_once()

        assert client._config["access_token"] == "test-access-token"
        assert str(client).startswith("KoreaInvestApiClient(")
