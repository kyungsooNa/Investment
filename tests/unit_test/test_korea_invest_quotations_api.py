import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from brokers.korea_investment.korea_invest_quotations_api import KoreaInvestApiQuotations
from common.types import (
    ResCommonResponse, ErrorCode,
    ResPriceSummary, ResMomentumStock, ResMarketCapStockItem,
    ResStockFullInfoApiOutput, ResTopMarketCapApiItem, ResDailyChartApiItem,
    ResAccountBalanceApiOutput, ResStockOrderApiOutput, ResStockFullInfoApiOutput  # 추가될 수 있는 타입들
)
from dataclasses import fields
from typing import List
from types import SimpleNamespace


def make_stock_info_response(rt_cd="0", price="10000", market_cap="123456789000",
                             open_price="900") -> ResCommonResponse:
    base_fields = {
        f.name: "" for f in fields(ResStockFullInfoApiOutput)
        if f.name not in {"stck_prpr", "stck_llam", "stck_oprc"}
    }
    stock_info = ResStockFullInfoApiOutput(
        stck_prpr=price,
        stck_llam=market_cap,
        stck_oprc=open_price,
        **base_fields
    )
    return ResCommonResponse(
        rt_cd=rt_cd,
        msg1="성공",
        data=stock_info
    )


def make_call_api_response(
        rt_cd=ErrorCode.SUCCESS.value, msg1="정상 처리",
        price="1000", market_cap="500000000000", open_price="900"
) -> ResCommonResponse:
    base_fields = {
        f.name: "" for f in fields(ResStockFullInfoApiOutput)
        if f.name not in {"stck_prpr", "stck_llam", "stck_oprc"}
    }
    output = ResStockFullInfoApiOutput(
        stck_prpr=price,
        stck_llam=market_cap,
        stck_oprc=open_price,
        **base_fields
    )

    return ResCommonResponse(
        rt_cd=rt_cd,
        msg1=msg1,
        data=output.__dict__  # 또는 data=output 자체도 가능 (타입에 따라)
    )


@pytest.fixture(scope="function")
def mock_quotations():
    mock_logger = MagicMock()
    mock_env = MagicMock()
    mock_config = {
        "api_key": "dummy-key",
        "api_secret_key": "dummy-secret",
        "base_url": "https://mock-base",
        "tr_ids": {
            "quotations": {
                "inquire_price": "dummy-tr-id",
                "top_market_cap": "dummy-tr-id",
                "search_info": "dummy-tr-id",
                "daily_itemchartprice_day": "dummy-tr-id",
                "daily_itemchartprice_minute": "dummy-tr-id",
                "inquire_daily_itemchartprice": "dummy-tr-id",
                "asking_price": "dummy-tr-id",
                "time_conclude": "dummy-tr-id",
                "search_stock": "dummy-tr-id",
                "ranking_rise": "dummy-tr-id",
                "ranking_fall": "dummy-tr-id",
                "ranking_volume": "dummy-tr-id",
                "ranking_foreign": "dummy-tr-id",
                "item_news": "dummy-tr-id",
                "etf_info": "dummy-tr-id",
            }
        },
        "custtype": "P",
        'paths':
            {
                "search_info": "/mock/search-info",
                "inquire_price": "/mock/inquire-price",
                "market_cap": "/mock/market-cap",
                "inquire_daily_itemchartprice": "/mock/itemchartprice",
                "asking_price": "/mock/asking-price",
                "time_conclude": "/mock/time-conclude",
                "search_stock": "/mock/search-stock",
                "ranking_rise": "/mock/ranking-rise",
                "ranking_fall": "/mock/ranking-fall",
                "ranking_volume": "/mock/ranking-volume",
                "ranking_foreign": "/mock/ranking-foreign",
                "item_news": "/mock/item-news",
                "etf_info": "/mock/etf-info",
            },
        'params':
            {
                'fid_div_cls_code': 2,
                'screening_code': '20174'
            },
        'market_code': 'J',
    }

    return KoreaInvestApiQuotations(
        env=mock_env,
        logger=mock_logger
    )


@pytest.mark.asyncio
async def test_get_price_summary(mock_quotations):
    # 1. ResStockFullInfoApiOutput 객체 생성 (dict처럼 감싸기 위함)
    mock_output = ResStockFullInfoApiOutput.from_dict({
        "stck_oprc": "10000",
        "stck_prpr": "11000",
        "prdy_ctrt": "10.0",
        "bstp_kor_isnm": "전자업종"
    })

    # 2. 반환값 설정: dict 구조로 감싸기
    mock_quotations.get_current_price = AsyncMock(return_value=ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value,
        msg1="성공",
        data={"output": mock_output}  # ✅ dict 구조에 맞게 mock
    ))

    # 테스트 실행
    result_common_response = await mock_quotations.get_price_summary("005930")

    # 5. 결과 검증
    # get_price_summary는 이제 ResCommonResponse를 반환하므로, 이를 검증해야 합니다.
    assert result_common_response.rt_cd == ErrorCode.SUCCESS.value
    assert result_common_response.msg1 == "정상 처리되었습니다."  # get_price_summary 성공 시 메시지

    # 실제 데이터는 'data' 필드에 있습니다.
    actual_price_summary_data: ResPriceSummary = result_common_response.data
    assert actual_price_summary_data == ResPriceSummary(  # ResPriceSummary TypedDict 인스턴스로 비교
        symbol="005930",
        open=10000,
        current=11000,
        change_rate=10.0,
        prdy_ctrt=10.0  # prdy_ctrt 필드도 검증에 포함
    )

    # get_current_price가 올바른 인자로 호출되었는지 확인 (선택 사항)
    mock_quotations.get_current_price.assert_called_once_with("005930")


