# api/env.py
import requests
import json
from datetime import datetime, timedelta
import certifi

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
        self.custtype = self.config_data.get('custtype', 'P')

        self.is_paper_trading = self.config_data.get('is_paper_trading', False)

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