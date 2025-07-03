# tests/test_token_manager.py
import unittest
import os
import json
import pytest
from datetime import datetime, timedelta
from unittest.mock import patch, AsyncMock, MagicMock
from brokers.korea_investment.korea_invest_token_manager import TokenManager
import pytz  # pytz 임포트
import shutil # shutil 임포트


class TestTokenManager(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # 테스트 환경을 위한 임시 토큰 파일 경로 설정
        self.temp_dir = 'temp_config_test' # 충돌 방지를 위해 이름 변경
        self.test_token_file = os.path.join(self.temp_dir, 'test_token.json')

        # 테스트 시작 시 임시 디렉토리가 존재하지 않으면 생성
        os.makedirs(self.temp_dir, exist_ok=True) # setUp에서 디렉토리 생성

        # TokenManager 초기화 시 'config' 인자 제거
        self.token_manager = TokenManager(token_file_path=self.test_token_file)
        self.kst_timezone = pytz.timezone('Asia/Seoul')

        # Mock config data (테스트에 필요한 최소한의 정보만 제공)
        self.mock_config = {
            'app_key': 'test_app_key',
            'app_secret': 'test_app_secret',
            'url': 'https://test-api.koreainvestment.com:9443',
            'paper_url': 'https://test-api.koreainvestment.com:9443'  # 모의투자 URL도 필요
        }

        # Test Env (TokenManager에 필요한 env 정보를 제공하기 위함)
        self.mock_env = MagicMock()
        self.mock_env.get_full_config.return_value = {
            'base_url': self.mock_config['url'],
            'api_key': self.mock_config['app_key'],
            'api_secret_key': self.mock_config['app_secret']
        }
        self.mock_env.base_url = self.mock_config['url']  # _get_token_base_url_from_file 비교를 위해 추가

    def tearDown(self):
        # 테스트 후 생성된 임시 토큰 파일 삭제
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    async def test_init(self):
        """
        TC-1: TokenManager 초기화 검증
        """
        self.assertIsNone(self.token_manager._access_token)
        self.assertIsNone(self.token_manager._token_expired_at)
        self.assertEqual(self.token_manager.token_file_path, self.test_token_file)

    async def test_issue_new_token_success(self):
        """
        TC-2: 새 토큰 발급 성공 검증 (API 호출 및 파일 저장)
        """
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'access_token': 'new_test_token',
            'expires_in': 86400  # 24시간
        }
        mock_response.raise_for_status.return_value = None  # ensure no exception raised

        with patch('httpx.AsyncClient') as MockAsyncClient:
            MockAsyncClient.return_value.__aenter__.return_value.post.return_value = mock_response

            # _issue_new_token은 이제 base_url, app_key, app_secret을 직접 받습니다.
            await self.token_manager._issue_new_token(
                base_url=self.mock_config['url'],
                app_key=self.mock_config['app_key'],
                app_secret=self.mock_config['app_secret']
            )

            MockAsyncClient.return_value.__aenter__.return_value.post.assert_called_once()
            self.assertEqual(self.token_manager._access_token, 'new_test_token')
            self.assertIsNotNone(self.token_manager._token_expired_at)
            self.assertTrue(os.path.exists(self.test_token_file))

            # 저장된 파일 내용 검증
            with open(self.test_token_file, 'r') as f:
                token_data = json.load(f)
                self.assertEqual(token_data['access_token'], 'new_test_token')
                self.assertIn('expired_at', token_data)
                self.assertEqual(token_data['base_url'], self.mock_config['url'])  # base_url 저장 확인

    async def test_get_token_from_file_valid(self):
        """
        TC-3: 파일에 유효한 토큰이 있을 때, 파일에서 로드되는지 검증
        """
        # --- Arrange (준비) ---
        # 유효한 토큰 파일을 미리 생성합니다.
        future_expiry = (datetime.now(self.kst_timezone) + timedelta(days=1)).isoformat()
        initial_token_data = {
            'access_token': 'token_from_file',
            'expired_at': future_expiry,
            'base_url': self.mock_config['url']  # 파일에 base_url 저장
        }
        with open(self.test_token_file, 'w') as f:
            json.dump(initial_token_data, f)

        # API 호출이 일어나지 않도록 Mocking
        with patch('httpx.AsyncClient') as MockAsyncClient:
            # --- Act (실행) ---
            # get_access_token은 이제 base_url, app_key, app_secret을 직접 받습니다.
            access_token = await self.token_manager.get_access_token(
                base_url=self.mock_config['url'],
                app_key=self.mock_config['app_key'],
                app_secret=self.mock_config['app_secret']
            )

            # --- Assert (검증) ---
            self.assertEqual(access_token, 'token_from_file')
            MockAsyncClient.assert_not_called()  # API 호출 없었는지 확인

    async def test_get_token_from_memory_cache(self):
        """
        TC-4: 한번 토큰을 가져온 뒤 다시 요청하면, 파일이나 API 접근 없이 메모리에서 바로 반환되는지 검증합니다.
        """
        print("DEBUG HERE")  # ← 여기에 break

        # --- Arrange (준비) ---
        # 메모리에 유효한 토큰이 저장된 상황을 가정합니다.
        self.token_manager._access_token = 'token_in_memory'
        self.token_manager._token_expired_at = datetime.now(self.kst_timezone) + timedelta(
            days=1)  # aware datetime으로 변경

        # 파일 시스템과 API 클라이언트를 모두 감시 대상으로 설정합니다.
        with patch('builtins.open') as mock_open_func, \
                patch('httpx.AsyncClient') as MockAsyncClient:
            # --- Act (실행) ---
            # get_access_token은 이제 base_url, app_key, app_secret을 직접 받습니다.
            access_token = await self.token_manager.get_access_token(
                base_url=self.mock_config['url'],
                app_key=self.mock_config['app_key'],
                app_secret=self.mock_config['app_secret']
            )

            # --- Assert (검증) ---
            # 1. 메모리에 있던 토큰이 반환되었는지 확인합니다.
            self.assertEqual(access_token, 'token_in_memory')

            # 2. 파일 I/O나 API 호출이 전혀 발생하지 않았는지 확인합니다.
            mock_open_func.assert_not_called()
            MockAsyncClient.assert_not_called()

    async def test_get_token_from_file_expired_then_new_issued(self):
        expired_time = (datetime.now(self.kst_timezone) - timedelta(days=1)).isoformat()
        initial_token_data = {
            'access_token': 'expired_token',
            'expired_at': expired_time,
            'base_url': self.mock_config['url']
        }
        with open(self.test_token_file, 'w') as f:
            json.dump(initial_token_data, f)

        mock_new_token_response = AsyncMock()
        mock_new_token_response.status_code = 200
        mock_new_token_response.json.return_value = {
            'access_token': 'fresh_new_token',
            'expires_in': 3600
        }
        mock_new_token_response.raise_for_status.return_value = None

        with patch('httpx.AsyncClient') as MockAsyncClient:
            MockAsyncClient.return_value.__aenter__.return_value.post.return_value = mock_new_token_response
            with patch('builtins.open', new_callable=MagicMock) as mock_open_func:
                def mock_open_side_effect(file, mode='r', **kwargs):
                    if file == self.test_token_file and 'r' in mode:
                        mock_file = MagicMock()
                        mock_file.__enter__.return_value = MagicMock()
                        mock_file.__enter__.return_value.read.return_value = json.dumps(initial_token_data)
                        return mock_file
                    # 쓰기 모드 ('w')일 경우, 컨텍스트 관리자로 동작할 수 있는 MagicMock 인스턴스 반환
                    mock_write_file = MagicMock()
                    mock_write_file.__enter__.return_value = mock_write_file  # __enter__가 자기 자신을 반환
                    return mock_write_file  # 이 mock 객체가 컨텍스트 관리자로 사용됨

                mock_open_func.side_effect = mock_open_side_effect

                access_token = await self.token_manager.get_access_token(
                    base_url=self.mock_config['url'],
                    app_key=self.mock_config['app_key'],
                    app_secret=self.mock_config['app_secret']
                )

                self.assertEqual(access_token, 'fresh_new_token')
                MockAsyncClient.return_value.__aenter__.return_value.post.assert_called_once()

                self.assertEqual(self.token_manager._access_token, 'fresh_new_token')
                self.assertIsNotNone(self.token_manager._token_expired_at)

    async def test_invalidate_token(self):
        """
        TC-6: 토큰 무효화 기능 검증
        """
        # --- Arrange (준비) ---
        # 토큰을 메모리와 파일에 설정
        self.token_manager._access_token = 'valid_token'
        self.token_manager._token_expired_at = datetime.now() + timedelta(hours=1)
        with open(self.test_token_file, 'w') as f:
            json.dump({'access_token': 'valid_token', 'expired_at': (datetime.now() + timedelta(hours=1)).isoformat(),
                       'base_url': self.mock_config['url']}, f)

        # --- Act (실행) ---
        self.token_manager.invalidate_token()

        # --- Assert (검증) ---
        self.assertIsNone(self.token_manager._access_token)
        self.assertIsNone(self.token_manager._token_expired_at)
        self.assertFalse(os.path.exists(self.test_token_file))  # 파일도 삭제되었는지 확인

    async def test_get_token_from_file_base_url_mismatch(self):
        """
        TC-7: 파일에 유효한 토큰이 있지만 base_url이 현재 환경과 다를 때, 새 토큰이 발급되는지 검증
        """
        # --- Arrange (준비) ---
        # 다른 base_url로 저장된 유효한 토큰 파일을 미리 생성합니다.
        future_expiry = (datetime.now(self.kst_timezone) + timedelta(days=1)).isoformat()
        initial_token_data = {
            'access_token': 'token_from_wrong_env_file',
            'expired_at': future_expiry,
            'base_url': 'https://wrong-url.com'  # 다른 base_url
        }
        with open(self.test_token_file, 'w') as f:
            json.dump(initial_token_data, f)

        # 새 토큰 발급을 위한 API 응답 Mocking
        mock_new_token_response = AsyncMock()
        mock_new_token_response.status_code = 200
        mock_new_token_response.json.return_value = {
            'access_token': 'fresh_new_token_for_correct_env',
            'expires_in': 3600
        }
        mock_new_token_response.raise_for_status.return_value = None

        with patch('httpx.AsyncClient') as MockAsyncClient:
            MockAsyncClient.return_value.__aenter__.return_value.post.return_value = mock_new_token_response

            # --- Act (실행) ---
            # 현재 환경의 base_url로 요청 (파일의 base_url과 다름)
            access_token = await self.token_manager.get_access_token(
                base_url=self.mock_config['url'],  # 올바른 base_url
                app_key=self.mock_config['app_key'],
                app_secret=self.mock_config['app_secret']
            )

            # --- Assert (검증) ---
            self.assertEqual(access_token, 'fresh_new_token_for_correct_env')  # 새 토큰이 발급되어야 함
            MockAsyncClient.return_value.__aenter__.return_value.post.assert_called_once()  # API 호출 있었는지 확인

            # 새 토큰이 올바른 base_url로 저장되었는지 확인
            with open(self.test_token_file, 'r') as f:
                token_data = json.load(f)
                self.assertEqual(token_data['base_url'], self.mock_config['url'])
                self.assertEqual(token_data['access_token'], 'fresh_new_token_for_correct_env')

