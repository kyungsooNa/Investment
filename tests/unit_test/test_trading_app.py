# test/test_trading_app.py

import pytest
import logging
import sys
import os  # os 모듈 추가
import unittest
import inspect
from unittest.mock import patch, call, AsyncMock, MagicMock
from datetime import datetime
from common.types import ResCommonResponse, ErrorCode, ResTopMarketCapApiItem
from app.trading_app import TradingApp
from app.user_action_executor import UserActionExecutor


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

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

try:
    import services.momentum_strategy
    import services.strategy_executor
    from app import trading_app
except ImportError as e:
    # Fallback for environments where direct import might not work
    # but the classes are defined in the mock section below.
    # Log the error for debugging if needed, but don't stop execution
    # as mocks will be used.
    logger = get_test_logger()
    logger.warning(f"Could not import a module for testing: {e}. Proceeding with mocks.")


# 각 테스트를 위한 목(mock) TradingApp 인스턴스 설정 픽스처
@pytest.fixture
def setup_mock_app(mocker):
    mock_config = get_mock_config()

    for cls in [
        TokenManager,
        KoreaInvestApiEnv,
        TimeManager,
        Logger,
        CLIView,
        KoreaInvestApiClient,
        TradingService,
        StockQueryService,
        OrderExecutionService,
        BrokerAPIWrapper,
        BacktestDataProvider
    ]:
        mocker.patch(f"{cls.__module__}.{cls.__name__}")
    app = TradingApp(logger=MagicMock())

    app.cli_view = mocker.MagicMock(spec=CLIView)  # AsyncMock 대신 MagicMock 사용
    app.cli_view.select_environment_input = AsyncMock()
    app.cli_view.display_invalid_input_warning = MagicMock()
    app.cli_view.display_strategy_running_message = MagicMock()
    app.cli_view.display_strategy_results = MagicMock()
    app.cli_view.display_strategy_error = MagicMock()
    app.cli_view.display_app_start_error = MagicMock()
    app.cli_view.display_gapup_pullback_selected_stocks = MagicMock()
    app.cli_view.display_gapup_pullback_rejected_stocks = MagicMock()
    app.cli_view.display_token_invalidated_message = MagicMock()
    app.cli_view.display_account_balance_failure = MagicMock()
    app.cli_view.display_invalid_menu_choice = MagicMock()
    app.cli_view.display_top_stocks_failure = MagicMock()
    app.cli_view.display_top_stocks_success = MagicMock()
    app.cli_view.display_follow_through_stocks = MagicMock()
    app.cli_view.display_not_follow_through_stocks = MagicMock()
    app.cli_view.display_account_balance = MagicMock()
    app.cli_view.display_exit_message = MagicMock()
    app.cli_view.display_warning_paper_trading_not_supported = MagicMock()
    app.cli_view.display_warning_strategy_market_closed = MagicMock()
    app.cli_view.display_no_stocks_for_strategy = MagicMock()
    app.cli_view.display_current_stock_price = MagicMock()
    app.cli_view.display_current_stock_price_error = MagicMock()
    app.cli_view.display_order_success = MagicMock()
    app.cli_view.display_order_failure = MagicMock()
    app.cli_view.display_stock_change_rate_success = MagicMock()
    app.cli_view.display_stock_change_rate_failure = MagicMock()
    app.cli_view.display_stock_vs_open_price_success = MagicMock()
    app.cli_view.display_stock_vs_open_price_failure = MagicMock()
    app.cli_view.display_top_market_cap_stocks_success = MagicMock()
    app.cli_view.display_top_market_cap_stocks_failure = MagicMock()
    app.cli_view.display_top10_market_cap_prices_success = MagicMock()
    app.cli_view.display_top10_market_cap_prices_failure = MagicMock()
    app.cli_view.display_upper_limit_stocks_success = MagicMock()
    app.cli_view.display_upper_limit_stocks_failure = MagicMock()


    app.cli_view.get_user_input = AsyncMock()  # 이 줄은 유지

    app.time_manager = mocker.MagicMock(spec=TimeManager)
    app.time_manager.is_market_open = MagicMock(return_value=True)  # 기본값은 True로 설정

    app.env = mocker.MagicMock(spec=KoreaInvestApiEnv)
    app.env.get_access_token = mocker.AsyncMock(return_value="mock_access_token")  # 명시적으로 AsyncMock으로 설정
    app.env.invalidate_token = mocker.MagicMock()  # 명시적으로 AsyncMock으로 설정
    app.env.get_full_config = mocker.MagicMock(return_value=mock_config)
    app.env.set_trading_mode = MagicMock()  # set_trading_mode 메서드 명시적 목킹 추가
    app.env.is_paper_trading = False

    # app._complete_api_initialization = AsyncMock(return_value=True) # 이 줄은 테스트에서 필요에 따라 개별적으로 목킹
    # app.select_environment = AsyncMock(return_value=True) # 이 줄은 테스트에서 필요에 따라 개별적으로 목킹

    app.order_execution_service = mocker.AsyncMock(spec=OrderExecutionService)
    app.broker = mocker.AsyncMock(spec=BrokerAPIWrapper)
    app.backtest_data_provider = mocker.AsyncMock(spec=BacktestDataProvider)


    app.stock_query_service = mocker.AsyncMock(spec=StockQueryService)
    app.stock_query_service.handle_get_top_10_market_cap_stocks_with_prices = AsyncMock()
    app.stock_query_service.handle_get_top_market_cap_stocks_code = AsyncMock()
    app.stock_query_service.handle_upper_limit_stocks = AsyncMock()
    app.stock_query_service.handle_get_current_stock_price = AsyncMock()
    app.stock_query_service.get_stock_change_rate = AsyncMock()
    app.stock_query_service.get_open_vs_current = AsyncMock()
    app.stock_query_service.handle_realtime_price_quote_stream = AsyncMock()
    app.stock_query_service.handle_get_asking_price = AsyncMock()
    app.stock_query_service.handle_current_upper_limit_stocks = AsyncMock()
    app.stock_query_service.handle_realtime_stream = AsyncMock()
    app.stock_query_service.handle_get_account_balance = AsyncMock()

    app.trading_service = mocker.AsyncMock(spec=TradingService)
    app.trading_service.get_code_by_name = AsyncMock()
    app.trading_service.get_top_market_cap_stocks_code = AsyncMock()
    app.trading_service.get_price_summary = AsyncMock()
    app.trading_service.get_account_balance = AsyncMock()

    mocker.patch('strategies.momentum_strategy.MomentumStrategy')
    mocker.patch('strategies.strategy_executor.StrategyExecutor')
    mocker.patch('strategies.GapUpPullback_strategy.GapUpPullbackStrategy')  # 이 클래스 자체를 목킹

    app.token_manager = MagicMock()
    app.token_manager.invalidate_token = MagicMock()

    app.order_execution_service.handle_sell_stock = AsyncMock()
    app.order_execution_service.handle_buy_stock = AsyncMock()

    yield app


def get_mock_config():
    """
    테스트를 위해 필요한 모든 URL 및 TR ID를 포함하는 목 설정 딕셔너리를 반환합니다.
    """
    return {
        'is_paper_trading': True,
        'paper_url': 'https://mock-paper-api.koreainvestment.com:443',
        'paper_websocket_url': 'ws://mock-paper-websocket.koreainvestment.com:80',
        'url': 'https://mock-real-api.koreainvestment.com:443',
        'websocket_url': 'ws://mock-real-websocket.koreainvestment.com:80',
        'app_key': 'mock_app_key',
        'app_secret_key': 'mock_app_secret',
        "paper_api_key": "mock-paper-key",
        "paper_api_secret_key": "mock-paper-secret",
        'account_number': 'mock_account_number',  # 일반 계좌 번호
        'stock_account_number': 'mock_stock_account_number',  # 특정 주식 계좌 번호
        'hts_id': 'mock_hts_id',
        'custtype': 'P',  # 개인 고객 유형
        'market_open_time': "09:00",
        'market_close_time': "15:30",
        'market_timezone': "Asia/Seoul",
        'token_file_path': 'config/token.json',
        'tr_ids': {
            'quotations': {
                'inquire_price': 'FHKST01010100',
                'search_info': 'FHKST01010500',
                'market_cap': 'FHPST01740000',
                'daily_itemchartprice_day': 'FHKST03010100',
                'daily_itemchartprice_minute': 'FHNKF03060000'
            },
            'account': {
                'inquire_balance_real': 'TTTC8434R',
                'inquire_balance_paper': 'VTTC8434R'
            },
            'trading': {
                'order_cash_buy_real': 'TTTC0012U',
                'order_cash_sell_real': 'TTTC0011U',
                'order_cash_buy_paper': 'VTTC0012U',
                'order_cash_sell_paper': 'VTTC0011U'
            },
            'websocket': {
                'approval_key': '실시간-000',
                'realtime_price': 'H0STCNT0',
                'realtime_quote': 'H0STASP0'
            }
        },
        "paths": {  # ✅ 반드시 포함
            "inquire_price": "/mock/inquire-price"
        },
        "params": {
            "fid_div_cls_code": 2,
            "screening_code": "20174"
        },
        "market_code": "J",
    }


class TokenManager:
    pass


class KoreaInvestApiEnv:
    def __init__(self, config_data, token_manager, logger):
        self.config_data = config_data
        self.is_paper_trading = config_data.get('is_paper_trading', False)
        self._set_base_urls()

    def _set_base_urls(self):
        if self.is_paper_trading:
            self.base_url = self.config_data.get('paper_url')
            self.websocket_url = self.config_data.get('paper_websocket_url')
        else:
            self.base_url = self.config_data.get('url')
            self.websocket_url = self.config_data.get('websocket_url')
        if not self.base_url or not self.websocket_url:
            raise ValueError("API URL 또는 WebSocket URL이 config.yaml에 올바르게 설정되지 않았습니다.")


class OrderExecutionService:
    pass


class TimeManager:
    def is_market_open(self):  # is_market_open 메서드 추가
        pass


class KoreaInvestApiClient:
    pass


