# integration_test/it_trading_app.py
import pytest
import asyncio
import json
import app
from app.trading_app import TradingApp
from unittest.mock import AsyncMock, MagicMock
from common.types import ResCommonResponse, ResTopMarketCapApiItem, ResFluctuation, ErrorCode
from brokers.korea_investment.korea_invest_trading_api import KoreaInvestApiTrading
from brokers.korea_investment.korea_invest_trid_keys import TrIdLeaf
from brokers.korea_investment.korea_invest_url_keys import EndpointKey
from app.user_action_executor import UserActionExecutor
from tests.integration_test import ctx  # ✅ IDE가 심볼을 인식합니다.


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

    # # 2. Hashkey 생성 로직 모킹
    # mock_trading_api_instance = MagicMock()
    # mock_trading_api_instance._get_hashkey.return_value = "mock_hashkey_for_it_test"
    # mocker.patch(f'{KoreaInvestApiTrading.__module__}.{KoreaInvestApiTrading.__name__}',
    #              return_value=mock_trading_api_instance)

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


# @pytest.mark.asyncio
# async def test_execute_action_select_environment_success_real(real_app_instance, mocker):
#     """
#     (통합 테스트) 메뉴 '0' - 거래 환경 변경 성공 시 running_status 유지
#     """
#     app = real_app_instance

#     # ✅ _select_environment() 모킹: 성공
#     mocker.patch.object(app, "select_environment", new_callable=AsyncMock, return_value=True)
#     app.logger.info = MagicMock()

#     # --- 실행 ---
#     executor = UserActionExecutor(app)
#     running_status = await executor.execute("0")

#     # --- 검증 ---
#     app.logger.info.assert_called_once_with("거래 환경 변경을 시작합니다.")
#     assert running_status is True


# @pytest.mark.asyncio
# async def test_execute_action_invalidate_token_success_real(real_app_instance):
#     """
#     (통합 테스트) 메뉴 '98' - 토큰 무효화 성공 흐름
#     TradingApp → TokenManager.invalidate_token → CLIView.display_token_invalidated_message
#     """
#     app = real_app_instance

#     # ✅ 의존성 모킹
#     app.env.invalidate_token = MagicMock()
#     app.cli_view.display_token_invalidated_message = MagicMock()

#     # --- 실행 ---
#     executor = UserActionExecutor(app)
#     running_status = await executor.execute("998")

#     # --- 검증 ---
#     app.env.invalidate_token.assert_called_once()
#     app.cli_view.display_token_invalidated_message.assert_called_once()
#     assert running_status is True


# @pytest.mark.asyncio
# async def test_execute_action_exit_success_real(real_app_instance):
#     """
#     (통합 테스트) 메뉴 '99' - 프로그램 종료 처리 흐름
#     TradingApp → CLIView.display_exit_message → running_status=False 반환
#     """
#     app = real_app_instance

#     # ✅ 종료 메시지 출력 함수 모킹
#     app.cli_view.display_exit_message = MagicMock()

#     # --- 실행 ---
#     executor = UserActionExecutor(app)
#     running_status = await executor.execute("999")

#     # --- 검증 ---
#     app.cli_view.display_exit_message.assert_called_once()
#     assert running_status is False


# @pytest.mark.asyncio
# async def test_execute_action_select_environment_fail_real(real_app_instance, mocker):
#     """
#     (통합 테스트) 메뉴 '0' - 거래 환경 변경 실패 시 running_status = False
#     """
#     app = real_app_instance

#     # ✅ _select_environment() 모킹: 실패
#     mocker.patch.object(app, "select_environment", new_callable=AsyncMock, return_value=False)
#     app.logger.info = MagicMock()

#     # --- 실행 ---
#     executor = UserActionExecutor(app)
#     running_status = await executor.execute("0")

#     # --- 검증 ---
#     app.logger.info.assert_called_once_with("거래 환경 변경을 시작합니다.")
#     assert running_status is False


# @pytest.mark.asyncio
# async def test_get_current_price_full_integration_real(real_app_instance, mocker):
#     """
#     (통합 테스트) 현재가 조회 시 TradingApp → StockQueryService → BrokerAPIWrapper →
#     get_current_price → call_api 흐름을 따라 실제 서비스가 실행되며,
#     최하위 API 호출만 모킹하여 검증합니다.
#     """
#     # --- Arrange ---
#     app = real_app_instance
#     ctx.ki.bind(app)  # ki_providers 역할

#     # ✅ 표준 스키마(output 키)로 payload 구성
#     payload = {
#         "rt_cd": "0",
#         "msg1": "정상",
#         "output": {
#             "stck_prpr": "70500",
#             "prdy_vrss": "1200",
#             "prdy_ctrt": "1.73",
#             "stck_cntg_hour": "101500"   # 선택이지만 있으면 뷰표시 깔끔
#         }
#     }

#     # 1) _execute_request는 '실행되도록' 스파이만
#     # 2) 네트워크 레이어 차단: 세션의 get만 모킹

#     quot_api = ctx.ki.quot
#     spy_exec, mock_get = ctx.spy_get(quot_api, mocker, payload)


#     # 입력 모킹
#     test_stock_code = "005930"
#     mocker.patch.object(app.cli_view, "get_user_input", new_callable=AsyncMock, return_value=test_stock_code)

#     app.cli_view.handle_get_current_stock_price = MagicMock()
#     app.cli_view.display_etf_info_error = MagicMock()

#     app.cli_view.display_current_stock_price = MagicMock()
#     app.cli_view.display_current_stock_price_error = MagicMock()

#     # --- 실행 ---
#     executor = UserActionExecutor(app)
#     ok = await executor.execute("1")
#     assert ok is True

#     # === _execute_request 레벨: 메서드/최종 URL/params 확인 ===
#     spy_exec.assert_called()
#     method, url = spy_exec.call_args.args[:2]
#     assert method == "GET"

#     # === 실제 세션 호출: headers/params 정확히 확인 ===
#     mock_get.assert_awaited_once()
#     g_args, g_kwargs = mock_get.call_args
#     req_url = g_args[0] if g_args else g_kwargs.get("url")
#     req_headers = g_kwargs.get("headers") or {}
#     req_params  = g_kwargs.get("params") or {}

#     trid_provider = ctx.ki.trid_quotations
#     env = ctx.ki.env
#     expected_trid = trid_provider.quotations(TrIdLeaf.INQUIRE_PRICE)
#     custtype = env.active_config["custtype"]
#     expected_url = ctx.expected_url_for_quotations(app, EndpointKey.INQUIRE_PRICE)

#     assert req_url == expected_url
#     assert req_headers.get("tr_id") == expected_trid
#     assert req_headers.get("custtype") == custtype
#     assert req_params.get("fid_input_iscd") == test_stock_code

#     # 뷰 호출(성공 경로)
#     app.cli_view.display_current_stock_price.assert_called_once()
#     app.cli_view.display_current_stock_price_error.assert_not_called()


# @pytest.mark.asyncio
# async def test_get_account_balance_full_integration_real(real_app_instance, mocker):
#     """
#     (통합 테스트) 계좌 잔고 조회 시, TradingApp -> TradingService -> BrokerAPIWrapper의
#     실제 로직을 모두 실행하고, 최하단 네트워크 호출('call_api')만 모킹하여 검증합니다.
#     """
#     # --- Arrange (준비) ---
#     app = real_app_instance

#     # 1. 모킹할 최종 API 응답을 미리 정의합니다.
#     mock_balance_data = {"dnca_tot_amt": "1000000", "tot_evlu_amt": "1200000"}
#     mock_api_response = ResCommonResponse(
#         rt_cd=ErrorCode.SUCCESS.value,
#         msg1="정상",
#         data=mock_balance_data
#     )

#     # 2. 가장 낮은 레벨의 API 호출 메서드를 모킹합니다.
#     #    이것이 실제 네트워크 통신을 차단하는 유일한 지점입니다.
#     mock_call_api = mocker.patch(
#         'brokers.korea_investment.korea_invest_api_base.KoreaInvestApiBase.call_api',
#         return_value=mock_api_response
#     )

#     # 2번 계좌 잔고 조회
#     mocker.patch.object(app.cli_view, 'get_user_input', new_callable=AsyncMock)
#     mocker.patch.object(app.cli_view, 'display_account_balance', new_callable=MagicMock)
#     mocker.patch.object(app.cli_view, 'display_account_balance_failure', new_callable=MagicMock)

#     # --- Act ---
#     executor = UserActionExecutor(app)
#     running_status = await executor.execute("2")

#     # --- Assert (검증) ---
#     assert running_status == True

#     mock_call_api.assert_awaited_once()

#     called_args, called_kwargs = mock_call_api.call_args

#     method = called_args[0]
#     key_or_path = called_args[1]

#     assert method == "GET"
#     assert key_or_path ==  EndpointKey.INQUIRE_BALANCE

#     # 2. 성공 경로의 비즈니스 로직이 올바르게 수행되었는지 검증합니다.
#     # ✅ 성공 로그가 올바른 데이터와 함께 기록되었는지 확인합니다.
#     app.logger.info.assert_any_call(f"계좌 잔고 조회 성공: {mock_balance_data}")

#     # ✅ 성공 결과를 표시하는 View 메서드가 올바른 데이터로 호출되었는지 확인합니다.
#     app.cli_view.display_account_balance.assert_called_once_with(mock_balance_data)
#     app.cli_view.display_account_balance_failure.assert_not_called()


# @pytest.mark.asyncio
# async def test_buy_stock_full_integration_real(real_app_instance, mocker):
#     app = real_app_instance
#     app.time_manager.is_market_open = mocker.MagicMock(return_value=True)

#     # 입력(3개)
#     mocker.patch.object(app.cli_view, "get_user_input", new_callable=AsyncMock)
#     code, qty, price = "005930", "10", "70000"
#     app.cli_view.get_user_input.side_effect = [code, qty, price]

#     payload = {"rt_cd": "0", "msg1": "정상", "output": {"ord_no": "1234567890"}}

#     ctx.ki.bind(app)
#     order_api = ctx.ki.trading_api or ctx.ki.account_api
#     assert order_api is not None

#     # ✅ 해시키+주문 동시 패치
#     spy_exec, mock_post, expected_order_url = ctx.patch_post_with_hash_and_order(order_api, mocker, payload)

#     # 출력 뷰어는 호출만 검증
#     app.cli_view.display_order_success = MagicMock()
#     app.cli_view.display_order_failure = MagicMock()

#     ok = await UserActionExecutor(app).execute("3")
#     assert ok is True

#     # _execute_request는 최소 1회(주문) 호출되어야 함
#     spy_exec.assert_called()
#     method, _ = spy_exec.call_args.args[:2]
#     assert method == "POST"

#     # 어떤 post 콜이 해시키/주문인지 분리
#     order_call, hash_call = None, None
#     for c in mock_post.call_args_list:
#         args, kwargs = c
#         url = (args[0] if args else kwargs.get("url"))
#         u = str(url)
#         if "hashkey" in u:
#             hash_call = c
#         if expected_order_url in u:
#             order_call = c

#     # assert hash_call is not None, "해시키 POST 호출이 없습니다."
#     assert order_call is not None, "주문 POST 호출이 없습니다."

#     # 주문 콜의 헤더/바디 검증
#     _, o_kwargs = order_call
#     o_headers = o_kwargs.get("headers") or {}
#     o_data = o_kwargs.get("data")
#     assert "json" not in o_kwargs  # 반드시 data= 로 전송

#     # tr_id / custtype / hashkey
#     leaf = getattr(TrIdLeaf, "ORDER_CASH_BUY_REAL", None)
#     trid_provider = ctx.ki.trid_trading
#     kind = "trading"
#     expected_trid = ctx.resolve_trid(trid_provider, leaf, kind)

