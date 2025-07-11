import pytest
from unittest.mock import AsyncMock, MagicMock
from brokers.korea_investment.korea_invest_quotations_api import KoreaInvestApiQuotations

@pytest.fixture(scope="function")
def mock_quotations():
    mock_logger = MagicMock()
    mock_token_manager = MagicMock()
    mock_config = {
        "api_key": "dummy-key",
        "api_secret_key": "dummy-secret",
        "base_url": "https://mock-base",
        "tr_ids": {
            "quotations": {
                "inquire_price": "dummy-tr-id",
                "top_market_cap": "dummy-tr-id",
                "search_info": "dummy-tr-id",
                "inquire_daily_itemchartprice": "FHKST03010100"
            }
        },
        "custtype": "P"
    }

    return KoreaInvestApiQuotations(
        base_url=mock_config["base_url"],
        headers={},
        config=mock_config,
        token_manager=mock_token_manager,
        logger=mock_logger
    )

@pytest.mark.asyncio
async def test_get_price_summary(mock_quotations):
    # 1. Mock 설정
    mock_quotations.get_current_price = AsyncMock(return_value={
        "output": {
            "stck_oprc": "10000",
            "stck_prpr": "11000"
        }
    })

    # 테스트 실행
    result = await mock_quotations.get_price_summary("005930")

    # 5. 결과 검증
    assert result == {
        "symbol": "005930",
        "open": 10000,
        "current": 11000,
        "change_rate": 10.0
    }


@pytest.mark.asyncio
async def test_get_price_summary_open_price_zero(mock_quotations):
    mock_quotations.get_current_price = AsyncMock(return_value={
        "output": {
            "stck_oprc": "0",
            "stck_prpr": "11000"
        }
    })

    result = await mock_quotations.get_price_summary("005930")
    assert result["symbol"] == "005930"
    assert result["open"] == 0
    assert result["current"] == 11000
    assert result["change_rate"] == 0.0  # 시가가 0이면 등락률도 0 처리

@pytest.mark.asyncio
async def test_get_price_summary_missing_keys(mock_quotations):
    mock_quotations.get_current_price = AsyncMock(return_value={
        "output": {
            "stck_oprc": None,
            "stck_prpr": None
        }
    })

    result = await mock_quotations.get_price_summary("005930")

    assert result == {
        "symbol": "005930",
        "open": 0,
        "current": 0,
        "change_rate": 0.0
    }


@pytest.mark.asyncio
async def test_get_price_summary_invalid_response(mock_quotations):
    mock_quotations.get_current_price = AsyncMock(return_value=None)

    result = await mock_quotations.get_price_summary("005930")

    assert result == {
        "symbol": "005930",
        "open": 0,
        "current": 0,
        "change_rate": 0.0
    }


@pytest.mark.asyncio
async def test_get_top_market_cap_stocks_success(mock_quotations):
    mock_quotations._env = MagicMock()
    mock_quotations._env.is_paper_trading = False

    mock_top_response = {
        "rt_cd": "0",
        "output": [
            {"iscd": "005930", "stck_avls": "500000000000"}
        ]
    }
    mock_quotations.call_api = AsyncMock(return_value=mock_top_response)
    mock_quotations.get_stock_name_by_code = AsyncMock(return_value="삼성전자")

    result = await mock_quotations.get_top_market_cap_stocks_code("0000", count=1)

    assert result == {
        "rt_cd": "0",
        "output": [
            {
                "code": "005930",
                "market_cap": 500000000000
            }
        ]
    }

@pytest.mark.asyncio
async def test_get_top_market_cap_stocks_failure(mock_quotations):
    mock_quotations.call_api = AsyncMock(return_value={
        "rt_cd": "1",  # 실패 코드
        "msg1": "시가총액 조회 실패",
        "output": None
    })

    result = await mock_quotations.get_top_market_cap_stocks_code("0000", count=1)

    assert result == {
        "rt_cd": "1",
        "msg1": "시가총액 조회 실패",
        "output": []
    }
