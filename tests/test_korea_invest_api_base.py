# tests/test_korea_invest_api_base.py
# test_korea_invest_api_base.py (수정된 setUp)

import unittest
from unittest.mock import MagicMock, AsyncMock, patch

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

        self.api_base = KoreaInvestApiBase(
            base_url="https://test.api.com",
            headers={"content-type": "application/json"},
            config=self.mock_config,
            logger=self.mock_logger
        )

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
            #    _env 인스턴스의 access_token과 만료 시간이 None으로 설정되었는지 확인합니다.
            self.assertIsNone(self.mock_env.access_token)
            self.assertIsNone(self.mock_env.token_expired_at)

            # 4. 로그 호출 검증: 토큰 만료 및 재시도 관련 로그가 올바르게 기록되었는지 확인합니다.
            self.mock_logger.error.assert_any_call("토큰 만료 오류 감지. 다음 요청 시 토큰을 재발급합니다.")
            self.mock_logger.info.assert_any_call("토큰 재발급 후 API 호출을 재시도합니다.")

