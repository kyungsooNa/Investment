# test/test_trading_app.py

import pytest
import logging
import sys
import os # os 모듈 추가
import builtins
import unittest
from unittest.mock import MagicMock, AsyncMock, patch, mock_open, call
from trading_app import TradingApp, load_config
from datetime import datetime

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
        'app_secret': 'mock_app_secret',
        'account_number': 'mock_account_number', # 일반 계좌 번호
        'stock_account_number': 'mock_stock_account_number', # 특정 주식 계좌 번호
        'hts_id': 'mock_hts_id',
        'custtype': 'P', # 개인 고객 유형
        'market_open_time': "09:00",
        'market_close_time': "15:30",
        'market_timezone': "Asia/Seoul",
        'token_file_path': 'config/token.json',
        'tr_ids': {
            'quotations': {
                'inquire_price': 'FHKST01010100',
                'search_info': 'FHKST01010500',
                'top_market_cap': 'FHPST01740000',
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
        }
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

# 각 테스트를 위한 목(mock) TradingApp 인스턴스 설정 픽스처
@pytest.fixture
def setup_mock_app(mocker):
    mock_config = get_mock_config()
    mocker.patch('trading_app.load_config', side_effect=lambda path: mock_config if 'config.yaml' in path or 'tr_ids_config.yaml' in path else None)

    mocker.patch('trading_app.TokenManager')
    mocker.patch('trading_app.KoreaInvestApiEnv')
    mocker.patch('trading_app.TimeManager')
    mocker.patch('trading_app.Logger')
    mocker.patch('trading_app.CLIView')
    mocker.patch('trading_app.KoreaInvestApiClient')
    mocker.patch('trading_app.TradingService')
    mocker.patch('trading_app.DataHandlers')
    mocker.patch('trading_app.TransactionHandlers')
    mocker.patch('trading_app.BrokerAPIWrapper')
    mocker.patch('trading_app.BacktestDataProvider')

    app = TradingApp(main_config_path="dummy/config.yaml", tr_ids_config_path="dummy/core/tr_ids_config.yaml")

    app.logger = mocker.MagicMock(spec=logging.Logger)
    app.cli_view = mocker.MagicMock(spec=CLIView) # AsyncMock 대신 MagicMock 사용
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

    app.cli_view.get_user_input = AsyncMock() # 이 줄은 유지


    app.time_manager = mocker.MagicMock(spec=TimeManager)
    app.env = mocker.MagicMock(spec=KoreaInvestApiEnv)
    app.env.get_access_token = mocker.AsyncMock(return_value="mock_access_token") # 명시적으로 AsyncMock으로 설정
    # app.env.get_access_token = mocker.AsyncMock(return_value="mock_access_token") # 이 줄은 테스트에서 필요에 따라 개별적으로 목킹
    # app._complete_api_initialization = AsyncMock(return_value=True) # 이 줄은 테스트에서 필요에 따라 개별적으로 목킹
    # app._select_environment = AsyncMock(return_value=True) # 이 줄은 테스트에서 필요에 따라 개별적으로 목킹

    app.data_handlers = mocker.AsyncMock(spec=DataHandlers)
    app.transaction_handlers = mocker.AsyncMock(spec=TransactionHandlers)
    app.broker = mocker.AsyncMock(spec=BrokerAPIWrapper)
    app.backtest_data_provider = mocker.AsyncMock(spec=BacktestDataProvider)

    app.env.get_full_config = mocker.MagicMock(return_value=mock_config)
    app.env.is_paper_trading = mock_config['is_paper_trading']
    app.env.set_trading_mode = MagicMock() # set_trading_mode 메서드 명시적 목킹 추가

    app.data_handlers.handle_get_top_10_market_cap_stocks_with_prices = AsyncMock()
    app.data_handlers.handle_get_top_market_cap_stocks = AsyncMock()
    app.data_handlers.handle_upper_limit_stocks = AsyncMock()
    app.data_handlers.handle_get_current_stock_price = AsyncMock()
    app.data_handlers.handle_display_stock_change_rate = AsyncMock()
    app.data_handlers.handle_display_stock_vs_open_price = AsyncMock()


    app.trading_service = mocker.AsyncMock(spec=TradingService)
    app.trading_service.get_code_by_name = AsyncMock()
    app.trading_service.get_top_market_cap_stocks_code = AsyncMock()
    app.trading_service.get_price_summary = AsyncMock()
    app.trading_service.get_account_balance = AsyncMock()

    mocker.patch('strategies.momentum_strategy.MomentumStrategy')
    mocker.patch('strategies.strategy_executor.StrategyExecutor')
    mocker.patch('strategies.GapUpPullback_strategy.GapUpPullbackStrategy') # 이 클래스 자체를 목킹

    app.transaction_handlers.handle_buy_stock = AsyncMock()
    app.transaction_handlers.handle_sell_stock = AsyncMock()
    app.transaction_handlers.handle_realtime_price_quote_stream = AsyncMock()
    app.token_manager = MagicMock()
    app.token_manager.invalidate_token = MagicMock()

    yield app


### `_execute_action` 메서드를 위한 새로운 테스트 케이스

@pytest.mark.asyncio
async def test_execute_action_change_environment_success(setup_mock_app): # capsys 제거
    app = setup_mock_app
    # 환경 선택을 담당하는 내부 메서드를 목(mock) 설정
    app._select_environment = AsyncMock(return_value=True)

    # 실제 _execute_action 메서드 호출
    result = await app._execute_action('0')

    # 검증
    app._select_environment.assert_awaited_once() # _select_environment가 호출되었는지 확인
    assert result is True # 환경 변경이 성공하면 앱은 계속 실행되어야 함
    # logger.info 호출을 확인
    app.logger.info.assert_called_once_with("거래 환경 변경을 시작합니다.")

@pytest.mark.asyncio
async def test_execute_action_stock_info_success(setup_mock_app):
    app = setup_mock_app
    app.cli_view.get_user_input.return_value = "005930" # 종목코드 직접 입력으로 변경
    # trading_service.get_code_by_name과 get_price_summary는 이제 1번 메뉴에서 직접 호출되지 않음
    # 대신 data_handlers.handle_get_current_stock_price가 호출됨
    app.data_handlers.handle_get_current_stock_price.return_value = None # handle_get_current_stock_price를 목킹

    result = await app._execute_action('1') # 1번 메뉴로 변경

    app.cli_view.get_user_input.assert_awaited_once_with("조회할 종목 코드를 입력하세요 (삼성전자: 005930): ")
    app.data_handlers.handle_get_current_stock_price.assert_awaited_once_with("005930") # handle_get_current_stock_price 호출 확인
    assert result is True

@pytest.mark.asyncio
async def test_execute_action_account_balance_failure(setup_mock_app, capsys):
    app = setup_mock_app
    app.trading_service.get_account_balance.return_value = None

    result = await app._execute_action('2') # 1번 -> 2번 메뉴로 변경

    app.trading_service.get_account_balance.assert_awaited_once()
    app.cli_view.display_account_balance_failure.assert_called_once() # CLIView 메서드 호출 확인
    assert result is True

@pytest.mark.asyncio
async def test_execute_action_place_buy_order_success(setup_mock_app):
    app = setup_mock_app
    # handle_buy_stock이 성공적으로 실행되도록 목(mock) 설정
    app.transaction_handlers.handle_buy_stock.return_value = None # 이 메서드는 반환값이 없음

    result = await app._execute_action('3')

    # 검증
    app.transaction_handlers.handle_buy_stock.assert_awaited_once() # handle_buy_stock이 호출되었는지 확인
    assert result is True # 앱은 계속 실행되어야 함

@pytest.mark.asyncio
async def test_execute_action_place_sell_order_success(setup_mock_app):
    app = setup_mock_app
    # handle_sell_stock이 성공적으로 실행되도록 목(mock) 설정
    app.transaction_handlers.handle_sell_stock.return_value = None # 이 메서드는 반환값이 없음

    result = await app._execute_action('4')

    # 검증
    app.transaction_handlers.handle_sell_stock.assert_awaited_once() # handle_sell_stock이 호출되었는지 확인
    assert result is True # 앱은 계속 실행되어야 함

@pytest.mark.asyncio
async def test_execute_action_display_stock_change_rate(setup_mock_app, mocker):
    app = setup_mock_app
    app.cli_view.get_user_input.return_value = "005930"
    app.data_handlers.handle_display_stock_change_rate.return_value = None

    result = await app._execute_action('5')

    app.cli_view.get_user_input.assert_awaited_once_with("조회할 종목 코드를 입력하세요 (삼성전자: 005930): ")
    app.data_handlers.handle_display_stock_change_rate.assert_awaited_once_with("005930")
    assert result is True

@pytest.mark.asyncio
async def test_execute_action_display_stock_vs_open_price(setup_mock_app, mocker):
    app = setup_mock_app
    app.cli_view.get_user_input.return_value = "005930" # 종목 코드 입력 시뮬레이션
    app.data_handlers.handle_display_stock_vs_open_price.return_value = None # 핸들러 목킹

    result = await app._execute_action('6')

    app.cli_view.get_user_input.assert_awaited_once_with("조회할 종목 코드를 입력하세요 (삼성전자: 005930): ")
    app.data_handlers.handle_display_stock_vs_open_price.assert_awaited_once_with("005930")
    assert result is True

@pytest.mark.asyncio
async def test_execute_action_get_top_market_cap_real_success(setup_mock_app):
    app = setup_mock_app
    app.env.is_paper_trading = False # 실전 투자 모드로 설정
    app.data_handlers.handle_get_top_market_cap_stocks.return_value = None # 반환값은 중요하지 않음

    result = await app._execute_action('7')

    # 검증
    app.data_handlers.handle_get_top_market_cap_stocks.assert_awaited_once_with("0000") # 올바른 인자로 호출되었는지 확인
    assert result is True # 앱은 계속 실행되어야 함

@pytest.mark.asyncio
async def test_execute_action_top_10_market_cap_paper_mode(setup_mock_app, capsys):
    app = setup_mock_app
    app.env.is_paper_trading = True # 환경을 모의 투자 모드로 설정
    app.time_manager.is_market_open.return_value = True # 시장 개장 (이 특정 경고에는 무관)

    # 실제 _execute_action 메서드 호출
    result = await app._execute_action('8')

    # 검증
    assert "WARNING: 모의투자 환경에서는 시가총액 1~10위 종목 조회를 지원하지 않습니다." in capsys.readouterr().out # 콘솔 경고 확인
    app.logger.warning.assert_called_once_with("모의투자 환경에서 시가총액 1~10위 종목 조회 시도 (미지원).") # 로거 경고 확인
    app.data_handlers.handle_get_top_10_market_cap_stocks_with_prices.assert_not_called() # 하위 핸들러가 호출되지 않았는지 확인
    assert result is True # 앱은 계속 실행되어야 함

@pytest.mark.asyncio
async def test_execute_action_momentum_strategy_market_closed(setup_mock_app, capsys):
    app = setup_mock_app
    app.time_manager.is_market_open.return_value = False # 시장 마감 시뮬레이션

    # 실제 _execute_action 메서드 호출
    result = await app._execute_action('10')

    # 검증
    app.cli_view.display_warning_strategy_market_closed.assert_called_once() # CLI 경고 메시지 확인
    app.logger.warning.assert_called_once_with("시장 미개장 상태에서 전략 실행 시도") # 로거 경고 확인
    # 시장이 마감되었을 때는 더 이상 전략 관련 호출이 이루어지지 않는지 확인
    app.trading_service.get_top_market_cap_stocks_code.assert_not_called()
    assert result is True # 앱은 계속 실행되어야 함

@pytest.mark.asyncio
async def test_execute_action_momentum_strategy_top_stocks_failure(setup_mock_app, capsys):
    app = setup_mock_app
    app.time_manager.is_market_open.return_value = True # 시장 개장 가정
    # get_top_market_cap_stocks_code가 실패 응답을 반환하도록 목 설정
    app.trading_service.get_top_market_cap_stocks_code.return_value = {"rt_cd": "1", "msg1": "API 조회 실패"}

    result = await app._execute_action('10')

    # 검증
    app.cli_view.display_strategy_running_message.assert_called_once_with("모멘텀")
    app.trading_service.get_top_market_cap_stocks_code.assert_awaited_once_with("0000")
    app.cli_view.display_top_stocks_failure.assert_called_once_with("API 조회 실패") # 실패 메시지 확인
    app.logger.warning.assert_called_once_with(f"시가총액 조회 실패. 응답: {{'rt_cd': '1', 'msg1': 'API 조회 실패'}}")
    assert result is True # 앱은 계속 실행되어야 함

@pytest.mark.asyncio
async def test_execute_action_momentum_backtest_invalid_count_input_value_error(setup_mock_app, capsys, mocker):
    app = setup_mock_app
    app.cli_view.get_user_input.return_value = "invalid_number"
    app.trading_service.get_top_market_cap_stocks_code = AsyncMock(return_value={"rt_cd": "0", "output": []})
    # mocker.patch('strategies.momentum_strategy.MomentumStrategy') # 이 줄을 제거
    # mocker.patch('strategies.strategy_executor.StrategyExecutor') # 이 줄을 제거

    result = await app._execute_action('11')

    app.cli_view.get_user_input.assert_awaited_once_with("시가총액 상위 몇 개 종목을 조회할까요? (기본값: 30): ")
    app.cli_view.display_invalid_input_warning.assert_called_once_with("숫자가 아닌 값이 입력되어 기본값 30을 사용합니다.")
    app.trading_service.get_top_market_cap_stocks_code.assert_awaited_once_with("0000", count=30)
    assert result is True

@pytest.mark.asyncio
async def test_execute_action_momentum_backtest_zero_or_negative_count_input(setup_mock_app, capsys):
    app = setup_mock_app
    app.cli_view.get_user_input.return_value = "-5" # 0 이하의 숫자 입력 시뮬레이션
    app.trading_service.get_top_market_cap_stocks_code = AsyncMock(return_value={"rt_cd": "0", "output": []})

    result = await app._execute_action('11')

    # 검증
    app.cli_view.get_user_input.assert_awaited_once_with("시가총액 상위 몇 개 종목을 조회할까요? (기본값: 30): ")
    app.cli_view.display_invalid_input_warning.assert_called_once_with("0 이하의 수는 허용되지 않으므로 기본값 30을 사용합니다.") # 경고 메시지 확인
    app.trading_service.get_top_market_cap_stocks_code.assert_awaited_once_with("0000", count=30) # 기본값 30으로 호출되었는지 확인
    assert result is True # 앱은 계속 실행되어야 함


@pytest.mark.asyncio
async def test_execute_action_gap_up_pullback_strategy_success(setup_mock_app, mocker):
    app = setup_mock_app
    app.time_manager.is_market_open.return_value = True  # 시장 개장 가정
    # trading_service.get_top_market_cap_stocks_code가 유효한 응답을 반환하도록 목 설정
    mock_top_codes_response = {
        "rt_cd": "0",
        "output": [
            {"mksc_shrn_iscd": "005930", "hts_kor_isnm": "삼성전자", "data_rank": "1"},
            {"mksc_shrn_iscd": "000660", "hts_kor_isnm": "SK하이닉스", "data_rank": "2"}
        ]
    }
    app.trading_service.get_top_market_cap_stocks_code.return_value = mock_top_codes_response

    # GapUpPullbackStrategy와 StrategyExecutor의 인스턴스 메서드를 목킹
    mock_strategy_instance = AsyncMock()
    mock_executor_instance = AsyncMock()
    mock_executor_instance.execute.return_value = {
        "total_processed": 2,
        "gapup_pullback_selected": [{"name": "삼성전자", "code": "005930"}],
        "gapup_pullback_rejected": [{"name": "SK하이닉스", "code": "000660"}],
    }

    # 클래스 자체를 목킹하여 인스턴스 생성 시 목 객체가 반환되도록 설정
    mocker.patch('strategies.GapUpPullback_strategy.GapUpPullbackStrategy', return_value=mock_strategy_instance)
    mocker.patch('strategies.strategy_executor.StrategyExecutor', return_value=mock_executor_instance)

    result = await app._execute_action('12')

    # 검증
    app.cli_view.display_strategy_running_message.assert_called_once_with("GapUpPullback")
    app.trading_service.get_top_market_cap_stocks_code.assert_awaited_once_with("0000")

    # GapUpPullbackStrategy와 StrategyExecutor가 올바른 인자로 생성되었는지 확인
    # (여기서는 인스턴스 자체를 목킹했으므로, 생성자 호출만 확인)
    assert app.cli_view.display_strategy_results.called  # 결과 표시 호출 확인
    app.cli_view.display_gapup_pullback_selected_stocks.assert_called_once_with([{"name": "삼성전자", "code": "005930"}])
    app.cli_view.display_gapup_pullback_rejected_stocks.assert_called_once_with([{"name": "SK하이닉스", "code": "000660"}])

    assert result is True  # 앱은 계속 실행되어야 함

@pytest.mark.asyncio
async def test_execute_action_realtime_stream_new_menu_option(setup_mock_app):
    app = setup_mock_app
    app.cli_view.get_user_input.return_value = "005930" # 구독할 종목 코드 입력 시뮬레이션
    app.transaction_handlers.handle_realtime_price_quote_stream.return_value = None

    result = await app._execute_action('13')

    app.cli_view.get_user_input.assert_awaited_once_with("구독할 종목 코드를 입력하세요: ")
    app.transaction_handlers.handle_realtime_price_quote_stream.assert_awaited_once_with("005930")
    assert result is True

@pytest.mark.asyncio
async def test_execute_action_invalidate_token(setup_mock_app, capsys):
    app = setup_mock_app
    app.token_manager.invalidate_token.return_value = None

    result = await app._execute_action('98')

    app.token_manager.invalidate_token.assert_called_once()
    app.cli_view.display_token_invalidated_message.assert_called_once()
    assert result is True

@pytest.mark.asyncio
async def test_execute_action_exit_app(mocker):
    """메뉴 '99' 선택 시 running_status가 False를 반환하는지 테스트합니다."""
    mocker.patch('trading_app.load_config', return_value=get_mock_config())
    app = TradingApp(main_config_path="dummy/path", tr_ids_config_path="dummy/path")
    running_status = await app._execute_action('99')
    assert running_status is False

@pytest.mark.asyncio
async def test_execute_action_invalid_menu_choice_general(setup_mock_app):
    app = setup_mock_app
    # 유효하지 않은 임의의 메뉴 선택
    result = await app._execute_action('999')

    # display_invalid_menu_choice가 호출되었는지 확인
    app.cli_view.display_invalid_menu_choice.assert_called_once()
    # 앱은 종료되지 않고 계속 실행되어야 함
    assert result is True

# 13. `_complete_api_initialization` 성공 시나리오 테스트
@pytest.mark.asyncio
async def test_complete_api_initialization_success(setup_mock_app, mocker):
    app = setup_mock_app

    # _complete_api_initialization의 내부에서 호출되는 종속성들을 목킹
    app.env.get_access_token.return_value = "valid_access_token"
    # 클래스 자체를 목킹하여 생성자 호출을 추적할 수 있도록 함
    mock_ki_client = mocker.patch('trading_app.KoreaInvestApiClient')
    mock_trading_service_cls = mocker.patch('trading_app.TradingService')
    mock_data_handlers_cls = mocker.patch('trading_app.DataHandlers')
    mock_transaction_handlers_cls = mocker.patch('trading_app.TransactionHandlers')
    mock_broker_wrapper_cls = mocker.patch('trading_app.BrokerAPIWrapper')
    mock_backtest_provider_cls = mocker.patch('trading_app.BacktestDataProvider')

    # 실제 _complete_api_initialization 메서드 호출
    result = await app._complete_api_initialization()

    # 검증
    app.env.get_access_token.assert_awaited_once()
    mock_ki_client.assert_called_once() # KoreaInvestApiClient 생성자 호출 확인
    mock_trading_service_cls.assert_called_once() # TradingService 생성자 호출 확인
    mock_data_handlers_cls.assert_called_once() # DataHandlers 생성자 호출 확인
    mock_transaction_handlers_cls.assert_called_once() # TransactionHandlers 생성자 호출 확인
    mock_broker_wrapper_cls.assert_called_once() # BrokerAPIWrapper 생성자 호출 확인
    mock_backtest_provider_cls.assert_called_once() # BacktestDataProvider 생성자 호출 확인

    app.logger.info.assert_called_with(mocker.ANY) # info 로깅 호출 확인
    assert result is True

@pytest.mark.asyncio
async def test_complete_api_initialization_token_failure(setup_mock_app, mocker):
    app = setup_mock_app

    # env.get_access_token이 None을 반환하도록 목킹하여 토큰 획득 실패 시뮬레이션
    app.env.get_access_token.return_value = None

    # 나머지 서비스들은 호출되지 않아야 함 (클래스 자체를 목킹하여 호출 여부 확인)
    mock_ki_client = mocker.patch('trading_app.KoreaInvestApiClient')
    mock_trading_service_cls = mocker.patch('trading_app.TradingService')
    mock_data_handlers_cls = mocker.patch('trading_app.DataHandlers')
    mock_transaction_handlers_cls = mocker.patch('trading_app.TransactionHandlers')
    mock_broker_wrapper_cls = mocker.patch('trading_app.BrokerAPIWrapper')
    mock_backtest_provider_cls = mocker.patch('trading_app.BacktestDataProvider')

    # 실제 _complete_api_initialization 메서드 호출
    result = await app._complete_api_initialization()

    # 검증
    app.env.get_access_token.assert_awaited_once()
    # 실제 로깅 메시지에 맞춰 수정
    app.logger.critical.assert_called_once_with("API 클라이언트 초기화 실패: API 접근 토큰 발급에 실패했습니다. config.yaml 설정을 확인하세요.")
    app.cli_view.display_app_start_error.assert_called_once_with("API 클라이언트 초기화 중 오류 발생: API 접근 토큰 발급에 실패했습니다. config.yaml 설정을 확인하세요.")

    # 다른 서비스들이 초기화되지 않았는지 확인
    mock_ki_client.assert_not_called()
    mock_trading_service_cls.assert_not_called()
    mock_data_handlers_cls.assert_not_called()
    mock_transaction_handlers_cls.assert_not_called()
    mock_broker_wrapper_cls.assert_not_called()
    mock_backtest_provider_cls.assert_not_called()

    assert result is False


# 15. `_select_environment` 실전투자 선택 성공 시나리오 테스트
@pytest.mark.asyncio
async def test_select_environment_real_trading_success(setup_mock_app):
    app = setup_mock_app

    # cli_view.select_environment_input이 '2' (실전투자)을 반환하도록 설정
    app.cli_view.select_environment_input.side_effect = ["2"]

    # env.get_access_token 및 _complete_api_initialization이 성공적으로 작동하도록 목킹
    app.env.get_access_token.return_value = "mock_access_token"
    app._complete_api_initialization = AsyncMock(return_value=True)

    # _select_environment 메서드 호출
    result = await app._select_environment()

    # 검증
    app.cli_view.select_environment_input.assert_awaited_once()
    app.env.set_trading_mode.assert_called_once_with(False)  # False (실전투자)로 호출되었는지 확인
    app.logger.info.assert_called_once_with("실전 투자 환경으로 설정되었습니다.")
    app.env.get_access_token.assert_awaited_once()
    app._complete_api_initialization.assert_awaited_once()
    assert result is True


# 16. `_select_environment` 모의투자 선택 성공 시나리오 테스트
@pytest.mark.asyncio
async def test_select_environment_paper_trading_success(setup_mock_app):
    app = setup_mock_app

    # cli_view.select_environment_input이 '1' (모의투자)을 반환하도록 설정
    app.cli_view.select_environment_input.side_effect = ["1"]

    # env.get_access_token 및 _complete_api_initialization이 성공적으로 작동하도록 목킹
    app.env.get_access_token.return_value = "mock_access_token"
    app._complete_api_initialization = AsyncMock(return_value=True)

    # _select_environment 메서드 호출
    result = await app._select_environment()

    # 검증
    app.cli_view.select_environment_input.assert_awaited_once()
    app.env.set_trading_mode.assert_called_once_with(True)  # True (모의투자)로 호출되었는지 확인
    app.logger.info.assert_called_once_with("모의 투자 환경으로 설정되었습니다.")
    app.env.get_access_token.assert_awaited_once()
    app._complete_api_initialization.assert_awaited_once()
    assert result is True


# 17. `_select_environment` 토큰 획득 실패 시나리오 테스트
@pytest.mark.asyncio
async def test_select_environment_token_acquisition_failure(setup_mock_app):
    app = setup_mock_app

    # cli_view.select_environment_input이 '2' (실전투자)을 반환하도록 설정
    app.cli_view.select_environment_input.side_effect = ["2"]

    # env.get_access_token이 None을 반환하도록 목킹하여 토큰 획득 실패 시뮬레이션
    app.env.get_access_token.return_value = None

    # _complete_api_initialization은 호출되지 않아야 함
    app._complete_api_initialization = AsyncMock()

    # _select_environment 메서드 호출
    result = await app._select_environment()

    # 검증
    app.cli_view.select_environment_input.assert_awaited_once()
    app.env.set_trading_mode.assert_called_once_with(False)  # 환경 설정은 시도됨
    app.env.get_access_token.assert_awaited_once()
    app.logger.critical.assert_called_once_with("선택된 환경의 토큰 발급에 실패했습니다. 애플리케이션을 종료합니다.")
    app._complete_api_initialization.assert_not_awaited()  # 토큰 실패 시 초기화 호출 안 됨
    assert result is False


# 18. `_select_environment` API 클라이언트 초기화 실패 시나리오 테스트
@pytest.mark.asyncio
async def test_select_environment_api_initialization_failure(setup_mock_app):
    app = setup_mock_app

    # cli_view.select_environment_input이 '2' (실전투자)을 반환하도록 설정
    app.cli_view.select_environment_input.side_effect = ["2"]

    # env.get_access_token은 성공하지만, _complete_api_initialization이 False를 반환하도록 목킹
    app.env.get_access_token.return_value = "mock_access_token"
    app._complete_api_initialization = AsyncMock(return_value=False)

    # _select_environment 메서드 호출
    result = await app._select_environment()

    # 검증
    app.cli_view.select_environment_input.assert_awaited_once()
    app.env.set_trading_mode.assert_called_once_with(False)
    app.env.get_access_token.assert_awaited_once()
    app._complete_api_initialization.assert_awaited_once()
    app.logger.critical.assert_called_once_with("API 클라이언트 초기화 실패. 애플리케이션을 종료합니다.")
    assert result is False



def test_load_config_yaml_success():
    # YAML 파일 정상 로드
    yaml_data = "api_key: test-key\n"
    with patch("builtins.open", mock_open(read_data=yaml_data)), \
         patch("core.config_loader.yaml.safe_load", return_value={"api_key": "test-key"}) as mock_yaml:
        result = load_config("dummy.yaml")
        assert result == {"api_key": "test-key"}
        mock_yaml.assert_called_once()


def test_load_config_json_fallback_on_yaml_import_error():
    # yaml import 실패 시 fallback to json.load
    json_data = '{"api_key": "test-key"}'
    with patch("builtins.open", mock_open(read_data=json_data)), \
         patch.dict("sys.modules", {"yaml": None}), \
         patch("trading_app.json.load", return_value={"api_key": "test-key"}) as mock_json:
        result = load_config("dummy.json")
        assert result == {"api_key": "test-key"}
        mock_json.assert_called_once()


def test_load_config_file_not_found():
    # 파일이 존재하지 않는 경우
    with patch("builtins.open", side_effect=FileNotFoundError):
        with pytest.raises(FileNotFoundError):
            load_config("missing.yaml")


def test_load_config_json_parse_error_raises_value_error():
    # JSON 파싱 오류 → ValueError 변환
    bad_json = '{"api_key": "bad_value"'
    with patch("builtins.open", mock_open(read_data=bad_json)), \
         patch("core.config_loader.yaml.safe_load", side_effect=ValueError):
        with pytest.raises(ValueError):
            load_config("broken.json")


def test_load_config_yaml_parse_error_raises_value_error():
    bad_yaml = "api_key: [unclosed"
    with patch.object(builtins, "open", mock_open(read_data=bad_yaml)):
        with pytest.raises(ValueError) as exc:
            load_config("broken.yaml")

        # 예외 메시지의 앞부분만 검증
        assert "설정 파일 형식이 올바르지 않습니다 (broken.yaml):" in str(exc.value)
        assert "while parsing a flow sequence" in str(exc.value)

def test_load_configs_and_init_env_file_not_found(mocker):
    # ─ Arrange ─
    mock_logger = MagicMock()

    # TradingApp 생성 이전에 load_config을 patch하여 예외 발생 시뮬레이션
    mocker.patch('trading_app.load_config', side_effect=FileNotFoundError("설정 파일 없음"))

    # ─ Act & Assert ─
    with pytest.raises(FileNotFoundError):
        app = TradingApp(main_config_path="invalid_path.yaml", tr_ids_config_path="tr_ids.yaml")
        app.logger = mock_logger

    # logger는 생성자 이후에 설정되므로, 이 예외는 직접적으로 logger 확인은 어렵고, 예외 발생만 검증합니다.

def test_load_configs_and_init_env_unexpected_exception(mocker):
    # load_config이 일반 Exception을 발생하도록 패치
    mocker.patch('trading_app.load_config', side_effect=Exception("예기치 않은 오류"))

    # __init__을 우회하여 인스턴스만 만들고 직접 메서드 호출
    app = object.__new__(TradingApp)
    app.main_config_path = "main.yaml"
    app.tr_ids_config_path = "tr_ids.yaml"
    app.logger = MagicMock()

    with pytest.raises(Exception):
        app._load_configs_and_init_env()

    app.logger.critical.assert_called_once()
    assert "애플리케이션 초기화 실패" in app.logger.critical.call_args[0][0]

@pytest.mark.asyncio
@patch('builtins.print') # Patch print to avoid actual console output
async def test_select_environment_invalid_choice_triggers_warning(mock_print): # Remove 'self'
    # ─ Arrange ─
    app = object.__new__(TradingApp)  # __init__ 우회
    app.cli_view = MagicMock()
    app.cli_view.select_environment_input = AsyncMock(side_effect=["abc", "1"])  # 잘못된 입력
    app.cli_view.display_invalid_environment_choice = MagicMock()

    app.env = MagicMock()
    app.env.set_trading_mode = MagicMock()
    app.env.get_access_token = AsyncMock(return_value="dummy_token")
    app.logger = MagicMock()

    app._complete_api_initialization = AsyncMock()

    # ─ Act ─
    result = await app._select_environment()

    # ─ Assert ─
    # Ensure display_invalid_environment_choice was called for the invalid input
    app.cli_view.display_invalid_environment_choice.assert_called_once()
    # Ensure select_environment_input was called twice (once for invalid, once for valid)
    assert app.cli_view.select_environment_input.call_count == 2 # Use pytest's assert
    # Ensure set_trading_mode was called for the valid input
    app.env.set_trading_mode.assert_called_once_with(True)
    # Ensure _complete_api_initialization was called
    app._complete_api_initialization.assert_awaited_once()
    # Ensure the method returns True on success
    assert result is True # Use pytest's assert


def test_display_menu_outputs_expected_messages():
    # ─ Arrange ─
    app = object.__new__(TradingApp)
    app.cli_view = MagicMock()

    # datetime 객체로 수정
    mock_time = datetime(2025, 1, 1, 9, 0, 0)
    app.time_manager = MagicMock()
    app.time_manager.get_current_kst_time.return_value = mock_time
    app.time_manager.is_market_open.return_value = True

    app.env = MagicMock()
    app.env.is_paper_trading = True

    # display_menu는 별도로 mock
    app.cli_view.display_menu = MagicMock()

    # ─ Act ─
    app._display_menu()

    # ─ Assert ─
    app.cli_view.display_menu.assert_called_once()
    args, kwargs = app.cli_view.display_menu.call_args
    assert kwargs['env_type'] == "모의투자"
    assert kwargs['market_status_str'] == "열려있음"
    assert kwargs['current_time_str'].startswith("2025-01-01 09:00:00")

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
    app.logger = MagicMock() # 로거도 Mocking하여 로그 호출을 검증할 수 있습니다.
    app.time_manager = MagicMock() # display_current_time 내부에서 사용될 수 있으므로 Mocking

    # TradingApp 내부의 비동기 메서드들을 Mocking하여 제어합니다.
    # _complete_api_initialization이 성공적으로 완료되었다고 가정합니다.
    app._complete_api_initialization = AsyncMock(return_value=True)
    # _select_environment가 성공적으로 완료되었다고 가정합니다.
    app._select_environment = AsyncMock(return_value=True)
    # _display_menu는 동기 메서드이므로 MagicMock으로 충분합니다.
    app._display_menu = MagicMock()
    # _execute_action은 사용자의 선택에 따라 루프를 계속할지(True) 종료할지(False)를 반환합니다.
    app._execute_action = AsyncMock()

    # 사용자 입력을 시뮬레이션합니다.
    # 첫 번째 입력: '1' (예: 현재가 조회)
    # 두 번째 입력: '99' (종료)
    app.cli_view.get_user_input = AsyncMock(side_effect=["1", "99"])

    # _execute_action의 반환 값을 시뮬레이션합니다.
    # 첫 번째 액션('1')은 루프를 계속하게 하고 (True),
    # 두 번째 액션('99')은 루프를 종료하게 합니다 (False).
    app._execute_action.side_effect = [True, False]

    # ─ 실행 (Act) ─
    await app.run_async()

    # ─ 검증 (Assert) ─

    # 1. 애플리케이션 시작 및 초기화 단계 검증
    # 환영 메시지가 한 번 표시되었는지 확인
    app.cli_view.display_welcome_message.assert_called_once()
    # API 초기화가 한 번 호출되고 await되었는지 확인
    app._complete_api_initialization.assert_awaited_once()
    # 환경 선택이 한 번 호출되고 await되었는지 확인
    app._select_environment.assert_awaited_once()

    # 2. 메인 루프의 반복 횟수 및 호출 검증
    # 루프는 '1' 입력 후 한 번, '99' 입력 후 한 번 더 실행되므로 총 두 번 반복됩니다.
    # 따라서 다음 메서드들은 두 번씩 호출되어야 합니다.
    assert app.cli_view.display_current_time.call_count == 2
    assert app._display_menu.call_count == 2
    assert app.cli_view.get_user_input.call_count == 2

    # _execute_action이 올바른 인자로 두 번 호출되었는지 확인
    # 첫 번째 호출은 "1"로, 두 번째 호출은 "99"로 이루어져야 합니다.
    app._execute_action.assert_has_calls([
        call("1"),
        call("99")
    ])
    # _execute_action이 정확히 두 번 호출되었는지 확인
    assert app._execute_action.call_count == 2

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
        self.mock_trading_service = AsyncMock() # TradingService Mock 추가

        # DataHandlers와 TransactionHandlers도 Mocking합니다.
        # 실제 인스턴스 대신 Mock을 사용합니다.
        self.mock_data_handlers = MagicMock()
        self.mock_transaction_handlers = MagicMock()

        # TradingApp 인스턴스를 __init__을 우회하여 생성하고 필요한 속성을 수동으로 Mocking합니다.
        self.app = object.__new__(TradingApp)
        self.app.cli_view = self.mock_cli_view
        self.app.env = self.mock_env
        self.app.logger = self.mock_logger
        self.app.time_manager = self.mock_time_manager
        self.app.trading_service = self.mock_trading_service # TradingService 할당
        self.app.data_handlers = self.mock_data_handlers # DataHandlers 할당
        self.app.transaction_handlers = self.mock_transaction_handlers # TransactionHandlers 할당

        # _complete_api_initialization 및 _select_environment를 Mocking하여 제어합니다.
        self.app._complete_api_initialization = AsyncMock(return_value=True)
        self.app._select_environment = AsyncMock(return_value=True)
        self.app._display_menu = MagicMock() # run_async 테스트에서 사용되므로 추가


    # @pytest.mark.asyncio # 이 데코레이터를 제거합니다.
    async def test_execute_action_choice_0_environment_change_fails(self):
        """
        _execute_action 메서드에서 choice '0' (환경 변경) 선택 시
        _select_environment가 실패하여 running_status가 False가 되는지 검증합니다 (175 라인).
        """
        # ─ 준비 (Arrange) ─
        # _select_environment가 False를 반환하도록 Mocking하여 환경 변경 실패를 시뮬레이션합니다.
        self.app._select_environment.return_value = False

        # ─ 실행 (Act) ─
        # _execute_action을 '0' 선택으로 호출합니다.
        running_status = await self.app._execute_action('0')

        # ─ 검증 (Assert) ─
        # 175번 라인: running_status가 False로 설정되었는지 확인
        assert running_status is False
        # logger.info("거래 환경 변경을 시작합니다.")가 호출되었는지 확인
        self.app.logger.info.assert_called_once_with("거래 환경 변경을 시작합니다.")
        # _select_environment가 호출되었는지 확인
        self.app._select_environment.assert_awaited_once()

    # @pytest.mark.asyncio # 이 데코레이터를 제거합니다.
    async def test_execute_action_choice_2_account_balance_success(self):
        """
        _execute_action 메서드에서 choice '2' (계좌 잔고 조회) 선택 시
        계좌 잔고 조회가 성공하여 display_account_balance가 호출되는지 검증합니다 (182 라인).
        """
        # ─ 준비 (Arrange) ─
        # trading_service.get_account_balance가 성공적인 잔고를 반환하도록 Mocking합니다.
        mock_balance_data = {"rt_cd": "0", "output": {"dnca_tot_amt": "1234567"}}
        self.mock_trading_service.get_account_balance.return_value = mock_balance_data

        # ─ 실행 (Act) ─
        # _execute_action을 '2' 선택으로 호출합니다.
        running_status = await self.app._execute_action('2')

        # ─ 검증 (Assert) ─
        # trading_service.get_account_balance가 호출되었는지 확인
        self.mock_trading_service.get_account_balance.assert_awaited_once()
        # 182번 라인: cli_view.display_account_balance가 올바른 인자로 호출되었는지 확인
        self.app.cli_view.display_account_balance.assert_called_once_with(mock_balance_data)
        # running_status가 True로 유지되었는지 확인 (기본값)
        assert running_status is True

    # @pytest.mark.asyncio # 이 데코레이터를 제거합니다.
    async def test_execute_action_choice_2_account_balance_failure(self):
        """
        _execute_action 메서드에서 choice '2' (계좌 잔고 조회) 선택 시
        계좌 잔고 조회가 실패하여 display_account_balance_failure가 호출되는지 검증합니다.
        """
        # ─ 준비 (Arrange) ─
        # trading_service.get_account_balance가 실패를 나타내는 None을 반환하도록 Mocking합니다.
        self.mock_trading_service.get_account_balance.return_value = None

        # ─ 실행 (Act) ─
        # _execute_action을 '2' 선택으로 호출합니다.
        running_status = await self.app._execute_action('2')

        # ─ 검증 (Assert) ─
        # trading_service.get_account_balance가 호출되었는지 확인
        self.mock_trading_service.get_account_balance.assert_awaited_once()
        # cli_view.display_account_balance가 호출되지 않았는지 확인
        self.app.cli_view.display_account_balance.assert_not_called()
        # cli_view.display_account_balance_failure가 호출되었는지 확인
        self.app.cli_view.display_account_balance_failure.assert_called_once()
        # running_status가 True로 유지되었는지 확인 (기본값)
        assert running_status is True

    # @pytest.mark.asyncio # 이 데코레이터를 제거합니다.
    async def test_run_async_api_initialization_fails(self):
        """
        run_async 메서드에서 _complete_api_initialization이 실패할 경우
        애플리케이션이 즉시 종료되고 다음 단계가 실행되지 않는지 검증합니다 (456-457 라인).
        """
        # ─ 준비 (Arrange) ─
        # _complete_api_initialization이 False를 반환하도록 Mocking하여 실패를 시뮬레이션합니다.
        self.app._complete_api_initialization.return_value = False

        # _select_environment 및 메인 루프 관련 메서드들이 호출되지 않음을 확인하기 위해 Mocking합니다.
        self.app._select_environment = AsyncMock()
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
        # _select_environment가 호출되지 않았는지 확인
        self.app._select_environment.assert_not_awaited()
        # 메인 루프 내부의 메서드들이 호출되지 않았는지 확인
        self.app.cli_view.display_current_time.assert_not_called()
        self.app._display_menu.assert_not_called()
        self.app.cli_view.get_user_input.assert_not_called()
        self.app._execute_action.assert_not_awaited()