@pytest.mark.asyncio
async def test_get_filtered_stocks_by_momentum(mock_quotations):
    # 시총 상위 mock 데이터
    mock_quotations.get_top_market_cap_stocks_code = AsyncMock(return_value={
        "output": [
            {"isu_cd": "A0001", "acc_trdvol": "300000"},
            {"isu_cd": "A0002", "acc_trdvol": "100000"}
        ]
    })

    # 전일 종가 및 거래량 mock
    mock_quotations.get_previous_day_info = MagicMock(side_effect=[
        {"prev_close": 8000, "prev_volume": 100000},  # symbol 0001
        {"prev_close": 10000, "prev_volume": 100000}  # symbol 0002
    ])

    # 현재가 요약 mock
    mock_quotations.get_price_summary = AsyncMock(side_effect=[
        {"symbol": "0001", "open": 8000, "current": 9000, "change_rate": 12.5},
        {"symbol": "0002", "open": 10000, "current": 10800, "change_rate": 8.0}
    ])

    results = await mock_quotations.get_filtered_stocks_by_momentum(
        count=2, min_change_rate=10.0, min_volume_ratio=2.0
    )

    assert len(results) == 1
    assert results[0]["symbol"] == "0001"
    assert results[0]["change_rate"] >= 10.0
    assert results[0]["current_volume"] / results[0]["prev_volume"] >= 2.0

@pytest.mark.asyncio
async def test_get_stock_info_by_code_success(mock_quotations):
    mock_output = {
        "hts_kor_isnm": "삼성전자",
        "stck_prpr_smkl_amt": "500000000000"
    }

    mock_quotations.call_api = AsyncMock(return_value={"rt_cd": "0", "output": mock_output})
    result = await mock_quotations.get_stock_info_by_code("005930")
    assert result == mock_output

@pytest.mark.asyncio
async def test_get_stock_info_by_code_failure(mock_quotations):
    mock_quotations.call_api = AsyncMock(return_value={"rt_cd": "1", "output": None})
    result = await mock_quotations.get_stock_info_by_code("005930")
    assert result == {}
    mock_quotations.logger.warning.assert_called_once()

@pytest.mark.asyncio
async def test_get_market_cap_success(mock_quotations):
    mock_quotations.get_stock_info_by_code = AsyncMock(return_value={
        "stck_prpr_smkl_amt": "123456789000"
    })
    result = await mock_quotations.get_market_cap("005930")
    assert result == 123456789000

@pytest.mark.asyncio
async def test_get_market_cap_failure_invalid_format(mock_quotations):
    mock_quotations.get_stock_info_by_code = AsyncMock(return_value={
        "stck_prpr_smkl_amt": "INVALID"
    })
    result = await mock_quotations.get_market_cap("005930")
    assert result == 0
    mock_quotations.logger.warning.assert_called_once()

@pytest.mark.asyncio
async def test_get_market_cap_conversion_error(mock_quotations):
    mock_quotations.get_stock_info_by_code = AsyncMock(return_value={
        "stck_prpr_smkl_amt": "invalid_number"
    })
    result = await mock_quotations.get_market_cap("005930")
    assert result == 0
    mock_quotations.logger.warning.assert_called_once()

@pytest.mark.asyncio
async def test_get_market_cap_failure_missing_key(mock_quotations):
    mock_quotations.get_stock_info_by_code = AsyncMock(return_value={})  # no market cap field

    result = await mock_quotations.get_market_cap("005930")
    assert result == 0


# @pytest.mark.asyncio
# async def test_get_stock_name_by_code_success(mock_quotations):
#     async def mock_info_method(_):
#         return {"hts_kor_isnm": "삼성전자"}
#
#     mock_quotations.get_stock_info_by_code = mock_info_method
#
#     result = await mock_quotations.get_stock_name_by_code("005930")
#
#     assert result == "삼성전자"
#
#
# @pytest.mark.asyncio
# async def test_get_stock_name_by_code_empty(mock_quotations):
#     mock_quotations.get_stock_info_by_code = AsyncMock(return_value={})  # no name field
#
#     result = await mock_quotations.get_stock_name_by_code("005930")
#
#     assert result == ""
#     mock_quotations.logger.warning.assert_called_once()  # 로깅 호출 여부 검사
#
#
# @pytest.mark.asyncio
# async def test_get_stock_name_by_code_key_missing(mock_quotations):
#     mock_quotations.get_stock_info_by_code = AsyncMock(return_value={"other_key": "value"})  # `hts_kor_isnm` 키 없음
#
#     result = await mock_quotations.get_stock_name_by_code("005930")
#     assert result == ""
#     mock_quotations.logger.warning.assert_called_once()  # 로깅 호출 여부만 검사


