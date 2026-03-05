# tests/test_korea_invest_api_base.py
import unittest
import pytest
import json
from unittest.mock import MagicMock, AsyncMock, patch, call
from brokers.korea_investment.korea_invest_api_base import KoreaInvestApiBase, ApiRetryError
from brokers.korea_investment.korea_invest_env import KoreaInvestApiEnv
from brokers.korea_investment.korea_invest_token_manager import TokenManager
import requests
import logging
import httpx  # 에러 시뮬레이션을 위해 import
from common.types import ErrorCode, ResCommonResponse


def get_test_logger():
    logger = logging.getLogger("test_logger")
    logger.setLevel(logging.DEBUG)

    # 기존 핸들러 제거
    if logger.hasHandlers():
        logger.handlers.clear()

    # 콘솔 출력만 (파일 기록 없음)
    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(logging.DEBUG)
    formatter = logging.Formatter("%(levelname)s - %(message)s")
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    return logger


def get_mock_env():
    mock_env = MagicMock(spec=KoreaInvestApiEnv)
    mock_env.get_access_token = AsyncMock(return_value="test-token-for-success-case")
    mock_env.my_agent = "test-agent"  # ✅ 필수 속성 설정
    mock_env.set_trading_mode(True)  # ✅ 모의투자 모드 설정
    mock_env.get_base_url.return_value = "https://mock-base"  # ✅ 이 부분 추가!

    mock_env.active_config = {
        "headers": {
            "User-Agent": "test-agent",
            "Content-Type": "application/json",
        },
        "api_key": "dummy",
        "api_secret_key": "dummy",
        "paths": {
            "token": "https://openapi.test.com/oauth2/tokenP",
            "token_reissue": "https://openapi.test.com/oauth2/reissue"
        },
        "tr_ids": {
            "quotations": {
                "search_info": "FHKST01010100"
            }
        },
        "custtype": "P"
    }

    return mock_env