class TradingService:
    async def get_account_balance(self):
        pass

    async def get_current_stock_price(self, stock_code):
        pass

    async def get_top_market_cap_stocks_code(self, stock_code):
        pass


class StockQueryService:
    def __init__(self, trading_service, cli_view, logger):
        self.trading_service = trading_service
        self.cli_view = cli_view
        self.logger = logger

    async def handle_get_current_stock_price(self, stock_code):
        self.cli_view.display_message(f"현재가 조회: {stock_code}")
        result = await self.trading_service.get_current_stock_price(stock_code)
        if result:
            self.cli_view.display_message(f"조회 결과: {result}")
            return True
        return False

    async def handle_get_account_balance(self):  # handle_get_account_balance 메서드 추가
        self.cli_view.display_message("계좌 잔고를 조회합니다.")
        # 실제 로직에서는 trading_service를 호출할 것입니다.
        # 여기서는 Mock된 trading_service를 사용합니다.
        result = await self.trading_service.get_account_balance()
        if result:
            self.cli_view.display_message(f"계좌 잔고 조회 결과: {result}")
            return True
        return False

    async def handle_display_stock_vs_open_price(self, stock_code: str):  # handle_display_stock_vs_open_price 메서드 추가
        self.cli_view.display_message(f"시가대비 등락률 조회: {stock_code}")
        # 실제 로직에서는 trading_service를 호출할 것입니다.
        # 여기서는 Mock된 trading_service를 사용합니다.
        result = await self.trading_service.get_current_stock_price(stock_code)  # 예시로 현재가 조회 사용
        if result:
            self.cli_view.display_message(f"시가대비 조회 결과: {result}")
            return True
        return False

    async def handle_display_stock_change_rate(self, stock_code: str):  # handle_display_stock_change_rate 메서드 추가
        self.cli_view.display_message(f"전일대비 등락률 조회: {stock_code}")
        # 실제 로직에서는 trading_service를 호출할 것입니다.
        # 여기서는 Mock된 trading_service를 사용합니다.
        result = await self.trading_service.get_current_stock_price(stock_code)  # 예시로 현재가 조회 사용
        if result:
            self.cli_view.display_message(f"전일대비 조회 결과: {result}")
            return True

    async def handle_get_top_market_cap_stocks(self, stock_code):
        pass


class CLIView:
    def display_message(self, message):
        print(message)

    def display_exit_message(self):
        print("애플리케이션을 종료합니다.")

    async def get_user_input(self, prompt):  # get_user_input 추가 (Mocking될 것임)
        return "mock_input"  # 테스트를 위해 기본값 반환

    async def select_environment_input(self):  # select_environment_input 메서드 추가
        print("\n--- 거래 환경 선택 ---")
        print("1. 모의투자")
        print("2. 실전투자")
        print("-----------------------")

    def display_strategy_running_message(self, strategy_name: str):  # display_strategy_running_message 추가
        print(f"\n--- {strategy_name} 전략 실행 시작 ---")

    def display_top_stocks_success(self):  # display_top_stocks_success 추가
        print("시가총액 상위 종목 조회 완료.")

    def display_strategy_results(self, strategy_name: str, results: dict):  # display_strategy_results 추가
        print(f"\n--- {strategy_name} 전략 실행 결과 ---")
        print(f"총 처리 종목: {results.get('total_processed', 0)}개")
        print(f"매수 시도 종목: {results.get('buy_attempts', 0)}개")
        print(f"매수 성공 종목: {results.get('buy_successes', 0)}개")
        print(f"매도 시도 종목: {results.get('sell_attempts', 0)}개")
        print(f"매도 성공 종목: {results.get('sell_successes', 0)}개")
        execution_time_value = results.get('execution_time', 0.0)
        if not isinstance(execution_time_value, (int, float)):
            execution_time_value = 0.0
        print(f"전략 실행 시간: {execution_time_value:.2f}초")
        print("---------------------------------")

    def display_follow_through_stocks(self, stocks: list):  # display_follow_through_stocks 추가
        print("✔️ Follow Through 종목:")
        if stocks:
            for s in stocks:
                if isinstance(s, dict):
                    print(f" - {s.get('name', 'N/A')}({s.get('code', 'N/A')})")
                else:
                    print(f" - {s}")
        else:
            print("   없음")

    def display_not_follow_through_stocks(self, stocks: list):  # display_not_follow_through_stocks 추가
        print("❌ Follow 실패 종목:")
        if stocks:
            for s in stocks:
                if isinstance(s, dict):
                    print(f" - {s.get('name', 'N/A')}({s.get('code', 'N/A')})")
                else:
                    print(f" - {s}")
        else:
            print("   없음")

    def display_warning_strategy_market_closed(self):  # display_warning_strategy_market_closed 추가
        print("⚠️ 시장이 폐장 상태이므로 전략을 실행할 수 없습니다.")

    def display_top_stocks_failure(self, message: str):  # display_top_stocks_failure 추가
        print(f"시가총액 상위 종목 조회 실패: {message}")

    def display_no_stocks_for_strategy(self):  # display_no_stocks_for_strategy 추가
        print("전략을 실행할 종목이 없습니다.")


class BrokerAPIWrapper:
    pass


class BacktestDataProvider:  # BacktestDataProvider 스펙 추가
    pass


class Logger:
    def info(self, message):
        pass

    def error(self, message):
        pass


class MomentumStrategy:  # MomentumStrategy 스펙 추가
    pass


class StrategyExecutor:  # StrategyExecutor 스펙 추가
    async def execute(self, stock_codes):
        pass

@pytest.mark.asyncio
async def test_execute_action_1_get_current_price(setup_mock_app):
    """메뉴 '1' 선택 시 stock_query_service.handle_get_current_stock_price가 호출되는지 테스트합니다."""
    # --- Arrange (준비) ---
    app = setup_mock_app

    # _execute_action('1') 내부에서 호출되는 get_user_input의 반환값을 설정합니다.
    # 이렇게 하면 실제 input() 함수가 호출되는 것을 막고 OSError를 방지합니다.
    app.cli_view.get_user_input.return_value = "005930"

    # --- Act (실행) ---
    # 실제 _execute_action 메서드를 호출합니다.
    executor = UserActionExecutor(app)
    result = await executor.execute("1")

    # --- Assert (검증) ---
    # 1. 사용자에게 종목 코드를 요청했는지 확인합니다.
    app.cli_view.get_user_input.assert_awaited_once_with("조회할 종목 코드를 입력하세요 (삼성전자: 005930): ")

    # 2. stock_query_service의 핸들러가 올바른 종목 코드로 호출되었는지 확인합니다.
    app.stock_query_service.handle_get_current_stock_price.assert_awaited_once_with("005930")


@pytest.mark.asyncio
async def test_execute_action_2_get_account_balance(setup_mock_app):
    """메뉴 '2' 선택 시 trading_service.get_account_balance가 호출되고, 성공 시 결과가 표시되는지 테스트합니다."""
    # --- Arrange (준비) ---
    app = setup_mock_app

    # trading_service가 성공 응답을 반환하도록 설정
    mock_balance_data = {"dnca_tot_amt": "1000000", "tot_evlu_amt": "1200000"}
    app.stock_query_service.handle_get_account_balance.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value,
        msg1="정상",
        data=mock_balance_data
    )

    # --- Act (실행) ---
    # 실제 _execute_action 메서드를 호출합니다.
    executor = UserActionExecutor(app)
    result = await executor.execute("2")
    
    # --- Assert (검증) ---
    # 1. trading_service의 메서드가 호출되었는지 확인합니다.
    app.stock_query_service.handle_get_account_balance.assert_awaited_once()

    # 2. 성공 시 cli_view의 display_account_balance가 올바른 데이터로 호출되었는지 확인합니다.
    app.cli_view.display_account_balance.assert_called_once_with(mock_balance_data)


@pytest.mark.asyncio
async def test_execute_action_2_account_balance_success(setup_mock_app):
    """메뉴 '2' 선택 시 계좌 잔고 조회가 성공하고 결과가 표시되는지 테스트합니다."""
    # --- Arrange (준비) ---
    app = setup_mock_app

    # trading_service가 성공 응답을 반환하도록 설정
    mock_balance_data = {"dnca_tot_amt": "1000000"}
    app.stock_query_service.handle_get_account_balance.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value,
        msg1="정상",
        data=mock_balance_data
    )

    # --- Act (실행) ---
    # 실제 _execute_action 메서드를 호출합니다.
    executor = UserActionExecutor(app)
    result = await executor.execute('2')

    # --- Assert (검증) ---
    # trading_service의 메서드가 호출되었는지 확인
    app.stock_query_service.handle_get_account_balance.assert_awaited_once()
    # 성공 시 cli_view의 display_account_balance가 호출되었는지 확인
    app.cli_view.display_account_balance.assert_called_once_with(mock_balance_data)
    # 실패 메시지 메서드는 호출되지 않았는지 확인
    app.cli_view.display_account_balance_failure.assert_not_called()


@pytest.mark.asyncio
async def test_execute_action_3_place_buy_order(setup_mock_app):
    """메뉴 '3' 선택 시 order_execution_service.handle_buy_stock이 호출되는지 테스트합니다."""
    # --- Arrange (준비) ---
    app = setup_mock_app

    # _execute_action('3') 내부에서 순서대로 요청될 사용자 입력을 미리 설정합니다.
    # 이렇게 하면 실제 input() 함수 호출을 막아 OSError를 방지합니다.
    app.cli_view.get_user_input.side_effect = ["005930", "10", "80000"]

    # --- Act (실행) ---
    # 실제 _execute_action 메서드를 호출합니다.
    executor = UserActionExecutor(app)
    result = await executor.execute('3')

    # --- Assert (검증) ---
    # 1. 사용자에게 종목코드, 수량, 가격을 순서대로 요청했는지 확인합니다.
    expected_calls = [
        call("매수할 종목 코드를 입력하세요: "),
        call("매수할 수량을 입력하세요: "),
        call("매수 가격을 입력하세요 (시장가: 0): ")
    ]
    app.cli_view.get_user_input.assert_has_calls(expected_calls)

    # 2. order_execution_service의 핸들러가 올바른 인자들로 호출되었는지 확인합니다.
    app.order_execution_service.handle_buy_stock.assert_awaited_once_with("005930", "10", "80000")