@pytest.mark.asyncio
async def test_get_price_summary_open_price_zero(mock_quotations):
    # Mock 반환 값을 ResCommonResponse 형식으로 변경
    mock_output = ResStockFullInfoApiOutput.from_dict({
        "stck_oprc": "0",
        "stck_prpr": "11000",
        "prdy_ctrt": "10.0",
        "bstp_kor_isnm": "전자업종"
    })

    # 2. 반환값 설정: dict 구조로 감싸기
    mock_quotations.get_current_price = AsyncMock(return_value=ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value,
        msg1="성공",
        data={"output": mock_output}  # ✅ dict 구조에 맞게 mock
    ))

    result_common = await mock_quotations.get_price_summary("005930")
    # ResCommonResponse의 성공 여부 확인
    assert result_common.rt_cd == ErrorCode.SUCCESS.value
    assert result_common.msg1 == "정상 처리되었습니다."

    # 실제 데이터는 data 필드에 ResPriceSummary 형태로 있음
    result: ResPriceSummary = result_common.data
    assert result.symbol == "005930"
    assert result.open == 0
    assert result.current == 11000
    assert result.change_rate == 0.0  # 시가가 0이면 등락률도 0 처리
    assert result.prdy_ctrt == 10.0  # prdy_ctrt도 확인


@pytest.mark.asyncio
async def test_get_price_summary_missing_keys(mock_quotations):
    # Mock 반환 값을 ResCommonResponse 형식으로 변경
    # data 필드에 필수 필드가 누락된 경우를 시뮬레이션
    mock_output = ResStockFullInfoApiOutput.from_dict({
        "some_other_key": "value"
    })

    # 2. 반환값 설정: dict 구조로 감싸기
    mock_quotations.get_current_price = AsyncMock(return_value=ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value,
        msg1="성공",
        data={"output": mock_output}  # ✅ dict 구조에 맞게 mock
    ))

    result_common = await mock_quotations.get_price_summary("005930")

    # get_price_summary 내부에서 필수 가격 데이터 누락을 감지하고 PARSING_ERROR를 반환할 것으로 기대
    assert result_common.rt_cd == ErrorCode.PARSING_ERROR.value
    assert result_common.data is None

    mock_quotations._logger.warning.assert_called_once()  # 경고 로깅 확인


@pytest.mark.asyncio
async def test_get_price_summary_invalid_response(mock_quotations):
    # mock_quotations.get_current_price가 None을 반환하는 경우
    # 이는 get_current_price 내부에서 ErrorCode.NETWORK_ERROR를 반환하는 시나리오
    mock_quotations.get_current_price = AsyncMock(return_value=ResCommonResponse(
        rt_cd=ErrorCode.NETWORK_ERROR.value,
        msg1="API 응답 실패 (네트워크)",
        data=None
    ))

    result_common = await mock_quotations.get_price_summary("005930")

    # get_price_summary는 get_current_price가 실패 응답을 반환하면 그 응답을 그대로 전달

    assert result_common.rt_cd == ErrorCode.API_ERROR.value
    assert "get_current_price 실패" in result_common.msg1
    assert result_common.data is None
    # 이 경우 get_price_summary 내부에서 추가 warning 로깅은 발생하지 않음 (get_current_price에서 이미 했으므로)
    # mock_quotations._logger.warning.assert_called_once() -> get_current_price 내부 로깅 확인은 해당 테스트에서 수행


@pytest.mark.asyncio
async def test_get_top_market_cap_stocks_success(mock_quotations):
    mock_quotations.call_api = AsyncMock(return_value=ResCommonResponse(
        rt_cd="0",
        msg1="SUCCESS",
        data={
            "output": [
                {"iscd": "005930", "mksc_shrn_iscd": "005930", "stck_avls": "500000000000"},
                {"iscd": "000660", "mksc_shrn_iscd": "000660", "stck_avls": "120000000000"}
            ]
        }
    ))

    result_common = await mock_quotations.get_top_market_cap_stocks_code("0000", count=2)

    # ResCommonResponse의 성공 여부 확인
    assert result_common.rt_cd == ErrorCode.SUCCESS.value
    assert result_common.msg1 == "시가총액 상위 종목 조회 성공"
    assert isinstance(result_common.data, list)
    assert len(result_common.data) == 2

    # 실제 데이터는 'data' 필드에 List[ResTopMarketCapApiItem] 형태로 있습니다.
    output_data: List[ResTopMarketCapApiItem] = result_common.data
    assert output_data[0].mksc_shrn_iscd == "005930"  # iscd 또는 mksc_shrn_iscd 중 하나 사용
    assert output_data[0].stck_avls == "500000000000"
    assert output_data[1].mksc_shrn_iscd == "000660"
    assert output_data[1].stck_avls == "120000000000"

    mock_quotations.call_api.assert_called_once()
    args, kwargs = mock_quotations.call_api.call_args
    assert args[0] == "GET"
    assert kwargs["params"]["fid_input_iscd"] == "0000"
    assert kwargs["retry_count"] == 1


@pytest.mark.asyncio
async def test_get_top_market_cap_stocks_failure(mock_quotations):
    mock_quotations.call_api = AsyncMock(return_value=ResCommonResponse(
        rt_cd="1",
        msg1="시가총액 조회 실패",
        data=None
    ))

    result_common = await mock_quotations.get_top_market_cap_stocks_code("0000", count=1)

    # ResCommonResponse의 실패 여부 확인
    assert result_common.rt_cd == ErrorCode.API_ERROR.value  # API에서 온 실패 코드
    assert result_common.msg1 == "시가총액 조회 실패"
    assert result_common.data == []  # 실패 시 data는 빈 리스트
    mock_quotations._logger.warning.assert_called_once()