@pytest.mark.asyncio
async def test_get_top_market_cap_stocks_success_revised(mock_quotations):
    mock_api_response = {
        "rt_cd": "0",
        "msg1": "SUCCESS",
        "output": [
            {"iscd": "005930", "mksc_shrn_iscd": "005930", "stck_avls": "500,000,000,000"},  # 삼성전자
            {"iscd": "000660", "mksc_shrn_iscd": "000660", "stck_avls": "120,000,000,000"}  # SK하이닉스
        ]
    }
    mock_quotations.call_api = AsyncMock(return_value=mock_api_response)

    def mock_get_stock_name(code):
        return {
            "005930": "삼성전자",
            "000660": "SK하이닉스"
        }.get(code, "알 수 없는 종목")

    mock_quotations.get_stock_name_by_code = AsyncMock(side_effect=mock_get_stock_name)

    result = await mock_quotations.get_top_market_cap_stocks_code("0000", count=2)

    assert isinstance(result, dict)
    assert result["rt_cd"] == "0"
    assert isinstance(result["output"], list)
    assert len(result["output"]) == 2

    output = result["output"]
    assert output[0]["code"] == "005930"
    assert output[0]["market_cap"] == 500000000000
    assert output[1]["code"] == "000660"
    assert output[1]["market_cap"] == 120000000000

    mock_quotations.call_api.assert_called_once()
    args, kwargs = mock_quotations.call_api.call_args
    assert args[0] == "GET"
    assert args[1] == "/uapi/domestic-stock/v1/ranking/market-cap"
    assert kwargs["params"]["fid_input_iscd"] == "0000"
    assert kwargs["retry_count"] == 1


@pytest.mark.asyncio
async def test_get_top_market_cap_stocks_failure_rt_cd_not_zero(mock_quotations):
    mock_quotations.call_api = AsyncMock(return_value={
        "rt_cd": "1",  # 실패 코드
        "msg1": "API 호출 실패",
        "output": []
    })

    result = await mock_quotations.get_top_market_cap_stocks_code("0000", count=1)

    assert result == {
        "rt_cd": "1",
        "msg1": "API 호출 실패",
        "output": []
    }
    mock_quotations.logger.warning.assert_called_once()  # 로깅 호출 여부 검사


@pytest.mark.asyncio
async def test_get_top_market_cap_stocks_count_validation(mock_quotations):
    mock_quotations.call_api = AsyncMock()
    mock_quotations.get_stock_name_by_code = AsyncMock()
    mock_quotations.logger.warning = MagicMock()

    # 1. count가 0 이하인 경우
    result_zero = await mock_quotations.get_top_market_cap_stocks_code("0000", count=0)
    assert result_zero == {
        "rt_cd": "1",
        "msg1": "요청된 count가 0 이하입니다. count=0",
        "output": []
    }
    mock_quotations.call_api.assert_not_called()
    mock_quotations.logger.warning.assert_called_once()

    mock_quotations.logger.reset_mock()
    mock_quotations.call_api.reset_mock()

    result_negative = await mock_quotations.get_top_market_cap_stocks_code("0000", count=-5)
    assert result_negative == {
        "rt_cd": "1",
        "msg1": "요청된 count가 0 이하입니다. count=-5",
        "output": []
    }
    mock_quotations.call_api.assert_not_called()
    mock_quotations.logger.warning.assert_called_once()

    # 2. count가 30을 초과하는 경우
    mock_quotations.logger.reset_mock()
    mock_quotations.call_api.reset_mock()

    mock_api_response_large = {
        "rt_cd": "0",
        "output": [{"iscd": f"{i:06d}", "stck_avls": f"{1000000000 + i}"} for i in range(40)]
    }
    mock_quotations.call_api.return_value = mock_api_response_large

    result_exceed_max = await mock_quotations.get_top_market_cap_stocks_code("0000", count=50)

    assert isinstance(result_exceed_max, dict)
    assert result_exceed_max["rt_cd"] == "0"
    assert len(result_exceed_max["output"]) == 30
    mock_quotations.call_api.assert_called_once()
    mock_quotations.logger.warning.assert_called_once()



