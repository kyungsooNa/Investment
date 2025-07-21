# integration_test/it_trading_app.py
import pytest
import asyncio
import pandas as pd
from trading_app import TradingApp
from unittest.mock import AsyncMock, MagicMock
from common.types import ResCommonResponse, ResTopMarketCapApiItem, ErrorCode

from brokers.korea_investment.korea_invest_trading_api import KoreaInvestApiTrading

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
        "is_paper_trading": True,
        "tr_ids": {
            "quotations": {
                "search_info": "TR_ID_SEARCH_INFO"
            }
        },
        "paths": {
            "inquire_price": "/mock/inquire-price"
        },
        "params": {
            "fid_div_cls_code": 2,
            "screening_code": "20174"
        }
    }



@pytest.fixture
def real_app_instance(mocker, get_mock_config):
    """
    통합 테스트를 위해 실제 TradingApp 인스턴스를 생성하고 초기화합니다.
    실제 네트워크 호출과 관련된 부분만 최소한으로 모킹합니다.
    """
    # 1. 초기화 과정에서 발생하는 네트워크 호출을 미리 모킹합니다.
    #    - TokenManager의 실제 토큰 발급 로직
    #    - Hashkey 생성 로직
    mock_token_manager_instance = MagicMock()
    mock_token_manager_instance.get_access_token = AsyncMock(return_value="mock_access_token")
    mock_token_manager_instance.issue_token = AsyncMock(return_value={
        "access_token": "mock_integration_test_token", "expires_in": 86400
    })
    mocker.patch('trading_app.TokenManager', return_value=mock_token_manager_instance)

    # ✅ Hashkey 생성을 담당하는 KoreaInvestApiTrading 클래스도 같은 방식으로 모킹합니다.
    #    이 클래스는 BrokerAPIWrapper 내부에서 사용될 가능성이 높습니다.
    mock_trading_api_instance = MagicMock()
    mock_trading_api_instance._get_hashkey.return_value = "mock_hashkey_for_it_test"
    mocker.patch(f'{KoreaInvestApiTrading.__module__}.{KoreaInvestApiTrading.__name__}', return_value=mock_trading_api_instance)

    # 2. 실제 TradingApp 인스턴스를 생성합니다.
    #    이 과정에서 config.yaml 로드, Logger, TimeManager, Env, TokenManager 초기화가 자동으로 수행됩니다.
    app = TradingApp()
    app.config = get_mock_config
    app.logger = MagicMock()

    # 3. TradingService 등 주요 서비스들을 실제 객체로 초기화합니다.
    #    이 과정은 app.run_async()의 일부이며, 동기적으로 실행하여 테스트 준비를 마칩니다.
    asyncio.run(app._complete_api_initialization())

    return app


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
        "stck_prpr": "70500",
        "prdy_vrss": "1200",
        "prdy_ctrt": "1.73"
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
    running_status = await app._execute_action("1")

    # --- Assert ---
    assert running_status == True
    mock_call_api.assert_awaited_once()

    method, path = mock_call_api.call_args[0][:2]
    assert method == "GET"
    assert path == "/uapi/domestic-stock/v1/quotations/inquire-price"

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
    running_status = await app._execute_action("2")

    # --- Assert (검증) ---
    assert running_status == True

    mock_call_api.assert_awaited_once()

    called_args, called_kwargs = mock_call_api.call_args

    method = called_args[0]
    path = called_args[1]

    assert method == "GET"
    assert path == "/uapi/domestic-stock/v1/trading/inquire-balance"

    # 2. 성공 경로의 비즈니스 로직이 올바르게 수행되었는지 검증합니다.
    # ✅ 성공 로그가 올바른 데이터와 함께 기록되었는지 확인합니다.
    app.logger.info.assert_any_call(f"계좌 잔고 조회 성공: {mock_balance_data}")

    # ✅ 성공 결과를 표시하는 View 메서드가 올바른 데이터로 호출되었는지 확인합니다.
    app.cli_view.display_account_balance.assert_called_once_with(mock_balance_data)
    app.cli_view.display_account_balance_failure.assert_not_called()


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
    mock_response = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value,
        msg1="매수 주문 성공",
        data={"ord_no": "1234567890"}
    )

    mock_call_api = mocker.patch(
        'brokers.korea_investment.korea_invest_api_base.KoreaInvestApiBase.call_api',
        return_value=mock_response
    )

    # --- Act ---
    running_status = await app._execute_action("3")

    # --- Assert (검증) ---
    assert running_status == True
    assert mock_call_api.await_count == 1
    called_args = mock_call_api.call_args[0]
    assert called_args[0] == "POST"
    assert "/uapi/domestic-stock/v1/trading/order-cash" in called_args[1]

    app.cli_view.get_user_input.assert_any_await("매수할 종목 코드를 입력하세요: ")
    app.cli_view.get_user_input.assert_any_await("매수할 수량을 입력하세요: ")
    app.cli_view.get_user_input.assert_any_await("매수 가격을 입력하세요 (시장가: 0): ")


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
    mock_response = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value,
        msg1="매도 주문 성공",
        data={"ord_no": "9876543210"}
    )

    mock_call_api = mocker.patch(
        'brokers.korea_investment.korea_invest_api_base.KoreaInvestApiBase.call_api',
        return_value=mock_response
    )

    # --- Act ---
    running_status = await app._execute_action("4")

    # --- Assert (검증) ---
    assert running_status == True
    assert mock_call_api.await_count == 1
    called_args = mock_call_api.call_args[0]
    assert called_args[0] == "POST"
    assert "/uapi/domestic-stock/v1/trading/order-cash" in called_args[1]

    app.cli_view.get_user_input.assert_any_await("매도할 종목 코드를 입력하세요: ")
    app.cli_view.get_user_input.assert_any_await("매도할 수량을 입력하세요: ")
    app.cli_view.get_user_input.assert_any_await("매도 가격을 입력하세요 (시장가: 0): ")


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
            "stck_prpr": "70500",
            "prdy_vrss": "1200",
            "prdy_ctrt": "1.73"
        }
    )

    mock_call_api = mocker.patch(
        'brokers.korea_investment.korea_invest_api_base.KoreaInvestApiBase.call_api',
        return_value=mock_response
    )

    # --- Act ---
    running_status = await app._execute_action("5")

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
            "stck_prpr": "70500",
            "stck_oprc": "69500",
            "prdy_vrss": "1000",
            "prdy_ctrt": "1.44"
        }
    )

    mock_call_api = mocker.patch(
        'brokers.korea_investment.korea_invest_api_base.KoreaInvestApiBase.call_api',
        return_value=mock_response
    )

    # --- Act ---
    running_status = await app._execute_action("6")

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
    running_status = await app._execute_action("7")

    # --- Assert (검증) ---
    assert running_status == True
    mock_call_api.assert_awaited_once()
    app.cli_view.get_user_input.assert_awaited_once_with("호가를 조회할 종목 코드를 입력하세요: ")

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
    running_status = await app._execute_action("8")

    # --- Assert (검증) ---
    assert running_status == True
    mock_call_api.assert_awaited_once()
    app.cli_view.get_user_input.assert_awaited_once_with("시간대별 체결가를 조회할 종목 코드를 입력하세요: ")