#     assert o_headers.get("tr_id") == expected_trid
#     assert o_headers.get("custtype") == ctx.ki.env.active_config["custtype"]
#     # assert o_headers.get("hashkey") == "abc123"  # ✅ 해시키가 헤더에 붙었는지

#     # 본문 파싱 후 값 확인
#     def parse_body(d):
#         if isinstance(d, (bytes, bytearray)): d = d.decode("utf-8")
#         if isinstance(d, str):
#             try:
#                 return json.loads(d)
#             except Exception:
#                 return d
#         return d

#     body = parse_body(o_data)

#     if isinstance(body, dict):
#         # 종목/수량은 그대로
#         assert any(str(body.get(k)) == code for k in ("PDNO", "pdno", "code", "stock_code"))
#         assert any(ctx.to_int(body.get(k)) == int(qty) for k in ("ORD_QTY", "qty", "quantity"))

#         # ✅ 가격 키 후보에 ORD_UNPR 추가
#         price_keys = ("ORD_UNPR", "ord_unpr", "ORD_PR", "price", "ord_pr")
#         ord_dvsn = (body.get("ORD_DVSN") or body.get("ord_dvsn"))
#         if ord_dvsn in (None, "", "01", 1, "LIMIT"):  # 지정가일 때는 가격 필수
#             assert any(ctx.to_int(body.get(k)) == int(price) for k in price_keys), f"가격 미일치/누락: {body}"
#         else:
#             # 시장가(예: '00')라면 가격 0/누락 가능 → 스킵
#             pass
#     else:
#         # 문자열 본문일 경우 단순 포함 체크
#         assert code in body and qty in body and price in body

#     # 뷰 호출(성공 경로)
#     app.cli_view.display_order_success.assert_called_once()
#     app.cli_view.display_order_failure.assert_not_called()


# @pytest.mark.asyncio
# async def test_sell_stock_full_integration_real(real_app_instance, mocker):
#     app = real_app_instance
#     app.time_manager.is_market_open = mocker.MagicMock(return_value=True)

#     # 입력(3개): 종목/수량/가격
#     mocker.patch.object(app.cli_view, "get_user_input", new_callable=AsyncMock)
#     code, qty, price = "005930", "5", "71000"
#     app.cli_view.get_user_input.side_effect = [code, qty, price]

#     # 주문 성공 payload
#     payload = {"rt_cd": "0", "msg1": "정상", "output": {"ord_no": "S123456789"}}

#     # 주문 API 선택(trading 우선 → account)
#     ctx.ki.bind(app)
#     order_api = ctx.ki.trading_api or ctx.ki.account_api
#     assert order_api is not None

#     # 해시키 + 주문 동시 패치 (세션 post 단에서 URL별 분기)
#     spy_exec, mock_post, expected_order_url = ctx.patch_post_with_hash_and_order(order_api, mocker, payload)

#     # 출력 뷰어는 호출만 검증
#     app.cli_view.display_order_success = MagicMock()
#     app.cli_view.display_order_failure = MagicMock()

#     # === 실행 (메뉴 '4' = 매도 가정) ===
#     ok = await UserActionExecutor(app).execute("4")
#     assert ok is True

#     # === _execute_request 레벨: 메서드만 확인 ===
#     spy_exec.assert_called()
#     method, _ = spy_exec.call_args.args[:2]
#     assert method == "POST"

#     # === 어떤 post 콜이 해시키/주문인지 분리 ===
#     order_call, hash_call = None, None
#     for c in mock_post.call_args_list:
#         args, kwargs = c
#         url = args[0] if args else kwargs.get("url")
#         u = str(url)
#         if "hashkey" in u:
#             hash_call = c
#         if expected_order_url in u:
#             order_call = c

#     # assert hash_call is not None, "해시키 POST 호출이 없습니다."
#     assert order_call is not None, "주문 POST 호출이 없습니다."

#     # === 주문 콜의 헤더/바디 검증 ===
#     _, o_kwargs = order_call
#     o_headers = o_kwargs.get("headers") or {}
#     o_data = o_kwargs.get("data")
#     assert "json" not in o_kwargs  # 반드시 data= 로 전송

#     # tr_id / custtype / hashkey
#     leaf = getattr(TrIdLeaf, "ORDER_CASH_SELL_REAL", None)
#     trid_provider = ctx.ki.trid_trading
#     kind = "trading"
#     expected_trid = ctx.resolve_trid(trid_provider, leaf, kind)

#     assert o_headers.get("tr_id") == expected_trid
#     assert o_headers.get("custtype") == ctx.ki.env.active_config["custtype"]
#     # assert o_headers.get("hashkey") == "abc123"  # 해시키가 헤더에 붙었는지

#     # 본문 파싱
#     def parse_body(d):
#         if isinstance(d, (bytes, bytearray)):
#             d = d.decode("utf-8")
#         if isinstance(d, str):
#             try:
#                 return json.loads(d)
#             except Exception:
#                 return d
#         return d

#     body = parse_body(o_data)

#     if isinstance(body, dict):
#         # 종목/수량
#         assert any(str(body.get(k)) == code for k in ("PDNO", "pdno", "code", "stock_code"))
#         assert any(ctx.to_int(body.get(k)) == int(qty) for k in ("ORD_QTY", "qty", "quantity"))

#         # 가격 키 후보 (지정가일 때 필수) — KIS는 보통 ORD_UNPR
#         price_keys = ("ORD_UNPR", "ord_unpr", "ORD_PR", "price", "ord_pr")
#         ord_dvsn = body.get("ORD_DVSN") or body.get("ord_dvsn")
#         if ord_dvsn in (None, "", "01", 1, "LIMIT"):  # 지정가
#             assert any(ctx.to_int(body.get(k)) == int(price) for k in price_keys), f"가격 미일치/누락: {body}"
#         # 시장가(예: '00')는 가격 0/누락 가능 → 스킵
#     else:
#         # 문자열 본문일 경우 단순 포함 체크
#         assert code in body and qty in body and price in body

#     # 뷰 호출(성공 경로)
#     app.cli_view.display_order_success.assert_called_once()
#     app.cli_view.display_order_failure.assert_not_called()


# @pytest.mark.asyncio
# async def test_display_stock_change_rate_full_integration_real(real_app_instance, mocker):
#     """
#     (통합 테스트) 전일대비 등락률 조회:
#     TradingApp → StockQueryService → BrokerAPIWrapper → (quotations api) → call_api → _execute_request
#     """
#     app = real_app_instance

#     # 입력 모킹
#     prompt = "조회할 종목 코드를 입력하세요 (삼성전자: 005930): "
#     code = "005930"
#     mocker.patch.object(app.cli_view, "get_user_input", new_callable=AsyncMock, return_value=code)

#     # 시세 API 응답(payload) – 표준 스키마
#     payload = {
#         "rt_cd": "0",
#         "msg1": "정상",
#         "output": {
#             "stck_prpr": "70500",  # 현재가
#             "stck_oprc": "70000",  # 시가 (open vs current 계산용)
#             "prdy_vrss": "1200",  # 전일대비
#             "prdy_ctrt": "1.73",  # 전일대비율(%)
#             "prdy_vrss_sign": "1",  # 부호 코드 (당신의 _get_sign_from_code 매핑에 맞춰 1=상승, 2=하락, 3=보합 등)
#         }
#     }

#     # 바인딩 + 시세 API 선택
#     ctx.ki.bind(app)
#     quot_api = ctx.ki.quot

#     # _execute_request 스파이 + 세션 get 모킹
#     spy_exec, mock_get = ctx.spy_get(quot_api, mocker, payload)

#     # 출력 뷰어는 호출만 검증
#     app.cli_view.display_stock_change_rate_success = MagicMock()
#     app.cli_view.display_stock_change_rate_failure = MagicMock()

#     ok = await UserActionExecutor(app).execute("20")
#     assert ok is True

#     # _execute_request: 메서드만 확인(중복 최소화)
#     spy_exec.assert_called()
#     method, _ = spy_exec.call_args.args[:2]
#     assert method == "GET"

#     # 실제 세션 호출: 최종 URL/헤더/파라미터 검증
#     mock_get.assert_awaited_once()
#     g_args, g_kwargs = mock_get.call_args
#     req_url     = g_args[0] if g_args else g_kwargs.get("url")
#     req_headers = g_kwargs.get("headers") or {}
#     req_params  = g_kwargs.get("params") or {}

#     expected_url = ctx.expected_url_for_quotations(app, EndpointKey.INQUIRE_PRICE)

#     # TRID: quotations용으로 계산 (프로바이더 메서드명이 다를 수 있어 폴백 처리)
#     trid_provider = ctx.ki.trid_quotations
#     if hasattr(trid_provider, "quotations"):
#         expected_trid = trid_provider.quotations(TrIdLeaf.INQUIRE_PRICE)
#     else:
#         expected_trid = ctx.resolve_trid(trid_provider, TrIdLeaf.INQUIRE_PRICE, kind="quotations")

#     assert req_url == expected_url
#     assert req_headers.get("tr_id") == expected_trid
#     assert req_headers.get("custtype") == ctx.ki.env.active_config["custtype"]
#     assert req_params.get("fid_input_iscd") == code

#     # 프롬프트가 정확히 사용되었는지
#     app.cli_view.get_user_input.assert_awaited_once_with(prompt)

#     # 뷰 호출(성공 경로)
#     app.cli_view.display_stock_change_rate_success.assert_called_once()
#     app.cli_view.display_stock_change_rate_failure.assert_not_called()


# @pytest.mark.asyncio
# async def test_display_stock_vs_open_price_full_integration_real(real_app_instance, mocker):
#     """
#     (통합 테스트) 시가대비 등락률 조회:
#     TradingApp → StockQueryService → BrokerAPIWrapper → (quotations api) → call_api → _execute_request
#     """
#     app = real_app_instance

#     # 입력 모킹
#     prompt = "조회할 종목 코드를 입력하세요 (삼성전자: 005930): "
#     code = "005930"
#     mocker.patch.object(app.cli_view, "get_user_input", new_callable=AsyncMock, return_value=code)

#     # 시세 API 응답(payload) – 표준 스키마에 현재가/시가 포함
#     payload = {
#         "rt_cd": "0",
#         "msg1": "정상",
#         "output": {
#             "stck_prpr": "70500",  # 현재가
#             "stck_oprc": "69500",  # 시가
#             "prdy_vrss": "1000",
#             "prdy_ctrt": "1.44",
#         }
#     }

#     # 바인딩 + 시세 API
#     ctx.ki.bind(app)
#     quot_api = ctx.ki.quot

#     # _execute_request 스파이 + 세션 get 모킹
#     spy_exec, mock_get = ctx.spy_get(quot_api, mocker, payload)

#     # 출력 뷰어는 호출만 검증
#     app.cli_view.display_stock_vs_open_price_success = MagicMock()
#     app.cli_view.display_stock_vs_open_price_failure = MagicMock()

#     ok = await UserActionExecutor(app).execute("21")
#     assert ok is True

#     # _execute_request: 메서드만 확인
#     spy_exec.assert_called()
#     method, _ = spy_exec.call_args.args[:2]
#     assert method == "GET"

#     # 실제 세션 호출: 최종 URL/헤더/파라미터 검증
#     mock_get.assert_awaited_once()
#     g_args, g_kwargs = mock_get.call_args
#     req_url     = g_args[0] if g_args else g_kwargs.get("url")
#     req_headers = g_kwargs.get("headers") or {}
#     req_params  = g_kwargs.get("params") or {}

#     expected_url = ctx.expected_url_for_quotations(app, EndpointKey.INQUIRE_PRICE)

#     # TRID 계산 (quotations 컨텍스트)
#     trid_provider = ctx.ki.trid_quotations
#     expected_trid = (
#         trid_provider.quotations(TrIdLeaf.INQUIRE_PRICE)
#         if hasattr(trid_provider, "quotations")
#         else ctx.resolve_trid(trid_provider, TrIdLeaf.INQUIRE_PRICE, kind="quotations")
#     )