@pytest.mark.asyncio
async def test_get_filtered_stocks_by_momentum_price_summary_failure(mock_quotations):
    # 시가총액 상위 mock 데이터 설정
    mock_quotations.get_top_market_cap_stocks_code = AsyncMock(return_value={
        "output": [
            {"iscd": "005930", "acc_trdvol": "100000"},
            {"iscd": "000660", "acc_trdvol": "50000"}
        ]
    })

    # get_previous_day_info는 동기 함수이므로 MagicMock 사용
    mock_quotations.get_previous_day_info = MagicMock(return_value={
        "prev_close": 100, "prev_volume": 1000
    })

    # 가격 요약 정보 실패 시 None 반환
    mock_quotations.get_price_summary = AsyncMock(return_value=None)

    results = await mock_quotations.get_filtered_stocks_by_momentum(
        count=1, min_change_rate=0, min_volume_ratio=0
    )

    assert results == []
    mock_quotations.logger.warning.assert_called_once()
    mock_quotations.logger.error.assert_not_called()


@pytest.mark.asyncio
async def test_get_filtered_stocks_by_momentum_prev_day_info_failure(mock_quotations):
    # 시가총액 상위 종목 mock 설정
    mock_quotations.get_top_market_cap_stocks_code = AsyncMock(return_value={
        "output": [
            {"isu_cd": "A0001", "acc_trdvol": "100000"}
        ]
    })

    # 전일 정보가 실패한 경우 (0으로 반환)
    mock_quotations.get_previous_day_info = MagicMock(return_value={
        "prev_close": 0, "prev_volume": 0
    })

    # 가격 요약은 정상 반환되나, 전일 정보가 실패한 경우에는 종목이 필터링되지 않음
    mock_quotations.get_price_summary = AsyncMock(return_value={
        "symbol": "0001", "open": 100, "current": 110, "change_rate": 10.0
    })

    results = await mock_quotations.get_filtered_stocks_by_momentum(
        count=1, min_change_rate=0, min_volume_ratio=0
    )

    assert results == []
    mock_quotations.logger.warning.assert_called_once()  # 로깅 호출 여부만 검사


# @pytest.mark.asyncio
# async def test_get_stock_name_by_code_no_info(mock_quotations):
#     mock_quotations.get_stock_info_by_code = AsyncMock(return_value={})  # 정보 없음
#
#     result = await mock_quotations.get_stock_name_by_code("005930")
#
#     assert result == ""
#     mock_quotations.logger.warning.assert_called_once()  # 로깅 호출 여부만 검사


@pytest.mark.asyncio
async def test_get_filtered_stocks_by_momentum_no_top_stocks_output():
    """
    Line 174-176: get_filtered_stocks_by_momentum에서 get_top_market_cap_stocks 응답에 'output' 키가 없을 경우
    """
    mock_logger = MagicMock()
    mock_config = {
        "tr_ids": {
            "quotations": {
                "inquire_price": "dummy-tr-id",
                "top_market_cap": "dummy-tr-id"
            }
        },
        "custtype": "P"
    }
    quotations = KoreaInvestApiQuotations(
        base_url="https://mock-base",
        headers={},
        config=mock_config,
        token_manager=MagicMock(),
        logger=mock_logger
    )

    # get_top_market_cap_stocks가 'output' 키가 없는 딕셔너리를 반환하는 경우
    quotations.get_top_market_cap_stocks_code = AsyncMock(return_value={"rt_cd": "0"})

    results = await quotations.get_filtered_stocks_by_momentum(
        count=10, min_change_rate=5.0, min_volume_ratio=1.5
    )

    assert results == []
    mock_logger.error.assert_called_once()  # 로깅 메시지 내용 대신 로깅 호출 여부만 검사