@pytest.mark.asyncio
async def test_execute_action_place_buy_order(setup_mock_app):
    """메뉴 '3' 선택 시 order_execution_service.handle_buy_stock이 호출되는지 테스트합니다."""
    # --- Arrange (준비) ---
    app = setup_mock_app

    # _execute_action('3') 내부에서 순서대로 요청될 사용자 입력을 미리 설정합니다.
    # 이렇게 하면 실제 input() 함수 호출을 막아 OSError를 방지합니다.
    app.cli_view.get_user_input.side_effect = ["005930", "10", "80000"]

    # --- Act (실행) ---
    # 실제 _execute_action 메서드를 호출합니다.
    executor = UserActionExecutor(app)
    result = await executor.execute('3')

    # --- Assert (검증) ---
    # 1. 사용자에게 종목코드, 수량, 가격을 순서대로 요청했는지 확인합니다.
    expected_calls = [
        call("매수할 종목 코드를 입력하세요: "),
        call("매수할 수량을 입력하세요: "),
        call("매수 가격을 입력하세요 (시장가: 0): ")
    ]
    app.cli_view.get_user_input.assert_has_calls(expected_calls)

    # 2. order_execution_service의 핸들러가 올바른 인자들로 호출되었는지 확인합니다.
    app.order_execution_service.handle_buy_stock.assert_awaited_once_with("005930", "10", "80000")

@pytest.mark.asyncio
async def test_execute_action_display_change_rate(setup_mock_app):
    """메뉴 '5' 선택 시 handle_display_stock_change_rate가 호출되는지 테스트합니다."""
    # --- Arrange (준비) ---
    app = setup_mock_app

    # 이렇게 하면 실제 input() 함수가 호출되는 것을 막고 OSError를 방지합니다.
    app.cli_view.get_user_input.return_value = "005930"

    # --- Act (실행) ---
    # 실제 _execute_action 메서드를 호출합니다.
    executor = UserActionExecutor(app)
    result = await executor.execute('20')

    # --- Assert (검증) ---
    # 1. 사용자에게 종목 코드를 요청했는지 확인합니다.
    app.cli_view.get_user_input.assert_awaited_once_with("조회할 종목 코드를 입력하세요 (삼성전자: 005930): ")

    # 2. stock_query_service의 핸들러가 올바른 종목 코드로 호출되었는지 확인합니다.
    app.stock_query_service.get_stock_change_rate.assert_awaited_once_with("005930")

@pytest.mark.asyncio
async def test_execute_action_display_vs_open_price(setup_mock_app):
    """메뉴 '6' 선택 시 handle_display_stock_vs_open_price가 호출되는지 테스트합니다."""
    # --- Arrange (준비) ---
    app = setup_mock_app

    # 이렇게 하면 실제 input() 함수가 호출되는 것을 막고 OSError를 방지합니다.
    app.cli_view.get_user_input.return_value = "005930"

    # --- Act (실행) ---
    # 실제 _execute_action 메서드를 호출합니다.
    executor = UserActionExecutor(app)
    result = await executor.execute('21')

    # --- Assert (검증) ---
    # 1. 사용자에게 종목 코드를 요청했는지 확인합니다.
    app.cli_view.get_user_input.assert_awaited_once_with("조회할 종목 코드를 입력하세요 (삼성전자: 005930): ")

    # 2. stock_query_service의 핸들러가 올바른 종목 코드로 호출되었는지 확인합니다.
    app.stock_query_service.get_open_vs_current.assert_awaited_once_with("005930")


@pytest.mark.asyncio
async def test_execute_action_get_top_market_cap_real(setup_mock_app):
    """메뉴 '13' 선택 시 (실전) handle_get_top_market_cap_stocks가 호출되는지 테스트합니다."""
    app = setup_mock_app
    app.env.is_paper_trading = False  # 실전 모드로 설정
    app.cli_view.get_user_input = AsyncMock(return_value=30)  # or side_effect=["30"]

    # 올바른 메뉴 번호 '13'으로 호출
    executor = UserActionExecutor(app)
    result = await executor.execute('50')

    # 올바른 서비스 메서드가 호출되었는지 확인
    app.stock_query_service.handle_get_top_market_cap_stocks_code.assert_awaited_once_with(
        market_code="0000",
        limit=30,
    )

@pytest.mark.asyncio
async def test_execute_action_get_top_market_cap_paper(setup_mock_app):
    """메뉴 '13' 선택 시 (모의) 경고 메시지가 출력되는지 테스트합니다."""
    app = setup_mock_app
    app.env.is_paper_trading = True  # 모의투자 모드로 설정
    # CLIView Mock에 경고 메시지 메서드 명시
    app.cli_view.display_warning_paper_trading_not_supported = MagicMock()

    executor = UserActionExecutor(app)
    result = await executor.execute('50')

    # 경고가 표시되고 서비스 메서드는 호출되지 않았는지 확인
    app.cli_view.display_warning_paper_trading_not_supported.assert_called_once_with("시가총액 상위 종목 조회")
    app.stock_query_service.handle_get_top_market_cap_stocks_code.assert_not_called()



@pytest.mark.asyncio
async def test_execute_action_get_top_10_with_prices(setup_mock_app):
    """메뉴 '8' 선택 시 handle_get_top_10_market_cap_stocks_with_prices가 호출되는지 테스트합니다."""
    app = setup_mock_app
    app.env.is_paper_trading = False
    app.stock_query_service = AsyncMock()

    executor = UserActionExecutor(app)
    result = await executor.execute('51')

    app.stock_query_service.handle_get_top_market_cap_stocks_code.assert_awaited_once()


### `_execute_action` 메서드를 위한 새로운 테스트 케이스

@pytest.mark.asyncio
async def test_execute_action_0_change_environment_success(setup_mock_app):  # capsys 제거
    app = setup_mock_app
    # 환경 선택을 담당하는 내부 메서드를 목(mock) 설정
    app.select_environment = AsyncMock(return_value=True)

    # 실제 _execute_action 메서드 호출
    executor = UserActionExecutor(app)
    result = await executor.execute('0')

    # 검증
    app.select_environment.assert_awaited_once()  # select_environment가 호출되었는지 확인
    assert result is True  # 환경 변경이 성공하면 앱은 계속 실행되어야 함
    # logger.info 호출을 확인
    app.logger.info.assert_any_call("거래 환경 변경을 시작합니다.")


@pytest.mark.asyncio
async def test_execute_action_1_stock_info_success(setup_mock_app):
    app = setup_mock_app
    app.cli_view.get_user_input.return_value = "005930"  # 종목코드 직접 입력으로 변경
    app.stock_query_service.handle_get_current_stock_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="정상",
        data={
            "code": "005930",
            "price": "70500",
            "time": "101500",
        }
    )
    executor = UserActionExecutor(app)
    result = await executor.execute('1')  # 1번 메뉴로 변경

    app.cli_view.get_user_input.assert_awaited_once_with("조회할 종목 코드를 입력하세요 (삼성전자: 005930): ")
    app.stock_query_service.handle_get_current_stock_price.assert_awaited_once_with(
        "005930")  # handle_get_current_stock_price 호출 확인
    assert result is True


@pytest.mark.asyncio
async def test_execute_action_2_account_balance_failure(setup_mock_app, capsys):
    app = setup_mock_app
    app.stock_query_service.handle_get_account_balance.return_value = ResCommonResponse(
        rt_cd=ErrorCode.API_ERROR.value,
        msg1="잔고 조회 실패",
        data=None
    )

    executor = UserActionExecutor(app)
    result = await executor.execute('2')

    app.stock_query_service.handle_get_account_balance.assert_awaited_once()
    app.cli_view.display_account_balance_failure.assert_called_once()  # CLIView 메서드 호출 확인
    assert result is True


@pytest.mark.asyncio
async def test_execute_action_2_account_balance_with_realistic_data(setup_mock_app):
    """실제 API 응답 형식(output1, output2)으로 계좌 잔고 조회 성공 테스트.
    기존 테스트는 간략한 mock 데이터만 사용하여 실제 응답 구조를 검증하지 못했음."""
    app = setup_mock_app
    mock_balance_data = {
        "rt_cd": "0",
        "msg_cd": "80000000",
        "msg1": "정상처리",
        "output1": [
            {
                "pdno": "005930", "prdt_name": "삼성전자",
                "hldg_qty": "10", "ord_psbl_qty": "10",
                "pchs_avg_pric": "70000.0000", "prpr": "72000",
                "evlu_amt": "720000", "evlu_pfls_amt": "20000",
                "pchs_amt": "700000", "trad_dvsn_name": "현금",
                "evlu_pfls_rt": "2.86"
            }
        ],
        "output2": [
            {
                "dnca_tot_amt": "5000000", "tot_evlu_amt": "5720000",
                "evlu_pfls_smtl_amt": "20000", "asst_icdc_erng_rt": "0.0035",
                "thdt_buy_amt": "0", "thdt_sll_amt": "0"
            }
        ]
    }
    app.stock_query_service.handle_get_account_balance.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="정상", data=mock_balance_data
    )

    executor = UserActionExecutor(app)
    await executor.execute('2')

    app.stock_query_service.handle_get_account_balance.assert_awaited_once()
    # display_account_balance가 output1/output2를 포함한 데이터로 호출되었는지 확인
    app.cli_view.display_account_balance.assert_called_once_with(mock_balance_data)
    called_data = app.cli_view.display_account_balance.call_args[0][0]
    assert "output1" in called_data, "실제 API 응답에는 output1 키가 있어야 합니다"
    assert "output2" in called_data, "실제 API 응답에는 output2 키가 있어야 합니다"
    assert len(called_data["output1"]) == 1
    assert called_data["output1"][0]["pdno"] == "005930"
    app.cli_view.display_account_balance_failure.assert_not_called()


