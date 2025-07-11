# tests/test_korea_invest_api_base.py
# test_korea_invest_api_base.py (수정된 setUp)
import logging
import unittest
from unittest.mock import MagicMock, AsyncMock, patch
import requests # requests.exceptions 사용을 위해 추가
import asyncio
import httpx
import pytest

# spec으로 사용할 실제 클래스를 import 해야 합니다.
from brokers.korea_investment.korea_invest_env import KoreaInvestApiEnv
from brokers.korea_investment.korea_invest_api_base import KoreaInvestApiBase

class TestKoreaInvestApiBase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        """ 각 테스트 실행 전에 필요한 객체들을 초기화합니다. """
        self.mock_logger = MagicMock()

        # spec=KoreaInvestApiEnv 인자를 추가하여 mock_env가
        # KoreaInvestApiEnv의 인스턴스인 것처럼 동작하게 만듭니다.
        self.mock_env = MagicMock(spec=KoreaInvestApiEnv)
        self.mock_env.access_token = "initial_token"
        self.mock_env.token_expired_at = "some_time"

        self.mock_config = {
            '_env_instance': self.mock_env,
        }
        self.mock_token_manager = MagicMock()

        self.api_base = KoreaInvestApiBase(
            base_url="https://test.api.com",
            headers={"content-type": "application/json"},
            config=self.mock_config,
            token_manager=self.mock_token_manager,
            logger=self.mock_logger
        )

        # _async_session을 httpx.AsyncClient 스펙을 따르는 AsyncMock으로 설정합니다.
        # 이렇게 하면 aclose 메서드도 자동으로 AsyncMock처럼 작동합니다.
        self.api_base._async_session = AsyncMock(spec=httpx.AsyncClient)
        # 📌 아래 줄을 제거하거나 주석 처리하세요:
        # self.api_base._async_session.aclose = AsyncMock() # 이 중복 할당이 문제의 원인!


    async def test_handle_token_expiration_and_retry_success(self):
        """
        TC-1: API 호출 시 토큰 만료(EGW00123) 응답을 받으면,
              토큰을 초기화하고 재시도하여 성공적으로 데이터를 반환하는지 검증합니다.
        """
        # --- Arrange (준비) ---
        # 1. 첫 번째 호출 응답: 토큰 만료 오류
        mock_response_token_expired = MagicMock()
        mock_response_token_expired.status_code = 200
        mock_response_token_expired.json.return_value = {
            "rt_cd": "1",
            "msg_cd": "EGW00123", # 토큰 만료 코드
            "msg1": "토큰값이 유효하지 않습니다."
        }

        # 2. 두 번째 호출 응답: 성공
        mock_response_success = MagicMock()
        mock_response_success.status_code = 200
        mock_response_success.json.return_value = {
            "rt_cd": "0",
            "output": {"result": "success_data"}
        }

        # _execute_request 메소드가 위에서 정의한 mock 응답들을 순서대로 반환하도록 설정
        # patch의 대상은 실제 비동기 호출이 일어나는 '_execute_request' 메소드입니다.
        with patch.object(self.api_base, '_execute_request', new_callable=AsyncMock) as mock_execute:
            mock_execute.side_effect = [
                mock_response_token_expired,
                mock_response_success
            ]

            # --- Act (실행) ---
            # call_api 메소드를 호출합니다.
            final_result = await self.api_base.call_api(method='POST', path='/test-path')

            # --- Assert (검증) ---
            # 1. 최종 결과 검증: 두 번째 시도의 성공적인 결과값이 반환되었는지 확인합니다.
            self.assertIsNotNone(final_result)
            self.assertEqual(final_result['output']['result'], 'success_data')

            # 2. 호출 횟수 검증: API가 총 2번 호출되었는지 확인합니다. (첫 시도 실패 -> 재시도 성공)
            self.assertEqual(mock_execute.call_count, 2)

            # 3. 토큰 초기화 로직 검증: 토큰 만료 처리 로직이 실행되어
            self.mock_token_manager.invalidate_token.assert_called_once()

            # 4. 로그 호출 검증: 토큰 만료 및 재시도 관련 로그가 올바르게 기록되었는지 확인합니다.
            self.mock_logger.error.assert_called()

    # --- 65번 라인 커버: close_session 호출 ---
    async def test_close_session(self):
        """
        TC: close_session 메서드가 호출될 때 _async_session.aclose()가 호출되는지 테스트합니다.
        이는 brokers/korea_investment/korea_invest_api_base.py의 65번 라인을 커버합니다.
        """
        # Given: setUp에서 이미 self.api_base._async_session.aclose = AsyncMock() 설정됨

        # When
        await self.api_base.close_session()

        # Then
        self.api_base._async_session.aclose.assert_awaited_once()  # 65번 라인 커버
        self.mock_logger.info.assert_called_once_with("HTTP 클라이언트 세션이 종료되었습니다.")  # 66번 라인 커버

    # --- 140, 141번 라인 커버: _handle_response에서 config가 None일 때 토큰 재발급 불가 ---
    async def test_handle_response_token_error_no_config(self):
        """
        TC: _handle_response에서 토큰 만료 오류(EGW00123)가 발생했으나,
            _config가 None이라 토큰 재발급을 시도할 수 없을 때의 로직을 테스트합니다.
        이는 brokers/korea_investment/korea_invest_api_base.py의 140, 141번 라인을 커버합니다.
        """
        # Given:
        # 1. 토큰 만료 오류 응답 Mock
        mock_response_json = {"rt_cd": "1", "msg_cd": "EGW00123", "msg1": "토큰이 만료되었습니다."}

        # 2. _config를 None으로 설정하여 이 테스트 케이스의 특정 경로를 트리거
        self.api_base._config = None

        # When: _handle_response 호출
        result = await self.api_base._handle_response(MagicMock(json=MagicMock(return_value=mock_response_json)))

        # Then:
        # 로거 오류 메시지 확인 (134번 라인)
        self.mock_logger.error.assert_any_call("토큰 만료 오류(EGW00123) 감지.")
        # 로거 오류 메시지 확인 (140번 라인)
        self.mock_logger.error.assert_any_call("KoreaInvestEnv(config) 인스턴스를 찾을 수 없어 토큰 초기화 불가") # 140번 라인 커버

        # 토큰 매니저의 invalidate_token이 호출되지 않았는지 확인 (config가 없으므로)
        self.mock_token_manager.invalidate_token.assert_not_called()

        # 결과는 None이어야 함 (141번 라인 커버)
        self.assertIsNone(result)

    # --- 165, 166번 라인 커버: _handle_token_expiration에서 재시도 횟수 초과 ---
    # async def test_handle_token_expiration_retry_exceeded(self):
    #     """
    #     TC: _handle_token_expiration 메서드에서 재시도 횟수를 초과했을 때
    #         (attempt >= retry_count) 에러 로깅 후 None을 반환하는지 테스트합니다.
    #     이는 brokers/korea_investment/korea_invest_api_base.py의 165, 166번 라인을 커버합니다.
    #     """
    #     # Given:
    #     mock_response_json = {"msg_cd": "EGW00123"}
    #     attempt = 3
    #     retry_count = 3  # attempt == retry_count 이므로 재시도 초과
    #
    #     with patch('asyncio.sleep', new_callable=AsyncMock) as mock_asyncio_sleep:
    #         result = await self.api_base._handle_token_expiration(
    #             mock_response_json, attempt, retry_count, delay=1
    #         )
    #
    #         self.mock_token_manager.invalidate_token.assert_called_once()
    #
    #         # 📌 수정된 부분: assert_called_once_with 대신 assert_called_with를 사용합니다.
    #         #    혹은 mock_logger.error.call_args_list[-1]을 사용하여 마지막 호출을 검증할 수도 있습니다.
    #         self.mock_logger.error.assert_called_with("토큰 재발급 후에도 실패, 종료")  # 165번 라인 커버
    #
    #         # self.mock_logger.error가 총 2번 호출되었는지 검증 (옵션)
    #         self.assertEqual(self.mock_logger.error.call_count, 2)
    #
    #         self.assertIsNone(result)  # 166번 라인 커버
    #         mock_asyncio_sleep.assert_not_awaited()


