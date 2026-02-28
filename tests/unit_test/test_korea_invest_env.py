# tests/test_korea_invest_env.py
import unittest
import os # os 모듈 추가 (테스트에 필요)
import logging # logging 모듈 추가 (테스트에 필요)
from datetime import datetime, timedelta
from unittest.mock import patch, AsyncMock, MagicMock
from brokers.korea_investment.korea_invest_env import KoreaInvestApiEnv
from brokers.korea_investment.korea_invest_token_manager import TokenManager  # TokenManager 임포트
import pytz

def get_test_logger():
    logger = logging.getLogger("test_logger")
    logger.setLevel(logging.DEBUG)

    # 기존 핸들러 제거
    if logger.hasHandlers():
        logger.handlers.clear()

    # 콘솔 출력만 (파일 기록 없음)
    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(logging.DEBUG)
    formatter = logging.Formatter("%(levelname)s - %(message)s")
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    return logger

class TestKoreaInvestApiEnv(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.mock_config_data = {
            "api_key": "test_real_app_key",
            "api_secret_key": "test_real_app_secret",
            "stock_account_number": "test_real_account",
            "url": "https://real-api.com",
            "websocket_url": "wss://real-ws",
            "paper_api_key": "test_paper_app_key",
            "paper_api_secret_key": "test_paper_app_secret",
            "paper_stock_account_number": "test_paper_account",
            "paper_url": "https://paper-api.com",
            "paper_websocket_url": "wss://paper-ws",
            "htsid": "test_htsid",
            "tr_ids": {"TR_CODE_1": "value1"},
            "token_file_path": "test_token_env.json",  # 테스트용 토큰 파일 경로 추가
            "paths": "test_paths" ,
            "params": "test_params",
            "market_code": "test_market_code",
        }
        self.logger = MagicMock()
        # self._token_manager = TokenManager(token_file_path=self.mock_config_data['token_file_path']) # 실제 인스턴스 대신 Mock 객체 사용

        self.env = KoreaInvestApiEnv(self.mock_config_data, logger=self.logger)  # env에 token_manager 전달

        self.mock_token_manager_paper = MagicMock(spec=TokenManager)
        self.mock_token_manager_real = MagicMock(spec=TokenManager)
        self.mock_token_manager_paper.get_access_token = AsyncMock(return_value="delegated_token")
        self.mock_token_manager_real.get_access_token = AsyncMock(return_value="delegated_token")

        # 테스트 후 생성된 임시 토큰 파일 삭제 (TokenManager가 Mock이므로 직접 파일을 다루지 않음)
        # TokenManager Mock 객체는 실제 파일을 생성하지 않으므로, 이 부분은 제거하거나 TokenManager의 Mock에 파일 I/O를 모킹해야 합니다.
        # 이 테스트 클래스는 env의 동작을 테스트하므로, TokenManager의 파일 I/O는 TokenManager 테스트에서 다룹니다.
        # 따라서, 여기서는 파일 삭제 로직은 불필요합니다.
        # if os.path.exists(self._token_manager.token_file_path):
        #     os.remove(self._token_manager.token_file_path)

    def tearDown(self):
        # TokenManager가 Mock이므로 실제 파일을 삭제할 필요 없음
        pass
        # if os.path.exists(self._token_manager.token_file_path):
        #     os.remove(self._token_manager.token_file_path)

    async def test_init(self):
        self.assertEqual(self.env.api_key, "test_real_app_key")
        self.assertEqual(self.env.api_secret_key, "test_real_app_secret")
        self.assertEqual(self.env.stock_account_number, "test_real_account")
        self.assertEqual(self.env.paper_api_key, "test_paper_app_key")
        self.assertEqual(self.env.paper_api_secret_key, "test_paper_app_secret")
        self.assertEqual(self.env.paper_stock_account_number, "test_paper_account")
        self.assertEqual(self.env.is_paper_trading, None)
        self.assertEqual(self.env._base_url, None)
        self.assertEqual(self.env._websocket_url, None)
        self.assertEqual(self.env.active_config, None)

    async def test_set_trading_mode_to_paper(self):
        self.env.set_trading_mode(True)
        self.assertTrue(self.env.is_paper_trading)
        self.assertEqual(self.env.active_config['api_key'], "test_paper_app_key")
        self.assertEqual(self.env.active_config['base_url'], "https://paper-api.com")
        self.logger.info.assert_called_with("거래 모드가 모의투자 환경으로 변경되었습니다.")

    async def test_set_trading_mode_to_real(self):
        self.env.set_trading_mode(False)
        self.assertFalse(self.env.is_paper_trading)
        self.assertEqual(self.env.active_config['api_key'], "test_real_app_key")
        self.assertEqual(self.env.active_config['base_url'], "https://real-api.com")
        self.logger.info.assert_called_with("거래 모드가 실전투자 환경으로 변경되었습니다.")

    async def test_get_base_headers(self):
        headers = self.env.get_base_headers()
        self.assertIn("Content-Type", headers)
        self.assertIn("User-Agent", headers)
        self.assertNotIn("Authorization", headers)  # Authorization 헤더는 이제 TokenManager를 통해 추가됨

    async def test_get_full_config_paper_mode(self):
        self.env.set_trading_mode(True)
        # TokenManager의 내부 상태를 Mock으로 설정하여 get_full_config가 올바른 값을 반환하도록 함
        self.env._token_manager._access_token = "mock_access_token_paper"
        self.env._token_manager._token_expired_at = datetime.now(pytz.timezone('Asia/Seoul')) + timedelta(
            hours=1)  # aware datetime

        full_config = self.env.get_full_config()
        self.assertEqual(full_config['api_key'], "test_paper_app_key")
        self.assertEqual(full_config['base_url'], "https://paper-api.com")
        self.assertTrue(full_config['is_paper_trading'])


    async def test_get_access_token_delegates_to_token_manager(self):  # mock_token_manager_get_access_token 인자 제거
        """
        TC-X: KoreaInvestApiEnv.get_access_token이 TokenManager.get_access_token을 올바른 인자와 함께 호출하는지 검증
        """

        self.env.set_trading_mode(False)
        self.env._token_manager = self.mock_token_manager_real  # ⭐ 현재 사용중인 토큰 매니저 지정
        # Act
        result = await self.env.get_access_token()

        # Assert
        self.assertEqual(result, "delegated_token")

        # get_access_token에 전달된 인자 확인
        self.env._token_manager.get_access_token.assert_called_once()
        call_args, call_kwargs = self.env._token_manager.get_access_token.call_args

        # 인자가 키워드 인자로 전달되었는지 확인
        self.assertIn('base_url', call_kwargs)
        self.assertIn('app_key', call_kwargs)
        self.assertIn('app_secret', call_kwargs)

        # 올바른 값이 전달되었는지 확인 (실전 환경 기준)
        self.assertEqual(call_kwargs['base_url'], self.mock_config_data['url'])
        self.assertEqual(call_kwargs['app_key'], self.mock_config_data['api_key'])
        self.assertEqual(call_kwargs['app_secret'], self.mock_config_data['api_secret_key'])

        # 모의투자 환경으로 변경 후 다시 테스트
        self.env.set_trading_mode(True)
        self.env._token_manager = self.mock_token_manager_paper  # ⭐ 현재 사용중인 토큰 매니저 지정
        self.env._token_manager.get_access_token.reset_mock()  # mock 호출 횟수 초기화

        # Act (모의투자 환경)
        result_paper = await self.env.get_access_token()

        # Assert (모의투자 환경)
        self.assertEqual(result_paper, "delegated_token")
        self.env._token_manager.get_access_token.assert_called_once()
        call_args_paper, call_kwargs_paper = self.env._token_manager.get_access_token.call_args

        self.assertEqual(call_kwargs_paper['base_url'], self.mock_config_data['paper_url'])
        self.assertEqual(call_kwargs_paper['app_key'], self.mock_config_data['paper_api_key'])
        self.assertEqual(call_kwargs_paper['app_secret'], self.mock_config_data['paper_api_secret_key'])

    def test_set_base_urls_missing_url_raises_error(self):
        """
        TC-1: config_data에 필수 API URL (base_url 또는 websocket_url)이 누락되었을 때
              _set_base_urls 호출 시 ValueError가 발생하는지 테스트합니다.
        이는 brokers/korea_investment/korea_invest_env.py의 65번 라인을 커버합니다.
        """
        # Given: 'url'이 누락된 config_data (실전투자 모드)
        invalid_config_data_real = {
            "api_key": "real_key",
            "api_secret_key": "real_secret",
            "stock_account_number": "real_acc",
            # 'url' 키를 의도적으로 누락시켜 base_url이 None이 되도록 함
            "websocket_url": "wss://real.ws.com",
            "is_paper_trading": False,
            "tr_ids": {},
            "custtype": "P"
        }
        mock_logger = MagicMock(spec=get_test_logger())

        # When & Then: ValueError가 발생하는지 검증
        with self.assertRaisesRegex(ValueError, "API URL 또는 WebSocket URL이 config.yaml에 올바르게 설정되지 않았습니다."):
            KoreaInvestApiEnv(invalid_config_data_real, mock_logger)
        mock_logger.assert_not_called()  # 에러 발생 시 로거는 호출되지 않아야 함 (직접 raise이므로)

        # Given: 'paper_websocket_url'이 누락된 config_data (모의투자 모드)
        invalid_config_data_paper = {
            "api_key": "real_key",
            "api_secret_key": "real_secret",
            "stock_account_number": "real_acc",
            "paper_url": "https://paper.api.com",
            # 'paper_websocket_url' 키를 의도적으로 누락시켜 websocket_url이 None이 되도록 함
            "is_paper_trading": True,
            "tr_ids": {},
            "custtype": "P"
        }
        mock_logger.reset_mock()
        with self.assertRaisesRegex(ValueError, "API URL 또는 WebSocket URL이 config.yaml에 올바르게 설정되지 않았습니다."):
            KoreaInvestApiEnv(invalid_config_data_paper, mock_logger)
        mock_logger.assert_not_called()

    # --- 79번 라인 커버: set_trading_mode에서 모드 변경이 없을 때 로깅 ---
    async def test_set_trading_mode_no_change_logging(self):
        """
        TC-3: set_trading_mode 호출 시 현재 모드와 동일한 모드로 설정할 때
              불필요한 작업 없이 로깅만 하는지 테스트합니다.
        이는 brokers/korea_investment/korea_invest_env.py의 79번 라인을 커버합니다.
        """
        # Given: 현재 env는 실전 투자 모드 (setUp에서 초기화됨)
        self.env.set_trading_mode(False)
        self.assertFalse(self.env.is_paper_trading)
        self.logger.info.reset_mock()  # 다음 테스트를 위해 Mock 초기화

        # _set_base_urls와 invalidate_token이 호출되지 않도록 Mock
        with patch.object(self.env, '_set_base_urls') as mock_set_base_urls, \
                patch.object(self.env._token_manager, 'invalidate_token') as mock_invalidate_token:
            # When: 현재와 동일한 실전 투자 모드로 다시 설정
            self.env.set_trading_mode(False)  # is_paper=False

            # 79번 라인의 info 로깅만 호출되었는지 확인
            self.logger.info.assert_called_once_with("거래 모드가 이미 실전투자 환경으로 설정되어 있습니다.")
            self.logger.info.reset_mock()  # 다음 테스트를 위해 Mock 초기화

            # When: 현재 실전 투자 모드에서 다시 실전 투자 모드로 설정 (다른 방법)
            self.env.set_trading_mode(False)
            self.logger.info.assert_called_once_with("거래 모드가 이미 실전투자 환경으로 설정되어 있습니다.")

    async def test_get_access_token_no_manager_raises_error(self):
        """
        TC: TokenManager가 설정되지 않은 상태에서 get_access_token 호출 시 RuntimeError 발생 검증
        """
        self.env._token_manager = None
        with self.assertRaisesRegex(RuntimeError, "TokenManager가 초기화되지 않았습니다"):
            await self.env.get_access_token()

    def test_save_access_token_no_manager_raises_error(self):
        """
        TC: TokenManager가 설정되지 않은 상태에서 save_access_token 호출 시 RuntimeError 발생 검증
        """
        self.env._token_manager = None
        with self.assertRaisesRegex(RuntimeError, "TokenManager가 초기화되지 않았습니다"):
            self.env.save_access_token("token")

    def test_save_access_token_delegates(self):
        """
        TC: save_access_token 호출 시 TokenManager로 위임되는지 검증
        """
        self.env._token_manager = MagicMock()
        self.env.save_access_token("new_token")
        self.env._token_manager.save_access_token.assert_called_once_with("new_token")

    def test_invalidate_token_delegates(self):
        """
        TC: invalidate_token 호출 시 TokenManager로 위임되는지 검증
        """
        self.env._token_manager = MagicMock()
        self.env.invalidate_token()
        self.env._token_manager.invalidate_token.assert_called_once()

    def test_invalidate_token_no_manager(self):
        """
        TC: TokenManager가 없을 때 invalidate_token 호출 시 에러 없이 넘어가는지 검증
        """
        self.env._token_manager = None
        # 에러가 발생하지 않아야 함
        self.env.invalidate_token()

    async def test_refresh_token_delegates(self):
        """
        TC: refresh_token 호출 시 TokenManager로 위임되는지 검증
        """
        self.env.set_trading_mode(False) # active_config 설정을 위해
        self.env._token_manager = AsyncMock()
        
        await self.env.refresh_token()
        
        self.env._token_manager.refresh_token.assert_awaited_once()
        call_kwargs = self.env._token_manager.refresh_token.call_args.kwargs
        self.assertEqual(call_kwargs['base_url'], self.mock_config_data['url'])
        self.assertEqual(call_kwargs['app_key'], self.mock_config_data['api_key'])
        self.assertEqual(call_kwargs['app_secret'], self.mock_config_data['api_secret_key'])

    async def test_refresh_token_no_manager(self):
        """
        TC: TokenManager가 없을 때 refresh_token 호출 시 에러 없이 넘어가는지 검증
        """
        self.env._token_manager = None
        # 에러가 발생하지 않아야 함
        await self.env.refresh_token()

    def test_get_urls(self):
        """
        TC: get_base_url, get_websocket_url 메서드 검증
        """
        self.env.set_trading_mode(False)
        self.assertEqual(self.env.get_base_url(), self.mock_config_data['url'])
        self.assertEqual(self.env.get_websocket_url(), self.mock_config_data['websocket_url'])
        
        self.env.set_trading_mode(True)
        self.assertEqual(self.env.get_base_url(), self.mock_config_data['paper_url'])
        self.assertEqual(self.env.get_websocket_url(), self.mock_config_data['paper_websocket_url'])