@pytest.mark.asyncio
async def test_get_price_summary_no_response(mock_quotations):
    # 1. get_current_price가 빈 딕셔너리를 반환하는 경우
    mock_quotations.get_current_price = AsyncMock(return_value={})

    result = await mock_quotations.get_price_summary("005930")
    assert result == {
        "symbol": "005930",
        "open": 0,
        "current": 0,
        "change_rate": 0.0
    }
    mock_quotations.logger.warning.assert_called_once()

    # 2. 'output' 키가 없는 딕셔너리를 반환하는 경우
    mock_quotations.logger.reset_mock()
    mock_quotations.get_current_price = AsyncMock(return_value={"rt_cd": "0"})

    result = await mock_quotations.get_price_summary("005930")
    assert result == {
        "symbol": "005930",
        "open": 0,
        "current": 0,
        "change_rate": 0.0
    }
    mock_quotations.logger.warning.assert_called_once()


@pytest.mark.asyncio
async def test_get_top_market_cap_stocks_item_missing_keys(mock_quotations):
    """
    Line 131-132: get_top_market_cap_stocks에서 item에 'iscd' 또는 'stck_avls'가 없을 경우
    """
    mock_api_response = {
        "rt_cd": "0",
        "output": [
            {"iscd": "005930", "stck_avls": "500,000,000,000"},  # 정상
            {"mksc_shrn_iscd": "000660"},  # stck_avls 없음
            {"stck_avls": "100,000,000,000"},  # iscd/mksc_shrn_iscd 없음
            {"iscd": "000770", "stck_avls": "INVALID"}  # 유효하지 않은 시총
        ]
    }

    mock_quotations.call_api = AsyncMock(return_value=mock_api_response)
    mock_quotations.get_stock_name_by_code = AsyncMock(side_effect=lambda code: f"이름_{code}")

    result = await mock_quotations.get_top_market_cap_stocks_code("0000", count=4)
    output = result["output"]  # ✅ 리스트 추출

    assert len(output) == 2  # '005930'은 정상, '000770'은 INVALID → 0 처리
    assert output[0]["code"] == "005930"
    assert output[0]["market_cap"] == 500000000000
    assert output[1]["code"] == "000770"
    assert output[1]["market_cap"] == 0



@pytest.mark.asyncio
async def test_get_previous_day_info_success():
    """
    Line 149-160: get_previous_day_info 함수 전체 커버
    """
    mock_logger = MagicMock()
    mock_config = {
        "api_key": "dummy-key",
        "api_secret_key": "dummy-secret",
        "base_url": "https://mock-base",
        "tr_ids": {
            "quotations": {
                "inquire_daily_itemchartprice": "FHKST03010100"  # 실제 TR ID와 유사하게 설정
            }
        },
        "custtype": "P"
    }

    quotations = KoreaInvestApiQuotations(
        base_url=mock_config["base_url"],
        headers={},
        config=mock_config,
        token_manager=MagicMock(),
        logger=mock_logger
    )

    # _client.request 모킹 (InvestAPIBase에서 사용하는 client)
    mock_response_json = {
        "rt_cd": "0",
        "output": {
            "stck_clpr": "95000",  # 전일 종가
            "acml_vol": "150000"  # 전일 거래량
        }
    }
    mock_client_response = MagicMock()
    mock_client_response.json.return_value = mock_response_json

    # _client 속성 자체를 모킹 (InvestAPIBase의 내부 동작)
    quotations._client = MagicMock()
    quotations._client.request.return_value = mock_client_response

    # get_previous_day_info는 async가 아님
    result = quotations.get_previous_day_info("005930")

    assert result == {
        "prev_close": 95000.0,
        "prev_volume": 150000
    }
    quotations._client.request.assert_called_once_with(
        "get",
        "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
        {
            "fid_cond_mrkt_div_code": "J",
            "fid_input_iscd": "005930",
        }
    )