class DummyAPI(KoreaInvestApiBase):
    def __init__(self, base_url, headers, config, token_manager, logger):
        # 부모 클래스의 생성자를 먼저 호출합니다.
        # 이 시점에 self._async_session은 실제 httpx.AsyncClient 인스턴스가 됩니다.
        super().__init__(base_url, headers, config, token_manager, logger)

        # 부모 생성자 호출 후, _async_session을 MagicMock으로 교체합니다.
        # 이렇게 하면 _async_session.get 같은 메서드들도 MagicMock 객체가 되어 side_effect를 할당할 수 있습니다.
        self._async_session = MagicMock()

        # 로거 메서드도 모킹하여 테스트 출력을 제어할 수 있습니다.
        self.logger.debug = MagicMock()
        self.logger.error = MagicMock()

    # call_api를 호출 가능하도록 래핑
    async def call_api_wrapper(self, *args, **kwargs):
        return await self.call_api(*args, **kwargs)

@pytest.mark.asyncio
async def testcall_api_retry_exceed_failure(caplog):
    base_url = "https://dummy-base"
    headers = {"Authorization": "Bearer dummy"}
    config = {
        "tr_ids": {},
        "_env_instance": None,
    }
    logger = logging.getLogger("test_logger")
    logger.setLevel(logging.ERROR)

    api = DummyAPI(base_url, headers, config, MagicMock(), logger)

    # 항상 500 + 초당 거래건수 초과 응답만 반환
    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_response.json.return_value = {"msg1": "초당 거래건수를 초과하였습니다."}
    mock_response.text = '{"msg1":"초당 거래건수를 초과하였습니다."}'
    mock_response.raise_for_status = MagicMock()

    api._session.get = MagicMock(return_value=mock_response)

    with caplog.at_level(logging.ERROR):
        result = await api.call_api('GET', '/dummy-path', retry_count=2, delay=0.01)

    assert result is None

    # ✅ 로그 메시지 수정에 맞춰 assertion 변경
    errors = [rec for rec in caplog.records if rec.levelname == "ERROR"]
    assert any("모든 재시도 실패" in rec.message for rec in errors)