#     assert req_url == expected_url
#     assert req_headers.get("tr_id") == expected_trid
#     assert req_headers.get("custtype") == ctx.ki.env.active_config["custtype"]
#     assert req_params.get("fid_input_iscd") == code

#     # 프롬프트 확인
#     app.cli_view.get_user_input.assert_awaited_once_with(prompt)

#     # 뷰 호출(성공 경로)
#     app.cli_view.display_stock_vs_open_price_success.assert_called_once()
#     app.cli_view.display_stock_vs_open_price_failure.assert_not_called()


# @pytest.mark.asyncio
# async def test_get_asking_price_full_integration_real(real_app_instance, mocker):
#     """
#     (통합 테스트) 실시간 호가 조회:
#     TradingApp → StockQueryService → BrokerAPIWrapper → (quotations api) → call_api → _execute_request
#     """
#     app = real_app_instance

#     # 입력 모킹
#     code = "005930"
#     mocker.patch.object(app.cli_view, "get_user_input", new_callable=AsyncMock, return_value=code)

#     # 시세 API 응답(payload) – 표준 스키마 'output'에 호가 정보 포함
#     payload = {
#         "rt_cd": "0",
#         "msg1": "정상",
#         "output1": {
#             "askp1": "70500",
#             "bidp1": "70400",
#             "askp_rsqn1": "100",
#             "bidp_rsqn1": "120",
#             # (선택) 더 깊은 레벨을 보고 싶으면 아래처럼 추가
#             # "askp2": "70600", "bidp2": "70300",
#             # "askp_rsqn2": "80", "bidp_rsqn2": "150",
#         },
#         # (선택) 시간외 단일가가 필요하면 여기
#         # "output2": {...}
#     }

#     # 바인딩 + 시세 API
#     ctx.ki.bind(app)
#     quot_api = ctx.ki.quot

#     # _execute_request 스파이 + 세션 get 모킹
#     spy_exec, mock_get = ctx.spy_get(quot_api, mocker, payload)

#     app.cli_view.display_asking_price = MagicMock()
#     app.cli_view.display_asking_price_error = MagicMock()

#     ok = await UserActionExecutor(app).execute("22")
#     assert ok is True

#     # _execute_request: 메서드만 확인(중복 최소화)
#     spy_exec.assert_called()
#     method, _ = spy_exec.call_args.args[:2]
#     assert method == "GET"

#     # 실제 세션 호출: 최종 URL/헤더/파라미터 검증
#     mock_get.assert_awaited_once()
#     g_args, g_kwargs = mock_get.call_args
#     req_url     = g_args[0] if g_args else g_kwargs.get("url")
#     req_headers = g_kwargs.get("headers") or {}
#     req_params  = g_kwargs.get("params") or {}

#     expected_url = ctx.expected_url_for_quotations(app, EndpointKey.ASKING_PRICE)

#     # TRID 계산 (quotations 컨텍스트)
#     trid_provider = ctx.ki.trid_quotations
#     expected_trid = (
#         trid_provider.quotations(TrIdLeaf.ASKING_PRICE)
#         if hasattr(trid_provider, "quotations")
#         else ctx.resolve_trid(trid_provider, TrIdLeaf.ASKING_PRICE, kind="quotations")
#     )

#     assert req_url == expected_url
#     assert req_headers.get("tr_id") == expected_trid
#     assert req_headers.get("custtype") == ctx.ki.env.active_config["custtype"]
#     assert req_params.get("fid_input_iscd") == code

#     # 프롬프트 문구 사용 검증(부분 일치)
#     app.cli_view.get_user_input.assert_awaited_once()
#     called_prompt = app.cli_view.get_user_input.await_args.args[0]
#     assert "호가를 조회할 종목 코드를 입력하세요" in called_prompt

#     # 뷰 호출(성공 경로)
#     app.cli_view.display_asking_price.assert_called_once()
#     app.cli_view.display_asking_price_error.assert_not_called()


# @pytest.mark.asyncio
# async def test_get_time_concluded_prices_full_integration_real(real_app_instance, mocker):
#     """
#     (통합 테스트) 시간대별 체결가 조회:
#     TradingApp → StockQueryService → BrokerAPIWrapper → (quotations api) → call_api → _execute_request
#     """
#     app = real_app_instance

#     # 입력 모킹
#     code = "005930"
#     prompt = "시간대별 체결가를 조회할 종목 코드를 입력하세요"
#     mocker.patch.object(app.cli_view, "get_user_input", new_callable=AsyncMock, return_value=code)

#     # 시세 API 응답(payload) – 표준 스키마 'output'에 필요한 필드 포함
#     payload = {
#         "rt_cd": "0",
#         "msg1": "정상",
#         "output": [
#             {
#                 "stck_bsop_date": "20250822",
#                 "stck_cntg_hour": "101500",
#                 "stck_prpr": "70200",
#                 "prdy_vrss": "100",     # (선택) 전일 대비
#                 "cntg_vol": "1000"
#             }
#         ]
#     }

#     # 바인딩 + 시세 API
#     ctx.ki.bind(app)
#     quot_api = ctx.ki.quot

#     # _execute_request 스파이 + 세션 get 모킹
#     spy_exec, mock_get = ctx.spy_get(quot_api, mocker, payload)

#     app.cli_view.display_time_concluded_prices = MagicMock()
#     app.cli_view.display_time_concluded_error = MagicMock()

#     ok = await UserActionExecutor(app).execute("23")
#     assert ok is True

#     # _execute_request: 메서드만 확인
#     spy_exec.assert_called()
#     method, _ = spy_exec.call_args.args[:2]
#     assert method == "GET"

#     # 실제 세션 호출: 최종 URL/헤더/파라미터 검증
#     mock_get.assert_awaited_once()
#     g_args, g_kwargs = mock_get.call_args
#     req_url     = g_args[0] if g_args else g_kwargs.get("url")
#     req_headers = g_kwargs.get("headers") or {}
#     req_params  = g_kwargs.get("params") or {}

#     # ✅ 엔드포인트/트리아이디: 전용 상수 우선, 없으면 유연 폴백
#     expected_url = ctx.expected_url_for_quotations(app, EndpointKey.TIME_CONCLUDE)
#     trid_provider = ctx.ki.trid_quotations
#     leaf = TrIdLeaf.TIME_CONCLUDE
#     expected_trid = (
#         trid_provider.quotations(leaf)
#         if hasattr(trid_provider, "quotations")
#         else ctx.resolve_trid(trid_provider, leaf, kind="quotations")
#     )
#     assert req_url == expected_url
#     assert req_headers.get("tr_id") == expected_trid
#     assert req_headers.get("custtype") == ctx.ki.env.active_config["custtype"]
#     assert req_params.get("fid_input_iscd") == code

#     # 프롬프트 문구 사용 검증(부분 일치)
#     app.cli_view.get_user_input.assert_awaited_once()
#     called_prompt = app.cli_view.get_user_input.await_args.args[0]
#     assert prompt in called_prompt

#     # 뷰 호출(성공 경로)
#     app.cli_view.display_time_concluded_prices.assert_called_once()
#     app.cli_view.display_time_concluded_error.assert_not_called()


# @pytest.mark.asyncio
# async def test_get_etf_info_full_integration_real(real_app_instance, mocker):
#     """
#     (통합 테스트) ETF 정보 조회:
#     TradingApp → StockQueryService → BrokerAPIWrapper → (quotations api) → call_api → _execute_request
#     """
#     app = real_app_instance

#     # 입력 모킹
#     etf_code = "069500"  # KODEX 200
#     prompt = "정보를 조회할 ETF 코드를 입력하세요"
#     mocker.patch.object(app.cli_view, "get_user_input", new_callable=AsyncMock, return_value=etf_code)

#     # 표준 스키마 'output'로 응답 페이로드 구성
#     payload = {
#         "rt_cd": "0",
#         "msg1": "정상",
#         "output": {
#             "etf_rprs_bstp_kor_isnm": "KODEX 200",
#             "stck_prpr": "41510",
#             "nav": "41500.00",
#             "stck_llam": "123456789000",
#             "prdy_ctrt": "0.45",
#         }
#     }

#     # 바인딩 + 시세 API 핸들
#     ctx.ki.bind(app)
#     quot_api = ctx.ki.quot

#     # _execute_request 스파이 + 세션 get 모킹
#     spy_exec, mock_get = ctx.spy_get(quot_api, mocker, payload)

#     app.cli_view.display_etf_info = MagicMock()
#     app.cli_view.display_etf_info_error = MagicMock()

#     ok = await UserActionExecutor(app).execute("24")
#     assert ok is True

#     # _execute_request: 메서드 확인
#     spy_exec.assert_called()
#     method, _ = spy_exec.call_args.args[:2]
#     assert method == "GET"

#     # 실제 세션 호출: 최종 URL/헤더/파라미터 검증
#     mock_get.assert_awaited_once()
#     g_args, g_kwargs = mock_get.call_args
#     req_url     = g_args[0] if g_args else g_kwargs.get("url")
#     req_headers = g_kwargs.get("headers") or {}
#     req_params  = g_kwargs.get("params") or {}

#     # ✅ 엄격: 고정 상수만 사용
#     expected_url = ctx.expected_url_for_quotations(app, EndpointKey.ETF_INFO)
#     trid_provider = ctx.ki.trid_quotations
#     expected_trid = trid_provider.quotations(TrIdLeaf.ETF_INFO)

#     assert req_url == expected_url
#     assert req_headers.get("tr_id") == expected_trid
#     assert req_headers.get("custtype") == ctx.ki.env.active_config["custtype"]
#     assert req_params.get("fid_input_iscd") == etf_code

#     # 프롬프트 문구 확인
#     app.cli_view.get_user_input.assert_awaited_once()
#     called_prompt = app.cli_view.get_user_input.await_args.args[0]
#     assert prompt in called_prompt

#     # 뷰 호출(성공 경로)
#     app.cli_view.display_etf_info.assert_called_once()
#     app.cli_view.display_etf_info_error.assert_not_called()


# @pytest.mark.asyncio
# async def test_get_ohlcv_day_full_integration_real(real_app_instance, mocker):
#     """
#     (통합 테스트-실전) OHLCV 일봉: call_api는 동일, TRID/헤더만 실전 환경 값으로 셋업됨
#     """
#     app = real_app_instance
#     ctx.ki.bind(app)
#     quot_api = ctx.ki.quot

#     payload = {
#         "rt_cd": "0",
#         "msg_cd": "MCA00000",
#         "msg1": "정상처리 되었습니다.",
#         "output2": [
#             {"stck_bsop_date":"20250812","stck_oprc":"70000","stck_hgpr":"71000","stck_lwpr":"69500","stck_clpr":"70500","acml_vol":"123456"},
#             {"stck_bsop_date":"20250813","stck_oprc":"70500","stck_hgpr":"71200","stck_lwpr":"70100","stck_clpr":"71000","acml_vol":"111111"},
#         ]
#     }
#     spy_exec, mock_get = ctx.spy_get(quot_api, mocker, payload)

#     code, period, limit = "005930", "D", "10"
#     mocker.patch.object(app.cli_view, "get_user_input", new_callable=AsyncMock)
#     app.cli_view.get_user_input.side_effect = [code, period, limit]

#     app.cli_view.display_ohlcv = MagicMock()
#     app.cli_view.display_ohlcv_error = MagicMock()

#     ok = await UserActionExecutor(app).execute("25")
#     assert ok is True

#     spy_exec.assert_called()
#     method, _ = spy_exec.call_args.args[:2]
#     assert method == "GET"

