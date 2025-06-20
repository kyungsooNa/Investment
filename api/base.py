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

    def __init__(self, base_url, headers, config, logger=None):
        self.logger = logger if logger else logging.getLogger(__name__)
        self._config = config
        self._base_url = base_url
        self._headers = headers.copy()
        self._session = requests.Session()  # requests.Session은 동기

        logging.getLogger('urllib3.connectionpool').setLevel(logging.WARNING)

        # _call_api 메서드를 async로 변경

    async def _call_api(self, method, path, params=None, data=None):  # <--- async def 추가
        """API 호출을 위한 내부 헬퍼 메서드."""
        url = f"{self._base_url}{path}"

        # 헤더는 _headers를 사용 (파생 클래스에서 이미 업데이트됨)
        # appkey, appsecret은 이미 _headers에 포함되어 있음.

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

            response = None
            loop = asyncio.get_running_loop()  # 현재 실행 중인 이벤트 루프 가져오기

            if method.upper() == 'GET':
                # requests 호출을 executor에서 실행하여 비동기적으로 만듦
                response = await loop.run_in_executor(
                    None,  # 기본 ThreadPoolExecutor 사용
                    lambda: self._session.get(url, headers=self._headers, params=params, verify=certifi.where())
                )
            elif method.upper() == 'POST':
                # requests 호출을 executor에서 실행하여 비동기적으로 만듦
                response = await loop.run_in_executor(
                    None,  # 기본 ThreadPoolExecutor 사용
                    lambda: self._session.post(url, headers=self._headers, data=json.dumps(data) if data else None,
                                               verify=certifi.where())
                )
            else:
                raise ValueError(f"지원하지 않는 HTTP 메서드: {method}")

            response.raise_for_status()
            self.logger.debug(f"API 응답 상태: {response.status_code}")
            self.logger.debug(f"API 응답 텍스트: {response.text}")
            return response.json()
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
