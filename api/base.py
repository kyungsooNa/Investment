# api/base.py
import requests
import json
import certifi # SSL 인증서 검증을 위해 필요

class _KoreaInvestAPIBase:
    """
    모든 한국투자증권 API 호출 클래스가 공통적으로 사용할 기본 클래스입니다.
    requests.Session을 사용하여 연결 효율성을 높입니다.
    """
    def __init__(self, base_url, headers, config):
        self._base_url = base_url
        self._headers = headers.copy() # 헤더 복사하여 사용
        self._config = config
        self._session = requests.Session() # Session을 사용하여 연결 재활용

    def _call_api(self, method, path, params=None, data=None):
        """API 호출을 위한 내부 헬퍼 메서드."""
        url = f"{self._base_url}{path}"
        try:
            # DEBUG: Headers being sent:
            print("\nDEBUG: Headers being sent:")
            for key, value in self._headers.items():
                try:
                    encoded_value = str(value).encode('latin-1')
                    print(f"  {key}: {encoded_value}")
                except UnicodeEncodeError:
                    print(f"  {key}: *** UnicodeEncodeError - Contains non-latin-1 characters ***")
                    print(f"  Problematic value (type: {type(value)}): {repr(value)}")
            print("--- End Headers Debug ---")

            if method.upper() == 'GET':
                response = self._session.get(url, headers=self._headers, params=params, verify=certifi.where())
            elif method.upper() == 'POST':
                response = self._session.post(url, headers=self._headers, data=json.dumps(data) if data else None, verify=certifi.where())
            else:
                raise ValueError(f"지원하지 않는 HTTP 메서드: {method}")

            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as http_err:
            print(f"HTTP 오류 발생: {http_err.response.status_code} - {http_err.response.text}")
            return None
        except requests.exceptions.ConnectionError as conn_err:
            print(f"연결 오류 발생: {conn_err}")
            return None
        except requests.exceptions.Timeout as timeout_err:
            print(f"타임아웃 오류 발생: {timeout_err}")
            return None
        except requests.exceptions.RequestException as req_err:
            print(f"알 수 없는 요청 오류 발생: {req_err}")
            return None
        except json.JSONDecodeError:
            print(f"응답 JSON 디코딩 실패: {response.text}")
            return None