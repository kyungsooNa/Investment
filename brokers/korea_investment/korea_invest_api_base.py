# brokers/korea_investment/korea_invest_api_base.py

import requests
import json
import certifi
import logging
import asyncio  # 비동기 처리를 위해 추가
import httpx  # 비동기 처리를 위해 requests 대신 httpx 사용
import ssl
import jwt
from brokers.korea_investment.korea_invest_env import KoreaInvestApiEnv  # TokenManager를 import
from common.types import ErrorCode, ResCommonResponse, ResponseStatus
from typing import Union, Optional


class KoreaInvestApiBase:
    """
    모든 한국투자증권 API 호출 클래스가 공통적으로 사용할 기본 클래스입니다.
    requests.Session을 사용하여 연결 효율성을 높입니다.
    """

    def __init__(self, env: KoreaInvestApiEnv,
                 logger=None, async_client: Optional[httpx.AsyncClient] = None):
        self._logger = logger if logger else logging.getLogger(__name__)
        self._env = env

        # self._headers = headers.copy()  # 초기화 시 전달받은 headers 복사하여 사용
        self._headers = {
            "Content-Type": "application/json",
            "User-Agent": self._env.my_agent,
            "charset": "UTF-8",
            "Authorization": "",
            "appkey": "",
            "appsecret": "",
        }
        # self._session = requests.Session()  # requests.Session은 동기
        self._base_url = None
        # self._config = config  # _config는 모든 설정(tr_ids, base_url 등)을 포함

        if async_client:
            self._async_session = async_client
        else:
            ssl_context = ssl.create_default_context(cafile=certifi.where())
            self._async_session = httpx.AsyncClient(verify=ssl_context)

        # urllib3 로거의 DEBUG 레벨을 비활성화하여 call_api의 DEBUG 로그와 분리
        logging.getLogger('urllib3.connectionpool').setLevel(logging.WARNING)
        logging.getLogger('httpcore').setLevel(logging.WARNING)  # httpx의 하위 로거

    async def call_api(self, method, path, params=None, data=None, retry_count=10, delay=1):
        self._base_url = self._env.get_base_url()
        url = f"{self._base_url}{path}"

        for attempt in range(1, retry_count + 1):
            try:
                self._logger.debug(f"API 호출 시도 {attempt}/{retry_count} - {method} {url}")
                self._log_headers()

                response = await self._execute_request(method, url, params, data)

                result: Union[dict, ResponseStatus] = await self._handle_response(response)

                if result is ResponseStatus.RETRY:
                    self._logger.info(f"재시도 필요: {attempt}/{retry_count}, 지연 {delay}초")
                    await asyncio.sleep(delay)  # 이 부분이 호출되어야 함
                    continue

                if isinstance(result, ResponseStatus):
                    self._logger.error(f"복구 불가능한 오류 발생: {url}, 응답: {response.text}")
                    return ResCommonResponse(
                        rt_cd=ErrorCode.PARSING_ERROR.value,
                        msg1=f"API 응답 파싱 실패 또는 처리 불가능 - {response.text}",
                        data=None
                    )

                return ResCommonResponse(
                    rt_cd=ErrorCode.SUCCESS.value,
                    msg1="정상",
                    data=result
                )

            except Exception as e:
                self._log_request_exception(e)
                if attempt < retry_count:
                    self._logger.info(f"예외 발생, 재시도: {attempt}/{retry_count}, 지연 {delay}초")
                    await asyncio.sleep(delay)  # 이 부분이 호출되어야 함
                    continue
                else:
                    pass

        self._logger.error("모든 재시도 실패, API 호출 종료")
        return ResCommonResponse(
            rt_cd=ErrorCode.RETRY_LIMIT.value,
            msg1=f"최대 재시도 횟수 초과",
            data=None
        )

    async def close_session(self):
        """애플리케이션 종료 시 httpx 세션을 닫습니다."""
        await self._async_session.aclose()
        self._logger.info("HTTP 클라이언트 세션이 종료되었습니다.")

    def _log_headers(self):
        self._logger.debug("\nDEBUG: Headers being sent:")
        for key, value in self._headers.items():
            try:
                encoded_value = str(value).encode('latin-1', errors='ignore')
            except UnicodeEncodeError:
                self._logger.debug(f"  {key}: *** UnicodeEncodeError ***")
            except Exception as e:
                self._logger.debug(f"  {key}: *** {type(e).__name__}: {e} ***")
            else:
                self._logger.debug(f"  {key}: {encoded_value}")

    def _log_request_exception(self, e):
        if isinstance(e, httpx.HTTPStatusError):
            self._logger.error(f"HTTP 오류 발생 (httpx): {e.response.status_code} - {e.response.text}")
        elif isinstance(e, requests.exceptions.HTTPError):
            self._logger.error(f"HTTP 오류 발생 (requests): {e.response.status_code} - {e.response.text}")
        elif isinstance(e, requests.exceptions.ConnectionError):
            self._logger.error(f"연결 오류 발생: {e}")
        elif isinstance(e, requests.exceptions.Timeout):
            self._logger.error(f"타임아웃 오류 발생: {e}")
        elif isinstance(e, requests.exceptions.RequestException):  # requests 관련 일반 예외
            self._logger.error(f"요청 예외 발생 (requests): {e}")
        elif isinstance(e, httpx.RequestError):  # httpx 관련 일반 요청 오류 (연결, 타임아웃 등)
            self._logger.error(f"요청 예외 발생 (httpx): {e}")
        elif isinstance(e, json.JSONDecodeError):
            self._logger.error("JSON 디코딩 오류 발생")
        else:
            self._logger.error(f"예상치 못한 예외 발생: {e}")

    async def _execute_request(self, method, url, params, data):
        loop = asyncio.get_running_loop()
        response = None
        token_refreshed = False  # ✅ 토큰 재발급 여부 플래그

        async def make_request():
            access_token: str = await self._env.get_access_token()
            # payload = jwt.decode(access_token, options={"verify_signature": False})
            # self._logger.debug(f"access_token payload: {payload}")
            if not isinstance(access_token, str) or access_token is None:
                raise ValueError("접근 토큰이 없습니다. KoreaInvestEnv에서 먼저 토큰을 발급받아야 합니다.")

            self._set_headers(access_token)

            if method.upper() == 'GET':
                return await self._async_session.get(url, headers=self._headers, params=params)
            elif method.upper() == 'POST':
                json_body = json.dumps(data) if data else None

                self._logger.debug(f"[POST] 요청 Url: {url}")
                self._logger.debug(f"[POST] 요청 Headers: {self._headers}")
                self._logger.debug(f"[POST] 요청 Data: {json_body}")

                return await self._async_session.post(
                    url,
                    headers=self._headers,
                    data=json_body,
                )
            else:
                raise ValueError(f"지원하지 않는 HTTP 메서드: {method}")

        try:
            response = await make_request()
            if response is None:
                raise ValueError("response is None")

            res_json = response.json()

            # ✅ 토큰 만료 응답 감지 시 재발급 + 재시도 (단 1회만)
            if isinstance(res_json, dict) and res_json.get("msg_cd") == "EGW00123" and not token_refreshed:
                self._logger.warning("🔁 토큰 만료 감지 (EGW00123). 재발급 후 1회 재시도")
                # await asyncio.sleep(65)
                await asyncio.sleep(3)
                await self._env.refresh_token()
                token_refreshed = True  # ✅ 재시도 플래그 설정

                # ✅ 강제 delay 삽입

                # ✅ 반드시 새로 가져온 토큰으로 Authorization 헤더 재세팅
                new_token = await self._env.get_access_token()
                self._headers["Authorization"] = f"Bearer {new_token}"
                self._logger.debug(f"✅ 재발급 후 토큰 적용 확인: {new_token[:40]}...")

                response = await make_request()

        except httpx.RequestError as e:
            if self._logger:
                self._logger.error(f"요청 예외 발생 (httpx): {str(e)}")
                self._logger.debug(f"[EGW00123 대응] 현재 Authorization 헤더: {self._headers['Authorization'][:40]}...")
            return ResCommonResponse(rt_cd=ErrorCode.NETWORK_ERROR.value, msg1=str(e), data=None)

        return response

    async def _handle_response(self, response) -> Union[dict, ResponseStatus]:
        """HTTP 응답을 처리하고, 오류 유형에 따라 재시도 여부를 결정합니다."""
        # 1. 호출 제한 오류 (Rate Limit) - 최상단에서 가장 먼저 검사하고 즉시 반환
        if response.status_code == 429 or \
                (response.status_code == 500 and "초당 거래건수를 초과하였습니다" in response.text):
            return ResponseStatus.RETRY  # <--- 이 조건이 만족되면 다른 검사 없이 즉시 반환

        # 2. 그 외의 HTTP 오류 (HTTP 상태 코드 자체로 인한 오류)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            self._logger.error(f"HTTP 오류 발생: {e.response.status_code} - {e.response.text}")
            return ResponseStatus.HTTP_ERROR

        # 3. 성공적인 응답 처리 (JSON 디코딩)
        try:
            response_json = response.json()
        except (json.JSONDecodeError, ValueError):
            self._logger.error(f"응답 JSON 디코딩 실패: {response.text}")
            return ResponseStatus.PARSING_ERROR

        # 4. 토큰 만료 오류 처리 (API 응답 내용 기반)
        if response_json.get('msg_cd') == 'EGW00123':
            self._logger.error("최종 토큰 만료 오류(EGW00123) 감지.")
            self._env.invalidate_token()
            return ResponseStatus.RETRY

        # 5. API 비즈니스 로직 오류 (rt_cd가 '0'이 아님)
        # 이 검사는 429/500 rate limit 케이스에서는 도달하지 않아야 합니다.
        if response_json.get('rt_cd') is None or response_json.get('rt_cd') != '0':
            # msg1이 있을 경우에만 로깅, 없을 경우 "None" 로깅 방지
            error_message = response_json.get('msg1', '알 수 없는 비즈니스 오류')
            self._logger.error(f"API 비즈니스 오류: {error_message}")
            return response_json  # 비즈니스 오류 내용을 반환

        # 모든 검사를 통과한 최종 성공적인 응답
        self._logger.debug(f"API 응답 성공: {response.text}")
        return response_json

    def _set_headers(self, access_token):
        self._headers["Authorization"] = f"Bearer {access_token}"
        self._headers["appkey"] = self._env.active_config['api_key']
        self._headers["appsecret"] = self._env.active_config['api_secret_key']
