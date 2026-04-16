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
        ("tm", patch("view.web.web_app_initializer.MarketClock", autospec=True)),
        ("broker", patch("view.web.web_app_initializer.BrokerAPIWrapper", autospec=True)),
        ("mds", patch("view.web.web_app_initializer.MarketDataService", autospec=True)),
        ("sqs", patch("view.web.web_app_initializer.StockQueryService", autospec=True)),
        ("oes", patch("view.web.web_app_initializer.OrderExecutionService", autospec=True)),
        ("vtm", patch("view.web.web_app_initializer.VirtualTradeRepository", autospec=True)),
        ("scm", patch("view.web.web_app_initializer.StockCodeRepository", autospec=True)),
        ("sched", patch("view.web.web_app_initializer.StrategyScheduler", autospec=True)),
        ("rdm", patch("view.web.web_app_initializer.ProgramTradingStreamService", autospec=True)),
        ("ind", patch("view.web.web_app_initializer.IndicatorService", autospec=True)),
        ("web_api", patch("view.web.web_app_initializer.web_api")),
        ("ous", patch("view.web.web_app_initializer.OneilUniverseService", autospec=True)),
        ("ranking_task", patch("view.web.web_app_initializer.RankingTask", autospec=True)),
        ("watchdog_task", patch("view.web.web_app_initializer.WebSocketWatchdogTask", autospec=True)),
        ("premium_watchlist_task", patch("view.web.web_app_initializer.PremiumWatchlistGeneratorTask", autospec=True)),
        ("log_cleanup_task", patch("view.web.web_app_initializer.LogCleanupTask", autospec=True)),
        ("newhigh_task", patch("view.web.web_app_initializer.NewHighTask", autospec=True)),
        ("vb", patch("view.web.web_app_initializer.VolumeBreakoutLiveStrategy", autospec=True)),
        ("pbf", patch("view.web.web_app_initializer.ProgramBuyFollowStrategy", autospec=True)),
        ("tvb", patch("view.web.web_app_initializer.TraditionalVolumeBreakoutStrategy", autospec=True)),
        ("osb", patch("view.web.web_app_initializer.OneilSqueezeBreakoutStrategy", autospec=True)),
        ("pp", patch("view.web.web_app_initializer.OneilPocketPivotStrategy", autospec=True)),
        ("htf", patch("view.web.web_app_initializer.HighTightFlagStrategy", autospec=True)),
        ("cm", patch("view.web.web_app_initializer.CacheStore", autospec=True)),
        ("logger", patch("view.web.web_app_initializer.Logger", autospec=True)),
        ("tn", patch("view.web.web_app_initializer.TelegramNotifier", autospec=True)),
        ("tr", patch("view.web.web_app_initializer.TelegramReporter", autospec=True)),
    ]

    with contextlib.ExitStack() as stack:
        mocks = {name: stack.enter_context(p) for name, p in patch_targets}
        mocks["load_configs"].return_value = {
            "market_open_time": "09:00",
            "market_close_time": "15:40",
            "market_timezone": "Asia/Seoul"
        }
        mocks["logger"].return_value.log_dir = "logs"
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
    # StockCodeRepository 인스턴스가 BrokerAPIWrapper에 주입되었는지 검증
    _, broker_kwargs = mock_deps["broker"].call_args
    assert broker_kwargs.get("stock_code_repository") is mock_deps["scm"].return_value
    mock_deps["mds"].assert_called()
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
    ctx.virtual_trade_service = MagicMock()
    ctx.order_execution_service = MagicMock()
    ctx.market_clock = MagicMock()
    ctx.trading_service = MagicMock()
    ctx.stock_query_service = MagicMock()
    ctx.broker = MagicMock()

    ctx.initialize_scheduler()
    
    mock_deps["sched"].assert_called_once()
    scheduler = mock_deps["sched"].return_value
    # 최소 2개 이상의 전략이 등록되어야 함 (VolumeBreakout, ProgramBuyFollow)
    assert scheduler.register.call_count >= 6
    
    # 전략 초기화 검증
    mock_deps["vb"].assert_called()
    mock_deps["pbf"].assert_called()
    mock_deps["tvb"].assert_called()
    mock_deps["osb"].assert_called()
    mock_deps["pp"].assert_called()
    mock_deps["htf"].assert_called()

