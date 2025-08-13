# integration_test/it_trading_app.py
import pytest
import asyncio
import json
from app.trading_app import TradingApp
from unittest.mock import AsyncMock, MagicMock
from common.types import ResCommonResponse, ResTopMarketCapApiItem, ResFluctuation, ErrorCode
from brokers.korea_investment.korea_invest_trading_api import KoreaInvestApiTrading
from brokers.korea_investment.korea_invest_url_keys import EndpointKey
from app.user_action_executor import UserActionExecutor


@pytest.fixture
def get_mock_config():
    """mock된 config 데이터 반환"""
    return {
        "api_key": "mock-api-key",
        "api_secret_key": "mock-api-secret",
        "base_url": "https://mock-base-url.com",
        "websocket_url": "wss://mock-websocket-url.com",
        "stock_account_number": "1234567890",
        "paper_api_key": "mock-paper-api-key",
        "paper_api_secret_key": "mock-paper-api-secret",
        "paper_stock_account_number": "0987654321",
        "htsid": "test-htsid",
        "custtype": "P",
        "market_code": "J",
        "is_paper_trading": False,
    }


@pytest.fixture
def real_app_instance(mocker, get_mock_config, test_logger):
    """
    통합 테스트를 위해 실제 TradingApp 인스턴스를 생성하고 초기화합니다.
    실제 네트워크 호출과 관련된 부분만 최소한으로 모킹합니다.
    """
    # 1. TokenManager 관련 네트워크 호출 모킹
    mock_token_manager_instance = MagicMock()
    mock_token_manager_instance.get_access_token = AsyncMock(return_value="mock_access_token")
    mock_token_manager_instance.issue_token = AsyncMock(return_value={
        "access_token": "mock_integration_test_token", "expires_in": 86400
    })
    mocker.patch('brokers.korea_investment.korea_invest_token_manager.TokenManager',
                 return_value=mock_token_manager_instance)

    # 2. Hashkey 생성 로직 모킹
    mock_trading_api_instance = MagicMock()
    mock_trading_api_instance._get_hashkey.return_value = "mock_hashkey_for_it_test"
    mocker.patch(f'{KoreaInvestApiTrading.__module__}.{KoreaInvestApiTrading.__name__}',
                 return_value=mock_trading_api_instance)

    # ✅ 3. logging.getLogger를 모킹하여 logger 핸들러 무력화
    # dummy_logger = MagicMock()

    # 2. 실제 TradingApp 인스턴스를 생성합니다.
    #    이 과정에서 config.yaml 로드, Logger, TimeManager, Env, TokenManager 초기화가 자동으로 수행됩니다.
    app = TradingApp(logger=test_logger)
    app.env.set_trading_mode(False)  # 실전 투자 환경 테스트
    app.config = get_mock_config
    # app.logger = MagicMock()

    # 3. TradingService 등 주요 서비스들을 실제 객체로 초기화합니다.
    #    이 과정은 app.run_async()의 일부이며, 동기적으로 실행하여 테스트 준비를 마칩니다.
    asyncio.run(app._complete_api_initialization())

    return app


@pytest.mark.asyncio
async def test_execute_action_select_environment_success(real_app_instance, mocker):
    """
    (통합 테스트) 메뉴 '0' - 거래 환경 변경 성공 시 running_status 유지
    """
    app = real_app_instance

    # ✅ _select_environment() 모킹: 성공
    mocker.patch.object(app, "select_environment", new_callable=AsyncMock, return_value=True)
    app.logger.info = MagicMock()

    # --- 실행 ---
    executor = UserActionExecutor(app)
    running_status = await executor.execute("0")

    # --- 검증 ---
    app.logger.info.assert_called_once_with("거래 환경 변경을 시작합니다.")
    assert running_status is True


@pytest.mark.asyncio
async def test_execute_action_select_environment_fail(real_app_instance, mocker):
    """
    (통합 테스트) 메뉴 '0' - 거래 환경 변경 실패 시 running_status = False
    """
    app = real_app_instance

    # ✅ _select_environment() 모킹: 실패
    mocker.patch.object(app, "select_environment", new_callable=AsyncMock, return_value=False)
    app.logger.info = MagicMock()

    # --- 실행 ---
    executor = UserActionExecutor(app)
    running_status = await executor.execute("0")

    # --- 검증 ---
    app.logger.info.assert_called_once_with("거래 환경 변경을 시작합니다.")
    assert running_status is False


@pytest.mark.asyncio
async def test_get_current_price_full_integration(real_app_instance, mocker):
    """
    (통합 테스트) 현재가 조회 시 TradingApp → StockQueryService → BrokerAPIWrapper →
    get_current_price → call_api 흐름을 따라 실제 서비스가 실행되며,
    최하위 API 호출만 모킹하여 검증합니다.
    """
    # --- Arrange ---
    app = real_app_instance
    test_price_data = {
        "output": {
            "stck_prpr": "70500",
            "prdy_vrss": "1200",
            "prdy_ctrt": "1.73"
        }
    }

    mock_api_response = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value,
        msg1="정상",
        data=test_price_data
    )

    # 최하단 API만 모킹
    mock_call_api = mocker.patch(
        'brokers.korea_investment.korea_invest_api_base.KoreaInvestApiBase.call_api',
        return_value=mock_api_response
    )

    # 1번 종목 조회
    mocker.patch.object(app.cli_view, 'get_user_input', new_callable=AsyncMock)
    test_stock_code = "005930"
    app.cli_view.get_user_input.return_value = test_stock_code

    # --- Act ---
    executor = UserActionExecutor(app)
    running_status = await executor.execute("1")

    # --- Assert ---
    assert running_status == True
    mock_call_api.assert_awaited_once()

    method, key_or_path = mock_call_api.call_args[0][:2]
    assert method == "GET"
    assert key_or_path ==  EndpointKey.INQUIRE_PRICE

    # 입력 프롬프트 호출 여부
    app.cli_view.get_user_input.assert_awaited_once_with("조회할 종목 코드를 입력하세요 (삼성전자: 005930): ")


