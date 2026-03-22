import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from brokers.korea_investment.korea_invest_quotations_api import KoreaInvestApiQuotations
from pydantic import ValidationError
from common.types import (
    ResCommonResponse, ErrorCode,
    ResPriceSummary, ResTopMarketCapApiItem, ResDailyChartApiItem,
    ResStockFullInfoApiOutput  # 추가될 수 있는 타입들
)
from typing import List
from types import SimpleNamespace


_BASE_DUMMY_DATA = {name: "0" for name in ResStockFullInfoApiOutput.model_fields}


def _create_dummy_output(overrides=None):
    """Pydantic 모델 유효성 검사를 통과하기 위해 더미 데이터를 채워 객체를 생성합니다."""
    data = _BASE_DUMMY_DATA.copy()
    if overrides:
        data.update(overrides)
    return ResStockFullInfoApiOutput.model_validate(data)


def make_stock_info_response(rt_cd="0", price="10000", market_cap="123456789000",
                             open_price="900") -> ResCommonResponse:
    stock_info = _create_dummy_output({
        "stck_prpr": price,
        "stck_llam": market_cap,
        "stck_oprc": open_price
    })
    return ResCommonResponse(
        rt_cd=rt_cd,
        msg1="성공",
        data=stock_info
    )


def make_call_api_response(
        rt_cd=ErrorCode.SUCCESS.value, msg1="정상 처리",
        price="1000", market_cap="500000000000", open_price="900"
) -> ResCommonResponse:
    output = _create_dummy_output({
        "stck_prpr": price,
        "stck_llam": market_cap,
        "stck_oprc": open_price
    })

    return ResCommonResponse(
        rt_cd=rt_cd,
        msg1=msg1,
        data=output.model_dump()  # 또는 data=output 자체도 가능 (타입에 따라)
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

    mock_env.active_config = mock_config

    mock_trid_provider = MagicMock()
    mock_trid_provider.quotations.return_value = "dummy-tr-id"
    mock_trid_provider.daily_itemchartprice.return_value = "dummy-tr-id"
    mock_trid_provider.time_itemchartprice.return_value = "dummy-tr-id"

    # httpx.AsyncClient 생성을 모킹하여 초기화 속도 개선 (실제 네트워크 연결 방지)
    with patch("brokers.korea_investment.korea_invest_api_base.httpx.AsyncClient"):
        api = KoreaInvestApiQuotations(
            env=mock_env,
            logger=mock_logger,
            trid_provider=mock_trid_provider
        )
    return api


@pytest.mark.asyncio
async def test_get_price_summary(mock_quotations):
    # 1. ResStockFullInfoApiOutput 객체 생성 (dict처럼 감싸기 위함)
    # 필수 필드를 모두 채우기 위해 make_call_api_response 로직과 유사하게 생성하거나
    # 모든 필드를 dummy로 채운 뒤 필요한 값만 덮어씌웁니다.
    mock_output = _create_dummy_output({
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
        prdy_ctrt=10.0,  # prdy_ctrt 필드도 검증에 포함
        new_high_low_status="-"
    )

    # get_current_price가 올바른 인자로 호출되었는지 확인 (선택 사항)
    mock_quotations.get_current_price.assert_called_once_with("005930")


@pytest.mark.asyncio
async def test_get_price_summary_open_price_zero(mock_quotations):
    # Mock 반환 값을 ResCommonResponse 형식으로 변경
    mock_output = _create_dummy_output({
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
    # Pydantic 모델은 필수 필드가 누락되면 생성 자체가 안되므로,
    # 여기서는 get_current_price가 성공했지만, 그 내부 데이터가 ResStockFullInfoApiOutput 형식이 아닌 경우(dict 등)를 가정하거나
    # 혹은 get_current_price가 파싱 에러를 리턴하는 상황을 테스트해야 합니다.
    # 하지만 get_price_summary 구현상 get_current_price의 결과(ResCommonResponse)를 받아서 처리하므로,
    # get_current_price가 성공했다면 data는 이미 ResStockFullInfoApiOutput 객체여야 합니다.

    # 2. 반환값 설정: dict 구조로 감싸기
    # 만약 get_current_price가 ResStockFullInfoApiOutput 객체를 반환하지 않고 이상한 dict를 반환한다면?
    # 하지만 get_current_price의 타입 힌트는 ResCommonResponse이고 data는 ResStockFullInfoApiOutput이어야 함.
    # 테스트 목적상 "필수 키 누락" 상황을 만들려면, get_current_price가 정상적인 ResStockFullInfoApiOutput을 반환하지 못하고
    # 에러를 냈거나, 혹은 ResStockFullInfoApiOutput 객체인데 특정 필드가 None인 경우(Optional 필드라면)를 테스트해야 함.
    # 그러나 ResStockFullInfoApiOutput의 필드들은 대부분 str이고 필수임.
    
    # 따라서 이 테스트는 "get_current_price가 파싱에 실패하여 PARSING_ERROR를 리턴하는 경우"를 테스트하는 것이 더 적절함.
    # 또는 get_current_price는 성공했으나 data가 None인 경우.
    
    mock_quotations.get_current_price = AsyncMock(return_value=ResCommonResponse(
        rt_cd=ErrorCode.PARSING_ERROR.value,
        msg1="파싱 에러",
        data=None
    ))

    result_common = await mock_quotations.get_price_summary("005930")

    assert result_common.rt_cd == ErrorCode.API_ERROR.value # get_price_summary는 하위 에러를 API_ERROR로 래핑하거나 그대로 리턴
    # 구현 확인: if response_common.rt_cd != ErrorCode.SUCCESS.value: return ... API_ERROR ...
    assert "get_current_price 실패" in result_common.msg1


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
    assert kwargs["retry_count"] == 3


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

    # 필수 필드 누락 시 PARSING_ERROR 반환 확인
    assert result_common.rt_cd == ErrorCode.PARSING_ERROR.value
    assert "종목 정보 응답 형식 오류" in result_common.msg1
    assert result_common.data is None


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
    assert kwargs["retry_count"] == 3


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
    get_current_price 응답은 성공했으나 가격 데이터가 숫자가 아닐 때,
    ValueError/TypeError를 처리하고 기본값을 반환하는지 테스트합니다.
    """
    # Arrange
    mock_output = _create_dummy_output({
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
        data={
            "output": _create_dummy_output({
                "stck_oprc": "80000",
                "stck_prpr": "85000",
                "dmrs_val": "85100",
                "dmsp_val": "84900"
            }).model_dump()
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
async def test_get_current_price_parsing_error(mock_quotations):
    """
    get_current_price: API 응답은 성공했으나 데이터 파싱(Pydantic 검증 등)에 실패하는 경우
    """
    # 1. call_api 모킹: 필수 필드가 누락된 데이터 반환
    # ResStockFullInfoApiOutput은 많은 필드를 요구하므로, 빈 dict나 일부만 있는 dict는 ValidationError를 유발함
    mock_quotations.call_api = AsyncMock(return_value=ResCommonResponse(
        rt_cd="0",
        msg1="정상",
        data={
            "output": {"invalid_field": "value"}  # 필수 필드 누락 -> ValidationError 유발
        }
    ))

    # 2. 메서드 호출
    result = await mock_quotations.get_current_price("005930")

    # 3. 검증
    assert result.rt_cd == ErrorCode.PARSING_ERROR.value
    assert "현재가 응답 데이터 파싱 실패" in result.msg1
    assert result.data is None
    mock_quotations._logger.error.assert_called_once()


@pytest.mark.asyncio
async def test_get_current_price_key_error(mock_quotations):
    """
    get_current_price: 응답 데이터에 'output' 키가 없는 경우 (KeyError 발생 시나리오)
    """
    mock_quotations.call_api = AsyncMock(return_value=ResCommonResponse(
        rt_cd="0",
        msg1="정상",
        data={"wrong_key": "value"}  # 'output' 키 없음 -> KeyError 유발
    ))

    result = await mock_quotations.get_current_price("005930")

    assert result.rt_cd == ErrorCode.PARSING_ERROR.value
    assert "현재가 응답 데이터 파싱 실패" in result.msg1
    mock_quotations._logger.error.assert_called_once()


@pytest.mark.asyncio
async def test_get_top_market_cap_stocks_validation_error(mock_quotations):
    """get_top_market_cap_stocks_code: 항목 생성 중 ValidationError 발생 시 건너뛰기 테스트"""
    # 1. 정상 데이터 1개, 에러 유발 데이터 1개
    data = {
        "output": [
            {"mksc_shrn_iscd": "005930", "stck_avls": "100"}, # 정상
            {"mksc_shrn_iscd": "000660", "stck_avls": "200"}  # 에러 유발용
        ]
    }
    
    mock_quotations.call_api = AsyncMock(return_value=ResCommonResponse(
        rt_cd="0", msg1="OK", data=data
    ))
    
    # ResTopMarketCapApiItem 생성자에서 ValidationError 발생 유도
    with patch("brokers.korea_investment.korea_invest_quotations_api.ResTopMarketCapApiItem") as MockItem:
        def side_effect(*args, **kwargs):
            if kwargs.get('mksc_shrn_iscd') == "000660":
                raise ValidationError.from_exception_data("Test", [])
            return MagicMock(mksc_shrn_iscd="005930", stck_avls="100", data_rank="1")
            
        MockItem.side_effect = side_effect
        
        result = await mock_quotations.get_top_market_cap_stocks_code("0000", count=2)
        
        # 에러 난 항목은 건너뛰고 정상 항목만 반환되어야 함
        assert result.rt_cd == ErrorCode.SUCCESS.value
        assert len(result.data) == 1
        assert result.data[0].mksc_shrn_iscd == "005930"
        mock_quotations._logger.warning.assert_called()


@pytest.mark.asyncio
async def test_get_top_rise_fall_stocks_validation_error(mock_quotations):
    """get_top_rise_fall_stocks: 항목 파싱 중 ValidationError 발생 시 에러 응답 반환 테스트"""
    data = {"output": [{"stck_shrn_iscd": "005930"}]}
    mock_quotations.call_api = AsyncMock(return_value=ResCommonResponse(rt_cd="0", msg1="OK", data=data))
    
    with patch("brokers.korea_investment.korea_invest_quotations_api.ResFluctuation.from_dict") as mock_from_dict:
        mock_from_dict.side_effect = ValidationError.from_exception_data("Test", [])
        
        result = await mock_quotations.get_top_rise_fall_stocks(rise=True)
        
        assert result.rt_cd == ErrorCode.PARSING_ERROR.value
        assert "등락률 응답 형식 오류" in result.msg1
        mock_quotations._logger.error.assert_called()


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

@pytest.mark.asyncio
async def test_get_price_summary_new_high_low_mismatch_warning(mock_quotations):
    """get_price_summary: new_high_low_status에 'vs'가 포함될 때 경고 로깅 테스트"""
    mock_output = _create_dummy_output({
        "stck_oprc": "10000", "stck_prpr": "11000", "prdy_ctrt": "10.0",
        "new_hgpr_lwpr_cls_code": "1 vs 4"  # 불일치 상태
    })
    mock_quotations.get_current_price = AsyncMock(return_value=ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="성공", data={"output": mock_output}
    ))
    
    await mock_quotations.get_price_summary("005930")
    
    mock_quotations._logger.warning.assert_called_once()
    assert "신고/신저가 불일치" in mock_quotations._logger.warning.call_args[0][0]

@pytest.mark.asyncio
async def test_get_market_cap_info_none(mock_quotations):
    """get_market_cap: get_stock_info_by_code가 data=None을 반환할 때"""
    mock_quotations.get_stock_info_by_code = AsyncMock(return_value=ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="성공", data=None
    ))
    
    result = await mock_quotations.get_market_cap("005930")
    
    assert result.rt_cd == ErrorCode.PARSING_ERROR.value
    assert "데이터가 None" in result.msg1

@pytest.mark.asyncio
async def test_get_top_market_cap_stocks_invalid_count(mock_quotations):
    """get_top_market_cap_stocks_code: count가 0 이하일 때"""
    result = await mock_quotations.get_top_market_cap_stocks_code("0000", count=0)
    assert result.rt_cd == ErrorCode.INVALID_INPUT.value
    assert "count가 0 이하" in result.msg1

@pytest.mark.asyncio
async def test_get_top_market_cap_stocks_parsing_error_in_loop(mock_quotations):
    """get_top_market_cap_stocks_code: 루프 내 파싱 오류 (KeyError)"""
    # Helper class to induce a TypeError on str() conversion
    class BadStrObject:
        def __str__(self):
            raise TypeError("Intentional type error for testing")

    mock_quotations.call_api = AsyncMock(return_value=ResCommonResponse(
        rt_cd="0", msg1="SUCCESS",
        # Pass the 'if' check with valid code and market_cap, but fail on another field
        data={"output": [{
            "mksc_shrn_iscd": "005930",
            "stck_avls": "1000000",
            "prdy_ctrt": BadStrObject() # This will raise TypeError when str() is called
        }]}
    ))
    
    result = await mock_quotations.get_top_market_cap_stocks_code("0000", count=1)
    
    # 파싱 오류가 발생한 항목은 건너뛰므로 최종 결과는 비어있어야 함
    assert result.rt_cd == ErrorCode.EMPTY_VALUES.value
    assert result.data == []
    mock_quotations._logger.warning.assert_called_once()
    assert "개별 항목 파싱 오류" in mock_quotations._logger.warning.call_args[0][0]

@pytest.mark.asyncio
async def test_get_top_market_cap_stocks_sorting_with_invalid_rank(mock_quotations):
    """get_top_market_cap_stocks_code: data_rank가 숫자가 아닐 때 정렬 테스트"""
    mock_quotations.call_api = AsyncMock(return_value=ResCommonResponse(
        rt_cd="0", msg1="SUCCESS",
        data={"output": [
            {"mksc_shrn_iscd": "A", "stck_avls": "100", "data_rank": "2"},
            {"mksc_shrn_iscd": "B", "stck_avls": "200", "data_rank": "invalid"}, # _to_int_safe가 default(큰 값) 반환
            {"mksc_shrn_iscd": "C", "stck_avls": "300", "data_rank": "1"},
        ]}
    ))
    
    result = await mock_quotations.get_top_market_cap_stocks_code("0000", count=3)
    
    assert result.rt_cd == ErrorCode.SUCCESS.value
    # 정렬 순서: C (rank 1), A (rank 2), B (rank invalid)
    assert [item.mksc_shrn_iscd for item in result.data] == ["C", "A", "B"]

@pytest.mark.asyncio
async def test_inquire_daily_itemchartprice_output_not_list(mock_quotations):
    """inquire_daily_itemchartprice: output이 list가 아닐 때"""
    mock_quotations.call_api = AsyncMock(return_value=ResCommonResponse(
        rt_cd="0", msg1="정상", data={"output": "not a list"}
    ))
    
    result = await mock_quotations.inquire_daily_itemchartprice("005930", "20250101")
    
    assert result.rt_cd == ErrorCode.SUCCESS.value
    # output_list가 [output]으로 변환되므로, 결과는 1개짜리 리스트
    assert result.data == [] # 파싱 실패로 빈 리스트가 될 것임

@pytest.mark.asyncio
async def test_inquire_daily_itemchartprice_item_parsing_error(mock_quotations):
    """inquire_daily_itemchartprice: 항목 파싱 중 TypeError 발생"""
    # dict가 아닌 항목을 전달하여 from_dict 내부에서 TypeError를 유발
    mock_quotations.call_api = AsyncMock(return_value=ResCommonResponse(
        rt_cd="0", msg1="정상", data={"output": ["not a dict"]}
    ))
    
    result = await mock_quotations.inquire_daily_itemchartprice("005930", "20250101")
    
    assert result.rt_cd == ErrorCode.SUCCESS.value
    assert result.data == [] # 파싱 실패 항목은 건너뜀
    mock_quotations._logger.warning.assert_called_once()
    assert "차트 데이터 항목 파싱 오류" in mock_quotations._logger.warning.call_args[0][0]

@pytest.mark.asyncio
async def test_get_price_summary_output_empty_dict(mock_quotations):
    """get_price_summary: output이 빈 딕셔너리일 때 (Line 150-152)"""
    mock_quotations.get_current_price = AsyncMock(return_value=ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data={"output": {}}
    ))
    
    result = await mock_quotations.get_price_summary("005930")
    
    assert result.rt_cd == ErrorCode.API_ERROR.value
    assert "output 데이터 없음" in result.msg1
    mock_quotations._logger.warning.assert_called()

@pytest.mark.asyncio
async def test_get_price_summary_status_vs(mock_quotations):
    """get_price_summary: status code에 'vs'가 포함된 경우 (Line 180)"""
    mock_output = _create_dummy_output({
        "stck_oprc": "1000", "stck_prpr": "1100", "prdy_ctrt": "10.0",
        "new_hgpr_lwpr_cls_code": "1 vs 2"
    })
    
    mock_quotations.get_current_price = AsyncMock(return_value=ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data={"output": mock_output}
    ))
    
    await mock_quotations.get_price_summary("005930")
    
    # 로그 확인
    args_list = mock_quotations._logger.warning.call_args_list
    assert any("신고/신저가 불일치" in str(args) for args in args_list)

@pytest.mark.asyncio
async def test_inquire_daily_itemchartprice_data_empty_list(mock_quotations):
    """inquire_daily_itemchartprice: data가 빈 리스트일 때 (Line 382-384)"""
    mock_quotations.call_api = AsyncMock(return_value=ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data=[]
    ))
    
    result = await mock_quotations.inquire_daily_itemchartprice("005930", "20250101")
    
    assert result.rt_cd == ErrorCode.MISSING_KEY.value
    assert "데이터가 비어있음" in result.msg1
    mock_quotations._logger.warning.assert_called()

@pytest.mark.asyncio
async def test_get_top_rise_fall_stocks_exception(mock_quotations):
    """get_top_rise_fall_stocks: 파싱 중 예외 발생 (Line 590)"""
    mock_quotations.call_api = AsyncMock(return_value=ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data="string_data" # dict가 아니므로 TypeError 유발
    ))
    
    result = await mock_quotations.get_top_rise_fall_stocks(rise=True)
    
    assert result.rt_cd == ErrorCode.PARSING_ERROR.value
    mock_quotations._logger.error.assert_called()

@pytest.mark.asyncio
async def test_get_multi_price_invalid_item(mock_quotations):
    """get_multi_price: 리스트 내 비정상 아이템 (Line 742)"""
    mock_quotations.call_api = AsyncMock(return_value=ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK", 
        data={"output": [{"stck_shrn_iscd": "005930", "stck_prpr": "1000"}, "string_item"]}
    ))
    
    result = await mock_quotations.get_multi_price(["005930"])
    
    assert len(result.data) == 1
    assert result.data[0]["stck_shrn_iscd"] == "005930"

@pytest.mark.asyncio
async def test_get_price_summary_output_empty_dict(mock_quotations):
    """get_price_summary: output이 빈 딕셔너리일 때 (Line 150-152)"""
    mock_quotations.get_current_price = AsyncMock(return_value=ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data={"output": {}}
    ))
    
    result = await mock_quotations.get_price_summary("005930")
    
    assert result.rt_cd == ErrorCode.API_ERROR.value
    assert "output 데이터 없음" in result.msg1
    mock_quotations._logger.warning.assert_called()

@pytest.mark.asyncio
async def test_get_price_summary_status_vs(mock_quotations):
    """get_price_summary: status code에 'vs'가 포함된 경우 (Line 180)"""
    mock_output = _create_dummy_output({
        "stck_oprc": "1000", "stck_prpr": "1100", "prdy_ctrt": "10.0",
        "new_hgpr_lwpr_cls_code": "1 vs 2"
    })
    
    mock_quotations.get_current_price = AsyncMock(return_value=ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data={"output": mock_output}
    ))
    
    await mock_quotations.get_price_summary("005930")
    
    # 로그 확인
    args_list = mock_quotations._logger.warning.call_args_list
    assert any("신고/신저가 불일치" in str(args) for args in args_list)

@pytest.mark.asyncio
async def test_inquire_daily_itemchartprice_data_empty_list(mock_quotations):
    """inquire_daily_itemchartprice: data가 빈 리스트일 때 (Line 382-384)"""
    mock_quotations.call_api = AsyncMock(return_value=ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data=[]
    ))
    
    result = await mock_quotations.inquire_daily_itemchartprice("005930", "20250101")
    
    assert result.rt_cd == ErrorCode.MISSING_KEY.value
    assert "데이터가 비어있음" in result.msg1
    mock_quotations._logger.warning.assert_called()

@pytest.mark.asyncio
async def test_get_top_rise_fall_stocks_exception(mock_quotations):
    """get_top_rise_fall_stocks: 파싱 중 예외 발생 (Line 590)"""
    mock_quotations.call_api = AsyncMock(return_value=ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data="string_data" # dict가 아니므로 TypeError 유발
    ))
    
    result = await mock_quotations.get_top_rise_fall_stocks(rise=True)
    
    assert result.rt_cd == ErrorCode.PARSING_ERROR.value
    mock_quotations._logger.error.assert_called()

@pytest.mark.asyncio
async def test_get_multi_price_invalid_item(mock_quotations):
    """get_multi_price: 리스트 내 비정상 아이템 (Line 742)"""
    mock_quotations.call_api = AsyncMock(return_value=ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK", 
        data={"output": [{"stck_shrn_iscd": "005930", "stck_prpr": "1000"}, "string_item"]}
    ))
    
    result = await mock_quotations.get_multi_price(["005930"])
    
    assert len(result.data) == 1
    assert result.data[0]["stck_shrn_iscd"] == "005930"

@pytest.mark.asyncio
async def test_get_asking_price_failure(mock_quotations):
    """get_asking_price: API 실패"""
    mock_quotations.call_api = AsyncMock(return_value=ResCommonResponse(
        rt_cd="1", msg1="Error"
    ))
    result = await mock_quotations.get_asking_price("005930")
    assert result.rt_cd == "1"
    mock_quotations._logger.warning.assert_called_once()

@pytest.mark.asyncio
async def test_get_time_concluded_prices_failure(mock_quotations):
    """get_time_concluded_prices: API 실패"""
    mock_quotations.call_api = AsyncMock(return_value=ResCommonResponse(
        rt_cd="1", msg1="Error"
    ))
    result = await mock_quotations.get_time_concluded_prices("005930")
    assert result.rt_cd == "1"
    mock_quotations._logger.warning.assert_called_once()

@pytest.mark.asyncio
async def test_get_etf_info_failure(mock_quotations):
    """get_etf_info: API 실패"""
    mock_quotations.call_api = AsyncMock(return_value=ResCommonResponse(
        rt_cd="1", msg1="Error"
    ))
    result = await mock_quotations.get_etf_info("122630")
    assert result.rt_cd == "1"
    mock_quotations._logger.warning.assert_called_once()

@pytest.mark.asyncio
async def test_get_price_summary_output_none(mock_quotations):
    """get_price_summary: get_current_price 응답의 output이 None일 때"""
    mock_quotations.get_current_price = AsyncMock(return_value=ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="성공", data={"output": None}
    ))
    
    result = await mock_quotations.get_price_summary("005930")
    
    assert result.rt_cd == ErrorCode.API_ERROR.value
    assert "API 응답 output 데이터 없음" in result.msg1

@pytest.mark.asyncio
async def test_get_price_summary_output_not_dataclass(mock_quotations):
    """get_price_summary: get_current_price 응답의 output이 dataclass가 아닐 때"""
    mock_quotations.get_current_price = AsyncMock(return_value=ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="성공", data={"output": "not a dataclass"}
    ))
    
    result = await mock_quotations.get_price_summary("005930")
    
    assert result.rt_cd == ErrorCode.WRONG_RET_TYPE.value
    assert "Wrong Ret Type" in result.msg1

@pytest.mark.asyncio
async def test_inquire_time_itemchartprice_success(mock_quotations):
    """inquire_time_itemchartprice: 성공적인 분봉 데이터 조회 테스트"""
    # Arrange
    api = mock_quotations
    stock_code = "005930"
    input_hour = "2025010110"

    mock_response_data = {
        "output2": [
            {"stck_cntg_hour": "100000", "stck_prpr": "70000"},
            {"stck_cntg_hour": "100100", "stck_prpr": "70100"},
        ]
    }
    api.call_api = AsyncMock(return_value=ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data=mock_response_data
    ))

    # Act
    result = await api.inquire_time_itemchartprice(stock_code, input_hour)

    # Assert
    api.call_api.assert_awaited_once()
    assert result.rt_cd == ErrorCode.SUCCESS.value
    assert result.msg1 == "분봉 차트 조회 성공"
    assert isinstance(result.data, list)
    assert len(result.data) == 2
    assert result.data[0]["stck_prpr"] == "70000"

@pytest.mark.asyncio
async def test_inquire_time_itemchartprice_api_failure(mock_quotations):
    """inquire_time_itemchartprice: API 호출 실패 시 에러 응답 반환 테스트"""
    # Arrange
    api = mock_quotations
    api.call_api = AsyncMock(return_value=ResCommonResponse(
        rt_cd=ErrorCode.API_ERROR.value, msg1="API Error", data=None
    ))

    # Act
    result = await api.inquire_time_itemchartprice("005930", "2025010110")

    # Assert
    assert result.rt_cd == ErrorCode.API_ERROR.value
    assert "분봉 차트 조회 실패" in result.msg1
    assert result.data == []
    api._logger.warning.assert_called_with("[분봉] 조회 실패: API Error")

@pytest.mark.asyncio
@pytest.mark.parametrize("data_key", ["output", "output1"])
async def test_inquire_time_itemchartprice_fallback_data_keys(mock_quotations, data_key):
    """inquire_time_itemchartprice: output2가 없을 때 output, output1 키로 폴백하는지 테스트"""
    # Arrange
    api = mock_quotations
    mock_response_data = {
        data_key: [{"stck_cntg_hour": "100000", "stck_prpr": "70000"}]
    }
    api.call_api = AsyncMock(return_value=ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data=mock_response_data
    ))

    # Act
    result = await api.inquire_time_itemchartprice("005930", "2025010110")

    # Assert
    assert result.rt_cd == ErrorCode.SUCCESS.value
    assert len(result.data) == 1
    assert result.data[0]["stck_prpr"] == "70000"

@pytest.mark.asyncio
async def test_inquire_time_dailychartprice_success(mock_quotations):
    """inquire_time_dailychartprice: 성공적인 일별 분봉 데이터 조회 테스트"""
    # Arrange
    api = mock_quotations
    stock_code = "005930"
    input_date = "20250101"
    input_hour = "100000"

    mock_response_data = {
        "output2": [
            {"stck_bsop_date": "20250101", "stck_cntg_hour": "100000", "stck_prpr": "70000"},
            {"stck_bsop_date": "20250101", "stck_cntg_hour": "100100", "stck_prpr": "70100"},
        ]
    }
    api.call_api = AsyncMock(return_value=ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data=mock_response_data
    ))

    # Act
    result = await api.inquire_time_dailychartprice(stock_code, input_hour, input_date)

    # Assert
    api.call_api.assert_awaited_once()
    assert result.rt_cd == ErrorCode.SUCCESS.value
    assert result.msg1 == "일변 분봉 조회 성공"
    assert isinstance(result.data, list)
    assert len(result.data) == 2
    assert result.data[0]["stck_prpr"] == "70000"

@pytest.mark.asyncio
async def test_inquire_time_dailychartprice_api_failure(mock_quotations):
    """inquire_time_dailychartprice: API 호출 실패 시 에러 응답 그대로 반환 테스트"""
    # Arrange
    api = mock_quotations
    error_response = ResCommonResponse(rt_cd=ErrorCode.API_ERROR.value, msg1="API Error", data=None)
    api.call_api = AsyncMock(return_value=error_response)

    # Act
    result = await api.inquire_time_dailychartprice("005930", "100000", "20250101")

    # Assert
    assert result == error_response

@pytest.mark.asyncio
async def test_inquire_time_dailychartprice_data_not_dict(mock_quotations):
    """inquire_time_dailychartprice: 응답 data가 dict가 아닐 때 빈 리스트 반환 테스트"""
    # Arrange
    api = mock_quotations
    api.call_api = AsyncMock(return_value=ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data="not a dict"
    ))

    # Act
    result = await api.inquire_time_dailychartprice("005930", "100000", "20250101")

    # Assert
    assert result.rt_cd == ErrorCode.SUCCESS.value
    assert result.data == []

@pytest.mark.asyncio
async def test_get_top_rise_fall_stocks_api_failure(mock_quotations):
    """get_top_rise_fall_stocks: API 호출 실패 시 에러 응답 반환 테스트"""
    # Arrange
    api = mock_quotations
    error_response = ResCommonResponse(rt_cd=ErrorCode.API_ERROR.value, msg1="API Error")
    api.call_api = AsyncMock(return_value=error_response)

    # Act
    result = await api.get_top_rise_fall_stocks(rise=True)

    # Assert
    assert result == error_response
    api._logger.info.assert_called_with("상승률 상위 종목 조회 시도...")


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_data", [
    "not a dict",  # data가 dict가 아닌 경우
    {"output": "not a list"},  # output이 list가 아닌 경우
    {"output": ["not a dict"]},  # output list의 item이 dict가 아닌 경우
])
async def test_get_top_rise_fall_stocks_parsing_error(mock_quotations, invalid_data):
    """get_top_rise_fall_stocks: 응답 데이터 파싱 실패 시 PARSING_ERROR 반환 테스트"""
    # Arrange
    api = mock_quotations
    api.call_api = AsyncMock(return_value=ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data=invalid_data
    ))

    # Act
    result = await api.get_top_rise_fall_stocks(rise=False)

    # Assert
    assert result.rt_cd == ErrorCode.PARSING_ERROR.value
    assert "등락률 응답 형식 오류" in result.msg1
    assert result.data is None
    api._logger.error.assert_called_once()

@pytest.mark.asyncio
async def test_get_top_volume_stocks_api_failure(mock_quotations):
    """get_top_volume_stocks: API 호출 실패 시 에러 응답 반환 테스트"""
    # Arrange
    api = mock_quotations
    error_response = ResCommonResponse(rt_cd=ErrorCode.API_ERROR.value, msg1="API Error")
    api.call_api = AsyncMock(return_value=error_response)

    # Act
    result = await api.get_top_volume_stocks()

    # Assert
    assert result == error_response
    api._logger.warning.assert_called_with("거래량 상위 조회 실패: API Error")


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_data", [
    "not a dict",
    {"output": "not a list"},
])
async def test_get_top_volume_stocks_parsing_error(mock_quotations, invalid_data):
    """get_top_volume_stocks: 응답 데이터 파싱 실패 시 PARSING_ERROR 반환 테스트"""
    # Arrange
    api = mock_quotations
    api.call_api = AsyncMock(return_value=ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data=invalid_data
    ))

    # Act
    result = await api.get_top_volume_stocks()

    # Assert
    assert result.rt_cd == ErrorCode.PARSING_ERROR.value
    assert "거래량 상위 응답 형식 오류" in result.msg1
    assert result.data is None
    api._logger.error.assert_called_once()


# ── get_multi_price 테스트 ──────────────────────────────────────

@pytest.mark.asyncio
async def test_get_multi_price_success(mock_quotations):
    """get_multi_price: 복수종목 현재가 정상 조회"""
    api = mock_quotations
    api.call_api = AsyncMock(return_value=ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK",
        data={"output": [
            {"stck_shrn_iscd": "005930", "stck_prpr": "70000", "prdy_ctrt": "1.50"},
            {"stck_shrn_iscd": "000660", "stck_prpr": "120000", "prdy_ctrt": "-0.80"},
        ]}
    ))

    result = await api.get_multi_price(["005930", "000660"])

    assert result.rt_cd == ErrorCode.SUCCESS.value
    assert result.msg1 == "복수종목 현재가 조회 성공"
    assert isinstance(result.data, list)
    assert len(result.data) == 2
    assert result.data[0]["stck_shrn_iscd"] == "005930"
    assert result.data[1]["stck_prpr"] == "120000"
    api.call_api.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_multi_price_empty_codes(mock_quotations):
    """get_multi_price: 빈 종목코드 리스트"""
    result = await mock_quotations.get_multi_price([])

    assert result.rt_cd == ErrorCode.INVALID_INPUT.value
    assert "비어 있습니다" in result.msg1
    assert result.data == []


@pytest.mark.asyncio
async def test_get_multi_price_over_30_truncated(mock_quotations):
    """get_multi_price: 30개 초과 시 30개로 자르기"""
    api = mock_quotations
    api.call_api = AsyncMock(return_value=ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK",
        data={"output": [{"stck_shrn_iscd": f"{i:06d}", "stck_prpr": "1000"} for i in range(30)]}
    ))

    codes = [f"{i:06d}" for i in range(35)]
    result = await api.get_multi_price(codes)

    assert result.rt_cd == ErrorCode.SUCCESS.value
    api._logger.warning.assert_called_once()
    assert "30개로 제한" in api._logger.warning.call_args[0][0]
    # call_api에 전달된 params 확인: 30번째까지만 존재
    _, kwargs = api.call_api.call_args
    params = kwargs["params"]
    assert "fid_input_iscd_30" in params
    assert "fid_input_iscd_31" not in params


@pytest.mark.asyncio
async def test_get_multi_price_api_failure(mock_quotations):
    """get_multi_price: API 호출 실패"""
    api = mock_quotations
    api.call_api = AsyncMock(return_value=ResCommonResponse(
        rt_cd=ErrorCode.API_ERROR.value, msg1="서버 오류", data=None
    ))

    result = await api.get_multi_price(["005930"])

    assert result.rt_cd == ErrorCode.API_ERROR.value
    api._logger.warning.assert_called_once()


@pytest.mark.asyncio
async def test_get_multi_price_empty_response(mock_quotations):
    """get_multi_price: 응답 data가 비어있을 때"""
    api = mock_quotations
    api.call_api = AsyncMock(return_value=ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data=None
    ))

    result = await api.get_multi_price(["005930"])

    assert result.rt_cd == ErrorCode.EMPTY_VALUES.value
    assert result.data == []


@pytest.mark.asyncio
async def test_get_multi_price_single_stock(mock_quotations):
    """get_multi_price: 단일 종목 조회"""
    api = mock_quotations
    api.call_api = AsyncMock(return_value=ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK",
        data={"output": [{"stck_shrn_iscd": "005930", "stck_prpr": "70000"}]}
    ))

    result = await api.get_multi_price(["005930"])

    assert result.rt_cd == ErrorCode.SUCCESS.value
    assert len(result.data) == 1
    # params에 1번만 설정되고 나머지는 빈 문자열인지 확인
    _, kwargs = api.call_api.call_args
    params = kwargs["params"]
    assert params["fid_input_iscd_1"] == "005930"
    assert "fid_input_iscd_2" not in params  # 빈 값은 제외

@pytest.mark.asyncio
async def test_get_stock_conclusion_success(mock_quotations):
    """get_stock_conclusion 성공 테스트"""
    mock_quotations.call_api = AsyncMock(return_value=ResCommonResponse(
        rt_cd="0", msg1="Success", data={"output": {"stck_prpr": "10000"}}
    ))
    result = await mock_quotations.get_stock_conclusion("005930")
    assert result.rt_cd == "0"
    mock_quotations.call_api.assert_called_once()

@pytest.mark.asyncio
async def test_get_stock_conclusion_failure(mock_quotations):
    """get_stock_conclusion 실패 테스트"""
    mock_quotations.call_api = AsyncMock(return_value=ResCommonResponse(
        rt_cd="1", msg1="Fail", data=None
    ))
    result = await mock_quotations.get_stock_conclusion("005930")
    assert result.rt_cd == "1"

@pytest.mark.asyncio
async def test_get_market_cap_api_failure(mock_quotations):
    """get_market_cap: 종목 정보 조회 API 실패 시 (Line 204)"""
    mock_quotations.get_stock_info_by_code = AsyncMock(return_value=ResCommonResponse(
        rt_cd=ErrorCode.API_ERROR.value, msg1="Fail", data=None
    ))
    
    result = await mock_quotations.get_market_cap("005930")
    
    assert result.rt_cd == ErrorCode.API_ERROR.value
    assert result.msg1 == "Fail"

@pytest.mark.asyncio
async def test_inquire_time_itemchartprice_output_is_dict(mock_quotations):
    """inquire_time_itemchartprice: output이 리스트가 아닌 dict일 때 (Line 470)"""
    # output2가 dict인 경우
    mock_data = {"output2": {"stck_cntg_hour": "100000", "stck_prpr": "10000"}}
    mock_quotations.call_api = AsyncMock(return_value=ResCommonResponse(
        rt_cd="0", msg1="OK", data=mock_data
    ))
    
    result = await mock_quotations.inquire_time_itemchartprice("005930", "2025010110")
    
    assert result.rt_cd == "0"
    assert isinstance(result.data, list)
    assert len(result.data) == 1
    assert result.data[0]["stck_prpr"] == "10000"

@pytest.mark.asyncio
async def test_get_multi_price_item_not_dict(mock_quotations):
    """get_multi_price: output 리스트에 dict가 아닌 항목이 있을 때 (Line 742)"""
    mock_data = {"output": [
        {"inter_shrn_iscd": "005930", "inter2_prpr": "70000"},
        "not_a_dict", # 건너뛰어야 함
        {"inter_shrn_iscd": "000660", "inter2_prpr": "120000"}
    ]}
    mock_quotations.call_api = AsyncMock(return_value=ResCommonResponse(
        rt_cd="0", msg1="OK", data=mock_data
    ))
    
    result = await mock_quotations.get_multi_price(["005930", "000660"])
    
    assert result.rt_cd == "0"
    assert len(result.data) == 2
    assert result.data[0]["stck_shrn_iscd"] == "005930"
    assert result.data[1]["stck_shrn_iscd"] == "000660"

@pytest.mark.asyncio
async def test_get_financial_ratio_success(mock_quotations):
    """get_financial_ratio 성공 테스트"""
    mock_quotations.call_api = AsyncMock(return_value=ResCommonResponse(
        rt_cd="0", msg1="Success", data={"output": {"sales": "1000"}}
    ))
    result = await mock_quotations.get_financial_ratio("005930")
    assert result.rt_cd == "0"
    mock_quotations.call_api.assert_called_once()

@pytest.mark.asyncio
async def test_get_financial_ratio_failure(mock_quotations):
    """get_financial_ratio 실패 테스트"""
    mock_quotations.call_api = AsyncMock(return_value=ResCommonResponse(
        rt_cd="1", msg1="Fail", data=None
    ))
    result = await mock_quotations.get_financial_ratio("005930")
    assert result.rt_cd == "1"
    mock_quotations._logger.warning.assert_called_once()

@pytest.mark.asyncio
async def test_get_price_summary_raw_status_code_warning(mock_quotations):
    """get_price_summary: raw_status_code에 'vs'가 포함된 경우 경고 로그 (Line 180)"""
    real_output = _create_dummy_output({
        "stck_oprc": "10000", "stck_prpr": "11000", "prdy_ctrt": "10.0",
        "new_hgpr_lwpr_cls_code": "1 vs 2"
    })
    
    mock_quotations.get_current_price = AsyncMock(return_value=ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": real_output}
    ))
    
    await mock_quotations.get_price_summary("005930")
    
    # 경고 로그 확인
    found = False
    for call in mock_quotations._logger.warning.call_args_list:
        if "신고/신저가 불일치" in str(call):
            found = True
            break
    assert found

@pytest.mark.asyncio
async def test_inquire_daily_itemchartprice_item_not_dict_output2(mock_quotations):
    """inquire_daily_itemchartprice: output2 리스트 내 아이템이 dict가 아닐 때"""
    mock_quotations.call_api = AsyncMock(return_value=ResCommonResponse(
        rt_cd="0", msg1="정상", data={"output2": ["not a dict", {"valid": "dict"}]}
    ))
    
    result = await mock_quotations.inquire_daily_itemchartprice("005930", "20250101")
    
    assert result.rt_cd == ErrorCode.SUCCESS.value
    # "not a dict"는 건너뛰고, {"valid": "dict"}는 ResDailyChartApiItem 생성 시도 중 에러 발생 가능성 있음
    # 결과적으로 빈 리스트일 가능성 높음.
    assert isinstance(result.data, list)
    mock_quotations._logger.warning.assert_called()


# ── 종목별 투자자 매매동향 일별 (investor-trade-by-stock-daily) ──────

@pytest.mark.asyncio
async def test_get_investor_trade_by_stock_daily_success(mock_quotations):
    """투자자 매매동향 정상 조회 — output1 + output2[0] 병합 반환."""
    api = mock_quotations
    api.call_api = AsyncMock(return_value=ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK",
        data={
            "output1": {
                "stck_prpr": "70000", "prdy_vrss": "100",
                "prdy_vrss_sign": "2", "prdy_ctrt": "1.5", "acml_vol": "10000",
            },
            "output2": [
                {"stck_bsop_date": "20260305", "frgn_ntby_qty": "500",
                 "prsn_ntby_qty": "-300", "orgn_ntby_qty": "200"},
                {"stck_bsop_date": "20260304", "frgn_ntby_qty": "100",
                 "prsn_ntby_qty": "-50", "orgn_ntby_qty": "80"},
            ],
        }
    ))

    result = await api.get_investor_trade_by_stock_daily("005930", "20260305")

    assert result.rt_cd == ErrorCode.SUCCESS.value
    assert result.data["stck_prpr"] == "70000"  # output1 필드
    assert result.data["frgn_ntby_qty"] == "500"  # output2[0] 필드
    assert result.data["prsn_ntby_qty"] == "-300"
    assert result.data["orgn_ntby_qty"] == "200"
    api.call_api.assert_called_once()
    _, kwargs = api.call_api.call_args
    assert kwargs["params"]["FID_INPUT_ISCD"] == "005930"
    assert kwargs["params"]["FID_INPUT_DATE_1"] == "20260305"
    assert kwargs["params"]["FID_COND_MRKT_DIV_CODE"] == "J"


@pytest.mark.asyncio
async def test_get_investor_trade_by_stock_daily_api_error(mock_quotations):
    """API 오류 시 에러 응답 그대로 반환."""
    api = mock_quotations
    api.call_api = AsyncMock(return_value=ResCommonResponse(
        rt_cd=ErrorCode.API_ERROR.value, msg1="API 오류", data=None
    ))

    result = await api.get_investor_trade_by_stock_daily("005930", "20260305")

    assert result.rt_cd == ErrorCode.API_ERROR.value
    assert result.msg1 == "API 오류"


@pytest.mark.asyncio
async def test_get_investor_trade_by_stock_daily_empty_output(mock_quotations):
    """output2가 비어있으면 data=None 반환."""
    api = mock_quotations
    api.call_api = AsyncMock(return_value=ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK",
        data={"output1": {}, "output2": []}
    ))

    result = await api.get_investor_trade_by_stock_daily("005930", "20260305")

    assert result.rt_cd == ErrorCode.SUCCESS.value
    assert result.data is None
    assert "데이터 없음" in result.msg1


@pytest.mark.asyncio
async def test_get_investor_trade_by_stock_daily_invalid_data(mock_quotations):
    """응답 data가 dict가 아닌 경우 PARSING_ERROR."""
    api = mock_quotations
    api.call_api = AsyncMock(return_value=ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK",
        data="invalid"
    ))

    result = await api.get_investor_trade_by_stock_daily("005930", "20260305")

    assert result.rt_cd == ErrorCode.PARSING_ERROR.value


@pytest.mark.asyncio
async def test_check_holiday_success(mock_quotations):
    """check_holiday 성공 테스트"""
    api = mock_quotations
    api.call_api = AsyncMock(return_value=ResCommonResponse(
        rt_cd="0", msg1="Success", data={"output": [{"bass_dt": "20250101", "bzdy_yn": "N"}]}
    ))
    
    result = await api.check_holiday("20250101")
    
    assert result.rt_cd == "0"
    assert result.data["output"][0]["bzdy_yn"] == "N"
    api.call_api.assert_called_once()
