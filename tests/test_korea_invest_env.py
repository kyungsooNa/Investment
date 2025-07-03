# tests/test_korea_invest_env.py
import unittest
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
            "token_file_path": "test_token_env.json"  # 테스트용 토큰 파일 경로 추가
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