@pytest.mark.asyncio
async def test_program_trading_subscription(mock_deps):
    """프로그램 매매 구독/해지 로직 검증 — streaming_stock_repo SSOT 기반"""
    from repositories.streaming_stock_repo import StreamingType
    ctx = WebAppContext(None)

    ctx.pm = MagicMock()
    ctx.pm.start_timer.return_value = 0.0
    ctx.streaming_service = MagicMock()
    ctx.streaming_service.connect_websocket = AsyncMock(return_value=True)
    ctx.streaming_service.subscribe_program_trading = AsyncMock(return_value=True)
    ctx.streaming_service.subscribe_realtime_price = AsyncMock(return_value=True)
    ctx.streaming_service.unsubscribe_program_trading = AsyncMock()
    ctx.streaming_service.unsubscribe_realtime_price = AsyncMock()

    # streaming_stock_repo mock
    ctx.streaming_stock_repo = MagicMock()
    ctx.streaming_stock_repo.get_desired = MagicMock(return_value=set())
    ctx.streaming_stock_repo.mark_desired = AsyncMock()
    ctx.streaming_stock_repo.unmark_desired = AsyncMock()
    ctx.streaming_stock_repo.mark_active = AsyncMock()
    ctx.streaming_stock_repo.mark_inactive = AsyncMock()

    # 1. 구독 시작
    await ctx.start_program_trading("005930")
    ctx.streaming_service.connect_websocket.assert_awaited_once()
    ctx.streaming_service.subscribe_program_trading.assert_awaited_with("005930")
    ctx.streaming_stock_repo.mark_desired.assert_called_with("005930", StreamingType.PROGRAM_TRADING)

    # 2. 중복 구독 시도 (API 호출 없어야 함)
    ctx.streaming_stock_repo.get_desired.return_value = {"005930"}
    await ctx.start_program_trading("005930")
    assert ctx.streaming_service.subscribe_program_trading.call_count == 1

    # 3. 구독 해지
    await ctx.stop_program_trading("005930")
    ctx.streaming_service.unsubscribe_program_trading.assert_awaited_with("005930")
    ctx.streaming_stock_repo.unmark_desired.assert_called_with("005930", StreamingType.PROGRAM_TRADING)

@pytest.mark.asyncio
async def test_market_clock_methods(mock_deps):
    """MarketClock 관련 메서드 테스트 (None 체크 포함)"""
    ctx = WebAppContext(None)
    
    # 1. time_manager가 없을 때
    ctx.market_clock = None
    ctx._mcs = None
    assert await ctx.is_market_open_now() is False
    assert ctx.get_current_time_str() == ""
    
    # 2. time_manager가 있을 때
    ctx.market_clock = MagicMock()
    ctx._mcs = AsyncMock()
    ctx._mcs.is_market_open_now.return_value = True
    
    # datetime 객체 모킹
    mock_dt = MagicMock()
    mock_dt.strftime.return_value = "2025-01-01 12:00:00"
    ctx.market_clock.get_current_kst_time.return_value = mock_dt
    
    assert await ctx.is_market_open_now() is True
    assert ctx.get_current_time_str() == "2025-01-01 12:00:00"