@pytest.mark.asyncio
async def test_get_account_balance_full_integration(real_app_instance, mocker):
    """
    (통합 테스트) 계좌 잔고 조회 시, TradingApp -> TradingService -> BrokerAPIWrapper의
    실제 로직을 모두 실행하고, 최하단 네트워크 호출('call_api')만 모킹하여 검증합니다.
    """
    # --- Arrange (준비) ---
    app = real_app_instance

    # 1. 모킹할 최종 API 응답을 미리 정의합니다.
    mock_balance_data = {"dnca_tot_amt": "1000000", "tot_evlu_amt": "1200000"}
    mock_api_response = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value,
        msg1="정상",
        data=mock_balance_data
    )

    # 2. 가장 낮은 레벨의 API 호출 메서드를 모킹합니다.
    #    이것이 실제 네트워크 통신을 차단하는 유일한 지점입니다.
    mock_call_api = mocker.patch(
        'brokers.korea_investment.korea_invest_api_base.KoreaInvestApiBase.call_api',
        return_value=mock_api_response
    )

    # 2번 계좌 잔고 조회
    mocker.patch.object(app.cli_view, 'get_user_input', new_callable=AsyncMock)
    mocker.patch.object(app.cli_view, 'display_account_balance', new_callable=MagicMock)
    mocker.patch.object(app.cli_view, 'display_account_balance_failure', new_callable=MagicMock)

    # --- Act ---
    executor = UserActionExecutor(app)
    running_status = await executor.execute("2")

    # --- Assert (검증) ---
    assert running_status == True

    mock_call_api.assert_awaited_once()

    called_args, called_kwargs = mock_call_api.call_args

    method = called_args[0]
    key_or_path = called_args[1]

    assert method == "GET"
    assert key_or_path ==  EndpointKey.INQUIRE_BALANCE

    # 2. 성공 경로의 비즈니스 로직이 올바르게 수행되었는지 검증합니다.
    # ✅ 성공 로그가 올바른 데이터와 함께 기록되었는지 확인합니다.
    app.logger.info.assert_any_call(f"계좌 잔고 조회 성공: {mock_balance_data}")

    # ✅ 성공 결과를 표시하는 View 메서드가 올바른 데이터로 호출되었는지 확인합니다.
    app.cli_view.display_account_balance.assert_called_once_with(mock_balance_data)
    app.cli_view.display_account_balance_failure.assert_not_called()


class FakeResp:
    def __init__(self, payload):
        self._payload = payload
        self.text = json.dumps(payload)

    def raise_for_status(self):  # 해시키 성공 가정
        return None

    def json(self):
        return self._payload


def make_call_api_side_effect(order_ok_response: ResCommonResponse):
    async def _side_effect(method, path, *args, **kwargs):
        # 1) 해시키
        if path.endswith("/uapi/hashkey"):
            return ResCommonResponse(
                rt_cd=ErrorCode.SUCCESS.value,
                msg1="ok",
                data=FakeResp({"HASH": "abc123"})
            )
        # 2) 주문
        if path.endswith("/uapi/domestic-stock/v1/trading/order-cash"):
            return order_ok_response
        # 혹시 다른 경로면 실패 응답
        return ResCommonResponse(
            rt_cd=ErrorCode.API_ERROR.value,
            msg1=f"unexpected path: {path}",
            data=None
        )

    return _side_effect


@pytest.mark.asyncio
async def test_buy_stock_full_integration(real_app_instance, mocker):
    """
    (통합 테스트) 주식 매수 요청: TradingApp -> OrderExecutionService -> TradingService -> BrokerAPIWrapper 호출 흐름 테스트
    """
    app = real_app_instance

    # ✅ 시장을 연 상태로 설정
    app.time_manager.is_market_open = MagicMock(return_value=True)

    # --- Mock 사용자 입력 ---
    mocker.patch.object(app.cli_view, 'get_user_input', new_callable=AsyncMock)
    app.cli_view.get_user_input.side_effect = ["005930", "10", "70000"]  # 종목코드, 수량, 가격

    # --- Mock API 응답 ---
    order_ok = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value,
        msg1="매수 주문 성공",
        data={"ord_no": "1234567890"}
    )

    mock_call_api = mocker.patch(
        'brokers.korea_investment.korea_invest_api_base.KoreaInvestApiBase.call_api',
        new_callable=AsyncMock
    )

    mock_call_api.side_effect = make_call_api_side_effect(order_ok)

    # --- Act ---
    executor = UserActionExecutor(app)
    running_status = await executor.execute("3")

    # --- Assert (검증) ---
    assert running_status is True
    # 해시키 + 주문 총 2회 호출
    assert mock_call_api.await_count == 2

    # 두 번째 호출이 주문 엔드포인트인지 확인
    key_or_path = mock_call_api.call_args_list[1][0][1]
    assert  EndpointKey.ORDER_CASH in key_or_path


@pytest.mark.asyncio
async def test_sell_stock_full_integration(real_app_instance, mocker):
    """
    (통합 테스트) 주식 매도 요청: TradingApp -> OrderExecutionService -> TradingService -> BrokerAPIWrapper 호출 흐름 테스트
    """
    app = real_app_instance

    # ✅ 시장을 연 상태로 설정
    app.time_manager.is_market_open = MagicMock(return_value=True)

    # --- Mock 사용자 입력 ---
    mocker.patch.object(app.cli_view, 'get_user_input', new_callable=AsyncMock)
    app.cli_view.get_user_input.side_effect = ["005930", "5", "69000"]

    # --- Mock API 응답 ---
    order_ok = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value,
        msg1="매도 주문 성공",
        data={"ord_no": "9876543210"}
    )

    mock_call_api = mocker.patch(
        'brokers.korea_investment.korea_invest_api_base.KoreaInvestApiBase.call_api',
        new_callable=AsyncMock
    )
    mock_call_api.side_effect = make_call_api_side_effect(order_ok)

    # --- Act ---
    executor = UserActionExecutor(app)
    running_status = await executor.execute("4")

    # --- Assert (검증) ---
    assert running_status is True
    # 해시키 + 주문 = 2회 호출
    assert mock_call_api.await_count == 2

    # 두 번째 호출이 주문 엔드포인트인지 확인
    key_or_path = mock_call_api.call_args_list[1][0][1]
    assert EndpointKey.ORDER_CASH in key_or_path


