# api/api_client.py
from api.env import KoreaInvestEnv
from api.quotations import KoreaInvestQuotationsAPI
from api.account import KoreaInvestAccountAPI
from api.trading import KoreaInvestTradingAPI
from api.websocket_client import KoreaInvestWebSocketAPI  # <--- 파일명 변경됨
import logging


class KoreaInvestAPI:  # 클래스 이름은 유지 (외부에서 호출 편의성)
    """
    한국투자증권 Open API와 상호작용하는 메인 클라이언트입니다.
    각 도메인별 API 클래스를 통해 접근합니다.
    """

    def __init__(self, env: KoreaInvestEnv, logger=None):
        self._env = env
        self.logger = logger if logger else logging.getLogger(__name__)

        self._config = self._env.get_full_config()

        if not self._config.get('access_token'):
            raise ValueError("접근 토큰이 없습니다. KoreaInvestEnv에서 먼저 토큰을 발급받아야 합니다.")

        common_headers_template = {
            "Content-Type": "application/json",
            "User-Agent": self._env.my_agent,
            "charset": "UTF-8",
            "Authorization": f"Bearer {self._config['access_token']}",
            "appkey": self._config['api_key'],
            "appsecret": self._config['api_secret_key']
        }

        # REST API 도메인별 클래스 인스턴스화
        self.quotations = KoreaInvestQuotationsAPI(self._config['base_url'], common_headers_template, self._config,
                                                   self.logger)
        self.account = KoreaInvestAccountAPI(self._config['base_url'], common_headers_template, self._config,
                                             self.logger)
        self.trading = KoreaInvestTradingAPI(self._config['base_url'], common_headers_template, self._config,
                                             self.logger)

        # WebSocket API 클라이언트 인스턴스화
        self.websocket = KoreaInvestWebSocketAPI(self._env, self.logger)  # <--- 파일명 변경됨

    def __str__(self):
        return f"KoreaInvestAPI(base_url={self._config['base_url']}, is_paper_trading={self._config['is_paper_trading']})"