@pytest.mark.asyncio
async def test_get_filtered_stocks_by_momentum(mock_quotations):
    # 시총 상위 mock 데이터 (ResCommonResponse 형식)
    mock_quotations.get_top_market_cap_stocks_code = AsyncMock(return_value=ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value,
        msg1="성공",
        data=[
            ResTopMarketCapApiItem(
                iscd="0001",
                mksc_shrn_iscd="0001",
                stck_avls="500000000000",
                data_rank="1",
                hts_kor_isnm="삼성전자",
                acc_trdvol="300000"
            ),
            ResTopMarketCapApiItem(
                iscd="0002",
                mksc_shrn_iscd="0002",
                stck_avls="300000000000",
                data_rank="2",
                hts_kor_isnm="SK하이닉스",
                acc_trdvol="100000"
            )
        ]
    ))

    # 전일 종가 및 거래량 mock (ResCommonResponse 형식)
    # ResCommonResponse의 data 필드에 {"prev_close": ..., "prev_volume": ...} 형태
    mock_quotations.get_previous_day_info = MagicMock(side_effect=[
        ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="성공", data={"prev_close": 8000.0, "prev_volume": 100000}),
        ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="성공", data={"prev_close": 10000.0, "prev_volume": 100000})
    ])

    # 현재가 요약 mock (ResCommonResponse 형식)
    # ResCommonResponse의 data 필드에 ResPriceSummary 형태
    mock_quotations.get_price_summary = AsyncMock(side_effect=[
        ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="성공",
                          data=ResPriceSummary(symbol="0001", open=8000, current=9000, change_rate=12.5,
                                               prdy_ctrt=12.5)),
        ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="성공",
                          data=ResPriceSummary(symbol="0002", open=10000, current=10800, change_rate=8.0,
                                               prdy_ctrt=8.0))
    ])

    results_common = await mock_quotations.get_filtered_stocks_by_momentum(
        count=2, min_change_rate=10.0, min_volume_ratio=2.0
    )

    assert results_common.rt_cd == ErrorCode.SUCCESS.value
    assert results_common.msg1 == "모멘텀 종목 필터링 성공"
    assert isinstance(results_common.data, list)

    results: List[ResMomentumStock] = results_common.data
    assert len(results) == 1
    assert results[0].symbol == "0001"
    assert results[0].change_rate >= 10.0
    assert results[0].current_volume / results[0].prev_volume >= 2.0


@pytest.mark.asyncio
async def test_get_stock_info_by_code_success(mock_quotations):
    mock_quotations.call_api = AsyncMock(return_value=make_call_api_response())

    result_common = await mock_quotations.get_stock_info_by_code("005930")

    assert result_common.rt_cd == ErrorCode.SUCCESS.value
    assert result_common.msg1 == "종목 정보 조회 성공"
    assert isinstance(result_common.data, ResStockFullInfoApiOutput)

    stock_info = result_common.data
    assert stock_info.stck_prpr == "1000"  # 현재가
    assert stock_info.stck_llam == "500000000000"  # 시가총액


@pytest.mark.asyncio
async def test_get_stock_info_by_code_failure(mock_quotations):
    mock_quotations.call_api = AsyncMock(
        return_value=make_call_api_response(rt_cd=ErrorCode.API_ERROR.value, msg1="종목 조회 실패"))
    result_common = await mock_quotations.get_stock_info_by_code("005930")

    assert result_common.rt_cd == ErrorCode.API_ERROR.value  # API에서 온 실패 코드
    assert result_common.data is None
    mock_quotations._logger.warning.assert_called_once()


@pytest.mark.asyncio
async def test_get_stock_info_by_code_parsing_error(mock_quotations):
    # 필수 필드가 누락된 mock output (ResStockFullInfoApiOutput에 정의되지 않은 필드)
    mock_quotations.call_api = AsyncMock(return_value=ResCommonResponse(
        rt_cd="0",
        msg1="SUCCESS",
        data={"some_unknown_key": "value"}
    ))

    result_common = await mock_quotations.get_stock_info_by_code("005930")

    assert result_common.rt_cd == ErrorCode.PARSING_ERROR.value
    assert "종목 정보 응답 형식 오류" in result_common.msg1
    assert result_common.data is None
    mock_quotations._logger.error.assert_called_once()


@pytest.mark.asyncio
async def test_get_market_cap_success(mock_quotations):
    # get_stock_info_by_code가 ResCommonResponse를 반환하도록 Mock
    mock_quotations.get_stock_info_by_code = AsyncMock(side_effect=[
        make_stock_info_response(rt_cd="0", price="10000", market_cap="123456789000"),
        ResCommonResponse(rt_cd="0", msg1="시가총액 조회 성공", data=None)
    ])

    result_common = await mock_quotations.get_market_cap("005930")

    assert result_common.rt_cd == ErrorCode.SUCCESS.value
    assert result_common.msg1 == "시가총액 조회 성공"
    assert result_common.data == 123456789000


@pytest.mark.asyncio
async def test_get_market_cap_failure_invalid_format(mock_quotations):
    # get_stock_info_by_code가 ResCommonResponse를 반환하도록 Mock
    mock_quotations.get_stock_info_by_code = AsyncMock(return_value=make_stock_info_response(
        rt_cd=ErrorCode.SUCCESS.value,
        price="1000",
        market_cap="INVALID",  # ✅ 여기서 의도한 파싱 실패 유도
        open_price="900"
    ))
    result_common = await mock_quotations.get_market_cap("005930")

    assert result_common.rt_cd == ErrorCode.PARSING_ERROR.value
    assert "시가총액 정보 없음 또는 형식 오류" in result_common.msg1
    assert result_common.data == 0
    mock_quotations._logger.warning.assert_called_once()


@pytest.mark.asyncio
async def test_get_market_cap_conversion_error(mock_quotations):
    mock_quotations.get_stock_info_by_code = AsyncMock(return_value=make_stock_info_response(
        rt_cd="0",
        market_cap="invalid_number"  # ✅ 변환 실패 유도
    ))

    result_common = await mock_quotations.get_market_cap("005930")

    assert result_common.rt_cd == ErrorCode.PARSING_ERROR.value
    assert result_common.data == 0
    mock_quotations._logger.warning.assert_called_once()


@pytest.mark.asyncio
async def test_get_market_cap_failure_missing_key(mock_quotations):
    mock_quotations.get_stock_info_by_code = AsyncMock(return_value=make_stock_info_response(
        rt_cd="0",
        market_cap=""  # 누락 대신 빈 값 처리
    ))
    result_common = await mock_quotations.get_market_cap("005930")
    assert result_common.rt_cd == ErrorCode.PARSING_ERROR.value
    assert "시가총액 정보 없음 또는 형식 오류" in result_common.msg1
    assert result_common.data == 0  # 실패 시 0 반환


# @pytest.mark.asyncio
# async def test_get_stock_name_by_code_success(mock_quotations):
#     # 이 함수는 BrokerAPIWrapper로 이동했으므로 KoreaInvestApiQuotations에서는 직접 테스트하지 않습니다.
#     # 만약 KoreaInvestApiQuotations에 이 함수가 남아있다면, ResCommonResponse를 반환하도록 수정해야 합니다.
#     pass