@pytest.mark.asyncio
async def test_display_stock_change_rate_full_integration(real_app_instance, mocker):
    """
    (통합 테스트) 전일대비 등락률 조회: TradingApp → StockQueryService → BrokerAPIWrapper 흐름 테스트
    """
    app = real_app_instance

    # ✅ 사용자 입력 모킹
    mocker.patch.object(app.cli_view, 'get_user_input', new_callable=AsyncMock)
    app.cli_view.get_user_input.return_value = "005930"

    # ✅ API 응답 모킹
    mock_response = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value,
        msg1="정상",
        data={
            "output": {
                "stck_prpr": "70500",
                "prdy_vrss": "1200",
                "prdy_ctrt": "1.73"
            }
        }
    )

    mock_call_api = mocker.patch(
        'brokers.korea_investment.korea_invest_api_base.KoreaInvestApiBase.call_api',
        return_value=mock_response
    )

    # --- Act ---
    executor = UserActionExecutor(app)
    running_status = await executor.execute("5")

    # --- Assert (검증) ---
    assert running_status == True
    mock_call_api.assert_awaited_once()
    app.cli_view.get_user_input.assert_awaited_once_with("조회할 종목 코드를 입력하세요 (삼성전자: 005930): ")


@pytest.mark.asyncio
async def test_display_stock_vs_open_price_full_integration(real_app_instance, mocker):
    """
    (통합 테스트) 시가대비 등락률 조회: TradingApp → StockQueryService → BrokerAPIWrapper 흐름 테스트
    """
    app = real_app_instance

    # ✅ 사용자 입력 모킹
    mocker.patch.object(app.cli_view, 'get_user_input', new_callable=AsyncMock)
    app.cli_view.get_user_input.return_value = "005930"

    # ✅ API 응답 모킹 (open_price와 현재가 비교 가능 데이터)
    mock_response = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value,
        msg1="정상",
        data={
            "output": {
                "stck_prpr": "70500",
                "stck_oprc": "69500",
                "prdy_vrss": "1000",
                "prdy_ctrt": "1.44"
            }
        }
    )

    mock_call_api = mocker.patch(
        'brokers.korea_investment.korea_invest_api_base.KoreaInvestApiBase.call_api',
        return_value=mock_response
    )

    # --- Act ---
    executor = UserActionExecutor(app)
    running_status = await executor.execute("6")

    # --- Assert (검증) ---
    assert running_status == True
    mock_call_api.assert_awaited_once()
    app.cli_view.get_user_input.assert_awaited_once_with("조회할 종목 코드를 입력하세요 (삼성전자: 005930): ")


@pytest.mark.asyncio
async def test_get_asking_price_full_integration(real_app_instance, mocker):
    """
    (통합 테스트) 실시간 호가 조회: TradingApp → StockQueryService → BrokerAPIWrapper 흐름 테스트
    """
    app = real_app_instance

    # ✅ 사용자 입력 모킹
    mocker.patch.object(app.cli_view, 'get_user_input', new_callable=AsyncMock)
    app.cli_view.get_user_input.return_value = "005930"

    # ✅ API 응답 모킹 (호가 정보 일부 포함)
    mock_response = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value,
        msg1="정상",
        data={
            "askp1": "70500",
            "bidp1": "70400",
            "askp_rsqn1": "100",
            "bidp_rsqn1": "120"
        }
    )

    mock_call_api = mocker.patch(
        'brokers.korea_investment.korea_invest_api_base.KoreaInvestApiBase.call_api',
        return_value=mock_response
    )

    # --- Act ---
    executor = UserActionExecutor(app)
    running_status = await executor.execute("7")

    # --- Assert (검증) ---
    assert running_status == True
    mock_call_api.assert_awaited_once()
    app.cli_view.get_user_input.assert_awaited_once()
    called_args = app.cli_view.get_user_input.await_args.args[0]
    assert "호가를 조회할 종목 코드를 입력하세요" in called_args


@pytest.mark.asyncio
async def test_get_time_concluded_prices_full_integration(real_app_instance, mocker):
    """
    (통합 테스트) 시간대별 체결가 조회: TradingApp → StockQueryService → BrokerAPIWrapper 흐름 테스트
    """
    app = real_app_instance

    # ✅ 사용자 입력 모킹
    mocker.patch.object(app.cli_view, 'get_user_input', new_callable=AsyncMock)
    app.cli_view.get_user_input.return_value = "005930"

    # ✅ API 응답 모킹 (시간대별 체결가 일부 포함)
    mock_response = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value,
        msg1="정상",
        data={
            "stck_cntg_hour": "1015",
            "stck_prpr": "70200",
            "cntg_vol": "1000"
        }
    )

    mock_call_api = mocker.patch(
        'brokers.korea_investment.korea_invest_api_base.KoreaInvestApiBase.call_api',
        return_value=mock_response
    )

    # --- Act ---
    executor = UserActionExecutor(app)
    running_status = await executor.execute("8")

    # --- Assert (검증) ---
    assert running_status == True
    mock_call_api.assert_awaited_once()
    called_args = app.cli_view.get_user_input.await_args.args[0]
    assert "시간대별 체결가를 조회할 종목 코드를 입력하세요" in called_args


