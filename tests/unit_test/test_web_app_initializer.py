import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from view.web.web_app_initializer import WebAppContext
from pydantic import BaseModel
import contextlib

@pytest.fixture
def mock_deps():
    """WebAppContext가 의존하는 모든 외부 모듈을 Mocking합니다."""
    patch_targets = [
        ("load_configs", patch("view.web.web_app_initializer.load_configs")),
        ("env", patch("view.web.web_app_initializer.KoreaInvestApiEnv", autospec=True)),
        ("tm", patch("view.web.web_app_initializer.TimeManager", autospec=True)),
        ("broker", patch("view.web.web_app_initializer.BrokerAPIWrapper", autospec=True)),
        ("ts", patch("view.web.web_app_initializer.TradingService", autospec=True)),
        ("sqs", patch("view.web.web_app_initializer.StockQueryService", autospec=True)),
        ("oes", patch("view.web.web_app_initializer.OrderExecutionService", autospec=True)),
        ("vtm", patch("view.web.web_app_initializer.VirtualTradeManager", autospec=True)),
        ("scm", patch("view.web.web_app_initializer.StockCodeMapper", autospec=True)),
        ("sched", patch("view.web.web_app_initializer.StrategyScheduler", autospec=True)),
        ("rdm", patch("view.web.web_app_initializer.RealtimeDataManager", autospec=True)),
        ("ind", patch("view.web.web_app_initializer.IndicatorService", autospec=True)),
        ("web_api", patch("view.web.web_app_initializer.web_api")),
        ("ous", patch("view.web.web_app_initializer.OneilUniverseService", autospec=True)),
        ("vb", patch("view.web.web_app_initializer.VolumeBreakoutLiveStrategy", autospec=True)),
        ("pbf", patch("view.web.web_app_initializer.ProgramBuyFollowStrategy", autospec=True)),
        ("tvb", patch("view.web.web_app_initializer.TraditionalVolumeBreakoutStrategy", autospec=True)),
        ("osb", patch("view.web.web_app_initializer.OneilSqueezeBreakoutStrategy", autospec=True)),
        ("pp", patch("view.web.web_app_initializer.OneilPocketPivotStrategy", autospec=True)),
        ("cm", patch("view.web.web_app_initializer.CacheManager", autospec=True)),
        ("logger", patch("view.web.web_app_initializer.Logger", autospec=True)),
    ]

    with contextlib.ExitStack() as stack:
        mocks = {name: stack.enter_context(p) for name, p in patch_targets}
        mocks["load_configs"].return_value = {
            "market_open_time": "09:00",
            "market_close_time": "15:30",
            "market_timezone": "Asia/Seoul"
        }
        yield mocks

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
    
    # Mock env.get_access_token / get_real_access_token 성공 설정
    env_instance = mock_deps["env"].return_value
    env_instance.get_access_token = AsyncMock(return_value=True)
    env_instance.get_real_access_token = AsyncMock(return_value="fake_real_token")
    
    # Act
    res = await ctx.initialize_services(is_paper_trading=True)
    
    # Assert
    assert res is True
    assert ctx.initialized is True
    env_instance.set_trading_mode.assert_called_with(True)
    mock_deps["broker"].assert_called()
    mock_deps["ts"].assert_called()
    mock_deps["ind"].assert_called()
    mock_deps["sqs"].assert_called()
    mock_deps["oes"].assert_called()
    mock_deps["ous"].assert_called()

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
    assert scheduler.register.call_count >= 5
    
    # 전략 초기화 검증
    mock_deps["vb"].assert_called()
    mock_deps["pbf"].assert_called()
    mock_deps["tvb"].assert_called()
    mock_deps["osb"].assert_called()
    mock_deps["pp"].assert_called()

@pytest.mark.asyncio
async def test_program_trading_subscription(mock_deps):
    """프로그램 매매 구독/해지 로직 검증"""
    ctx = WebAppContext(None)
    
    # RealtimeDataManager Mock 인스턴스 설정
    mock_rdm_instance = ctx.realtime_data_manager
    mock_rdm_instance.is_subscribed.return_value = False

    ctx.stock_query_service = MagicMock()
    ctx.stock_query_service.connect_websocket = AsyncMock(return_value=True)
    ctx.stock_query_service.subscribe_program_trading = AsyncMock()
    ctx.stock_query_service.subscribe_realtime_price = AsyncMock()
    ctx.stock_query_service.unsubscribe_program_trading = AsyncMock()
    ctx.stock_query_service.unsubscribe_realtime_price = AsyncMock()
    
    # 1. 구독 시작
    await ctx.start_program_trading("005930")
    ctx.stock_query_service.connect_websocket.assert_awaited_once()
    ctx.stock_query_service.subscribe_program_trading.assert_awaited_with("005930")
    mock_rdm_instance.add_subscribed_code.assert_called_with("005930")
    
    # 2. 중복 구독 시도 (API 호출 없어야 함)
    mock_rdm_instance.is_subscribed.return_value = True
    await ctx.start_program_trading("005930")
    assert ctx.stock_query_service.subscribe_program_trading.call_count == 1
    
    # 3. 구독 해지
    await ctx.stop_program_trading("005930")
    ctx.stock_query_service.unsubscribe_program_trading.assert_awaited_with("005930")
    mock_rdm_instance.remove_subscribed_code.assert_called_with("005930")

