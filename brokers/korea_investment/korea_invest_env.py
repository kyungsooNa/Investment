# brokers/korea_investment/korea_invest_env.py

import requests
import json
import os
from datetime import datetime, timedelta
import certifi
import yaml
import logging
import pytz
from brokers.korea_investment.korea_invest_token_manager import TokenManager # TokenManager 임포트


class KoreaInvestApiEnv:
    """
    한국투자증권 Open API 환경 설정을 관리하는 클래스입니다.
    API 키, 계좌 정보, 도메인 정보 등을 로드하고,
    API 요청에 필요한 기본 헤더를 생성합니다.
    토큰을 로컬 파일에 저장하고 재사용하는 기능을 포함합니다.
    """

    def __init__(self, config_data, token_manager: TokenManager = None, logger=None):
        self.config_data = config_data
        self.logger = logger if logger else logging.getLogger(__name__)
        self.token_manager = token_manager # TokenManager 인스턴스 저장

        self._load_config()

        self.base_url = None
        self.websocket_url = None
        self._set_base_urls()  # 초기 base_url, websocket_url 설정 (config.yaml의 is_paper_trading 기반)

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


    def _set_base_urls(self):
        """is_paper_trading 값에 따라 base_url과 websocket_url을 설정합니다."""
        if self.is_paper_trading:
            self.base_url = self.config_data.get('paper_url')
            self.websocket_url = self.config_data.get('paper_websocket_url')
        else:
            self.base_url = self.config_data.get('url')
            self.websocket_url = self.config_data.get('websocket_url')

        if not self.base_url or not self.websocket_url:
            raise ValueError("API URL 또는 WebSocket URL이 config.yaml에 올바르게 설정되지 않았습니다.")

    def set_trading_mode(self, is_paper: bool):
        """
        거래 모드(실전/모의)를 동적으로 변경합니다.
        :param is_paper: 모의투자 모드이면 True, 실전 투자 모드이면 False
        """
        if self.is_paper_trading != is_paper:
            self.is_paper_trading = is_paper
            # 모드 변경 시 base_url, websocket_url 재설정
            self._set_base_urls()
            self.token_manager.invalidate_token() # TokenManager를 통해 토큰 무효화
            self.logger.info(f"거래 모드가 {'모의투자' if is_paper else '실전투자'} 환경으로 변경되었습니다.")
        else:
            self.logger.info(f"거래 모드가 이미 {'모의투자' if is_paper else '실전투자'} 환경으로 설정되어 있습니다.")

    def get_base_headers(self):
        """API 요청 시 사용할 기본 헤더를 반환합니다."""
        headers = {
            "Content-Type": "application/json",
            "User-Agent": self.my_agent,
            "charset": "UTF-8"
        }
        return headers

    def get_full_config(self):
        """
        현재 활성화된 환경(실전/모의투자)에 맞는 API 키, 계좌 정보, URL 등을 반환합니다.
        tr_ids는 config_data에서 그대로 가져와 포함합니다.
        """
        active_api_key = self.paper_api_key if self.is_paper_trading else self.api_key
        active_api_secret_key = self.paper_api_secret_key if self.is_paper_trading else self.api_secret_key
        active_stock_account_number = self.paper_stock_account_number if self.is_paper_trading else self.stock_account_number
        active_base_url = self.base_url
        active_websocket_url = self.websocket_url

        tr_ids_from_config = self.config_data.get('tr_ids', {})

        # TokenManager에서 현재 활성 토큰과 만료 시간을 가져옴
        current_access_token = self.token_manager._access_token # 직접 접근 (혹은 TokenManager에 public getter 필요)
        current_token_expired_at = self.token_manager._token_expired_at # 직접 접근

        return {
            'api_key': active_api_key,
            'api_secret_key': active_api_secret_key,
            'stock_account_number': active_stock_account_number,
            'base_url': active_base_url,
            'websocket_url': active_websocket_url,
            'htsid': self.htsid,
            'custtype': self.custtype,
            'access_token': current_access_token,  # 현재 인스턴스에 저장된 토큰
            'token_expired_at': current_token_expired_at,  # 현재 인스턴스에 저장된 만료 시간
            'is_paper_trading': self.is_paper_trading,
            'tr_ids': tr_ids_from_config,
            'paths': self.config_data['paths'],
            'params': self.config_data['params'],
            'market_code': self.config_data['market_code'],
            '_env_instance': self  # <--- _env_instance는 KoreaInvestAPI로 전달하기 위해 여기에 추가
        }

    async def get_access_token(self, force_new=False):
        """
        접근 토큰을 발급받거나 갱신합니다.
        토큰 관리를 TokenManager에 위임합니다.
        """
        # TokenManager의 get_access_token을 호출하고 결과를 반환합니다.
        # TokenManager 내부에서 force_new 로직을 처리하므로, 여기서는 인자를 전달하지 않습니다.
        active_config = self.get_full_config()
        return await self.token_manager.get_access_token(
            base_url=active_config['base_url'],
            app_key=active_config['api_key'],
            app_secret=active_config['api_secret_key']
        )