@pytest.mark.asyncio
async def test_execute_action_2_account_balance_none_response(setup_mock_app):
    """handle_get_account_balance가 None을 반환하는 경우 실패 처리 테스트."""
    app = setup_mock_app
    app.stock_query_service.handle_get_account_balance.return_value = None

    executor = UserActionExecutor(app)
    result = await executor.execute('2')

    app.stock_query_service.handle_get_account_balance.assert_awaited_once()
    app.cli_view.display_account_balance_failure.assert_called_once_with("잔고 조회 실패: 응답 없음")
    app.cli_view.display_account_balance.assert_not_called()


@pytest.mark.asyncio
async def test_execute_action_2_account_balance_retry_limit(setup_mock_app):
    """API rate limit 초과로 RETRY_LIMIT 에러 발생 시 실패 처리 테스트.
    virtual/history 엔드포인트의 현재가 조회가 rate limit을 소진한 후
    계좌잔고 조회가 실패하는 시나리오를 시뮬레이션."""
    app = setup_mock_app
    app.stock_query_service.handle_get_account_balance.return_value = ResCommonResponse(
        rt_cd=ErrorCode.RETRY_LIMIT.value,
        msg1="최대 재시도 횟수 초과",
        data=None
    )

    executor = UserActionExecutor(app)
    result = await executor.execute('2')

    app.stock_query_service.handle_get_account_balance.assert_awaited_once()
    # rt_cd가 "0"이 아니므로 실패 처리되어야 함
    app.cli_view.display_account_balance_failure.assert_called_once_with("최대 재시도 횟수 초과")
    app.cli_view.display_account_balance.assert_not_called()


@pytest.mark.asyncio
async def test_execute_action_place_buy_order_success(setup_mock_app):
    app = setup_mock_app
    # handle_buy_stock이 성공적으로 실행되도록 목(mock) 설정
    app.order_execution_service.handle_buy_stock.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value,
        msg1="주문 성공",
        data={"ord_no": "T123"},
    )

    executor = UserActionExecutor(app)
    result = await executor.execute('3')

    # 검증
    app.order_execution_service.handle_buy_stock.assert_awaited_once()  # handle_buy_stock이 호출되었는지 확인
    assert result is True  # 앱은 계속 실행되어야 함

@pytest.mark.asyncio
# 테스트 이름과 목적을 실제 기능에 맞게 수정
async def test_execute_action_place_sell_order(setup_mock_app):
    """메뉴 '4' 선택 시 order_execution_service.handle_sell_stock이 호출되는지 테스트합니다."""
    # --- Arrange (준비) ---
    app = setup_mock_app

    # _execute_action('4') 내부에서 순서대로 요청될 사용자 입력을 미리 설정합니다.
    # 이렇게 하면 실제 input() 함수 호출을 막아 OSError를 방지합니다.
    app.cli_view.get_user_input.side_effect = ["005930", "10", "70000"]

    # --- Act (실행) ---
    # 실제 _execute_action 메서드를 호출합니다.
    executor = UserActionExecutor(app)
    result = await executor.execute('4')

    # --- Assert (검증) ---
    # 1. 사용자에게 종목코드, 수량, 가격을 순서대로 요청했는지 확인합니다.
    expected_calls = [
        call("매도할 종목 코드를 입력하세요: "),
        call("매도할 수량을 입력하세요: "),
        call("매도 가격을 입력하세요 (시장가: 0): ")
    ]
    app.cli_view.get_user_input.assert_has_calls(expected_calls)

    # 2. order_execution_service의 핸들러가 올바른 인자들로 호출되었는지 확인합니다.
    app.order_execution_service.handle_sell_stock.assert_awaited_once_with("005930", "10", "70000")


@pytest.mark.asyncio
async def test_execute_action_display_stock_change_rate(setup_mock_app):
    app = setup_mock_app
    app.cli_view.get_user_input.return_value = "005930"
    app.stock_query_service.get_stock_change_rate.return_value = None

    executor = UserActionExecutor(app)
    result = await executor.execute('20')

    app.cli_view.get_user_input.assert_awaited_once_with("조회할 종목 코드를 입력하세요 (삼성전자: 005930): ")
    app.stock_query_service.get_stock_change_rate.assert_awaited_once_with("005930")
    assert result is True


@pytest.mark.asyncio
async def test_execute_action_display_stock_vs_open_price(setup_mock_app):
    app = setup_mock_app
    app.cli_view.get_user_input.return_value = "005930"  # 종목 코드 입력 시뮬레이션
    app.stock_query_service.get_open_vs_current.return_value = None  # 핸들러 목킹

    executor = UserActionExecutor(app)
    result = await executor.execute('21')

    app.cli_view.get_user_input.assert_awaited_once_with("조회할 종목 코드를 입력하세요 (삼성전자: 005930): ")
    app.stock_query_service.get_open_vs_current.assert_awaited_once_with("005930")
    assert result is True

@pytest.mark.asyncio
# 테스트 이름과 목적을 실제 기능에 맞게 수정
async def test_execute_action_get_asking_price(setup_mock_app):
    """메뉴 '7' 선택 시 handle_get_asking_price가 호출되는지 테스트합니다."""
    # --- Arrange (준비) ---
    app = setup_mock_app

    app.cli_view.get_user_input.return_value = "005930"

    # (1) CLIView에 필요한 출력 메서드 붙이기
    app.cli_view.display_asking_price = MagicMock()
    app.cli_view.display_asking_price_error = MagicMock()

    # (2) 서비스가 성공을 반환하도록 모킹 (실패 경로로 안 떨어지게)
    app.stock_query_service.handle_get_asking_price = AsyncMock(return_value=ResCommonResponse(
        rt_cd="0", msg1="정상",
        data={
            "code": "005930",
            "rows": [
                {"level": 1, "ask_price": "70500", "ask_rem": "100", "bid_price": "70400", "bid_rem": "120"}
            ],
            "meta": {"prpr": "70450", "time": "101500"}
        }
    ))
    # --- Act (실행) ---
    # 실제 _execute_action 메서드를 호출합니다.
    executor = UserActionExecutor(app)
    result = await executor.execute('22')

    # --- Assert (검증) ---
    # 1. 사용자에게 종목 코드를 요청했는지 확인합니다.
    called_args = app.cli_view.get_user_input.await_args.args[0]
    assert "호가를 조회할 종목" in called_args

    # 2. stock_query_service의 핸들러가 올바른 종목 코드로 호출되었는지 확인합니다.
    app.stock_query_service.handle_get_asking_price.assert_awaited_once_with("005930")

    app.cli_view.display_asking_price.assert_called_once()
    app.cli_view.display_asking_price_error.assert_not_called()

@pytest.mark.asyncio
async def test_execute_action_market_cap_query_failure_in_live_env():
    """실전투자 환경에서 시가총액 10위 조회 실패 시에도 running_status는 True"""
    from app.trading_app import TradingApp

    # ─ Arrange ─
    app = object.__new__(TradingApp)
    app.logger = MagicMock()
    app.cli_view = MagicMock()
    app.env = MagicMock()
    app.env.is_paper_trading = False  # 실전 환경
    app.stock_query_service = AsyncMock()

    app.stock_query_service.handle_get_top_market_cap_stocks_code = AsyncMock(
        return_value=ResCommonResponse(
            rt_cd=ErrorCode.API_ERROR.value,  # 실패 코드
            msg1="시가총액 10위 조회 실패",
            data=None
        )
    )

    # ─ Act ─
    executor = UserActionExecutor(app)
    result = await executor.execute("51")

    # ─ Assert ─
    assert result is True  # 실패해도 True 반환 (계속 실행)
    app.stock_query_service.handle_get_top_market_cap_stocks_code.assert_called_once()
    app.logger.warning.assert_not_called()


# @pytest.mark.asyncio
# async def test_execute_action_momentum_strategy_market_closed(setup_mock_app, capsys):
#     app = setup_mock_app
#
#     app.time_manager.is_market_open = MagicMock(return_value=False)
#
#     # ✅ display_warning_strategy_market_closed 명시적으로 모킹
#     app.cli_view = MagicMock()
#     app.cli_view.display_warning_strategy_market_closed = MagicMock()
#
#     executor = UserActionExecutor(app)
#     result = await executor.execute("100")
#
#     app.cli_view.display_warning_strategy_market_closed.assert_called_once()
#     app.logger.warning.assert_called_once_with("시장 미개장 상태에서 전략 실행 시도")
#     app.stock_query_service.handle_get_top_market_cap_stocks_code.assert_not_called()
#     assert result is True


