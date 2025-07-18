# tests/test_korea_invest_env.py
import unittest
import os # os 모듈 추가 (테스트에 필요)
import logging # logging 모듈 추가 (테스트에 필요)
from datetime import datetime, timedelta
from unittest.mock import patch, AsyncMock, MagicMock
from brokers.korea_investment.korea_invest_env import KoreaInvestApiEnv
from brokers.korea_investment.korea_invest_token_manager import TokenManager  # TokenManager 임포트
import pytz


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
        # self.token_manager = TokenManager(token_file_path=self.mock_config_data['token_file_path']) # 실제 인스턴스 대신 Mock 객체 사용
        self.token_manager = MagicMock(spec=TokenManager)  # MagicMock으로 초기화
        # TokenManager의 내부 속성에 접근하는 테스트를 위해 필요 시 직접 설정
        self.token_manager._access_token = None
        self.token_manager._token_expired_at = None

        self.env = KoreaInvestApiEnv(self.mock_config_data, logger=self.logger,
                                     token_manager=self.token_manager)  # env에 token_manager 전달

        # 테스트 후 생성된 임시 토큰 파일 삭제 (TokenManager가 Mock이므로 직접 파일을 다루지 않음)
        # TokenManager Mock 객체는 실제 파일을 생성하지 않으므로, 이 부분은 제거하거나 TokenManager의 Mock에 파일 I/O를 모킹해야 합니다.
        # 이 테스트 클래스는 env의 동작을 테스트하므로, TokenManager의 파일 I/O는 TokenManager 테스트에서 다룹니다.
        # 따라서, 여기서는 파일 삭제 로직은 불필요합니다.
        # if os.path.exists(self.token_manager.token_file_path):
        #     os.remove(self.token_manager.token_file_path)

    def tearDown(self):
        # TokenManager가 Mock이므로 실제 파일을 삭제할 필요 없음
        pass
        # if os.path.exists(self.token_manager.token_file_path):
        #     os.remove(self.token_manager.token_file_path)

    async def test_init(self):
        self.assertEqual(self.env.api_key, "test_real_app_key")
        self.assertEqual(self.env.base_url, "https://real-api.com")
        self.assertFalse(self.env.is_paper_trading)
        self.assertIs(self.env.token_manager, self.token_manager)  # token_manager가 제대로 주입되었는지 확인

    async def test_set_trading_mode_to_paper(self):
        self.env.set_trading_mode(True)
        self.assertTrue(self.env.is_paper_trading)
        self.assertEqual(self.env.api_key, "test_real_app_key")  # config_data의 api_key는 변하지 않음
        self.assertEqual(self.env.base_url, "https://paper-api.com")  # base_url은 paper_url로 변경됨
        self.token_manager.invalidate_token.assert_called_once()  # invalidate_token이 호출되었는지 확인
        self.logger.info.assert_called_with("거래 모드가 모의투자 환경으로 변경되었습니다.")

    async def test_set_trading_mode_to_real(self):
        self.env.set_trading_mode(True)  # 먼저 모의투자로 변경
        self.token_manager.invalidate_token.reset_mock()  # 호출 횟수 초기화

        self.env.set_trading_mode(False)
        self.assertFalse(self.env.is_paper_trading)
        self.assertEqual(self.env.base_url, "https://real-api.com")
        self.token_manager.invalidate_token.assert_called_once()  # invalidate_token이 호출되었는지 확인
        self.logger.info.assert_called_with("거래 모드가 실전투자 환경으로 변경되었습니다.")

    async def test_get_base_headers(self):
        headers = self.env.get_base_headers()
        self.assertIn("Content-Type", headers)
        self.assertIn("User-Agent", headers)
        self.assertNotIn("Authorization", headers)  # Authorization 헤더는 이제 TokenManager를 통해 추가됨

    async def test_get_full_config_real_mode(self):
        # TokenManager의 내부 상태를 Mock으로 설정하여 get_full_config가 올바른 값을 반환하도록 함
        self.token_manager._access_token = "mock_access_token_real"
        self.token_manager._token_expired_at = datetime.now(pytz.timezone('Asia/Seoul')) + timedelta(
            hours=1)  # aware datetime

        full_config = self.env.get_full_config()
        self.assertEqual(full_config['api_key'], "test_real_app_key")
        self.assertEqual(full_config['base_url'], "https://real-api.com")
        self.assertFalse(full_config['is_paper_trading'])
        self.assertIn('tr_ids', full_config)
        self.assertIs(full_config['_env_instance'], self.env)

        self.assertEqual(full_config['access_token'], "mock_access_token_real")
        self.assertIsNotNone(full_config['token_expired_at'])
        # 반환된 token_expired_at이 aware datetime인지도 확인
        self.assertIsNotNone(full_config['token_expired_at'].tzinfo)

    async def test_get_full_config_paper_mode(self):
        self.env.set_trading_mode(True)
        # TokenManager의 내부 상태를 Mock으로 설정하여 get_full_config가 올바른 값을 반환하도록 함
        self.token_manager._access_token = "mock_access_token_paper"
        self.token_manager._token_expired_at = datetime.now(pytz.timezone('Asia/Seoul')) + timedelta(
            hours=1)  # aware datetime

        full_config = self.env.get_full_config()
        self.assertEqual(full_config['api_key'], "test_paper_app_key")
        self.assertEqual(full_config['base_url'], "https://paper-api.com")
        self.assertTrue(full_config['is_paper_trading'])

        self.assertEqual(full_config['access_token'], "mock_access_token_paper")
        self.assertIsNotNone(full_config['token_expired_at'])
        self.assertIsNotNone(full_config['token_expired_at'].tzinfo)

    # @patch.object(TokenManager, "get_access_token") # 이 데코레이터 제거
    async def test_get_access_token_delegates_to_token_manager(self):  # mock_token_manager_get_access_token 인자 제거
        """
        TC-X: KoreaInvestApiEnv.get_access_token이 TokenManager.get_access_token을 올바른 인자와 함께 호출하는지 검증
        """
        # self.token_manager는 이미 MagicMock(spec=TokenManager)이므로,
        # self.token_manager.get_access_token은 자동으로 AsyncMock처럼 동작합니다.
        self.token_manager.get_access_token.return_value = "delegated_token"

        # Act
        result = await self.env.get_access_token()

        # Assert
        self.assertEqual(result, "delegated_token")

        # get_access_token에 전달된 인자 확인
        self.token_manager.get_access_token.assert_called_once()
        call_args, call_kwargs = self.token_manager.get_access_token.call_args

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
        self.token_manager.get_access_token.reset_mock()  # mock 호출 횟수 초기화

        # Act (모의투자 환경)
        result_paper = await self.env.get_access_token()

        # Assert (모의투자 환경)
        self.assertEqual(result_paper, "delegated_token")
        self.token_manager.get_access_token.assert_called_once()
        call_args_paper, call_kwargs_paper = self.token_manager.get_access_token.call_args

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
        mock_token_manager = MagicMock(spec=TokenManager)
        mock_logger = MagicMock(spec=logging.Logger)

        # When & Then: ValueError가 발생하는지 검증
        with self.assertRaisesRegex(ValueError, "API URL 또는 WebSocket URL이 config.yaml에 올바르게 설정되지 않았습니다."):
            KoreaInvestApiEnv(invalid_config_data_real, mock_token_manager, mock_logger)
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
        mock_token_manager.reset_mock()  # mock 초기화
        mock_logger.reset_mock()
        with self.assertRaisesRegex(ValueError, "API URL 또는 WebSocket URL이 config.yaml에 올바르게 설정되지 않았습니다."):
            KoreaInvestApiEnv(invalid_config_data_paper, mock_token_manager, mock_logger)
        mock_logger.assert_not_called()

    # --- 79번 라인 커버: set_trading_mode에서 모드 변경이 없을 때 로깅 ---
    async def test_set_trading_mode_no_change_logging(self):
        """
        TC-3: set_trading_mode 호출 시 현재 모드와 동일한 모드로 설정할 때
              불필요한 작업 없이 로깅만 하는지 테스트합니다.
        이는 brokers/korea_investment/korea_invest_env.py의 79번 라인을 커버합니다.
        """
        # Given: 현재 env는 실전 투자 모드 (setUp에서 초기화됨)
        self.assertFalse(self.env.is_paper_trading)

        # _set_base_urls와 invalidate_token이 호출되지 않도록 Mock
        with patch.object(self.env, '_set_base_urls') as mock_set_base_urls, \
                patch.object(self.token_manager, 'invalidate_token') as mock_invalidate_token:
            # When: 현재와 동일한 실전 투자 모드로 다시 설정
            self.env.set_trading_mode(False)  # is_paper=False

            # Then:
            self.assertFalse(self.env.is_paper_trading)  # 모드 변경 없음
            mock_set_base_urls.assert_not_called()  # _set_base_urls 호출 안 됨
            mock_invalidate_token.assert_not_called()  # 토큰 무효화 호출 안 됨

            # 79번 라인의 info 로깅만 호출되었는지 확인
            self.logger.info.assert_called_once_with("거래 모드가 이미 실전투자 환경으로 설정되어 있습니다.")
            self.logger.info.reset_mock()  # 다음 테스트를 위해 Mock 초기화

            # When: 현재 실전 투자 모드에서 다시 실전 투자 모드로 설정 (다른 방법)
            self.env.set_trading_mode(False)
            self.logger.info.assert_called_once_with("거래 모드가 이미 실전투자 환경으로 설정되어 있습니다.")
