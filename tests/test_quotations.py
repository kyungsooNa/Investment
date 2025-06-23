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

@pytest.mark.asyncio
async def test_get_filtered_stocks_by_momentum():
    mock_logger = MagicMock()
    mock_config = {
        "api_key": "dummy-key",
        "api_secret_key": "dummy-secret",
        "base_url": "https://mock-base",
        "tr_ids": {
            "quotations": {
                "inquire_price": "dummy-tr-id",
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

    # 시총 상위 mock 데이터
    quotations.get_top_market_cap_stocks = AsyncMock(return_value={
        "output": [
            {"isu_cd": "A0001", "acc_trdvol": "300000"},
            {"isu_cd": "A0002", "acc_trdvol": "100000"}
        ]
    })

    # 전일 종가 및 거래량 mock
    quotations.get_previous_day_info = MagicMock(side_effect=[
        {"prev_close": 8000, "prev_volume": 100000},  # symbol 0001
        {"prev_close": 10000, "prev_volume": 100000}  # symbol 0002
    ])

    # 현재가 요약 mock
    quotations.get_price_summary = AsyncMock(side_effect=[
        {"symbol": "0001", "open": 8000, "current": 9000, "change_rate": 12.5},
        {"symbol": "0002", "open": 10000, "current": 10800, "change_rate": 8.0}
    ])

    results = await quotations.get_filtered_stocks_by_momentum(
        count=2, min_change_rate=10.0, min_volume_ratio=2.0
    )

    assert len(results) == 1
    assert results[0]["symbol"] == "0001"
    assert results[0]["change_rate"] >= 10.0
    assert results[0]["current_volume"] / results[0]["prev_volume"] >= 2.0