@pytest.mark.asyncio
async def test_get_top_market_cap_stocks_success_revised(mock_quotations):
    mock_quotations.call_api = AsyncMock(return_value=ResCommonResponse(
        rt_cd="0",
        msg1="SUCCESS",
        data={
            "output": [
                {"iscd": "005930", "mksc_shrn_iscd": "005930", "stck_avls": "500,000,000,000"},
                {"iscd": "000660", "mksc_shrn_iscd": "000660", "stck_avls": "120,000,000,000"}
            ]
        }
    ))

    # get_stock_name_by_code는 이 함수에서 호출되지 않으므로 mock 불필요.
    # def mock_get_stock_name(code):
    #     return {
    #         "005930": "삼성전자",
    #         "000660": "SK하이닉스"
    #     }.get(code, "알 수 없는 종목")
    # mock_quotations.get_stock_name_by_code = AsyncMock(side_effect=mock_get_stock_name)

    result_common = await mock_quotations.get_top_market_cap_stocks_code("0000", count=2)

    assert result_common.rt_cd == ErrorCode.SUCCESS.value  # Enum 값 사용
    assert result_common.msg1 == "시가총액 상위 종목 조회 성공"
    assert isinstance(result_common.data, list)
    assert len(result_common.data) == 2

    # 'data' 필드에서 실제 데이터 추출
    output_data: List[ResTopMarketCapApiItem] = result_common.data
    assert output_data[0].mksc_shrn_iscd == "005930"
    assert output_data[0].stck_avls == "500,000,000,000"  # 원본 문자열 유지 (변환은 상위 계층에서)
    assert output_data[1].mksc_shrn_iscd == "000660"
    assert output_data[1].stck_avls == "120,000,000,000"

    mock_quotations.call_api.assert_called_once()
    args, kwargs = mock_quotations.call_api.call_args
    assert args[0] == "GET"
    assert kwargs["params"]["fid_input_iscd"] == "0000"
    assert kwargs["retry_count"] == 1


@pytest.mark.asyncio
async def test_get_top_market_cap_stocks_failure_rt_cd_not_zero(mock_quotations):
    mock_quotations.call_api = AsyncMock(return_value=ResCommonResponse(
        rt_cd="1",  # 실패 코드
        msg1="API 호출 실패",
        data=[]
    ))

    result_common = await mock_quotations.get_top_market_cap_stocks_code("0000", count=1)

    assert result_common.rt_cd == ErrorCode.API_ERROR.value
    assert result_common.data == []
    mock_quotations._logger.warning.assert_called_once()


@pytest.mark.asyncio
async def test_get_top_market_cap_stocks_count_validation(mock_quotations):
    mock_quotations.call_api = AsyncMock()
    # mock_quotations.get_stock_name_by_code = AsyncMock() # 이 함수에서 호출되지 않음
    mock_quotations._logger.warning = MagicMock()

    # 1. count가 0 이하인 경우 (ResCommonResponse 반환 예상)
    result_zero_common = await mock_quotations.get_top_market_cap_stocks_code("0000", count=0)
    assert result_zero_common.rt_cd == ErrorCode.INVALID_INPUT.value  # Enum 값 사용
    assert result_zero_common.msg1 == "요청된 count가 0 이하입니다. count=0"
    assert result_zero_common.data == []
    mock_quotations.call_api.assert_not_called()
    mock_quotations._logger.warning.assert_called_once_with("요청된 count가 0 이하입니다. count=0")  # 정확한 메시지 확인

    mock_quotations._logger.reset_mock()
    mock_quotations.call_api.reset_mock()

    result_negative_common = await mock_quotations.get_top_market_cap_stocks_code("0000", count=-5)
    assert result_negative_common.rt_cd == ErrorCode.INVALID_INPUT.value  # Enum 값 사용
    assert result_negative_common.msg1 == "요청된 count가 0 이하입니다. count=-5"
    assert result_negative_common.data == []
    mock_quotations.call_api.assert_not_called()
    mock_quotations._logger.warning.assert_called_once_with("요청된 count가 0 이하입니다. count=-5")

    # 2. count가 30을 초과하는 경우 (ResCommonResponse 반환 예상)
    mock_quotations._logger.reset_mock()
    mock_quotations.call_api.reset_mock()

    mock_api_response_large = ResCommonResponse(
        rt_cd="0",
        msg1="SUCCESS",
        data={
            "output": [
                {"iscd": f"{i:06d}", "mksc_shrn_iscd": f"{i:06d}", "stck_avls": f"{1000000000 + i}"}
                for i in range(40)
            ]
        }
    )

    mock_quotations.call_api.return_value = mock_api_response_large

    result_exceed_max_common = await mock_quotations.get_top_market_cap_stocks_code("0000", count=50)

    assert result_exceed_max_common.rt_cd == ErrorCode.SUCCESS.value  # Enum 값 사용
    assert isinstance(result_exceed_max_common.data, list)
    assert len(result_exceed_max_common.data) == 30  # 30개로 제한
    mock_quotations.call_api.assert_called_once()
    mock_quotations._logger.warning.assert_called_once_with("요청 수 50는 최대 허용값 30을 초과하므로 30개로 제한됩니다.")


@pytest.mark.asyncio
async def test_get_filtered_stocks_by_momentum_price_summary_failure(mock_quotations):
    # 시가총액 상위 mock 데이터 (ResCommonResponse 형식)
    mock_quotations.get_top_market_cap_stocks_code = AsyncMock(return_value=ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value,
        msg1="성공",
        data=[
            SimpleNamespace(mksc_shrn_iscd="005930", acc_trdvol="100000"),
            SimpleNamespace(mksc_shrn_iscd="000660", acc_trdvol="50000")
        ]
    ))

    # 전일 종가 및 거래량 mock (ResCommonResponse 형식)
    mock_quotations.get_previous_day_info = MagicMock(return_value=ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="성공", data={"prev_close": 100.0, "prev_volume": 1000}
    ))

    # 가격 요약 정보 실패 시 (ResCommonResponse, 성공 코드가 아니거나 data=None)
    mock_quotations.get_price_summary = AsyncMock(return_value=ResCommonResponse(
        rt_cd=ErrorCode.API_ERROR.value,  # 실패 코드
        msg1="API 응답 없음",
        data=None
    ))

    results_common = await mock_quotations.get_filtered_stocks_by_momentum(
        count=1, min_change_rate=0, min_volume_ratio=0
    )

    assert results_common.rt_cd == ErrorCode.SUCCESS.value  # 필터링 자체는 성공했으니 성공
    assert results_common.data == []  # 결과는 비어있음
    mock_quotations._logger.warning.assert_called_once()  # get_price_summary 실패 경고 로그