# @pytest.mark.asyncio
# async def test_get_stock_news_full_integration(real_app_instance, mocker):
#     """
#     (통합 테스트) 종목 뉴스 조회: TradingApp → StockQueryService → BrokerAPIWrapper 흐름 테스트
#     """
#     app = real_app_instance
#
#     # ✅ 사용자 입력 모킹
#     mocker.patch.object(app.cli_view, 'get_user_input', new_callable=AsyncMock)
#     app.cli_view.get_user_input.return_value = "005930"
#
#     # ✅ API 응답 모킹 (뉴스 항목 일부 포함)
#     mock_response = ResCommonResponse(
#         rt_cd=ErrorCode.SUCCESS.value,
#         msg1="정상",
#         data={
#             "output": [  # ✅ 이 구조가 필요
#                 {
#                     "news_title": "삼성전자, 2분기 실적 발표",
#                     "news_date": "20250721",
#                     "news_time": "093000",
#                     "news_summary": "영업이익 증가 발표"
#                 }
#             ]
#         }
#     )
#
#     mock_call_api = mocker.patch(
#         'brokers.korea_investment.korea_invest_api_base.KoreaInvestApiBase.call_api',
#         return_value=mock_response
#     )
#
#     # --- Act ---
#     executor = UserActionExecutor(app)
#     running_status = await executor.execute("9")
#
#     # --- Assert (검증) ---
#     assert running_status == True
#     mock_call_api.assert_awaited_once()
#     app.cli_view.get_user_input.assert_awaited_once()
#     called_args = app.cli_view.get_user_input.await_args.args[0]
#     assert "뉴스를 조회할 종목 코드를 입력하세요" in called_args


@pytest.mark.asyncio
async def test_get_etf_info_full_integration(real_app_instance, mocker):
    """
    (통합 테스트) ETF 정보 조회: TradingApp → StockQueryService → BrokerAPIWrapper 흐름 테스트
    """
    app = real_app_instance

    # ✅ 사용자 입력 모킹
    mocker.patch.object(app.cli_view, 'get_user_input', new_callable=AsyncMock)
    app.cli_view.get_user_input.return_value = "069500"  # 예: KODEX 200

    # ✅ API 응답 모킹 (ETF 정보 포함)
    mock_response = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value,
        msg1="정상",
        data={
            "etf_name": "KODEX 200",
            "nav": "41500.00",
            "prdy_ctrt": "0.45"
        }
    )

    mock_call_api = mocker.patch(
        'brokers.korea_investment.korea_invest_api_base.KoreaInvestApiBase.call_api',
        return_value=mock_response
    )

    # --- Act ---
    executor = UserActionExecutor(app)
    running_status = await executor.execute("10")

    # --- Assert (검증) ---
    assert running_status == True
    mock_call_api.assert_awaited_once()
    called_args = app.cli_view.get_user_input.await_args.args[0]
    assert "정보를 조회할 ETF 코드를 입력하세요" in called_args


# @pytest.mark.asyncio
# async def test_search_stocks_by_keyword_full_integration(real_app_instance, mocker):
#     """
#     (통합 테스트) 키워드로 종목 검색: TradingApp → StockQueryService → BrokerAPIWrapper 흐름 테스트
#     """
#     app = real_app_instance
#
#     # ✅ 사용자 입력 모킹
#     mocker.patch.object(app.cli_view, 'get_user_input', new_callable=AsyncMock)
#     app.cli_view.get_user_input.return_value = "삼성"
#
#     # ✅ API 응답 모킹 (검색 결과 포함)
#     mock_response = ResCommonResponse(
#         rt_cd=ErrorCode.SUCCESS.value,
#         msg1="정상",
#         data={
#             "output": [
#                 {"code": "005930", "name": "삼성전자"},
#                 {"code": "005935", "name": "삼성전자우"}
#             ]
#         }
#     )
#
#     mock_call_api = mocker.patch(
#         'brokers.korea_investment.korea_invest_api_base.KoreaInvestApiBase.call_api',
#         return_value=mock_response
#     )
#
#     # --- Act ---
#     executor = UserActionExecutor(app)
#     running_status = await executor.execute("11")
#
#     # --- Assert (검증) ---
#     assert running_status == True
#     mock_call_api.assert_awaited_once()
#     app.cli_view.get_user_input.assert_awaited_once_with("검색할 키워드를 입력하세요: ")
#
@pytest.mark.asyncio
async def test_get_top_volume_full_integration(real_app_instance, mocker):
    """
    (통합 테스트) 상위 랭킹 조회 (volume): TradingApp → StockQueryService → BrokerAPIWrapper 흐름 테스트
    """
    app = real_app_instance
    # 🔑 실제 클라이언트의 _quotations
    q_real = app.stock_query_service.trading_service._broker_api_wrapper._client._client._quotations

    # 최종 산출을 그대로: ResFluctuation 객체 리스트
    top30 = [
        ResFluctuation.from_dict({
            "stck_shrn_iscd": "005930",
            "hts_kor_isnm": "삼성전자",
            "stck_prpr": "70000",
            "prdy_ctrt": "3.2",
            "prdy_vrss": "2170",
        }),
        ResFluctuation.from_dict({
            "stck_shrn_iscd": "000660",
            "hts_kor_isnm": "SK하이닉스",
            "stck_prpr": "150000",
            "prdy_ctrt": "2.7",
            "prdy_vrss": "3950",
        }),
    ]

    # ✅ 여기만 패치! call_api는 패치하지 마세요.
    mock_get_volume = mocker.patch.object(
        q_real, "get_top_volume_stocks",
        AsyncMock(return_value=ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value, msg1="정상", data=top30
        ))
    )

    # (선택) CLI 출력 검증 원하면 모킹
    app.cli_view.display_top_stocks_ranking = MagicMock()
    app.cli_view.display_top_stocks_ranking_error = MagicMock()

    executor = UserActionExecutor(app)
    running_status = await executor.execute("30")  # volume

    assert running_status is True
    mock_get_volume.assert_awaited_once()

    # (선택) 출력 리스트 검증
    app.cli_view.display_top_stocks_ranking.assert_called_once()
    app.cli_view.display_top_stocks_ranking_error.assert_not_called()
    passed = app.cli_view.display_top_stocks_ranking.call_args[0][1]  # 보통 (title, list, ...)
    assert isinstance(passed, list) and len(passed) == 2
    assert {x.stck_shrn_iscd for x in passed} == {"005930", "000660"}

    title_arg, items_arg = app.cli_view.display_top_stocks_ranking.call_args[0][:2]
    assert title_arg == "volume"
    assert items_arg is top30  # 동일 리스트 객체 전달 확인