@pytest.mark.asyncio
async def testcall_api_retry_exceed_failure(caplog):
    base_url = "https://dummy-base"
    headers = {"Authorization": "Bearer dummy"}
    config = {
        "tr_ids": {},
        "_env_instance": None,
    }
    # logger 변수를 제거하고 DummyAPI에 logger=None을 전달하여
    # KoreaInvestApiBase가 자체 __name__ 로거를 사용하도록 합니다.
    # logger = logging.getLogger("test_logger") # <- 이 줄 제거

    # 변경: DummyAPI 생성 시 logger=None을 전달합니다.
    mock_logger = MagicMock()
    api = DummyAPI(base_url, headers, config, MagicMock(), logger=mock_logger)

    # caplog를 KoreaInvestApiBase가 사용하는 __name__ 로거에 맞게 설정합니다.
    caplog.set_level(logging.ERROR, logger='brokers.korea_investment.korea_invest_api_base')

    # 항상 500 + 초당 거래건수 초과 응답만 반환
    # side_effect가 httpx.HTTPStatusError 인스턴스를 반환하도록 설정합니다.
    # 이 테스트는 재시도 후 실패하는 시나리오를 테스트하므로, 실제 응답을 시뮬레이션할 필요가 있습니다.
    # 여러 번 실패하고 마지막에 재시도 횟수 초과로 종료되어야 합니다.
    # 따라서 side_effect는 호출될 때마다 httpx.HTTPStatusError를 발생시켜야 합니다.
    api._async_session.get.side_effect = [
        httpx.HTTPStatusError(
            "Rate Limit Exceeded",
            request=httpx.Request("GET", f"{base_url}/test"),
            response=MagicMock(status_code=429, text="Rate Limit Exceeded")
        ) for _ in range(3)  # retry_count가 3이므로 3번 모두 실패하도록 설정
    ]

    # with caplog.at_level(logging.ERROR): # caplog.set_level을 이미 위에 설정했으므로, 이 컨텍스트 매니저는 불필요할 수 있습니다.
    result = await api.call_api('GET', '/test', retry_count=3, delay=0.01)

    # 변경: caplog.text 대신 mock_logger.error의 호출을 직접 단언합니다.
    mock_logger.error.assert_called_with("모든 재시도 실패, API 호출 종료")

    assert api._async_session.get.call_count == 3

    assert mock_logger.error.call_count == 4


