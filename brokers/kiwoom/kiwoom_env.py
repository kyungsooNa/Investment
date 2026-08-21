import logging

from brokers.kiwoom.kiwoom_token_provider import KiwoomTokenProvider


DEFAULT_REAL_BASE_URL = "https://api.kiwoom.com"
DEFAULT_PAPER_BASE_URL = "https://mockapi.kiwoom.com"
DEFAULT_REAL_WEBSOCKET_URL = "wss://api.kiwoom.com:10000"
DEFAULT_PAPER_WEBSOCKET_URL = "wss://mockapi.kiwoom.com:10000"


class KiwoomApiEnv:
    """키움증권 REST API 환경 설정과 토큰 제공자를 관리합니다."""

    def __init__(self, config_data: dict, logger=None):
        self._logger = logger if logger else logging.getLogger(__name__)
        self._config_data = config_data.get("kiwoom", config_data)
        self.is_paper_trading = bool(self._config_data.get("is_paper_trading", True))
        self._token_provider = None
        self._token_provider_real = KiwoomTokenProvider(
            token_file_path=self._token_path("real"),
            logger=self._logger,
        )
        self._token_provider_paper = KiwoomTokenProvider(
            token_file_path=self._token_path("paper"),
            logger=self._logger,
        )
        self._base_url = None
        self._websocket_url = None
        self.active_config = None
        self.set_trading_mode(self.is_paper_trading, force=True)

    def _mode_config(self, mode: str) -> dict:
        return self._config_data.get(mode, {}) or {}

    def _token_path(self, mode: str) -> str:
        token_config = self._config_data.get("token", {}) or {}
        if mode == "paper":
            return token_config.get("paper_path") or token_config.get("path") or "config/token_kiwoom_paper.json"
        return token_config.get("real_path") or "config/token_kiwoom_real.json"

    def _active_mode(self) -> str:
        return "paper" if self.is_paper_trading else "real"

    def _default_base_url(self, mode: str) -> str:
        return DEFAULT_PAPER_BASE_URL if mode == "paper" else DEFAULT_REAL_BASE_URL

    def _default_websocket_url(self, mode: str) -> str:
        return DEFAULT_PAPER_WEBSOCKET_URL if mode == "paper" else DEFAULT_REAL_WEBSOCKET_URL

    def _require(self, mode_config: dict, key: str, mode: str) -> str:
        value = mode_config.get(key)
        if value in (None, ""):
            raise ValueError(f"kiwoom.{mode}.{key} 설정이 필요합니다.")
        return value

    def set_trading_mode(self, is_paper: bool, force: bool = False):
        if not force and self.is_paper_trading is is_paper:
            return

        self.is_paper_trading = is_paper
        mode = self._active_mode()
        mode_config = self._mode_config(mode)
        self._base_url = mode_config.get("base_url") or self._default_base_url(mode)
        self._websocket_url = mode_config.get("websocket_url") or self._default_websocket_url(mode)
        self._token_provider = self._token_provider_paper if is_paper else self._token_provider_real
        self.active_config = self.get_full_config()

    def get_base_headers(self) -> dict:
        return {
            "Content-Type": "application/json;charset=UTF-8",
        }

    def get_full_config(self) -> dict:
        mode = self._active_mode()
        mode_config = self._mode_config(mode)
        return {
            "api_key": self._require(mode_config, "app_key", mode),
            "api_secret_key": self._require(mode_config, "app_secret", mode),
            "stock_account_number": self._require(mode_config, "account_no", mode),
            "base_url": self._base_url,
            "websocket_url": self._websocket_url,
            "is_paper_trading": self.is_paper_trading,
            "_env_instance": self,
        }

    async def get_access_token(self, force_new: bool = False) -> str:
        if not self._token_provider:
            raise RuntimeError("KiwoomTokenProvider가 초기화되지 않았습니다.")
        if force_new:
            self._token_provider.invalidate_token()
        return await self._token_provider.get_access_token(
            base_url=self.active_config["base_url"],
            app_key=self.active_config["api_key"],
            app_secret=self.active_config["api_secret_key"],
        )

    async def refresh_token(self):
        if not self._token_provider:
            raise RuntimeError("KiwoomTokenProvider가 초기화되지 않았습니다.")
        await self._token_provider.refresh_token(
            base_url=self.active_config["base_url"],
            app_key=self.active_config["api_key"],
            app_secret=self.active_config["api_secret_key"],
        )

    def invalidate_token(self):
        if self._token_provider:
            self._token_provider.invalidate_token()

    def get_base_url(self) -> str:
        return self._base_url

    def get_websocket_url(self) -> str:
        return self._websocket_url