# @pytest.mark.asyncio
# async def test_execute_action_momentum_strategy_top_stocks_failure(setup_mock_app, capsys):
#     app = setup_mock_app
#     app.time_manager.is_market_open = MagicMock(return_value=True)
#     # get_top_market_cap_stocks_code가 실패 응답을 반환하도록 목 설정
#     app.stock_query_service.handle_get_top_market_cap_stocks_code.return_value = ResCommonResponse(
#         rt_cd="1",
#         msg1="API 조회 실패",
#         data=None
#     )
# 
#     executor = UserActionExecutor(app)
#     result = await executor.execute("100")
# 
#     # 검증
#     app.cli_view.display_strategy_running_message.assert_called_once_with("모멘텀")
#     app.stock_query_service.handle_get_top_market_cap_stocks_code.assert_awaited_once_with("0000")
#     app.cli_view.display_top_stocks_failure.assert_called_once_with("API 조회 실패")  # 실패 메시지 확인
#     app.logger.warning.assert_called()
#     assert result is True  # 앱은 계속 실행되어야 함
# 
# 
# @pytest.mark.asyncio
# async def test_execute_action_momentum_success(setup_mock_app):
#     # ─ Arrange ─
#     app = setup_mock_app
#     app.cli_view = MagicMock()
#     app.logger = MagicMock()
#     app.time_manager = MagicMock()
#     app.time_manager.is_market_open.return_value = True
#     app.stock_query_service = AsyncMock()
#     app.broker = MagicMock()
#     app.env.is_paper_trading = False
# 
#     # ✅ 시총 상위 종목 응답 Mock
#     app.stock_query_service.handle_get_top_market_cap_stocks_code.return_value = ResCommonResponse(
#         rt_cd="0",
#         msg1="성공",
#         data=[
#             ResTopMarketCapApiItem(
#                 iscd="ISCD1",
#                 mksc_shrn_iscd="005930",
#                 hts_kor_isnm="삼성전자",
#                 data_rank="1",
#                 stck_avls="시총1",
#                 acc_trdvol="100000"
#             ),
#             ResTopMarketCapApiItem(
#                 iscd="ISCD2",
#                 mksc_shrn_iscd="000660",
#                 hts_kor_isnm="SK하이닉스",
#                 data_rank="2",
#                 stck_avls="시총2",
#                 acc_trdvol="95000"
#             )
#         ]
#     )
# 
#     # ✅ broker.get_price_summary도 mocking 필요
#     app.broker.get_price_summary = AsyncMock(return_value=ResCommonResponse(
#         rt_cd="0",
#         msg1="정상",
#         data={
#             "code": "005930",
#             "name": "삼성전자",
#             "price": 70000,
#             "rank": 1,
#             "score": 95.2
#         }
#     ))
#     app.broker.get_current_price = AsyncMock(return_value=ResCommonResponse(
#         rt_cd="0",
#         msg1="정상",
#         data={
#             "stck_prpr": "70000",  # ✅ 현재가 필수 필드
#             "prdy_vrss": "500",
#             "prdy_ctrt": "0.72",
#             "prdy_vrss_sign": "2"
#         }
#     ))
# 
#     # StrategyExecutor.execute mock
#     mock_executor = AsyncMock()
#     mock_executor.execute.return_value = {
#         "follow_through": [
#             {
#                 "code": "005930",
#                 "name": "삼성전자",
#                 "price": 70000,
#                 "rank": 1,
#                 "score": 95.2
#             }
#         ],
#         "not_follow_through": [],
#         "total_processed": 1,
#         "buy_attempts": 1,
#         "buy_successes": 1,
#         "sell_attempts": 0,
#         "sell_successes": 0,
#         "execution_time": 1.23
#     }
# 
#     # ─ Act ─
#     executor = UserActionExecutor(app)
#     result = await executor.execute("100")
# 
#     # ─ Assert ─
#     app.cli_view.display_strategy_running_message.assert_called_once_with("모멘텀")
#     app.cli_view.display_top_stocks_success.assert_called_once()
#     assert result == True  # running_status 그대로 반환
# 
# 
# @pytest.mark.asyncio
# async def test_execute_action_momentum_top_stock_api_failure(mocker):
#     """시가총액 상위 종목 API 응답 실패 시 경고 출력 및 중단"""
# 
#     # ─ Arrange ─
#     app = object.__new__(TradingApp)
#     app.cli_view = MagicMock()
#     app.logger = MagicMock()
#     app.time_manager = MagicMock()
#     app.stock_query_service = AsyncMock()
#     app.broker = MagicMock()
#     app.env = MagicMock()
#     app.env.is_paper_trading = False
# 
#     app.time_manager.is_market_open.return_value = True
# 
#     # API 실패 응답 모의 (rt_cd != '0')
#     app.stock_query_service.handle_get_top_market_cap_stocks_code.return_value = ResCommonResponse(
#         rt_cd="1",
#         msg1="API 오류",
#         data=None
#     )
# 
#     # ─ Act ─
#     executor = UserActionExecutor(app)
#     result = await executor.execute("100")
# 
#     # ─ Assert ─
#     app.cli_view.display_top_stocks_failure.assert_called_once_with("API 오류")
#     call_arg = app.logger.warning.call_args[0][0]
#     assert "시가총액 조회 실패. 응답: ResCommonResponse" in call_arg
#     assert "rt_cd='1'" in call_arg
#     assert "msg1='API 오류'" in call_arg
#     assert result is True
# 
# 
# @pytest.mark.asyncio
# async def test_execute_action_momentum_no_stocks_for_strategy(setup_mock_app):
#     """시가총액 종목 조회는 성공했지만 전략 대상 종목이 없을 때"""
#     app = setup_mock_app
# 
#     # is_market_open 타입에 맞춰 패치 (async/sync 둘 다 커버)
#     if hasattr(app.time_manager, "is_market_open") and inspect.iscoroutinefunction(app.time_manager.is_market_open):
#         app.time_manager.is_market_open = AsyncMock(return_value=True)
#     else:
#         app.time_manager.is_market_open.return_value = True
# 
#     # viewer 호출만 검증
#     app.cli_view.display_no_stocks_for_strategy = MagicMock()
# 
#     # 핵심: async 메서드는 AsyncMock 으로
#     # (리팩토링 후 이름이 바뀌었다면 아래 함수명을 현재 코드에 맞춰 바꾸세요)
#     app.stock_query_service.handle_get_top_market_cap_stocks_code = AsyncMock(
#         return_value=ResCommonResponse(
#             rt_cd="0",
#             msg1="성공",
#             data=[
#                 ResTopMarketCapApiItem(
#                     iscd="ISCDX",
#                     mksc_shrn_iscd="",  # 전략 대상 아님
#                     hts_kor_isnm="",
#                     data_rank="",
#                     stck_avls="",
#                     acc_trdvol=""
#                 )
#             ]
#         )
#     )
# 
#     # 전략 실행이 별도 코루틴이면 이것도 await 대상 → 안전하게 AsyncMock
#     # (실제 코드 경로에 따라 필요 없을 수도 있음)
#     if hasattr(app, "strategy_executor") and hasattr(app.strategy_executor, "run_momentum"):
#         if inspect.iscoroutinefunction(app.strategy_executor.run_momentum):
#             app.strategy_executor.run_momentum = AsyncMock(return_value=[])
#         else:
#             # sync 함수면 MagicMock으로 유지
#             pass
# 
#     from app.user_action_executor import UserActionExecutor
#     executor = UserActionExecutor(app)
# 
#     result = await executor.execute("100")  # 메뉴 번호는 실제 매핑과 일치해야 함
#     assert result is True
# 
#     # 성공 기준: 전략 대상 없음 메시지 1회
#     app.cli_view.display_no_stocks_for_strategy.assert_called_once()
# 
#     # 보너스: 'await' 여부를 테스트로 강제 검증
#     app.stock_query_service.handle_get_top_market_cap_stocks_code.assert_awaited_once()
#     if hasattr(app, "strategy_executor") and hasattr(app.strategy_executor, "run_momentum") \
#        and isinstance(app.strategy_executor.run_momentum, AsyncMock):
#         app.strategy_executor.run_momentum.assert_awaited()
# 
# 
# @pytest.mark.asyncio
# async def test_execute_action_momentum_backtest_invalid_count_input_value_error(setup_mock_app, capsys, mocker):
#     app = setup_mock_app
#     app.cli_view.get_user_input.return_value = "invalid_number"
#     app.stock_query_service.get_top_market_cap_stocks_code = AsyncMock(return_value={"rt_cd": "0", "output": []})
#     # mocker.patch('strategies.momentum_strategy.MomentumStrategy') # 이 줄을 제거
#     # mocker.patch('strategies.strategy_executor.StrategyExecutor') # 이 줄을 제거
# 
#     executor = UserActionExecutor(app)
#     result = await executor.execute("101")
# 
#     app.cli_view.get_user_input.assert_awaited_once_with("시가총액 상위 몇 개 종목을 조회할까요? (기본값: 30): ")
#     app.cli_view.display_invalid_input_warning.assert_called_once_with("숫자가 아닌 값이 입력되어 기본값 30을 사용합니다.")
#     app.stock_query_service.handle_get_top_market_cap_stocks_code.assert_awaited_once_with("0000", count=30)
#     assert result is True
# 
# 
# @pytest.mark.asyncio
# async def test_execute_action_momentum_backtest_zero_or_negative_count_input(setup_mock_app, capsys):
#     app = setup_mock_app
#     app.cli_view.get_user_input.return_value = "-5"  # 0 이하의 숫자 입력 시뮬레이션
#     app.stock_query_service.get_top_market_cap_stocks_code = AsyncMock(return_value={"rt_cd": "0", "output": []})
# 
#     executor = UserActionExecutor(app)
#     result = await executor.execute("101")
# 
#     # 검증
#     app.cli_view.get_user_input.assert_awaited_once_with("시가총액 상위 몇 개 종목을 조회할까요? (기본값: 30): ")
#     app.cli_view.display_invalid_input_warning.assert_called_once_with("0 이하의 수는 허용되지 않으므로 기본값 30을 사용합니다.")  # 경고 메시지 확인
#     app.stock_query_service.handle_get_top_market_cap_stocks_code.assert_awaited_once_with("0000", count=30)  # 기본값 30으로 호출되었는지 확인
#     assert result is True  # 앱은 계속 실행되어야 함
# 
# 
# @pytest.mark.asyncio
# async def test_execute_action_momentum_backtest_empty_top_codes_list(mocker):
#     """시가총액 상위 종목 응답이 빈 리스트인 경우"""
# 
#     app = object.__new__(TradingApp)
#     app.cli_view = MagicMock()
#     app.logger = MagicMock()
#     app.time_manager = MagicMock()
#     app.stock_query_service = AsyncMock()
#     app.broker = MagicMock()
#     app.env = MagicMock()
#     app.env.is_paper_trading = False
# 
# 
#     app.backtest_data_provider = MagicMock()
#     app.backtest_data_provider.realistic_price_lookup = MagicMock()
# 
#     app.cli_view.get_user_input = AsyncMock(return_value="2")
# 
#     app.stock_query_service.handle_get_top_market_cap_stocks_code.return_value = ResCommonResponse(
#         rt_cd="0",
#         msg1="정상",
#         data=[]
#     )
# 
#     executor = UserActionExecutor(app)
#     result = await executor.execute("101")
# 
#     assert result is True
#     app.cli_view.display_top_stocks_failure.assert_called()
# 
# 
# @pytest.mark.asyncio
# async def test_execute_action_momentum_backtest_input_negative_number(mocker):
#     """음수 입력 시 기본값 30으로 처리되고 정상 동작하는지 검증"""
# 
#     app = object.__new__(TradingApp)
#     app.cli_view = MagicMock()
#     app.logger = MagicMock()
#     app.time_manager = MagicMock()
#     app.stock_query_service = AsyncMock()
#     app.broker = MagicMock()
#     app.env = MagicMock()
#     app.env.is_paper_trading = False
# 
#     app.backtest_data_provider = MagicMock()
#     app.backtest_data_provider.realistic_price_lookup = MagicMock()
# 
#     app.cli_view.get_user_input = AsyncMock(return_value="-5")
# 
#     app.stock_query_service.handle_get_top_market_cap_stocks_code.return_value = [{"code": "005930"}]
# 
#     mock_executor = AsyncMock()
#     mock_executor.execute.return_value = {"follow_through": [], "not_follow_through": []}
# 
#     mocker.patch("strategies.momentum_strategy.MomentumStrategy", return_value=MagicMock())
#     mocker.patch("strategies.strategy_executor.StrategyExecutor", return_value=mock_executor)
# 
#     executor = UserActionExecutor(app)
#     result = await executor.execute("101")
# 
#     assert result is True
#     app.cli_view.display_invalid_input_warning.assert_called_with(
#         "0 이하의 수는 허용되지 않으므로 기본값 30을 사용합니다."
#     )
# 
# 
# @pytest.mark.asyncio
# async def test_execute_action_momentum_backtest_input_not_a_number(mocker):
#     """숫자가 아닌 입력 시 기본값 30으로 처리"""
# 
#     app = object.__new__(TradingApp)
#     app.cli_view = MagicMock()
#     app.logger = MagicMock()
#     app.time_manager = MagicMock()
#     app.stock_query_service = AsyncMock()
#     app.broker = MagicMock()
#     app.env = MagicMock()
#     app.env.is_paper_trading = False
# 
#     app.backtest_data_provider = MagicMock()
#     app.backtest_data_provider.realistic_price_lookup = MagicMock()
# 
#     app.cli_view.get_user_input = AsyncMock(return_value="abc")
#     app.stock_query_service.handle_get_top_market_cap_stocks_code.return_value = [{"code": "005930"}]
# 
#     mock_executor = AsyncMock()
#     mock_executor.execute.return_value = {"follow_through": [], "not_follow_through": []}
# 
#     mocker.patch("strategies.momentum_strategy.MomentumStrategy", return_value=MagicMock())
#     mocker.patch("strategies.strategy_executor.StrategyExecutor", return_value=mock_executor)
# 
#     executor = UserActionExecutor(app)
#     result = await executor.execute("101")
# 
#     assert result is True
#     app.cli_view.display_invalid_input_warning.assert_called_with(
#         "숫자가 아닌 값이 입력되어 기본값 30을 사용합니다."
#     )
# 
# 
# @pytest.mark.asyncio
# async def test_execute_action_momentum_backtest_api_failure(mocker):
#     """시가총액 조회 API 실패 시 경고 메시지 출력"""
# 
#     app = object.__new__(TradingApp)
#     app.cli_view = MagicMock()
#     app.logger = MagicMock()
#     app.time_manager = MagicMock()
#     app.stock_query_service = AsyncMock()
#     app.broker = MagicMock()
#     app.env = MagicMock()
#     app.env.is_paper_trading = False
# 
# 
#     app.backtest_data_provider = MagicMock()
#     app.backtest_data_provider.realistic_price_lookup = MagicMock()
# 
#     app.cli_view.get_user_input = AsyncMock(return_value="2")
# 
#     app.stock_query_service.handle_get_top_market_cap_stocks_code.return_value = ResCommonResponse(
#         rt_cd="1",  # 실패
#         msg1="오류 발생",
#         data=None
#     )
# 
#     executor = UserActionExecutor(app)
#     result = await executor.execute("101")
# 
#     assert result is True
#     app.cli_view.display_top_stocks_failure.assert_called_with("오류 발생")
# 
# 
# @pytest.mark.asyncio
# async def test_execute_action_momentum_backtest_strategy_exception(mocker):
#     """전략 실행 중 예외 발생 시 에러 출력"""
# 
#     app = object.__new__(TradingApp)
#     app.cli_view = MagicMock()
#     app.logger = MagicMock()
#     app.time_manager = MagicMock()
#     app.stock_query_service = AsyncMock()
#     app.broker = MagicMock()
#     app.env = MagicMock()
#     app.env.is_paper_trading = False
# 
#     app.backtest_data_provider = MagicMock()
#     app.backtest_data_provider.realistic_price_lookup = MagicMock()
# 
#     app.cli_view.get_user_input = AsyncMock(return_value="1")
#     app.stock_query_service.handle_get_top_market_cap_stocks_code.return_value = [{"code": "005930"}]
# 
#     mock_strategy = MagicMock()
#     mock_executor = AsyncMock()
#     mock_executor.execute.side_effect = Exception("예외 발생")
# 
#     mocker.patch("strategies.momentum_strategy.MomentumStrategy", return_value=mock_strategy)
#     mocker.patch("strategies.strategy_executor.StrategyExecutor", return_value=mock_executor)
# 
#     executor = UserActionExecutor(app)
#     result = await executor.execute("101")
# 
#     assert result is True
#     app.cli_view.display_strategy_error.assert_called_once()
#     app.logger.error.assert_called_once()
# 
# 
# @pytest.mark.asyncio
# async def test_execute_action_momentum_backtest_no_stock_codes_after_filtering(mocker):
#     """종목 리스트 존재하지만 필터 후 사용할 종목이 없는 경우"""
# 
#     app = object.__new__(TradingApp)
#     app.cli_view = MagicMock()
#     app.logger = MagicMock()
#     app.time_manager = MagicMock()
#     app.stock_query_service = AsyncMock()
#     app.broker = MagicMock()
#     app.env = MagicMock()
#     app.env.is_paper_trading = False
# 
# 
#     app.backtest_data_provider = MagicMock()
#     app.backtest_data_provider.realistic_price_lookup = MagicMock()
# 
#     app.cli_view.get_user_input = AsyncMock(return_value="2")
# 
#     # 종목 정보가 있으나 'code' 필드가 없음 → 필터링 후 빈 리스트
#     app.stock_query_service.handle_get_top_market_cap_stocks_code.return_value = ResCommonResponse(
#         rt_cd="0",
#         msg1="정상",
#         data=[{"ticker": "INVALID"}]  # mksc_shrn_iscd 없는 데이터
#     )
# 
#     executor = UserActionExecutor(app)
#     result = await executor.execute("101")
# 
#     assert result is True
#     app.cli_view.display_no_stocks_for_strategy.assert_called_once()
# 
# 
# @pytest.mark.asyncio
# async def test_execute_action_gapup_pullback_response_format_error():
#     app = object.__new__(TradingApp)
#     app.cli_view = MagicMock()
#     app.logger = MagicMock()
#     app.stock_query_service = MagicMock()
#     app.broker = MagicMock()
#     app.env = MagicMock()
#     app.env.is_paper_trading = False
# 
#     app.stock_query_service.handle_get_top_market_cap_stocks_code.return_value = ResCommonResponse(
#         rt_cd="0",
#         msg1="정상처리",
#         data="INVALID"  # 올바른 리스트가 아닌 경우 (예외 케이스 유도)
#     )
#     executor = UserActionExecutor(app)
#     result = await executor.execute("102")
# 
#     assert result is True
#     app.cli_view.display_strategy_error.assert_called()
#     app.logger.error.assert_called()
# 
# 
# @pytest.mark.asyncio
# async def test_execute_action_gapup_pullback_no_stocks_for_strategy(mocker):
#     app = object.__new__(TradingApp)
#     app.cli_view = MagicMock()
#     app.cli_view.get_user_input = AsyncMock(return_value="1")
#     app.logger = MagicMock()
#     app.stock_query_service = AsyncMock()
#     app.broker = MagicMock()
#     app.env = MagicMock()
#     app.env.is_paper_trading = False
# 
#     app.stock_query_service.handle_get_top_market_cap_stocks_code = AsyncMock(
#         return_value=ResCommonResponse(
#             rt_cd="0",
#             msg1="정상",
#             data=[]
#         )
#     )
# 
#     executor = UserActionExecutor(app)
#     result = await executor.execute("102")
# 
#     assert result is True
#     app.cli_view.display_no_stocks_for_strategy.assert_called_once()
# 
# 
# @pytest.mark.asyncio
# async def test_execute_action_gapup_pullbackstrategy_exception(mocker):
#     app = object.__new__(TradingApp)
#     app.cli_view = MagicMock()
#     app.logger = MagicMock()
#     app.stock_query_service = AsyncMock()
#     app.broker = MagicMock()
#     app.env = MagicMock()
#     app.env.is_paper_trading = False
# 
#     app.stock_query_service.handle_get_top_market_cap_stocks_code.return_value = {
#         "rt_cd": "0",
#         "output": [{"mksc_shrn_iscd": "005930"}]
#     }
# 
#     mock_strategy = MagicMock()
#     mock_executor = AsyncMock()
#     mock_executor.execute.side_effect = Exception("GapUpPullback 실행 오류")
# 
#     mocker.patch("strategies.GapUpPullback_strategy.GapUpPullbackStrategy", return_value=mock_strategy)
#     mocker.patch("strategies.strategy_executor.StrategyExecutor", return_value=mock_executor)
# 
#     executor = UserActionExecutor(app)
#     result = await executor.execute("102")
# 
#     assert result is True
#     app.cli_view.display_strategy_error.assert_called_once()
#     app.logger.error.assert_called_once()