@pytest.mark.asyncio
async def test_get_stock_news_full_integration(real_app_instance, mocker):
    """
    (통합 테스트) 종목 뉴스 조회: TradingApp → StockQueryService → BrokerAPIWrapper 흐름 테스트
    """
    app = real_app_instance

    # ✅ 사용자 입력 모킹
    mocker.patch.object(app.cli_view, 'get_user_input', new_callable=AsyncMock)
    app.cli_view.get_user_input.return_value = "005930"

    # ✅ API 응답 모킹 (뉴스 항목 일부 포함)
    mock_response = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value,
        msg1="정상",
        data={
            "output": [  # ✅ 이 구조가 필요
                {
                    "news_title": "삼성전자, 2분기 실적 발표",
                    "news_date": "20250721",
                    "news_time": "093000",
                    "news_summary": "영업이익 증가 발표"
                }
            ]
        }
    )

    mock_call_api = mocker.patch(
        'brokers.korea_investment.korea_invest_api_base.KoreaInvestApiBase.call_api',
        return_value=mock_response
    )

    # --- Act ---
    running_status = await app._execute_action("9")

    # --- Assert (검증) ---
    assert running_status == True
    mock_call_api.assert_awaited_once()
    app.cli_view.get_user_input.assert_awaited_once_with("뉴스를 조회할 종목 코드를 입력하세요: ")

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
    running_status = await app._execute_action("10")

    # --- Assert (검증) ---
    assert running_status == True
    mock_call_api.assert_awaited_once()
    app.cli_view.get_user_input.assert_awaited_once_with("정보를 조회할 ETF 코드를 입력하세요: ")

