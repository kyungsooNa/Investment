# api/client.py
from api.env import KoreaInvestEnv
from api.quotations import KoreaInvestQuotationsAPI
from api.account import KoreaInvestAccountAPI
from api.trading import KoreaInvestTradingAPI

class KoreaInvestAPI:
    """
    한국투자증권 Open API와 상호작용하는 메인 클라이언트입니다.
    각 도메인별 API 클래스를 통해 접근합니다.
    """
    def __init__(self, env: KoreaInvestEnv):
        self._env = env
        self._config = env.get_full_config()
        self._base_url = self._config['base_url']
        self._access_token = self._config['access_token']

        if not self._access_token:
            raise ValueError("접근 토큰이 없습니다. KoreaInvestEnv에서 먼저 토큰을 발급받아야 합니다.")

        # API 호출 시 사용할 기본 헤더 설정 (하위 클래스에 전달)
        common_headers = {
            "Content-Type": "application/json",
            "User-Agent": self._env.my_agent,
            "charset": "UTF-8",
            "Authorization": f"Bearer {self._access_token}",
            "appkey": self._config['api_key'],
            "appsecret": self._config['api_secret_key']
        }

        # 각 도메인별 API 클래스 인스턴스화
        self.quotations = KoreaInvestQuotationsAPI(self._base_url, common_headers, self._config)
        self.account = KoreaInvestAccountAPI(self._base_url, common_headers, self._config)
        self.trading = KoreaInvestTradingAPI(self._base_url, common_headers, self._config)

    def __str__(self):
        """객체를 문자열로 표현할 때 사용."""
        return f"KoreaInvestAPI(base_url={self._base_url}, is_paper_trading={self._config['is_paper_trading']})"