@pytest.mark.asyncio
async def test_get_top_rise_full_integration(real_app_instance, mocker):
    """
    (통합 테스트) 상위 랭킹 조회 (rise): TradingApp → StockQueryService → BrokerAPIWrapper 흐름 테스트
    """
    app = real_app_instance
    # 실제 클라이언트의 quotations 객체
    q_real = app.stock_query_service.trading_service._broker_api_wrapper._client._client._quotations

    top30 = [
        ResFluctuation.from_dict({
            "stck_shrn_iscd": "005930", "hts_kor_isnm": "삼성전자",
            "stck_prpr": "70000", "prdy_ctrt": "3.2", "prdy_vrss": "2170", "data_rank": "1"
        }),
        ResFluctuation.from_dict({
            "stck_shrn_iscd": "000660", "hts_kor_isnm": "SK하이닉스",
            "stck_prpr": "150000", "prdy_ctrt": "2.7", "prdy_vrss": "3950", "data_rank": "2"
        }),
    ]

    # ✅ 여기만 패치! (call_api 패치 제거)
    mock_get_rise = mocker.patch.object(
        q_real,
        "get_top_rise_fall_stocks",
        AsyncMock(return_value=ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value, msg1="정상", data=top30
        )),
    )


    # 출력 모킹
    app.cli_view.display_top_stocks_ranking = MagicMock()
    app.cli_view.display_top_stocks_ranking_error = MagicMock()

    # 실행 (31 = 상승률 ~30)
    ok = await UserActionExecutor(app).execute("31")
    assert ok is True
    mock_get_rise.assert_awaited_once()

    # ✅ 출력 검증
    app.cli_view.display_top_stocks_ranking.assert_called_once()
    app.cli_view.display_top_stocks_ranking_error.assert_not_called()
    title_arg, items_arg = app.cli_view.display_top_stocks_ranking.call_args[0][:2]
    assert title_arg == "rise"
    assert items_arg is top30  # 동일 리스트 객체 전달 확인


@pytest.mark.asyncio
async def test_get_top_fall_full_integration(real_app_instance, mocker):
    """
    (통합 테스트) 상위 랭킹 조회 (fall): TradingApp → StockQueryService → BrokerAPIWrapper 흐름 테스트
    """

    app = real_app_instance
    # 실제 클라이언트의 quotations 객체
    q_real = app.stock_query_service.trading_service._broker_api_wrapper._client._client._quotations

    top30 = [
        ResFluctuation.from_dict({
            "stck_shrn_iscd": "005930", "hts_kor_isnm": "삼성전자",
            "stck_prpr": "70000", "prdy_ctrt": "3.2", "prdy_vrss": "2170", "data_rank": "1"
        }),
        ResFluctuation.from_dict({
            "stck_shrn_iscd": "000660", "hts_kor_isnm": "SK하이닉스",
            "stck_prpr": "150000", "prdy_ctrt": "2.7", "prdy_vrss": "3950", "data_rank": "2"
        }),
    ]

    # ✅ 여기만 패치! (call_api 패치 제거)
    mock_get_fall = mocker.patch.object(
        q_real,
        "get_top_rise_fall_stocks",
        AsyncMock(return_value=ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value, msg1="정상", data=top30
        )),
    )

    # 출력 모킹
    app.cli_view.display_top_stocks_ranking = MagicMock()
    app.cli_view.display_top_stocks_ranking_error = MagicMock()

    # --- Act ---
    executor = UserActionExecutor(app)
    running_status = await executor.execute("32")

    # --- Assert (검증) ---
    assert running_status is True
    mock_get_fall.assert_awaited_once()

    # ✅ 출력 검증
    app.cli_view.display_top_stocks_ranking.assert_called_once()
    app.cli_view.display_top_stocks_ranking_error.assert_not_called()
    title_arg, items_arg = app.cli_view.display_top_stocks_ranking.call_args[0][:2]
    assert title_arg == "fall"
    assert items_arg is top30  # 동일 리스트 객체 전달 확인


@pytest.mark.asyncio
async def test_get_top_market_cap_stocks_full_integration(real_app_instance, mocker):
    """
    (통합 테스트) 시가총액 상위 조회 (실전 전용): TradingApp → StockQueryService → BrokerAPIWrapper 흐름 테스트
    """
    app = real_app_instance

    # ✅ API 응답 모킹 (시가총액 상위 종목 목록)
    mock_response = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value,
        msg1="정상",
        data={
            "output": [
                {"mksc_shrn_iscd": "005930", "code": "005930", "name": "삼성전자"},
                {"mksc_shrn_iscd": "000660", "code": "000660", "name": "SK하이닉스"}
            ]
        }
    )

    mock_call_api = mocker.patch(
        'brokers.korea_investment.korea_invest_api_base.KoreaInvestApiBase.call_api',
        return_value=mock_response
    )

    # --- Act ---
    executor = UserActionExecutor(app)
    running_status = await executor.execute("13")

    # --- Assert (검증) ---
    assert running_status == True
    mock_call_api.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_top_10_market_cap_stocks_with_prices_full_integration(real_app_instance, mocker):
    """
    (통합 테스트) 시가총액 상위 10개 현재가 조회 (실전 전용):
    TradingApp → StockQueryService → TradingService → BrokerAPIWrapper 흐름 테스트
    """
    app = real_app_instance

    # ✅ 시장을 연 상태로 설정
    app.time_manager.is_market_open = MagicMock(return_value=True)

    # ✅ API 응답 모킹 (시가총액 상위 + 현재가)
    mock_top_response = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value,
        msg1="정상",
        data={
            "output": [
                {"mksc_shrn_iscd": "005930", "stck_avls": "1000000000", "hts_kor_isnm": "삼성전자", "data_rank": "1"},
                {"mksc_shrn_iscd": "000660", "stck_avls": "500000000", "hts_kor_isnm": "SK하이닉스", "data_rank": "2"}
            ]
        }
    )

    mock_price_response = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value,
        msg1="정상",
        data={
            "output": {
                "stck_prpr": "70500",
                "prdy_vrss": "1200",
                "prdy_ctrt": "1.73"
            }
        }
    )

    # 첫 번째 호출: 시가총액 상위 종목 목록 조회
    # 두 번째 이후: 종목별 현재가 조회
    mock_call_api = mocker.patch(
        'brokers.korea_investment.korea_invest_api_base.KoreaInvestApiBase.call_api',
        side_effect=[mock_top_response, mock_price_response, mock_price_response]
    )

    # --- Act ---
    executor = UserActionExecutor(app)
    running_status = await executor.execute("14")

    # --- Assert (검증) ---
    assert running_status == True
    assert mock_call_api.await_count == 3  # 1번 top 종목, 2번 개별 가격 조회