@pytest.mark.asyncio
async def test_search_stocks_by_keyword_full_integration(real_app_instance, mocker):
    """
    (통합 테스트) 키워드로 종목 검색: TradingApp → StockQueryService → BrokerAPIWrapper 흐름 테스트
    """
    app = real_app_instance

    # ✅ 사용자 입력 모킹
    mocker.patch.object(app.cli_view, 'get_user_input', new_callable=AsyncMock)
    app.cli_view.get_user_input.return_value = "삼성"

    # ✅ API 응답 모킹 (검색 결과 포함)
    mock_response = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value,
        msg1="정상",
        data={
            "output": [
                {"code": "005930", "name": "삼성전자"},
                {"code": "005935", "name": "삼성전자우"}
            ]
        }
    )

    mock_call_api = mocker.patch(
        'brokers.korea_investment.korea_invest_api_base.KoreaInvestApiBase.call_api',
        return_value=mock_response
    )

    # --- Act ---
    running_status = await app._execute_action("11")

    # --- Assert (검증) ---
    assert running_status == True
    mock_call_api.assert_awaited_once()
    app.cli_view.get_user_input.assert_awaited_once_with("검색할 키워드를 입력하세요: ")


@pytest.mark.asyncio
async def test_get_top_stocks_full_integration(real_app_instance, mocker):
    """
    (통합 테스트) 상위 랭킹 조회 (rise|fall|volume|foreign): TradingApp → StockQueryService → BrokerAPIWrapper 흐름 테스트
    """
    app = real_app_instance

    # ✅ 사용자 입력 모킹
    mocker.patch.object(app.cli_view, 'get_user_input', new_callable=AsyncMock)
    app.cli_view.get_user_input.return_value = "rise"

    # ✅ API 응답 모킹 (상위 랭킹 종목 리스트)
    mock_response = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value,
        msg1="정상",
        data={
            "output": [
                {"code": "005930", "name": "삼성전자", "change_rate": "3.2"},
                {"code": "000660", "name": "SK하이닉스", "change_rate": "2.7"}
            ]
        }
    )

    mock_call_api = mocker.patch(
        'brokers.korea_investment.korea_invest_api_base.KoreaInvestApiBase.call_api',
        return_value=mock_response
    )

    # --- Act ---
    running_status = await app._execute_action("12")

    # --- Assert (검증) ---
    assert running_status == True
    mock_call_api.assert_awaited_once()
    app.cli_view.get_user_input.assert_awaited_once_with("조회할 랭킹 종류를 입력하세요 (rise|fall|volume|foreign): ")