@pytest.mark.asyncio
async def test_get_filtered_stocks_by_momentum_prev_day_info_failure(mock_quotations):
    # 시가총액 상위 종목 mock 설정 (ResCommonResponse 형식)
    mock_quotations.get_top_market_cap_stocks_code = AsyncMock(return_value=ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value,
        msg1="성공",
        data=[
            SimpleNamespace(mksc_shrn_iscd="0001", acc_trdvol="100000")
        ]
    ))

    # 전일 정보가 실패한 경우 (ResCommonResponse, 성공 코드가 아니거나 data=None)
    mock_quotations.get_previous_day_info = MagicMock(return_value=ResCommonResponse(
        rt_cd=ErrorCode.API_ERROR.value,  # 실패 코드
        msg1="전일 정보 조회 실패",
        data={"prev_close": 0.0, "prev_volume": 0}  # 실패 시에도 기본값으로 채워진 데이터 반환 가능성
    ))

    # 가격 요약은 정상 반환되나, 전일 정보가 실패한 경우에는 종목이 필터링되지 않음
    mock_quotations.get_price_summary = AsyncMock(return_value=ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="성공",
        data=ResPriceSummary(symbol="0001", open=100, current=110, change_rate=10.0, prdy_ctrt=10.0)
    ))

    results_common = await mock_quotations.get_filtered_stocks_by_momentum(
        count=1, min_change_rate=0, min_volume_ratio=0
    )

    assert results_common.rt_cd == ErrorCode.SUCCESS.value  # 필터링 자체는 성공
    assert results_common.data == []  # 결과는 비어있음
    mock_quotations._logger.warning.assert_called_once()  # get_previous_day_info 실패 경고 로그


@pytest.mark.asyncio
async def test_get_filtered_stocks_by_momentum_no_top_stocks_output(mock_quotations):
    """
    get_filtered_stocks_by_momentum에서 get_top_market_cap_stocks 응답에 'data' 키가 없거나 실패할 경우
    """
    # get_top_market_cap_stocks_code가 실패 응답을 반환하는 경우
    mock_quotations.get_top_market_cap_stocks_code = AsyncMock(return_value=ResCommonResponse(
        rt_cd=ErrorCode.API_ERROR.value,
        msg1="API 응답에 'output' 키 없음",
        data=None  # 또는 []
    ))

    results_common = await mock_quotations.get_filtered_stocks_by_momentum(
        count=10, min_change_rate=5.0, min_volume_ratio=1.5
    )

    assert results_common.rt_cd == ErrorCode.API_ERROR.value  # get_top_market_cap_stocks_code의 실패가 전파됨
    assert "시가총액 상위 종목 조회 실패" in results_common.msg1
    assert results_common.data == []  # 빈 리스트 반환
    mock_quotations._logger.error.assert_called_once()  # 로깅 메시지 내용 대신 로깅 호출 여부만 검사


#
# @pytest.mark.asyncio
# async def test_get_price_summary_no_response(mock_quotations):
#     # 1. None 응답
#     mock_output = None
#
#     # 2. 반환값 설정: dict 구조로 감싸기
#     mock_quotations.get_current_price = AsyncMock(return_value=ResCommonResponse(
#         rt_cd=ErrorCode.SUCCESS.value,
#         msg1="성공",
#         data={"output": mock_output}  # ✅ dict 구조에 맞게 mock
#     ))
#
#     result_common = await mock_quotations.get_price_summary("005930")
#     assert result_common.rt_cd == ErrorCode.API_ERROR.value
#     assert "API 응답 output 데이터 없음" in result_common.msg1
#     assert result_common.data is None
#     mock_quotations._logger.warning.assert_called_once()
#
#     # 2. 빈 dict 응답
#     mock_quotations._logger.reset_mock()
#     mock_quotations.get_current_price = AsyncMock(return_value=ResCommonResponse(
#         rt_cd=ErrorCode.SUCCESS.value,
#         msg1="성공했으나 output 필드 없음",
#         data={}
#     ))
#
#     result_common = await mock_quotations.get_price_summary("005930")
#     assert result_common.rt_cd == ErrorCode.API_ERROR.value  # ✅ 수정
#     assert "API 응답 output 데이터 없음" in result_common.msg1  # ✅ 수정
#     assert result_common.data is None
#     mock_quotations._logger.warning.assert_called_once()


@pytest.mark.asyncio
async def test_get_top_market_cap_stocks_item_missing_keys(mock_quotations):
    """
    get_top_market_cap_stocks에서 item에 'iscd' 또는 'stck_avls'가 없을 경우
    """
    mock_quotations.call_api = AsyncMock(return_value=ResCommonResponse(
        rt_cd="0",
        msg1="SUCCESS",
        data={
            "output": [
                {"iscd": "005930", "stck_avls": "500,000,000,000"},
                {"mksc_shrn_iscd": "000660"},  # stck_avls 없음
                {"stck_avls": "100,000,000,000"},  # iscd 없음
                {"iscd": "000770", "stck_avls": "INVALID"}  # 시총 파싱 실패 → 0으로 처리
            ]
        }
    ))

    # get_stock_name_by_code는 이 함수에서 호출되지 않으므로 mock 불필요.
    # mock_quotations.get_stock_name_by_code = AsyncMock(side_effect=lambda code: f"이름_{code}")

    result_common = await mock_quotations.get_top_market_cap_stocks_code("0000", count=4)
    assert result_common.rt_cd == ErrorCode.SUCCESS.value
    assert isinstance(result_common.data, list)

    output_data: List[ResTopMarketCapApiItem] = result_common.data
    # mksc_shrn_iscd가 없어도 iscd가 있으면 처리됨
    assert len(output_data) == 2  # '005930'은 정상, '000770'은 INVALID → 0 처리 (단 iscd는 유효)
    assert output_data[0].mksc_shrn_iscd == "005930"
    assert output_data[0].stck_avls == "500,000,000,000"  # raw string
    assert output_data[1].mksc_shrn_iscd == "000770"
    assert output_data[1].stck_avls == "INVALID"  # raw string