@pytest.mark.asyncio
async def test_lifecycle_methods(mock_deps):
    """백그라운드 태스크 시작 및 종료 테스트 — BackgroundScheduler에 위임."""
    ctx = WebAppContext(None)

    # Mock background_scheduler
    ctx.background_scheduler = MagicMock()
    ctx.background_scheduler.start_all = AsyncMock()
    ctx.background_scheduler.shutdown = AsyncMock()
    ctx.websocket_watchdog_task = MagicMock()

    # Start — BackgroundScheduler.start_all()에 위임
    with patch("view.web.web_app_initializer.asyncio.create_task") as mock_create_task:
        ctx.start_background_tasks()
        mock_create_task.assert_called_once()

    # Shutdown — BackgroundScheduler.shutdown()에 위임
    await ctx.shutdown()
    ctx.background_scheduler.shutdown.assert_awaited_once()

def test_web_realtime_callback(mock_deps):
    """웹소켓 콜백 처리 테스트"""
    ctx = WebAppContext(None)
    ctx.streaming_service = MagicMock()
    ctx.streaming_service.dispatch_realtime_message = MagicMock()
    ctx.streaming_service.get_cached_realtime_price = MagicMock(return_value=None)
    ctx.program_trading_stream_service = MagicMock()

    # 1. 일반 데이터 (program trading 아님)
    data_normal = {"type": "realtime_price", "data": {}}
    ctx._web_realtime_callback(data_normal)
    ctx.streaming_service.dispatch_realtime_message.assert_called_with(data_normal)
    ctx.program_trading_stream_service.on_data_received.assert_not_called()

    # 2. 프로그램 매매 데이터 (가격 정보 주입 - dict 형태)
    mock_price_data = {"price": "70000", "change": "100", "rate": "0.1", "sign": "2"}
    ctx.streaming_service.get_cached_realtime_price.return_value = mock_price_data

    data_pt = {
        "type": "realtime_program_trading",
        "data": {"유가증권단축종목코드": "005930"}
    }
    ctx._web_realtime_callback(data_pt)

    ctx.streaming_service.get_cached_realtime_price.assert_called_with("005930")
    # dispatch 전에 가격이 주입되어 data_pt['data']에 반영됨
    assert data_pt['data']['price'] == "70000"
    assert data_pt['data']['rate'] == "0.1"
    # dispatch_realtime_message가 가격 주입된 data로 호출됨
    ctx.streaming_service.dispatch_realtime_message.assert_called_with(data_pt)
    # on_data_received는 StreamingService 옵저버 패턴을 통해 호출되므로 직접 호출 안 됨
    ctx.program_trading_stream_service.on_data_received.assert_not_called()

@pytest.mark.asyncio
async def test_stop_all_program_trading(mock_deps):
    """모든 프로그램 매매 구독 해지 테스트 — streaming_stock_repo SSOT 기반"""
    from repositories.streaming_stock_repo import StreamingType
    ctx = WebAppContext(None)

    ctx.streaming_service = MagicMock()
    ctx.streaming_service.unsubscribe_program_trading = AsyncMock()
    ctx.streaming_service.unsubscribe_realtime_price = AsyncMock()

    ctx.streaming_stock_repo = MagicMock()
    ctx.streaming_stock_repo.get_desired = MagicMock(return_value={"005930", "000660"})
    ctx.streaming_stock_repo.unmark_desired = AsyncMock()
    ctx.streaming_stock_repo.mark_inactive = AsyncMock()

    await ctx.stop_all_program_trading()

    assert ctx.streaming_service.unsubscribe_program_trading.call_count == 2
    assert ctx.streaming_service.unsubscribe_realtime_price.call_count == 2
    assert ctx.streaming_stock_repo.unmark_desired.call_count == 2

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
        market_close_time: str = "15:40"
        market_timezone: str = "Asia/Seoul"
        cache: dict = {"base_dir": ".cache"}

    config_model = MockAppConfig()
    
    # load_configs가 dict가 아닌 객체를 반환하도록 설정 (기존 mock 오버라이드)
    mock_deps["load_configs"].return_value = config_model
    
    ctx.load_config_and_env()
    
    # Act
    await ctx.initialize_services(is_paper_trading=True)

    # Assert
    # CacheStore 생성자에 전달된 인자가 dict 타입인지 확인
    mock_deps["cm"].assert_called_once()
    init_arg = mock_deps["cm"].call_args[0][0]
    assert isinstance(init_arg, dict)
    assert init_arg["market_open_time"] == "09:00"

