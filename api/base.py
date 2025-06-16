# api/base.py
import requests
import json
import certifi
import logging
from api.env import KoreaInvestEnv


class _KoreaInvestAPIBase:
    """
    모든 한국투자증권 API 호출 클래스가 공통적으로 사용할 기본 클래스입니다.
    requests.Session을 사용하여 연결 효율성을 높입니다.
    """

    def __init__(self, base_url, headers, config, logger=None):  # base_url, headers, config를 명시적으로 받음
        # self._env는 이제 여기서 직접 사용하지 않습니다. 필요한 정보는 config를 통해 전달됩니다.
        self.logger = logger if logger else logging.getLogger(__name__)
        self._config = config  # _config는 이제 모든 설정(tr_ids, base_url 등)을 포함
        self._base_url = base_url  # 초기화 시 전달받은 base_url 사용
        self._headers = headers.copy()  # 초기화 시 전달받은 headers 복사하여 사용
        self._session = requests.Session()

        logging.getLogger('urllib3.connectionpool').setLevel(logging.WARNING)

    def _call_api(self, method, path, params=None, data=None):
        """API 호출을 위한 내부 헬퍼 메서드."""
        # _headers는 이미 __init__에서 common_headers_template의 복사본으로 설정됨.
        # 파생 클래스에서 tr_id 등 필요한 헤더를 _headers에 추가하고 이 메서드 호출.
        # 따라서 여기서 headers를 다시 구성할 필요 없음.

        url = f"{self._base_url}{path}"  # <--- __init__에서 저장된 _base_url 사용

        try:
            self.logger.debug("\nDEBUG: Headers being sent:")
            self.logger.debug(f"DEBUG: Checking _config in _call_api (tr_ids exists: {'tr_ids' in self._config})")
            self.logger.debug(f"DEBUG: _config keys in _call_api: {self._config.keys()}")

            for key, value in self._headers.items():  # <--- self._headers 사용
                try:
                    encoded_value = str(value).encode('latin-1', errors='ignore')
                    self.logger.debug(f"  {key}: {encoded_value}")
                except UnicodeEncodeError:
                    self.logger.debug(f"  {key}: *** UnicodeEncodeError - Contains non-latin-1 characters ***")
                    self.logger.debug(f"  Problematic value (type: {type(value)}): {repr(value)}")
            self.logger.debug("--- End Headers Debug ---")

            response = None
            if method.upper() == 'GET':
                response = self._session.get(url, headers=self._headers, params=params,
                                             verify=certifi.where())  # <--- self._headers 사용
            elif method.upper() == 'POST':
                response = self._session.post(url, headers=self._headers, data=json.dumps(data) if data else None,
                                              verify=certifi.where())  # <--- self._headers 사용
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
