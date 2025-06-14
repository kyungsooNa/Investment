# api/env.py
import requests
import json
import os
from datetime import datetime, timedelta
import certifi
import yaml  # 토큰 파일을 YAML 형식으로 읽고 쓰기 위해 필요

import logging
import http.client # http.client 모듈 임포트
# requests 로거 설정
logging.basicConfig(level=logging.DEBUG) # 또는 logging.INFO, WARNING 등
# http.client 로거 설정 (실제 HTTP 요청/응답 헤더와 바디를 보여줌)
http.client.HTTPConnection.debuglevel = 1

class KoreaInvestEnv:
    """
    한국투자증권 Open API 환경 설정을 관리하는 클래스입니다.
    API 키, 계좌 정보, 도메인 정보 등을 로드하고,
    API 요청에 필요한 기본 헤더를 생성합니다.
    토큰을 로컬 파일에 저장하고 재사용하는 기능을 포함합니다.
    """

    def __init__(self, config_data):
        self.config_data = config_data
        self._load_config()
        self._set_base_urls()

        # 토큰 파일 경로 설정 (본인 환경에 맞게 조정 가능)
        # 예제 kis_auth.py와 유사하게 현재 디렉토리에 KIS_YYYYMMDD.yaml 형태로 저장
        self._token_file_path = os.path.join(os.getcwd(), f'KIS_TOKEN_{datetime.today().strftime("%Y%m%d")}.yaml')

        # requests 세션 초기화
        self._session = requests.Session()

    def _load_config(self):
        """환경 설정 데이터를 로드합니다."""
        self.api_key = self.config_data.get('api_key')
        self.api_secret_key = self.config_data.get('api_secret_key')
        self.stock_account_number = self.config_data.get('stock_account_number')

        self.paper_api_key = self.config_data.get('paper_api_key')
        self.paper_api_secret_key = self.config_data.get(
            'paper_api_secret_key')  # <--- 수정: paper_secret_key로 변경해야 할 수도 있음. config.yaml 확인
        self.paper_stock_account_number = self.config_data.get('paper_stock_account_number')

        self.htsid = self.config_data.get('htsid')
        self.custtype = self.config_data.get('custtype', 'P')

        self.is_paper_trading = self.config_data.get('is_paper_trading', False)

        self.my_agent = self.config_data.get('my_agent',
                                             "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36")

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

    def _save_token_to_file(self, token_data):
        """토큰 정보를 로컬 파일에 저장합니다."""
        try:
            with open(self._token_file_path, 'w', encoding='utf-8') as f:
                yaml.dump(token_data, f)
            print(f"INFO: 토큰 정보를 파일에 저장했습니다: {self._token_file_path}")
        except Exception as e:
            print(f"ERROR: 토큰 파일 저장 실패: {e}")

    def _read_token_from_file(self):
        """로컬 파일에서 토큰 정보를 읽어옵니다."""
        try:
            if not os.path.exists(self._token_file_path):
                return None

            with open(self._token_file_path, 'r', encoding='utf-8') as f:
                token_data = yaml.safe_load(f)

            if token_data and 'token' in token_data and 'valid-date' in token_data:
                # 파일에 저장된 만료일자가 현재보다 미래인지 확인
                if token_data['valid-date'] > datetime.now():
                    return token_data
            return None
        except Exception as e:
            print(f"ERROR: 토큰 파일 읽기 실패: {e}")
            return None

    def get_access_token(self, force_new=False):
        """
        접근 토큰을 발급받거나 갱신합니다.
        토큰이 유효하면 기존 토큰을 반환하고, 아니면 새로 발급받습니다.
        force_new=True 시에는 유효 토큰이 있어도 새로 발급 시도합니다.
        """
        if not force_new:
            # 로컬 파일에서 유효한 토큰을 먼저 시도
            saved_token_data = self._read_token_from_file()
            if saved_token_data:
                self.access_token = saved_token_data['token']
                self.token_expired_at = saved_token_data['valid-date']
                print("INFO: 파일에서 기존 유효한 토큰 사용.")
                return self.access_token

            # 메모리에 있는 토큰이 유효한지 확인 (파일에 없었거나 만료된 경우)
            if self.access_token and self.token_expired_at and datetime.now() < self.token_expired_at:
                print("INFO: 메모리에서 기존 유효한 토큰 사용.")
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
            response = self._session.post(auth_url, headers=headers, data=json.dumps(body), verify=certifi.where())
            response.raise_for_status()
            auth_data = response.json()

            if auth_data and auth_data.get('access_token'):
                self.access_token = auth_data['access_token']
                # access_token_token_expired는 kis_auth.py 예제에서 사용
                # 일반적으로 expires_in으로 만료 초를 받음.
                expires_in = auth_data.get('expires_in', 0)
                # 만료 시간 설정 (예제처럼 정확한 datetime 문자열로 올 경우 해당 문자열 파싱)
                # 만료 60초 전 재발급을 위해 60초 뺌
                self.token_expired_at = datetime.now() + timedelta(seconds=expires_in - 60)

                # 새로 발급받은 토큰을 파일에 저장
                token_data_to_save = {
                    'token': self.access_token,
                    'valid-date': self.token_expired_at
                }
                self._save_token_to_file(token_data_to_save)

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