#     mock_get.assert_awaited_once()
#     g_args, g_kwargs = mock_get.call_args
#     req_url     = g_args[0] if g_args else g_kwargs.get("url")
#     req_headers = g_kwargs.get("headers") or {}
#     req_params  = g_kwargs.get("params") or {}

#     expected_url = ctx.expected_url_for_quotations(app, EndpointKey.DAILY_ITEMCHARTPRICE)
#     trid_provider = ctx.ki.trid_quotations
#     expected_trid = trid_provider.daily_itemchartprice()
#     assert req_url == expected_url
#     assert req_headers.get("tr_id") == expected_trid
#     assert req_headers.get("custtype") == ctx.ki.env.active_config["custtype"]
#     assert req_params.get("fid_input_iscd") == code

#     app.cli_view.display_ohlcv.assert_called_once()
#     app.cli_view.display_ohlcv_error.assert_not_called()



# @pytest.mark.asyncio
# async def test_get_ohlcv_week_full_integration_paper(real_app_instance, mocker):
#     """
#     (통합 테스트-모의) OHLCV 일봉:
#     TradingApp → StockQueryService → TradingService → BrokerAPIWrapper →
#     KoreaInvestApiQuotations.inquire_daily_itemchartprice → call_api → _execute_request
#     """
#     app = real_app_instance
#     ctx.ki.bind(app)
#     quot_api = ctx.ki.quot

#     # HTTP 레이어 모킹: 일봉 응답 payload (표준 'output' 리스트)
#     # ✅ 표준 래퍼 + output2 로 교체
#     payload = {
#         "rt_cd": "0",
#         "msg_cd": "MCA00000",
#         "msg1": "정상처리 되었습니다.",
#         "output2": [
#             {"stck_bsop_date":"20250812","stck_oprc":"70000","stck_hgpr":"71000","stck_lwpr":"69500","stck_clpr":"70500","acml_vol":"123456"},
#             {"stck_bsop_date":"20250813","stck_oprc":"70500","stck_hgpr":"71200","stck_lwpr":"70100","stck_clpr":"71000","acml_vol":"111111"},
#         ]
#     }
#     spy_exec, mock_get = ctx.spy_get(quot_api, mocker, payload)

#     # 입력: 종목코드 / 기간 D / limit
#     code, period, limit = "005930", "W", "5"
#     mocker.patch.object(app.cli_view, "get_user_input", new_callable=AsyncMock)
#     app.cli_view.get_user_input.side_effect = [code, period, limit]

#     # 출력 뷰어는 호출만 검증
#     app.cli_view.display_ohlcv = MagicMock()
#     app.cli_view.display_ohlcv_error = MagicMock()

#     # 실행 (메뉴 '11' = OHLCV 조회 가정)
#     ok = await UserActionExecutor(app).execute("25")
#     assert ok is True

#     # --- 최하단 호출 검증 ---
#     spy_exec.assert_called()
#     method, _ = spy_exec.call_args.args[:2]
#     assert method == "GET"

#     mock_get.assert_awaited_once()
#     g_args, g_kwargs = mock_get.call_args
#     req_url     = g_args[0] if g_args else g_kwargs.get("url")
#     req_headers = g_kwargs.get("headers") or {}
#     req_params  = g_kwargs.get("params") or {}

#     expected_url = ctx.expected_url_for_quotations(app, EndpointKey.DAILY_ITEMCHARTPRICE)
#     trid_provider = ctx.ki.trid_quotations
#     expected_trid = trid_provider.daily_itemchartprice()  # 일봉
#     assert req_url == expected_url
#     assert req_headers.get("tr_id") == expected_trid
#     assert req_headers.get("custtype") == ctx.ki.env.active_config["custtype"]
#     assert req_params.get("fid_input_iscd") == code  # 종목 코드 전달

#     # 뷰 호출(성공 경로)
#     app.cli_view.display_ohlcv.assert_called_once()
#     app.cli_view.display_ohlcv_error.assert_not_called()


# @pytest.mark.asyncio
# async def test_get_ohlcv_month_full_integration_paper(real_app_instance, mocker):
#     """
#     (통합 테스트-모의) OHLCV 일봉:
#     TradingApp → StockQueryService → TradingService → BrokerAPIWrapper →
#     KoreaInvestApiQuotations.inquire_daily_itemchartprice → call_api → _execute_request
#     """
#     app = real_app_instance
#     ctx.ki.bind(app)
#     quot_api = ctx.ki.quot

#     # HTTP 레이어 모킹: 일봉 응답 payload (표준 'output' 리스트)
#     # ✅ 표준 래퍼 + output2 로 교체
#     payload = {
#         "rt_cd": "0",
#         "msg_cd": "MCA00000",
#         "msg1": "정상처리 되었습니다.",
#         "output2": [
#             {"stck_bsop_date":"20250812","stck_oprc":"70000","stck_hgpr":"71000","stck_lwpr":"69500","stck_clpr":"70500","acml_vol":"123456"},
#             {"stck_bsop_date":"20250813","stck_oprc":"70500","stck_hgpr":"71200","stck_lwpr":"70100","stck_clpr":"71000","acml_vol":"111111"},
#         ]
#     }
#     spy_exec, mock_get = ctx.spy_get(quot_api, mocker, payload)

#     # 입력: 종목코드 / 기간 D / limit
#     code, period, limit = "005930", "M", "5"
#     mocker.patch.object(app.cli_view, "get_user_input", new_callable=AsyncMock)
#     app.cli_view.get_user_input.side_effect = [code, period, limit]

#     # 출력 뷰어는 호출만 검증
#     app.cli_view.display_ohlcv = MagicMock()
#     app.cli_view.display_ohlcv_error = MagicMock()

#     # 실행 (메뉴 '11' = OHLCV 조회 가정)
#     ok = await UserActionExecutor(app).execute("25")
#     assert ok is True

#     # --- 최하단 호출 검증 ---
#     spy_exec.assert_called()
#     method, _ = spy_exec.call_args.args[:2]
#     assert method == "GET"

#     mock_get.assert_awaited_once()
#     g_args, g_kwargs = mock_get.call_args
#     req_url     = g_args[0] if g_args else g_kwargs.get("url")
#     req_headers = g_kwargs.get("headers") or {}
#     req_params  = g_kwargs.get("params") or {}

#     expected_url = ctx.expected_url_for_quotations(app, EndpointKey.DAILY_ITEMCHARTPRICE)
#     trid_provider = ctx.ki.trid_quotations
#     expected_trid = trid_provider.daily_itemchartprice()  # 일봉
#     assert req_url == expected_url
#     assert req_headers.get("tr_id") == expected_trid
#     assert req_headers.get("custtype") == ctx.ki.env.active_config["custtype"]
#     assert req_params.get("fid_input_iscd") == code  # 종목 코드 전달

#     # 뷰 호출(성공 경로)
#     app.cli_view.display_ohlcv.assert_called_once()
#     app.cli_view.display_ohlcv_error.assert_not_called()


# @pytest.mark.asyncio
# async def test_get_ohlcv_year_full_integration_paper(real_app_instance, mocker):
#     """
#     (통합 테스트-모의) OHLCV 일봉:
#     TradingApp → StockQueryService → TradingService → BrokerAPIWrapper →
#     KoreaInvestApiQuotations.inquire_daily_itemchartprice → call_api → _execute_request
#     """
#     app = real_app_instance
#     ctx.ki.bind(app)
#     quot_api = ctx.ki.quot

#     # HTTP 레이어 모킹: 일봉 응답 payload (표준 'output' 리스트)
#     # ✅ 표준 래퍼 + output2 로 교체
#     payload = {
#         "rt_cd": "0",
#         "msg_cd": "MCA00000",
#         "msg1": "정상처리 되었습니다.",
#         "output2": [
#             {"stck_bsop_date":"20250812","stck_oprc":"70000","stck_hgpr":"71000","stck_lwpr":"69500","stck_clpr":"70500","acml_vol":"123456"},
#             {"stck_bsop_date":"20250813","stck_oprc":"70500","stck_hgpr":"71200","stck_lwpr":"70100","stck_clpr":"71000","acml_vol":"111111"},
#         ]
#     }
#     spy_exec, mock_get = ctx.spy_get(quot_api, mocker, payload)

#     # 입력: 종목코드 / 기간 D / limit
#     code, period, limit = "005930", "Y", "5"
#     mocker.patch.object(app.cli_view, "get_user_input", new_callable=AsyncMock)
#     app.cli_view.get_user_input.side_effect = [code, period, limit]

#     # 출력 뷰어는 호출만 검증
#     app.cli_view.display_ohlcv = MagicMock()
#     app.cli_view.display_ohlcv_error = MagicMock()

#     # 실행 (메뉴 '11' = OHLCV 조회 가정)
#     ok = await UserActionExecutor(app).execute("25")
#     assert ok is True

#     # --- 최하단 호출 검증 ---
#     spy_exec.assert_called()
#     method, _ = spy_exec.call_args.args[:2]
#     assert method == "GET"

#     mock_get.assert_awaited_once()
#     g_args, g_kwargs = mock_get.call_args
#     req_url     = g_args[0] if g_args else g_kwargs.get("url")
#     req_headers = g_kwargs.get("headers") or {}
#     req_params  = g_kwargs.get("params") or {}

#     expected_url = ctx.expected_url_for_quotations(app, EndpointKey.DAILY_ITEMCHARTPRICE)
#     trid_provider = ctx.ki.trid_quotations
#     expected_trid = trid_provider.daily_itemchartprice()  # 일봉
#     assert req_url == expected_url
#     assert req_headers.get("tr_id") == expected_trid
#     assert req_headers.get("custtype") == ctx.ki.env.active_config["custtype"]
#     assert req_params.get("fid_input_iscd") == code  # 종목 코드 전달

#     # 뷰 호출(성공 경로)
#     app.cli_view.display_ohlcv.assert_called_once()
#     app.cli_view.display_ohlcv_error.assert_not_called()


# @pytest.mark.asyncio
# async def test_handle_fetch_recnt_daily_ohlcv_full_integration_real(real_app_instance, mocker):
#     """
#     (통합 테스트-모의) 최근 일봉 조회:
#     TradingApp → UserActionExecutor(26) → StockQueryService → TradingService →
#     BrokerAPIWrapper → KoreaInvestApiQuotations.inquire_daily_itemchartprice → _execute_request
#     """
#     app = real_app_instance
#     ctx.ki.bind(app)
#     quot_api = ctx.ki.quot

#     # --- HTTP 레이어 모킹: 일봉 응답 payload (output2 사용) ---
#     payload = {
#         "rt_cd": "0",
#         "msg_cd": "MCA00000",
#         "msg1": "정상처리 되었습니다.",
#         "output2": [
#             {"stck_bsop_date":"20250812","stck_oprc":"70000","stck_hgpr":"71000","stck_lwpr":"69500","stck_clpr":"70500","acml_vol":"123456"},
#             {"stck_bsop_date":"20250813","stck_oprc":"70500","stck_hgpr":"71200","stck_lwpr":"70100","stck_clpr":"71000","acml_vol":"111111"},
#         ]
#     }
#     spy_exec, mock_get = ctx.spy_get(quot_api, mocker, payload)

#     # --- 입력 프롬프트 모킹: 종목코드, limit ---
#     code, limit = "005930", "5"
#     mocker.patch.object(app.cli_view, "get_user_input", new_callable=AsyncMock)
#     app.cli_view.get_user_input.side_effect = [code, limit]

#     # --- 출력 위임 뷰 모킹 ---
#     app.cli_view.display_ohlcv = MagicMock()
#     app.cli_view.display_ohlcv_error = MagicMock()

#     # --- 실행 (메뉴 '26' = 최근 일봉 조회) ---
#     ok = await UserActionExecutor(app).execute("26")
#     assert ok is True

#     # --- 최하단 HTTP 호출 검증 ---
#     spy_exec.assert_called()
#     method, _ = spy_exec.call_args.args[:2]
#     assert method == "GET"

