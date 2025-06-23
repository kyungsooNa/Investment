import pytest
from unittest.mock import AsyncMock, MagicMock
from api.quotations import Quotations

@pytest.mark.asyncio
async def test_get_price_summary():
    # 1. Mock 설정
    mock_logger = MagicMock()
    mock_config = {
        "api_key": "dummy-key",
        "api_secret_key": "dummy-secret",
        "base_url": "https://mock-base",
        "tr_ids": {
            "quotations": {
                "inquire_price": "dummy-tr-id"
            }
        },
        "custtype": "P"
    }

    # 2. Quotations 인스턴스 생성
    quotations = Quotations(
        base_url=mock_config["base_url"],
        headers={},
        config=mock_config,
        logger=mock_logger
    )

    # 3. get_current_price 메서드를 mocking
    quotations.get_current_price = AsyncMock(return_value={
        "output": {
            "stck_oprc": "10000",
            "stck_prpr": "11000"
        }
    })

    # 4. 테스트 실행
    result = await quotations.get_price_summary("005930")  # 삼성전자 예시

    # 5. 결과 검증
    assert result == {
        "symbol": "005930",
        "open": 10000,
        "current": 11000,
        "change_rate": 10.0
    }

@pytest.mark.asyncio
async def test_get_price_summary_open_price_zero():

    mock_logger = MagicMock()
    mock_config = {
        "api_key": "dummy-key",
        "api_secret_key": "dummy-secret",
        "base_url": "https://mock-base",
        "tr_ids": {
            "quotations": {
                "inquire_price": "dummy-tr-id"
            }
        },
        "custtype": "P"
    }

    # 2. Quotations 인스턴스 생성
    quotations = Quotations(
        base_url=mock_config["base_url"],
        headers={},
        config=mock_config,
        logger=mock_logger
    )

    quotations.get_current_price = AsyncMock(return_value={
        "output": {
            "stck_oprc": "0",
            "stck_prpr": "11000"
        }
    })

    result = await quotations.get_price_summary("005930")
    assert result["symbol"] == "005930"
    assert result["open"] == 0
    assert result["current"] == 11000
    assert result["change_rate"] == 0.0  # 시가가 0이면 등락률도 0 처리

@pytest.mark.asyncio
async def test_get_price_summary_missing_fields():
    mock_logger = MagicMock()
    mock_config = {
        "api_key": "dummy-key",
        "api_secret_key": "dummy-secret",
        "base_url": "https://mock-base",
        "tr_ids": {
            "quotations": {
                "inquire_price": "dummy-tr-id"
            }
        },
        "custtype": "P"
    }

    # 2. Quotations 인스턴스 생성
    quotations = Quotations(
        base_url=mock_config["base_url"],
        headers={},
        config=mock_config,
        logger=mock_logger
    )

    quotations.get_current_price = AsyncMock(return_value={
        "output": {
            "stck_oprc": "0",
            "stck_prpr": "11000"
        }
    })

    result = await quotations.get_price_summary("005930")
    assert result["symbol"] == "005930"
    assert result["open"] == 0
    assert result["current"] == 11000
    assert result["change_rate"] == 0.0  # 0으로 나누는 경우 방지됨

@pytest.mark.asyncio
async def test_get_top_market_cap_stocks_success():
    mock_logger = MagicMock()
    mock_config = {
        "api_key": "dummy-key",
        "api_secret_key": "dummy-secret",
        "base_url": "https://mock-base",
        "tr_ids": {
            "quotations": {
                "top_market_cap": "dummy-tr-id"
            }
        },
        "custtype": "P"
    }

    quotations = Quotations(
        base_url=mock_config["base_url"],
        headers={},
        config=mock_config,
        logger=mock_logger
    )

    mock_response = {
        "rt_cd": "0",
        "output": [{"symbol": "005930", "name": "삼성전자"}]
    }

    quotations._call_api = AsyncMock(return_value=mock_response)

    result = await quotations.get_top_market_cap_stocks()
    assert result == mock_response

@pytest.mark.asyncio
async def test_get_top_market_cap_stocks_failure():
    mock_logger = MagicMock()
    mock_config = {
        "api_key": "dummy-key",
        "api_secret_key": "dummy-secret",
        "base_url": "https://mock-base",
        "tr_ids": {
            "quotations": {
                "top_market_cap": "dummy-tr-id"
            }
        },
        "custtype": "P"
    }

    quotations = Quotations(
        base_url=mock_config["base_url"],
        headers={},
        config=mock_config,
        logger=mock_logger
    )

    quotations._call_api = AsyncMock(return_value={
        "rt_cd": "1",  # 실패 코드
        "output": None
    })

    result = await quotations.get_top_market_cap_stocks()
    assert result is None