@pytest.mark.asyncio
async def test_start_background_tasks_with_restore(mock_deps):
    """
    start_background_tasks가 BackgroundScheduler.start_all()에
    올바르게 위임하는지 검증합니다.
    """
    # Arrange
    ctx = WebAppContext(None)

    # Mock background_scheduler
    ctx.background_scheduler = MagicMock()
    ctx.background_scheduler.start_all = AsyncMock()
    ctx.websocket_watchdog_task = MagicMock()

    # Act
    with patch("view.web.web_app_initializer.asyncio.create_task") as mock_create_task:
        ctx.start_background_tasks()

    # Assert — BackgroundScheduler.start_all()이 create_task로 호출되었는지 확인
    mock_create_task.assert_called_once()

def test_load_config_and_env_with_telegram(mock_deps):
    """설정에 텔레그램 정보가 있으면 TelegramReporter가 초기화되는지 검증"""
    # Arrange
    mock_deps["load_configs"].return_value = {
        "market_open_time": "09:00",
        "market_close_time": "15:40",
        "market_timezone": "Asia/Seoul",
        "telegram_backlog_bot_token": "TEST_BACKLOG_TOKEN",
        "telegram_strategy_bot_token": "TEST_STRATEGY_TOKEN",
        "telegram_report_bot_token": "TEST_REPORT_TOKEN",
        "telegram_chat_id": "TEST_CHAT_ID"
    }
    ctx = WebAppContext(None)

    # Act
    ctx.load_config_and_env()

    # Assert
    mock_deps["tn"].assert_called_once_with(
        backlog_bot_token="TEST_BACKLOG_TOKEN",
        strategy_bot_token="TEST_STRATEGY_TOKEN",
        chat_id="TEST_CHAT_ID"
    )  # Notifier
    mock_deps["tr"].assert_called_once_with(report_bot_token="TEST_REPORT_TOKEN", chat_id="TEST_CHAT_ID") # Reporter
    assert ctx.telegram_reporter is not None

@pytest.mark.asyncio
async def test_initialize_services_injects_reporter(mock_deps):
    """RankingTask 초기화 시 telegram_reporter가 주입되는지 검증"""
    ctx = WebAppContext(None)
    ctx.load_config_and_env()
    ctx.telegram_reporter = MagicMock() # Reporter가 있다고 가정

    # Env mock 설정
    ctx.env.get_access_token = AsyncMock(return_value=True)
    ctx.env.get_real_access_token = AsyncMock(return_value="token")

    # Act
    await ctx.initialize_services(is_paper_trading=True)

    # Assert
    mock_ranking_cls = mock_deps["ranking_task"]
    _, kwargs = mock_ranking_cls.call_args
    assert kwargs.get("telegram_reporter") == ctx.telegram_reporter
    
    mock_newhigh_cls = mock_deps["newhigh_task"]
    _, newhigh_kwargs = mock_newhigh_cls.call_args
    assert newhigh_kwargs.get("stock_query_service") == ctx.stock_query_service


# ── _initialize_price_subscriptions ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_initialize_price_subscriptions_holdings(mock_deps):
    """보유 종목 구독: virtual_trade_service.get_holds() 기준으로 HIGH 구독."""
    from unittest.mock import patch as _patch
    ctx = WebAppContext(None)

    mock_price_svc = AsyncMock()
    ctx.price_subscription_service = mock_price_svc

    mock_vts = MagicMock()
    mock_vts.get_holds.return_value = [
        {"code": "005930", "name": "삼성전자"},
        {"code": "000660", "name": "SK하이닉스"},
    ]
    ctx.virtual_trade_service = mock_vts

    with _patch("view.web.web_app_initializer.StreamingType") as mock_st, \
         _patch("view.web.web_app_initializer.SubscriptionPriority", create=True):
        from services.price_subscription_service import SubscriptionPriority
        await ctx._initialize_price_subscriptions()

    # get_holds()가 호출되어야 하고, broker API는 호출되지 않아야 함
    mock_vts.get_holds.assert_called_once()
    assert mock_price_svc.add_subscription.call_count == 2
    codes_called = [c.args[0] for c in mock_price_svc.add_subscription.call_args_list]
    assert "005930" in codes_called
    assert "000660" in codes_called