@pytest.mark.asyncio
async def test_get_top_market_cap_stocks_full_integration(real_app_instance, mocker):
    """
    (통합 테스트) 시가총액 상위 조회 (실전 전용): TradingApp → StockQueryService → BrokerAPIWrapper 흐름 테스트
    """
    app = real_app_instance

    # ✅ 실전 투자 환경으로 설정
    app.env.is_paper_trading = False

    # ✅ API 응답 모킹 (시가총액 상위 종목 목록)
    mock_response = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value,
        msg1="정상",
        data=[
            {"mksc_shrn_iscd": "005930", "code": "005930", "name": "삼성전자"},
            {"mksc_shrn_iscd": "000660", "code": "000660", "name": "SK하이닉스"}
        ]
    )

    mock_call_api = mocker.patch(
        'brokers.korea_investment.korea_invest_api_base.KoreaInvestApiBase.call_api',
        return_value=mock_response
    )

    # --- Act ---
    running_status = await app._execute_action("13")

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

    # ✅ 실전 투자 환경으로 설정
    app.env.set_trading_mode(False)  # ← 이게 실제 API 내부 속성까지 반영

    # ✅ API 응답 모킹 (시가총액 상위 + 현재가)
    mock_top_response = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value,
        msg1="정상",
        data=[
            {"mksc_shrn_iscd": "005930", "stck_avls": "1000000000"},
            {"mksc_shrn_iscd": "000660", "stck_avls": "500000000"}
        ]
    )
    mock_price_response = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value,
        msg1="정상",
        data={
            "stck_prpr": "70500",
            "prdy_vrss": "1200",
            "prdy_ctrt": "1.73"
        }
    )

    # 첫 번째 호출: 시가총액 상위 종목 목록 조회
    # 두 번째 이후: 종목별 현재가 조회
    mock_call_api = mocker.patch(
        'brokers.korea_investment.korea_invest_api_base.KoreaInvestApiBase.call_api',
        side_effect=[mock_top_response, mock_price_response, mock_price_response]
    )

    # --- Act ---
    running_status = await app._execute_action("14")

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

    # ✅ 실전 투자 환경으로 설정
    app.env.is_paper_trading = False

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
    running_status = await app._execute_action("15")

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

    # ✅ 실전 투자 환경으로 설정
    app.env.set_trading_mode(False)

    # ✅ 모의 응답: 시가총액 상위 종목 코드 조회 → 종목 코드 리스트 반환
    mock_top_response = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value,
        msg1="정상",
        data=[
            {"mksc_shrn_iscd": "005930", "stck_avls": "492,000,000,000"},
            {"mksc_shrn_iscd": "000660", "stck_avls": "110,000,000,000"}
        ]
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
    running_status = await app._execute_action("16")

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

    # ✅ 실전 투자 환경으로 설정
    app.env.set_trading_mode(False)

    mocker.patch.object(
        app.trading_service._broker_api_wrapper,
        "get_all_stock_codes",
        return_value=pd.DataFrame({
            "종목코드": ["005930", "000660"],
            "종목명": ["삼성전자", "SK하이닉스"]
        })
    )

    # ── ② 각 종목 get_current_price() 용 mock (open·현재가·등락률 포함) ─────
    price_payload = {
        "stck_oprc": "69500",
        "stck_prpr": "70500",
        "prdy_ctrt": "29.85"
    }
    mock_price_response = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value,
        msg1="정상",
        data=price_payload
    )

    # ── call_api() 호출 순서: ① 코드-목록 → ② 삼성전자 → ③ SK하이닉스 ───────
    mock_call_api = mocker.patch(
        "brokers.korea_investment.korea_invest_api_base."
        "KoreaInvestApiBase.call_api",
        side_effect=[mock_price_response,
                     mock_price_response]
    )

    # --- Act ---
    running_status = await app._execute_action("17")

    # --- Assert (검증) ---
    assert running_status == True
    assert mock_call_api.await_count == 2

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
    mock_subscribe = mocker.patch.object(
        app.trading_service._broker_api_wrapper._client._websocketAPI,
        "subscribe_realtime_price",
        new_callable=AsyncMock
    )

    # --- Act ---
    running_status = await app._execute_action("18")

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

    # ✅ 실전 투자 환경으로 설정
    app.env.set_trading_mode(False)

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

    mocker.patch.object(
        app.trading_service._broker_api_wrapper._client._quotations,
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
    running_status = await app._execute_action("20")

    # --- Assert (검증) ---
    assert running_status == True
    app.cli_view.display_strategy_running_message.assert_called_once_with("모멘텀")
    app.cli_view.display_top_stocks_success.assert_called_once()
    mock_executor.assert_awaited_once()
    app.cli_view.display_strategy_results.assert_called_once_with("모멘텀", mock_strategy_result)
    app.cli_view.display_follow_through_stocks.assert_called_once_with(mock_strategy_result["follow_through"])
    app.cli_view.display_not_follow_through_stocks.assert_called_once_with(mock_strategy_result["not_follow_through"])


@pytest.mark.asyncio
async def test_execute_action_momentum_strategy_market_cap_fail(real_app_instance, mocker):
    """
    (통합 테스트) 메뉴 '20' - 모멘텀 전략 실행 중 시가총액 상위 종목 조회 실패 시 흐름 검증

    TradingApp → StockQueryService → TradingService.get_top_market_cap_stocks_code
    → 실패 시 display_top_stocks_failure 및 로그 기록
    """
    app = real_app_instance

    # ✅ 실전 투자 환경으로 설정
    app.env.set_trading_mode(False)

    # ✅ 시장 개장 상태로 설정
    mocker.patch.object(app.time_manager, "is_market_open", return_value=True)

    # ✅ 종목 조회 실패 응답 (rt_cd != '0')
    fail_response = ResCommonResponse(
        rt_cd=ErrorCode.API_ERROR.value,
        msg1="시가총액 조회 실패",
        data=None
    )

    # ✅ 실패 응답 모킹
    mocker.patch.object(
        app.trading_service._broker_api_wrapper._client._quotations,
        "get_top_market_cap_stocks_code",
        new_callable=AsyncMock,
        return_value=fail_response
    )

    # ✅ 메시지 출력 메서드 모킹
    app.cli_view.display_top_stocks_failure = MagicMock()
    app.logger.warning = MagicMock()

    # --- Act ---
    running_status = await app._execute_action("20")

    # --- Assert (검증) ---
    assert running_status == True
    app.cli_view.display_top_stocks_failure.assert_called_once_with("시가총액 조회 실패")
    app.logger.warning.assert_called()
