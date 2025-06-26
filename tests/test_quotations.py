import pytest
from unittest.mock import AsyncMock, MagicMock
from api.kr_quotations import Quotations

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

    # 🧪 모의투자 아님으로 설정
    quotations._env = MagicMock()
    quotations._env.is_paper_trading = False

    # API 응답 모킹
    mock_top_response = {
        "rt_cd": "0",
        "output": [
            {"iscd": "005930", "stck_avls": "500000000000"}
        ]
    }
    quotations.call_api = AsyncMock(return_value=mock_top_response)

    # 종목명 반환 함수 모킹
    quotations.get_stock_name_by_code = AsyncMock(return_value="삼성전자")

    # 실행 및 검증
    result = await quotations.get_top_market_cap_stocks("0000", count=1)

    assert result == [{
        "code": "005930",
        "name": "삼성전자",
        "market_cap": 500000000000
    }]

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

    quotations.call_api = AsyncMock(return_value={
        "rt_cd": "1",  # 실패 코드
        "output": None
    })

    result = await quotations.get_top_market_cap_stocks("0000", count=1)
    assert result == []

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

@pytest.mark.asyncio
async def test_get_stock_info_by_code_success():
    mock_logger = MagicMock()
    mock_config = {
        "tr_ids": {"quotations": {"search_info": "dummy-tr-id"}},
        "custtype": "P"
    }

    quotations = Quotations("https://mock-url", {}, mock_config, mock_logger)
    mock_output = {
        "hts_kor_isnm": "삼성전자",
        "stck_prpr_smkl_amt": "500000000000"
    }

    quotations.call_api = AsyncMock(return_value={"rt_cd": "0", "output": mock_output})

    result = await quotations.get_stock_info_by_code("005930")
    assert result == mock_output

@pytest.mark.asyncio
async def test_get_stock_info_by_code_failure():
    mock_logger = MagicMock()
    mock_config = {
        "tr_ids": {"quotations": {"search_info": "dummy-tr-id"}},
        "custtype": "P"
    }

    quotations = Quotations("https://mock-url", {}, mock_config, mock_logger)
    quotations.call_api = AsyncMock(return_value={"rt_cd": "1", "output": None})

    result = await quotations.get_stock_info_by_code("005930")
    assert result == {}

@pytest.mark.asyncio
async def test_get_market_cap_success():
    mock_logger = MagicMock()
    mock_config = {}

    quotations = Quotations("https://mock-url", {}, mock_config, mock_logger)
    quotations.get_stock_info_by_code = AsyncMock(return_value={
        "stck_prpr_smkl_amt": "123456789000"
    })

    result = await quotations.get_market_cap("005930")
    assert result == 123456789000

@pytest.mark.asyncio
async def test_get_market_cap_failure_invalid_format():
    mock_logger = MagicMock()
    quotations = Quotations("https://mock-url", {}, {}, mock_logger)
    quotations.get_stock_info_by_code = AsyncMock(return_value={
        "stck_prpr_smkl_amt": "INVALID"
    })

    result = await quotations.get_market_cap("005930")
    assert result == 0

@pytest.mark.asyncio
async def test_get_market_cap_failure_missing_key():
    mock_logger = MagicMock()
    quotations = Quotations("https://mock-url", {}, {}, mock_logger)
    quotations.get_stock_info_by_code = AsyncMock(return_value={})  # no market cap field

    result = await quotations.get_market_cap("005930")
    assert result == 0

@pytest.mark.asyncio
async def test_get_stock_name_by_code_success():
    mock_logger = MagicMock()

    # config와 headers는 최소 필수 필드 포함하도록 설정
    mock_config = {
        "tr_ids": {
            "quotations": {
                "search_info": "dummy-tr-id"
            }
        },
        "custtype": "P"
    }

    quotations = Quotations("https://mock-url", {}, mock_config, mock_logger)

    async def mock_info_method(_):
        return {"hts_kor_isnm": "삼성전자"}
    quotations.get_stock_info_by_code = mock_info_method

    result = await quotations.get_stock_name_by_code("005930")

    assert result == "삼성전자"

@pytest.mark.asyncio
async def test_get_stock_name_by_code_empty():
    mock_logger = MagicMock()
    quotations = Quotations("https://mock-url", {}, {}, mock_logger)
    quotations.get_stock_info_by_code = AsyncMock(return_value={})  # no name field

    result = await quotations.get_stock_name_by_code("005930")
    assert result == ""
