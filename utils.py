import os
import yaml
import requests
import json
import time
from datetime import datetime, timedelta
import certifi # <--- 이 줄을 추가합니다.

# CA_BUNDLE_PATH 정의를 삭제하거나 주석 처리합니다.
# CA_BUNDLE_PATH = os.path.join(os.path.dirname(__file__), "corporate_ca.pem")


class KoreaInvestEnv:
    """
    한국투자증권 Open API 환경 설정을 관리하는 클래스입니다.
    API 키, 계좌 정보, 도메인 정보 등을 로드하고,
    API 요청에 필요한 기본 헤더를 생성합니다.
    """
    def __init__(self, config_data):
        self.config_data = config_data
        self._load_config()
        self._set_base_urls()

    def _load_config(self):
        """환경 설정 데이터를 로드합니다."""
        self.api_key = self.config_data.get('api_key')
        self.api_secret_key = self.config_data.get('api_secret_key')
        self.stock_account_number = self.config_data.get('stock_account_number')

        self.paper_api_key = self.config_data.get('paper_api_key')
        self.paper_api_secret_key = self.config_data.get('paper_api_secret_key')
        self.paper_stock_account_number = self.config_data.get('paper_stock_account_number')

        self.htsid = self.config_data.get('htsid')
        self.custtype = self.config_data.get('custtype', 'P') # 기본값 개인

        self.is_paper_trading = self.config_data.get('is_paper_trading', False) # 기본값 실거래

        self.my_agent = self.config_data.get('my_agent', "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36")

        self.access_token = None
        self.token_expired_at = None

    def _set_base_urls(self):
        """실거래/모의투자에 따라 기본 URL을 설정합니다."""
        if self.is_paper_trading:
            self.base_url = self.config_data.get('paper_url')
            self.websocket_url = self.config_data.get('paper_websocket_url')
            print("INFO: 모의투자 환경으로 설정되었습니다.")
        else:
            self.base_url = self.config_data.get('url')
            self.websocket_url = self.config_data.get('websocket_url')
            print("INFO: 실거래 환경으로 설정되었습니다.")

        if not self.base_url or not self.websocket_url:
            raise ValueError("API URL 또는 WebSocket URL이 config.yaml에 올바르게 설정되지 않았습니다.")

    def get_base_headers(self):
        """API 요청 시 사용할 기본 헤더를 반환합니다."""
        headers = {
            "Content-Type": "application/json",
            "User-Agent": self.my_agent,
            "charset": "UTF-8"
        }
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        return headers

    def get_full_config(self):
        """현재 환경의 전체 설정(실거래/모의투자 구분된)을 딕셔너리로 반환합니다."""
        return {
            'api_key': self.paper_api_key if self.is_paper_trading else self.api_key,
            'api_secret_key': self.paper_api_secret_key if self.is_paper_trading else self.api_secret_key,
            'stock_account_number': self.paper_stock_account_number if self.is_paper_trading else self.stock_account_number,
            'base_url': self.base_url,
            'websocket_url': self.websocket_url,
            'htsid': self.htsid,
            'custtype': self.custtype,
            'access_token': self.access_token,
            'token_expired_at': self.token_expired_at,
            'is_paper_trading': self.is_paper_trading
        }

    def _get_auth_body(self):
        """인증 요청에 사용될 바디 데이터를 반환합니다."""
        if self.is_paper_trading:
            return {
                "grant_type": "client_credentials",
                "appkey": self.paper_api_key,
                "appsecret": self.paper_api_secret_key
            }
        else:
            return {
                "grant_type": "client_credentials",
                "appkey": self.api_key,
                "appsecret": self.api_secret_key
            }

    def get_access_token(self):
        """
        접근 토큰을 발급받거나 갱신합니다.
        토큰이 유효하면 기존 토큰을 반환하고, 아니면 새로 발급받습니다.
        """
        if self.access_token and self.token_expired_at and datetime.now() < self.token_expired_at:
            print("INFO: 기존 유효한 토큰 사용.")
            return self.access_token

        print("INFO: 새로운 접근 토큰 발급 시도...")
        auth_url = f"{self.base_url}/oauth2/tokenP"
        headers = {
            "Content-Type": "application/json",
            "User-Agent": self.my_agent,
            "charset": "UTF-8"
        }
        body = self._get_auth_body()

        try:
            # verify=CA_BUNDLE_PATH 대신 certifi.where() 사용
            response = requests.post(auth_url, headers=headers, data=json.dumps(body), verify=certifi.where())
            response.raise_for_status()

            auth_data = response.json()

            if auth_data and auth_data.get('access_token'):
                self.access_token = auth_data['access_token']
                expires_in = auth_data.get('expires_in', 0)
                self.token_expired_at = datetime.now() + timedelta(
                    seconds=expires_in - 60)
                print(f"INFO: 토큰 발급 성공. 만료 시간: {self.token_expired_at}")
                return self.access_token
            else:
                print(f"ERROR: 토큰 발급 실패 - 응답 데이터 오류: {auth_data}")
                return None
        except requests.exceptions.RequestException as e:
            print(f"ERROR: 토큰 발급 중 네트워크 오류: {e}")
            return None
        except json.JSONDecodeError:
            print(f"ERROR: 토큰 발급 응답 JSON 디코딩 실패: {response.text}")
            return None
        except Exception as e:
            print(f"ERROR: 토큰 발급 중 알 수 없는 오류: {e}")
            return None