@pytest.mark.asyncio
async def test_get_previous_day_info_success():
    """
    Line 149-160: get_previous_day_info 함수 전체 커버
    """
    # mock_config = {
    #     "api_key": "dummy-key",
    #     "api_secret_key": "dummy-secret",
    #     "base_url": "https://mock-base",
    #     "tr_ids": {
    #         "quotations": {
    #             "inquire_daily_itemchartprice": "FHKST03010100"  # 실제 TR ID와 유사하게 설정
    #         }
    #     },
    #     "custtype": "P"
    # }

    quotations = KoreaInvestApiQuotations(
        env=MagicMock(),
        logger=MagicMock()
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
@patch("brokers.korea_investment.korea_invest_quotations_api.requests.get")
async def test_get_previous_day_info_success(mock_get, mock_quotations):  # fixture 활용
    """
    get_previous_day_info 함수의 정상 응답 시 전체 로직 커버
    """

    mock_response_json = {
        "rt_cd": "0",
        "output": {
            "stck_bsop_date": "20250712",
            "stck_oprc": "94000",
            "stck_hgpr": "95500",
            "stck_lwpr": "93000",
            "stck_clpr": "95000",
            "acml_vol": "150000"
        }
    }

    mock_response = MagicMock()
    mock_response.json.return_value = mock_response_json
    mock_get.return_value = mock_response  # ✅ 이 줄이 중요

    # get_previous_day_info는 동기 함수
    result_common = mock_quotations.get_previous_day_info("005930")

    assert result_common.rt_cd == ErrorCode.SUCCESS.value
    assert result_common.msg1 == "전일 정보 조회 성공"
    assert result_common.data == {
        "prev_close": 95000.0,
        "prev_volume": 150000
    }


@pytest.mark.asyncio
async def test_get_previous_day_info_missing_output_keys(mock_quotations, mocker):
    """
    get_previous_day_info - 응답에 필요한 키가 없을 경우를 커버
    """
    # ✅ 정상적인 응답 구조지만 필수 키 누락
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "rt_cd": "0",
        "output": {}  # 필드 없음
    }

    mocker.patch("requests.get", return_value=mock_response)

    result_common = mock_quotations.get_previous_day_info("005930")

    assert result_common.rt_cd == ErrorCode.MISSING_KEY.value  # ✅ "104"
    assert result_common.data == {
        "prev_close": 0,
        "prev_volume": 0
    }
    mock_quotations._logger.error.assert_called_once()


@pytest.mark.asyncio
async def test_get_filtered_stocks_by_momentum_no_top_stocks_output(mock_quotations):
    """
    get_top_market_cap_stocks 응답에 'output' 키가 없을 경우
    """
    # get_top_market_cap_stocks가 'output' 키가 없는 응답을 반환하도록 설정
    mock_quotations.get_top_market_cap_stocks_code = AsyncMock(return_value=ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value,
        msg1="성공",
        data=None  # 또는 [] 등 비어있는 구조로 설정
    ))

    results = await mock_quotations.get_filtered_stocks_by_momentum(
        count=10, min_change_rate=5.0, min_volume_ratio=1.5
    )

    assert results.rt_cd == ErrorCode.API_ERROR.value
    assert results.data == []
    mock_quotations._logger.warning.assert_called_once()


@pytest.mark.asyncio
async def test_get_price_summary_with_invalid_response(mock_quotations):
    # get_current_price가 비어 있는 dict를 반환 (비정상 응답 시뮬레이션)
    mock_quotations.get_current_price = AsyncMock(return_value=ResCommonResponse(
        rt_cd=ErrorCode.API_ERROR.value,
        msg1="응답 구조 비정상",
        data=None
    ))

    result = await mock_quotations.get_price_summary("005930")

    assert result.rt_cd == ErrorCode.API_ERROR.value
    assert result.data is None
    mock_quotations._logger.warning.assert_called_once()


@pytest.mark.asyncio
async def test_get_market_cap_with_invalid_string(mock_quotations):
    # stck_prpr_smkl_amt가 숫자가 아닌 경우 → ValueError 발생
    mock_quotations.get_stock_info_by_code = AsyncMock(return_value=ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value,
        msg1="정상 처리",
        data=SimpleNamespace(stck_llam="NotANumber")
    ))

    result = await mock_quotations.get_market_cap("005930")

    assert result.rt_cd == ErrorCode.PARSING_ERROR.value
    assert "형식 오류" in result.msg1
    assert result.data == 0
    mock_quotations._logger.warning.assert_called_once()


@pytest.mark.asyncio
@patch("brokers.korea_investment.korea_invest_quotations_api.requests.get")
async def test_get_previous_day_info_missing_required_keys(mock_get, mock_quotations):
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "rt_cd": "0",
        "output": {
            "stck_bsop_date": "20250715",
            "stck_oprc": "93000",
            "stck_hgpr": "94000",
            "stck_lwpr": "92000",
            "stck_clpr": "INVALID_PRICE",  # ✅ 의도된 파싱 오류
            "acml_vol": "100000"
        }
    }
    mock_get.return_value = mock_response

    result_common = mock_quotations.get_previous_day_info("005930")

    assert result_common.rt_cd == ErrorCode.PARSING_ERROR.value
    assert result_common.data == {"prev_close": 0, "prev_volume": 0}  # ✅ 수정
    mock_quotations._logger.error.assert_called_once()