# --- TestTokenManager 클래스 밖으로 완전히 분리된 테스트 함수 ---
@pytest.mark.asyncio
async def test_get_token_no_file_new_issued_isolated(tmp_path):  # 이제 self 인수는 없습니다.
    """
    TC-8: 토큰 파일이 없을 때, 새 토큰이 발급되고 파일에 저장되는지 검증
    (완전히 격리된 버전)
    """
    # --- Arrange (준비) ---
    # 각 테스트마다 고유한 임시 파일 경로를 설정합니다.
    unique_token_file = tmp_path / "unique_test_token.json"

    # 함수형 테스트이므로, 필요한 설정은 여기서 직접 정의합니다.
    mock_config = {
        'url': 'https://test-api.koreainvestment.com:9443',
        'app_key': 'test_app_key',
        'app_secret': 'test_app_secret'
    }

    # TokenManager 인스턴스 생성 시 고유한 파일 경로 전달
    token_manager = TokenManager(token_file_path=unique_token_file)

    # 토큰 파일이 없음을 확인 (tmp_path는 기본적으로 비어있으므로 보통 필요 없음)
    if os.path.exists(unique_token_file):
        os.remove(unique_token_file)

    # 새 토큰 발급을 위한 API 응답 Mocking
    mock_new_token_response = AsyncMock()
    mock_new_token_response.status_code = 200
    mock_new_token_response.json.return_value = {
        'access_token': 'token_no_file_new_issued',
        'expires_in': 3600
    }
    mock_new_token_response.raise_for_status.return_value = None

    with patch('httpx.AsyncClient') as MockAsyncClient:
        MockAsyncClient.return_value.__aenter__.return_value.post.return_value = mock_new_token_response

        # --- Act (실행) ---
        access_token = await token_manager.get_access_token(
            base_url=mock_config['url'],
            app_key=mock_config['app_key'],
            app_secret=mock_config['app_secret']
        )

        # --- Assert (검증) ---
        assert access_token == 'token_no_file_new_issued'
        MockAsyncClient.return_value.__aenter__.return_value.post.assert_called_once()
        assert os.path.exists(unique_token_file)

        with open(unique_token_file, 'r') as f:
            token_data = json.load(f)
            assert token_data['access_token'] == 'token_no_file_new_issued'
            assert token_data['base_url'] == mock_config['url']