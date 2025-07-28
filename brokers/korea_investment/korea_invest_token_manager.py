import os
import json
import httpx  # 비동기 HTTP 클라이언트
from datetime import datetime, timedelta
import logging
import pytz # pytz 임포트
from typing import Optional


class TokenManager:
    """
    한국투자증권 API의 액세스 토큰 관리를 전담하는 클래스.
    - 토큰을 파일에 저장하여 영속성을 보장합니다.
    - 토큰의 유효성을 검사하고, 만료 시 자동으로 재발급합니다.
    """

    def __init__(self, token_file_path: Optional[str] = None, logger=None):
        """
        :param token_file_path: 명시적으로 토큰 파일 경로를 지정할 수 있음
        :param is_paper_trading: True면 모의투자용 토큰 파일, False면 실전투자용
        """
        self.token_file_path = token_file_path
        self._access_token = None
        self._token_expired_at = None
        self._logger = logger if logger else logging.getLogger(__name__)

    async def get_access_token(self, base_url: str, app_key: str, app_secret: str) -> str: # env 인자 대신 필요한 정보만 받음
        """유효한 액세스 토큰을 반환합니다. 필요 시 파일에서 로드하거나 새로 발급합니다."""
        # 1. 메모리에 토큰이 있고 유효한지 먼저 확인
        if self._access_token and self._is_token_valid():
            return self._access_token

        # 2. 파일에서 토큰을 로드하고 유효한지 확인
        self._load_token_from_file()
        if self._access_token and self._is_token_valid():
            # 파일에서 로드한 토큰이 현재 환경의 base_url과 일치하는지 확인
            loaded_token_base_url = self._get_token_base_url_from_file()
            if loaded_token_base_url == base_url: # 전달받은 base_url과 비교
                self._logger.info("파일에서 유효한 토큰을 로드했습니다.")
                return self._access_token
            else:
                self._logger.warning(f"파일에서 로드한 토큰의 base_url이 현재 환경과 다릅니다. 저장된: {loaded_token_base_url}, 현재: {base_url}. 새 토큰 발급 필요.")
                self._access_token = None # base_url이 다르면 토큰 무효화
                self._token_expired_at = None

        # 3. 위 모든 경우에 해당하지 않으면 새로 발급
        self._logger.info("새로운 액세스 토큰을 발급합니다.")
        await self._issue_new_token(base_url, app_key, app_secret) # 필요한 정보 전달

        return self._access_token

    def _is_token_valid(self):
        """토큰이 존재하고, 만료되지 않았는지 확인합니다. (5분 여유시간)"""
        if not self._access_token or not self._token_expired_at:
            return False
        # 만료 시간 5분 전에 갱신하도록 여유를 둡니다.
        now_kst = datetime.now(pytz.timezone('Asia/Seoul'))
        # self._logger.debug(
        #     f"현재 시각: {now_kst}, 만료 시각: {self._token_expired_at}, 기준 시각: {self._token_expired_at - timedelta(minutes=10)}")

        return now_kst < self._token_expired_at - timedelta(minutes=10)

    def _load_token_from_file(self):
        """파일에서 토큰 정보를 로드합니다."""
        try:
            with open(self.token_file_path, 'r') as f:
                token_data = json.load(f)
                self._access_token = token_data.get('access_token')
                expiry_str = token_data.get('expired_at')

                if expiry_str:
                    # fromisoformat()이 이미 타임존 정보를 파싱하므로, localize()를 다시 호출할 필요 없음
                    self._token_expired_at = datetime.fromisoformat(expiry_str)

        except (FileNotFoundError, json.JSONDecodeError):
            self._logger.warning(f"토큰 파일({self.token_file_path})을 찾을 수 없거나 형식이 잘못되었습니다. 새 토큰 발급을 시도합니다.")
            self._access_token = None
            self._token_expired_at = None

    def _get_token_base_url_from_file(self):
        """토큰 파일에서 base_url을 읽어옵니다."""
        try:
            if not os.path.exists(self.token_file_path):
                return None
            with open(self.token_file_path, 'r') as f:
                token_data = json.load(f)
                return token_data.get('base_url')  # base_url 필드를 읽음
        except (FileNotFoundError, json.JSONDecodeError):
            return None

    def _save_token_to_file(self, base_url_for_token: str):  # base_url 인자 추가
        """현재 토큰 정보를 파일에 저장합니다."""
        token_data = {
            'access_token': self._access_token,
            'expired_at': self._token_expired_at.isoformat(),
            'base_url': base_url_for_token  # base_url도 함께 저장
        }
        os.makedirs(os.path.dirname(self.token_file_path), exist_ok=True)
        with open(self.token_file_path, 'w') as f:
            json.dump(token_data, f, indent=4)
        self._logger.info("새 토큰을 파일에 저장했습니다.")

    async def _issue_new_token(self, base_url: str, app_key: str, app_secret: str):  # 필요한 정보만 받음
        """API 서버에 요청하여 새로운 토큰을 발급받고, 상태를 업데이트합니다."""

        if not base_url or not app_key or not app_secret:
            self._logger.critical("토큰 발급에 필요한 환경 설정(base_url, app_key, app_secret)이 부족합니다.")
            raise ValueError("Missing environment configuration for token issuance.")

        url = f"{base_url}/oauth2/tokenP"  # 전달받은 base_url 사용
        body = {
            "grant_type": "client_credentials",
            "appkey": app_key,  # 전달받은 app_key 사용
            "appsecret": app_secret  # 전달받은 app_secret 사용
        }
        async with httpx.AsyncClient() as client:
            headers = {
                "Cache-Control": "no-cache",
                "Pragma": "no-cache"
            }
            response = await client.post(url, json=body, headers=headers)
            # response = await client.post(url, json=body)
            response.raise_for_status()
            res_data = response.json()

            self._access_token = res_data.get('access_token')
            self._logger.info(f"✅ _issue_new_token - {self._access_token}")
            expires_in = int(res_data.get('expires_in', 0))

            # KST timezone을 고려하여 datetime 객체 생성
            kst_timezone = pytz.timezone('Asia/Seoul')
            self._token_expired_at = kst_timezone.localize(datetime.now() + timedelta(seconds=expires_in))

            self._save_token_to_file(base_url)  # 현재 발급된 토큰의 base_url 저장

    def invalidate_token(self):
        """외부에서 토큰 만료를 감지했을 때, 현재 토큰을 강제로 무효화합니다."""
        self._access_token = None
        self._token_expired_at = None
        if os.path.exists(self.token_file_path):
            os.remove(self.token_file_path)
        self._logger.info("저장된 토큰이 무효화되었습니다.")

    async def refresh_token(self, base_url: str, app_key: str, app_secret: str):
        """
        외부에서 강제로 토큰을 재발급하고 상태를 초기화할 때 사용합니다.
        EGW00123 오류 응답을 받았을 때 호출하면 됩니다.
        """
        self._logger.info("🔁 refresh_token() 호출됨 - 강제 토큰 재발급 시작")
        if self._access_token:
            self._logger.debug(f"✅ refresh_token 기존 토큰: {self._access_token[:40]}...")
        else:
            self._logger.debug("✅ refresh_token 기존 토큰: (None)")
        self.invalidate_token()  # ✅ 캐시/파일 무효화 먼저!
        await self._issue_new_token(base_url, app_key, app_secret)

        if self._access_token:
            self._logger.debug(f"✅ refresh_token 재발급 후 토큰: {self._access_token[:40]}...")
        else:
            raise Exception

        self._logger.info("✅ 강제 토큰 재발급 완료")