#     # ✅ 내부적으로 여러 번 GET이 호출될 수 있으므로, 1회 이상 호출되었는지만 확인
#     assert mock_get.await_count >= 1

#     # ✅ 우리가 원하는 호출(일봉 엔드포인트)만 골라서 검증
#     #    EndpointKey 사용이 가능하면 정확 URL로, 아니면 부분 문자열로 필터링
#     try:
#         from brokers.korea_investment.korea_invest_url_keys import EndpointKey as EKey
#         expected_url = ctx.expected_url_for_quotations(app, EKey.DAILY_ITEMCHARTPRICE)

#         def is_target(call):
#             args, kwargs = call
#             url = args[0] if args else kwargs.get("url")
#             return str(url) == expected_url
#     except Exception:
#         def is_target(call):
#             args, kwargs = call
#             url = args[0] if args else kwargs.get("url")
#             return "inquire-daily-itemchartprice" in str(url)

#     target_call = next((c for c in mock_get.call_args_list if is_target(c)), None)
#     assert target_call is not None, "GET to 'inquire-daily-itemchartprice' was not captured."

#     g_args, g_kwargs = target_call
#     req_url = g_args[0] if g_args else g_kwargs.get("url")
#     req_headers = g_kwargs.get("headers") or {}
#     req_params = g_kwargs.get("params") or {}

#     # TRID/헤더/파라미터 검증
#     trid_provider = ctx.ki.trid_quotations
#     expected_trid = trid_provider.daily_itemchartprice()  # 일봉 TRID
#     assert req_headers.get("tr_id") == expected_trid
#     assert req_headers.get("custtype") == ctx.ki.env.active_config["custtype"]
#     assert req_params.get("fid_input_iscd") == code


# @pytest.mark.asyncio
# async def test_intraday_minutes_today_full_integration_real(real_app_instance, mocker):
#     """
#     (통합-모의) 메뉴 27: 당일 분봉 조회
#     TradingApp → UserActionExecutor(27) → StockQueryService → TradingService →
#     BrokerAPIWrapper → KoreaInvestApiQuotations.inquire_time_itemchartprice → _execute_request
#     """
#     app = real_app_instance
#     ctx.ki.bind(app)
#     quot_api = ctx.ki.quot

#     # --- 응답 페이로드 (output2 기준) ---
#     payload = {
#         "rt_cd": "0",
#         "msg1": "정상",
#         "output2": [
#             {"stck_bsop_date":"20250820","stck_cntg_hour":"0901","stck_prpr":"70500","cntg_vol":"1200"},
#             {"stck_bsop_date":"20250820","stck_cntg_hour":"0902","stck_prpr":"70550","cntg_vol":"900"},
#         ]
#     }
#     spy_exec, mock_get = ctx.spy_get(quot_api, mocker, payload)

#     # --- 입력 프롬프트: 종목, 기준시간(정규화 테스트용: YYYYMMDDHH 처럼 길게 줘도 됨) ---
#     code, hour = "005930", "2025082009"  # → HHMMSS로 정규화되며 '082009'가 기대됨
#     mocker.patch.object(app.cli_view, "get_user_input", new_callable=AsyncMock)
#     app.cli_view.get_user_input.side_effect = [code, hour]

#     # --- 출력 뷰 모킹 ---
#     app.cli_view.display_intraday_minutes = MagicMock()
#     app.cli_view.display_intraday_error = MagicMock()

#     # --- 실행 ---
#     ok = await UserActionExecutor(app).execute("27")
#     assert ok is True

#     # --- 최하단 HTTP 호출 검증 (여러 호출 중 타겟만 필터) ---
#     spy_exec.assert_called()
#     assert mock_get.await_count >= 1

#     try:
#         from brokers.korea_investment.korea_invest_url_keys import EndpointKey as EKey
#         expected_url = ctx.expected_url_for_quotations(app, EKey.TIME_ITEMCHARTPRICE)
#         def is_target(call):
#             args, kwargs = call
#             url = args[0] if args else kwargs.get("url")
#             return str(url) == expected_url
#     except Exception:
#         def is_target(call):
#             args, kwargs = call
#             url = args[0] if args else kwargs.get("url")
#             return "inquire-time-itemchartprice" in str(url)

#     target_call = next((c for c in mock_get.call_args_list if is_target(c)), None)
#     assert target_call is not None, "GET to 'inquire-time-itemchartprice' was not captured."

#     g_args, g_kwargs = target_call
#     req_url     = g_args[0] if g_args else g_kwargs.get("url")
#     req_headers = g_kwargs.get("headers") or {}
#     req_params  = g_kwargs.get("params") or {}

#     # TRID 검증 (상수 정리 전이면 존재성만)
#     trid_provider = ctx.ki.trid_quotations
#     leaf = getattr(TrIdLeaf, "TIME_ITEMCHARTPRICE", None)
#     if leaf is not None and hasattr(trid_provider, "quotations"):
#         expected_trid = trid_provider.quotations(leaf)
#         assert req_headers.get("tr_id") == expected_trid
#     else:
#         assert req_headers.get("tr_id")

#     assert req_headers.get("custtype") == ctx.ki.env.active_config["custtype"]
#     assert req_params.get("fid_input_iscd") == code
#     expected_hour = app.time_manager.to_hhmmss(hour)
#     assert req_params.get("fid_input_hour_1") == expected_hour

#     # --- 출력 위임 ---
#     app.cli_view.display_intraday_minutes.assert_called_once()
#     app.cli_view.display_intraday_error.assert_not_called()

#     # --- 프롬프트 2회 호출 ---
#     assert app.cli_view.get_user_input.await_count == 2


# @pytest.mark.asyncio
# async def test_intraday_minutes_by_date_full_integration_real(real_app_instance, mocker):
#     """
#     (통합-실전) 메뉴 28: 일별 분봉 조회
#     - inquire-time-dailychartprice 호출 검증
#     - TRID / 헤더 / 파라미터 검증
#     """
#     app = real_app_instance
#     ctx.ki.bind(app)
#     quot_api = ctx.ki.quot

#     payload = {
#         "rt_cd": "0",
#         "msg1": "정상",
#         "output2": [
#             {"stck_bsop_date":"20241023","stck_cntg_hour":"0930","stck_prpr":"70100","cntg_vol":"1000"},
#             {"stck_bsop_date":"20241023","stck_cntg_hour":"0931","stck_prpr":"70150","cntg_vol":"800"},
#         ]
#     }
#     spy_exec, mock_get = ctx.spy_get(quot_api, mocker, payload)

#     code, ymd, hour = "005930", "20241023", "00130000"  # 시간은 길이 10 예시
#     mocker.patch.object(app.cli_view, "get_user_input", new_callable=AsyncMock)
#     app.cli_view.get_user_input.side_effect = [code, ymd, hour]

#     app.cli_view.display_intraday_minutes = MagicMock()
#     app.cli_view.display_intraday_error = MagicMock()

#     ok = await UserActionExecutor(app).execute("28")
#     assert ok is True

#     # 여러 GET 중 타겟 호출만 필터링
#     assert mock_get.await_count >= 1
#     try:
#         from brokers.korea_investment.korea_invest_url_keys import EndpointKey as EKey
#         key = getattr(EKey, "TIME_DAILYCHARTPRICE", None)
#         if key:
#             expected_url = ctx.expected_url_for_quotations(app, key)
#             def is_target(call):
#                 args, kwargs = call
#                 url = args[0] if args else kwargs.get("url")
#                 return str(url) == expected_url
#         else:
#             def is_target(call):
#                 args, kwargs = call
#                 url = args[0] if args else kwargs.get("url")
#                 return "inquire-time-dailychartprice" in str(url)
#     except Exception:
#         def is_target(call):
#             args, kwargs = call
#             url = args[0] if args else kwargs.get("url")
#             return "inquire-time-dailychartprice" in str(url)

#     target_call = next((c for c in mock_get.call_args_list if is_target(c)), None)
#     assert target_call is not None, "GET to 'inquire-time-dailychartprice' was not captured."

#     g_args, g_kwargs = target_call
#     req_headers = g_kwargs.get("headers") or {}
#     req_params  = g_kwargs.get("params") or {}

#     # TRID 검증 (정의되어 있으면 정확 비교, 없으면 존재성만)
#     trid_provider = ctx.ki.trid_quotations
#     leaf = getattr(TrIdLeaf, "TIME_DAILYCHARTPRICE", None)
#     if leaf is not None and hasattr(trid_provider, "quotations"):
#         expected_trid = trid_provider.quotations(leaf)
#         assert req_headers.get("tr_id") == expected_trid
#     else:
#         assert req_headers.get("tr_id")

#     assert req_headers.get("custtype") == ctx.ki.env.active_config["custtype"]
#     assert req_params.get("fid_input_iscd") == code
#     assert req_params.get("fid_input_date_1") == ymd
#     # 허봉 포함 여부: 공백 필수
#     assert req_params.get("fid_fake_tick_incu_yn", "") == ""
#     expected_hour = app.time_manager.to_hhmmss(hour)
#     assert req_params.get("fid_input_hour_1") == expected_hour

#     app.cli_view.display_intraday_minutes.assert_called_once()
#     app.cli_view.display_intraday_error.assert_not_called()
#     assert app.cli_view.get_user_input.await_count == 3


# # =========================
# # (실전) 메뉴 29: 하루 분봉 조회 — ByDate 경로(get_intraday_minutes_by_date 기반)
# # - 집계 함수가 date_ymd를 받아 호출되었는지 확인
# # - EXTENDED 세션(08:00~20:00) 선택 확인
# # =========================
# @pytest.mark.asyncio
# async def test_day_intraday_minutes_real_by_date_calls_service(real_app_instance, mocker):
#     """
#     (통합-실전) 메뉴 29: 하루 분봉 조회
#     - 내부적으로 by_date(100개/배치) 경로를 사용 → inquire-time-dailychartprice 호출 검증
#     - TRID / 헤더 / 파라미터 검증
#     - 모킹은 quot_api만
#     """
#     app = real_app_instance
#     ctx.ki.bind(app)
#     quot_api = ctx.ki.quot

#     # payload: start를 '0900' 포함시켜 첫 배치 이후 바로 종료되도록(루프 최소화)
#     payload = {
#         "rt_cd": "0",
#         "msg1": "정상",
#         "output2": [
#             {"stck_bsop_date": "20241023", "stck_cntg_hour": "0900", "stck_prpr": "70100", "cntg_vol": "1000"},
#             {"stck_bsop_date": "20241023", "stck_cntg_hour": "0901", "stck_prpr": "70150", "cntg_vol": "800"},
#         ],
#     }
#     spy_exec, mock_get = ctx.spy_get(quot_api, mocker, payload)

#     # 입력: 종목 / 범위(1=REGULAR 09:00~15:30) / 날짜(YYYYMMDD)
#     # REGULAR 세션에서는 첫 요청의 fid_input_hour_1이 '153000'이어야 함(end_hhmmss)
#     code, range_choice, ymd = "005930", "1", "20241023"
#     mocker.patch.object(app.cli_view, "get_user_input", new_callable=AsyncMock)
#     app.cli_view.get_user_input.side_effect = [code, range_choice, ymd]

#     # 출력 뷰
#     # (전용 헬퍼가 있으면 우선 사용, 없으면 일반 출력)
#     if hasattr(app.cli_view, "display_intraday_minutes_full_day"):
#         app.cli_view.display_intraday_minutes_full_day = MagicMock()
#     app.cli_view.display_intraday_minutes = MagicMock()
#     app.cli_view.display_intraday_error = MagicMock()

#     # 실행
#     ok = await UserActionExecutor(app).execute("29")
#     assert ok is True