class KoreaInvestAPI:
    """
    한국투자증권 Open API와 상호작용하는 핵심 클래스입니다.
    API 호출 메서드를 포함합니다.
    """
    def __init__(self, config, base_headers):
        self.config = config
        self.base_headers = base_headers
        self.base_url = config['base_url']
        self.access_token = config['access_token']

        if not self.access_token:
            raise ValueError("접근 토큰이 없습니다. KoreaInvestEnv에서 먼저 토큰을 발급받아야 합니다.")

        self.headers = self.base_headers.copy()
        self.headers["Authorization"] = f"Bearer {self.access_token}"
        self.headers["appkey"] = self.config['api_key']
        self.headers["appsecret"] = self.config['api_secret_key']


    def _call_api(self, method, path, params=None, data=None):
        """API 호출을 위한 내부 헬퍼 메서드."""
        url = f"{self.base_url}{path}"
        try:
            # --- 디버깅용 코드 추가 시작 ---
            print("\nDEBUG: Headers being sent:")
            for key, value in self.headers.items():
                try:
                    # 각 헤더 값을 latin-1로 인코딩 시도하여 문제 발생 여부 확인
                    encoded_value = str(value).encode('latin-1') # value가 숫자인 경우를 대비해 str() 추가
                    print(f"  {key}: {encoded_value}")
                except UnicodeEncodeError:
                    print(f"  {key}: *** UnicodeEncodeError - Contains non-latin-1 characters ***")
                    print(f"  Problematic value (type: {type(value)}): {repr(value)}") # 문제의 값 상세 출력
            print("--- End Headers Debug ---")
            # --- 디버깅용 코드 추가 끝 ---

            if method.upper() == 'GET':
                # verify=CA_BUNDLE_PATH 대신 certifi.where() 사용
                response = requests.get(url, headers=self.headers, params=params, verify=certifi.where())
            elif method.upper() == 'POST':
                # verify=CA_BUNDLE_PATH 대신 certifi.where() 사용
                response = requests.post(url, headers=self.headers, data=json.dumps(data) if data else None, verify=certifi.where())
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

    def get_current_price(self, stock_code):
        """
        주식 현재가를 조회하는 예시 메서드.
        """
        path = "/uapi/domestic-stock/v1/quotations/inquire-price"
        self.headers["tr_id"] = "FHKST01010100"
        self.headers["custtype"] = self.config['custtype']

        params = {
            "fid_cond_mrkt_div_code": "J",
            "fid_input_iscd": stock_code
        }
        print(f"INFO: {stock_code} 현재가 조회 시도...")
        return self._call_api('GET', path, params=params)

    def get_account_balance(self):
        """
        계좌 잔고를 조회하는 예시 메서드.
        (실제 API 명세에 따라 파라미터와 헤더 구성이 달라질 수 있습니다.)
        """
        path = "/uapi/domestic-stock/v1/trading/inquire-balance" # <-- 이 부분을 확인 및 수정!
        # TR ID를 모의투자용 'VTTC8434R'로 변경
        self.headers["tr_id"] = "VTTC8434R"
        self.headers["custtype"] = self.config['custtype']
        # --- 계좌번호 및 상품 코드 설정 ---
        # 제공해주신 8자리 계좌번호를 CANO로 직접 사용
        cano = self.config['stock_account_number']
        # ACNT_PRDT_DIV_CODE는 "01" (주식)으로 고정하여 시도
        acnt_prdt_div_code = "01" # <-- 이 부분을 "01"로 고정합니다.

        params = {
            'CANO': self.config['stock_account_number'], # config.yaml의 8자리 계좌번호 (예: "50139085")
            'ACNT_PRDT_CD': '01', # <-- 필드 이름 변경: ACNT_PRDT_DIV_CODE -> ACNT_PRDT_CD
            'AFHR_FLPR_YN': 'N',
            'FNCG_AMT_AUTO_RDPT_YN': 'N',
            'FUND_STTL_ICLD_YN': 'N',
            'INQR_DVSN': '01',
            'OFL_YN': 'N',
            'PRCS_DVSN': '01', # <-- 값 변경: "00" -> "01"
            'UNPR_DVSN': '01',
            'CTX_AREA_FK100': '',
            'CTX_AREA_NK100': ''
        }
        print(f"INFO: 계좌 잔고 조회 시도...")
        return self._call_api('GET', path, params=params)
    def __str__(self):
        """객체를 문자열로 표현할 때 사용."""
        return f"KoreaInvestAPI(base_url={self.base_url}, is_paper_trading={self.config['is_paper_trading']})"