@pytest.mark.asyncio
async def test_handle_upper_limit_stocks_full_integration(real_app_instance, mocker):
    """
    (통합 테스트) 상한가 종목 조회 (실전 전용):
    TradingApp → StockQueryService → TradingService → BrokerAPIWrapper 흐름 테스트
    """
    app = real_app_instance

    # ✅ 시장을 연 상태로 설정
    app.time_manager.is_market_open = MagicMock(return_value=True)

    # ✅ 상한가 종목 API 응답 모킹
    mock_response = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value,
        msg1="정상",
        data=[
            {"code": "005930", "name": "삼성전자", "price": "70500", "change_rate": "29.85"}
        ]
    )

    mock_call_api = mocker.patch(
        'brokers.korea_investment.korea_invest_api_base.KoreaInvestApiBase.call_api',
        return_value=mock_response
    )

    # --- Act ---
    executor = UserActionExecutor(app)
    running_status = await executor.execute("15")

    # --- Assert (검증) ---
    assert running_status == True
    mock_call_api.assert_awaited()


@pytest.mark.asyncio
async def test_handle_yesterday_upper_limit_stocks_full_integration(real_app_instance, mocker):
    """
    (통합 테스트) 전일 상한가 종목 조회 (상위):
    TradingApp → StockQueryService → TradingService → BrokerAPIWrapper 흐름 테스트
    """
    app = real_app_instance

    # ✅ 모의 응답: 시가총액 상위 종목 코드 조회 → 종목 코드 리스트 반환
    mock_top_response = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value,
        msg1="정상",
        data={
            "output": [
                {"mksc_shrn_iscd": "005930", "stck_avls": "492,000,000,000"},
                {"mksc_shrn_iscd": "000660", "stck_avls": "110,000,000,000"}
            ]
        }
    )

    # ✅ 모의 응답: 전일 상한가 종목 조회 → 리스트 반환
    mock_upper_response = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value,
        msg1="정상",
        data=[
            {"code": "005930", "name": "삼성전자", "price": "70500", "change_rate": "29.85"}
        ]
    )

    mock_call_api = mocker.patch(
        'brokers.korea_investment.korea_invest_api_base.KoreaInvestApiBase.call_api',
        side_effect=[mock_top_response, mock_upper_response]
    )

    # --- Act ---
    executor = UserActionExecutor(app)
    running_status = await executor.execute("16")

    # --- Assert (검증) ---
    assert running_status == True
    assert mock_call_api.await_count == 3


@pytest.mark.asyncio
async def test_handle_current_upper_limit_stocks_full_integration(real_app_instance, mocker):
    """
    (통합 테스트) 전일 상한가 종목 조회 (전체):
    TradingApp → StockQueryService → TradingService → BrokerAPIWrapper 흐름 테스트
    """
    app = real_app_instance

    top30_sample = [
        ResFluctuation.from_dict({
            "stck_shrn_iscd": "000001",
            "hts_kor_isnm": "A",
            "stck_prpr": "5590",
            "stck_hgpr": "5590",  # 고가=현재가 → 상한가 조건
            "prdy_ctrt": "30.00",
            "prdy_vrss": "1290",
        }),
        ResFluctuation.from_dict({
            "stck_shrn_iscd": "000002",
            "hts_kor_isnm": "B",
            "stck_prpr": "20000",
            "stck_hgpr": "20000",  # 고가=현재가 → 상한가 조건
            "prdy_ctrt": "30.00",
            "prdy_vrss": "3000",
        }),
        ResFluctuation.from_dict({
            "stck_shrn_iscd": "000003",
            "hts_kor_isnm": "C",
            "stck_prpr": "15000",
            "stck_hgpr": "16000",  # 상한가 아님
            "prdy_ctrt": "8.50",
            "prdy_vrss": "1170",
        }),
    ]

    inner_client = app.stock_query_service.trading_service._broker_api_wrapper._client._client
    mocker.patch.object(
        inner_client._quotations,
        "get_top_rise_fall_stocks",
        AsyncMock(return_value=ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value,
            msg1="정상",
            data=top30_sample
        ))
    )

    # 3) CLI 출력 모킹
    app.cli_view.display_current_upper_limit_stocks = MagicMock()
    app.cli_view.display_no_current_upper_limit_stocks = MagicMock()

    # --- Act ---
    try:
        executor = UserActionExecutor(app)
        running_status = await executor.execute("17")
    except TypeError as e:
        assert str(e) == "Error 발생하면 안됨."
        running_status = None

    # --- Assert (검증) ---
    assert running_status is True
    app.cli_view.display_current_upper_limit_stocks.assert_called_once()
    app.cli_view.display_no_current_upper_limit_stocks.assert_not_called()

    # 전달된 리스트 검증 (서비스가 ResBasicStockInfo로 매핑했다고 가정)
    lst = app.cli_view.display_current_upper_limit_stocks.call_args[0][0]
    assert isinstance(lst, list) and len(lst) >= 2

    # dataclass 또는 dict 모두 대응
    def _code(x):
        return getattr(x, "code", None) or (x.get("code") if isinstance(x, dict) else None)

    def _name(x):
        return getattr(x, "name", None) or (x.get("name") if isinstance(x, dict) else None)

    codes = {_code(x) for x in lst}
    names = {_name(x) for x in lst}
    assert "000001" in codes and "000002" in codes
    assert "A" in names and "B" in names


