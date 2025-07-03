import pytest
import logging
import inspect
from unittest.mock import MagicMock, AsyncMock
from trading_app import TradingApp
from services.trading_service import TradingService
from core.time_manager import TimeManager
from user_api.broker_api_wrapper import BrokerAPIWrapper
from strategies.momentum_strategy import MomentumStrategy
from strategies.strategy_executor import StrategyExecutor


@pytest.mark.asyncio
async def test_execute_action_momentum_strategy_success(mocker, capsys):
    """
    TradingApp._execute_action('10') - 모멘텀 전략 실행이 성공하는 시나리오를 테스트합니다.
    """
    # --- Arrange (준비) ---

    # 1. 의존성 모킹 (Patching)
    mock_config = {
        'token_file_path': 'dummy_token.json',
        'market_open_time': '09:00',
        'market_close_time': '15:30',
        'market_timezone': 'Asia/Seoul',  # TimeManager 초기화에 필요

        # KoreaInvestApiEnv 초기화에 필요한 값들
        'is_paper_trading': False,  # 실전/모의 환경을 명시
        'url': 'https://mock-real-url',  # is_paper_trading=False 일 때 사용
        'websocket_url': 'wss://mock-real-ws-url',
        'paper_url': 'https://mock-paper-url',  # is_paper_trading=True 일 때 사용
        'paper_websocket_url': 'wss://mock-paper-ws-url',

        # tr_ids_data 로드 흉내
        'tr_ids': {}
    }
    mocker.patch('trading_app.load_config', return_value=mock_config)

    # Patch 대상을 '사용되는 위치'가 아닌 '정의된 원본 위치'로 변경합니다.
    mock_strategy_class = mocker.patch('services.momentum_strategy.MomentumStrategy')
    mock_executor_class = mocker.patch('services.strategy_executor.StrategyExecutor')

    # StrategyExecutor 인스턴스의 execute 메서드를 AsyncMock으로 설정
    mock_executor_instance = mock_executor_class.return_value
    mock_executor_instance.execute = AsyncMock(return_value={
        "follow_through": [{'code': '005930', 'name': '삼성전자'}],
        "not_follow_through": [{'code': '000660', 'name': 'SK하이닉스'}]
    })

    # (이하 테스트 코드의 나머지 부분은 동일합니다)
    app = TradingApp(main_config_path="dummy/path", tr_ids_config_path="dummy/path")

    app.time_manager = MagicMock(spec=TimeManager)
    app.time_manager.is_market_open.return_value = True

    app.trading_service = MagicMock(spec=TradingService)
    mock_top_stocks_response = {
        "rt_cd": "0",  # <<-- 성공 코드를 추가합니다.
        "output": [
            {"mksc_shrn_iscd": "005930"},
            {"mksc_shrn_iscd": "000660"}
        ]
    }
    app.trading_service.get_top_market_cap_stocks_code = AsyncMock(return_value=mock_top_stocks_response)

    app.broker = MagicMock(spec=BrokerAPIWrapper)
    app.logger = logging.getLogger('test_trading_app')

    await app._execute_action('10')

    captured = capsys.readouterr()
    assert "모멘텀 전략 결과" in captured.out
    assert str({'code': '005930', 'name': '삼성전자'}) in captured.out

# Helper function to create a mock config for tests
def get_mock_config():
    """테스트에 필요한 최소한의 모의 설정(config) 객체를 반환합니다."""
    return {
        'token_file_path': 'dummy.json',
        'is_paper_trading': False,
        'url': 'https://dummy-url.com',
        'paper_url': 'https://dummy-paper-url.com',
        'websocket_url': 'wss://dummy-ws.com',
        'paper_websocket_url': 'wss://dummy-paper-ws.com',
    }

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

    module_path = inspect.getmodule(MomentumStrategy).__name__
    mocker.patch(f"{module_path}.MomentumStrategy")
    # mocker.patch('services.momentum_strategy.MomentumStrategy')

    executor_path = inspect.getmodule(StrategyExecutor).__name__
    mock_executor_class = mocker.patch(f"{executor_path}.StrategyExecutor")
    # mock_executor_class = mocker.patch('services.strategy_executor.StrategyExecutor')

    mock_executor_instance = mock_executor_class.return_value
    mock_executor_instance.execute = AsyncMock(return_value={
        "follow_through": [{'code': '005930', 'name': '삼성전자'}],
        "not_follow_through": [{'code': '000660', 'name': 'SK하이닉스'}]
    })

    app = TradingApp(main_config_path="dummy/path", tr_ids_config_path="dummy/path")
    app.time_manager = MagicMock(spec=TimeManager)
    app.time_manager.is_market_open.return_value = True
    app.trading_service = MagicMock(spec=TradingService)
    app.trading_service.get_top_market_cap_stocks_code = AsyncMock(return_value={
        "rt_cd": "0",
        "output": [{"mksc_shrn_iscd": "005930"}, {"mksc_shrn_iscd": "000660"}]
    })
    app.broker = MagicMock(spec=BrokerAPIWrapper)
    app.logger = logging.getLogger('test_trading_app')

    await app._execute_action('10')

    captured = capsys.readouterr()
    assert "모멘텀 전략 결과" in captured.out
    assert str({'code': '005930', 'name': '삼성전자'}) in captured.out

# --- New and Corrected Test Cases ---
@pytest.mark.asyncio
async def test_execute_action_get_current_price(mocker):
    """메뉴 '1' 선택 시 data_handlers.handle_get_current_stock_price가 호출되는지 테스트합니다."""
    mocker.patch('trading_app.load_config', return_value=get_mock_config())
    app = TradingApp(main_config_path="dummy/path", tr_ids_config_path="dummy/path")
    app.data_handlers = AsyncMock()
    await app._execute_action('1')
    app.data_handlers.handle_get_current_stock_price.assert_awaited_once_with("005930")

