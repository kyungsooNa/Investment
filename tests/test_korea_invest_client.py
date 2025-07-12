import pytest
from unittest.mock import MagicMock, AsyncMock, patch
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
    mock_token_manager = MagicMock()

    # 2. 각 도메인 API 클래스들도 patch
    with patch("brokers.korea_investment.korea_invest_client.KoreaInvestApiQuotations") as mock_quotations, \
         patch("brokers.korea_investment.korea_invest_client.KoreaInvestApiAccount") as mock_account, \
         patch("brokers.korea_investment.korea_invest_client.KoreaInvestApiTrading") as mock_trading, \
         patch("brokers.korea_investment.korea_invest_client.KoreaInvestWebSocketAPI") as mock_ws:

        client = KoreaInvestApiClient(env=mock_env, token_manager=mock_token_manager)

        # 3. 인스턴스 존재 확인
        mock_quotations.assert_called_once()
        mock_account.assert_called_once()
        mock_trading.assert_called_once()
        mock_ws.assert_called_once()

        assert client._config["access_token"] == "test-access-token"
        assert str(client).startswith("KoreaInvestApiClient(")


@pytest.mark.asyncio
async def test_quotations_get_price_summary_success():
    # env와 config mock
    mock_env = MagicMock()
    mock_env.get_full_config.return_value = {
        "access_token": "dummy-access-token",
        "api_key": "dummy-key",
        "api_secret_key": "dummy-secret",
        "base_url": "https://mock-base",
        "websocket_url": "wss://mock-websocket-url",
        "tr_ids": {
            "quotations": {
                "inquire_price": "dummy-tr-id"
            }
        },
        "custtype": "P",
        "is_paper_trading": False
    }

    # 1. TokenManager에 대한 모의(mock) 객체를 생성합니다.
    mock_token_manager = MagicMock()

    # 2. KoreaInvestApiClient 생성자를 호출할 때 mock_token_manager를 전달합니다.
    client = KoreaInvestApiClient(env=mock_env, token_manager=mock_token_manager)

    # quotations.call_api 비동기 메서드 모킹 (실제 네트워크 호출 차단)
    client._quotations.call_api = AsyncMock(return_value={
        "output": {
            "stck_oprc": "10000",
            "stck_prpr": "10500"
        }
    })

    # get_price_summary 호출
    result = await client._quotations.get_price_summary("005930")

    assert result["symbol"] == "005930"
    assert result["open"] == 10000
    assert result["current"] == 10500
    assert abs(result["change_rate"] - 5.0) < 0.01


@pytest.mark.asyncio
async def test_client_str_and_missing_access_token():
    # 정상 케이스
    mock_env = MagicMock()
    mock_env.get_full_config.return_value = {
        "access_token": "dummy-access-token",
        "api_key": "dummy-key",
        "api_secret_key": "dummy-secret",
        "base_url": "https://mock-base",
        "websocket_url": "wss://mock-websocket-url",  # 추가 필수
        "tr_ids": {
            "quotations": {
                "inquire_price": "dummy-tr-id"
            }
        },
        "custtype": "P",
        "is_paper_trading": False
    }
    # 1. TokenManager에 대한 모의(mock) 객체를 생성합니다.
    mock_token_manager = MagicMock()

    # 2. KoreaInvestApiClient 생성자를 호출할 때 mock_token_manager를 전달합니다.
    client = KoreaInvestApiClient(env=mock_env, token_manager=mock_token_manager)
    expected_str = "KoreaInvestApiClient(base_url=https://mock-base, is_paper_trading=False)"
    assert str(client) == expected_str

    # access_token 누락 시 ValueError 발생
    mock_env2 = MagicMock()
    mock_env2.get_full_config.return_value = {
        "access_token": None,
        "api_key": "dummy-key",
        "api_secret_key": "dummy-secret",
        "base_url": "https://mock-base",
        "websocket_url": "wss://mock-websocket-url",  # 추가 필수
        "tr_ids": {
            "quotations": {
                "inquire_price": "dummy-tr-id"
            }
        },
        "custtype": "P",
        "is_paper_trading": False
    }
    import pytest
    with pytest.raises(ValueError, match="접근 토큰이 없습니다"):
        KoreaInvestApiClient(env=mock_env2, token_manager=mock_token_manager)