@pytest.mark.asyncio
async def test_execute_action_98_invalidate_token(setup_mock_app, capsys):
    app = setup_mock_app
    app.env.invalidate_token.return_value = None

    executor = UserActionExecutor(app)
    result = await executor.execute("998")

    app.env.invalidate_token.assert_called_once()
    app.cli_view.display_token_invalidated_message.assert_called_once()
    assert result is True


@pytest.mark.asyncio
# 테스트 이름과 내용을 '99'번 메뉴에 맞게 수정합니다.
async def test_execute_action_99_exit_app(setup_mock_app):
    """
    메뉴 '99' 선택 시 running_status가 False를 반환하는지 테스트합니다.
    """
    # --- Arrange (준비) ---
    app = setup_mock_app

    # --- Act (실행) ---
    # '0' 대신 올바른 종료 메뉴 번호인 '99'를 호출합니다.
    executor = UserActionExecutor(app)
    running_status = await executor.execute("999")

    # --- Assert (검증) ---
    # 앱 종료를 위해 False가 반환되었는지 확인합니다.
    assert running_status is False
    # 종료 메시지가 호출되었는지 확인합니다.
    app.cli_view.display_exit_message.assert_called_once()


@pytest.mark.asyncio
async def test_execute_action_999_invalid_menu_general(setup_mock_app):
    app = setup_mock_app
    # 유효하지 않은 임의의 메뉴 선택
    executor = UserActionExecutor(app)
    result = await executor.execute("-1")

    # display_invalid_menu_choice가 호출되었는지 확인
    app.cli_view.display_invalid_menu_choice.assert_called_once()
    # 앱은 종료되지 않고 계속 실행되어야 함
    assert result is True