@pytest.mark.asyncio
async def test_get_previous_day_info_success(mock_quotations):
    """
    get_previous_day_info 함수의 정상 응답 시 전체 로직 커버
    """
    mock_response_json = {
        "rt_cd": "0",
        "output": {
            "stck_clpr": "95000",  # 전일 종가
            "acml_vol": "150000"  # 전일 거래량
        }
    }
    mock_client_response = MagicMock()
    mock_client_response.json.return_value = mock_response_json

    mock_quotations._client = MagicMock()
    mock_quotations._client.request.return_value = mock_client_response

    result = mock_quotations.get_previous_day_info("005930")

    assert result == {
        "prev_close": 95000.0,
        "prev_volume": 150000
    }
    mock_quotations._client.request.assert_called_once_with(
        "get",
        "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
        {
            "fid_cond_mrkt_div_code": "J",
            "fid_input_iscd": "005930",
        }
    )


@pytest.mark.asyncio
async def test_get_previous_day_info_missing_output_keys(mock_quotations):
    """
    get_previous_day_info - 응답에 필요한 키가 없을 경우를 커버
    """
    mock_client_response = MagicMock()
    mock_client_response.json.return_value = {"rt_cd": "0", "output": {}}
    mock_quotations._client = MagicMock()
    mock_quotations._client.request.return_value = mock_client_response

    result = mock_quotations.get_previous_day_info("005930")

    assert result == {
        "prev_close": 0,
        "prev_volume": 0
    }
    mock_quotations.logger.error.assert_called_once()


@pytest.mark.asyncio
async def test_get_filtered_stocks_by_momentum_no_top_stocks_output(mock_quotations):
    """
    get_top_market_cap_stocks 응답에 'output' 키가 없을 경우
    """
    # get_top_market_cap_stocks가 'output' 키가 없는 응답을 반환하도록 설정
    mock_quotations.get_top_market_cap_stocks_code = AsyncMock(return_value={"rt_cd": "0"})

    results = await mock_quotations.get_filtered_stocks_by_momentum(
        count=10, min_change_rate=5.0, min_volume_ratio=1.5
    )

    assert results == []
    mock_quotations.logger.error.assert_called_once()  # 로깅 호출 여부만 검사

@pytest.mark.asyncio
async def test_init_with_provided_headers(mock_quotations):
    # mock_quotations는 이미 기본 mock_config, mock_token_manager, mock_logger 등을 포함

    custom_headers = {
        "User-Agent": "MyTestAgent",
        "Content-Type": "application/xml"
    }

    # 새 인스턴스를 직접 생성 (fixture를 참조하되 헤더만 다르게)
    quotations = KoreaInvestApiQuotations(
        base_url=mock_quotations._base_url,
        headers=custom_headers,
        config=mock_quotations._config,
        token_manager=mock_quotations.token_manager,
        logger=mock_quotations.logger
    )

    for key, val in custom_headers.items():
        assert quotations._headers.get(key) == val


@pytest.mark.asyncio
async def test_get_price_summary_with_invalid_response(mock_quotations):
    # get_current_price가 "output" 키가 없는 응답을 반환
    mock_quotations.get_current_price = AsyncMock(return_value={})

    result = await mock_quotations.get_price_summary("005930")

    assert result["symbol"] == "005930"
    assert result["open"] == 0
    assert result["current"] == 0
    assert result["change_rate"] == 0.0
    mock_quotations.logger.warning.assert_called_once()



@pytest.mark.asyncio
async def test_get_market_cap_with_invalid_string(mock_quotations):
    # stck_prpr_smkl_amt가 숫자가 아닌 경우 → ValueError 발생
    mock_quotations.get_stock_info_by_code = AsyncMock(return_value={
        "stck_prpr_smkl_amt": "NotANumber"
    })

    result = await mock_quotations.get_market_cap("005930")
    assert result == 0
    mock_quotations.logger.warning.assert_called_once()