@pytest.mark.asyncio
async def testcall_api_success(caplog):
    # caplog 설정은 이전과 동일
    caplog.set_level(logging.DEBUG, logger='brokers.korea_investment.korea_invest_api_base')

    # DummyAPI에 전달할 로거를 명시적인 MagicMock으로 생성합니다.
    dummy_logger = MagicMock()
    dummy = DummyAPI(
        base_url="https://mock-base",
        headers={},
        config={},
        token_manager=MagicMock(),
        logger=dummy_logger
    )

    dummy._log_request_exception = MagicMock()

    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.text = '{"key":"value", "rt_cd":"0"}'
    mock_response.json.return_value = {"key": "value", "rt_cd": "0"}

    mock_response.raise_for_status.return_value = None
    mock_response.raise_for_status.side_effect = None

    dummy._async_session.get = AsyncMock(return_value=mock_response)

    result = await dummy.call_api('GET', '/test')

    assert result == {"key": "value", "rt_cd": "0"}

    # 이제 dummy._log_request_exception은 MagicMock이므로 assert_not_called() 사용 가능
    dummy._log_request_exception.get.assert_not_called()

    # 로깅 단언문은 이전과 동일
    assert dummy_logger.debug.called  # debug 로거가 호출되었는지 확인
    dummy_logger.debug.assert_called_with(f"API 응답 성공: {mock_response.text}")
    dummy_logger.error.assert_not_called()

    # caplog를 통한 추가 로그 검증
    assert not any("JSON 디코딩 실패" in record.message for record in caplog.records)
    assert not any("HTTP 오류 발생" in record.message for record in caplog.records)
    assert not any("예상치 못한 예외 발생" in record.message for record in caplog.records)
    assert not any("모든 재시도 실패" in record.message for record in caplog.records)
    assert not any("API 비즈니스 오류" in record.message for record in caplog.records)

    error_logs = [record for record in caplog.records if record.levelno >= logging.ERROR]
    assert len(error_logs) == 0, f"예상치 못한 오류 로그: {[record.message for record in error_logs]}"

@pytest.mark.asyncio
@patch("asyncio.sleep", new_callable=AsyncMock)  # <-- sleep patch
async def testcall_api_retry_on_429(mock_sleep, caplog):
    dummy_logger = MagicMock() # 모의 로거 생성
    dummy = DummyAPI(
        base_url="https://mock-base",
        headers={},
        config={},
        token_manager=MagicMock(),
        logger=dummy_logger # 모의 로거 전달
    )
    responses_list = [] # mock_get_async가 생성하는 응답 객체를 추적하기 위한 리스트

    # 변경: mock_get을 비동기 코루틴 함수로 정의
    async def mock_get_async(*args, **kwargs):
        resp = MagicMock(spec=httpx.Response)  # httpx.Response 스펙을 따름
        if len(responses_list) < 2:  # 첫 2번은 429 응답
            resp.status_code = 429
            resp.text = "Too Many Requests"
            resp.json.return_value = {}  # 빈 딕셔너리 반환
            resp.raise_for_status.return_value = None  # HTTP 오류를 발생시키지 않도록
            resp.raise_for_status.side_effect = None
        else:  # 3번째부터는 200 성공 응답
            resp.status_code = 200
            resp.text = '{"success":true}'
            resp.json.return_value = {"success": True}
            resp.raise_for_status.return_value = None
            resp.raise_for_status.side_effect = None

        responses_list.append(resp)  # 생성된 응답 객체를 리스트에 추가
        return resp  # 비동기 함수이므로 awaitable을 반환

    # 변경: dummy._async_session.get에 mock_get_async를 side_effect로 할당
    # AsyncMock은 side_effect가 awaitable을 반환하면 그 awaitable을 await합니다.
    dummy._async_session.get.side_effect = mock_get_async

    # 변경: call_api_wrapper 대신 KoreaInvestApiBase의 call_api를 직접 호출
    # retry_count를 넉넉하게 설정하여 3번의 호출이 충분히 발생하도록 합니다.
    result = await dummy.call_api('GET', '/retry', retry_count=5, delay=0.01)

    assert result == {"success": True}
    assert len(responses_list) == 3 # 3번의 응답 객체가 생성되었는지 확인 (2번 실패, 1번 성공)
    assert dummy._async_session.get.call_count == 3 # 모의 get 메서드가 3번 호출되었는지 확인

    # asyncio.sleep이 호출되었는지, 적절한 인자로 호출되었는지 확인
    # 429 에러가 2번 발생했으므로, 2번의 sleep 호출 예상 (첫 실패 후, 두 번째 실패 후)
    assert mock_sleep.call_count == 2
    mock_sleep.assert_called_with(0.01) # delay 인자로 호출되었는지 확인


