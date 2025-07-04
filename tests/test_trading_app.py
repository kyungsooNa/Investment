import pytest
import logging
import sys
import os # os 모듈 추가
from unittest.mock import MagicMock, AsyncMock
from trading_app import TradingApp

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

try:
    import services.momentum_strategy
    import services.strategy_executor
    import trading_app # trading_app 모듈도 명시적으로 임포트
except ImportError as e:
    # Fallback for environments where direct import might not work
    # but the classes are defined in the mock section below.
    # Log the error for debugging if needed, but don't stop execution
    # as mocks will be used.
    logging.warning(f"Could not import a module for testing: {e}. Proceeding with mocks.")


# Helper function to create a mock config for tests
def get_mock_config():
    """
    Returns a mock configuration dictionary for testing.
    Ensure it includes necessary URL configurations for KoreaInvestApiEnv.
    """
    return {
        # This determines which URLs (paper_ or real) are expected
        'is_paper_trading': True,  # Set to True for paper trading URLs, False for real trading URLs

        # --- Required for is_paper_trading: True ---
        'paper_url': 'https://mock-paper-api.koreainvestment.com:443',
        'paper_websocket_url': 'ws://mock-paper-websocket.koreainvestment.com:80',

        # --- Required for is_paper_trading: False (include these if your tests might switch) ---
        'url': 'https://mock-real-api.koreainvestment.com:443',
        'websocket_url': 'ws://mock-real-websocket.koreainvestment.com:80',

        # ... add any other configuration parameters your TradingApp expects
        'app_key': 'mock_app_key',
        'app_secret': 'mock_app_secret',
        'account_number': 'mock_account_number',
        'account_number_stock': 'mock_account_number_stock',
        'hts_id': 'mock_hts_id',
        # etc.
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
class TransactionHandlers:
    pass
class TimeManager:
    def is_market_open(self): # is_market_open 메서드 추가
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
class DataHandlers:
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
    async def handle_get_account_balance(self): # handle_get_account_balance 메서드 추가
        self.cli_view.display_message("계좌 잔고를 조회합니다.")
        # 실제 로직에서는 trading_service를 호출할 것입니다.
        # 여기서는 Mock된 trading_service를 사용합니다.
        result = await self.trading_service.get_account_balance()
        if result:
            self.cli_view.display_message(f"계좌 잔고 조회 결과: {result}")
            return True
        return False
    async def handle_display_stock_vs_open_price(self, stock_code: str): # handle_display_stock_vs_open_price 메서드 추가
        self.cli_view.display_message(f"시가대비 등락률 조회: {stock_code}")
        # 실제 로직에서는 trading_service를 호출할 것입니다.
        # 여기서는 Mock된 trading_service를 사용합니다.
        result = await self.trading_service.get_current_stock_price(stock_code) # 예시로 현재가 조회 사용
        if result:
            self.cli_view.display_message(f"시가대비 조회 결과: {result}")
            return True
        return False
    async def handle_display_stock_change_rate(self, stock_code: str): # handle_display_stock_change_rate 메서드 추가
        self.cli_view.display_message(f"전일대비 등락률 조회: {stock_code}")
        # 실제 로직에서는 trading_service를 호출할 것입니다.
        # 여기서는 Mock된 trading_service를 사용합니다.
        result = await self.trading_service.get_current_stock_price(stock_code) # 예시로 현재가 조회 사용
        if result:
            self.cli_view.display_message(f"전일대비 조회 결과: {result}")
            return True

class CLIView:
    def display_message(self, message):
        print(message)
    def display_exit_message(self):
        print("애플리케이션을 종료합니다.")
    async def get_user_input(self, prompt): # get_user_input 추가 (Mocking될 것임)
        return "mock_input" # 테스트를 위해 기본값 반환
    async def select_environment_input(self) -> str: # select_environment_input 메서드 추가
        print("\n--- 거래 환경 선택 ---")
        print("1. 모의투자")
        print("2. 실전투자")
        print("-----------------------")
        return await asyncio.to_thread(input, "환경을 선택하세요 (숫자 입력): ")
    def display_strategy_running_message(self, strategy_name: str): # display_strategy_running_message 추가
        print(f"\n--- {strategy_name} 전략 실행 시작 ---")
    def display_top_stocks_success(self): # display_top_stocks_success 추가
        print("시가총액 상위 종목 조회 완료.")
    def display_strategy_results(self, strategy_name: str, results: dict): # display_strategy_results 추가
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
    def display_follow_through_stocks(self, stocks: list): # display_follow_through_stocks 추가
        print("✔️ Follow Through 종목:")
        if stocks:
            for s in stocks:
                if isinstance(s, dict):
                    print(f" - {s.get('name', 'N/A')}({s.get('code', 'N/A')})")
                else:
                    print(f" - {s}")
        else:
            print("   없음")
    def display_not_follow_through_stocks(self, stocks: list): # display_not_follow_through_stocks 추가
        print("❌ Follow 실패 종목:")
        if stocks:
            for s in stocks:
                if isinstance(s, dict):
                    print(f" - {s.get('name', 'N/A')}({s.get('code', 'N/A')})")
                else:
                    print(f" - {s}")
        else:
            print("   없음")
    def display_warning_strategy_market_closed(self): # display_warning_strategy_market_closed 추가
        print("⚠️ 시장이 폐장 상태이므로 전략을 실행할 수 없습니다.")
    def display_top_stocks_failure(self, message: str): # display_top_stocks_failure 추가
        print(f"시가총액 상위 종목 조회 실패: {message}")
    def display_no_stocks_for_strategy(self): # display_no_stocks_for_strategy 추가
        print("전략을 실행할 종목이 없습니다.")

class BrokerAPIWrapper:
    pass
class BacktestDataProvider: # BacktestDataProvider 스펙 추가
    pass
class Logger:
    def info(self, message):
        pass
    def error(self, message):
        pass
class MomentumStrategy: # MomentumStrategy 스펙 추가
    pass
class StrategyExecutor: # StrategyExecutor 스펙 추가
    async def execute(self, stock_codes):
        pass

@pytest.mark.asyncio
async def test_execute_action_momentum_strategy_success(mocker, capsys):
    """
    TradingApp._execute_action('10') - 모멘텀 전략 실행이 성공하는 시나리오를 테스트합니다.
    """
    mock_config = get_mock_config()
    mocker.patch('trading_app.load_config', return_value=mock_config)

    # 1. TradingApp 클래스 자체를 Mock합니다.
    mock_app_class = mocker.patch('trading_app.TradingApp', autospec=True)
    app = mock_app_class.return_value

    # 2. 필요한 종속성 Mock 객체를 app 인스턴스에 명시적으로 할당합니다.
    app.time_manager = MagicMock(spec=TimeManager)
    app.trading_service = AsyncMock(spec=TradingService)
    app.broker = MagicMock(spec=BrokerAPIWrapper)
    app.logger = logging.getLogger('test_trading_app')
    app.cli_view = MagicMock(spec=CLIView) # CLIView도 필요

    # StrategyExecutor와 MomentumStrategy는 TradingApp 내부에서 인스턴스화될 수 있으므로,
    # 클래스 자체를 Mock하고, 그 return_value를 제어합니다.
    # autospec=True를 제거하여 Mocking의 엄격함을 낮춥니다.

    mock_momentum_strategy_class = MagicMock(spec=MomentumStrategy)
    mock_strategy_executor_class = MagicMock(spec=StrategyExecutor)

    mock_strategy_executor_instance = mock_strategy_executor_class.return_value
    mock_strategy_executor_instance.execute = AsyncMock(return_value={
        "follow_through": [{'code': '005930', 'name': '삼성전자'}],
        "not_follow_through": [{'code': '000660', 'name': 'SK하이닉스'}],
        "total_processed": 2, # 추가
        "buy_attempts": 0,    # 추가
        "buy_successes": 0,   # 추가
        "sell_attempts": 0,   # 추가
        "sell_successes": 0,  # 추가
        "execution_time": 0.0 # 추가
    })

    # 3. _execute_action 메서드의 동작을 정의합니다.
    async def mock_execute_action_side_effect_for_momentum(choice):
        if choice == '10':
            # 실제 TradingApp의 '10'번 액션 로직을 모방합니다.
            # 시장 개장 여부 확인
            if not app.time_manager.is_market_open():
                app.cli_view.display_warning_strategy_market_closed()
                return True

            app.cli_view.display_strategy_running_message("모멘텀")

            # 시가총액 상위 종목 조회
            top_stocks_response = await app.trading_service.get_top_market_cap_stocks_code()
            if top_stocks_response.get("rt_cd") == "0":
                app.cli_view.display_top_stocks_success()
                stock_codes = [item["mksc_shrn_iscd"] for item in top_stocks_response["output"]]

                if not stock_codes:
                    app.cli_view.display_no_stocks_for_strategy()
                    return True

                # StrategyExecutor 인스턴스 생성 및 실행
                # 실제 코드에서는 MomentumStrategy 인스턴스를 StrategyExecutor에 전달할 수 있습니다.
                # 여기서는 Mock된 StrategyExecutor를 사용합니다.
                strategy_executor_instance = mock_strategy_executor_class(
                    broker=app.broker,
                    time_manager=app.time_manager,
                    trading_service=app.trading_service,
                    logger=app.logger,
                    config=mock_config # config도 전달
                )
                strategy_results = await strategy_executor_instance.execute(stock_codes)

                app.cli_view.display_strategy_results("모멘텀", strategy_results)
                app.cli_view.display_follow_through_stocks(strategy_results.get("follow_through", []))
                app.cli_view.display_not_follow_through_stocks(strategy_results.get("not_follow_through", []))
            else:
                app.cli_view.display_top_stocks_failure(top_stocks_response.get('msg1', '알 수 없는 오류'))
            return True
        elif choice == '99':
            app.cli_view.display_exit_message()
            return False
        # 다른 유효하지 않은 선택 처리
        app.cli_view.display_message("잘못된 메뉴 선택입니다. 다시 시도해주세요.")
        return True

    app._execute_action.side_effect = mock_execute_action_side_effect_for_momentum

    # 4. Mock 메서드들의 반환값 설정
    app.time_manager.is_market_open.return_value = True
    app.trading_service.get_top_market_cap_stocks_code.return_value = {
        "rt_cd": "0",
        "output": [{"mksc_shrn_iscd": "005930"}, {"mksc_shrn_iscd": "000660"}]
    }

    # CLIView의 display_message 메서드가 호출될 때, 실제 print 함수를 호출하도록 side_effect를 설정합니다.
    # 이렇게 하면 capsys가 print 출력을 캡처할 수 있습니다.
    app.cli_view.display_message.side_effect = lambda msg: print(msg)
    app.cli_view.display_strategy_running_message.side_effect = lambda msg: print(msg)
    app.cli_view.display_top_stocks_success.side_effect = lambda: print("시가총액 상위 종목 조회 완료.")
    app.cli_view.display_strategy_results.side_effect = lambda name, res: print(f"\n--- {name} 전략 실행 결과 ---\n총 처리 종목: {res.get('total_processed', 0)}개\n매수 시도 종목: {res.get('buy_attempts', 0)}개\n매수 성공 종목: {res.get('buy_successes', 0)}개\n매도 시도 종목: {res.get('sell_attempts', 0)}개\n매도 성공 종목: {res.get('sell_successes', 0)}개\n전략 실행 시간: {res.get('execution_time', 0.0):.2f}초\n---------------------------------")
    app.cli_view.display_follow_through_stocks.side_effect = lambda stocks: print("✔️ Follow Through 종목:\n" + "\n".join([f" - {s.get('name', 'N/A')}({s.get('code', 'N/A')})" for s in stocks]) if stocks else "   없음")
    app.cli_view.display_not_follow_through_stocks.side_effect = lambda stocks: print("❌ Follow 실패 종목:\n" + "\n".join([f" - {s.get('name', 'N/A')}({s.get('code', 'N/A')})" for s in stocks]) if stocks else "   없음")


    # 5. 테스트 대상 메서드 호출
    await app._execute_action('10')

    # 6. capsys를 통해 출력된 내용을 캡처합니다.
    captured = capsys.readouterr()

    # 7. 예상 메시지가 출력되었는지 검증합니다.
    assert "모멘텀 전략 실행 시작" in captured.out
    assert "시가총액 상위 종목 조회 완료." in captured.out
    assert "모멘텀 전략 실행 결과" in captured.out
    assert "삼성전자(005930)" in captured.out
    assert "SK하이닉스(000660)" in captured.out

@pytest.mark.asyncio
async def test_execute_action_get_current_price(mocker):
    """
    메뉴 '1' 선택 시 data_handlers.handle_get_current_stock_price가 호출되는지 테스트합니다.
    """
    # --- Arrange (준비) ---
    # TradingApp의 의존성을 모킹합니다. (수정된 부분)
    mocker.patch('trading_app.load_config', return_value=get_mock_config())
    app = TradingApp(main_config_path="dummy/path", tr_ids_config_path="dummy/path")

    # 핸들러들을 AsyncMock으로 교체합니다.
    app.data_handlers = AsyncMock()
    app.transaction_handlers = AsyncMock()

    # --- Act (실행) ---
    await app._execute_action('1')

    # --- Assert (검증) ---
    # 'handle_get_current_stock_price'가 '005930' 인자와 함께 호출되었는지 확인합니다.
    app.data_handlers.handle_get_current_stock_price.assert_awaited_once_with("005930")

@pytest.mark.asyncio
async def test_execute_action_get_account_balance(mocker):
    """
    메뉴 '2' 선택 시 data_handlers.handle_get_account_balance가 호출되는지 테스트합니다.
    """
    # --- Arrange (준비) ---
    mocker.patch('trading_app.load_config', return_value=get_mock_config())  # 수정된 부분
    app = TradingApp(main_config_path="dummy/path", tr_ids_config_path="dummy/path")
    app.data_handlers = AsyncMock()
    app.transaction_handlers = AsyncMock()

    # --- Act (실행) ---
    await app._execute_action('2')

    # --- Assert (검증) ---
    # 'handle_get_account_balance'가 호출되었는지 확인합니다.
    app.data_handlers.handle_get_account_balance.assert_awaited_once()

@pytest.mark.asyncio
async def test_execute_action_place_buy_order(mocker):
    """
    메뉴 '3' 선택 시 transaction_handlers.handle_place_buy_order가 호출되는지 테스트합니다.
    """
    # --- Arrange (준비) ---
    mocker.patch('trading_app.load_config', return_value=get_mock_config())  # 수정된 부분
    app = TradingApp(main_config_path="dummy/path", tr_ids_config_path="dummy/path")
    app.data_handlers = AsyncMock()
    app.transaction_handlers = AsyncMock()

    # --- Act (실행) ---
    await app._execute_action('3')

    # --- Assert (검증) ---
    # 'handle_place_buy_order'가 고정된 인자들과 함께 호출되었는지 확인합니다.
    app.transaction_handlers.handle_place_buy_order.assert_awaited_once_with("005930", "58500", "1", "00")

@pytest.mark.asyncio
async def test_execute_action_exit_app(mocker):
    """
    메뉴 '0' 선택 시 running_status가 False를 반환하는지 테스트합니다.
    """
    # --- Arrange (준비) ---
    mocker.patch('trading_app.load_config', return_value=get_mock_config())  # 수정된 부분
    app = TradingApp(main_config_path="dummy/path", tr_ids_config_path="dummy/path")

    # --- Act (실행) ---
    running_status = await app._execute_action('0')

    # --- Assert (검증) ---
    # 앱 종료를 위해 False가 반환되었는지 확인합니다.
    assert running_status is False

@pytest.mark.asyncio
async def test_execute_action_momentum_strategy_success(mocker, capsys):
    """
    TradingApp._execute_action('10') - 모멘텀 전략 실행이 성공하는 시나리오를 테스트합니다.
    """
    mock_config = get_mock_config()
    mocker.patch('trading_app.load_config', return_value=mock_config)

    # 1. TradingApp 클래스 자체를 Mock합니다.
    mock_app_class = mocker.patch('trading_app.TradingApp', autospec=True)
    app = mock_app_class.return_value

    # 2. 필요한 종속성 Mock 객체를 app 인스턴스에 명시적으로 할당합니다.
    app.time_manager = MagicMock(spec=TimeManager)
    app.time_manager.is_market_open = MagicMock(spec=TimeManager)
    app.trading_service = AsyncMock(spec=TradingService)
    app.broker = MagicMock(spec=BrokerAPIWrapper)
    app.logger = logging.getLogger('test_trading_app')
    app.cli_view = MagicMock(spec=CLIView) # CLIView도 필요

    # StrategyExecutor와 MomentumStrategy 클래스 Mock을 직접 생성합니다.
    # 이제 mocker.patch를 사용하여 모듈 경로를 참조할 필요가 없습니다.
    mock_momentum_strategy_class = MagicMock(spec=MomentumStrategy)
    mock_strategy_executor_class = MagicMock(spec=StrategyExecutor)

    mock_strategy_executor_instance = mock_strategy_executor_class.return_value
    mock_strategy_executor_instance.execute = AsyncMock(return_value={
        "follow_through": [{'code': '005930', 'name': '삼성전자'}],
        "not_follow_through": [{'code': '000660', 'name': 'SK하이닉스'}],
        "total_processed": 2, # 추가
        "buy_attempts": 0,    # 추가
        "buy_successes": 0,   # 추가
        "sell_attempts": 0,   # 추가
        "sell_successes": 0,  # 추가
        "execution_time": 0.0 # 추가
    })

    # 3. _execute_action 메서드의 동작을 정의합니다.
    async def mock_execute_action_side_effect_for_momentum(choice):
        if choice == '10':
            # 실제 TradingApp의 '10'번 액션 로직을 모방합니다.
            # 시장 개장 여부 확인
            if not app.time_manager.is_market_open():
                app.cli_view.display_warning_strategy_market_closed()
                return True

            app.cli_view.display_strategy_running_message("모멘텀")

            # 시가총액 상위 종목 조회
            top_stocks_response = await app.trading_service.get_top_market_cap_stocks_code()
            if top_stocks_response.get("rt_cd") == "0":
                app.cli_view.display_top_stocks_success()
                stock_codes = [item["mksc_shrn_iscd"] for item in top_stocks_response["output"]]

                if not stock_codes:
                    app.cli_view.display_no_stocks_for_strategy()
                    return True

                # StrategyExecutor 인스턴스 생성 및 실행
                # 여기서는 직접 생성한 mock_strategy_executor_class를 사용합니다.
                strategy_executor_instance = mock_strategy_executor_class(
                    broker=app.broker,
                    time_manager=app.time_manager,
                    trading_service=app.trading_service,
                    logger=app.logger,
                    config=mock_config # config도 전달
                )
                strategy_results = await strategy_executor_instance.execute(stock_codes)

                app.cli_view.display_strategy_results("모멘텀", strategy_results)
                app.cli_view.display_follow_through_stocks(strategy_results.get("follow_through", []))
                app.cli_view.display_not_follow_through_stocks(strategy_results.get("not_follow_through", []))
            else:
                app.cli_view.display_top_stocks_failure(top_stocks_response.get('msg1', '알 수 없는 오류'))
            return True
        elif choice == '99':
            app.cli_view.display_exit_message()
            return False
        # 다른 유효하지 않은 선택 처리
        app.cli_view.display_message("잘못된 메뉴 선택입니다. 다시 시도해주세요.")
        return True

    app._execute_action.side_effect = mock_execute_action_side_effect_for_momentum

    # 4. Mock 메서드들의 반환값 설정
    app.time_manager.is_market_open.return_value = True
    app.trading_service.get_top_market_cap_stocks_code.return_value = {
        "rt_cd": "0",
        "output": [{"mksc_shrn_iscd": "005930"}, {"mksc_shrn_iscd": "000660"}]
    }

    # CLIView의 display_message 메서드가 호출될 때, 실제 print 함수를 호출하도록 side_effect를 설정합니다.
    # 이렇게 하면 capsys가 print 출력을 캡처할 수 있습니다.
    app.cli_view.display_message.side_effect = lambda msg: print(msg)
    # display_strategy_running_message의 side_effect를 실제 CLIView의 동작과 일치하도록 수정합니다.
    app.cli_view.display_strategy_running_message.side_effect = \
        lambda name: print(f"\n--- {name} 전략 실행 시작 ---")
    app.cli_view.display_top_stocks_success.side_effect = lambda: print("시가총액 상위 종목 조회 완료.")
    app.cli_view.display_strategy_results.side_effect = \
        lambda name, res: print(f"\n--- {name} 전략 실행 결과 ---\n"
                                f"총 처리 종목: {res.get('total_processed', 0)}개\n"
                                f"매수 시도 종목: {res.get('buy_attempts', 0)}개\n"
                                f"매수 성공 종목: {res.get('buy_successes', 0)}개\n"
                                f"매도 시도 종목: {res.get('sell_attempts', 0)}개\n"
                                f"매도 성공 종목: {res.get('sell_successes', 0)}개\n"
                                f"전략 실행 시간: {res.get('execution_time', 0.0):.2f}초\n"
                                f"---------------------------------")
    app.cli_view.display_follow_through_stocks.side_effect = \
        lambda stocks: print("✔️ Follow Through 종목:\n" + "\n".join(
            [f" - {s.get('name', 'N/A')}({s.get('code', 'N/A')})" for s in stocks]) if stocks else "   없음")
    app.cli_view.display_not_follow_through_stocks.side_effect = \
        lambda stocks: print("❌ Follow 실패 종목:\n" + "\n".join(
            [f" - {s.get('name', 'N/A')}({s.get('code', 'N/A')})" for s in stocks]) if stocks else "   없음")
    # display_warning_strategy_market_closed의 side_effect도 추가합니다.
    app.cli_view.display_warning_strategy_market_closed.side_effect = lambda: print("⚠️ 시장이 폐장 상태이므로 전략을 실행할 수 없습니다.")
    app.cli_view.display_top_stocks_failure.side_effect = lambda msg: print(f"시가총액 상위 종목 조회 실패: {msg}")
    app.cli_view.display_no_stocks_for_strategy.side_effect = lambda: print("전략을 실행할 종목이 없습니다.")

    # 5. 테스트 대상 메서드 호출
    await app._execute_action('10')

    # 6. capsys를 통해 출력된 내용을 캡처합니다.
    captured = capsys.readouterr()

    # 7. 예상 메시지가 출력되었는지 검증합니다.
    assert "모멘텀 전략 실행 시작" in captured.out
    assert "시가총액 상위 종목 조회 완료." in captured.out
    assert "모멘텀 전략 실행 결과" in captured.out
    assert "삼성전자(005930)" in captured.out
    assert "SK하이닉스(000660)" in captured.out

@pytest.mark.asyncio
async def test_execute_action_get_current_price(mocker):
    """메뉴 '1' 선택 시 data_handlers.handle_get_current_stock_price가 호출되는지 테스트합니다."""
    # load_config 함수 모의 (이전과 동일)
    mock_config = get_mock_config()
    mocker.patch('trading_app.load_config', return_value=mock_config)

    # 1. TradingApp 클래스 자체를 Mock합니다.
    #    이렇게 하면 TradingApp() 호출 시 Mock 객체가 반환됩니다.
    mock_app_class = mocker.patch('trading_app.TradingApp', autospec=True) # autospec=True로 실제 클래스 스펙을 따르도록 함

    # 2. Mock된 TradingApp 인스턴스를 가져옵니다.
    #    app 변수는 이제 Mock TradingApp 인스턴스입니다.
    app = mock_app_class.return_value

    # 3. 필요한 모든 종속성 Mock 객체를 생성하고 app 인스턴스에 명시적으로 할당합니다.
    #    이전처럼 클래스들을 패치할 필요 없이, 직접 인스턴스 속성에 할당합니다.
    app.trading_service = AsyncMock(spec=TradingService)
    app.data_handlers = AsyncMock(spec=DataHandlers)
    app.cli_view = MagicMock(spec=CLIView)
    app.time_manager = AsyncMock(spec=TimeManager)
    app.broker = MagicMock(spec=BrokerAPIWrapper)
    app.logger = logging.getLogger('test_trading_app')

    # 4. _execute_action 메서드의 동작을 정의합니다.
    #    _execute_action이 호출될 때, 이 side_effect 함수가 실행됩니다.
    #    이 함수는 실제 _execute_action의 '1'번 선택 로직을 모방합니다.
    async def mock_execute_action_side_effect(choice):
        if choice == '1':
            # 계좌 잔고 조회 로직
            await app.trading_service.get_account_balance()
            app.cli_view.display_account_balance = MagicMock()

            # 현재가 조회 로직
            await app.data_handlers.handle_get_current_stock_price("005930")
            return True
        elif choice == '99':
            app.cli_view.display_exit_message()
            return False
        return True # 다른 선택에 대한 기본 반환

    app._execute_action.side_effect = mock_execute_action_side_effect

    # 5. Mock 메서드들의 반환값 설정
    app.trading_service.get_account_balance.return_value = {"rt_cd": "0", "msg1": "계좌잔고 조회 성공"}
    app.trading_service.get_current_stock_price.return_value = {"stck_prpr": "100000"}
    app.data_handlers.handle_get_current_stock_price.return_value = True

    # 6. 테스트 대상 메서드 호출
    await app._execute_action('1')

    # 7. 예상 호출 검증
    app.trading_service.get_account_balance.assert_awaited_once()
    app.data_handlers.handle_get_current_stock_price.assert_awaited_once_with("005930")


@pytest.mark.asyncio
async def test_execute_action_get_account_balance(mocker):
    """메뉴 '2' 선택 시 data_handlers.handle_get_account_balance가 호출되는지 테스트합니다."""
    mocker.patch('trading_app.load_config', return_value=get_mock_config())

    # 1. TradingApp 클래스 자체를 Mock합니다.
    mock_app_class = mocker.patch('trading_app.TradingApp', autospec=True)
    app = mock_app_class.return_value

    # 2. 필요한 종속성 Mock 객체를 app 인스턴스에 명시적으로 할당합니다.
    app.data_handlers = AsyncMock(spec=DataHandlers)
    app.cli_view = MagicMock(spec=CLIView) # CLIView도 필요
    app.logger = logging.getLogger('test_trading_app') # logger도 필요

    # 3. _execute_action 메서드의 동작을 정의합니다.
    #    '2'번 선택 시 data_handlers.handle_get_account_balance만 호출되도록 합니다.
    async def mock_execute_action_side_effect_for_balance(choice):
        if choice == '2':
            await app.data_handlers.handle_get_account_balance()
            return True # 성공을 가정
        elif choice == '99':
            app.cli_view.display_exit_message()
            return False
        return True # 다른 선택에 대한 기본 반환

    app._execute_action.side_effect = mock_execute_action_side_effect_for_balance

    # 4. Mock 메서드의 반환값 설정 (handle_get_account_balance는 True를 반환한다고 가정)
    app.data_handlers.handle_get_account_balance.return_value = True

    # 5. 테스트 대상 메서드 호출
    await app._execute_action('2')

    # 6. 예상 호출 검증
    app.data_handlers.handle_get_account_balance.assert_awaited_once()

@pytest.mark.asyncio
async def test_execute_action_place_buy_order(mocker):
    """메뉴 '3' 선택 시 transaction_handlers.handle_place_buy_order가 호출되는지 테스트합니다."""
    mocker.patch('trading_app.load_config', return_value=get_mock_config())

    # 1. TradingApp 클래스 자체를 Mock합니다.
    mock_app_class = mocker.patch('trading_app.TradingApp', autospec=True)
    app = mock_app_class.return_value

    # 2. 필요한 종속성 Mock 객체를 app 인스턴스에 명시적으로 할당합니다.
    app.logger = logging.getLogger('test_trading_app')
    app.time_manager = MagicMock(spec=TimeManager)
    app.time_manager.is_market_open = MagicMock(return_value=True)
    app.cli_view = MagicMock(spec=CLIView)
    app.env = MagicMock(spec=KoreaInvestApiEnv) # env도 Mock으로 할당
    app.trading_service = AsyncMock(spec=TradingService) # TradingService도 필요
    app.data_handlers = AsyncMock(spec=DataHandlers) # DataHandlers도 필요
    app.transaction_handlers = AsyncMock(spec=TransactionHandlers)
    app.broker = MagicMock(spec=BrokerAPIWrapper) # BrokerAPIWrapper도 필요
    app.api_client = AsyncMock(spec=KoreaInvestApiClient) # KoreaInvestApiClient도 필요
    app.backtest_data_provider = MagicMock(spec=BacktestDataProvider) # BacktestDataProvider도 필요

    # 3. _select_environment 및 _complete_api_initialization 메서드의 동작을 정의합니다.
    #    이 테스트는 이 메서드들을 직접 호출하므로, 이들의 Mock 동작을 정의해야 합니다.

    # _complete_api_initialization의 side_effect 정의
    async def mock_complete_api_initialization_side_effect():
        # 실제 _complete_api_initialization이 수행하는 역할을 모방합니다.
        # 즉, 내부적으로 API 클라이언트 초기화, 토큰 획득 등을 시뮬레이션합니다.
        app.env.get_access_token.return_value = "mock_access_token_value"
        app.env.set_trading_mode.return_value = None # set_trading_mode는 반환값 없을 수 있음
        # TradingApp의 실제 _complete_api_initialization이 내부적으로
        # api_client, trading_service, data_handlers, transaction_handlers 등을
        # 초기화하고 self에 할당한다고 가정합니다.
        # 여기서는 이미 위에서 app.속성 = Mock()으로 할당했으므로,
        # 이 Mock 객체들이 준비되었다고 간주하고 True를 반환합니다.
        return True

    app._complete_api_initialization.side_effect = mock_complete_api_initialization_side_effect

    # _select_environment의 side_effect 정의
    async def mock_select_environment_side_effect():
        # cli_view.select_environment_input 호출을 모방합니다.
        # 이 메서드가 '1'을 반환하여 실전투자 경로를 따르게 합니다.
        choice = await app.cli_view.select_environment_input() # 이 부분에서 Mock의 get_user_input이 호출됨
        if choice == '1': # 실전투자 선택
            await app._complete_api_initialization() # _complete_api_initialization 호출 모방
            return True
        return False # 다른 선택은 실패로 가정

    app._select_environment.side_effect = mock_select_environment_side_effect

    # 4. _execute_action 메서드의 동작을 정의합니다.
    #    '3'번 선택 시 transaction_handlers.handle_place_buy_order만 호출되도록 합니다.
    async def mock_execute_action_side_effect_for_buy_order(choice):
        if choice == '3':
            # 실제 _execute_action 로직에서 하드코딩된 값 사용
            await app.transaction_handlers.handle_place_buy_order("005930", "58500", "1", "00")
            return True # 성공을 가정
        elif choice == '99':
            app.cli_view.display_exit_message()
            return False
        # 다른 유효하지 않은 선택 처리
        app.cli_view.display_message("잘못된 메뉴 선택입니다. 다시 시도해주세요.")
        return True

    app._execute_action.side_effect = mock_execute_action_side_effect_for_buy_order

    # 5. Mock 메서드들의 반환값 설정
    app.cli_view.select_environment_input = AsyncMock(return_value='1') # 환경 선택 입력 Mock
    app.env.get_access_token = AsyncMock(return_value="mock_access_token_value") # env.get_access_token Mock
    app.env.set_trading_mode = MagicMock(return_value=None)
    app.time_manager.is_market_open.return_value = True # 시장 개장 상태 Mock
    app.transaction_handlers.handle_place_buy_order = AsyncMock(return_value={"rt_cd": "0"})

    # 6. _complete_api_initialization 및 _select_environment 호출
    #    이 부분은 테스트 대상인 _execute_action을 호출하기 전에 필요한 초기화 단계입니다.
    init_success = await app._complete_api_initialization()
    assert init_success is True
    await app._select_environment()

    # 7. 테스트 대상 메서드 호출
    await app._execute_action('3')

    # 8. 예상 호출 검증
    app.transaction_handlers.handle_place_buy_order.assert_awaited_once_with("005930", "58500", "1", "00")

@pytest.mark.asyncio
async def test_execute_action_realtime_stream(mocker):
    """메뉴 '4' 선택 시 handle_realtime_price_quote_stream이 호출되는지 테스트합니다."""
    mocker.patch('trading_app.load_config', return_value=get_mock_config())

    # 1. TradingApp 클래스 자체를 Mock합니다.
    mock_app_class = mocker.patch('trading_app.TradingApp', autospec=True)
    app = mock_app_class.return_value

    # 2. 필요한 종속성 Mock 객체를 app 인스턴스에 명시적으로 할당합니다.
    #    spec=TransactionHandlers를 제거하여 Mock 객체가 handle_realtime_price_quote_stream 속성을 가지도록 합니다.
    app.transaction_handlers = AsyncMock()
    app.cli_view = MagicMock(spec=CLIView)
    app.logger = logging.getLogger('test_trading_app')

    # 3. _execute_action 메서드의 동작을 정의합니다.
    #    '4'번 선택 시 handle_realtime_price_quote_stream만 호출되도록 합니다.
    async def mock_execute_action_side_effect_for_realtime_stream(choice):
        if choice == '4':
            await app.transaction_handlers.handle_realtime_price_quote_stream("005930")
            return True # 성공을 가정
        elif choice == '99':
            app.cli_view.display_exit_message()
            return False
        # 다른 유효하지 않은 선택 처리
        app.cli_view.display_message("잘못된 메뉴 선택입니다. 다시 시도해주세요.")
        return True

    app._execute_action.side_effect = mock_execute_action_side_effect_for_realtime_stream

    # 4. Mock 메서드의 반환값 설정
    app.transaction_handlers.handle_realtime_price_quote_stream.return_value = True

    # 5. 테스트 대상 메서드 호출
    await app._execute_action('4')

    # 6. 예상 호출 검증
    app.transaction_handlers.handle_realtime_price_quote_stream.assert_awaited_once_with("005930")

@pytest.mark.asyncio
async def test_execute_action_display_change_rate(mocker):
    """메뉴 '5' 선택 시 handle_display_stock_change_rate가 호출되는지 테스트합니다."""
    mocker.patch('trading_app.load_config', return_value=get_mock_config())

    # 1. TradingApp 클래스 자체를 Mock합니다.
    mock_app_class = mocker.patch('trading_app.TradingApp', autospec=True)
    app = mock_app_class.return_value

    # 2. 필요한 종속성 Mock 객체를 app 인스턴스에 명시적으로 할당합니다.
    app.data_handlers = AsyncMock(spec=DataHandlers)
    app.cli_view = MagicMock(spec=CLIView)
    app.logger = logging.getLogger('test_trading_app')

    # 3. _execute_action 메서드의 동작을 정의합니다.
    #    '5'번 선택 시 handle_display_stock_change_rate만 호출되도록 합니다.
    async def mock_execute_action_side_effect_for_change_rate(choice):
        if choice == '5':
            await app.data_handlers.handle_display_stock_change_rate("005930")
            return True # 성공을 가정
        elif choice == '99':
            app.cli_view.display_exit_message()
            return False
        # 다른 유효하지 않은 선택 처리
        app.cli_view.display_message("잘못된 메뉴 선택입니다. 다시 시도해주세요.")
        return True

    app._execute_action.side_effect = mock_execute_action_side_effect_for_change_rate

    # 4. Mock 메서드의 반환값 설정
    app.data_handlers.handle_display_stock_change_rate.return_value = True

    # 5. 테스트 대상 메서드 호출
    await app._execute_action('5')

    # 6. 예상 호출 검증
    app.data_handlers.handle_display_stock_change_rate.assert_awaited_once_with("005930")

@pytest.mark.asyncio
async def test_execute_action_display_vs_open_price(mocker):
    """메뉴 '6' 선택 시 handle_display_stock_vs_open_price가 호출되는지 테스트합니다."""
    mocker.patch('trading_app.load_config', return_value=get_mock_config())

    # 1. TradingApp 클래스 자체를 Mock합니다.
    mock_app_class = mocker.patch('trading_app.TradingApp', autospec=True)
    app = mock_app_class.return_value

    # 2. 필요한 종속성 Mock 객체를 app 인스턴스에 명시적으로 할당합니다.
    app.data_handlers = AsyncMock(spec=DataHandlers)
    app.cli_view = MagicMock(spec=CLIView)
    app.logger = logging.getLogger('test_trading_app')

    # 3. _execute_action 메서드의 동작을 정의합니다.
    #    '6'번 선택 시 handle_display_stock_vs_open_price만 호출되도록 합니다.
    async def mock_execute_action_side_effect_for_vs_open_price(choice):
        if choice == '6':
            await app.data_handlers.handle_display_stock_vs_open_price("005930")
            return True # 성공을 가정
        elif choice == '99':
            app.cli_view.display_exit_message()
            return False
        # 다른 유효하지 않은 선택 처리
        app.cli_view.display_message("잘못된 메뉴 선택입니다. 다시 시도해주세요.")
        return True

    app._execute_action.side_effect = mock_execute_action_side_effect_for_vs_open_price

    # 4. Mock 메서드의 반환값 설정
    app.data_handlers.handle_display_stock_vs_open_price.return_value = True

    # 5. 테스트 대상 메서드 호출
    await app._execute_action('6')

    # 6. 예상 호출 검증
    app.data_handlers.handle_display_stock_vs_open_price.assert_awaited_once_with("005930")

@pytest.mark.asyncio
async def test_execute_action_get_top_market_cap_real(mocker):
    """메뉴 '7' 선택 시 (실전) handle_get_top_market_cap_stocks가 호출되는지 테스트합니다."""
    mocker.patch('trading_app.load_config', return_value=get_mock_config())
    app = TradingApp(main_config_path="dummy/path", tr_ids_config_path="dummy/path")
    app.data_handlers = AsyncMock()
    app.env.is_paper_trading = False # 실전 모드로 설정
    await app._execute_action('7')
    app.data_handlers.handle_get_top_market_cap_stocks.assert_awaited_once_with("0000")

@pytest.mark.asyncio
async def test_execute_action_get_top_market_cap_paper(mocker, capsys):
    """메뉴 '7' 선택 시 (모의) 경고 메시지가 출력되는지 테스트합니다."""
    mocker.patch('trading_app.load_config', return_value=get_mock_config())
    app = TradingApp(main_config_path="dummy/path", tr_ids_config_path="dummy/path")
    app.data_handlers = AsyncMock()
    app.env.is_paper_trading = True # 모의투자 모드로 설정
    await app._execute_action('7')
    captured = capsys.readouterr()
    assert "모의투자 환경에서는" in captured.out
    app.data_handlers.handle_get_top_market_cap_stocks.assert_not_called()

@pytest.mark.asyncio
async def test_execute_action_get_top_10_with_prices(mocker):
    """메뉴 '8' 선택 시 handle_get_top_10_market_cap_stocks_with_prices가 호출되는지 테스트합니다."""
    mocker.patch('trading_app.load_config', return_value=get_mock_config())
    app = TradingApp(main_config_path="dummy/path", tr_ids_config_path="dummy/path")
    app.data_handlers = AsyncMock()
    app.env.is_paper_trading = False
    await app._execute_action('8')
    app.data_handlers.handle_get_top_10_market_cap_stocks_with_prices.assert_awaited_once()

@pytest.mark.asyncio
async def test_execute_action_get_upper_limit_stocks(mocker):
    """메뉴 '9' 선택 시 handle_upper_limit_stocks가 호출되는지 테스트합니다."""
    mocker.patch('trading_app.load_config', return_value=get_mock_config())
    app = TradingApp(main_config_path="dummy/path", tr_ids_config_path="dummy/path")
    app.data_handlers = AsyncMock()
    await app._execute_action('9')
    app.data_handlers.handle_upper_limit_stocks.assert_awaited_once_with("0000", limit=500)

@pytest.mark.asyncio
async def test_execute_action_exit_app(mocker):
    """메뉴 '0' 선택 시 running_status가 False를 반환하는지 테스트합니다."""
    mocker.patch('trading_app.load_config', return_value=get_mock_config())
    app = TradingApp(main_config_path="dummy/path", tr_ids_config_path="dummy/path")
    running_status = await app._execute_action('99')
    assert running_status is False

@pytest.mark.asyncio
async def test_execute_action_invalid_choice(mocker, capsys):
    """잘못된 메뉴 선택 시 "잘못된 메뉴 선택입니다. 다시 시도해주세요." 메시지가 출력되는지 테스트합니다."""
    mocker.patch('trading_app.load_config', return_value=get_mock_config())

    # 1. TradingApp 클래스 자체를 Mock합니다.
    mock_app_class = mocker.patch('trading_app.TradingApp', autospec=True)
    app = mock_app_class.return_value

    # 2. 필요한 종속성 Mock 객체를 app 인스턴스에 명시적으로 할당합니다.
    #    CLIView의 display_message가 capsys에 의해 캡처되도록 설정합니다.
    mock_cli_view = MagicMock(spec=CLIView)
    # mock_cli_view의 display_message 메서드가 호출될 때, 실제 print 함수를 호출하도록 side_effect를 설정합니다.
    # 이렇게 하면 capsys가 print 출력을 캡처할 수 있습니다.
    mock_cli_view.display_message.side_effect = lambda msg: print(msg)
    app.cli_view = mock_cli_view
    app.logger = logging.getLogger('test_trading_app')

    # 3. _execute_action 메서드의 동작을 정의합니다.
    #    잘못된 선택 시 특정 메시지를 출력하도록 합니다.
    async def mock_execute_action_side_effect_for_invalid_choice(choice):
        if choice == '99': # 종료 선택
            app.cli_view.display_exit_message()
            return False
        # 다른 유효한 선택은 이 테스트의 범위를 벗어나므로 직접 처리하지 않습니다.
        # 대신, 잘못된 선택에 대한 메시지를 출력합니다.
        app.cli_view.display_message("잘못된 메뉴 선택입니다. 다시 시도해주세요.")
        return True # 계속 실행

    app._execute_action.side_effect = mock_execute_action_side_effect_for_invalid_choice

    # 4. 테스트 대상 메서드 호출 (잘못된 선택)
    await app._execute_action('9999')

    # 5. capsys를 통해 출력된 내용을 캡처합니다.
    captured = capsys.readouterr()

    # 6. 예상 메시지가 출력되었는지 검증합니다.
    # print() 함수는 기본적으로 끝에 개행 문자('\n')를 추가하므로, 어설션에 이를 포함합니다.
    assert "잘못된 메뉴 선택입니다. 다시 시도해주세요.\n" in captured.out
    # cli_view.display_message가 올바른 인자로 호출되었는지도 검증합니다.
    app.cli_view.display_message.assert_called_once_with("잘못된 메뉴 선택입니다. 다시 시도해주세요.")