@pytest.mark.asyncio
async def test_execute_action_get_account_balance(mocker):
    """메뉴 '2' 선택 시 data_handlers.handle_get_account_balance가 호출되는지 테스트합니다."""
    mocker.patch('trading_app.load_config', return_value=get_mock_config())
    app = TradingApp(main_config_path="dummy/path", tr_ids_config_path="dummy/path")
    app.data_handlers = AsyncMock()
    await app._execute_action('2')
    app.data_handlers.handle_get_account_balance.assert_awaited_once()


@pytest.mark.asyncio
async def test_execute_action_place_buy_order(mocker):
    """메뉴 '3' 선택 시 transaction_handlers.handle_place_buy_order가 호출되는지 테스트합니다."""
    mocker.patch('trading_app.load_config', return_value=get_mock_config())

    app = TradingApp(main_config_path="dummy/path", tr_ids_config_path="dummy/path")

    app.logger = mocker.MagicMock()
    app.time_manager = mocker.MagicMock()

    # app.cli_view를 Mock하고, _select_environment에서 호출되는 메서드를 AsyncMock으로 설정
    app.cli_view = mocker.MagicMock()
    # Traceback에 따라 get_user_input이 문제이므로, 이를 AsyncMock으로 설정
    # _select_environment가 '1'을 반환하도록 설정하여 실전투자 경로를 따르게 함
    app.cli_view.get_user_input = AsyncMock(return_value='1')  # <<< 이 부분을 수정했습니다.

    # _complete_api_initialization이 호출될 때 필요한 종속성들을 모의합니다.
    mock_api_client_class = mocker.patch('trading_app.KoreaInvestApiClient')
    mock_trading_service_class = mocker.patch('trading_app.TradingService')
    mock_data_handlers_class = mocker.patch('trading_app.DataHandlers')
    mock_transaction_handlers_class = mocker.patch('trading_app.TransactionHandlers')
    mock_broker_api_wrapper_class = mocker.patch('trading_app.BrokerAPIWrapper')
    mock_backtest_data_provider_class = mocker.patch('trading_app.BacktestDataProvider')

    mock_api_client_instance = mock_api_client_class.return_value
    mock_trading_service_instance = mock_trading_service_class.return_value
    mock_data_handlers_instance = mock_data_handlers_class.return_value

    mock_transaction_handlers_instance = mock_transaction_handlers_class.return_value
    mock_transaction_handlers_instance.handle_place_buy_order = AsyncMock()

    mock_broker_api_wrapper_instance = mock_broker_api_wrapper_class.return_value
    mock_backtest_data_provider_instance = mock_backtest_data_provider_class.return_value

    # _complete_api_initialization에서 호출되는 self.env.get_access_token 모의
    app.env = mocker.MagicMock()
    app.env.get_access_token = AsyncMock(return_value="mock_access_token_value")
    app.env.set_trading_mode = mocker.MagicMock()

    # 시장이 열려있는 상태를 모의하여 주문 제출 로직이 실행되도록 보장합니다.
    app.time_manager.is_market_open.return_value = True

    # _complete_api_initialization을 호출하여 app의 핸들러들을 올바르게 초기화합니다.
    init_success = await app._complete_api_initialization()
    assert init_success is True

    # _select_environment를 호출하여 환경이 선택되고 API 클라이언트가 초기화된 상태를 만듭니다.
    await app._select_environment()

    await app._execute_action('3')

    # assert_awaited_once_with를 사용하여 비동기 메서드가 한 번 호출되었는지 확인
    mock_transaction_handlers_instance.handle_place_buy_order.assert_awaited_once_with("005930", "58500", "1", "00")


@pytest.mark.asyncio
async def test_execute_action_realtime_stream(mocker):
    """메뉴 '4' 선택 시 handle_realtime_price_quote_stream이 호출되는지 테스트합니다."""
    mocker.patch('trading_app.load_config', return_value=get_mock_config())
    app = TradingApp(main_config_path="dummy/path", tr_ids_config_path="dummy/path")
    app.transaction_handlers = AsyncMock()
    await app._execute_action('4')
    app.transaction_handlers.handle_realtime_price_quote_stream.assert_awaited_once_with("005930")

@pytest.mark.asyncio
async def test_execute_action_display_change_rate(mocker):
    """메뉴 '5' 선택 시 handle_display_stock_change_rate가 호출되는지 테스트합니다."""
    mocker.patch('trading_app.load_config', return_value=get_mock_config())
    app = TradingApp(main_config_path="dummy/path", tr_ids_config_path="dummy/path")
    app.data_handlers = AsyncMock()
    await app._execute_action('5')
    app.data_handlers.handle_display_stock_change_rate.assert_awaited_once_with("005930")

@pytest.mark.asyncio
async def test_execute_action_display_vs_open_price(mocker):
    """메뉴 '6' 선택 시 handle_display_stock_vs_open_price가 호출되는지 테스트합니다."""
    mocker.patch('trading_app.load_config', return_value=get_mock_config())
    app = TradingApp(main_config_path="dummy/path", tr_ids_config_path="dummy/path")
    app.data_handlers = AsyncMock()
    await app._execute_action('6')
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
    """잘못된 메뉴 선택 시 "Invalid choice" 메시지가 출력되는지 테스트합니다."""
    mocker.patch('trading_app.load_config', return_value=get_mock_config())
    app = TradingApp(main_config_path="dummy/path", tr_ids_config_path="dummy/path")
    await app._execute_action('9999')
    captured = capsys.readouterr()
    assert "유효하지 않은 선택입니다" in captured.out