# 13. `_complete_api_initialization` 성공 시나리오 테스트
@pytest.mark.asyncio
async def test_complete_api_initialization_success(setup_mock_app, mocker):
    app = setup_mock_app

    # _complete_api_initialization의 내부에서 호출되는 종속성들을 목킹
    # 클래스 자체를 목킹하여 생성자 호출을 추적할 수 있도록 함
    mock_trading_service_cls = mocker.patch('app.trading_app.TradingService')
    mock_order_execution_service_cls = mocker.patch('app.trading_app.OrderExecutionService')
    mock_stock_query_service_cls = mocker.patch('app.trading_app.StockQueryService')
    mock_broker_wrapper_cls = mocker.patch('app.trading_app.BrokerAPIWrapper')
    mock_backtest_provider_cls = mocker.patch('app.trading_app.BacktestDataProvider')

    # 실제 _complete_api_initialization 메서드 호출
    result = await app._complete_api_initialization()

    # 검증
    mock_trading_service_cls.assert_called_once()  # TradingService 생성자 호출 확인
    mock_order_execution_service_cls.assert_called_once()  # OrderExecutionService 생성자 호출 확인
    mock_stock_query_service_cls.assert_called_once()  # StockQueryService 생성자 호출 확인
    mock_broker_wrapper_cls.assert_called_once()  # BrokerAPIWrapper 생성자 호출 확인
    mock_backtest_provider_cls.assert_called_once()  # BacktestDataProvider 생성자 호출 확인

    app.logger.info.assert_called_with(mocker.ANY)  # info 로깅 호출 확인
    assert result is True


# 15. `select_environment` 실전투자 선택 성공 시나리오 테스트
@pytest.mark.asyncio
async def testselect_environment_real_trading_success(setup_mock_app):
    app = setup_mock_app

    # cli_view.select_environment_input이 '2' (실전투자)을 반환하도록 설정
    app.cli_view.select_environment_input.side_effect = ["2"]

    # env.get_access_token 및 _complete_api_initialization이 성공적으로 작동하도록 목킹
    app.env.get_access_token.return_value = "mock_access_token"

    # select_environment 메서드 호출
    result = await app.select_environment()

    # 검증
    app.cli_view.select_environment_input.assert_awaited_once()
    app.env.set_trading_mode.assert_called_once_with(False)  # False (실전투자)로 호출되었는지 확인
    app.logger.info.assert_any_call("실전 투자 환경으로 설정되었습니다.")
    app.env.get_access_token.assert_awaited_once()
    assert result is True


# 16. `select_environment` 모의투자 선택 성공 시나리오 테스트
@pytest.mark.asyncio
async def testselect_environment_paper_trading_success(setup_mock_app):
    app = setup_mock_app

    # cli_view.select_environment_input이 '1' (모의투자)을 반환하도록 설정
    app.cli_view.select_environment_input.side_effect = ["1"]

    # env.get_access_token 및 _complete_api_initialization이 성공적으로 작동하도록 목킹
    app.env.get_access_token.return_value = "mock_access_token"

    # select_environment 메서드 호출
    result = await app.select_environment()

    # 검증
    app.cli_view.select_environment_input.assert_awaited_once()
    app.env.set_trading_mode.assert_called_once_with(True)  # True (모의투자)로 호출되었는지 확인
    app.logger.info.assert_any_call("모의 투자 환경으로 설정되었습니다.")
    app.env.get_access_token.assert_awaited_once()
    assert result is True


@pytest.mark.asyncio
async def testselect_environment_token_acquisition_failure(setup_mock_app):
    app = setup_mock_app

    # cli_view.select_environment_input이 '2' (실전투자)을 반환하도록 설정
    app.cli_view.select_environment_input.side_effect = ["2"]

    # env.get_access_token이 None을 반환하도록 목킹하여 토큰 획득 실패 시뮬레이션
    app.env.get_access_token.return_value = None

    # _complete_api_initialization은 호출되지 않아야 함
    app._complete_api_initialization = AsyncMock()

    # select_environment 메서드 호출
    result = await app.select_environment()

    # 검증
    app.cli_view.select_environment_input.assert_awaited_once()
    app.env.set_trading_mode.assert_called_once_with(False)  # 환경 설정은 시도됨
    app.env.get_access_token.assert_awaited_once()
    app.logger.critical.assert_called_once_with("선택된 환경의 토큰 발급에 실패했습니다. 애플리케이션을 종료합니다.")
    app._complete_api_initialization.assert_not_awaited()  # 토큰 실패 시 초기화 호출 안 됨
    assert result is False

# @pytest.mark.asyncio
# async def test_execute_action_strategy_exception(mocker):
#     """모멘텀 전략 실행 중 예외 발생 시 예외 메시지 출력 및 로그 확인"""
#
#     # ─ Arrange ─
#     app = object.__new__(TradingApp)
#     app.cli_view = MagicMock()
#     app.logger = MagicMock()
#     app.time_manager = MagicMock()
#     app.stock_query_service = AsyncMock()
#     app.broker = MagicMock()
#     app.env = MagicMock()
#
#     app.env.is_paper_trading = False
#     app.time_manager.is_market_open.return_value = True
#
#     # 정상적으로 종목 조회는 됨
#     app.stock_query_service.handle_get_top_market_cap_stocks_code.return_value = {
#         'rt_cd': '0',
#         'output': [{'mksc_shrn_iscd': '005930'}]
#     }
#
#     # MomentumStrategy와 StrategyExecutor 패치
#     mock_strategy = MagicMock()
#     mock_executor = AsyncMock()
#     mock_executor.execute.side_effect = Exception("전략 내부 오류 발생")
#
#     # 전략 관련 클래스 패치
#     from strategies.momentum_strategy import MomentumStrategy
#     from strategies.strategy_executor import StrategyExecutor
#
#     MODULE_PATH_STRATEGY = MomentumStrategy.__module__  # 'strategies.momentum_strategy'
#     MODULE_PATH_EXECUTOR = StrategyExecutor.__module__  # 'strategies.strategy_executor'
#     mocker.patch(f"{MODULE_PATH_STRATEGY}.MomentumStrategy", return_value=MagicMock())
#     mocker.patch(f"{MODULE_PATH_EXECUTOR}.StrategyExecutor", return_value=mock_executor)
#
#     # ─ Act ─
#     executor = UserActionExecutor(app)
#     result = await executor.execute("100")
#
#     # ─ Assert ─
#     app.logger.error.assert_called_once()
#     app.cli_view.display_strategy_error.assert_called_once()
#     assert result is True


@pytest.mark.asyncio
@patch('builtins.print')  # Patch print to avoid actual console output
async def test_select_environment_invalid_choice_triggers_warning(mock_print):  # Remove 'self'
    # ─ Arrange ─
    app = object.__new__(TradingApp)  # __init__ 우회
    app.cli_view = MagicMock()
    app.cli_view.select_environment_input = AsyncMock(side_effect=["abc", "1"])  # 잘못된 입력
    app.cli_view.display_invalid_environment_choice = MagicMock()

    app.env = MagicMock()
    app.env.set_trading_mode = MagicMock()
    app.env.get_access_token = AsyncMock(return_value="dummy_token")
    app.logger = MagicMock()

    # ─ Act ─
    result = await app.select_environment()

    # ─ Assert ─
    # Ensure display_invalid_environment_choice was called for the invalid input
    app.cli_view.display_invalid_environment_choice.assert_called_once()
    # Ensure select_environment_input was called twice (once for invalid, once for valid)
    assert app.cli_view.select_environment_input.call_count == 2  # Use pytest's assert
    # Ensure set_trading_mode was called for the valid input
    app.env.set_trading_mode.assert_called_once_with(True)
    # Ensure _complete_api_initialization was called
    assert result is True  # Use pytest's assert


