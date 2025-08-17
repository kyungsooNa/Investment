import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from brokers.korea_investment.korea_invest_quotations_api import KoreaInvestApiQuotations
from common.types import (
    ResCommonResponse, ErrorCode,
    ResPriceSummary, ResTopMarketCapApiItem, ResDailyChartApiItem,
    ResStockFullInfoApiOutput  # 추가될 수 있는 타입들
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
        stock_code="005930", start_date="20250708",end_date="20250708", fid_period_div_code="D"
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

# @TODO 분봉 조회 API 잘못됨.

# @pytest.mark.asyncio
# async def test_inquire_daily_itemchartprice_with_minute_code_logs_debug(mock_quotations):
#     # 필수 필드 포함한 mock 응답
#
#     mock_response = ResCommonResponse(
#         rt_cd="0",
#         msg1="정상처리",
#         data=[
#             {
#                 "stck_bsop_date": "20250708",
#                 "stck_oprc": "950",
#                 "stck_hgpr": "1010",
#                 "stck_lwpr": "940",
#                 "stck_clpr": "1000",
#                 "acml_vol": "100"
#             }
#         ]
#     )
#
#     mock_quotations.call_api = AsyncMock(return_value=mock_response)
#
#     result_common = await mock_quotations.inquire_daily_itemchartprice("005930", start_date="20250708", end_date="20250708", fid_period_div_code="M")
#
#     # 검증
#     mock_quotations._logger.debug.assert_called()
#     assert result_common.rt_cd == ErrorCode.SUCCESS.value
#     assert result_common.data == [
#         ResDailyChartApiItem(
#             stck_bsop_date="20250708",
#             stck_oprc="950",
#             stck_hgpr="1010",
#             stck_lwpr="940",
#             stck_clpr="1000",
#             acml_vol="100"
#         )
#     ]


@pytest.mark.asyncio
async def test_inquire_daily_itemchartprice_parses_output2(mocker, mock_quotations):
    payload = {
        "msg1": "정상처리 되었습니다.",
        "msg_cd": "MCA00000",
        "rt_cd": "0",
        "output1": {"stck_shrn_iscd": "005930", "hts_kor_isnm": "삼성전자"},
        "output2": [
            {"stck_bsop_date": "20250814", "stck_oprc": "71900", "stck_hgpr": "71900",
             "stck_lwpr": "71200", "stck_clpr": "71600", "acml_vol": "11946122"}
        ]
    }
    # call_api 모킹
    mocker.patch.object(
        mock_quotations, "call_api",
        return_value=ResCommonResponse(rt_cd="0", msg1="ok", data=payload)
    )

    res = await mock_quotations.inquire_daily_itemchartprice(
        "005930", start_date="20250814",end_date="20250814", fid_period_div_code="D"
    )
    assert res.rt_cd == "0"
    assert isinstance(res.data, list) and len(res.data) == 1
    bar = res.data[0]
    get = (lambda k: getattr(bar, k, None) if not isinstance(bar, dict) else bar.get(k))
    assert get("stck_bsop_date") == "20250814"
    assert get("stck_clpr") == "71600"


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


# @pytest.mark.asyncio
# async def test_get_stock_news_success(mock_quotations):
#     mock_quotations.call_api = AsyncMock(return_value=ResCommonResponse(
#         rt_cd="0", msg1="Success", data={"news": ["기사1", "기사2"]}
#     ))
#     result = await mock_quotations.get_stock_news("005930")
#     assert result.rt_cd == "0"
#     mock_quotations._logger.info.assert_called_once()


# @pytest.mark.asyncio
# async def test_get_top_foreign_buying_stocks_success(mock_quotations):
#     mock_quotations.call_api = AsyncMock(return_value=ResCommonResponse(
#         rt_cd="0", msg1="Success", data={"foreign": "data"}
#     ))
#     result = await mock_quotations.get_top_foreign_buying_stocks()
#     assert result.rt_cd == "0"
#     mock_quotations._logger.info.assert_called_once()


@pytest.mark.asyncio
async def test_get_etf_info_success(mock_quotations):
    mock_quotations.call_api = AsyncMock(return_value=ResCommonResponse(
        rt_cd="0", msg1="Success", data={"etf": "info"}
    ))
    result = await mock_quotations.get_etf_info("ETF12345")
    assert result.rt_cd == "0"
    mock_quotations._logger.info.assert_called_once()