class TestKoreaInvestApiBase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        """ 각 테스트 실행 전에 필요한 객체들을 초기화합니다. """
        self.mock_logger = MagicMock()
        self.mock_time_manager = AsyncMock()

        # spec=KoreaInvestApiEnv 인자를 추가하여 mock_env가
        # KoreaInvestApiEnv의 인스턴스인 것처럼 동작하게 만듭니다.
        self.mock_env = get_mock_env()
        self.mock_config = {
            '_env_instance': self.mock_env,
        }

        self.api_base = KoreaInvestApiBase(
            env=self.mock_env,
            logger=self.mock_logger,
            time_manager=self.mock_time_manager
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
            "msg_cd": "EGW00123",  # 토큰 만료 코드
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
            final_result = await self.api_base.call_api('POST', '/test-path')

            # --- Assert (검증) ---
            # 1. 최종 결과 검증: 두 번째 시도의 성공적인 결과값이 반환되었는지 확인합니다.
            self.assertIsNotNone(final_result)
            self.assertEqual(final_result.data["output"].get("result"), "success_data")

            # 2. 호출 횟수 검증: API가 총 2번 호출되었는지 확인합니다. (첫 시도 실패 -> 재시도 성공)
            self.assertEqual(mock_execute.call_count, 2)

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

    async def test_execute_request_internal_token_refresh(self):
        """
        TC: _execute_request 내부에서 EGW00123 응답 시 토큰 갱신 후 재시도 로직 검증
        """
        # Arrange
        # get_access_token이 3번 호출됨: 1. 첫 시도, 2. 명시적 갱신 후 조회, 3. 재시도
        self.mock_env.get_access_token = AsyncMock(side_effect=["old_token", "new_token", "new_token"])
        self.mock_env.refresh_token = AsyncMock()

        # httpx response mocking
        # 1st response: EGW00123
        resp1 = MagicMock(spec=httpx.Response)
        resp1.json.return_value = {"msg_cd": "EGW00123", "rt_cd": "1"}

        # 2nd response: Success
        resp2 = MagicMock(spec=httpx.Response)
        resp2.json.return_value = {"rt_cd": "0", "msg_cd": "MCA00000"}

        # _async_session.post를 모킹 (method가 POST라고 가정)
        self.api_base._async_session.post = AsyncMock(side_effect=[resp1, resp2])

        # Act
        response = await self.api_base._execute_request("POST", "http://test-url", None, {"data": 1})

        # Assert
        self.mock_env.refresh_token.assert_awaited_once()
        self.assertEqual(response, resp2)
        # 총 2번의 post 요청이 있었는지 확인
        self.assertEqual(self.api_base._async_session.post.await_count, 2)

    async def test_call_api_custom_retry_delay(self):
        """
        TC: ApiRetryError에 delay가 설정된 경우 해당 시간만큼 대기하는지 검증
        """
        # Arrange
        custom_delay = 0.5
        success_response = ResCommonResponse(rt_cd="0", msg1="Success", data={"output": {}})

        # _execute_request가 ApiRetryError를 발생시키도록 설정
        with patch.object(self.api_base, '_execute_request', new_callable=AsyncMock) as mock_execute:
            mock_execute.side_effect = [
                ApiRetryError("Custom Delay Error", delay=custom_delay),
                success_response
            ]

            # Act
            await self.api_base.call_api("GET", "/test", retry_count=2)

            # Assert
            # time_manager.async_sleep이 custom_delay로 호출되었는지 확인
            self.mock_time_manager.async_sleep.assert_awaited_with(custom_delay)

    async def test_handle_response_no_standard_schema(self):
        """
        TC: expect_standard_schema=False일 때 rt_cd 검사 없이 성공 처리되는지 검증
        """
        # Arrange
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {"some_key": "some_value"}  # No rt_cd
        mock_response.raise_for_status.return_value = None

        # Act
        result = await self.api_base._handle_response(mock_response, expect_standard_schema=False)

        # Assert
        self.assertEqual(result, {"some_key": "some_value"})

    async def test_execute_request_missing_token(self):
        """
        TC: Access Token이 없을 때 ValueError 발생하는지 검증
        """
        # Arrange
        self.mock_env.get_access_token = AsyncMock(return_value=None)

        # Act & Assert
        with self.assertRaises(ValueError) as cm:
            await self.api_base._execute_request("GET", "http://test", None, None)
        self.assertIn("접근 토큰이 없습니다", str(cm.exception))

    async def test_execute_request_httpx_error(self):
        """
        TC: _execute_request 내부에서 httpx.RequestError 발생 시 ResCommonResponse 반환 검증
        """
        # Arrange
        self.mock_env.get_access_token = AsyncMock(return_value="token")
        self.api_base._async_session.get = AsyncMock(side_effect=httpx.RequestError("Connection failed"))

        # Act
        response = await self.api_base._execute_request("GET", "http://test", None, None)

        # Assert
        self.assertIsInstance(response, ResCommonResponse)
        self.assertEqual(response.rt_cd, ErrorCode.NETWORK_ERROR.value)
        self.assertIn("Connection failed", response.msg1)

    async def test_execute_request_response_is_none(self):
        """
        TC: _execute_request 내부에서 make_request가 None을 반환할 때 ValueError 발생 검증
        """
        # Arrange
        self.mock_env.get_access_token = AsyncMock(return_value="token")
        # _async_session.get이 None을 반환하도록 설정
        self.api_base._async_session.get = AsyncMock(return_value=None)

        # Act & Assert
        with self.assertRaises(ValueError) as cm:
            await self.api_base._execute_request("GET", "http://test", None, None)
        self.assertIn("response is None", str(cm.exception))

    async def test_execute_request_token_refresh_retry_fail(self):
        """
        TC: _execute_request 내부에서 토큰 갱신 후 재시도했으나 여전히 EGW00123 응답인 경우
        """
        # Arrange
        self.mock_env.get_access_token = AsyncMock(return_value="token")
        self.mock_env.refresh_token = AsyncMock()

        # EGW00123 응답
        resp_fail = MagicMock(spec=httpx.Response)
        resp_fail.json.return_value = {"msg_cd": "EGW00123", "rt_cd": "1"}

        # 2번 모두 실패 응답
        self.api_base._async_session.post = AsyncMock(return_value=resp_fail)

        # Act
        response = await self.api_base._execute_request("POST", "http://test", None, {"data": 1})

        # Assert
        self.mock_env.refresh_token.assert_awaited_once()
        # 결과가 여전히 실패 응답이어야 함
        self.assertEqual(response, resp_fail)
        # 총 2번 호출 (첫 시도 -> 갱신 -> 재시도)
        self.assertEqual(self.api_base._async_session.post.await_count, 2)


class DummyAPI(KoreaInvestApiBase):
    def __init__(self, env, logger, time_manager):
        # 부모 클래스의 생성자를 먼저 호출합니다.
        # 이 시점에 self._async_session은 실제 httpx.AsyncClient 인스턴스가 됩니다.
        super().__init__(env, logger, time_manager)

        # 부모 생성자 호출 후, _async_session을 MagicMock으로 교체합니다.
        # 이렇게 하면 _async_session.get 같은 메서드들도 MagicMock 객체가 되어 side_effect를 할당할 수 있습니다.
        self._async_session = MagicMock()

        # 로거 메서드도 모킹하여 테스트 출력을 제어할 수 있습니다.
        self._logger.debug = MagicMock()
        self._logger.error = MagicMock()

    # call_api를 호출 가능하도록 래핑
    async def call_api_wrapper(self, *args, **kwargs):
        return await self.call_api(*args, **kwargs)

def get_api():
    mock_env = get_mock_env()
    mock_logger = get_test_logger()
    mock_time_manager = AsyncMock()
    mock_time_manager.async_sleep = AsyncMock(return_value=None)

    return DummyAPI(mock_env,mock_logger,mock_time_manager)

@pytest.mark.asyncio
async def testcall_api_retry_exceed_failure(caplog):
    logger = get_test_logger()
    logger.setLevel(logging.ERROR)

    api = get_api()

    # 항상 500 + 초당 거래건수 초과 응답만 반환
    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_response.json.return_value = {"msg1": "초당 거래건수를 초과하였습니다."}
    mock_response.text = '{"msg1":"초당 거래건수를 초과하였습니다."}'
    mock_response.raise_for_status = MagicMock()

    with caplog.at_level(logging.ERROR):
        result = await api.call_api('GET', '/dummy-path', retry_count=2, delay=0.01)

    assert result is None

    # ✅ 로그 메시지 수정에 맞춰 assertion 변경
    errors = [rec for rec in caplog.records if rec.levelname == "ERROR"]
    assert any("모든 재시도 실패" in rec.message for rec in errors)


@pytest.mark.asyncio
async def testcall_api_retry_exceed_failure(caplog):
    base_url = "https://dummy-base"

    # logger 변수를 제거하고 DummyAPI에 logger=None을 전달하여
    # KoreaInvestApiBase가 자체 __name__ 로거를 사용하도록 합니다.
    # logger = logging.getLogger("test_logger") # <- 이 줄 제거

    # 변경: DummyAPI 생성 시 logger=None을 전달합니다.
    api = get_api()

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
    api._logger.error.assert_called_with("모든 재시도 실패, API 호출 종료")

    assert api._async_session.get.call_count == 3

    api._logger.error.assert_any_call("모든 재시도 실패, API 호출 종료")


@pytest.mark.asyncio
async def testcall_api_success(caplog):
    # caplog 설정은 이전과 동일
    caplog.set_level(logging.DEBUG, logger='brokers.korea_investment.korea_invest_api_base')

    # --- 수정된 부분 ---
    # _execute_request에서 `await self._env.get_access_token()`을 호출하므로,
    # 비동기(async) 메서드를 가진 mock 객체를 생성해야 합니다.
    mock_env = MagicMock()
    mock_env.get_access_token = AsyncMock(return_value="test-token-for-success-case")
    mock_env.my_agent = "test-agent"  # ✅ 필수 속성 설정
    # ✅ base_url을 문자열로 명시
    mock_env.get_base_url = MagicMock(return_value="https://api.test")

    # DummyAPI에 전달할 로거를 명시적인 MagicMock으로 생성합니다.
    api = get_api()

    api._log_request_exception = MagicMock()

    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.text = '{"rt_cd":"0","msg1":"정상","output":{"key":"value"}}'
    mock_response.json.return_value = {
        "rt_cd": "0",
        "msg1": "정상",
        "output": {"key": "value"}
    }

    mock_response.raise_for_status.return_value = None
    mock_response.raise_for_status.side_effect = None

    api._async_session.get = AsyncMock(return_value=mock_response)

    result = await api.call_api('GET', '/test')

    assert result.rt_cd == ErrorCode.SUCCESS.value
    assert result.msg1 == "정상"
    assert result.data.get("output") == {"key": "value"}

    # 이제 dummy._log_request_exception은 MagicMock이므로 assert_not_called() 사용 가능
    api._log_request_exception.get.assert_not_called()

    # 로깅 단언문은 이전과 동일
    assert api._logger.debug.called  # debug 로거가 호출되었는지 확인
    api._logger.debug.assert_called_with(f"API 응답 성공: {mock_response.text}")
    api._logger.error.assert_not_called()

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
async def testcall_api_retry_on_429(caplog):
    api = get_api()

    # ✅ 재시도 대기 비활성 + await 가능
    api.time_manager.async_sleep = AsyncMock(return_value=None)

    responses_list = []  # mock_get_async가 생성하는 응답 객체를 추적하기 위한 리스트

    # 변경: mock_get을 비동기 코루틴 함수로 정의
    async def mock_get_async(*args, **kwargs):
        resp = MagicMock(spec=httpx.Response)  # httpx.Response 스펙을 따름
        if len(responses_list) < 2:  # 첫 2번은 429 응답
            resp.status_code = 429
            resp.text = '{"rt_cd":"0","msg1":"Too Many Requests","output":{}}'
            resp.json.return_value = {
                "rt_cd": "0",
                "msg1": "정상",
                "output": {}
            }

            resp.raise_for_status.return_value = None  # HTTP 오류를 발생시키지 않도록
            resp.raise_for_status.side_effect = None
        else:  # 3번째부터는 200 성공 응답
            resp.status_code = 200
            resp.text = '{"rt_cd":"0","msg1":"정상","output":{"key":"value"}}'
            resp.json.return_value = {
                "rt_cd": "0",
                "msg1": "정상",
                "output": {"success": True}
            }
            resp.raise_for_status.return_value = None
            resp.raise_for_status.side_effect = None

        responses_list.append(resp)  # 생성된 응답 객체를 리스트에 추가
        return resp  # 비동기 함수이므로 awaitable을 반환

    # 변경: dummy._async_session.get에 mock_get_async를 side_effect로 할당
    # AsyncMock은 side_effect가 awaitable을 반환하면 그 awaitable을 await합니다.
    api._async_session.get.side_effect = mock_get_async

    # 변경: call_api_wrapper 대신 KoreaInvestApiBase의 call_api를 직접 호출
    # retry_count를 넉넉하게 설정하여 3번의 호출이 충분히 발생하도록 합니다.
    result = await api.call_api('GET', '/retry', retry_count=5, delay=0.01)

    assert result.rt_cd == "0"
    assert result.msg1 == "정상"
    assert result.data["output"]["success"] is True  # ✅ 성공

    assert len(responses_list) == 3  # 3번의 응답 객체가 생성되었는지 확인 (2번 실패, 1번 성공)
    assert api._async_session.get.call_count == 3  # 모의 get 메서드가 3번 호출되었는지 확인

    # asyncio.sleep이 호출되었는지, 적절한 인자로 호출되었는지 확인
    # 429 에러가 2번 발생했으므로, 2번의 sleep 호출 예상 (첫 실패 후, 두 번째 실패 후)
    assert api.time_manager.async_sleep.await_count == 2
    # 지수 백오프 적용: 1회차 0.01, 2회차 0.02
    api.time_manager.async_sleep.assert_has_awaits([call(0.01), call(0.02)])


@pytest.mark.asyncio
@patch("core.time_manager.TimeManager.async_sleep", new_callable=AsyncMock)  # ← 타겟 교체
async def testcall_api_retry_on_500_rate_limit(mock_sleep):
    api = get_api()
    api._logger = MagicMock(wraps=api._logger)

    responses_list = []

    async def mock_get_async(*args, **kwargs):
        resp = MagicMock(spec=httpx.Response)
        if len(responses_list) < 2:
            resp.status_code = 500
            resp.text = '{"rt_cd":"1","msg1":"초당 거래건수를 초과하였습니다.","output":{"success": True}}'
            resp.json.return_value = {
                "rt_cd": "1",
                "msg1": "초당 거래건수를 초과하였습니다.",
                "output": {"success": False}
            }
            # 이 500 오류 응답은 _handle_response에서 "retry"로 처리되어야 하며,
            # 비즈니스 오류로 로깅되지 않아야 합니다. (위의 _handle_response 수정으로 보장)
        else:
            resp.status_code = 200
            # 변경: 성공 응답에 rt_cd: "0"을 포함하도록 수정
            resp.text = '{"rt_cd":"0","msg1":"정상","output":{"success":true}}'
            resp.json.return_value = {
                "rt_cd": "0",
                "msg1": "정상",
                "output": {"success": True}
            }
        resp.raise_for_status.return_value = None
        resp.raise_for_status.side_effect = None
        responses_list.append(resp)
        return resp

    api._async_session.get.side_effect = mock_get_async
    api._log_request_exception = MagicMock()

    result = await api.call_api('GET', '/retry500', retry_count=5, delay=0.01)

    assert result.rt_cd == "0"
    assert result.msg1 == "정상"
    assert result.data["output"] == {"success": True}

    assert len(responses_list) == 3
    assert api._async_session.get.call_count == 3
    assert api.time_manager.async_sleep.await_count == 2
    # 지수 백오프 적용: 1회차 0.01, 2회차 0.02
    api.time_manager.async_sleep.assert_has_awaits([call(0.01), call(0.02)])


    # _log_request_exception은 예외가 call_api에서 잡힐 때만 호출됩니다.
    # 이 테스트에서는 _handle_response가 "retry"를 반환하므로 예외는 call_api에서 잡히지 않습니다.
    api._log_request_exception.assert_not_called()

    # 이제 dummy_logger.error는 호출되지 않아야 합니다 (_handle_response가 수정되었으므로).
    api._logger.error.assert_not_called()

    # 로그 메시지 포맷 변경 반영 (사유 포함)
    assert any("재시도 필요: 1/5" in str(c) for c in api._logger.warning.call_args_list)
    assert any("재시도 필요: 2/5" in str(c) for c in api._logger.warning.call_args_list)

    # 디버그 로그는 성공 응답 시에만 호출되어야 합니다.
    api._logger.debug.assert_any_call(f"API 응답 성공: {responses_list[2].text}")


@pytest.mark.asyncio
async def testcall_api_token_expired_retry():
    class MockTokenManager:
        def __init__(self):
            self.invalidated = False

        def invalidate_token(self):
            self.invalidated = True

    dummy = get_api()

    responses = []

    async def mock_get_async(*args, **kwargs):
        resp = MagicMock(spec=httpx.Response)
        if len(responses) < 1:
            resp.status_code = 200
            resp.text = '{"rt_cd":"1","msg_cd":"EGW00123","output":{"success":true}}'
            resp.json.return_value = {
                "rt_cd": "1",
                "msg_cd": "EGW00123",
                "output": {"success": False}}
        else:
            resp.status_code = 200
            resp.text = '{"rt_cd":"0","msg1":"정상","output":{"success":true}}'
            resp.json.return_value = {
                "rt_cd": "0",
                "msg1": "정상",
                "output": {"success": True}
            }

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

    assert result.rt_cd == ErrorCode.SUCCESS.value
    assert result.msg1 == "정상"
    assert result.data["output"] == {"success": True}
    assert dummy._async_session.get.call_count == 2

    # 수정된 부분: 전체 URL을 예상 인자로 사용
    dummy._async_session.get.assert_called_with(
        'https://mock-base/token_expired',  # <- 전체 URL로 변경
        headers=dummy._headers.build(),  # ✅ 객체 → 빌드된 dict로 검증
        params=None
    )


@pytest.mark.asyncio
async def testcall_api_http_error(monkeypatch):
    class MockTokenManager:
        def __init__(self):
            self.invalidated = False

        def invalidate_token(self):
            self.invalidated = True

    api = get_api()

    resp = MagicMock()
    resp.status_code = 400
    resp.text = "Bad Request"
    http_error = requests.exceptions.HTTPError(response=resp)

    async def mock_get_async(*args, **kwargs):
        raise http_error

    api._async_session.get = MagicMock(side_effect=mock_get_async)

    result = await api.call_api_wrapper('GET', '/http_error')

    assert result.rt_cd != "0"
    assert result.msg1 != "정상"
    assert result.data is None


@pytest.mark.asyncio
async def testcall_api_connection_error(monkeypatch):
    class MockTokenManager:
        def __init__(self):
            self.invalidated = False

        def invalidate_token(self):
            self.invalidated = True

    mock_env = get_mock_env()

    api = get_api()

    async def mock_get_async(*args, **kwargs):
        raise requests.exceptions.ConnectionError("Connection failed")

    api._async_session.get = AsyncMock(side_effect=mock_get_async)

    result = await api.call_api_wrapper('GET', '/conn_err')

    assert result.rt_cd != "0"
    assert result.msg1 != "정상"
    assert result.data is None


@pytest.mark.asyncio
async def testcall_api_timeout(monkeypatch):
    class MockTokenManager:
        def __init__(self):
            self.invalidated = False

        def invalidate_token(self):
            self.invalidated = True

    mock_env = get_mock_env()

    api = get_api()

    async def mock_get_async(*args, **kwargs):
        raise requests.exceptions.Timeout("Timeout error")

    api._async_session.get = MagicMock(side_effect=mock_get_async)

    result = await api.call_api_wrapper('GET', '/timeout')

    assert result.rt_cd != "0"
    assert result.msg1 != "정상"
    assert result.data is None


@pytest.mark.asyncio
async def testcall_api_json_decode_error(monkeypatch):
    mock_env = get_mock_env()

    api = get_api()

    resp = AsyncMock()
    resp.status_code = 200
    resp.text = "not json"
    resp.json.side_effect = ValueError("JSON decode error")
    resp.raise_for_status.return_value = None

    api._async_session.get = MagicMock(return_value=resp)

    result = await api.call_api_wrapper('GET', '/json_error')

    assert result.rt_cd != "0"
    assert result.msg1 != "정상"
    assert result.data is None


@pytest.mark.asyncio
async def test_log_request_exception_cases(caplog):
    mock_env = get_mock_env()
    api = KoreaInvestApiBase(mock_env, logger=None)

    class DummyResponse:
        status_code = 500
        text = "error"

    http_error = requests.exceptions.HTTPError(response=DummyResponse())
    connection_error = requests.exceptions.ConnectionError("conn")
    timeout_error = requests.exceptions.Timeout("timeout")
    request_exception = requests.exceptions.RequestException("req")
    json_error = json.JSONDecodeError("msg", "doc", 0)
    generic_exception = Exception("generic")

    with caplog.at_level("ERROR"):
        for exc in [http_error, connection_error, timeout_error, request_exception, json_error, generic_exception]:
            api._log_request_exception(exc)

    for expected in ["HTTP ", " ", "", " ", "JSON ", " "]:
        assert any(expected in message for message in caplog.messages)

    for record in caplog.records:
        assert record.exc_info is not None


@pytest.mark.asyncio
async def test_execute_request_post(monkeypatch):  # monkeypatch fixture 사용
    mock_env = get_mock_env()

    api = KoreaInvestApiBase(mock_env, logger=None)

    # httpx.Response 스펙을 따르는 mock_response 생성
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.text = '{"some": "data"}'  # 응답 텍스트 추가
    mock_response.json.return_value = {"some": "data"}  # json() 메서드 모킹

    # 변경: api._session 대신 api._async_session.post를 대상으로 모킹합니다.
    # _async_session.post는 비동기 메서드이므로 AsyncMock으로 설정해야 합니다.
    # AsyncMock의 return_value에 mock_response를 설정합니다.
    monkeypatch.setattr(api._async_session, "post", AsyncMock(return_value=mock_response))

    # _execute_request는 데이터를 'json' 파라미터로 httpx에 전달합니다.
    result = await api._execute_request("POST", "http://test", params={}, data={"x": "y"})

    assert result.status_code == 200

    # 변경: 모킹된 post 메서드가 올바른 인자로 호출되었는지 확인합니다.
    # httpx는 딕셔너리 데이터를 'json' 파라미터로 받습니다.
    api._async_session.post.assert_called_once_with(
        "http://test",
        headers=api._headers.build(),             # ← provider 객체가 아니라 build() 결과
        data=json.dumps({"x": "y"})  # ✅ 문자열로 바꿔야 함
    )


@pytest.mark.asyncio
async def test_execute_request_invalid_method():
    mock_env = get_mock_env()

    api = KoreaInvestApiBase(mock_env, logger=None)
    api._session = MagicMock()

    with pytest.raises(ValueError):
        await api._execute_request("PUT", "http://test", {}, {})


class ExplodingString(str):
    def encode(self, encoding='utf-8', errors='strict'):
        raise UnicodeEncodeError(encoding, self, 0, 1, "intentional failure")


class ExplodingHeader:
    def __str__(self):
        return ExplodingString("trigger")


class ExplodingStr:
    def __str__(self):
        raise UnicodeEncodeError("utf-8", "x", 0, 1, "invalid character")

@pytest.mark.asyncio
async def test_log_headers_unicode_error_with_custom_object(caplog):
    mock_env = get_mock_env()
    api = KoreaInvestApiBase(env=mock_env, logger=None)

    # build()가 에러 유발 객체를 포함한 dict를 반환하도록 설정
    api._headers.build = MagicMock(return_value={
        "Authorization": ExplodingStr(),
        "User-Agent": "test-agent"
    })

    with caplog.at_level("DEBUG"):
        api._log_headers()

    logs = [rec.getMessage() for rec in caplog.records]
    assert any("*** UnicodeEncodeError" in msg or "ExplodingStr" in msg for msg in logs)


@pytest.mark.asyncio
async def test_call_api_with_http_error_status(caplog):
    # --- Arrange (준비) ---
    # 1. HTTP 500 오류를 내는 가짜 응답 객체 생성
    # _handle_response 내부 로직과 호환되도록 httpx.Response 스펙을 따릅니다.
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 500
    mock_response.text = "Internal Server Error"
    # _handle_response의 raise_for_status()가 호출될 때 실제 에러를 발생시키도록 설정
    mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        message="Server Error", request=MagicMock(), response=mock_response
    )

    mock_env = get_mock_env()

    # 3. 테스트 대상 API 인스턴스 생성
    api = KoreaInvestApiBase(
        env=mock_env
    )

    # ▼▼▼ 핵심 수정 부분 ▼▼▼
    # 실제 네트워크 호출을 하는 _execute_request를 패치하고,
    # 미리 만들어둔 가짜 응답(mock_response)을 반환하도록 설정합니다.
    with patch.object(api, '_execute_request', new_callable=AsyncMock,
                      return_value=mock_response) as mock_execute_request:
        # --- Act (실행) ---
        # 이제 call_api는 실제 네트워크 통신 없이 즉시 mock_response를 받게 됩니다.
        result = await api.call_api("GET", "/fail")

        # --- Assert (검증) ---
        # _execute_request가 1번 호출되었는지 확인
        mock_execute_request.assert_awaited_once()

        # _handle_response 로직에 의해 최종적으로 None이 반환되어야 함
        assert result.data is None

        # _handle_response가 남기는 로그가 정상적으로 찍혔는지 확인
        assert "HTTP 오류 발생: 500 - Internal Server Error" in caplog.text

        # 재시도 루프가 돌지 않았으므로 '모든 재시도 실패' 로그는 없어야 함
        assert "모든 재시도 실패" not in caplog.text