#     # 타겟 HTTP 호출 필터링 (by_date → inquire-time-dailychartprice)
#     assert mock_get.await_count >= 1
#     try:
#         from brokers.korea_investment.korea_invest_url_keys import EndpointKey as EKey
#         key = getattr(EKey, "TIME_DAILYCHARTPRICE", None)
#         if key:
#             expected_url = ctx.expected_url_for_quotations(app, key)
#             def is_target(call):
#                 args, kwargs = call
#                 url = args[0] if args else kwargs.get("url")
#                 return str(url) == expected_url
#         else:
#             def is_target(call):
#                 args, kwargs = call
#                 url = args[0] if args else kwargs.get("url")
#                 return "inquire-time-dailychartprice" in str(url)
#     except Exception:
#         def is_target(call):
#             args, kwargs = call
#             url = args[0] if args else kwargs.get("url")
#             return "inquire-time-dailychartprice" in str(url)

#     target_call = next((c for c in mock_get.call_args_list if is_target(c)), None)
#     assert target_call is not None, "GET to 'inquire-time-dailychartprice' was not captured."

#     # 파라미터/헤더 검증
#     g_args, g_kwargs = target_call
#     req_headers = g_kwargs.get("headers") or {}
#     req_params  = g_kwargs.get("params") or {}

#     # TRID
#     trid_provider = ctx.ki.trid_quotations
#     leaf = getattr(TrIdLeaf, "TIME_DAILYCHARTPRICE", None)
#     if leaf is not None and hasattr(trid_provider, "quotations"):
#         expected_trid = trid_provider.quotations(leaf)
#         assert req_headers.get("tr_id") == expected_trid
#     else:
#         assert req_headers.get("tr_id")

#     assert req_headers.get("custtype") == ctx.ki.env.active_config["custtype"]
#     assert req_params.get("fid_input_iscd") == code
#     assert req_params.get("fid_input_date_1") == ymd
#     # 허봉 포함 여부: 공백
#     assert req_params.get("fid_fake_tick_incu_yn", "") == ""

#     # 메뉴 29 REGULAR: 첫 커서 = end_hhmmss = '153000'
#     expected_hour = "153000"
#     assert req_params.get("fid_input_hour_1") == expected_hour

#     # 출력 호출 확인
#     if hasattr(app.cli_view, "display_intraday_minutes_full_day"):
#         app.cli_view.display_intraday_minutes_full_day.assert_called_once()
#         app.cli_view.display_intraday_minutes.assert_not_called()
#     else:
#         app.cli_view.display_intraday_minutes.assert_called_once()
#     app.cli_view.display_intraday_error.assert_not_called()

#     # 입력 횟수(코드/범위/날짜)
#     assert app.cli_view.get_user_input.await_count == 3


# @pytest.mark.asyncio
# async def test_get_top_market_cap_stocks_full_integration_real(real_app_instance, mocker):
#     """
#     (통합 테스트-실전) 시가총액 상위:
#     TradingApp → StockQueryService → BrokerAPIWrapper → (quotations api) → call_api → _execute_request
#     """
#     app = real_app_instance
#     mocker.patch.object(app.cli_view, "get_user_input", new_callable=AsyncMock, return_value=2)

#     payload = {
#         "rt_cd": "0",
#         "msg1": "정상",
#         "output": [
#             {
#                 "mksc_shrn_iscd": "005930",
#                 "hts_kor_isnm": "삼성전자",
#                 "stck_avls": "467000000000000",  # 시가총액 (문자열 숫자)
#                 "data_rank": "1",
#                 "prdy_vrss_sign": "1",
#                 "acc_trdvol": "12345678"
#             },
#             {
#                 "mksc_shrn_iscd": "000660",
#                 "hts_kor_isnm": "SK하이닉스",
#                 "stck_avls": "345000000000000",
#                 "data_rank": "2",
#                 "prdy_vrss_sign": "2",
#                 "acc_trdvol": "9876543"
#             },
#         ],
#     }

#     # 실행 경로 바인딩
#     ctx.ki.bind(app)
#     quot_api = ctx.ki.quot

#     # _execute_request 스파이 + 세션 GET만 모킹
#     spy_exec, mock_get = ctx.spy_get(quot_api, mocker, payload)

#     # 출력 뷰어는 호출만 검증
#     app.cli_view.display_top_market_cap_stocks_success = MagicMock()
#     app.cli_view.display_top_market_cap_stocks_failure = MagicMock()

#     ok = await UserActionExecutor(app).execute("50")  # 시총 상위
#     assert ok is True

#     # 메서드/URL/헤더 검증
#     spy_exec.assert_called()
#     method, _ = spy_exec.call_args.args[:2]
#     assert method == "GET"

#     mock_get.assert_awaited_once()
#     g_args, g_kwargs = mock_get.call_args
#     req_url     = g_args[0] if g_args else g_kwargs.get("url")
#     req_headers = g_kwargs.get("headers") or {}
#     req_params  = g_kwargs.get("params") or {}

#     expected_url  = ctx.expected_url_for_quotations(app, EndpointKey.MARKET_CAP)
#     expected_trid = ctx.ki.trid_quotations.quotations(TrIdLeaf.MARKET_CAP)

#     assert req_url == expected_url
#     assert req_headers.get("tr_id") == expected_trid
#     assert req_headers.get("custtype") == ctx.ki.env.active_config["custtype"]

#     # (선택) 시장 코드 정도만 엄격 체크
#     assert req_params.get("fid_cond_mrkt_div_code") == "J"

#     # 뷰 호출(성공 경로)
#     app.cli_view.display_top_market_cap_stocks_success.assert_called_once()
#     app.cli_view.display_top_market_cap_stocks_failure.assert_not_called()


# @pytest.mark.asyncio
# async def test_get_top_10_market_cap_stocks_with_prices_full_integration_real(real_app_instance, mocker):
#     """
#     (통합 테스트-실전) 시가총액 상위 10개 + 현재가:
#     첫 콜: MARKET_CAP, 이후 콜들: INQUIRE_PRICE
#     """
#     app = real_app_instance
#     app.time_manager.is_market_open = MagicMock(return_value=True)

#     # 표준 스키마 payload
#     top_payload = {
#         "rt_cd": "0",
#         "msg1": "정상",
#         "output": [
#             {
#                 "mksc_shrn_iscd": "005930",
#                 "hts_kor_isnm": "삼성전자",
#                 "stck_avls": "467000000000000",  # 시가총액 (문자열 숫자)
#                 "data_rank": "1",
#                 "prdy_vrss_sign": "1",
#                 "acc_trdvol": "12345678"
#             },
#             {
#                 "mksc_shrn_iscd": "000660",
#                 "hts_kor_isnm": "SK하이닉스",
#                 "stck_avls": "345000000000000",
#                 "data_rank": "2",
#                 "prdy_vrss_sign": "2",
#                 "acc_trdvol": "9876543"
#             },
#         ],
#     }

#     # 바인딩
#     ctx.ki.bind(app)
#     quot_api = ctx.ki.quot

#     # URL 판별형 side_effect (세션 GET 레벨에서 분기)
#     market_cap_url  = ctx.expected_url_for_quotations(app, EndpointKey.MARKET_CAP)

#     def _make_resp(obj):
#         return ctx.make_http_response(obj, 200)  # ctx helper에 맞춰 사용

#     async def _get_side_effect(url, *args, **kwargs):
#         u = str(url)
#         if u == market_cap_url:
#             return _make_resp(top_payload)
#         return None

#     # _execute_request 스파이 + 세션 GET만 직접 패치
#     spy_exec = mocker.spy(quot_api, "_execute_request")
#     mock_get = mocker.patch.object(quot_api._async_session, "get", new_callable=AsyncMock, side_effect=_get_side_effect)

#     app.cli_view.display_top10_market_cap_prices_success = MagicMock()
#     app.cli_view.display_top10_market_cap_prices_failure = MagicMock()

#     ok = await UserActionExecutor(app).execute("51")  # 상위 10 + 현재가
#     assert ok is True

#     # 최소 3회 호출(1:시총상위 + 2:현재가들)
#     assert mock_get.await_count == 1

#     # 각 URL별로 적어도 1번 이상 호출되었는지 분류 확인
#     urls = [(ca[0][0] if ca[0] else ca[1].get("url")) for ca in mock_get.call_args_list]
#     assert market_cap_url in map(str, urls)

#     # 첫 콜(시총)의 헤더가 MARKET_CAP TRID인지, 가격 콜의 헤더가 INQUIRE_PRICE TRID인지 샘플링 체크
#     m_trid = ctx.ki.trid_quotations.quotations(TrIdLeaf.MARKET_CAP)

#     # 시총 호출 하나 집어 검사
#     for ca in mock_get.call_args_list:
#         url = (ca[0][0] if ca[0] else ca[1].get("url"))
#         headers = ca[1].get("headers") or {}
#         if str(url) == market_cap_url:
#             assert headers.get("tr_id") == m_trid
#             break

#     # 뷰 호출(성공 경로)
#     app.cli_view.display_top10_market_cap_prices_success.assert_called_once()
#     app.cli_view.display_top10_market_cap_prices_failure.assert_not_called()


# @pytest.mark.asyncio
# async def test_handle_current_upper_limit_stocks_full_integration_real(real_app_instance, mocker):
#     """
#     (통합 테스트-실전) 당일 상한가 종목 (전체)
#     """
#     app = real_app_instance

#     top30_sample = [
#         ResFluctuation.from_dict({
#             "stck_shrn_iscd": "000001", "hts_kor_isnm": "A",
#             "stck_prpr": "5590", "stck_hgpr": "5590", "prdy_ctrt": "30.00", "prdy_vrss": "1290",
#         }),
#         ResFluctuation.from_dict({
#             "stck_shrn_iscd": "000002", "hts_kor_isnm": "B",
#             "stck_prpr": "20000", "stck_hgpr": "20000", "prdy_ctrt": "30.00", "prdy_vrss": "3000",
#         }),
#         ResFluctuation.from_dict({
#             "stck_shrn_iscd": "000003", "hts_kor_isnm": "C",
#             "stck_prpr": "15000", "stck_hgpr": "16000", "prdy_ctrt": "8.50",  "prdy_vrss": "1170",
#         }),
#     ]

#     # 바인딩 후 quotations에 바로 패치
#     ctx.ki.bind(app)
#     quot_api = ctx.ki.quot

#     mocker.patch.object(
#         quot_api,
#         "get_top_rise_fall_stocks",
#         AsyncMock(return_value=ResCommonResponse(
#             rt_cd=ErrorCode.SUCCESS.value, msg1="정상", data=top30_sample
#         )),
#     )

#     app.cli_view.display_current_upper_limit_stocks = MagicMock()
#     app.cli_view.display_no_current_upper_limit_stocks = MagicMock()

#     ok = await UserActionExecutor(app).execute("54")
#     assert ok is True
#     app.cli_view.display_current_upper_limit_stocks.assert_called_once()
#     app.cli_view.display_no_current_upper_limit_stocks.assert_not_called()

#     lst = app.cli_view.display_current_upper_limit_stocks.call_args[0][0]
#     assert isinstance(lst, list) and len(lst) >= 2

#     def _code(x):
#         return getattr(x, "code", None) or (x.get("code") if isinstance(x, dict) else None)

#     def _name(x):
#         return getattr(x, "name", None) or (x.get("name") if isinstance(x, dict) else None)

#     codes = {_code(x) for x in lst}
#     names = {_name(x) for x in lst}
#     assert "000001" in codes and "000002" in codes
#     assert "A" in names and "B" in names


# @pytest.mark.asyncio
# async def test_handle_realtime_stream_full_integration_real(real_app_instance, mocker):
#     """
#     메뉴 '18' 실시간 구독 흐름(라이트):
#     - 2회 입력(종목코드, 구독타입)
#     - websocketAPI.connect() 호출
#     - 'price'면 subscribe_realtime_price(), 'quote'면 subscribe_realtime_quote() 호출 검증
#     """
#     app = real_app_instance