@pytest.mark.asyncio
async def test_run_async_main_loop():
    """
    TradingApp의 run_async 메서드가 애플리케이션의 메인 루프를 올바르게 실행하고 종료하는지 테스트합니다.
    """
    # ─ 준비 (Arrange) ─
    # TradingApp 인스턴스를 __init__을 우회하여 생성하고 필요한 속성을 수동으로 Mocking합니다.
    app = object.__new__(TradingApp)

    # CLIView, Logger, TimeManager 등 TradingApp의 주요 종속성 Mocking
    app.cli_view = MagicMock()
    app.logger = MagicMock()  # 로거도 Mocking하여 로그 호출을 검증할 수 있습니다.
    app.time_manager = MagicMock()  # display_current_time 내부에서 사용될 수 있으므로 Mocking

    # TradingApp 내부의 비동기 메서드들을 Mocking하여 제어합니다.
    # _complete_api_initialization이 성공적으로 완료되었다고 가정합니다.
    app._complete_api_initialization = AsyncMock(return_value=True)
    # select_environment가 성공적으로 완료되었다고 가정합니다.
    app.select_environment = AsyncMock(return_value=True)
    # _display_menu는 동기 메서드이므로 MagicMock으로 충분합니다.
    app._display_menu = MagicMock()
    # _execute_action은 사용자의 선택에 따라 루프를 계속할지(True) 종료할지(False)를 반환합니다.
    app.executor = AsyncMock()
    app.executor.execute = AsyncMock()

    # 사용자 입력을 시뮬레이션합니다.
    # 첫 번째 입력: '1' (예: 현재가 조회)
    # 두 번째 입력: '99' (종료)
    app.cli_view.get_user_input = AsyncMock(side_effect=["1", "99"])

    # _execute_action의 반환 값을 시뮬레이션합니다.
    # 첫 번째 액션('1')은 루프를 계속하게 하고 (True),
    # 두 번째 액션('99')은 루프를 종료하게 합니다 (False).
    app.executor.execute.side_effect = [True, False]

    # ─ 실행 (Act) ─
    await app.run_async()

    # ─ 검증 (Assert) ─

    # 1. 애플리케이션 시작 및 초기화 단계 검증
    # 환영 메시지가 한 번 표시되었는지 확인
    app.cli_view.display_welcome_message.assert_called_once()
    # API 초기화가 한 번 호출되고 await되었는지 확인
    app._complete_api_initialization.assert_awaited_once()
    # 환경 선택이 한 번 호출되고 await되었는지 확인
    app.select_environment.assert_awaited_once()

    # 2. 메인 루프의 반복 횟수 및 호출 검증
    # 루프는 '1' 입력 후 한 번, '99' 입력 후 한 번 더 실행되므로 총 두 번 반복됩니다.
    # 따라서 다음 메서드들은 두 번씩 호출되어야 합니다.
    assert app.cli_view.display_current_time.call_count == 2
    assert app._display_menu.call_count == 2
    assert app.cli_view.get_user_input.call_count == 2

    # _execute_action이 올바른 인자로 두 번 호출되었는지 확인
    # 첫 번째 호출은 "1"로, 두 번째 호출은 "99"로 이루어져야 합니다.
    app.executor.execute.assert_has_calls([
        call("1"),
        call("99")
    ])
    # _execute_action이 정확히 두 번 호출되었는지 확인
    assert app.executor.execute.call_count == 2

    # 추가 검증 (선택 사항):
    # 예를 들어, 로거가 특정 메시지를 기록했는지 확인할 수 있습니다.
    # app.logger.info.assert_any_call("애플리케이션 종료.") # 만약 종료 로그가 있다면


# 기존 TestTradingApp 클래스에 이 메서드들을 추가합니다.
class TestTradingApp(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        """
        각 테스트 메서드 실행 전에 필요한 Mock 객체와 TradingApp 인스턴스를 초기화합니다.
        """
        self.mock_cli_view = MagicMock()
        self.mock_env = MagicMock()
        self.mock_logger = MagicMock()
        self.mock_time_manager = MagicMock()

        # OrderExecutionService와 StockQueryService도 Mocking합니다.
        # 실제 인스턴스 대신 Mock을 사용합니다.
        self.mock_stock_query_service = MagicMock()
        self.mock_order_execution_service = MagicMock()

        # TradingApp 인스턴스를 __init__을 우회하여 생성하고 필요한 속성을 수동으로 Mocking합니다.
        self.app = object.__new__(TradingApp)
        self.app.cli_view = self.mock_cli_view
        self.app.env = self.mock_env
        self.app.logger = self.mock_logger
        self.app.time_manager = self.mock_time_manager
        self.app.stock_query_service = self.mock_stock_query_service # StockQueryService 할당
        self.app.order_execution_service = self.mock_order_execution_service # OrderExecutionService 할당

        # _complete_api_initialization 및 select_environment를 Mocking하여 제어합니다.
        self.app._complete_api_initialization = AsyncMock(return_value=True)
        self.app.select_environment = AsyncMock(return_value=True)
        self.app._display_menu = MagicMock()  # run_async 테스트에서 사용되므로 추가

    # @pytest.mark.asyncio # 이 데코레이터를 제거합니다.
    async def test_execute_action_0_environment_change_fails(self):
        """
        _execute_action 메서드에서 choice '0' (환경 변경) 선택 시
        select_environment가 실패하여 running_status가 False가 되는지 검증합니다 (175 라인).
        """
        # ─ 준비 (Arrange) ─
        # select_environment가 False를 반환하도록 Mocking하여 환경 변경 실패를 시뮬레이션합니다.
        self.app.select_environment.return_value = False

        # ─ 실행 (Act) ─
        # _execute_action을 '0' 선택으로 호출합니다.
        executor = UserActionExecutor(self.app)
        running_status = await executor.execute('0')

        # ─ 검증 (Assert) ─
        # 175번 라인: running_status가 False로 설정되었는지 확인
        assert running_status is False
        # logger.info("거래 환경 변경을 시작합니다.")가 호출되었는지 확인
        self.app.logger.info.assert_called_once_with("거래 환경 변경을 시작합니다.")
        # select_environment가 호출되었는지 확인
        self.app.select_environment.assert_awaited_once()

    # @pytest.mark.asyncio # 이 데코레이터를 제거합니다.
    async def test_run_async_api_initialization_fails(self):
        """
        run_async 메서드에서 _complete_api_initialization이 실패할 경우
        애플리케이션이 즉시 종료되고 다음 단계가 실행되지 않는지 검증합니다 (456-457 라인).
        """
        # ─ 준비 (Arrange) ─
        # _complete_api_initialization이 False를 반환하도록 Mocking하여 실패를 시뮬레이션합니다.
        self.app._complete_api_initialization.return_value = False

        # select_environment 및 메인 루프 관련 메서드들이 호출되지 않음을 확인하기 위해 Mocking합니다.
        self.app.select_environment = AsyncMock()
        self.app.cli_view.display_current_time = MagicMock()
        self.app._display_menu = MagicMock()
        self.app.cli_view.get_user_input = AsyncMock()
        self.app._execute_action = AsyncMock()

        # ─ 실행 (Act) ─
        await self.app.run_async()

        # ─ 검증 (Assert) ─
        # 환영 메시지가 한 번 표시되었는지 확인
        self.app.cli_view.display_welcome_message.assert_called_once()
        # _complete_api_initialization이 한 번 호출되었는지 확인
        self.app._complete_api_initialization.assert_awaited_once()

        # 중요: _complete_api_initialization 실패 시 다음 단계가 실행되지 않음을 검증
        # select_environment가 호출되지 않았는지 확인
        self.app.select_environment.assert_not_awaited()
        # 메인 루프 내부의 메서드들이 호출되지 않았는지 확인
        self.app.cli_view.display_current_time.assert_not_called()
        self.app._display_menu.assert_not_called()
        self.app.cli_view.get_user_input.assert_not_called()
        self.app._execute_action.assert_not_awaited()



@pytest.mark.asyncio
async def test_execute_action_realtime_subscription(setup_mock_app):
    """메뉴 '18' 선택 시 실시간 구독 핸들러가 올바르게 호출되는지 테스트합니다."""
    # --- Arrange (준비) ---
    app = setup_mock_app

    # 사용자 입력을 모의(Mock)합니다.
    app.cli_view.get_user_input.side_effect = [
        "005930",  # 종목 코드 입력
        "price,quote"  # 구독할 데이터 타입 입력
    ]

    # --- Act (실행) ---
    executor = UserActionExecutor(app)
    result = await executor.execute('70')

    # --- Assert (검증) ---
    # 1. 사용자에게 종목 코드를 요청했는지 확인합니다.
    calls = [args[0][0] for args in app.cli_view.get_user_input.await_args_list]
    assert "구독할 종목 코드를 입력하세요: " in calls
    assert "구독할 데이터 타입을 입력하세요 (price, quote 중 택1 또는 쉼표로 구분): " in calls

    # 2. stock_query_service의 핸들러가 올바른 종목 코드로 호출되었는지 확인합니다.
    app.stock_query_service.handle_realtime_stream.assert_awaited_once_with(
        ['005930'], ['price', 'quote'], duration=30
    )

@pytest.mark.asyncio
async def test_initialization_and_environment_selection(setup_mock_app):
    """
    (통합 테스트) 앱이 성공적으로 초기화되고, 사용자 입력에 따라 환경을 선택하는 흐름을 검증합니다.
    """
    app = setup_mock_app

    # --- Arrange (준비) ---
    # ✅ 사용자가 '1' (모의투자)을 선택했다고 명시적으로 가정합니다.
    app.cli_view.select_environment_input.return_value = '1'

    # ✅ API 초기화가 성공했다고 가정합니다. (이미 픽스처에 설정되어 있을 수 있지만, 명시적으로 제어)

    # --- Act (실행) ---
    # select_environment는 환경 선택 및 API 초기화를 담당합니다.
    success = await app.select_environment()

    # --- Assert (검증) ---
    assert success is True
    # env.set_trading_mode가 모의투자 모드(True)로 호출되었는지 확인합니다.
    app.env.set_trading_mode.assert_called_once_with(True)


@pytest.mark.asyncio
async def test_execute_action_realtime_stream_new_menu_option(setup_mock_app):
    """메뉴 '18' 선택 시 실시간 구독 기능이 호출되는지 테스트합니다."""  # Docstring도 일관성 있게 수정
    app = setup_mock_app
    app.cli_view.get_user_input.side_effect = [
        "005930",  # 종목 코드
        "price,quote"  # 데이터 타입
    ]
    # ✅ 검증할 메서드에 맞게 return_value 설정 (필수는 아니지만 좋은 습관)
    app.stock_query_service.handle_realtime_stream.return_value = None

    executor = UserActionExecutor(app)
    result = await executor.execute('70')

    calls = [call.args[0] for call in app.cli_view.get_user_input.await_args_list]

    assert any("종목 코드" in msg for msg in calls), "Expected prompt for 종목 코드"
    assert any("데이터 타입" in msg for msg in calls), "Expected prompt for 데이터 타입"

    # ✅ 실제 호출되는 메서드 이름으로 수정합니다.
    app.stock_query_service.handle_realtime_stream.assert_awaited_once_with(
        ['005930'], ['price', 'quote'], duration=30
    )
    assert result is True
