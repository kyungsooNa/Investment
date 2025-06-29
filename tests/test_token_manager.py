import unittest
from unittest.mock import patch, mock_open, AsyncMock, MagicMock
import json
from datetime import datetime, timedelta
import os

# 테스트 대상 클래스를 import 합니다.
from brokers.korea_investment.korea_invest_token_manager import TokenManager


class TestTokenManager(unittest.IsolatedAsyncioTestCase):
    """
    TokenManager 클래스의 모든 기능을 검증하는 테스트 스위트입니다.
    - 파일 I/O와 HTTP 요청은 모두 Mock(가짜) 객체로 대체하여 테스트합니다.
    """

    def setUp(self):
        """각 테스트 케이스 실행 전에 공통적으로 필요한 설정을 초기화합니다."""
        # 설정(config) 객체를 Mock으로 생성합니다.
        self.mock_config = MagicMock()
        self.mock_config.base_url = "https://test.api.com"
        self.mock_config.app_key = "test_key"
        self.mock_config.app_secret = "test_secret"

        self.token_file_path = 'config/test_token.json'

        # TokenManager 인스턴스를 생성합니다.
        self.token_manager = TokenManager(
            config=self.mock_config,
            token_file_path=self.token_file_path
        )
        # logger를 MagicMock으로 교체 (테스트 간 공통 적용)
        self.token_manager.logger = MagicMock()


    async def test_issue_new_token_success(self):
        """
        TC-1: 토큰 파일이 없을 때, 새로운 토큰이 성공적으로 발급되고 파일에 저장되는지 검증합니다.
        """
        # --- Arrange (준비) ---
        # 파일 시스템 관련 함수들을 Mocking합니다. 파일이 존재하지 않는 상황을 가정합니다.
        with patch('os.path.exists', return_value=False), \
                patch('builtins.open', mock_open()), \
                patch('httpx.AsyncClient') as MockAsyncClient, \
                patch('json.dump') as mock_json_dump:  # ✅ 추가
            # httpx 클라이언트의 POST 요청이 성공적인 토큰 응답을 반환하도록 설정합니다.
            mock_response = MagicMock()
            mock_response.json.return_value = {
                'access_token': 'new_issued_token_123',
                'expires_in': 86400  # 24시간
            }
            # AsyncMock을 사용하여 비동기 context manager(__aenter__)를 모킹합니다.
            mock_client_instance = AsyncMock()
            mock_client_instance.post.return_value = mock_response
            MockAsyncClient.return_value.__aenter__.return_value = mock_client_instance

            # --- Act (실행) ---
            access_token = await self.token_manager.get_access_token()

            # --- Assert (검증) ---
            # 1. 반환된 토큰이 예상과 일치하는지 확인합니다.
            self.assertEqual(access_token, 'new_issued_token_123')

            dumped_data = mock_json_dump.call_args[0][0]
            self.assertEqual(dumped_data['access_token'], 'new_issued_token_123')
            self.assertIn('expired_at', dumped_data)

    async def test_load_valid_token_from_file_success(self):
        """
        TC-2: 유효한 토큰이 담긴 파일이 존재할 때, 이를 성공적으로 읽어와 사용하는지 검증합니다.
        """
        # --- Arrange (준비) ---
        # 미래의 만료 시간을 설정합니다.
        future_expiry = datetime.now() + timedelta(hours=12)
        valid_token_data = json.dumps({
            'access_token': 'valid_token_from_file',
            'expired_at': future_expiry.isoformat()
        })

        # 파일이 존재하고, 읽기 모드로 열 때 위에서 정의한 데이터를 반환하도록 설정합니다.
        with patch('os.path.exists', return_value=True), \
                patch('builtins.open', mock_open(read_data=valid_token_data)) as mocked_file:
            # --- Act (실행) ---
            access_token = await self.token_manager.get_access_token()

            # --- Assert (검증) ---
            # 1. 파일에서 읽어온 토큰이 정확히 반환되었는지 확인합니다.
            self.assertEqual(access_token, 'valid_token_from_file')

            # 2. 파일이 읽기 모드('r')로 열렸는지 확인합니다.
            mocked_file.assert_called_once_with(self.token_file_path, 'r')

    async def test_reissue_token_when_file_token_is_expired(self):
        """
        TC-3: 파일에 저장된 토큰이 만료되었을 때, 새로 토큰을 발급받는지 검증합니다.
        """
        # --- Arrange ---
        past_expiry = datetime.now() - timedelta(hours=1)
        expired_token_data = json.dumps({
            'access_token': 'expired_token',
            'expired_at': past_expiry.isoformat()
        })

        with patch('os.path.exists', return_value=True), \
             patch('builtins.open', mock_open(read_data=expired_token_data)) as mocked_file, \
             patch('httpx.AsyncClient') as MockAsyncClient:

            mock_response = MagicMock()
            mock_response.json.return_value = {
                'access_token': 'reissued_fresh_token',
                'expires_in': 86400
            }

            mock_client_instance = AsyncMock()
            mock_client_instance.post.return_value = mock_response
            MockAsyncClient.return_value.__aenter__.return_value = mock_client_instance

            # --- Act ---
            access_token = await self.token_manager.get_access_token()

            # --- Assert ---
            self.assertEqual(access_token, 'reissued_fresh_token')
            self.token_manager.logger.info.assert_any_call("새로운 액세스 토큰을 발급합니다.")
            self.assertEqual(mocked_file.call_count, 2)  # read + write


    async def test_get_token_from_memory_cache(self):
        """
        TC-4: 한번 토큰을 가져온 뒤 다시 요청하면, 파일이나 API 접근 없이 메모리에서 바로 반환되는지 검증합니다.
        """
        print("DEBUG HERE")  # ← 여기에 break

        # --- Arrange (준비) ---
        # 메모리에 유효한 토큰이 저장된 상황을 가정합니다.
        self.token_manager._access_token = 'token_in_memory'
        self.token_manager._token_expired_at = datetime.now() + timedelta(days=1)

        # 파일 시스템과 API 클라이언트를 모두 감시 대상으로 설정합니다.
        with patch('builtins.open') as mock_open_func, \
                patch('httpx.AsyncClient') as MockAsyncClient:
            # --- Act (실행) ---
            access_token = await self.token_manager.get_access_token()

            # --- Assert (검증) ---
            # 1. 메모리에 있던 토큰이 반환되었는지 확인합니다.
            self.assertEqual(access_token, 'token_in_memory')

            # 2. 파일 I/O나 API 호출이 전혀 발생하지 않았는지 확인합니다.
            mock_open_func.assert_not_called()
            MockAsyncClient.assert_not_called()

    async def test_invalidate_token_and_reissue(self):
        """
        TC-5: invalidate_token() 호출 시 토큰 파일이 삭제되고, 이후 새 토큰이 발급되는지 검증합니다.
        """
        # --- Arrange (준비) ---
        # 파일이 존재하고, 삭제 가능하며, API는 새 토큰을 발급할 준비가 된 상황을 설정합니다.
        with patch('os.path.exists', return_value=True), \
                patch('os.remove') as mock_remove, \
                patch('httpx.AsyncClient') as MockAsyncClient:
            mock_response = MagicMock()
            mock_response.json.return_value = {'access_token': 'after_invalidate_token', 'expires_in': 86400}
            mock_client_instance = AsyncMock()
            mock_client_instance.post.return_value = mock_response
            MockAsyncClient.return_value.__aenter__.return_value = mock_client_instance

            # --- Act 1 (무효화) ---
            self.token_manager.invalidate_token()

            # --- Assert 1 (삭제 검증) ---
            # 1. os.remove가 올바른 파일 경로로 호출되었는지 확인합니다.
            mock_remove.assert_called_once_with(self.token_file_path)

            # --- Act 2 (재발급) ---
            # with 블록을 새로 만들어 open을 모킹합니다.
            with patch('builtins.open', mock_open()) as mocked_file:
                access_token = await self.token_manager.get_access_token()

            # --- Assert 2 (재발급 검증) ---
            # 2. 무효화 이후 새로 발급받은 토큰이 맞는지 확인합니다.
            self.assertEqual(access_token, 'after_invalidate_token')