#     # 1) 사용자 입력: 종목코드, 구독타입("price" 또는 "quote")
#     mocker.patch.object(app.cli_view, "get_user_input", new_callable=AsyncMock)
#     app.cli_view.get_user_input.side_effect = ["005930", "price"]

#     # 2) 내부 websocket API 인스턴스 접근 경로 수정
#     inner = app.stock_query_service.trading_service._broker_api_wrapper._client
#     wsapi = inner._websocketAPI

#     # 3) 네트워크 차단 및 오타 수정
#     mocker.patch.object(wsapi, "_get_approval_key", new_callable=AsyncMock, return_value="APPROVAL-KEY")
#     mock_connect = mocker.patch.object(wsapi, "connect", new_callable=AsyncMock, return_value=True)
#     sub_price = mocker.patch.object(wsapi, "subscribe_realtime_price", new_callable=AsyncMock, return_value=True)
#     sub_quote = mocker.patch.object(wsapi, "subscribe_realtime_quote", new_callable=AsyncMock, return_value=True)

#     # 실행
#     ok = await UserActionExecutor(app).execute("70")
#     assert ok is True

#     # 검증
#     wsapi.connect.assert_awaited_once()
#     sub_price.assert_awaited_once_with("005930")
#     sub_quote.assert_not_called()
#     assert app.cli_view.get_user_input.await_count == 2


# @pytest.mark.asyncio
# async def test_handle_realtime_stream_deep_checks_real(real_app_instance, mocker):
#     app = real_app_instance

#     # 입력: 종목코드/타입
#     mocker.patch.object(
#         app.cli_view, "get_user_input",
#         new_callable=AsyncMock, side_effect=["005930", "quote"]
#     )

#     inner = app.stock_query_service.trading_service._broker_api_wrapper._client
#     wsapi = inner._websocketAPI

#     # approval_key/연결 우회
#     mocker.patch.object(wsapi, "_get_approval_key", new_callable=AsyncMock, return_value="APPROVAL-KEY")
#     mocker.patch.object(wsapi, "connect", new_callable=AsyncMock, return_value=True)

#     # ✅ 스파이는 딱 한 번, Act 전에만!
#     send_spy = mocker.spy(wsapi, "send_realtime_request")

#     # 실제 구현을 타도록 wraps 사용 (quote 구독 경로)
#     mocker.patch.object(wsapi, "subscribe_realtime_quote", wraps=wsapi.subscribe_realtime_quote)

#     # Act
#     ok = await UserActionExecutor(app).execute("70")
#     assert ok is True

#     # Assert: 구독 요청이 올바른 TR_ID / 코드 / tr_type=1 로 나갔는지
#     tr_id = app.env.active_config["tr_ids"]["websocket"]["realtime_quote"]  # 예: "H0STASP0"
#     calls = send_spy.await_args_list

#     # 구독(1) 콜 존재
#     assert any(
#         c.args[:2] == (tr_id, "005930") and c.kwargs.get("tr_type") == "1"
#         for c in calls
#     ), f"구독 요청이 전송되지 않았습니다. calls={calls}"

#     # (선택) 해지(2) 콜 존재도 보고 싶다면: (단순 구독 시에는 호출되지 않을 수 있음)
#     # assert any(
#     #     c.args[:2] == (tr_id, "005930") and c.kwargs.get("tr_type") == "2"
#     #     for c in calls
#     # ), f"해지 요청이 전송되지 않았습니다. calls={calls}"


# @pytest.mark.asyncio
# async def test_get_top_volume_full_integration_real(real_app_instance, mocker):
#     """
#     (통합 테스트-실전) 상위 거래량 랭킹:
#     TradingApp → StockQueryService → BrokerAPIWrapper → (quotations api) → call_api → _execute_request
#     """
#     app = real_app_instance

#     # View 결과 검증을 원하면 모킹(선택)
#     app.cli_view.display_top_stocks_ranking = MagicMock()
#     app.cli_view.display_top_stocks_ranking_error = MagicMock()

#     # API 응답(payload) – 표준 스키마 'output' 리스트
#     payload = {
#         "rt_cd": "0",
#         "msg1": "정상",
#         "output": [
#             {"stck_shrn_iscd": "005930", "hts_kor_isnm": "삼성전자", "stck_prpr": "70000", "prdy_ctrt": "3.2",
#              "prdy_vrss": "2170"},
#             {"stck_shrn_iscd": "000660", "hts_kor_isnm": "SK하이닉스", "stck_prpr": "150000", "prdy_ctrt": "2.7",
#              "prdy_vrss": "3950"},
#         ]
#     }

#     # 바인딩 + 시세 API
#     ctx.ki.bind(app)
#     quot_api = ctx.ki.quot

#     # _execute_request 스파이 + 세션 get 모킹
#     spy_exec, mock_get = ctx.spy_get(quot_api, mocker, payload)

#     ok = await UserActionExecutor(app).execute("55")
#     assert ok is True

#     # _execute_request 호출/메서드 확인
#     spy_exec.assert_called()
#     method, _ = spy_exec.call_args.args[:2]
#     assert method == "GET"

#     # 실제 세션 호출: 최종 URL/헤더/파라미터
#     mock_get.assert_awaited_once()
#     g_args, g_kwargs = mock_get.call_args
#     req_url     = g_args[0] if g_args else g_kwargs.get("url")
#     req_headers = g_kwargs.get("headers") or {}
#     req_params  = g_kwargs.get("params") or {}

#     # ✅ 엄격: 고정 상수만 사용
#     expected_url  = ctx.expected_url_for_quotations(app, EndpointKey.RANKING_VOLUME)
#     expected_trid = ctx.ki.trid_quotations.quotations(TrIdLeaf.RANKING_VOLUME)

#     assert req_url == expected_url
#     assert req_headers.get("tr_id") == expected_trid
#     assert req_headers.get("custtype") == ctx.ki.env.active_config["custtype"]

#     # volume 랭킹은 종목코드 입력이 필요 없으므로, 특정 코드 파라미터가 없어야 정상(프로젝트 규약에 맞춰 조정)
#     assert "fid_input_iscd" not in req_params

#     # (선택) View 호출 검증
#     app.cli_view.display_top_stocks_ranking.assert_called_once()
#     app.cli_view.display_top_stocks_ranking_error.assert_not_called()

#     # get_top_volume_stocks()가 output 리스트를 파싱하여 list로 반환
#     title_arg, data_arg = app.cli_view.display_top_stocks_ranking.call_args[0][:2]

#     assert title_arg == "volume"
#     assert isinstance(data_arg, list), f"뷰에 list 형태의 종목 데이터가 전달되어야 합니다. got={type(data_arg)}"
#     assert len(data_arg) == 2
#     assert {item["stck_shrn_iscd"] for item in data_arg} == {"005930", "000660"}


# @pytest.mark.asyncio
# async def test_get_top_rise_full_integration_real(real_app_instance, mocker):
#     """
#     (통합 테스트-실전) 상위 랭킹 조회(rise):
#     TradingApp → StockQueryService → BrokerAPIWrapper → (quotations api) → call_api → _execute_request
#     """
#     app = real_app_instance

#     # 표준 스키마 payload (뷰로는 res.data 그대로 전달됨)
#     payload = {
#         "rt_cd": "0",
#         "msg1": "정상",
#         "output": [
#             {"stck_shrn_iscd": "005930", "hts_kor_isnm": "삼성전자",
#              "stck_prpr": "70000", "prdy_ctrt": "3.2", "prdy_vrss": "2170", "data_rank": "1"},
#             {"stck_shrn_iscd": "000660", "hts_kor_isnm": "SK하이닉스",
#              "stck_prpr": "150000", "prdy_ctrt": "2.7", "prdy_vrss": "3950", "data_rank": "2"},
#         ]
#     }

#     # 뷰 모킹(실행코드는 display_top_stocks_ranking(title, res.data) 호출)
#     app.cli_view.display_top_stocks_ranking = MagicMock()
#     app.cli_view.display_top_stocks_ranking_error = MagicMock()

#     # 바인딩 + 시세 API
#     ctx.ki.bind(app)
#     quot_api = ctx.ki.quot

#     # _execute_request 실행 + 세션 GET만 모킹
#     spy_exec, mock_get = ctx.spy_get(quot_api, mocker, payload)

#     ok = await UserActionExecutor(app).execute("56")
#     assert ok is True

#     # _execute_request 호출/메서드 확인
#     spy_exec.assert_called()
#     method, _ = spy_exec.call_args.args[:2]
#     assert method == "GET"

#     # 실제 세션 호출: 최종 URL/헤더/파라미터 검증(엄격 상수)
#     mock_get.assert_awaited_once()
#     g_args, g_kwargs = mock_get.call_args
#     req_url     = g_args[0] if g_args else g_kwargs.get("url")
#     req_headers = g_kwargs.get("headers") or {}
#     req_params  = g_kwargs.get("params") or {}

#     expected_url  = ctx.expected_url_for_quotations(app, EndpointKey.RANKING_FLUCTUATION)
#     expected_trid = ctx.ki.trid_quotations.quotations(TrIdLeaf.RANKING_FLUCTUATION)

#     assert req_url == expected_url
#     assert req_headers.get("tr_id") == expected_trid
#     assert req_headers.get("custtype") == ctx.ki.env.active_config["custtype"]

#     # ✅ 파라미터 검증: '없어야 한다' 대신 '핵심 값이 맞다'로 엄격 검증
#     #   - 시장구분: 국내 주식 'J' (프로젝트 환경에 맞춰 조정 가능)
#     #   - 스크린 코드: 상승용 (실제 런타임에서 20170로 내려오는 것 확인됨)
#     assert req_params.get("fid_cond_mrkt_div_code") == "J"
#     assert req_params.get("fid_cond_scr_div_code") == "20170"

#     # === 뷰 검증: 실행코드는 상승 랭킹에서 리스트(ResFluctuation 리스트)를 넘김 ===
#     app.cli_view.display_top_stocks_ranking.assert_called_once()
#     app.cli_view.display_top_stocks_ranking_error.assert_not_called()

#     title_arg, items_arg = app.cli_view.display_top_stocks_ranking.call_args[0][:2]
#     assert title_arg == "rise"

#     # 리스트 형태 + 각 원소는 ResFluctuation(또는 dict)인지 확인
#     assert isinstance(items_arg, list) and len(items_arg) == 2

#     # 객체/딕셔너리 모두 커버해서 종목코드 집합 확인
#     codes = {
#         (x.stck_shrn_iscd if hasattr(x, "stck_shrn_iscd") else x.get("stck_shrn_iscd"))
#         for x in items_arg
#     }
#     assert codes == {"005930", "000660"}


# @pytest.mark.asyncio
# async def test_get_top_fall_full_integration_real(real_app_instance, mocker):
#     """
#     (통합 테스트-실전) 상위 하락률 랭킹:
#     TradingApp → StockQueryService → BrokerAPIWrapper → (quotations api) → call_api → _execute_request
#     """
#     app = real_app_instance

#     # 표준 스키마 payload (HTTP 모킹용)
#     payload = {
#         "rt_cd": "0",
#         "msg1": "정상",
#         "output": [
#             {"stck_shrn_iscd": "005930", "hts_kor_isnm": "삼성전자",
#              "stck_prpr": "70000", "prdy_ctrt": "-3.2", "prdy_vrss": "-2170", "data_rank": "1"},
#             {"stck_shrn_iscd": "000660", "hts_kor_isnm": "SK하이닉스",
#              "stck_prpr": "150000", "prdy_ctrt": "-2.7", "prdy_vrss": "-3950", "data_rank": "2"},
#         ]
#     }

