import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from api.client import KoreaInvestAPI
from api.quotations import Quotations

@pytest.mark.asyncio
async def test_quotations_get_price_summary_success():
    # env와 config mock
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

    # KoreaInvestAPI 인스턴스 생성
    client = KoreaInvestAPI(env=mock_env)

    # quotations._call_api 비동기 메서드 모킹 (실제 네트워크 호출 차단)
    client.quotations._call_api = AsyncMock(return_value={
        "output": {
            "stck_oprc": "10000",
            "stck_prpr": "10500"
        }
    })

    # get_price_summary 호출
    result = await client.quotations.get_price_summary("005930")

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

    client = KoreaInvestAPI(env=mock_env)
    expected_str = "KoreaInvestAPI(base_url=https://mock-base, is_paper_trading=False)"
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
        KoreaInvestAPI(env=mock_env2)