@pytest.mark.asyncio
@patch("asyncio.sleep", new_callable=AsyncMock)
async def testcall_api_retry_on_500_rate_limit(mock_sleep):
    dummy_logger = MagicMock()
    dummy = DummyAPI(
        base_url="https://mock-base",
        headers={},
        config={},
        token_manager=MagicMock(),
        logger=dummy_logger
    )
    responses_list = []

    async def mock_get_async(*args, **kwargs):
        resp = MagicMock(spec=httpx.Response)
        if len(responses_list) < 2:
            resp.status_code = 500
            resp.text = '{"msg1":"초당 거래건수를 초과하였습니다."}'
            resp.json.return_value = {"msg1": "초당 거래건수를 초과하였습니다."}
            # 이 500 오류 응답은 _handle_response에서 "retry"로 처리되어야 하며,
            # 비즈니스 오류로 로깅되지 않아야 합니다. (위의 _handle_response 수정으로 보장)
        else:
            resp.status_code = 200
            # 변경: 성공 응답에 rt_cd: "0"을 포함하도록 수정
            resp.text = '{"success":true, "rt_cd":"0"}'
            resp.json.return_value = {"success": True, "rt_cd": "0"}

        resp.raise_for_status.return_value = None
        resp.raise_for_status.side_effect = None
        responses_list.append(resp)
        return resp

    dummy._async_session.get.side_effect = mock_get_async
    dummy._log_request_exception = MagicMock()

    result = await dummy.call_api('GET', '/retry500', retry_count=5, delay=0.01)

    assert result == {"success": True, "rt_cd": "0"}
    assert len(responses_list) == 3
    assert dummy._async_session.get.call_count == 3
    assert mock_sleep.call_count == 2
    mock_sleep.assert_called_with(0.01)

    # _log_request_exception은 예외가 call_api에서 잡힐 때만 호출됩니다.
    # 이 테스트에서는 _handle_response가 "retry"를 반환하므로 예외는 call_api에서 잡히지 않습니다.
    dummy._log_request_exception.assert_not_called()

    # 이제 dummy_logger.error는 호출되지 않아야 합니다 (_handle_response가 수정되었으므로).
    dummy_logger.error.assert_not_called()

    dummy_logger.info.assert_any_call("재시도 필요: 1/5, 지연 0.01초")
    dummy_logger.info.assert_any_call("재시도 필요: 2/5, 지연 0.01초")

    # 디버그 로그는 성공 응답 시에만 호출되어야 합니다.
    dummy_logger.debug.assert_any_call(f"API 응답 성공: {responses_list[2].text}")