#     # 뷰 모킹 (실행코드는 display_top_stocks_ranking(title, items) 호출)
#     app.cli_view.display_top_stocks_ranking = MagicMock()
#     app.cli_view.display_top_stocks_ranking_error = MagicMock()

#     # 바인딩 + 시세 API
#     ctx.ki.bind(app)
#     quot_api = ctx.ki.quot

#     # _execute_request 실행 + 세션 GET만 모킹
#     spy_exec, mock_get = ctx.spy_get(quot_api, mocker, payload)

#     ok = await UserActionExecutor(app).execute("57")
#     assert ok is True

#     # _execute_request 호출/메서드 확인
#     spy_exec.assert_called()
#     method, _ = spy_exec.call_args.args[:2]
#     assert method == "GET"

#     # 실제 세션 호출: 최종 URL/헤더/파라미터 검증(엄격 상수)
#     mock_get.assert_awaited_once()
#     g_args, g_kwargs = mock_get.call_args
#     req_url     = g_args[0] if g_args else g_kwargs.get("url")
#     req_headers = g_kwargs.get("headers") or {}
#     req_params  = g_kwargs.get("params") or {}

#     expected_url  = ctx.expected_url_for_quotations(app, EndpointKey.RANKING_FLUCTUATION)
#     expected_trid = ctx.ki.trid_quotations.quotations(TrIdLeaf.RANKING_FLUCTUATION)

#     assert req_url == expected_url
#     assert req_headers.get("tr_id") == expected_trid
#     assert req_headers.get("custtype") == ctx.ki.env.active_config["custtype"]

#     # 파라미터: 시장코드는 고정, 스크린코드는 존재만 엄격 확인(값은 구현별)
#     assert req_params.get("fid_cond_mrkt_div_code") == "J"
#     assert req_params.get("fid_cond_scr_div_code")  # non-empty 존재

#     # === 뷰 검증: 리스트(ResFluctuation 리스트) 전달 ===
#     app.cli_view.display_top_stocks_ranking.assert_called_once()
#     app.cli_view.display_top_stocks_ranking_error.assert_not_called()

#     title_arg, items_arg = app.cli_view.display_top_stocks_ranking.call_args[0][:2]
#     assert title_arg == "fall"
#     assert isinstance(items_arg, list) and len(items_arg) == 2

#     codes = {
#         (x.stck_shrn_iscd if hasattr(x, "stck_shrn_iscd") else x.get("stck_shrn_iscd"))
#         for x in items_arg
#     }
#     assert codes == {"005930", "000660"}


# # @pytest.mark.asyncio
# # async def test_execute_action_momentum_strategy_success_real(real_app_instance, mocker):
# #     app = real_app_instance
# #
# #     # 시장 개장
# #     mocker.patch.object(app.time_manager, "is_market_open", return_value=True)
# #
# #     # === 표준 시총 상위 payload (HTTP 모킹용) ===
# #     payload = {
# #         "rt_cd": "0",
# #         "msg1": "정상",
# #         "output": [
# #             {
# #                 "mksc_shrn_iscd": "005930",
# #                 "hts_kor_isnm": "삼성전자",
# #                 "stck_avls": "467000000000000",
# #                 "data_rank": "1",
# #                 "stck_prpr": "70000",
# #                 "prdy_ctrt": "2.1",
# #                 "prdy_vrss_sign": "1",
# #                 "acml_vol": "100000",
# #             },
# #             {
# #                 "mksc_shrn_iscd": "000660",
# #                 "hts_kor_isnm": "SK하이닉스",
# #                 "stck_avls": "345000000000000",
# #                 "data_rank": "2",
# #                 "stck_prpr": "150000",
# #                 "prdy_ctrt": "-0.7",
# #                 "prdy_vrss_sign": "2",
# #                 "acml_vol": "80000",
# #             },
# #         ],
# #     }
# #
# #     # 뷰 모킹
# #     app.cli_view.display_strategy_running_message = MagicMock()
# #     app.cli_view.display_strategy_results = MagicMock()
# #     app.cli_view.display_follow_through_stocks = MagicMock()
# #     app.cli_view.display_not_follow_through_stocks = MagicMock()
# #
# #     # 바인딩 + 시세 API 스파이
# #     ctx.ki.bind(app)
# #     quot_api = ctx.ki.quot
# #     spy_exec, mock_get = ctx.spy_get(quot_api, mocker, payload)
# #
# #     # 전략 실행 모킹 (실로직 부담 줄임)
# #     mock_exec = mocker.patch(
# #         "strategies.strategy_executor.StrategyExecutor.execute",
# #         new_callable=AsyncMock,
# #         return_value={
# #             "follow_through": [{"code": "005930", "score": 95}],
# #             "not_follow_through": [{"code": "000660", "score": 50}],
# #         },
# #     )
# #
# #     # 실행
# #     ok = await UserActionExecutor(app).execute("100")
# #     assert ok is True
# #
# #     # _execute_request 호출/메서드 확인
# #     spy_exec.assert_called()
# #     method, _ = spy_exec.call_args.args[:2]
# #     assert method == "GET"
# #
# #     # 실제 세션 호출: 최종 URL/헤더/파라미터 검증
# #     mock_get.assert_awaited_once()
# #     g_args, g_kwargs = mock_get.call_args
# #     req_url     = g_args[0] if g_args else g_kwargs.get("url")
# #     req_headers = g_kwargs.get("headers") or {}
# #     req_params  = g_kwargs.get("params") or {}
# #
# #     expected_url  = ctx.expected_url_for_quotations(app, EndpointKey.MARKET_CAP)
# #     expected_trid = ctx.ki.trid_quotations.quotations(TrIdLeaf.MARKET_CAP)
# #     assert req_url == expected_url
# #     assert req_headers.get("tr_id") == expected_trid
# #     assert req_headers.get("custtype") == ctx.ki.env.active_config["custtype"]
# #     assert req_params.get("fid_cond_mrkt_div_code") == "J"  # 시장코드 고정
# #
# #     # 전략 호출 검증: 코드 리스트 포함 여부
# #     mock_exec.assert_awaited_once()
# #     _, kwargs = mock_exec.await_args
# #     got_codes = kwargs.get("codes") or kwargs.get("stock_codes") or []
# #     assert set(got_codes) >= {"005930", "000660"}
# #
# #     # 뷰 검증
# #     app.cli_view.display_strategy_results.assert_called_once()
# #
# #
# # @pytest.mark.asyncio
# # async def test_execute_action_momentum_strategy_market_cap_fail_real(real_app_instance, mocker):
# #     app = real_app_instance
# #     mocker.patch.object(app.time_manager, "is_market_open", return_value=True)
# #
# #     fail = ResCommonResponse(rt_cd=ErrorCode.API_ERROR.value, msg1="시가총액 조회 실패", data=None)
# #
# #     inner = app.stock_query_service.trading_service._broker_api_wrapper._client._client
# #     mocker.patch.object(inner._quotations, "get_top_market_cap_stocks_code",
# #                         new_callable=AsyncMock, return_value=fail)
# #
# #     app.cli_view.display_top_stocks_failure = MagicMock()
# #     app.logger.warning = MagicMock()
# #
# #     ok = await UserActionExecutor(app).execute("100")
# #
# #     assert ok is True
# #     app.cli_view.display_top_stocks_failure.assert_called()
# #     app.logger.warning.assert_called()
# #
# #
# # @pytest.mark.asyncio
# # async def test_execute_action_momentum_backtest_strategy_success_real(real_app_instance, mocker):
# #     app = real_app_instance
# #
# #     # 몇 개 종목? → 2
# #     mocker.patch.object(app.cli_view, "get_user_input", new_callable=AsyncMock, return_value="2")
# #
# #     # 코드 리스트(dict)
# #     mock_market_cap_response = ResCommonResponse(
# #         rt_cd=ErrorCode.SUCCESS.value, msg1="성공",
# #         data=[{"mksc_shrn_iscd": "005930"}, {"mksc_shrn_iscd": "000660"}]
# #     )
# #     inner = app.stock_query_service.trading_service._broker_api_wrapper._client._client
# #     mocker.patch.object(inner._quotations, "get_top_market_cap_stocks_code",
# #                         new_callable=AsyncMock, return_value=mock_market_cap_response)
# #
# #     # 백테스트에서 MomentumStrategy는 backtest_lookup 필요  :contentReference[oaicite:3]{index=3}
# #     app.backtest_data_provider.realistic_price_lookup = MagicMock()
# #
# #     mock_strategy_result = {
# #         "follow_through": [{"code": "005930"}],
# #         "not_follow_through": [{"code": "000660"}],
# #     }
# #     mocker.patch("strategies.strategy_executor.StrategyExecutor.execute",
# #                  new_callable=AsyncMock, return_value=mock_strategy_result)
# #
# #     app.cli_view.display_strategy_running_message = MagicMock()
# #     app.cli_view.display_strategy_results = MagicMock()
# #     app.cli_view.display_follow_through_stocks = MagicMock()
# #     app.cli_view.display_not_follow_through_stocks = MagicMock()
# #
# #     ok = await UserActionExecutor(app).execute("101")
# #
# #     assert ok is True
# #     app.cli_view.display_strategy_running_message.assert_called_once_with("모멘텀 백테스트")
# #     app.cli_view.display_strategy_results.assert_called_once_with("백테스트", mock_strategy_result)
# #     app.cli_view.display_follow_through_stocks.assert_called_once_with(mock_strategy_result["follow_through"])
# #     app.cli_view.display_not_follow_through_stocks.assert_called_once_with(mock_strategy_result["not_follow_through"])
# #
# #
# # @pytest.mark.asyncio
# # async def test_execute_action_gapup_pullback_strategy_success_real(real_app_instance, mocker):
# #     app = real_app_instance
# #
# #     # 상위 N개 입력 → 2
# #     mocker.patch.object(app.cli_view, "get_user_input", new_callable=AsyncMock, return_value="2")
# #
# #     # 코드 리스트(dict)
# #     mock_market_cap_response = ResCommonResponse(
# #         rt_cd=ErrorCode.SUCCESS.value, msg1="성공",
# #         data=[{"mksc_shrn_iscd": "005930"}, {"mksc_shrn_iscd": "000660"}]
# #     )
# #     inner = app.stock_query_service.trading_service._broker_api_wrapper._client._client
# #     mocker.patch.object(inner._quotations, "get_top_market_cap_stocks_code",
# #                         new_callable=AsyncMock, return_value=mock_market_cap_response)
# #
# #     # GapUpPullback 결과 키 (selected / rejected)  :contentReference[oaicite:4]{index=4}
# #     mock_strategy_result = {
# #         "gapup_pullback_selected": [{"code": "005930"}],
# #         "gapup_pullback_rejected": [{"code": "000660"}],
# #     }
# #     mocker.patch("strategies.strategy_executor.StrategyExecutor.execute",
# #                  new_callable=AsyncMock, return_value=mock_strategy_result)
# #
# #     app.cli_view.display_strategy_running_message = MagicMock()
# #     app.cli_view.display_strategy_results = MagicMock()
# #     app.cli_view.display_gapup_pullback_selected_stocks = MagicMock()
# #     app.cli_view.display_gapup_pullback_rejected_stocks = MagicMock()
# #
# #     ok = await UserActionExecutor(app).execute("102")
# #
# #     assert ok is True
# #     app.cli_view.display_strategy_running_message.assert_called_once_with("GapUpPullback")
# #     app.cli_view.display_strategy_results.assert_called_once_with("GapUpPullback", mock_strategy_result)
# #     app.cli_view.display_gapup_pullback_selected_stocks.assert_called_once_with(
# #         mock_strategy_result["gapup_pullback_selected"])
# #     app.cli_view.display_gapup_pullback_rejected_stocks.assert_called_once_with(
# #         mock_strategy_result["gapup_pullback_rejected"])
# #
# #
