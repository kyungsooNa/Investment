# api/base.py
import requests
import json
import certifi
import logging
import asyncio  # 비동기 처리를 위해 추가
from api.env import KoreaInvestEnv


class _KoreaInvestAPIBase:
    """
    모든 한국투자증권 API 호출 클래스가 공통적으로 사용할 기본 클래스입니다.
    requests.Session을 사용하여 연결 효율성을 높입니다.
    """

    def __init__(self, base_url, headers, config, logger=None):  # base_url, headers, config, logger를 받음
        self.logger = logger if logger else logging.getLogger(__name__)
        self._config = config  # _config는 모든 설정(tr_ids, base_url 등)을 포함
        self._base_url = base_url  # 초기화 시 전달받은 base_url 사용
        self._headers = headers.copy()  # 초기화 시 전달받은 headers 복사하여 사용
        self._session = requests.Session()  # requests.Session은 동기

        # _env_instance는 _config 딕셔너리 안에 저장되어 있으므로, 초기화 시 여기에 참조
        # (API 호출 시 토큰 만료 등 특정 오류 발생 시 KoreaInvestEnv 인스턴스에 직접 접근하여 토큰 초기화 목적)
        self._env = self._config.get('_env_instance')

        # urllib3 로거의 DEBUG 레벨을 비활성화하여 call_api의 DEBUG 로그와 분리
        logging.getLogger('urllib3.connectionpool').setLevel(logging.WARNING)

    async def call_api(self, method, path, params=None, data=None, retry_count=10, delay=1):
        url = f"{self._base_url}{path}"

        for attempt in range(1, retry_count + 1):
            try:
                self.logger.debug(f"API 호출 시도 {attempt}/{retry_count} - {method} {url}")
                self._log_headers()

                response = await self._execute_request(method, url, params, data)

                result = await self._handle_response(response, attempt, retry_count, delay)
                if result == "retry":
                    continue
                elif result is not None:
                    return result

            except Exception as e:
                self._log_request_exception(e)
                return None

        self.logger.error("모든 재시도 실패, API 호출 종료")
        return None

    def _log_headers(self):
        self.logger.debug("\nDEBUG: Headers being sent:")
        for key, value in self._headers.items():
            try:
                encoded_value = str(value).encode('latin-1', errors='ignore')
                self.logger.debug(f"  {key}: {encoded_value}")
            except UnicodeEncodeError:
                self.logger.debug(f"  {key}: *** UnicodeEncodeError ***")

    def _log_request_exception(self, e):
        if isinstance(e, requests.exceptions.HTTPError):
            self.logger.error(f"HTTP 오류 발생: {e.response.status_code} - {e.response.text}")
        elif isinstance(e, requests.exceptions.ConnectionError):
            self.logger.error(f"연결 오류 발생: {e}")
        elif isinstance(e, requests.exceptions.Timeout):
            self.logger.error(f"타임아웃 오류 발생: {e}")
        elif isinstance(e, requests.exceptions.RequestException):
            self.logger.error(f"요청 예외 발생: {e}")
        elif isinstance(e, json.JSONDecodeError):
            self.logger.error("JSON 디코딩 오류 발생")
        else:
            self.logger.error(f"예상치 못한 예외 발생: {e}")

    async def _execute_request(self, method, url, params, data):
        loop = asyncio.get_running_loop()

        if method.upper() == 'GET':
            return await loop.run_in_executor(
                None,
                lambda: self._session.get(url, headers=self._headers, params=params, verify=certifi.where())
            )
        elif method.upper() == 'POST':
            return await loop.run_in_executor(
                None,
                lambda: self._session.post(
                    url, headers=self._headers,
                    data=json.dumps(data) if data else None,
                    verify=certifi.where()
                )
            )
        else:
            raise ValueError(f"지원하지 않는 HTTP 메서드: {method}")

    async def _handle_response(self, response, attempt, retry_count, delay):
        if response.status_code == 429 or (response.status_code == 500 and
                                           response.json().get("msg1") == "초당 거래건수를 초과하였습니다."):
            self.logger.warning(f"호출 제한 오류 감지(HTTP {response.status_code}). {delay}초 후 재시도 {attempt}/{retry_count} ...")
            if attempt < retry_count:
                await asyncio.sleep(delay)
                return "retry"
            else:
                self.logger.error("재시도 횟수 초과, API 호출 실패")
                return None

        response.raise_for_status()

        if response.status_code != 200:
            self.logger.error(f"비정상 HTTP 상태 코드: {response.status_code}, 응답 내용: {response.text}")
            return None

        try:
            response_json = response.json()
        except (json.JSONDecodeError, ValueError):
            self.logger.error(f"응답 JSON 디코딩 실패: {response.text if response else '응답 없음'}")
            return None

        if not isinstance(response_json, dict):
            self.logger.error(f"잘못된 응답 형식: {response_json}")
            return None

        if response_json.get('rt_cd') == '1' and response_json.get('msg_cd') == 'EGW00123':
            return await self._handle_token_expiration(response_json, attempt, retry_count, delay)

        self.logger.debug(f"API 응답 상태: {response.status_code}")
        self.logger.debug(f"API 응답 텍스트: {response.text}")
        return response_json

    async def _handle_token_expiration(self, response_json, attempt, retry_count, delay):
        self.logger.error("토큰 만료 오류 감지. 다음 요청 시 토큰을 재발급합니다.")
        if self._env and isinstance(self._env, KoreaInvestEnv):
            self._env.access_token = None
            self._env.token_expired_at = None

            if attempt < retry_count:
                self.logger.info("토큰 재발급 후 API 호출을 재시도합니다.")
                await asyncio.sleep(delay)
                return "retry"
            else:
                self.logger.error("토큰 재발급 후에도 실패, 종료")
                return None
        else:
            self.logger.error("KoreaInvestEnv 인스턴스를 찾을 수 없어 토큰 초기화 불가")
            return None