@pytest.mark.asyncio
async def test_handle_realtime_stream_full_integration(real_app_instance, mocker):
    """
    (통합 테스트) 실시간 체결가/호가 구독:
    TradingApp → StockQueryService → BrokerAPIWrapper.websocket_subscribe 흐름 테스트
    """
    app = real_app_instance

    # ✅ 사용자 입력 모킹 (2번 호출될 것)
    mocker.patch.object(app.cli_view, 'get_user_input', new_callable=AsyncMock)
    app.cli_view.get_user_input.side_effect = ["005930", "price"]

    # ✅ 웹소켓 구독 함수 모킹
    inner_client = app.stock_query_service.trading_service._broker_api_wrapper._client._client

    mock_subscribe = mocker.patch.object(
        inner_client._websocketAPI,
        "subscribe_realtime_price",
        new_callable=AsyncMock,
        return_value=AsyncMock
    )
    # --- Act ---
    executor = UserActionExecutor(app)
    running_status = await executor.execute("18")

    # --- Assert (검증) ---
    assert running_status == True
    mock_subscribe.assert_awaited_once_with("005930")


@pytest.mark.asyncio
async def test_execute_action_momentum_strategy_success(real_app_instance, mocker):
    """
    (통합 테스트) 메뉴 '20' - 모멘텀 전략 정상 실행 흐름 테스트

    TradingApp → StockQueryService → TradingService.get_top_market_cap_stocks_code → StrategyExecutor.execute
    """
    app = real_app_instance

    # ✅ 시장 개장 상태로 설정
    mocker.patch.object(app.time_manager, "is_market_open", return_value=True)

    # ✅ 시가총액 상위 종목 mock 응답
    mock_market_cap_response = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value,
        msg1="성공",
        data=[
            ResTopMarketCapApiItem(
                iscd="KR7005930003",
                mksc_shrn_iscd="005930",
                stck_avls="500000000000",
                data_rank="1",
                hts_kor_isnm="삼성전자",
                acc_trdvol="100000"
            ),
            ResTopMarketCapApiItem(
                iscd="KR7000660001",
                mksc_shrn_iscd="000660",
                stck_avls="300000000000",
                data_rank="2",
                hts_kor_isnm="SK하이닉스",
                acc_trdvol="80000"
            )
        ]
    )

    inner_client = app.stock_query_service.trading_service._broker_api_wrapper._client._client

    mocker.patch.object(
        inner_client._quotations,
        "get_top_market_cap_stocks_code",
        new_callable=AsyncMock,
        return_value=mock_market_cap_response
    )

    # ✅ StrategyExecutor.execute 모킹
    mock_strategy_result = {
        "follow_through": [{"code": "005930", "score": 95}],
        "not_follow_through": [{"code": "000660", "score": 50}]
    }
    mock_executor = mocker.patch(
        "strategies.strategy_executor.StrategyExecutor.execute",
        new_callable=AsyncMock,
        return_value=mock_strategy_result
    )

    # ✅ 결과 출력 함수들 모킹
    app.cli_view.display_top_stocks_success = MagicMock()
    app.cli_view.display_strategy_running_message = MagicMock()
    app.cli_view.display_strategy_results = MagicMock()
    app.cli_view.display_follow_through_stocks = MagicMock()
    app.cli_view.display_not_follow_through_stocks = MagicMock()

    # --- Act ---
    executor = UserActionExecutor(app)
    running_status = await executor.execute("20")

    # --- Assert (검증) ---
    assert running_status == True
    app.cli_view.display_strategy_running_message.assert_called_once_with("모멘텀")
    app.cli_view.display_top_stocks_success.assert_called_once()
    mock_executor.assert_awaited_once()
    app.cli_view.display_strategy_results.assert_called_once_with("모멘텀", mock_strategy_result)
    app.cli_view.display_follow_through_stocks.assert_called_once_with(mock_strategy_result["follow_through"])
    app.cli_view.display_not_follow_through_stocks.assert_called_once_with(
        mock_strategy_result["not_follow_through"])


@pytest.mark.asyncio
async def test_execute_action_momentum_strategy_market_cap_fail(real_app_instance, mocker):
    """
    (통합 테스트) 메뉴 '20' - 모멘텀 전략 실행 중 시가총액 상위 종목 조회 실패 시 흐름 검증

    TradingApp → StockQueryService → TradingService.get_top_market_cap_stocks_code
    → 실패 시 display_top_stocks_failure 및 로그 기록
    """
    app = real_app_instance

    # ✅ 시장 개장 상태로 설정
    mocker.patch.object(app.time_manager, "is_market_open", return_value=True)

    # ✅ 종목 조회 실패 응답 (rt_cd != '0')
    fail_response = ResCommonResponse(
        rt_cd=ErrorCode.API_ERROR.value,
        msg1="시가총액 조회 실패",
        data=None
    )

    inner_client = app.stock_query_service.trading_service._broker_api_wrapper._client._client

    # ✅ 실패 응답 모킹
    mocker.patch.object(
        inner_client._quotations,
        "get_top_market_cap_stocks_code",
        new_callable=AsyncMock,
        return_value=fail_response
    )

    # ✅ 메시지 출력 메서드 모킹
    app.cli_view.display_top_stocks_failure = MagicMock()
    app.logger.warning = MagicMock()

    # --- Act ---
    executor = UserActionExecutor(app)
    running_status = await executor.execute("20")

    # --- Assert (검증) ---
    assert running_status == True
    app.cli_view.display_top_stocks_failure.assert_called_once_with("시가총액 조회 실패")
    app.logger.warning.assert_called()