@pytest.mark.asyncio
@patch("brokers.korea_investment.korea_invest_quotations_api.requests.get")  # 정확한 경로 사용
async def test_get_previous_day_info_value_error(mock_get, mock_quotations):  # fixture 활용
    mock_quotations._logger.reset_mock()

    mock_quotations._headers = {
        "authorization": "Bearer TEST_TOKEN",
        "content-type": "application/json"
    }

    # 거래량 파싱 오류
    mock_response_json = {
        "rt_cd": "0",
        "output": {
            "stck_bsop_date": "20250715",
            "stck_oprc": "93000",
            "stck_hgpr": "94000",
            "stck_lwpr": "92000",
            "stck_clpr": "10000",
            "acml_vol": "INVALID_VOLUME"  # ValueError 유발
        }
    }

    mock_response = MagicMock()
    mock_response.json.return_value = mock_response_json
    mock_get.return_value = mock_response  # ✅ 핵심: requests.get을 patch

    result_vol_error_common = mock_quotations.get_previous_day_info("005930")

    assert result_vol_error_common.rt_cd == ErrorCode.PARSING_ERROR.value
    assert "데이터 변환 실패" in result_vol_error_common.msg1
    assert result_vol_error_common.data == {"prev_close": 0, "prev_volume": 0}
    mock_quotations._logger.error.assert_called_once()


@pytest.mark.asyncio
async def test_get_price_summary_parsing_error(mock_quotations):
    """
    get_current_price 응답의 가격 데이터가 숫자가 아닐 때,
    ValueError/TypeError를 처리하고 기본값을 반환하는지 테스트합니다.
    """
    # Arrange
    # 현재가(stck_prpr)에 숫자로 변환 불가능한 문자열을 넣어 예외 상황을 만듭니다.
    mock_output = ResStockFullInfoApiOutput.from_dict({
        "stck_oprc": "0",
        "stck_prpr": "INVALID",
        "prdy_ctrt": "10.0",
        "bstp_kor_isnm": "전자업종"
    })

    # 2. 반환값 설정: dict 구조로 감싸기
    mock_quotations.get_current_price = AsyncMock(return_value=ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value,
        msg1="성공",
        data={"output": mock_output}  # ✅ dict 구조에 맞게 mock
    ))

    # Act
    result_common = await mock_quotations.get_price_summary("005930")

    # Assert
    # 1. 예외가 발생했을 때 반환하기로 약속된 ResCommonResponse가 반환되었는지 확인합니다.
    assert result_common.rt_cd == ErrorCode.PARSING_ERROR.value
    assert "가격 데이터 파싱 실패" in result_common.msg1
    assert result_common.data is None  # 파싱 실패 시 data는 None

    # 2. 예외 상황에 대한 경고 로그가 기록되었는지 확인합니다.
    mock_quotations._logger.warning.assert_called_once()


@pytest.mark.asyncio
async def test_inquire_daily_itemchartprice_success_day(mock_quotations):  # fixture 활용
    # 2. call_api mock
    mocked_response = ResCommonResponse(
        rt_cd="0",
        msg1="정상처리",
        data=[
            {
                "stck_bsop_date": "20250708",
                "stck_oprc": "980",
                "stck_hgpr": "1020",
                "stck_lwpr": "970",
                "stck_clpr": "1000",
                "acml_vol": "100"
            }
        ]
    )
    mock_quotations.call_api = AsyncMock(return_value=mocked_response)

    # 3. Call method
    result_common = await mock_quotations.inquire_daily_itemchartprice(
        stock_code="005930", date="20250708", fid_period_div_code="D"
    )

    # 4. 검증
    assert result_common.rt_cd == ErrorCode.SUCCESS.value
    assert result_common.msg1 == "일별/분봉 차트 데이터 조회 성공"
    assert isinstance(result_common.data, list)
    assert len(result_common.data) == 1
    assert (result_common.data[0] == ResDailyChartApiItem(
        stck_bsop_date="20250708",
        stck_oprc="980",
        stck_hgpr="1020",
        stck_lwpr="970",
        stck_clpr="1000",
        acml_vol="100"
    ))

    mock_quotations._logger.error.assert_not_called()
    mock_quotations._logger.critical.assert_not_called()


@pytest.mark.asyncio
async def test_inquire_daily_itemchartprice_unsupported_period_code(mock_quotations):
    mock_quotations.call_api = AsyncMock(return_value=ResCommonResponse(
        rt_cd="0",
        msg1="정상",
        data={}
    ))
    result_common = await mock_quotations.inquire_daily_itemchartprice("005930", "20250708", fid_period_div_code="X")

    assert result_common.rt_cd == ErrorCode.INVALID_INPUT.value  # Enum 값 사용
    assert "지원하지 않는 fid_period_div_code" in result_common.msg1
    assert result_common.data == []
    mock_quotations._logger.error.assert_called_once()


@pytest.mark.asyncio
async def test_inquire_daily_itemchartprice_call_api_none(mock_quotations):
    mock_quotations.call_api = AsyncMock(return_value=ResCommonResponse(
        rt_cd="0",
        msg1="정상",
        data=None
    ))

    result_common = await mock_quotations.inquire_daily_itemchartprice("005930", "20250708", fid_period_div_code="D")

    assert result_common.rt_cd != ErrorCode.SUCCESS.value  # Enum 값 사용
    assert result_common.data == []  # 실패 시 빈 리스트
    mock_quotations._logger.warning.assert_called_once()


#
# @pytest.mark.asyncio
# async def test_inquire_daily_itemchartprice_missing_tr_id_in_config(mock_quotations):
#     mock_quotations._config = {
#         "api_key": "dummy-key",
#         "api_secret_key": "dummy-secret",
#         "base_url": "https://mock-base",
#         "custtype": "P",
#         'paths':
#             {
#                 'search_info': 'test_path',
#                 'inquire_price': 'test_path',
#                 'market_cap': 'test_path',
#                 'inquire_daily_itemchartprice': 'test_path',
#                 'asking_price': 'test_path'
#             },
#         'params':
#             {
#                 'fid_div_cls_code': 2,
#                 'screening_code': '20174'
#             },
#         'market_code': 'J',
#     }
#     result_common = await mock_quotations.inquire_daily_itemchartprice("005930", "20250708", fid_period_div_code="D")
#
#     assert result_common.rt_cd== ErrorCode.INVALID_INPUT.value  # Enum 값 사용
#     assert "TR_ID 설정을 찾을 수 없습니다" in result_common.msg1
#     assert result_common.data == []
#     mock_quotations._logger.critical.assert_called_once()