def test_time_manager_methods(mock_deps):
    """TimeManager 관련 메서드 테스트 (None 체크 포함)"""
    ctx = WebAppContext(None)
    
    # 1. time_manager가 없을 때
    ctx.time_manager = None
    assert ctx.is_market_open() is False
    assert ctx.get_current_time_str() == ""
    
    # 2. time_manager가 있을 때
    ctx.time_manager = MagicMock()
    ctx.time_manager.is_market_open.return_value = True
    
    # datetime 객체 모킹
    mock_dt = MagicMock()
    mock_dt.strftime.return_value = "2025-01-01 12:00:00"
    ctx.time_manager.get_current_kst_time.return_value = mock_dt
    
    assert ctx.is_market_open() is True
    assert ctx.get_current_time_str() == "2025-01-01 12:00:00"

@pytest.mark.asyncio
async def test_lifecycle_methods(mock_deps):
    """백그라운드 태스크 시작 및 종료 테스트"""
    ctx = WebAppContext(None)
    ctx.realtime_data_manager = MagicMock()
    ctx.realtime_data_manager.shutdown = AsyncMock()
    
    # Start
    ctx.start_background_tasks()
    ctx.realtime_data_manager.start_background_tasks.assert_called_once()
    
    # Shutdown
    await ctx.shutdown()
    ctx.realtime_data_manager.shutdown.assert_awaited_once()

def test_web_realtime_callback(mock_deps):
    """웹소켓 콜백 처리 테스트"""
    ctx = WebAppContext(None)
    ctx.stock_query_service = MagicMock()
    ctx.stock_query_service.dispatch_realtime_message = MagicMock()
    ctx.stock_query_service.get_cached_realtime_price = MagicMock(return_value=None)
    ctx.realtime_data_manager = MagicMock()
    
    # 1. 일반 데이터 (program trading 아님)
    data_normal = {"type": "realtime_price", "data": {}}
    ctx._web_realtime_callback(data_normal)
    ctx.stock_query_service.dispatch_realtime_message.assert_called_with(data_normal)
    ctx.realtime_data_manager.on_data_received.assert_not_called()
    
    # 2. 프로그램 매매 데이터 (가격 정보 주입 - dict 형태)
    mock_price_data = {"price": "70000", "change": "100", "rate": "0.1", "sign": "2"}
    ctx.stock_query_service.get_cached_realtime_price.return_value = mock_price_data
    
    data_pt = {
        "type": "realtime_program_trading",
        "data": {"유가증권단축종목코드": "005930"}
    }
    ctx._web_realtime_callback(data_pt)
    
    ctx.stock_query_service.get_cached_realtime_price.assert_called_with("005930")
    # 데이터가 주입되었는지 확인
    received_data = ctx.realtime_data_manager.on_data_received.call_args[0][0]
    assert received_data["price"] == "70000"
    assert received_data["rate"] == "0.1"

@pytest.mark.asyncio
async def test_stop_all_program_trading(mock_deps):
    """모든 프로그램 매매 구독 해지 테스트"""
    ctx = WebAppContext(None)
    ctx.realtime_data_manager = MagicMock()
    ctx.realtime_data_manager.get_subscribed_codes.return_value = ["005930", "000660"]
    
    ctx.stock_query_service = MagicMock()
    ctx.stock_query_service.unsubscribe_program_trading = AsyncMock()
    ctx.stock_query_service.unsubscribe_realtime_price = AsyncMock()
    
    await ctx.stop_all_program_trading()
    
    assert ctx.stock_query_service.unsubscribe_program_trading.call_count == 2
    assert ctx.stock_query_service.unsubscribe_realtime_price.call_count == 2
    ctx.realtime_data_manager.clear_subscribed_codes.assert_called_once()

@pytest.mark.asyncio
async def test_initialize_services_with_pydantic_config_object(mock_deps):
    """
    설정 객체가 dict가 아닌 Pydantic 모델일 때(get 메서드 없음),
    initialize_services 내부에서 dict로 변환하여 CacheManager에 전달하는지 검증.
    """
    # Arrange
    ctx = WebAppContext(None)

    # Pydantic 모델 흉내 (dict 메서드 또는 model_dump 메서드 보유, get 메서드 없음)
    class MockAppConfig(BaseModel):
        market_open_time: str = "09:00"
        market_close_time: str = "15:30"
        market_timezone: str = "Asia/Seoul"
        cache: dict = {"base_dir": ".cache"}

    config_model = MockAppConfig()
    
    # load_configs가 dict가 아닌 객체를 반환하도록 설정 (기존 mock 오버라이드)
    mock_deps["load_configs"].return_value = config_model
    
    ctx.load_config_and_env()
    
    # Act
    await ctx.initialize_services(is_paper_trading=True)

    # Assert
    # CacheManager 생성자에 전달된 인자가 dict 타입인지 확인
    mock_deps["cm"].assert_called_once()
    init_arg = mock_deps["cm"].call_args[0][0]
    assert isinstance(init_arg, dict)
    assert init_arg["market_open_time"] == "09:00"