@pytest.mark.asyncio
async def test_get_previous_day_info_missing_required_keys(mock_quotations):
    mock_response_json_missing_keys = {
        "rt_cd": "0",
        "output": {
            "other_key": "some_value"
        }
    }
    mock_client_response = MagicMock()
    mock_client_response.json.return_value = mock_response_json_missing_keys
    mock_quotations._client = MagicMock()
    mock_quotations._client.request.return_value = mock_client_response

    result = mock_quotations.get_previous_day_info("005930")

    assert result == {"prev_close": 0, "prev_volume": 0}
    mock_quotations.logger.error.assert_called_once()


@pytest.mark.asyncio
async def test_get_previous_day_info_value_error(mock_quotations):
    mock_response_json_invalid_price = {
        "rt_cd": "0",
        "output": {
            "stck_clpr": "INVALID_PRICE",
            "acml_vol": "100000"
        }
    }
    mock_client_response = MagicMock()
    mock_client_response.json.return_value = mock_response_json_invalid_price
    mock_quotations._client = MagicMock()
    mock_quotations._client.request.return_value = mock_client_response

    result = mock_quotations.get_previous_day_info("005930")
    assert result == {"prev_close": 0, "prev_volume": 0}
    mock_quotations.logger.error.assert_called_once()

    mock_quotations.logger.reset_mock()

    mock_response_json_invalid_volume = {
        "rt_cd": "0",
        "output": {
            "stck_clpr": "10000",
            "acml_vol": "INVALID_VOLUME"
        }
    }
    mock_client_response.json.return_value = mock_response_json_invalid_volume
    result_vol_error = mock_quotations.get_previous_day_info("005930")
    assert result_vol_error == {"prev_close": 0, "prev_volume": 0}
    mock_quotations.logger.error.assert_called_once()

@pytest.mark.asyncio
async def test_get_price_summary_parsing_error(mock_quotations):
    """
    get_current_price 응답의 가격 데이터가 숫자가 아닐 때,
    ValueError/TypeError를 처리하고 기본값을 반환하는지 테스트합니다.
    """
    # Arrange
    # 현재가(stck_prpr)에 숫자로 변환 불가능한 문자열을 넣어 예외 상황을 만듭니다.
    mock_quotations.get_current_price = AsyncMock(return_value={
        "output": {
            "stck_oprc": "10000",
            "stck_prpr": "INVALID_PRICE"  # int() 변환 시 ValueError 발생
        }
    })

    # Act
    result = await mock_quotations.get_price_summary("005930")

    # Assert
    # 1. 예외가 발생했을 때 반환하기로 약속된 기본 딕셔너리가 반환되었는지 확인합니다.
    assert result == {
        "symbol": "005930",
        "open": 0,
        "current": 0,
        "change_rate": 0.0
    }

    # 2. 예외 상황에 대한 경고 로그가 기록되었는지 확인합니다.
    mock_quotations.logger.warning.assert_called_once()

    # 3. 로그 메시지에 "가격 데이터 파싱 실패"가 포함되어 있는지 확인 (더 상세한 검증)
    log_message = mock_quotations.logger.warning.call_args[0][0]
    assert "가격 데이터 파싱 실패" in log_message

@pytest.mark.asyncio
async def test_inquire_daily_itemchartprice_success_day(mocker):
    # 1. Setup
    mock_headers = {"Authorization": "Bearer dummy"}
    mock_config = {
        "tr_ids": {
            "daily_itemchartprice_day": "FHKST03010100"
        },
        "custtype": "P"
    }
    mock_token_manager = MagicMock()
    mock_logger = MagicMock()

    api = KoreaInvestApiQuotations(
        base_url="https://mock.api",
        headers=mock_headers,
        config=mock_config,
        token_manager=mock_token_manager,
        logger=mock_logger
    )

    # 2. call_api mock
    mocked_response = {"rt_cd": "0", "output": [{"date": "20250708"}]}
    mocker.patch.object(api, "call_api", return_value=mocked_response)

    # 3. Call method
    result = await api.inquire_daily_itemchartprice(
        stock_code="005930", date="20250708", fid_period_div_code="D"
    )

    # 4. 검증
    assert result == [{"date": "20250708"}]
    mock_logger.error.assert_not_called()
    mock_logger.critical.assert_not_called()

