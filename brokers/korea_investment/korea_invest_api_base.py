# brokers/korea_investment/korea_invest_api_base.py

import requests
import json
import certifi
import logging
import asyncio  # 비동기 처리를 위해 추가
import httpx  # 비동기 처리를 위해 requests 대신 httpx 사용
import ssl
from brokers.korea_investment.korea_invest_env import KoreaInvestApiEnv  # TokenManager를 import
from common.types import ErrorCode, ResCommonResponse
from typing import Union, Optional
from brokers.korea_investment.korea_invest_header_provider import build_header_provider_from_env, \
    KoreaInvestHeaderProvider
from brokers.korea_investment.korea_invest_url_provider import KoreaInvestUrlProvider
from brokers.korea_investment.korea_invest_trid_provider import KoreaInvestTrIdProvider


class ApiRetryError(Exception):
    """재시도 가능한 API 오류 (예: 일시적 네트워크 오류, Rate Limit, 토큰 만료)"""
    def __init__(self, message, delay=0.0):
        super().__init__(message)
        self.delay = delay

class ApiFatalError(Exception):
    """복구 불가능한 API 오류 (예: 파싱 실패, 비즈니스 로직 오류)"""
    def __init__(self, message, rt_cd=None):
        super().__init__(message)
        self.rt_cd = rt_cd