@pytest.mark.asyncio
async def test_inquire_daily_itemchartprice_with_minute_code_logs_debug(mock_quotations):
    # 필수 필드 포함한 mock 응답

    mock_response = ResCommonResponse(
        rt_cd="0",
        msg1="정상처리",
        data=[
            {
                "stck_bsop_date": "20250708",
                "stck_oprc": "950",
                "stck_hgpr": "1010",
                "stck_lwpr": "940",
                "stck_clpr": "1000",
                "acml_vol": "100"
            }
        ]
    )

    mock_quotations.call_api = AsyncMock(return_value=mock_response)

    result_common = await mock_quotations.inquire_daily_itemchartprice("005930", "20250708", fid_period_div_code="M")

    # 검증
    mock_quotations._logger.debug.assert_called()
    assert result_common.rt_cd == ErrorCode.SUCCESS.value
    assert result_common.data == [
        ResDailyChartApiItem(
            stck_bsop_date="20250708",
            stck_oprc="950",
            stck_hgpr="1010",
            stck_lwpr="940",
            stck_clpr="1000",
            acml_vol="100"
        )
    ]


@pytest.mark.asyncio
async def test_get_current_price_success(mock_quotations):
    """
    get_current_price가 정상적인 응답을 반환하는 경우를 테스트합니다.
    """
    # 1. call_api를 모킹
    mock_quotations.call_api = AsyncMock(return_value=ResCommonResponse(
        rt_cd="0",
        msg1="정상",
        data={  # ✅ "output" 키로 감싸기
            "output": {
                "stck_oprc": "80000",
                "stck_prpr": "85000",
                "dmrs_val": "85100",  # askp1 → dmrs_val
                "dmsp_val": "84900"  # bidp1 → dmsp_val
            }
        }
    ))

    # 3. 메서드 호출
    result_common = await mock_quotations.get_current_price("005930")

    # 4. 검증
    output: ResStockFullInfoApiOutput = result_common.data["output"]

    assert isinstance(output, ResStockFullInfoApiOutput)
    assert output.stck_oprc == "80000"
    assert output.stck_prpr == "85000"
    assert output.dmsp_val == "84900"  # bidp1 → dmsp_val 로 매핑돼 있을 수 있음
    assert output.dmrs_val == "85100"  # askp1 → dmrs_val 로 매핑돼 있을 수 있음


@pytest.mark.asyncio
async def test_get_current_price_api_failure(mock_quotations):
    """
    get_current_price가 API 오류로 인해 None을 반환하는 경우
    """
    # call_api가 실패 (None 반환)
    mock_quotations.call_api = AsyncMock(return_value=ResCommonResponse(
        rt_cd=ErrorCode.NETWORK_ERROR.value,
        msg1="API 에러",
        data=None
    ))

    result_common = await mock_quotations.get_current_price("005930")

    assert result_common.rt_cd == ErrorCode.NETWORK_ERROR.value
    assert "API 에러" in result_common.msg1
    assert result_common.data is None
    mock_quotations._logger.warning.assert_called_once()


@pytest.mark.asyncio
async def test_get_asking_price_success(mock_quotations):
    mock_quotations.call_api = AsyncMock(return_value=ResCommonResponse(
        rt_cd="0", msg1="Success", data={"askp1": "50000", "bidp1": "49000"}
    ))

    result = await mock_quotations.get_asking_price("005930")

    assert result.rt_cd == "0"
    assert result.data["askp1"] == "50000"
    mock_quotations._logger.info.assert_called_once()


@pytest.mark.asyncio
async def test_get_time_concluded_prices_success(mock_quotations):
    mock_quotations.call_api = AsyncMock(return_value=ResCommonResponse(
        rt_cd="0", msg1="Success", data={"time": "0930", "price": "50000"}
    ))
    result = await mock_quotations.get_time_concluded_prices("005930")
    assert result.rt_cd == "0"
    mock_quotations._logger.info.assert_called_once()


@pytest.mark.asyncio
async def test_search_stocks_by_keyword_success(mock_quotations):
    mock_quotations.call_api = AsyncMock(return_value=ResCommonResponse(
        rt_cd="0", msg1="Success", data={"output": [{"code": "005930"}]}
    ))
    result = await mock_quotations.search_stocks_by_keyword("삼성")
    assert result.rt_cd == "0"
    mock_quotations._logger.info.assert_called_once()


@pytest.mark.asyncio
async def test_get_top_rise_fall_stocks_success(mock_quotations):
    mock_quotations.call_api = AsyncMock(return_value=ResCommonResponse(
        rt_cd="0", msg1="Success", data={"stocks": ["A", "B"]}
    ))
    result = await mock_quotations.get_top_rise_fall_stocks(rise=True)
    assert result.rt_cd == "0"
    mock_quotations._logger.info.assert_called_once()


@pytest.mark.asyncio
async def test_get_top_volume_stocks_success(mock_quotations):
    mock_quotations.call_api = AsyncMock(return_value=ResCommonResponse(
        rt_cd="0", msg1="Success", data={"volume": "1000000"}
    ))
    result = await mock_quotations.get_top_volume_stocks()
    assert result.rt_cd == "0"
    mock_quotations._logger.info.assert_called_once()


@pytest.mark.asyncio
async def test_get_stock_news_success(mock_quotations):
    mock_quotations.call_api = AsyncMock(return_value=ResCommonResponse(
        rt_cd="0", msg1="Success", data={"news": ["기사1", "기사2"]}
    ))
    result = await mock_quotations.get_stock_news("005930")
    assert result.rt_cd == "0"
    mock_quotations._logger.info.assert_called_once()


@pytest.mark.asyncio
async def test_get_top_foreign_buying_stocks_success(mock_quotations):
    mock_quotations.call_api = AsyncMock(return_value=ResCommonResponse(
        rt_cd="0", msg1="Success", data={"foreign": "data"}
    ))
    result = await mock_quotations.get_top_foreign_buying_stocks()
    assert result.rt_cd == "0"
    mock_quotations._logger.info.assert_called_once()


@pytest.mark.asyncio
async def test_get_etf_info_success(mock_quotations):
    mock_quotations.call_api = AsyncMock(return_value=ResCommonResponse(
        rt_cd="0", msg1="Success", data={"etf": "info"}
    ))
    result = await mock_quotations.get_etf_info("ETF12345")
    assert result.rt_cd == "0"
    mock_quotations._logger.info.assert_called_once()