@pytest.fixture
def api_instance(mocker):
    mock_headers = {"Authorization": "Bearer dummy"}
    mock_config = {
        "tr_ids": {
            "daily_itemchartprice_day": "TRID-D",
            "daily_itemchartprice_minute": "TRID-M"
        },
        "custtype": "P"
    }
    mock_token_manager = MagicMock()
    mock_logger = MagicMock()

    api = KoreaInvestApiQuotations(
        base_url="https://mock.api",
        headers=mock_headers,
        config=mock_config,
        token_manager=mock_token_manager,
        logger=mock_logger
    )

    return api, mock_logger


@pytest.mark.asyncio
async def test_inquire_daily_itemchartprice_unsupported_period_code(mocker, api_instance):
    api, mock_logger = api_instance

    # 응답은 정상적으로 반환되도록 mock 처리
    mocker.patch.object(api, "call_api", return_value={"rt_cd": "0", "output": []})

    result = await api.inquire_daily_itemchartprice("005930", "20250708", fid_period_div_code="X")

    assert result == []  # output이 빈 리스트로 반환됨
    mock_logger.error.assert_called_once()
    assert "지원하지 않는 fid_period_div_code" in mock_logger.error.call_args[0][0]


@pytest.mark.asyncio
async def test_inquire_daily_itemchartprice_call_api_none(mocker, api_instance):
    api, mock_logger = api_instance

    mocker.patch.object(api, "call_api", return_value=None)

    result = await api.inquire_daily_itemchartprice("005930", "20250708", fid_period_div_code="D")

    assert result is None
    mock_logger.error.assert_called_once()
    assert "API 응답 비정상" in mock_logger.error.call_args[0][0]


@pytest.mark.asyncio
async def test_inquire_daily_itemchartprice_missing_tr_id_in_config(mocker):
    mock_headers = {"Authorization": "Bearer dummy"}
    mock_config = {
        "tr_ids": {
            # deliberately omit "daily_itemchartprice_day"
            "daily_itemchartprice_minute": "TRID-M"
        },
        "custtype": "P"
    }
    mock_token_manager = MagicMock()
    mock_logger = MagicMock()

    api = KoreaInvestApiQuotations(
        base_url="https://mock.api",
        headers=mock_headers,
        config=mock_config,
        token_manager=mock_token_manager,
        logger=mock_logger
    )

    result = await api.inquire_daily_itemchartprice("005930", "20250708", fid_period_div_code="D")

    assert result is None
    mock_logger.critical.assert_called_once()
    assert "TR_ID 설정을 찾을 수 없습니다" in mock_logger.critical.call_args[0][0]


@pytest.mark.asyncio
async def test_inquire_daily_itemchartprice_with_minute_code_logs_debug(mocker):
    mock_headers = {"Authorization": "Bearer dummy"}
    mock_config = {
        "tr_ids": {
            "daily_itemchartprice_minute": "TRID-M"
        },
        "custtype": "P"
    }
    mock_token_manager = MagicMock()
    mock_logger = MagicMock()

    # API 인스턴스 생성
    api = KoreaInvestApiQuotations(
        base_url="https://mock.api",
        headers=mock_headers,
        config=mock_config,
        token_manager=mock_token_manager,
        logger=mock_logger
    )

    # call_api 모킹
    mock_response = {"rt_cd": "0", "output": [{"dummy": "data"}]}
    api.call_api = AsyncMock(return_value=mock_response)

    # 함수 호출
    result = await api.inquire_daily_itemchartprice("005930", "20250708", fid_period_div_code="M")

    # 검증
    mock_logger.debug.assert_called_once_with("현재 _config['tr_ids'] 내용: {'daily_itemchartprice_minute': 'TRID-M'}")
    assert result == [{"dummy": "data"}]