@pytest.mark.asyncio
async def test_start_background_tasks_with_restore(mock_deps):
    """
    start_background_tasks가 구독 복원 및 백그라운드 서비스 태스크를
    올바르게 생성하고 실행하는지 검증합니다.
    """
    # Arrange
    ctx = WebAppContext(None)
    
    # Mock realtime_data_manager
    mock_rdm_instance = ctx.realtime_data_manager
    mock_rdm_instance.get_subscribed_codes.return_value = ["005930", "000660"]
    
    # Mock background_service
    ctx.background_service = MagicMock()
    ctx.background_service.refresh_investor_ranking = AsyncMock()
    ctx.background_service.start_after_market_scheduler = AsyncMock()
    
    # Mock the method that will be called inside the task
    ctx._restore_program_trading = AsyncMock()

    # Patch asyncio.create_task to collect coroutines
    created_coroutines = []
    def coro_collector(coro):
        created_coroutines.append(coro)
        return MagicMock() # return a mock task object
        
    with patch("view.web.web_app_initializer.asyncio.create_task", side_effect=coro_collector) as mock_create_task:
        # Act
        ctx.start_background_tasks()

    # Await all collected coroutines
    for coro in created_coroutines:
        await coro
        
    # Assert
    # 1. RDM의 start_background_tasks 호출 확인
    mock_rdm_instance.start_background_tasks.assert_called_once()
    
    # 2. RDM의 get_subscribed_codes 호출 확인
    mock_rdm_instance.get_subscribed_codes.assert_called_once()
    
    # 3. create_task가 3번 호출되었는지 확인
    assert mock_create_task.call_count == 3
    
    # 4. 각 태스크의 내부 메서드가 await 되었는지 확인
    ctx._restore_program_trading.assert_awaited_once_with(["005930", "000660"])
    ctx.background_service.refresh_investor_ranking.assert_awaited_once()
    ctx.background_service.start_after_market_scheduler.assert_awaited_once()

@pytest.mark.asyncio
async def test_restore_program_trading_success(mock_deps):
    """_restore_program_trading: 모든 종목 구독 복원 성공 케이스."""
    # Arrange
    ctx = WebAppContext(None)
    ctx.stock_query_service = MagicMock()
    ctx.stock_query_service.connect_websocket = AsyncMock(return_value=True)
    ctx.stock_query_service.subscribe_program_trading = AsyncMock()
    ctx.stock_query_service.subscribe_realtime_price = AsyncMock()
    
    codes_to_restore = ["005930", "000660"]
    
    # Act
    await ctx._restore_program_trading(codes_to_restore)
    
    # Assert
    assert ctx.stock_query_service.connect_websocket.call_count == 2
    assert ctx.stock_query_service.subscribe_program_trading.call_count == 2
    assert ctx.stock_query_service.subscribe_realtime_price.call_count == 2
    
    ctx.logger.info.assert_any_call(f"프로그램매매 구독 복원 완료: 2/2개 종목")

@pytest.mark.asyncio
async def test_restore_program_trading_partial_failure(mock_deps):
    """_restore_program_trading: 일부 종목 복원 실패 시에도 계속 진행하는지 검증."""
    # Arrange
    ctx = WebAppContext(None)
    ctx.stock_query_service = MagicMock()
    
    # 005930: connect fails, 000660: subscribe fails, 035720: success
    async def connect_side_effect(callback):
        return ctx.stock_query_service.connect_websocket.await_count != 1
    async def subscribe_side_effect(code):
        if code == "000660": raise Exception("Subscription failed")
    
    ctx.stock_query_service.connect_websocket = AsyncMock(side_effect=connect_side_effect)
    ctx.stock_query_service.subscribe_program_trading = AsyncMock(side_effect=subscribe_side_effect)
    ctx.stock_query_service.subscribe_realtime_price = AsyncMock()
    
    # Act
    await ctx._restore_program_trading(["005930", "000660", "035720"])
    
    # Assert
    assert ctx.stock_query_service.connect_websocket.await_count == 3
    assert ctx.stock_query_service.subscribe_program_trading.await_count == 2
    ctx.stock_query_service.subscribe_realtime_price.assert_awaited_once_with("035720")
    ctx.logger.warning.assert_called_with("프로그램매매 복원 실패 (WebSocket 연결 불가): 005930")
    ctx.logger.error.assert_called_with("프로그램매매 복원 중 오류 (000660): Subscription failed")
    ctx.logger.info.assert_any_call("프로그램매매 구독 복원 완료: 1/3개 종목")