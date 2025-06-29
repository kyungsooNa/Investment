# brokers/korea_investment/korea_invest_api_base.py

import requests
import json
import certifi
import logging
import asyncio  # 비동기 처리를 위해 추가
import httpx  # 비동기 처리를 위해 requests 대신 httpx 사용
from brokers.korea_investment.korea_invest_token_manager import TokenManager # TokenManager를 import


class KoreaInvestApiBase:
    """
    모든 한국투자증권 API 호출 클래스가 공통적으로 사용할 기본 클래스입니다.
    requests.Session을 사용하여 연결 효율성을 높입니다.
    """

    def __init__(self, base_url, headers, config, token_manager: TokenManager, logger=None):  # base_url, headers, config, logger를 받음
        self.logger = logger if logger else logging.getLogger(__name__)
        self._config = config  # _config는 모든 설정(tr_ids, base_url 등)을 포함
        self._base_url = base_url  # 초기화 시 전달받은 base_url 사용
        self._headers = headers.copy()  # 초기화 시 전달받은 headers 복사하여 사용
        self._session = requests.Session()  # requests.Session은 동기
        self.token_manager = token_manager
        self._async_session = httpx.AsyncClient(verify=certifi.where()) # 비동기 세션 생성


        # urllib3 로거의 DEBUG 레벨을 비활성화하여 call_api의 DEBUG 로그와 분리
        logging.getLogger('urllib3.connectionpool').setLevel(logging.WARNING)
        logging.getLogger('httpcore').setLevel(logging.WARNING) # httpx의 하위 로거

    async def call_api(self, method, path, params=None, data=None, retry_count=10, delay=1):
        url = f"{self._base_url}{path}"

        for attempt in range(1, retry_count + 1):
            try:
                self.logger.debug(f"API 호출 시도 {attempt}/{retry_count} - {method} {url}")
                self._log_headers()

                response = await self._execute_request(method, url, params, data)

                result = await self._handle_response(response)
                if result == "retry":
                    continue
                # ▼▼▼ 핵심 수정 부분 ▼▼▼
                # _handle_response가 None을 반환하면, 이는 복구 불가능한 오류를 의미합니다.
                # 따라서 재시도를 중단하고 None을 즉시 반환해야 합니다.
                # 'continue'를 'return None'으로 변경합니다.
                if result is None:
                    return None
                # ▲▲▲ 핵심 수정 부분 ▲▲▲
                return result

            except Exception as e:
                self._log_request_exception(e)
                return None

        self.logger.error("모든 재시도 실패, API 호출 종료")
        return None

    async def close_session(self):
        """애플리케이션 종료 시 httpx 세션을 닫습니다."""
        await self._async_session.aclose()
        self.logger.info("HTTP 클라이언트 세션이 종료되었습니다.")

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

    # brokers/korea_investment/korea_invest_api_base.py

    async def _handle_response(self, response):
        """HTTP 응답을 처리하고, 오류 유형에 따라 재시도 여부를 결정합니다."""
        # 1. 호출 제한 오류 (Rate Limit)
        if response.status_code == 429 or \
                (response.status_code == 500 and "초당 거래건수" in response.text):
            return "retry"

        # 2. 그 외의 HTTP 오류
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            self.logger.error(f"HTTP 오류 발생: {e.response.status_code} - {e.response.text}")
            return None  # HTTP 오류 시 재시도하지 않고 종료

        # 3. 성공적인 응답 처리
        try:
            response_json = response.json()
        except (json.JSONDecodeError, ValueError):
            self.logger.error(f"응답 JSON 디코딩 실패: {response.text}")
            return None

        # 4. 토큰 만료 오류 처리 (수정된 로직)
        if response_json.get('msg_cd') == 'EGW00123':
            self.logger.error("토큰 만료 오류(EGW00123) 감지.")

            # ▼▼▼ 핵심 수정 부분 ▼▼▼
            # 토큰 재발급에 필요한 config 정보가 있는지 확인
            if self._config is None:
                # 테스트에서 기대했던 바로 그 로그 메시지
                self.logger.error("KoreaInvestEnv(config) 인스턴스를 찾을 수 없어 토큰 초기화 불가")
                return None  # 재시도 없이 즉시 종료
            # ▲▲▲ 핵심 수정 부분 ▲▲▲

            self.token_manager.invalidate_token()  # TokenManager에 토큰 무효화 위임
            return "retry"  # 재시도를 위해 "retry" 반환

        # 5. API 비즈니스 로직 오류
        if response_json.get('rt_cd') != '0':
            self.logger.error(f"API 비즈니스 오류: {response_json.get('msg1')}")
            return response_json  # 오류 내용도 일단 반환하여 상위에서 처리

        # 모든 검사를 통과한 성공적인 응답
        self.logger.debug(f"API 응답 성공: {response.text}")
        return response_json

    async def _handle_token_expiration(self, response_json, attempt, retry_count, delay):
        self.logger.error("토큰 만료 오류 감지. 다음 요청 시 토큰을 재발급합니다.")
        self.token_manager.invalidate_token() # TokenManager에 무효화 요청

        if attempt < retry_count:
            self.logger.info("토큰 재발급 후 API 호출을 재시도합니다.")
            await asyncio.sleep(delay)
            return "retry"
        else:
            self.logger.error("토큰 재발급 후에도 실패, 종료")
            return None

