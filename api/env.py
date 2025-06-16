# api/env.py
import requests
import json
import os
from datetime import datetime, timedelta
import certifi
import yaml
import logging
import pytz  # pytz 임포트


class KoreaInvestEnv:
    """
    한국투자증권 Open API 환경 설정을 관리하는 클래스입니다.
    API 키, 계좌 정보, 도메인 정보 등을 로드하고,
    API 요청에 필요한 기본 헤더를 생성합니다.
    토큰을 로컬 파일에 저장하고 재사용하는 기능을 포함합니다.
    """

    def __init__(self, config_data, logger=None):
        self.config_data = config_data
        self.logger = logger if logger else logging.getLogger(__name__)
        self._load_config()
        self._set_base_urls()

        self._token_file_path = os.path.join(os.getcwd(), 'kis_access_token.yaml')

        self._session = requests.Session()

    def _load_config(self):
        self.api_key = self.config_data.get('api_key')
        self.api_secret_key = self.config_data.get('api_secret_key')
        self.stock_account_number = self.config_data.get('stock_account_number')

        self.paper_api_key = self.config_data.get('paper_api_key')
        self.paper_api_secret_key = self.config_data.get('paper_api_secret_key')
        self.paper_stock_account_number = self.config_data.get('paper_stock_account_number')

        self.htsid = self.config_data.get('htsid')
        self.custtype = self.config_data.get('custtype', 'P')

        self.is_paper_trading = self.config_data.get('is_paper_trading', False)

        self.my_agent = self.config_data.get('my_agent',
                                             "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36")

        self.access_token = None
        self.token_expired_at = None

    def _set_base_urls(self):
        if self.is_paper_trading:
            self.base_url = self.config_data.get('paper_url')
            self.websocket_url = self.config_data.get('paper_websocket_url')
            self.logger.info("모의투자 환경으로 설정되었습니다.")
        else:
            self.base_url = self.config_data.get('url')
            self.websocket_url = self.config_data.get('websocket_url')
            self.logger.info("실거래 환경으로 설정되었습니다.")

        if not self.base_url or not self.websocket_url:
            raise ValueError("API URL 또는 WebSocket URL이 config.yaml에 올바르게 설정되지 않았습니다.")

    def get_base_headers(self):
        headers = {
            "Content-Type": "application/json",
            "User-Agent": self.my_agent,
            "charset": "UTF-8"
        }
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        return headers

    def get_full_config(self):
        """
        현재 활성화된 환경(실거래/모의투자)에 맞는 API 키, 계좌 정보, URL 등을 반환합니다.
        tr_ids는 config_data에서 그대로 가져와 포함합니다.
        """
        active_api_key = self.paper_api_key if self.is_paper_trading else self.api_key
        active_api_secret_key = self.paper_api_secret_key if self.is_paper_trading else self.api_secret_key
        active_stock_account_number = self.paper_stock_account_number if self.is_paper_trading else self.stock_account_number
        active_base_url = self.base_url  # _set_base_urls에서 이미 설정된 base_url 사용
        active_websocket_url = self.websocket_url  # _set_base_urls에서 이미 설정된 websocket_url 사용

        # tr_ids는 config_data에 이미 병합되어 있으므로, 그 값을 그대로 포함
        tr_ids_from_config = self.config_data.get('tr_ids', {})  # tr_ids 키가 없을 경우 대비

        return {
            'api_key': active_api_key,
            'api_secret_key': active_api_secret_key,
            'stock_account_number': active_stock_account_number,
            'base_url': active_base_url,
            'websocket_url': active_websocket_url,
            'htsid': self.htsid,
            'custtype': self.custtype,
            'access_token': self.access_token,  # 현재 인스턴스에 저장된 토큰
            'token_expired_at': self.token_expired_at,  # 현재 인스턴스에 저장된 만료 시간
            'is_paper_trading': self.is_paper_trading,
            'tr_ids': tr_ids_from_config  # <--- tr_ids를 포함
        }

    def _get_auth_body(self):
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

    def _save_token_to_file(self, token, expires_at_str):
        """토큰 정보를 로컬 파일에 저장합니다."""
        try:
            token_data = {
                'token': token,
                'valid-date': expires_at_str
            }
            with open(self._token_file_path, 'w', encoding='utf-8') as f:
                yaml.dump(token_data, f)
            self.logger.info(f"토큰 정보를 파일에 저장했습니다: {self._token_file_path}")
        except Exception as e:
            self.logger.error(f"토큰 파일 저장 실패: {e}")

    def _read_token_from_file(self):
        """로컬 파일에서 토큰 정보를 읽어옵니다."""
        try:
            if not os.path.exists(self._token_file_path):
                return None

            with open(self._token_file_path, 'r', encoding='utf-8') as f:
                token_data = yaml.safe_load(f)

            if token_data and 'token' in token_data and 'valid-date' in token_data:
                token_valid_date_str = token_data['valid-date']
                try:
                    kst_timezone = pytz.timezone('Asia/Seoul')
                    token_valid_dt = kst_timezone.localize(datetime.strptime(token_valid_date_str, '%Y-%m-%d %H:%M:%S'))
                except ValueError as e:
                    self.logger.error(f"토큰 파일의 만료 시간 파싱 오류: {e}, 데이터: {token_valid_date_str}")
                    return None

                now_kst = datetime.now(pytz.timezone('Asia/Seoul'))

                if token_valid_dt > now_kst:
                    return {'token': token_data['token'], 'valid-date': token_valid_dt}
            return None
        except Exception as e:
            self.logger.error(f"토큰 파일 읽기 실패: {e}")
            return None

    def get_access_token(self, force_new=False):
        """
        접근 토큰을 발급받거나 갱신합니다.
        토큰이 유효하면 기존 토큰을 반환하고, 아니면 새로 발급받습니다.
        force_new=True 시에는 유효 토큰이 있어도 새로 발급 시도합니다.
        """
        if not force_new:
            saved_token_data = self._read_token_from_file()
            if saved_token_data:
                self.access_token = saved_token_data['token']
                self.token_expired_at = saved_token_data['valid-date']
                self.logger.info("파일에서 기존 유효한 토큰 사용.")
                return self.access_token

            if self.access_token and self.token_expired_at and datetime.now(
                    pytz.timezone('Asia/Seoul')) < self.token_expired_at:
                self.logger.info("메모리에서 기존 유효한 토큰 사용.")
                return self.access_token

        self.logger.info("새로운 접근 토큰 발급 시도...")
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
                expires_in = auth_data.get('expires_in', 0)

                kst_timezone = pytz.timezone('Asia/Seoul')
                token_issue_time = datetime.now(kst_timezone)
                self.token_expired_at = token_issue_time + timedelta(seconds=expires_in - 60)
                expires_at_str = self.token_expired_at.strftime('%Y-%m-%d %H:%M:%S')

                self._save_token_to_file(self.access_token, expires_at_str)

                self.logger.info(f"토큰 발급 성공. 만료 시간: {self.token_expired_at.strftime('%Y-%m-%d %H:%M:%S %Z%z')}")
                return self.access_token
            else:
                self.logger.error(f"토큰 발급 실패 - 응답 데이터 오류: {auth_data}")
                return None
        except requests.exceptions.RequestException as e:
            self.logger.error(f"토큰 발급 중 네트워크 오류: {e}")
            return None
        except json.JSONDecodeError:
            self.logger.error(f"토큰 발급 응답 JSON 디코딩 실패: {response.text}")
            return None
        except Exception as e:
            self.logger.error(f"토큰 발급 중 알 수 없는 오류: {e}")
            return None