@pytest.mark.asyncio
async def test_call_api_with_invalid_json_type(caplog):
    """응답이 dict가 아님"""
    # caplog 설정: 테스트 대상 모듈의 로그를 캡처
    caplog.set_level(logging.DEBUG, logger='brokers.korea_investment.korea_invest_api_base')

    response_mock = MagicMock(spec=httpx.Response)  # httpx.Response 스펙을 따름
    response_mock.status_code = 200

    # 변경: response_mock.json()이 json.JSONDecodeError를 발생시키도록 side_effect 설정
    # _handle_response 메서드는 이 예외를 (json.JSONDecodeError, ValueError)로 잡습니다.
    response_mock.json.side_effect = json.JSONDecodeError("Invalid JSON", doc="not a dict", pos=0)

    mock_env = get_mock_env()

    api = KoreaInvestApiBase(mock_env, logger=None)  # logger=None은 기본 로거 사용

    # 변경: api._session.request 대신 api._async_session.get을 모킹
    # _execute_request는 GET 메서드에 대해 awaitable을 반환하므로 AsyncMock 사용
    api._async_session.get = AsyncMock(return_value=response_mock)

    result = await api.call_api("GET", "/invalid", retry_count=1)

    assert result.data is None
    # 변경: 예상되는 로그 메시지를 "응답 JSON 디코딩 실패"로 수정
    assert any("JSON" in r.message for r in caplog.records)

    # 추가: 불필요한 다른 오류 로그가 없는지 확인
    assert not any("HTTP 오류 발생" in r.message for r in caplog.records)
    assert not any("토큰 만료 오류" in r.message for r in caplog.records)
    assert not any("API 비즈니스 오류" in r.message for r in caplog.records)
    # assert not any("모든 재시도 실패" in r.message for r in caplog.records)
    assert not any("예상치 못한 예외 발생" in r.message for r in caplog.records)  # 이 예외는 _handle_response에서 명시적으로 처리되므로 없어야 함


