# api/client.py
from api.env import KoreaInvestEnv
from api.quotations import Quotations
from api.account import KoreaInvestAccountAPI
from api.trading import KoreaInvestTradingAPI
from api.websocket_client import WebSocketClient
import logging


class KoreaInvestAPI:
    """
    한국투자증권 Open API와 상호작용하는 메인 클라이언트입니다.
    각 도메인별 API 클래스를 통해 접근합니다.
    """

    def __init__(self, env: KoreaInvestEnv, logger=None):
        self._env = env
        self.logger = logger if logger else logging.getLogger(__name__)

        # _config는 env.get_full_config()를 통해 모든 설정(tr_ids 포함)을 가져옴
        self._config = self._env.get_full_config()

        # get_full_config가 access_token을 포함하도록 변경되었으므로 여기서 바로 확인
        if not self._config.get('access_token'):
            raise ValueError("접근 토큰이 없습니다. KoreaInvestEnv에서 먼저 토큰을 발급받아야 합니다.")

        # API 호출 시 사용할 기본 헤더 설정 (하위 클래스에 전달)
        # _env.get_base_headers()는 access_token 포함 여부만 판단하므로,
        # 여기서는 access_token이 확보된 후 _config에서 직접 값들을 가져와 헤더를 구성합니다.
        common_headers_template = {
            "Content-Type": "application/json",
            "User-Agent": self._env.my_agent,
            "charset": "UTF-8",
            "Authorization": f"Bearer {self._config['access_token']}",  # _config에서 access_token 사용
            "appkey": self._config['api_key'],  # _config에서 api_key 사용
            "appsecret": self._config['api_secret_key']  # _config에서 api_secret_key 사용
        }

        # 각 도메인별 API 클래스 인스턴스화
        # _config에서 바로 base_url을 가져와 전달
        self.quotations = Quotations(self._config['base_url'], common_headers_template, self._config,
                                                   self.logger)
        self.account = KoreaInvestAccountAPI(self._config['base_url'], common_headers_template, self._config,
                                             self.logger)
        self.trading = KoreaInvestTradingAPI(self._config['base_url'], common_headers_template, self._config,
                                             self.logger)

        self.websocket = WebSocketAPI(self._env, self.logger)

    def __str__(self):
        """객체를 문자열로 표현할 때 사용."""
        # _config에서 base_url과 is_paper_trading을 가져옴
        return f"KoreaInvestAPI(base_url={self._config['base_url']}, is_paper_trading={self._config['is_paper_trading']})"
