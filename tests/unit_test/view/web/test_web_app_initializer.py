import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch
from view.web.web_app_initializer import WebAppContext
from pydantic import BaseModel
import contextlib
from types import SimpleNamespace

@pytest.fixture
def mock_deps():
    """WebAppContext가 의존하는 모든 외부 모듈을 Mocking합니다."""
    patch_targets = [
        ("load_configs", patch("view.web.bootstrap.config_bootstrap.load_configs")),
        ("env", patch("view.web.bootstrap.config_bootstrap.KoreaInvestApiEnv", autospec=True)),
        ("tm", patch("view.web.bootstrap.config_bootstrap.MarketClock", autospec=True)),
        ("broker", patch("view.web.bootstrap.broker_bootstrap.BrokerAPIWrapper", autospec=True)),
        ("mds", patch("view.web.bootstrap.market_data_bootstrap.MarketDataService", autospec=True)),
        ("sqs", patch("view.web.bootstrap.query_bootstrap.StockQueryService", autospec=True)),
        ("oes", patch("view.web.bootstrap.service_container.OrderExecutionService", autospec=True)),
        ("risk_gate", patch("view.web.bootstrap.service_container.RiskGateService", autospec=True)),
        ("order_policy", patch("view.web.bootstrap.service_container.OrderPolicyService", autospec=True)),
        ("vtm", patch("view.web.web_app_initializer.VirtualTradeRepository", autospec=True)),
        ("backtest_journal_repo", patch("view.web.web_app_initializer.BacktestJournalRepository", autospec=True)),
        ("scm", patch("view.web.web_app_initializer.StockCodeRepository", autospec=True)),
        ("oscm", patch("view.web.web_app_initializer.OverseasStockCodeRepository", autospec=True)),
        ("sched", patch("view.web.bootstrap.strategy_factory.StrategyScheduler", autospec=True)),
        ("rdm", patch("view.web.web_app_initializer.ProgramTradingStreamService", autospec=True)),
        ("ind", patch("view.web.bootstrap.market_data_bootstrap.IndicatorService", autospec=True)),

        ("ous", patch("view.web.bootstrap.service_container.OneilUniverseService", autospec=True)),
        ("ranking_task", patch("view.web.bootstrap.query_bootstrap.RankingTask", autospec=True)),
        ("watchdog_task", patch("view.web.bootstrap.realtime_bootstrap.WebSocketWatchdogTask", autospec=True)),
        ("watchdog_task_in_main", patch("view.web.web_app_initializer.WebSocketWatchdogTask", autospec=True)),
        ("premium_watchlist_task", patch("view.web.bootstrap.service_container.PremiumWatchlistGeneratorTask", autospec=True)),
        ("log_cleanup_task", patch("view.web.bootstrap.service_container.LogCleanupTask", autospec=True)),
        ("newhigh_task", patch("view.web.bootstrap.service_container.NewHighTask", autospec=True)),
        ("osb", patch("view.web.bootstrap.strategy_factory.OneilSqueezeBreakoutStrategy", autospec=True)),
        ("pp", patch("view.web.bootstrap.strategy_factory.OneilPocketPivotStrategy", autospec=True)),
        ("htf", patch("view.web.bootstrap.strategy_factory.HighTightFlagStrategy", autospec=True)),
        ("cm", patch("view.web.bootstrap.repository_bootstrap.CacheStore", autospec=True)),
        ("logger", patch("view.web.web_app_initializer.Logger", autospec=True)),
        ("tn", patch("view.web.bootstrap.config_bootstrap.TelegramNotifier", autospec=True)),
        ("tr", patch("view.web.bootstrap.config_bootstrap.TelegramReporter", autospec=True)),
        ("strategy_log_report_task", patch("view.web.bootstrap.service_container.StrategyLogReportTask", autospec=True)),
        ("strategy_log_report_service", patch("view.web.bootstrap.service_container.StrategyLogReportService", autospec=True)),
        ("newhigh_coverage_service", patch("view.web.bootstrap.backtest_task_bootstrap.NewHighStrategyCoverageBacktestService", autospec=True)),
        ("newhigh_coverage_task", patch("view.web.bootstrap.backtest_task_bootstrap.NewHighStrategyCoverageBacktestTask", autospec=True)),
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


def test_runtime_mode_defaults_to_all(mock_deps):
    """runtime_mode 미지정 시 default = ALL (현행 동작 회귀 방지)."""
    from view.web.bootstrap.runtime_mode import RuntimeMode

    ctx = WebAppContext(None)
    assert ctx.runtime_mode is RuntimeMode.ALL


def test_runtime_mode_injection(mock_deps):
    """runtime_mode 주입 시 ctx 에 보존된다."""
    from view.web.bootstrap.runtime_mode import RuntimeMode

    ctx = WebAppContext(None, runtime_mode=RuntimeMode.WEB)
    assert ctx.runtime_mode is RuntimeMode.WEB

    ctx2 = WebAppContext(None, runtime_mode=RuntimeMode.WEB | RuntimeMode.TRADING)
    assert ctx2.runtime_mode == (RuntimeMode.WEB | RuntimeMode.TRADING)


def test_initialize_scheduler_noop_when_trading_disabled(mock_deps):
    """runtime_mode=WEB 일 때 initialize_scheduler() 가 StrategyScheduler 를 생성하지 않는다."""
    from view.web.bootstrap.runtime_mode import RuntimeMode

    ctx = WebAppContext(None, runtime_mode=RuntimeMode.WEB)
    ctx.background_scheduler = MagicMock()
    ctx.initialize_scheduler()

    # StrategyScheduler 는 호출되지 않아야 한다.
    mock_deps["sched"].assert_not_called()
    assert ctx.scheduler is None


def test_load_config_and_env(mock_deps):
    """설정 로드 및 환경 객체 초기화 검증"""
    ctx = WebAppContext(None)
    ctx.load_config_and_env()
    
    mock_deps["load_configs"].assert_called_once()
    mock_deps["env"].assert_called_once()
    mock_deps["tm"].assert_called_once()
    assert ctx.virtual_repo.tm is mock_deps["tm"].return_value
    assert ctx.virtual_trade_service.tm is mock_deps["tm"].return_value
    assert ctx.backtest_journal_repository is mock_deps["backtest_journal_repo"].return_value
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
    _, oes_kwargs = mock_deps["oes"].call_args
    assert oes_kwargs.get("virtual_trade_service") is ctx.virtual_trade_service
    mock_deps["risk_gate"].assert_called_once()
    assert oes_kwargs.get("risk_gate_service") is mock_deps["risk_gate"].return_value
    mock_deps["order_policy"].assert_called_once()
    assert oes_kwargs.get("order_policy_service") is mock_deps["order_policy"].return_value
    mock_deps["ous"].assert_called()
    _, report_kwargs = mock_deps["strategy_log_report_service"].call_args
    assert report_kwargs.get("stock_code_repo") is mock_deps["scm"].return_value
    assert report_kwargs.get("virtual_trade_service") is ctx.virtual_trade_service
    assert report_kwargs.get("backtest_journal_provider") is ctx.backtest_journal_repository.load_records_for_date
    env_instance.get_real_access_token.assert_awaited_once()

@pytest.mark.asyncio
async def test_initialize_services_real_mode_skips_real_token_prefetch(mock_deps):
    """실전투자 모드에서는 실전 토큰 사전 발급을 추가 호출하지 않는다."""
    ctx = WebAppContext(None)
    ctx.load_config_and_env()

    env_instance = mock_deps["env"].return_value
    env_instance.get_access_token = AsyncMock(return_value=True)
    env_instance.get_real_access_token = AsyncMock(return_value="fake_real_token")

    res = await ctx.initialize_services(is_paper_trading=False)

    assert res is True
    env_instance.set_trading_mode.assert_called_with(False)
    env_instance.get_real_access_token.assert_not_awaited()

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
    assert scheduler.register.call_count >= 7

    # 전략 초기화 검증
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
    ctx.streaming_service.wait_program_trading_ack = AsyncMock(return_value=True)
    ctx.streaming_service.subscribe_unified_price = AsyncMock(return_value=True)
    ctx.streaming_service.wait_unified_price_ack = AsyncMock(return_value=True)
    ctx.streaming_service.unsubscribe_program_trading = AsyncMock()
    ctx.streaming_service.unsubscribe_unified_price = AsyncMock()

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
    ctx.streaming_service.subscribe_unified_price.assert_awaited_with("005930")
    ctx.streaming_stock_repo.mark_desired.assert_called_with(
        "005930",
        StreamingType.PROGRAM_TRADING,
        source="manual",
    )
    ctx.streaming_stock_repo.mark_active.assert_any_call("005930", StreamingType.PROGRAM_TRADING)
    ctx.streaming_stock_repo.mark_active.assert_any_call("005930", StreamingType.UNIFIED_PRICE)

    # 2. 중복 구독 시도 (API 호출 없어야 함)
    ctx.streaming_stock_repo.get_desired.side_effect = (
        lambda stream_type: {"005930"} if stream_type == StreamingType.PROGRAM_TRADING else set()
    )
    await ctx.start_program_trading("005930")
    assert ctx.streaming_service.subscribe_program_trading.call_count == 1

    # 3. 구독 해지
    await ctx.stop_program_trading("005930")
    ctx.streaming_service.unsubscribe_program_trading.assert_awaited_with("005930")
    ctx.streaming_stock_repo.unmark_desired.assert_called_with("005930", StreamingType.PROGRAM_TRADING)
    ctx.streaming_stock_repo.mark_inactive.assert_any_call("005930", StreamingType.PROGRAM_TRADING)
    ctx.streaming_stock_repo.mark_inactive.assert_any_call("005930", StreamingType.UNIFIED_PRICE)

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


def test_start_background_tasks_schedules_price_subscription_init(mock_deps):
    """가격 구독 초기화는 background start 시점에 task로 시작하고 context에 보관한다."""
    ctx = WebAppContext(None)
    ctx.background_scheduler = None
    ctx.price_subscription_service = MagicMock()
    ctx._initialize_price_subscriptions = AsyncMock()

    created = MagicMock()

    def fake_create_task(coro):
        coro.close()
        return created

    with patch("view.web.web_app_initializer.asyncio.create_task", side_effect=fake_create_task) as mock_create_task:
        ctx.start_background_tasks()

    mock_create_task.assert_called_once()
    assert ctx._price_subscription_init_task is created


def test_start_background_tasks_does_not_duplicate_price_subscription_init(mock_deps):
    """이미 초기 가격 구독 task가 실행 중이면 중복 생성하지 않는다."""
    ctx = WebAppContext(None)
    ctx.background_scheduler = None
    ctx.price_subscription_service = MagicMock()
    running = MagicMock()
    running.done.return_value = False
    ctx._price_subscription_init_task = running

    with patch("view.web.web_app_initializer.asyncio.create_task") as mock_create_task:
        ctx.start_background_tasks()

    mock_create_task.assert_not_called()


def test_web_realtime_callback(mock_deps):
    """웹소켓 콜백 처리 테스트"""
    ctx = WebAppContext(None)
    ctx.streaming_service = MagicMock()
    ctx.streaming_service.dispatch_realtime_message = MagicMock()
    ctx.streaming_service.get_cached_realtime_price = MagicMock(return_value=None)
    ctx._schedule_rest_price_refresh = MagicMock()
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
    ctx._schedule_rest_price_refresh.assert_not_called()

    # 3. 프로그램 매매 데이터 (가격 캐시 없음 -> REST 보강 예약)
    ctx.streaming_service.get_cached_realtime_price.return_value = None
    data_pt_missing = {
        "type": "realtime_program_trading",
        "data": {"유가증권단축종목코드": "000660"}
    }
    with patch.object(ctx, "_log_streaming_missing_reason") as mock_missing_reason:
        ctx._web_realtime_callback(data_pt_missing)

    mock_missing_reason.assert_called_once_with("000660")
    ctx._schedule_rest_price_refresh.assert_called_once_with("000660")


@pytest.mark.asyncio
async def test_refresh_price_from_rest_caches_snapshot(mock_deps):
    """REST 현재가 조회 성공 시 PT 보강용 가격 캐시를 채운다."""
    ctx = WebAppContext(None)
    ctx.stock_query_service = MagicMock()
    ctx.stock_query_service.get_current_price = AsyncMock(return_value=MagicMock(
        rt_cd="0",
        data={
            "output": {
                "stck_prpr": "70000",
                "prdy_vrss": "1000",
                "prdy_ctrt": "1.45",
                "prdy_vrss_sign": "2",
                "acml_vol": "12345",
            }
        },
    ))
    ctx.price_stream_service = MagicMock()
    ctx.streaming_event_logger = MagicMock()

    await ctx._refresh_price_from_rest("005930")

    ctx.stock_query_service.get_current_price.assert_awaited_once_with(
        "005930",
        caller="WebAppContext",
        count_stats=False,
        force_fresh=True,
    )
    ctx.price_stream_service.cache_price_snapshot.assert_called_once_with(
        "005930",
        price="70000",
        change="1000",
        rate="1.45",
        sign="2",
        volume="12345",
    )
    ctx.streaming_event_logger.log_missing_reason.assert_not_called()


@pytest.mark.asyncio
async def test_refresh_price_from_rest_logs_rest_failed(mock_deps):
    """REST 현재가 조회 실패 시 rest_failed 원인을 남긴다."""
    ctx = WebAppContext(None)
    ctx.stock_query_service = MagicMock()
    ctx.stock_query_service.get_current_price = AsyncMock(return_value=MagicMock(
        rt_cd="1",
        data=None,
    ))
    ctx.price_stream_service = MagicMock()
    ctx.streaming_event_logger = MagicMock()

    await ctx._refresh_price_from_rest("005930")

    ctx.streaming_event_logger.log_missing_reason.assert_called_once_with("005930", "rest_failed")

@pytest.mark.asyncio
async def test_stop_all_program_trading(mock_deps):
    """모든 프로그램 매매 구독 해지 테스트 — streaming_stock_repo SSOT 기반"""
    from repositories.streaming_stock_repo import StreamingType
    ctx = WebAppContext(None)

    ctx.streaming_service = MagicMock()
    ctx.streaming_service.unsubscribe_program_trading = AsyncMock()
    ctx.streaming_service.unsubscribe_unified_price = AsyncMock()

    ctx.streaming_stock_repo = MagicMock()
    ctx.streaming_stock_repo.get_desired = MagicMock(
        side_effect=lambda stream_type: {"005930", "000660"}
        if stream_type == StreamingType.PROGRAM_TRADING else set()
    )
    ctx.streaming_stock_repo.unmark_desired = AsyncMock()
    ctx.streaming_stock_repo.mark_inactive = AsyncMock()

    await ctx.stop_all_program_trading()

    assert ctx.streaming_service.unsubscribe_program_trading.call_count == 2
    assert ctx.streaming_service.unsubscribe_unified_price.call_count == 2
    assert ctx.streaming_stock_repo.unmark_desired.call_count == 2
    assert ctx.streaming_stock_repo.mark_inactive.call_count == 4

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


@pytest.mark.asyncio
async def test_start_background_tasks_manual_repeat_is_guarded(mock_deps):
    """웹 start hook/수동 start 연타가 들어와도 실제 task start는 한 번만 수행된다."""
    from scheduler.background_scheduler import BackgroundScheduler
    from interfaces.schedulable_task import TaskPriority, TaskState

    ctx = WebAppContext(None)
    ctx.streaming_service = None
    scheduler = BackgroundScheduler(logger=MagicMock())
    started = asyncio.Event()
    release = asyncio.Event()
    task = MagicMock()
    task.task_name = "web_manual_start"
    task.priority = TaskPriority.LOW
    task.state = TaskState.IDLE

    async def slow_start():
        started.set()
        await release.wait()

    task.start = AsyncMock(side_effect=slow_start)
    task.stop = AsyncMock()
    task.suspend = AsyncMock()
    task.resume = AsyncMock()
    scheduler.register(task)
    ctx.background_scheduler = scheduler

    created = []
    original_create_task = asyncio.create_task

    def track_create_task(coro):
        created_task = original_create_task(coro)
        created.append(created_task)
        return created_task

    with patch("view.web.web_app_initializer.asyncio.create_task", side_effect=track_create_task):
        ctx.start_background_tasks()
        await asyncio.wait_for(started.wait(), timeout=1)
        ctx.start_background_tasks()
        release.set()
        await asyncio.gather(*created)

        ctx.start_background_tasks()
        await created[-1]

    task.start.assert_awaited_once()
    scheduler._logger.warning.assert_any_call("[BackgroundScheduler] start_all 중복 호출 무시")
    scheduler._logger.warning.assert_any_call("[BackgroundScheduler] 이미 시작됨 — start_all 호출 무시")

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


def test_get_cache_stats_delegates_and_handles_missing_repo(mock_deps):
    """StockRepository가 있으면 cache stats를 위임하고, 없으면 빈 dict를 반환."""
    ctx = WebAppContext(None)

    ctx.stock_repository = None
    assert ctx.get_cache_stats(expand=True, latest_trading_date="20260102") == {}

    ctx.stock_repository = MagicMock()
    ctx.stock_repository.get_cache_stats.return_value = {"memory": {"entries": 3}}

    result = ctx.get_cache_stats(expand=True, latest_trading_date="20260102")

    assert result == {"memory": {"entries": 3}}
    ctx.stock_repository.get_cache_stats.assert_called_once_with(
        expand=True,
        latest_trading_date="20260102",
    )


def test_emit_missing_reason_throttles_duplicate_logs(mock_deps):
    """같은 code/reason 로그는 60초 이내에 한 번만 기록한다."""
    ctx = WebAppContext(None)
    ctx.streaming_event_logger = MagicMock()

    with patch("view.web.web_app_initializer.time.monotonic", side_effect=[100.0, 120.0, 161.0]):
        ctx._emit_missing_reason("005930", "rest_failed")
        ctx._emit_missing_reason("005930", "rest_failed")
        ctx._emit_missing_reason("005930", "rest_failed")

    assert ctx.streaming_event_logger.log_missing_reason.call_count == 2
    ctx.streaming_event_logger.log_missing_reason.assert_called_with("005930", "rest_failed")


def test_log_streaming_missing_reason_branches(mock_deps):
    """구독 상태와 grace window에 따라 missing reason 기록 여부를 검증."""
    ctx = WebAppContext(None)
    ctx.streaming_event_logger = MagicMock()
    ctx.streaming_service = MagicMock()
    ctx.price_stream_service = MagicMock()
    ctx._emit_missing_reason = MagicMock()

    ctx.streaming_service.is_subscribed_realtime_price.return_value = False
    ctx._log_streaming_missing_reason("005930")
    ctx._emit_missing_reason.assert_called_once_with("005930", "not_subscribed")

    ctx._emit_missing_reason.reset_mock()
    ctx.streaming_service.is_subscribed_realtime_price.return_value = True
    ctx.price_stream_service.get_subscription_age.return_value = 1
    with patch.object(mock_deps["watchdog_task_in_main"], "PRICE_SUBSCRIPTION_GRACE_SEC", 5, create=True):
        ctx._log_streaming_missing_reason("005930")
    ctx._emit_missing_reason.assert_not_called()

    ctx.price_stream_service.get_subscription_age.return_value = 10
    with patch.object(mock_deps["watchdog_task_in_main"], "PRICE_SUBSCRIPTION_GRACE_SEC", 5, create=True):
        ctx._log_streaming_missing_reason("005930")
    ctx._emit_missing_reason.assert_called_once_with("005930", "subscribed_no_tick")


def test_schedule_rest_price_refresh_creates_task_and_cleans_up(mock_deps):
    """REST 보강 예약은 create_task로 만들고 done callback에서 pending map을 정리한다."""
    ctx = WebAppContext(None)
    ctx.stock_query_service = MagicMock()
    ctx.price_stream_service = MagicMock()
    ctx.streaming_service = MagicMock()
    ctx.streaming_service.is_subscribed_realtime_price.return_value = True
    ctx._refresh_price_from_rest = MagicMock(return_value="refresh-coro")
    fake_task = MagicMock()
    fake_task.done.return_value = False

    with patch("view.web.web_app_initializer.time.monotonic", return_value=100.0), \
         patch("view.web.web_app_initializer.asyncio.create_task", return_value=fake_task) as mock_create_task:
        ctx._schedule_rest_price_refresh("005930")

    ctx._refresh_price_from_rest.assert_called_once_with("005930")
    mock_create_task.assert_called_once_with("refresh-coro")
    assert ctx._pending_rest_price_refresh_tasks["005930"] is fake_task

    cleanup = fake_task.add_done_callback.call_args.args[0]
    cleanup(fake_task)
    assert "005930" not in ctx._pending_rest_price_refresh_tasks


def test_schedule_rest_price_refresh_skips_unready_and_cooldown(mock_deps):
    """필수 의존성 누락, 미구독, cooldown 상태에서는 REST 보강 task를 만들지 않는다."""
    ctx = WebAppContext(None)
    ctx._refresh_price_from_rest = MagicMock(return_value="refresh-coro")

    ctx._schedule_rest_price_refresh("")
    ctx._refresh_price_from_rest.assert_not_called()

    ctx.stock_query_service = MagicMock()
    ctx.price_stream_service = MagicMock()
    ctx.streaming_service = MagicMock()
    ctx.streaming_service.is_subscribed_realtime_price.return_value = False
    ctx._schedule_rest_price_refresh("005930")
    ctx._refresh_price_from_rest.assert_not_called()

    ctx.streaming_service.is_subscribed_realtime_price.return_value = True
    ctx._last_rest_price_refresh_ts["005930"] = 95.0
    with patch("view.web.web_app_initializer.time.monotonic", return_value=100.0):
        ctx._schedule_rest_price_refresh("005930")
    ctx._refresh_price_from_rest.assert_not_called()


@pytest.mark.asyncio
async def test_refresh_price_from_rest_exception_logs_rest_failed(mock_deps):
    """REST 조회가 예외를 발생하면 warning과 rest_failed reason을 남긴다."""
    ctx = WebAppContext(None)
    ctx.stock_query_service = MagicMock()
    ctx.stock_query_service.get_current_price = AsyncMock(side_effect=RuntimeError("boom"))
    ctx.logger = MagicMock()
    ctx._emit_missing_reason = MagicMock()

    await ctx._refresh_price_from_rest("005930")

    ctx.logger.warning.assert_called_once()
    ctx._emit_missing_reason.assert_called_once_with("005930", "rest_failed")


@pytest.mark.asyncio
async def test_start_program_trading_reconnect_existing_desired(mock_deps):
    """desired 종목의 수신 task가 죽었으면 watchdog reconnect 성공을 성공으로 간주."""
    from repositories.streaming_stock_repo import StreamingType
    ctx = WebAppContext(None)
    ctx.logger = MagicMock()
    ctx.broker = MagicMock()
    ctx.broker.is_websocket_receive_alive.return_value = False
    ctx.websocket_watchdog_task = MagicMock()
    ctx.websocket_watchdog_task.force_reconnect_program_trading = AsyncMock()
    ctx.streaming_stock_repo = MagicMock()
    ctx.streaming_stock_repo.get_desired.return_value = {"005930"}
    ctx.streaming_service = MagicMock()

    result = await ctx.start_program_trading("005930")

    assert result is True
    ctx.websocket_watchdog_task.force_reconnect_program_trading.assert_awaited_once()
    ctx.streaming_service.connect_websocket.assert_not_called()
    ctx.streaming_stock_repo.get_desired.assert_called_with(StreamingType.PROGRAM_TRADING)


@pytest.mark.asyncio
async def test_start_program_trading_subscription_failure_cleans_partial_success(mock_deps):
    """PT 구독만 성공하고 가격 구독이 실패하면 PT 구독을 해제한다."""
    ctx = WebAppContext(None)
    ctx.pm = MagicMock()
    ctx.pm.start_timer.return_value = 0.0
    ctx.logger = MagicMock()
    ctx.streaming_stock_repo = MagicMock()
    ctx.streaming_stock_repo.get_desired.return_value = set()
    ctx.streaming_service = MagicMock()
    ctx.streaming_service.connect_websocket = AsyncMock(return_value=True)
    ctx.streaming_service.subscribe_program_trading = AsyncMock(return_value=True)
    ctx.streaming_service.wait_program_trading_ack = AsyncMock(return_value=True)
    ctx.streaming_service.subscribe_unified_price = AsyncMock(return_value=False)
    ctx.streaming_service.unsubscribe_program_trading = AsyncMock()
    ctx.streaming_service.unsubscribe_unified_price = AsyncMock()

    result = await ctx.start_program_trading("005930")

    assert result is False
    ctx.streaming_service.unsubscribe_program_trading.assert_awaited_once_with("005930")
    ctx.streaming_service.unsubscribe_unified_price.assert_not_awaited()


@pytest.mark.asyncio
async def test_shutdown_cancels_pending_refresh_tasks_and_stops_broker(mock_deps):
    """shutdown은 예약된 REST 보강 task를 취소하고 broker.stop까지 위임한다."""
    ctx = WebAppContext(None)
    pending = MagicMock()
    pending.cancel = MagicMock()
    ctx._pending_rest_price_refresh_tasks = {"005930": pending}
    ctx.background_scheduler = None
    ctx.broker = MagicMock()
    ctx.broker.stop = AsyncMock()

    with patch("view.web.web_app_initializer.asyncio.gather", new=AsyncMock()) as mock_gather:
        await ctx.shutdown()

    pending.cancel.assert_called_once()
    mock_gather.assert_awaited_once_with(pending, return_exceptions=True)
    assert ctx._pending_rest_price_refresh_tasks == {}
    ctx.broker.stop.assert_awaited_once()


@pytest.mark.asyncio
async def test_shutdown_cancels_price_subscription_init_task(mock_deps):
    """shutdown은 초기 가격 구독 task도 취소해 pending task를 남기지 않는다."""
    ctx = WebAppContext(None)
    init_task = MagicMock()
    init_task.done.return_value = False
    init_task.cancel = MagicMock()
    ctx._price_subscription_init_task = init_task
    ctx.background_scheduler = None
    ctx.broker = None

    with patch("view.web.web_app_initializer.asyncio.gather", new=AsyncMock()) as mock_gather:
        await ctx.shutdown()

    init_task.cancel.assert_called_once()
    mock_gather.assert_awaited_once_with(init_task, return_exceptions=True)
    assert ctx._price_subscription_init_task is None


@pytest.mark.asyncio
async def test_initialize_services_logs_bootstrap_service_failure(mock_deps):
    """서비스 부트스트랩 예외는 critical 로그 후 False를 반환한다."""
    ctx = WebAppContext(None)
    ctx.env = MagicMock()
    ctx._bootstrap_broker = AsyncMock(return_value=True)
    ctx._bootstrap_services = MagicMock(side_effect=RuntimeError("service boom"))
    ctx._bootstrap_schedulers = MagicMock()

    result = await ctx.initialize_services(is_paper_trading=True)

    assert result is False
    ctx.logger.critical.assert_called_once()
    ctx._bootstrap_schedulers.assert_not_called()


@pytest.mark.asyncio
async def test_bootstrap_broker_exception_returns_false(mock_deps):
    """브로커 초기화 중 예외가 나면 False를 반환한다."""
    ctx = WebAppContext(None)
    ctx.env = MagicMock()
    ctx.env.get_access_token = AsyncMock(side_effect=RuntimeError("token boom"))

    result = await ctx._bootstrap_broker(is_paper_trading=True)

    assert result is False
    ctx.logger.critical.assert_called_once()


def test_load_config_and_env_uses_legacy_dict_method(mock_deps):
    """Pydantic v1 스타일 dict() 설정 객체도 dict로 변환한다."""
    class LegacyConfig:
        def __init__(self):
            self.called = False

        def dict(self):
            self.called = True
            return {
                "market_open_time": "08:30",
                "market_close_time": "15:00",
                "market_timezone": "Asia/Seoul",
                "notifications": {"telegram": {"enabled": False}},
            }

    config_obj = LegacyConfig()
    mock_deps["load_configs"].return_value = config_obj

    ctx = WebAppContext(None)
    ctx.load_config_and_env()

    assert config_obj.called is True
    mock_deps["env"].assert_called_once()


def test_web_realtime_callback_injects_scalar_price_and_skips_without_streaming(mock_deps):
    """가격 캐시가 scalar면 price만 주입하고, streaming_service가 없으면 dispatch를 생략한다."""
    ctx = WebAppContext(None)
    ctx.streaming_service = MagicMock()
    ctx.streaming_service.get_cached_realtime_price.return_value = "70100"
    data = {"type": "realtime_program_trading", "data": {"유가증권단축종목코드": "005930"}}

    ctx._web_realtime_callback(data)

    assert data["data"]["price"] == "70100"
    ctx.streaming_service.dispatch_realtime_message.assert_called_once_with(data)

    ctx.streaming_service = None
    ctx._web_realtime_callback({"type": "realtime_program_trading", "data": {}})


def test_emit_and_log_missing_reason_no_logger_or_price_service(mock_deps):
    """streaming logger/service 누락 및 price stream service 누락 분기를 확인한다."""
    ctx = WebAppContext(None)
    ctx.streaming_event_logger = None
    ctx._emit_missing_reason("005930", "rest_failed")

    ctx.streaming_event_logger = MagicMock()
    ctx.streaming_service = None
    ctx._log_streaming_missing_reason("005930")
    ctx.streaming_event_logger.log_missing_reason.assert_not_called()

    ctx.streaming_service = MagicMock()
    ctx.streaming_service.is_subscribed_realtime_price.return_value = True
    ctx.price_stream_service = None
    ctx._emit_missing_reason = MagicMock()
    ctx._log_streaming_missing_reason("005930")
    ctx._emit_missing_reason.assert_called_once_with("005930", "subscribed_no_tick")


def test_schedule_rest_price_refresh_skips_existing_running_task(mock_deps):
    """이미 실행 중인 REST 보강 task가 있으면 새 task를 만들지 않는다."""
    ctx = WebAppContext(None)
    ctx.stock_query_service = MagicMock()
    ctx.price_stream_service = MagicMock()
    ctx.streaming_service = MagicMock()
    ctx.streaming_service.is_subscribed_realtime_price.return_value = True
    ctx._refresh_price_from_rest = MagicMock()
    existing = MagicMock()
    existing.done.return_value = False
    ctx._pending_rest_price_refresh_tasks["005930"] = existing

    ctx._schedule_rest_price_refresh("005930")

    ctx._refresh_price_from_rest.assert_not_called()


@pytest.mark.asyncio
async def test_refresh_price_from_rest_quality_and_invalid_output_edges(mock_deps):
    """REST 보강은 품질 실패, output 누락, 가격 누락을 각각 reason으로 기록한다."""
    ctx = WebAppContext(None)
    ctx.stock_query_service = MagicMock()
    ctx.stock_query_service.get_current_price = AsyncMock()
    ctx.price_stream_service = MagicMock()
    ctx._emit_missing_reason = MagicMock()
    ctx.data_quality_service = MagicMock()
    ctx.data_quality_service.validate_api_response.return_value = SimpleNamespace(ok=False, reason="zero_price")

    ctx.stock_query_service.get_current_price.return_value = SimpleNamespace(rt_cd="0", data={"output": {}})
    await ctx._refresh_price_from_rest("005930")
    ctx._emit_missing_reason.assert_called_with("005930", "zero_price")

    ctx._emit_missing_reason.reset_mock()
    ctx.data_quality_service = None
    ctx.stock_query_service.get_current_price.return_value = SimpleNamespace(rt_cd="0", data={})
    await ctx._refresh_price_from_rest("005930")
    ctx._emit_missing_reason.assert_called_with("005930", "rest_failed")

    ctx._emit_missing_reason.reset_mock()
    ctx.stock_query_service.get_current_price.return_value = SimpleNamespace(rt_cd="0", data={"output": {}})
    await ctx._refresh_price_from_rest("005930")
    ctx._emit_missing_reason.assert_called_with("005930", "rest_invalid")

    ctx._emit_missing_reason.reset_mock()
    ctx.stock_query_service.get_current_price.return_value = SimpleNamespace(rt_cd="0", data={"output": {"stck_prpr": ""}})
    await ctx._refresh_price_from_rest("005930")
    ctx._emit_missing_reason.assert_called_with("005930", "rest_invalid")


@pytest.mark.asyncio
async def test_start_program_trading_connection_failure_price_only_cleanup_and_exception(mock_deps):
    """프로그램매매 구독 시작의 연결 실패, 가격 구독만 성공, 예외 경로를 확인한다."""
    ctx = WebAppContext(None)
    ctx.pm = MagicMock()
    ctx.pm.start_timer.return_value = 0.0
    ctx.logger = MagicMock()
    ctx.streaming_stock_repo = MagicMock()
    ctx.streaming_stock_repo.get_desired.return_value = set()
    ctx.streaming_service = MagicMock()
    ctx.streaming_service.connect_websocket = AsyncMock(return_value=False)

    assert await ctx.start_program_trading("005930") is False
    ctx.logger.warning.assert_called_with("프로그램매매 구독 실패 (WebSocket 연결 불가): 005930")

    ctx.streaming_service.connect_websocket = AsyncMock(return_value=True)
    ctx.streaming_service.subscribe_program_trading = AsyncMock(return_value=False)
    ctx.streaming_service.subscribe_unified_price = AsyncMock(return_value=True)
    ctx.streaming_service.wait_unified_price_ack = AsyncMock(return_value=True)
    ctx.streaming_service.unsubscribe_program_trading = AsyncMock()
    ctx.streaming_service.unsubscribe_unified_price = AsyncMock()

    assert await ctx.start_program_trading("005930") is False
    ctx.streaming_service.unsubscribe_unified_price.assert_awaited_once_with("005930")

    ctx.streaming_service.connect_websocket = AsyncMock(side_effect=RuntimeError("boom"))
    assert await ctx.start_program_trading("005930") is False
    ctx.logger.error.assert_called()


@pytest.mark.asyncio
async def test_start_program_trading_requires_program_ack_before_marking_active(mock_deps):
    """PT 요청 전송만 성공하고 ACK가 거부되면 active로 기록하지 않는다."""
    ctx = WebAppContext(None)
    ctx.pm = MagicMock()
    ctx.pm.start_timer.return_value = 0.0
    ctx.logger = MagicMock()
    ctx.streaming_stock_repo = MagicMock()
    ctx.streaming_stock_repo.get_desired.return_value = set()
    ctx.streaming_stock_repo.mark_desired = AsyncMock()
    ctx.streaming_stock_repo.mark_active = AsyncMock()
    ctx.streaming_service = MagicMock()
    ctx.streaming_service.connect_websocket = AsyncMock(return_value=True)
    ctx.streaming_service.subscribe_program_trading = AsyncMock(return_value=True)
    ctx.streaming_service.wait_program_trading_ack = AsyncMock(return_value=False)
    ctx.streaming_service.subscribe_unified_price = AsyncMock(return_value=True)
    ctx.streaming_service.wait_unified_price_ack = AsyncMock(return_value=True)
    ctx.streaming_service.unsubscribe_program_trading = AsyncMock()
    ctx.streaming_service.unsubscribe_unified_price = AsyncMock()

    assert await ctx.start_program_trading("005930") is False

    ctx.streaming_stock_repo.mark_active.assert_not_awaited()
    ctx.streaming_service.unsubscribe_program_trading.assert_awaited_once_with("005930")
    ctx.streaming_service.unsubscribe_unified_price.assert_awaited_once_with("005930")


@pytest.mark.asyncio
async def test_stop_program_trading_no_desired_and_stop_all_without_repo(mock_deps):
    """desired가 없거나 repo가 없으면 구독 해지 호출 없이 종료한다."""
    ctx = WebAppContext(None)
    ctx.streaming_service = MagicMock()
    ctx.streaming_service.unsubscribe_program_trading = AsyncMock()
    ctx.streaming_service.unsubscribe_unified_price = AsyncMock()
    ctx.streaming_stock_repo = MagicMock()
    ctx.streaming_stock_repo.get_desired.return_value = set()

    await ctx.stop_program_trading("005930")
    ctx.streaming_service.unsubscribe_program_trading.assert_not_awaited()

    ctx.streaming_stock_repo = None
    await ctx.stop_all_program_trading()
    ctx.streaming_service.unsubscribe_program_trading.assert_not_awaited()


# --- ensure_strategy_states_loaded() barrier 실패 정책 테스트 ---

def _make_cfg(name: str, load_state_fn=None):
    """scheduler._strategies 가 보는 cfg 형태."""
    strategy = MagicMock()
    strategy.name = name
    if load_state_fn is None:
        strategy.load_state = AsyncMock()
    else:
        strategy.load_state = load_state_fn
    cfg = SimpleNamespace(strategy=strategy)
    return cfg


def _make_ctx_with_strategies(strategies, is_paper_trading: bool):
    """scheduler 와 env.is_paper_trading 만 설정한 최소 WebAppContext."""
    ctx = WebAppContext(None)
    ctx.scheduler = MagicMock()
    ctx.scheduler._strategies = strategies
    ctx.env = MagicMock()
    ctx.env.is_paper_trading = is_paper_trading
    ctx.logger = MagicMock()
    return ctx


@pytest.mark.asyncio
async def test_ensure_strategy_states_loaded_no_scheduler_noop(mock_deps):
    """scheduler=None 이면 조용히 통과."""
    ctx = WebAppContext(None)
    ctx.scheduler = None
    await ctx.ensure_strategy_states_loaded()  # 예외 없음


@pytest.mark.asyncio
async def test_ensure_strategy_states_loaded_all_success_paper(mock_deps):
    """paper 모드에서 모든 전략 load 성공 → 정상 진행."""
    cfgs = [_make_cfg("StratA"), _make_cfg("StratB")]
    ctx = _make_ctx_with_strategies(cfgs, is_paper_trading=True)

    await ctx.ensure_strategy_states_loaded()

    cfgs[0].strategy.load_state.assert_awaited_once()
    cfgs[1].strategy.load_state.assert_awaited_once()


@pytest.mark.asyncio
async def test_ensure_strategy_states_loaded_all_success_real(mock_deps):
    """real 모드에서 모든 전략 load 성공 → 정상 진행 (raise 없음)."""
    cfgs = [_make_cfg("StratA"), _make_cfg("StratB")]
    ctx = _make_ctx_with_strategies(cfgs, is_paper_trading=False)

    await ctx.ensure_strategy_states_loaded()  # raise 없어야 함

    cfgs[0].strategy.load_state.assert_awaited_once()
    cfgs[1].strategy.load_state.assert_awaited_once()


@pytest.mark.asyncio
async def test_ensure_strategy_states_loaded_skips_strategies_without_load_state(mock_deps):
    """load_state 메서드가 없는 전략은 paper/real 모두에서 skip."""
    cfg_with = _make_cfg("HasLoad")
    cfg_without = SimpleNamespace(strategy=SimpleNamespace(name="NoLoad"))
    ctx = _make_ctx_with_strategies([cfg_with, cfg_without], is_paper_trading=False)

    await ctx.ensure_strategy_states_loaded()  # raise 없어야 함

    cfg_with.strategy.load_state.assert_awaited_once()


@pytest.mark.asyncio
async def test_ensure_strategy_states_loaded_paper_fails_open_on_load_error(mock_deps):
    """paper 모드: 일부 전략 load 실패해도 raise 없이 계속 진행 + error log."""
    failing = AsyncMock(side_effect=RuntimeError("load boom"))
    cfg_ok = _make_cfg("StratOK")
    cfg_fail = _make_cfg("StratFail", load_state_fn=failing)
    ctx = _make_ctx_with_strategies([cfg_ok, cfg_fail], is_paper_trading=True)

    await ctx.ensure_strategy_states_loaded()  # raise 없어야 함

    cfg_ok.strategy.load_state.assert_awaited_once()
    failing.assert_awaited_once()
    ctx.logger.error.assert_called()


@pytest.mark.asyncio
async def test_ensure_strategy_states_loaded_real_fails_close_on_load_error(mock_deps):
    """real 모드: 한 전략이라도 load 실패하면 RuntimeError raise (fail-close)."""
    failing = AsyncMock(side_effect=RuntimeError("load boom"))
    cfg_ok = _make_cfg("StratOK")
    cfg_fail = _make_cfg("StratFail", load_state_fn=failing)
    ctx = _make_ctx_with_strategies([cfg_ok, cfg_fail], is_paper_trading=False)

    with pytest.raises(RuntimeError) as exc_info:
        await ctx.ensure_strategy_states_loaded()

    assert "StratFail" in str(exc_info.value)
    failing.assert_awaited_once()


@pytest.mark.asyncio
async def test_ensure_strategy_states_loaded_real_multiple_failures_in_error(mock_deps):
    """real 모드 + 다중 실패: 모든 실패 전략 이름이 RuntimeError 메시지에 포함."""
    cfg_fail_a = _make_cfg("StratA", load_state_fn=AsyncMock(side_effect=RuntimeError("boom A")))
    cfg_fail_b = _make_cfg("StratB", load_state_fn=AsyncMock(side_effect=ValueError("boom B")))
    ctx = _make_ctx_with_strategies([cfg_fail_a, cfg_fail_b], is_paper_trading=False)

    with pytest.raises(RuntimeError) as exc_info:
        await ctx.ensure_strategy_states_loaded()

    msg = str(exc_info.value)
    assert "StratA" in msg
    assert "StratB" in msg


@pytest.mark.asyncio
async def test_ensure_strategy_states_loaded_no_env_defaults_to_fail_open(mock_deps):
    """env=None 이면 모드 결정 불가 → 보수적으로 fail-OPEN (paper 동작)."""
    failing = AsyncMock(side_effect=RuntimeError("load boom"))
    cfg_fail = _make_cfg("StratFail", load_state_fn=failing)
    ctx = WebAppContext(None)
    ctx.scheduler = MagicMock()
    ctx.scheduler._strategies = [cfg_fail]
    ctx.env = None  # 환경 정보 없음
    ctx.logger = MagicMock()

    await ctx.ensure_strategy_states_loaded()  # raise 없어야 함
    failing.assert_awaited_once()