@pytest.mark.asyncio
async def test_execute_action_momentum_backtest_strategy_success(real_app_instance, mocker):
    """
    (통합 테스트) 메뉴 '21' - 모멘텀 백테스트 전략 정상 실행 흐름 테스트

    TradingApp → StockQueryService → TradingService.get_top_market_cap_stocks_code
    → StrategyExecutor.execute (백테스트 모드)
    """
    app = real_app_instance

    # ✅ 사용자 입력: 조회 개수
    mocker.patch.object(app.cli_view, "get_user_input", new_callable=AsyncMock)
    app.cli_view.get_user_input.return_value = "2"

    # ✅ 시가총액 상위 종목 mock 응답 (dict 형태로 리턴)
    mock_market_cap_response = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value,
        msg1="성공",
        data=[
            {"mksc_shrn_iscd": "005930"},
            {"mksc_shrn_iscd": "000660"}
        ]
    )

    inner_client = app.stock_query_service.trading_service._broker_api_wrapper._client._client

    mocker.patch.object(
        inner_client._quotations,
        "get_top_market_cap_stocks_code",
        new_callable=AsyncMock,
        return_value=mock_market_cap_response
    )

    # ✅ 백테스트 price lookup 모킹
    app.backtest_data_provider.realistic_price_lookup = MagicMock()

    # ✅ StrategyExecutor.execute 모킹
    mock_strategy_result = {
        "follow_through": [{"code": "005930"}],
        "not_follow_through": [{"code": "000660"}]
    }
    mocker.patch("strategies.strategy_executor.StrategyExecutor.execute", new_callable=AsyncMock,
                 return_value=mock_strategy_result)

    # ✅ CLI 출력 함수 모킹
    app.cli_view.display_strategy_running_message = MagicMock()
    app.cli_view.display_strategy_results = MagicMock()
    app.cli_view.display_follow_through_stocks = MagicMock()
    app.cli_view.display_not_follow_through_stocks = MagicMock()

    # --- 실행 ---
    executor = UserActionExecutor(app)
    running_status = await executor.execute("21")

    # --- 검증 ---
    assert running_status is True
    app.cli_view.display_strategy_running_message.assert_called_once_with("모멘텀 백테스트")
    app.cli_view.display_strategy_results.assert_called_once_with("백테스트", mock_strategy_result)
    app.cli_view.display_follow_through_stocks.assert_called_once_with(mock_strategy_result["follow_through"])
    app.cli_view.display_not_follow_through_stocks.assert_called_once_with(
        mock_strategy_result["not_follow_through"])


@pytest.mark.asyncio
async def test_execute_action_gapup_pullback_strategy_success(real_app_instance, mocker):
    """
    (통합 테스트) 메뉴 '22' - GapUpPullback 전략 정상 실행 흐름 테스트

    TradingApp → StockQueryService → TradingService.get_top_market_cap_stocks_code
    → StrategyExecutor.execute → 결과 출력까지 전 과정 검증
    """
    app = real_app_instance

    # ✅ 사용자 입력: 시가총액 상위 몇 개 종목?
    mocker.patch.object(app.cli_view, "get_user_input", new_callable=AsyncMock)
    app.cli_view.get_user_input.return_value = "2"

    # ✅ 시가총액 상위 종목 조회 응답 모킹
    mock_market_cap_response = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value,
        msg1="성공",
        data=[
            {"mksc_shrn_iscd": "005930"},
            {"mksc_shrn_iscd": "000660"}
        ]
    )

    inner_client = app.stock_query_service.trading_service._broker_api_wrapper._client._client

    # ✅ 실패 응답 모킹
    mocker.patch.object(
        inner_client._quotations,
        "get_top_market_cap_stocks_code",
        new_callable=AsyncMock,
        return_value=mock_market_cap_response
    )

    # ✅ 전략 실행 결과 모킹
    mock_strategy_result = {
        "gapup_pullback_selected": [{"code": "005930"}],
        "gapup_pullback_rejected": [{"code": "000660"}]
    }
    mocker.patch("strategies.strategy_executor.StrategyExecutor.execute", new_callable=AsyncMock,
                 return_value=mock_strategy_result)

    # ✅ CLI 출력 메서드 모킹
    app.cli_view.display_strategy_running_message = MagicMock()
    app.cli_view.display_strategy_results = MagicMock()
    app.cli_view.display_gapup_pullback_selected_stocks = MagicMock()
    app.cli_view.display_gapup_pullback_rejected_stocks = MagicMock()

    # --- 실행 ---
    executor = UserActionExecutor(app)
    running_status = await executor.execute("22")

    # --- 검증 ---
    assert running_status is True
    app.cli_view.display_strategy_running_message.assert_called_once_with("GapUpPullback")
    app.cli_view.display_strategy_results.assert_called_once_with("GapUpPullback", mock_strategy_result)
    app.cli_view.display_gapup_pullback_selected_stocks.assert_called_once_with(
        mock_strategy_result["gapup_pullback_selected"])
    app.cli_view.display_gapup_pullback_rejected_stocks.assert_called_once_with(
        mock_strategy_result["gapup_pullback_rejected"])


@pytest.mark.asyncio
async def test_execute_action_invalidate_token_success(real_app_instance):
    """
    (통합 테스트) 메뉴 '98' - 토큰 무효화 성공 흐름
    TradingApp → TokenManager.invalidate_token → CLIView.display_token_invalidated_message
    """
    app = real_app_instance

    # ✅ 의존성 모킹
    app.env.invalidate_token = MagicMock()
    app.cli_view.display_token_invalidated_message = MagicMock()

    # --- 실행 ---
    executor = UserActionExecutor(app)
    running_status = await executor.execute("98")

    # --- 검증 ---
    app.env.invalidate_token.assert_called_once()
    app.cli_view.display_token_invalidated_message.assert_called_once()
    assert running_status is True


@pytest.mark.asyncio
async def test_execute_action_exit_success(real_app_instance):
    """
    (통합 테스트) 메뉴 '99' - 프로그램 종료 처리 흐름
    TradingApp → CLIView.display_exit_message → running_status=False 반환
    """
    app = real_app_instance

    # ✅ 종료 메시지 출력 함수 모킹
    app.cli_view.display_exit_message = MagicMock()

    # --- 실행 ---
    executor = UserActionExecutor(app)
    running_status = await executor.execute("99")

    # --- 검증 ---
    app.cli_view.display_exit_message.assert_called_once()
    assert running_status is False
