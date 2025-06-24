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

        # urllib3 로거의 DEBUG 레벨을 비활성화하여 _call_api의 DEBUG 로그와 분리
        logging.getLogger('urllib3.connectionpool').setLevel(logging.WARNING)

    async def _call_api(self, method, path, params=None, data=None, retry_count=3, delay=1):
        """
        API 호출 헬퍼 메서드.
        retry_count: 재시도 횟수 (초당 거래건수 초과 등 API 제한 대응용)
        delay: 재시도 전 대기 시간(초)
        """
        url = f"{self._base_url}{path}"

        for attempt in range(1, retry_count + 1):
            try:
                self.logger.debug("\nDEBUG: Headers being sent:")
                self.logger.debug(f"DEBUG: Checking _config in _call_api (tr_ids exists: {'tr_ids' in self._config})")
                self.logger.debug(f"DEBUG: _config keys in _call_api: {self._config.keys()}")

                for key, value in self._headers.items():
                    try:
                        encoded_value = str(value).encode('latin-1', errors='ignore')
                        self.logger.debug(f"  {key}: {encoded_value}")
                    except UnicodeEncodeError:
                        self.logger.debug(f"  {key}: *** UnicodeEncodeError - Contains non-latin-1 characters ***")
                        self.logger.debug(f"  Problematic value (type: {type(value)}): {repr(value)}")
                self.logger.debug("--- End Headers Debug ---")

                loop = asyncio.get_running_loop()
                response = None

                if method.upper() == 'GET':
                    response = await loop.run_in_executor(
                        None,
                        lambda: self._session.get(url, headers=self._headers, params=params, verify=certifi.where())
                    )
                elif method.upper() == 'POST':
                    response = await loop.run_in_executor(
                        None,
                        lambda: self._session.post(url, headers=self._headers,
                                                   data=json.dumps(data) if data else None,
                                                   verify=certifi.where())
                    )
                else:
                    raise ValueError(f"지원하지 않는 HTTP 메서드: {method}")

                response.raise_for_status()
                response_json = response.json()

                # 호출 제한 에러 체크 (예: 500 + 초당 거래건수 초과)
                if response.status_code == 500 and response_json.get("msg1") == "초당 거래건수를 초과하였습니다.":
                    self.logger.warning(f"초당 거래건수 초과 오류 감지. {delay}초 후 재시도 {attempt}/{retry_count} ...")
                    if attempt < retry_count:
                        await asyncio.sleep(delay)
                        continue
                    else:
                        self.logger.error("재시도 횟수 초과, API 호출 실패")
                        return None

                # 만료 토큰 오류 감지
                if response_json.get('rt_cd') == '1' and response_json.get('msg_cd') == 'EGW00123':
                    self.logger.error(f"HTTP 오류 발생: {response.status_code} - {response.text}")
                    self.logger.error("토큰 만료 오류 감지. 다음 요청 시 토큰을 재발급합니다.")

                    if self._env and isinstance(self._env, KoreaInvestEnv):
                        self._env.access_token = None
                        self._env.token_expired_at = None

                        if attempt < retry_count:
                            self.logger.info("토큰 재발급 후 API 호출을 재시도합니다.")
                            # 재시도 (재귀 대신 루프로 처리 중이므로 continue)
                            await asyncio.sleep(delay)
                            continue
                        else:
                            self.logger.error("토큰 재발급 후에도 재시도 횟수가 없어 API 호출에 실패했습니다.")
                            return None
                    else:
                        self.logger.error("KoreaInvestEnv 인스턴스를 찾을 수 없어 토큰 초기화 및 재시도를 할 수 없습니다.")
                        return None

                # 정상 응답
                self.logger.debug(f"API 응답 상태: {response.status_code}")
                self.logger.debug(f"API 응답 텍스트: {response.text}")
                return response_json

            except requests.exceptions.HTTPError as http_err:
                self.logger.error(f"HTTP 오류 발생: {http_err.response.status_code} - {http_err.response.text}")
                return None
            except requests.exceptions.ConnectionError as conn_err:
                self.logger.error(f"연결 오류 발생: {conn_err}")
                return None
            except requests.exceptions.Timeout as timeout_err:
                self.logger.error(f"타임아웃 오류 발생: {timeout_err}")
                return None
            except requests.exceptions.RequestException as req_err:
                self.logger.error(f"알 수 없는 요청 오류 발생: {req_err}")
                return None
            except json.JSONDecodeError:
                self.logger.error(f"응답 JSON 디코딩 실패: {response.text if response else '응답 없음'}")
                return None
        return None
