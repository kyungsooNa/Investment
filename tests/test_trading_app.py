import pytest
import logging
from unittest.mock import MagicMock, AsyncMock
from trading_app import TradingApp
from services.trading_service import TradingService
from core.time_manager import TimeManager
from user_api.broker_api_wrapper import BrokerAPIWrapper


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
    app.trading_service.get_top_market_cap_stocks = AsyncMock(return_value=mock_top_stocks_response)

    app.broker = MagicMock(spec=BrokerAPIWrapper)
    app.logger = logging.getLogger('test_trading_app')

    await app._execute_action('10')

    captured = capsys.readouterr()
    assert "모멘텀 전략 결과" in captured.out
    assert str({'code': '005930', 'name': '삼성전자'}) in captured.out