import os
import json
import httpx  # 비동기 HTTP 클라이언트
from datetime import datetime, timedelta
import logging


class TokenManager:
    """
    한국투자증권 API의 액세스 토큰 관리를 전담하는 클래스.
    - 토큰을 파일에 저장하여 영속성을 보장합니다.
    - 토큰의 유효성을 검사하고, 만료 시 자동으로 재발급합니다.
    """

    def __init__(self, config, token_file_path='config/token.json'):
        self.config = config
        self.token_file_path = token_file_path
        self._access_token = None
        self._token_expired_at = None
        self.logger = logging.getLogger(__name__)

    async def get_access_token(self):
        """유효한 액세스 토큰을 반환합니다. 필요 시 파일에서 로드하거나 새로 발급합니다."""
        # 1. 메모리에 토큰이 있고 유효한지 먼저 확인
        if self._access_token and self._is_token_valid():
            return self._access_token

        # 2. 파일에서 토큰을 로드하고 유효한지 확인
        self._load_token_from_file()
        if self._access_token and self._is_token_valid():
            self.logger.info("파일에서 유효한 토큰을 로드했습니다.")
            return self._access_token

        # 3. 위 모든 경우에 해당하지 않으면 새로 발급
        self.logger.info("새로운 액세스 토큰을 발급합니다.")
        await self._issue_new_token()
        return self._access_token

    def _is_token_valid(self):
        """토큰이 존재하고, 만료되지 않았는지 확인합니다. (5분 여유시간)"""
        if not self._access_token or not self._token_expired_at:
            return False
        # 만료 시간 5분 전에 갱신하도록 여유를 둡니다.
        return datetime.now() < self._token_expired_at - timedelta(minutes=5)

    def _load_token_from_file(self):
        """파일에서 토큰 정보를 로드합니다."""
        try:
            with open(self.token_file_path, 'r') as f:
                token_data = json.load(f)
                self._access_token = token_data.get('access_token')
                expiry_str = token_data.get('expired_at')
                if expiry_str:
                    self._token_expired_at = datetime.fromisoformat(expiry_str)
        except (FileNotFoundError, json.JSONDecodeError):
            self.logger.warning("토큰 파일을 찾을 수 없거나 형식이 잘못되었습니다.")
            self._access_token = None
            self._token_expired_at = None

    def _save_token_to_file(self):
        """현재 토큰 정보를 파일에 저장합니다."""
        token_data = {
            'access_token': self._access_token,
            'expired_at': self._token_expired_at.isoformat()
        }
        os.makedirs(os.path.dirname(self.token_file_path), exist_ok=True)
        with open(self.token_file_path, 'w') as f:
            json.dump(token_data, f, indent=4)
        self.logger.info("새 토큰을 파일에 저장했습니다.")

    async def _issue_new_token(self):
        """API 서버에 요청하여 새로운 토큰을 발급받고, 상태를 업데이트합니다."""
        url = f"{self.config.base_url}/oauth2/tokenP"
        body = {
            "grant_type": "client_credentials",
            "appkey": self.config.app_key,
            "appsecret": self.config.app_secret
        }
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=body)
            response.raise_for_status()
            res_data = response.json()

            self._access_token = res_data.get('access_token')
            expires_in = int(res_data.get('expires_in', 0))
            self._token_expired_at = datetime.now() + timedelta(seconds=expires_in)

            self._save_token_to_file()

    def invalidate_token(self):
        """외부에서 토큰 만료를 감지했을 때, 현재 토큰을 강제로 무효화합니다."""
        self._access_token = None
        self._token_expired_at = None
        if os.path.exists(self.token_file_path):
            os.remove(self.token_file_path)
        self.logger.info("저장된 토큰이 무효화되었습니다.")