# tests/test_korea_invest_api_base.py
# test_korea_invest_api_base.py (수정된 setUp)

import unittest
from unittest.mock import MagicMock, AsyncMock, patch
import requests # requests.exceptions 사용을 위해 추가
import asyncio
import httpx

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
    async def test_handle_token_expiration_retry_exceeded(self):
        """
        TC: _handle_token_expiration 메서드에서 재시도 횟수를 초과했을 때
            (attempt >= retry_count) 에러 로깅 후 None을 반환하는지 테스트합니다.
        이는 brokers/korea_investment/korea_invest_api_base.py의 165, 166번 라인을 커버합니다.
        """
        # Given:
        mock_response_json = {"msg_cd": "EGW00123"}
        attempt = 3
        retry_count = 3  # attempt == retry_count 이므로 재시도 초과

        with patch('asyncio.sleep', new_callable=AsyncMock) as mock_asyncio_sleep:
            result = await self.api_base._handle_token_expiration(
                mock_response_json, attempt, retry_count, delay=1
            )

            self.mock_token_manager.invalidate_token.assert_called_once()

            # 📌 수정된 부분: assert_called_once_with 대신 assert_called_with를 사용합니다.
            #    혹은 mock_logger.error.call_args_list[-1]을 사용하여 마지막 호출을 검증할 수도 있습니다.
            self.mock_logger.error.assert_called_with("토큰 재발급 후에도 실패, 종료")  # 165번 라인 커버

            # self.mock_logger.error가 총 2번 호출되었는지 검증 (옵션)
            self.assertEqual(self.mock_logger.error.call_count, 2)

            self.assertIsNone(result)  # 166번 라인 커버
            mock_asyncio_sleep.assert_not_awaited()