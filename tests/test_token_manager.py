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
import tempfile # tempfile 모듈 임포트

class TestTokenManager(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # 각 테스트 실행마다 고유한 임시 디렉토리 생성
        self.temp_dir = tempfile.mkdtemp()  # unique temp directory for each test run
        self.test_token_file = os.path.join(self.temp_dir, 'test_token.json')

        self.token_manager = TokenManager(token_file_path=self.test_token_file)
        self.kst_timezone = pytz.timezone('Asia/Seoul')

        self.mock_config = {
            'app_key': 'test_app_key',
            'app_secret': 'test_app_secret',
            'url': 'https://test-api.koreainvestment.com:9443',
            'paper_url': 'https://test-api.koreainvestment.com:9443'
        }

        self.mock_env = MagicMock()
        self.mock_env.get_full_config.return_value = {
            'base_url': self.mock_config['url'],
            'api_key': self.mock_config['app_key'],
            'api_secret_key': self.mock_config['app_secret']
        }
        self.mock_env.base_url = self.mock_config['url']

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
        mock_response.json = MagicMock(return_value={
            'access_token': 'new_test_token',
            'expires_in': 86400  # 24시간
        })
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
        mock_new_token_response.json = MagicMock(return_value={
            'access_token': 'fresh_new_token',
            'expires_in': 3600
        })
        mock_new_token_response.raise_for_status = MagicMock(return_value=None)

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
        mock_new_token_response.json = MagicMock(return_value={
            'access_token': 'fresh_new_token_for_correct_env',
            'expires_in': 3600
        })
        # 경고 제거를 위한 핵심 수정:
        # raise_for_status는 동기 메서드이므로 MagicMock으로 명시적으로 모킹합니다.
        # 이렇게 하면 AsyncMockMixin이 불필요하게 코루틴을 생성하지 않습니다.
        mock_new_token_response.raise_for_status = MagicMock(return_value=None)
        # 만약 raise_for_status가 에러를 발생시키는 경우를 테스트한다면:
        # mock_new_token_response.raise_for_status = MagicMock(side_effect=httpx.HTTPStatusError("Test Error", request=httpx.Request("GET", "http://example.com"), response=mock_new_token_response))

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
    mock_new_token_response = AsyncMock()  # 응답 객체 자체는 AsyncMock으로 유지
    mock_new_token_response.status_code = 200
    mock_new_token_response.raise_for_status = MagicMock(return_value=None)

    # response.json() 메서드가 await 없이 딕셔너리를 직접 반환하도록 설정
    mock_new_token_response.json = MagicMock(return_value={  # <<< 이 부분을 수정했습니다.
        'access_token': 'token_no_file_new_issued',
        'expires_in': 3600
    })

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


@pytest.mark.asyncio
async def test_is_token_valid_when_token_is_none():
    """
    _is_token_valid: _access_token이 None일 때 False 반환 검증.
    """
    token_manager = TokenManager() # 초기화 시 _access_token과 _token_expired_at은 None
    assert token_manager._is_token_valid() is False

@pytest.mark.asyncio
async def test_is_token_valid_when_expiry_is_none():
    """
    _is_token_valid: _token_expired_at이 None일 때 False 반환 검증.
    """
    token_manager = TokenManager()
    token_manager._access_token = "some_token" # access_token만 설정
    assert token_manager._is_token_valid() is False

@pytest.mark.asyncio
async def test_get_token_base_url_from_file_not_found(tmp_path):
    """
    _get_token_base_url_from_file: 토큰 파일이 없을 때 None 반환 검증.
    """
    non_existent_file = tmp_path / "non_existent_token.json"
    token_manager = TokenManager(token_file_path=str(non_existent_file))
    assert token_manager._get_token_base_url_from_file() is None

@pytest.mark.asyncio
async def test_get_token_base_url_from_file_invalid_json(tmp_path):
    """
    _get_token_base_url_from_file: 토큰 파일이 있지만 JSON 형식이 아닐 때 None 반환 검증.
    """
    invalid_json_file = tmp_path / "invalid_token.json"
    with open(invalid_json_file, 'w') as f:
        f.write("this is not json")
    token_manager = TokenManager(token_file_path=str(invalid_json_file))
    assert token_manager._get_token_base_url_from_file() is None

@pytest.mark.asyncio
async def test_issue_new_token_missing_base_url_raises_error():
    """
    _issue_new_token: base_url이 누락되었을 때 ValueError 발생 검증.
    """
    token_manager = TokenManager()
    with pytest.raises(ValueError, match="Missing environment configuration for token issuance."):
        await token_manager._issue_new_token(base_url="", app_key="key", app_secret="secret")

@pytest.mark.asyncio
async def test_issue_new_token_missing_app_key_raises_error():
    """
    _issue_new_token: app_key가 누락되었을 때 ValueError 발생 검증.
    """
    token_manager = TokenManager()
    with pytest.raises(ValueError, match="Missing environment configuration for token issuance."):
        await token_manager._issue_new_token(base_url="url", app_key="", app_secret="secret")

@pytest.mark.asyncio
async def test_issue_new_token_missing_app_secret_raises_error():
    """
    _issue_new_token: app_secret이 누락되었을 때 ValueError 발생 검증.
    """
    token_manager = TokenManager()
    with pytest.raises(ValueError, match="Missing environment configuration for token issuance."):
        await token_manager._issue_new_token(base_url="url", app_key="key", app_secret="")

@pytest.mark.asyncio
async def test_invalidate_token_when_file_not_exists(tmp_path):
    """
    invalidate_token: 토큰 파일이 존재하지 않을 때 호출 시, 파일 삭제 시도 없이 토큰만 초기화되는지 검증.
    """
    non_existent_file = tmp_path / "non_existent_token.json"
    token_manager = TokenManager(token_file_path=str(non_existent_file))

    # 토큰이 메모리에 있는 것처럼 설정
    token_manager._access_token = 'token_to_invalidate'
    token_manager._token_expired_at = datetime.now() + timedelta(hours=1)

    # os.remove가 호출되지 않는지 확인하기 위해 patch
    with patch('os.remove') as mock_os_remove:
        token_manager.invalidate_token()

        assert token_manager._access_token is None
        assert token_manager._token_expired_at is None
        mock_os_remove.assert_not_called()  # 파일이 없으므로 os.remove 호출되면 안 됨

@pytest.mark.asyncio
async def test_load_token_from_file_no_expiry_str(tmp_path):
    """
    _load_token_from_file: expired_at 필드가 없는 토큰 파일을 로드할 때,
    _token_expired_at이 None으로 유지되는지 검증하여 라인 62 분기점 커버.
    """
    token_file = tmp_path / "token_no_expiry.json"
    initial_token_data = {
        'access_token': 'token_without_expiry_field',
        # 'expired_at' 필드를 의도적으로 제외
        'base_url': 'https://test-url.com'
    }
    with open(token_file, 'w') as f:
        json.dump(initial_token_data, f)

    token_manager = TokenManager(token_file_path=str(token_file))

    # 로드하기 전에 _token_expired_at이 None인지 확인 (초기 상태)
    assert token_manager._token_expired_at is None

    token_manager._load_token_from_file()

    assert token_manager._access_token == 'token_without_expiry_field'
    assert token_manager._token_expired_at is None  # expired_at이 없으므로 None으로 유지되어야 함