@pytest.mark.asyncio
async def test_initialize_price_subscriptions_holdings_no_broker_call(mock_deps):
    """보유 종목 구독 시 broker.get_account_balance()를 호출하지 않음."""
    ctx = WebAppContext(None)

    mock_price_svc = AsyncMock()
    ctx.price_subscription_service = mock_price_svc

    mock_broker = MagicMock()
    ctx.broker = mock_broker

    mock_vts = MagicMock()
    mock_vts.get_holds.return_value = []
    ctx.virtual_trade_service = mock_vts

    await ctx._initialize_price_subscriptions()

    mock_broker.get_account_balance.assert_not_called()


@pytest.mark.asyncio
async def test_initialize_price_subscriptions_premium_extracts_code(mock_deps, tmp_path):
    """프리미엄 종목 구독: dict 항목에서 'code' 필드만 추출하여 sync_subscriptions에 전달."""
    import json
    premium_data = {
        "kospi": [
            {"code": "001820", "name": "삼화콘덴서", "market": "KOSPI", "rs_score": 0.0},
            {"code": "005930", "name": "삼성전자", "market": "KOSPI"},
        ],
        "kosdaq": [
            {"code": "086520", "name": "에코프로", "market": "KOSDAQ"},
        ],
    }
    premium_file = tmp_path / "premium_stocks.json"
    premium_file.write_text(json.dumps(premium_data), encoding="utf-8")

    ctx = WebAppContext(None)

    mock_price_svc = AsyncMock()
    ctx.price_subscription_service = mock_price_svc

    mock_vts = MagicMock()
    mock_vts.get_holds.return_value = []
    ctx.virtual_trade_service = mock_vts

    with patch("os.path.join", return_value=str(premium_file)), \
         patch("os.path.exists", return_value=True):
        await ctx._initialize_price_subscriptions()

    mock_price_svc.sync_subscriptions.assert_called_once()
    call_kwargs = mock_price_svc.sync_subscriptions.call_args[1]
    codes = call_kwargs["codes"]
    # 문자열 코드만 들어있어야 함 (dict 아님)
    assert all(isinstance(c, str) for c in codes), f"dict가 섞임: {codes}"
    assert set(codes) == {"001820", "005930", "086520"}


@pytest.mark.asyncio
async def test_initialize_price_subscriptions_premium_no_dict_leakage(mock_deps, tmp_path):
    """프리미엄 종목 codes에 dict가 절대 포함되지 않음을 확인 (버그 재현 방지)."""
    import json
    premium_data = {
        "kospi": [{"code": "001820", "name": "삼화콘덴서"}],
        "kosdaq": [],
    }
    premium_file = tmp_path / "premium_stocks.json"
    premium_file.write_text(json.dumps(premium_data), encoding="utf-8")

    ctx = WebAppContext(None)

    mock_price_svc = AsyncMock()
    ctx.price_subscription_service = mock_price_svc

    mock_vts = MagicMock()
    mock_vts.get_holds.return_value = []
    ctx.virtual_trade_service = mock_vts

    with patch("os.path.join", return_value=str(premium_file)), \
         patch("os.path.exists", return_value=True):
        await ctx._initialize_price_subscriptions()

    call_kwargs = mock_price_svc.sync_subscriptions.call_args[1]
    codes = call_kwargs["codes"]
    for c in codes:
        assert not isinstance(c, dict), f"codes에 dict 포함: {c}"