@pytest.mark.asyncio
async def testcall_api_token_expired_retry():
    class MockTokenManager:
        def __init__(self):
            self.invalidated = False
        def invalidate_token(self):
            self.invalidated = True

    token_manager = MockTokenManager()

    dummy = DummyAPI(
        base_url="https://mock-base",
        headers={},
        config={"_env_instance": MagicMock()},  # _config is not None
        token_manager=token_manager,
        logger=MagicMock()
    )

    responses = []

    async def mock_get_async(*args, **kwargs):
        resp = MagicMock(spec=httpx.Response)
        if len(responses) < 1:
            resp.status_code = 200
            resp.text = '{"rt_cd":"1","msg_cd":"EGW00123"}'
            resp.json.return_value = {"rt_cd": "1", "msg_cd": "EGW00123"}
        else:
            resp.status_code = 200
            resp.text = '{"success":true}'
            resp.json.return_value = {"success": True}

        def _raise_for_status():
            if resp.status_code >= 400:
                raise httpx.HTTPStatusError(
                    f"HTTP Error {resp.status_code}",
                    request=httpx.Request("GET", args[0]),
                    response=resp
                )

        resp.raise_for_status.side_effect = _raise_for_status
        responses.append(resp)
        return resp

    dummy._async_session.get.side_effect = mock_get_async

    result = await dummy.call_api('GET', '/token_expired', retry_count=5, delay=0.01)

    assert result == {"success": True}
    assert token_manager.invalidated is True
    assert dummy._async_session.get.call_count == 2

    # 수정된 부분: 전체 URL을 예상 인자로 사용
    dummy._async_session.get.assert_called_with(
        'https://mock-base/token_expired',  # <- 전체 URL로 변경
        headers=dummy._headers,
        params=None
    )

@pytest.mark.asyncio
async def testcall_api_http_error(monkeypatch):
    class MockTokenManager:
        def __init__(self):
            self.invalidated = False
        def invalidate_token(self):
            self.invalidated = True

    token_manager = MockTokenManager()

    dummy = DummyAPI(
        base_url="https://mock-base",
        headers={},
        config={"_env_instance": MagicMock()},  # _config is not None
        token_manager=token_manager,
        logger=MagicMock()
    )
    resp = MagicMock()
    resp.status_code = 400
    resp.text = "Bad Request"
    http_error = requests.exceptions.HTTPError(response=resp)

    async def mock_get_async(*args, **kwargs):
        raise http_error

    dummy._async_session.get = MagicMock(side_effect=mock_get_async)

    result = await dummy.call_api_wrapper('GET', '/http_error')
    assert result is None

@pytest.mark.asyncio
async def testcall_api_connection_error(monkeypatch):
    class MockTokenManager:
        def __init__(self):
            self.invalidated = False
        def invalidate_token(self):
            self.invalidated = True

    token_manager = MockTokenManager()

    dummy = DummyAPI(
        base_url="https://mock-base",
        headers={},
        config={"_env_instance": MagicMock()},  # _config is not None
        token_manager=token_manager,
        logger=MagicMock()
    )

    async def mock_get_async(*args, **kwargs):
        raise requests.exceptions.ConnectionError("Connection failed")

    dummy._async_session.get = AsyncMock(side_effect=mock_get_async)

    result = await dummy.call_api_wrapper('GET', '/conn_err')
    assert result is None

@pytest.mark.asyncio
async def testcall_api_timeout(monkeypatch):
    class MockTokenManager:
        def __init__(self):
            self.invalidated = False
        def invalidate_token(self):
            self.invalidated = True

    token_manager = MockTokenManager()

    dummy = DummyAPI(
        base_url="https://mock-base",
        headers={},
        config={"_env_instance": MagicMock()},  # _config is not None
        token_manager=token_manager,
        logger=MagicMock()
    )
    async def mock_get_async(*args, **kwargs):
        raise requests.exceptions.Timeout("Timeout error")

    dummy._async_session.get = MagicMock(side_effect=mock_get_async)

    result = await dummy.call_api_wrapper('GET', '/timeout')
    assert result is None

@pytest.mark.asyncio
async def testcall_api_json_decode_error(monkeypatch):
    class MockTokenManager:
        def __init__(self):
            self.invalidated = False
        def invalidate_token(self):
            self.invalidated = True

    token_manager = MockTokenManager()

    dummy = DummyAPI(
        base_url="https://mock-base",
        headers={},
        config={"_env_instance": MagicMock()},  # _config is not None
        token_manager=token_manager,
        logger=MagicMock()
    )

    resp = AsyncMock()
    resp.status_code = 200
    resp.text = "not json"
    resp.json.side_effect = ValueError("JSON decode error")
    resp.raise_for_status.return_value = None

    dummy._async_session.get = MagicMock(return_value=resp)

    result = await dummy.call_api_wrapper('GET', '/json_error')
    assert result is None