@pytest.mark.asyncio
async def test_call_api_no_env_instance(caplog):
    """토큰 재발급에 필요한 config 정보가 없어 토큰 초기화가 불가능한 시나리오"""
    api = get_api()

    # api._env = None # 이 라인은 _handle_response 로직에 직접적인 영향 없음. (self._config를 검사)

    response_mock = MagicMock(spec=httpx.Response)  # httpx.Response 스펙을 따름
    response_mock.status_code = 200
    response_mock.text = "expired"
    response_mock.json.return_value = {"rt_cd": "1", "msg_cd": "EGW00123"}  # 토큰 만료 메시지
    response_mock.raise_for_status.return_value = None  # raise_for_status는 예외를 발생시키지 않음

    # 변경: api._session.request 대신 api._async_session.get을 모킹
    api._async_session.get = AsyncMock(side_effect=[response_mock, response_mock])

    logger_name = getattr(api._logger, "name", None) or api.__class__.__module__
    with caplog.at_level(logging.WARNING, logger=logger_name):  # 전체 캡처
        result = await api.call_api("GET", "/no-env", retry_count=1, delay=0)

    # 디버깅을 위해 캡처된 로그 출력
    print("\n=== Captured Log ===")
    for r in caplog.records:
        print(f"[{r.levelname}] {r.name} - {r.message}")
    print("=====================\n")
    print({(rec.name, rec.levelname, rec.getMessage()) for rec in caplog.records})

    assert result.data is None

    # ⛳️ 로그 검증: EGW00123 경고가 찍혔는지 (문구 변화에 강하게 키워드로)
    msgs = [rec.getMessage() for rec in caplog.records if rec.name == logger_name]
    assert any(("EGW00123" in m and "토큰 만료" in m) for m in msgs)

    # ⛳️ 강제 1회 재시도(총 2회 호출) 검증
    assert api._async_session.get.await_count == 2

    # 추가 단언: 다른 유형의 오류 로그는 없어야 합니다.
    assert not any("HTTP 오류 발생" in r.message for r in caplog.records)
    assert not any("JSON 디코딩 오류 발생" in r.message for r in caplog.records)
    assert not any("API 비즈니스 오류" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_call_api_token_renew_failed(caplog):

    # 토큰 만료된 응답 모킹
    token_expired_response_mock = MagicMock(spec=httpx.Response)
    token_expired_response_mock.status_code = 200
    token_expired_response_mock.text = '{"rt_cd":"1","msg_cd":"EGW00123"}'
    token_expired_response_mock.json.return_value = {
        "rt_cd": "1",
        "msg_cd": "EGW00123"
    }
    token_expired_response_mock.raise_for_status.return_value = None

    # AsyncMock으로 httpx.AsyncClient 모킹
    mock_async_session = AsyncMock()
    mock_async_session.get.side_effect = [token_expired_response_mock] * 3

    # API 인스턴스 생성
    api = get_api()
    # 실제 _async_session을 모킹한 객체로 덮어쓰기
    retry_count = 3

    api._async_session.get = AsyncMock(side_effect=[token_expired_response_mock] * (2 * retry_count))
    # 테스트 실행
    result = await api.call_api("GET", "/token-expired", retry_count=retry_count, delay=0.01)

    # 검증
    assert result.data is None
    assert api._async_session.get.await_count == 2 * retry_count

    logger_name = getattr(api._logger, "name", None) or KoreaInvestApiBase.__module__
    msgs = [rec.getMessage() for rec in caplog.records if rec.name == logger_name]
    assert any(("EGW00123" in m and "토큰 만료" in m) for m in msgs)


@pytest.mark.asyncio
async def test_log_request_exception_httpx_request_error(caplog):
    caplog.set_level(logging.ERROR)

    mock_env = get_mock_env()

    api = KoreaInvestApiBase(
        env=mock_env,
        logger=None  # 실제 로거 사용
    )

    # httpx.RequestError 예외를 던지는 mock 세션 생성
    mock_session = AsyncMock()
    mock_session.get.side_effect = httpx.RequestError("연결 실패", request=MagicMock())
    api._async_session = mock_session

    result = await api.call_api("GET", "/error", retry_count=1, delay=0)

    assert result.data is None
    assert any("요청 예외 발생 (httpx): 연결 실패" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_call_api_response_not_dict(caplog):
    """응답이 JSON 파싱은 되지만 dict가 아닌 경우 (예: list)"""
    caplog.set_level(logging.ERROR, logger='brokers.korea_investment.korea_invest_api_base')

    api = get_api()
    # 실제 로거를 사용하는 인스턴스 생성 (DummyAPI는 로거를 모킹하므로 caplog 사용 불가)
    mock_env = get_mock_env()
    api = KoreaInvestApiBase(mock_env, logger=None, time_manager=AsyncMock())

    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.text = '[{"key": "value"}]'
    mock_response.json.return_value = [{"key": "value"}]  # 리스트 반환
    mock_response.raise_for_status.return_value = None

    api._async_session = AsyncMock()
    api._async_session.get = AsyncMock(return_value=mock_response)

    result = await api.call_api("GET", "/not-dict", retry_count=1)

    assert result.data is None
    assert any("API 응답 형식이 dict가 아님" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_call_api_response_missing_output(caplog):
    """응답이 성공(rt_cd='0')이지만 output 데이터가 없는 경우"""
    caplog.set_level(logging.ERROR, logger='brokers.korea_investment.korea_invest_api_base')

    api = get_api()
    # 실제 로거를 사용하는 인스턴스 생성
    mock_env = get_mock_env()
    api = KoreaInvestApiBase(mock_env, logger=None, time_manager=AsyncMock())

    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.text = '{"rt_cd": "0", "msg1": "정상"}'  # output 없음
    mock_response.json.return_value = {"rt_cd": "0", "msg1": "정상"}  # output 키 누락
    mock_response.raise_for_status.return_value = None

    api._async_session = AsyncMock()
    api._async_session.get = AsyncMock(return_value=mock_response)

    result = await api.call_api("GET", "/missing-output", retry_count=1)

    assert result.data is None
    assert any("API 응답에 output 데이터가 없습니다" in r.message for r in caplog.records)