class KoreaInvestApiBase:
    """
    모든 한국투자증권 API 호출 클래스가 공통적으로 사용할 기본 클래스입니다.
    requests.Session을 사용하여 연결 효율성을 높입니다.
    """

    def __init__(self, env: KoreaInvestApiEnv,
                 logger=None,
                 time_manager=None,
                 async_client: Optional[httpx.AsyncClient] = None,
                 header_provider: Optional[KoreaInvestHeaderProvider] = None,
                 url_provider: Optional[KoreaInvestUrlProvider] = None,
                 trid_provider: Optional[KoreaInvestTrIdProvider] = None):
        self._logger = logger if logger else logging.getLogger(__name__)
        self.time_manager = time_manager
        self._env = env
        self._base_url = None
        self._headers: KoreaInvestHeaderProvider = header_provider or build_header_provider_from_env(env)
        self._url_provider: KoreaInvestUrlProvider = url_provider or KoreaInvestUrlProvider.from_env_and_kis_config(env)
        self._trid_provider = trid_provider or KoreaInvestTrIdProvider.from_config_loader(env)
        self._use_real_auth: bool = False  # True면 항상 실전 인증 사용 (조회 API)

        if async_client:
            self._async_session = async_client
        else:
            ssl_context = ssl.create_default_context(cafile=certifi.where())
            self._async_session = httpx.AsyncClient(verify=ssl_context)

        # urllib3 로거의 DEBUG 레벨을 비활성화하여 call_api의 DEBUG 로그와 분리
        logging.getLogger('urllib3.connectionpool').setLevel(logging.WARNING)
        logging.getLogger('httpcore').setLevel(logging.WARNING)  # httpx의 하위 로거

    # ✅ 하위 클래스가 URL 만들 때 쓰는 헬퍼
    def url(self, key_or_path) -> str:
        return self._url_provider.url(key_or_path)

    async def call_api(self,
                       method,
                       key_or_path,
                       params=None,
                       data=None,
                       expect_standard_schema: bool = True,
                       retry_count=10,
                       delay=1):
        url = self.url(key_or_path)

        for attempt in range(1, retry_count + 1):
            try:
                self._logger.debug(f"API 호출 시도 {attempt}/{retry_count} - {method} {url}")

                response = await self._execute_request(method, url, params, data)

                # 네트워크 에러 시 ResCommonResponse가 반환될 수 있음 → 바로 리턴
                if isinstance(response, ResCommonResponse):
                    return response

                # 예외 기반 처리: 성공 시 dict 반환, 실패 시 예외 발생
                result_data = await self._handle_response(response, expect_standard_schema)

                return ResCommonResponse(
                    rt_cd=ErrorCode.SUCCESS.value,
                    msg1="정상",
                    data=result_data
                )

            except ApiRetryError as e:
                # 지수 백오프 적용: 기본 delay * 2^(attempt-1)
                if e.delay > 0:
                    wait_time = e.delay
                else:
                    wait_time = delay * (2 ** (attempt - 1))
                self._logger.warning(f"재시도 필요: {attempt}/{retry_count}, 사유: {e}, 지연 {wait_time}초")
                await self.time_manager.async_sleep(wait_time)
                continue

            except ApiFatalError as e:
                self._logger.error(f"복구 불가능한 오류 발생: {url}, 사유: {e}")
                return ResCommonResponse(
                    rt_cd=str(e.rt_cd) if e.rt_cd else ErrorCode.PARSING_ERROR.value,
                    msg1=f"API 오류: {e}",
                    data=None
                )

            except Exception as e:
                self._log_request_exception(e)
                if attempt < retry_count:
                    self._logger.info(f"예외 발생, 재시도: {attempt}/{retry_count}, 지연 {delay}초")
                    await self.time_manager.async_sleep(delay)  # 이 부분이 호출되어야 함
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
        for key, value in self._headers.build().items():
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
            self._logger.error(f"HTTP 오류 발생 (httpx): {e.response.status_code} - {e.response.text}", exc_info=True)
        elif isinstance(e, requests.exceptions.HTTPError):
            self._logger.error(f"HTTP 오류 발생 (requests): {e.response.status_code} - {e.response.text}", exc_info=True)
        elif isinstance(e, requests.exceptions.ConnectionError):
            self._logger.error(f"연결 오류 발생: {e}", exc_info=True)
        elif isinstance(e, requests.exceptions.Timeout):
            self._logger.error(f"타임아웃 오류 발생: {e}", exc_info=True)
        elif isinstance(e, requests.exceptions.RequestException):  # requests 관련 일반 예외
            self._logger.error(f"요청 예외 발생 (requests): {e}", exc_info=True)
        elif isinstance(e, httpx.RequestError):  # httpx 관련 일반 요청 오류 (연결, 타임아웃 등)
            self._logger.error(f"요청 예외 발생 (httpx): {e}", exc_info=True)
        elif isinstance(e, json.JSONDecodeError):
            self._logger.error("JSON 디코딩 오류 발생", exc_info=True)
        else:
            self._logger.error(f"예상치 못한 예외 발생: {e}", exc_info=True)

    async def _execute_request(self, method, url, params, data):
        loop = asyncio.get_running_loop()
        response = None
        token_refreshed = False  # ✅ 토큰 재발급 여부 플래그

        async def make_request():
            self._headers.sync_from_env(self._env)

            if self._use_real_auth:
                # 조회 API: 항상 실전 인증 사용
                access_token = await self._env.get_real_access_token()
                real_cfg = self._env.get_real_config()
                self._headers.set_app_keys(real_cfg['api_key'], real_cfg['api_secret_key'])
            else:
                access_token = await self._env.get_access_token()
                self._headers.set_app_keys(self._env.active_config['api_key'], self._env.active_config['api_secret_key'])

            if not isinstance(access_token, str) or access_token is None:
                raise ValueError("접근 토큰이 없습니다. KoreaInvestEnv에서 먼저 토큰을 발급받아야 합니다.")

            self._headers.set_auth_bearer(access_token)
            headers = self._headers.build()
            # self._log_headers()

            if method.upper() == 'GET':
                self._logger.debug(f"[GET] 요청 Url: {url}")
                self._logger.debug(f"[GET] 요청 Headers: {headers}")
                self._logger.debug(f"[GET] 요청 Data: {params}")
                return await self._async_session.get(url, headers=headers, params=params)
            elif method.upper() == 'POST':
                json_body = json.dumps(data) if data else None

                self._logger.debug(f"[POST] 요청 Url: {url}")
                self._logger.debug(f"[POST] 요청 Headers: {headers}")
                self._logger.debug(f"[POST] 요청 Data: {json_body}")

                return await self._async_session.post(
                    url,
                    headers=headers,
                    data=json_body,  # json 넘기면 실패.
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
                await self.time_manager.async_sleep(3)
                await self._env.refresh_token()
                token_refreshed = True  # ✅ 재시도 플래그 설정

                # ✅ 강제 delay 삽입

                # ✅ 반드시 새로 가져온 토큰으로 Authorization 헤더 재세팅
                new_token = await self._env.get_access_token()
                self._headers.set_auth_bearer(new_token)  # ✅ 메서드 사용
                self._logger.debug(f"✅ 재발급 후 토큰 적용 확인: {new_token[:40]}...")

                response = await make_request()

        except httpx.RequestError as e:
            if self._logger:
                self._logger.error(f"요청 예외 발생 (httpx): {str(e)}")
                auth = self._headers.build().get("Authorization", "")  # ✅ 안전 조회
                self._logger.debug(f"[EGW00123 대응] 현재 Authorization 헤더: {auth[:40]}...")
            return ResCommonResponse(rt_cd=ErrorCode.NETWORK_ERROR.value, msg1=str(e), data=None)

        return response

    async def _handle_response(self, response, expect_standard_schema: bool = True) -> dict:
        """HTTP 응답을 처리하고, 오류 유형에 따라 재시도 여부를 결정합니다."""
        # 1. 호출 제한 오류 (Rate Limit) - 최상단에서 가장 먼저 검사하고 즉시 반환
        if response.status_code == 429 or \
                (response.status_code == 500 and "초당 거래건수를 초과하였습니다" in response.text):
            raise ApiRetryError("Rate limit exceeded")

        # 2. 그 외의 HTTP 오류 (HTTP 상태 코드 자체로 인한 오류)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            self._logger.error(f"HTTP 오류 발생: {e.response.status_code} - {e.response.text}")
            raise ApiFatalError(f"HTTP Error {e.response.status_code}", rt_cd=ErrorCode.NETWORK_ERROR.value)

        # 3. 성공적인 응답 처리 (JSON 디코딩)
        try:
            response_json = response.json()
        except (json.JSONDecodeError, ValueError):
            self._logger.error(f"응답 JSON 디코딩 실패: {response.text}")
            raise ApiFatalError("JSON Decode Error", rt_cd=ErrorCode.PARSING_ERROR.value)

        # 3-1. 응답 형식 검증 (dict 여부)
        if not isinstance(response_json, dict):
            self._logger.error(f"API 응답 형식이 dict가 아님: {type(response_json)}")
            raise ApiFatalError("Response is not a dict", rt_cd=ErrorCode.PARSING_ERROR.value)

        # 4. 토큰 만료 오류 처리 (API 응답 내용 기반)
        if response_json.get('msg_cd') == 'EGW00123':
            self._logger.error("최종 토큰 만료 오류(EGW00123) 감지.")
            self._env.invalidate_token()
            raise ApiRetryError("Token expired")

        if not expect_standard_schema:
            # ✅ 표준 스키마(rt_cd 등) 미적용 엔드포인트: 2xx면 성공으로 간주
            return response_json

        # 5. API 비즈니스 로직 오류 (rt_cd가 '0'이 아님)
        # 이 검사는 429/500 rate limit 케이스에서는 도달하지 않아야 합니다.
        if response_json.get('rt_cd') is None or response_json.get('rt_cd') != '0':
            # msg1이 있을 경우에만 로깅, 없을 경우 "None" 로깅 방지
            error_message = response_json.get('msg1', '알 수 없는 비즈니스 오류')
            self._logger.error(f"API 비즈니스 오류: {error_message}")
            raise ApiFatalError(f"Business Error: {error_message}", rt_cd=ErrorCode.API_ERROR.value)

        # 6. 데이터 존재 여부 검증 (성공 응답인 경우)
        if not any(key in response_json for key in ["output", "output1", "output2"]):
            self._logger.error(f"API 응답에 output 데이터가 없습니다: {response.text}")
            raise ApiFatalError("Missing output data", rt_cd=ErrorCode.PARSING_ERROR.value)

        # 모든 검사를 통과한 최종 성공적인 응답
        self._logger.debug(f"API 응답 성공: {response.text}")
        return response_json
