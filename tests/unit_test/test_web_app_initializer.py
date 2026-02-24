import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from view.web.web_app_initializer import WebAppContext

@pytest.fixture
def mock_deps():
    """WebAppContext가 의존하는 모든 외부 모듈을 Mocking합니다."""
    with patch("view.web.web_app_initializer.load_configs") as mock_load, \
         patch("view.web.web_app_initializer.KoreaInvestApiEnv") as mock_env, \
         patch("view.web.web_app_initializer.TimeManager") as mock_tm, \
         patch("view.web.web_app_initializer.BrokerAPIWrapper") as mock_broker, \
         patch("view.web.web_app_initializer.TradingService") as mock_ts, \
         patch("view.web.web_app_initializer.StockQueryService") as mock_sqs, \
         patch("view.web.web_app_initializer.OrderExecutionService") as mock_oes, \
         patch("view.web.web_app_initializer.VirtualTradeManager") as mock_vtm, \
         patch("view.web.web_app_initializer.StockCodeMapper") as mock_scm, \
         patch("view.web.web_app_initializer.StrategyScheduler") as mock_sched, \
         patch("view.web.web_app_initializer.web_api") as mock_web_api:
        
        mock_load.return_value = {
            "market_open_time": "09:00",
            "market_close_time": "15:30",
            "market_timezone": "Asia/Seoul"
        }
        yield {
            "load_configs": mock_load,
            "env": mock_env,
            "tm": mock_tm,
            "broker": mock_broker,
            "ts": mock_ts,
            "sqs": mock_sqs,
            "oes": mock_oes,
            "vtm": mock_vtm,
            "scm": mock_scm,
            "sched": mock_sched,
            "web_api": mock_web_api
        }

def test_initialization(mock_deps):
    """WebAppContext 객체 생성 시 초기 상태 검증"""
    # Arrange
    mock_app_ctx = MagicMock()
    
    # Act
    ctx = WebAppContext(mock_app_ctx)
    
    # Assert
    assert ctx.initialized is False
    # web_api에 context가 설정되었는지 확인
    mock_deps["web_api"].set_ctx.assert_called_with(ctx)

def test_load_config_and_env(mock_deps):
    """설정 로드 및 환경 객체 초기화 검증"""
    ctx = WebAppContext(None)
    ctx.load_config_and_env()
    
    mock_deps["load_configs"].assert_called_once()
    mock_deps["env"].assert_called_once()
    mock_deps["tm"].assert_called_once()
    assert ctx.full_config is not None

@pytest.mark.asyncio
async def test_initialize_services_success(mock_deps):
    """서비스 초기화 성공 시나리오"""
    ctx = WebAppContext(None)
    ctx.load_config_and_env()
    
    # Mock env.get_access_token 성공 설정
    env_instance = mock_deps["env"].return_value
    env_instance.get_access_token = AsyncMock(return_value=True)
    
    # Act
    res = await ctx.initialize_services(is_paper_trading=True)
    
    # Assert
    assert res is True
    assert ctx.initialized is True
    env_instance.set_trading_mode.assert_called_with(True)
    mock_deps["broker"].assert_called()
    mock_deps["ts"].assert_called()

@pytest.mark.asyncio
async def test_initialize_services_failure(mock_deps):
    """토큰 발급 실패 시 서비스 초기화 실패 검증"""
    ctx = WebAppContext(None)
    ctx.load_config_and_env()
    
    env_instance = mock_deps["env"].return_value
    env_instance.get_access_token = AsyncMock(return_value=False)
    
    res = await ctx.initialize_services(is_paper_trading=False)
    
    assert res is False
    assert ctx.initialized is False

def test_get_env_type(mock_deps):
    """환경 타입 문자열 반환 검증"""
    ctx = WebAppContext(None)
    assert ctx.get_env_type() == "미설정"
    
    ctx.env = MagicMock()
    ctx.env.is_paper_trading = True
    assert ctx.get_env_type() == "모의투자"
    
    ctx.env.is_paper_trading = False
    assert ctx.get_env_type() == "실전투자"

def test_initialize_scheduler(mock_deps):
    """스케줄러 초기화 및 전략 등록 검증"""
    ctx = WebAppContext(None)
    # 스케줄러 생성에 필요한 의존성 주입
    ctx.virtual_manager = MagicMock()
    ctx.order_execution_service = MagicMock()
    ctx.time_manager = MagicMock()
    ctx.trading_service = MagicMock()
    ctx.stock_query_service = MagicMock()
    
    ctx.initialize_scheduler()
    
    mock_deps["sched"].assert_called_once()
    scheduler = mock_deps["sched"].return_value
    # 최소 2개 이상의 전략이 등록되어야 함 (VolumeBreakout, ProgramBuyFollow)
    assert scheduler.register.call_count >= 2

@pytest.mark.asyncio
async def test_program_trading_subscription(mock_deps):
    """프로그램 매매 구독/해지 로직 검증"""
    ctx = WebAppContext(None)
    ctx.broker = MagicMock()
    ctx.broker.connect_websocket = AsyncMock(return_value=True)
    ctx.trading_service = MagicMock()
    ctx.trading_service.subscribe_program_trading = AsyncMock()
    ctx.trading_service.subscribe_realtime_price = AsyncMock()
    ctx.trading_service.unsubscribe_program_trading = AsyncMock()
    ctx.trading_service.unsubscribe_realtime_price = AsyncMock()
    
    # 1. 구독 시작
    await ctx.start_program_trading("005930")
    ctx.broker.connect_websocket.assert_awaited_once()
    ctx.trading_service.subscribe_program_trading.assert_awaited_with("005930")
    assert "005930" in ctx._pt_codes
    
    # 2. 중복 구독 시도 (API 호출 없어야 함)
    await ctx.start_program_trading("005930")
    assert ctx.trading_service.subscribe_program_trading.call_count == 1
    
    # 3. 구독 해지
    await ctx.stop_program_trading("005930")
    ctx.trading_service.unsubscribe_program_trading.assert_awaited_with("005930")
    assert "005930" not in ctx._pt_codes