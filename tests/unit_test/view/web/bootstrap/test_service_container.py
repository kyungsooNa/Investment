"""ServiceContainer 단위 테스트.

`WebAppContext._bootstrap_services()` 본문에서 추출된 ServiceContainer가
컨텍스트에 핵심 서비스(StockQueryService, OrderExecutionService 등)와
태스크(RankingTask, WebSocketWatchdogTask 등)를 동일하게 주입하는지
스모크 테스트로 검증한다. 상세 검증은 기존
`tests/unit_test/view/web/test_web_app_initializer.py` 의
`test_initialize_services_*` 가 회귀 가드 역할을 한다.
"""
import contextlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from view.web.bootstrap.runtime_mode import RuntimeMode


SERVICE_CONTAINER_PATCH_NAMES = [
    "MinerviniStageService", "MinerviniUpdateTask",
    "StreamingService", "PriceStreamService", "StreamingStockRepo",
    "ExecutionStrengthRepository",
    "PriceSubscriptionService", "WebSocketWatchdogTask", "PreMarketHealthCheckTask",
    "DailyPriceCollectorTask", "OhlcvUpdateTask", "AccountSnapshotCache",
    "PositionSizingService", "RiskGateService", "ExecutionFlowService",
    "OrderPolicyService", "DeferredOrderQueue", "OrderExecutionService",
    "OneilUniverseService", "NaverFinanceScraperService",
    "ThemeClassificationCollectorService", "ThemeClassificationTask", "ThemeDailyLeaderReportTask",
    "ThemeIntradayLeaderAlertTask",
    "USMarketCalendarService",
    "BacktestMicrostructureCaptureService", "MicrostructureCaptureTask",
    "PremiumWatchlistGeneratorTask", "CacheWarmupTask", "LogCleanupTask",
    "NewHighTask", "NewHighService", "StrategyLogReportTask",
    "StrategyLogReportService", "NotificationQueueTask",
    "AfterMarketReconcileTask", "OpeningPositionReconcileTask",
    "OpeningPositionReconcileService",
]

REPOSITORY_BOOTSTRAP_PATCH_NAMES = [
    "CacheStore", "StockRepository", "PerformanceProfiler",
]

MARKET_DATA_BOOTSTRAP_PATCH_NAMES = [
    "RSRatingRepository", "RSRatingService", "StockClassificationRepository",
    "ThemeLeaderService", "ThemeDailyLeaderService", "MarketDataService",
    "IndicatorService", "MessageBroker", "DlqManager", "WorkerPool",
    "TimeDispatcher", "DataQualityService",
]

QUERY_BOOTSTRAP_PATCH_NAMES = [
    "RankingTask", "MarketCapGapService", "MarketCapGapReportTask",
    "StockQueryService",
]

BACKTEST_BOOTSTRAP_PATCH_NAMES = [
    "PostMarketReplayAuditService", "PostMarketReplayAuditTask",
    "NewHighStrategyCoverageBacktestService", "NewHighStrategyCoverageBacktestTask",
]


@pytest.fixture
def patched_service_container_deps():
    """ServiceContainer 가 호출하는 모든 클래스를 mock 한다."""
    targets = [
        (name, patch(f"view.web.bootstrap.service_container.{name}", autospec=True))
        for name in SERVICE_CONTAINER_PATCH_NAMES
    ]
    targets.extend(
        (name, patch(f"view.web.bootstrap.backtest_task_bootstrap.{name}", autospec=True))
        for name in BACKTEST_BOOTSTRAP_PATCH_NAMES
    )
    targets.extend(
        (name, patch(f"view.web.bootstrap.repository_bootstrap.{name}", autospec=True))
        for name in REPOSITORY_BOOTSTRAP_PATCH_NAMES
    )
    targets.extend(
        (name, patch(f"view.web.bootstrap.market_data_bootstrap.{name}", autospec=True))
        for name in MARKET_DATA_BOOTSTRAP_PATCH_NAMES
    )
    targets.extend(
        (name, patch(f"view.web.bootstrap.query_bootstrap.{name}", autospec=True))
        for name in QUERY_BOOTSTRAP_PATCH_NAMES
    )
    with contextlib.ExitStack() as stack:
        mocks = {name: stack.enter_context(p) for name, p in targets}
        yield mocks


def _make_fake_context():
    ctx = SimpleNamespace()
    ctx.runtime_mode = RuntimeMode.ALL
    ctx.logger = MagicMock()
    ctx.logger.log_dir = "logs"
    ctx.full_config = {}
    ctx.env = MagicMock()
    ctx.env.is_paper_trading = True
    ctx.broker = MagicMock()
    ctx.market_clock = MagicMock()
    ctx._mcs = MagicMock()
    ctx.streaming_event_logger = MagicMock()
    ctx.stock_code_repository = MagicMock()
    ctx.virtual_repo = MagicMock()
    ctx.virtual_trade_service = MagicMock()
    ctx.backtest_journal_repository = MagicMock()
    ctx.notification_service = MagicMock()
    ctx.operator_alert_service = MagicMock()
    ctx.kill_switch_service = MagicMock()
    ctx.rejection_distribution_service = MagicMock()
    ctx.favorite_service = MagicMock()
    ctx.program_trading_stream_service = MagicMock()
    ctx.telegram_reporter = MagicMock()
    ctx.pm = None
    ctx._get_enabled_strategy_names_for_report = MagicMock(return_value=[])
    return ctx


def test_service_container_creates_core_services(patched_service_container_deps):
    """MarketDataService, IndicatorService, DataQualityService 가 ctx 에 주입된다."""
    from view.web.bootstrap.service_container import ServiceContainer

    ctx = _make_fake_context()
    ServiceContainer(ctx).run()

    assert ctx.market_data_service is patched_service_container_deps["MarketDataService"].return_value
    assert ctx.indicator_service is patched_service_container_deps["IndicatorService"].return_value
    assert ctx.data_quality_service is patched_service_container_deps["DataQualityService"].return_value


def test_service_container_creates_query_and_order_services(patched_service_container_deps):
    """StockQueryService, OrderExecutionService, RiskGateService 가 ctx 에 주입된다."""
    from view.web.bootstrap.service_container import ServiceContainer

    ctx = _make_fake_context()
    ServiceContainer(ctx).run()

    assert ctx.stock_query_service is patched_service_container_deps["StockQueryService"].return_value
    assert ctx.order_execution_service is patched_service_container_deps["OrderExecutionService"].return_value
    assert ctx.risk_gate_service is patched_service_container_deps["RiskGateService"].return_value


def test_service_container_creates_theme_leader_service(patched_service_container_deps):
    """ThemeLeaderService, ThemeDailyLeaderService 와 분류 저장소가 ctx 에 주입된다."""
    from view.web.bootstrap.service_container import ServiceContainer

    ctx = _make_fake_context()
    ServiceContainer(ctx).run()

    assert ctx.theme_classification_repository is patched_service_container_deps[
        "StockClassificationRepository"].return_value
    assert ctx.theme_leader_service is patched_service_container_deps["ThemeLeaderService"].return_value
    assert ctx.theme_daily_leader_service is patched_service_container_deps[
        "ThemeDailyLeaderService"].return_value


def test_service_container_passes_order_execution_retry_config(patched_service_container_deps):
    """config.order_execution 값이 OrderExecutionService submit retry 정책으로 주입된다."""
    from view.web.bootstrap.service_container import ServiceContainer

    ctx = _make_fake_context()
    ctx.full_config = {
        "order_execution": {
            "order_max_retries": 5,
            "order_retry_delay_sec": 1,
        },
        "execution_quality_report": None,
    }
    ServiceContainer(ctx).run()

    kwargs = patched_service_container_deps["OrderExecutionService"].call_args.kwargs
    assert kwargs["order_max_retries"] == 5
    assert kwargs["order_retry_delay_sec"] == 1


def test_service_container_injects_market_buy_reference_price_provider(patched_service_container_deps):
    """RiskGateService가 시장가 매수 기준가격 provider와 함께 인스턴스화된다."""
    from view.web.bootstrap.service_container import ServiceContainer

    ctx = _make_fake_context()
    ServiceContainer(ctx).run()

    kwargs = patched_service_container_deps["RiskGateService"].call_args.kwargs
    provider = kwargs.get("market_buy_reference_price_provider")
    assert provider is not None
    assert callable(provider)


def test_service_container_creates_streaming_chain(patched_service_container_deps):
    """StreamingService, PriceStreamService, PriceSubscriptionService 인스턴스가 생성된다.

    streaming_service.set_* / set_streaming_stock_repo 등의 wiring 은 WiringPhase 가 담당하므로
    여기서는 ServiceContainer 가 wiring 을 호출하지 않는다는 invariant 도 함께 검증한다.
    """
    from view.web.bootstrap.service_container import ServiceContainer

    ctx = _make_fake_context()
    ServiceContainer(ctx).run()

    assert ctx.streaming_service is patched_service_container_deps["StreamingService"].return_value
    assert ctx.price_stream_service is patched_service_container_deps["PriceStreamService"].return_value
    assert ctx.price_subscription_service is patched_service_container_deps["PriceSubscriptionService"].return_value
    ctx.streaming_service.set_price_stream_service.assert_not_called()
    ctx.streaming_service.set_streaming_stock_repo.assert_not_called()


def test_service_container_restores_pt_desired_from_snapshot_fallback(patched_service_container_deps):
    """부팅 시 저장 스냅샷의 PT 구독 종목을 StreamingStockRepo 복원 fallback으로 전달한다."""
    from view.web.bootstrap.service_container import ServiceContainer

    ctx = _make_fake_context()
    ctx.program_trading_stream_service.load_snapshot.return_value = {
        "subscribedCodes": ["005930", "000660"],
    }
    ServiceContainer(ctx).run()

    repo = patched_service_container_deps["StreamingStockRepo"].return_value
    repo.load_pt_desired_from_db.assert_called_once_with(
        "data/program_subscribe/program_trading.db",
        fallback_codes=["005930", "000660"],
    )


def test_service_container_batch_mode_skips_streaming_and_intraday_web_tasks(patched_service_container_deps):
    """BATCH 단독은 장마감 작업에 불필요한 실시간/장중/웹 task 생성을 건너뛴다."""
    from view.web.bootstrap.service_container import ServiceContainer

    ctx = _make_fake_context()
    ctx.runtime_mode = RuntimeMode.BATCH
    ServiceContainer(ctx).run()

    patched_service_container_deps["StreamingService"].assert_not_called()
    patched_service_container_deps["PriceStreamService"].assert_not_called()
    patched_service_container_deps["PriceSubscriptionService"].assert_not_called()
    patched_service_container_deps["WebSocketWatchdogTask"].assert_not_called()
    patched_service_container_deps["PreMarketHealthCheckTask"].assert_not_called()
    patched_service_container_deps["OpeningPositionReconcileTask"].assert_not_called()
    patched_service_container_deps["NotificationQueueTask"].assert_not_called()

    assert ctx.streaming_service is None
    assert ctx.price_stream_service is None
    assert ctx.price_subscription_service is None
    assert ctx.websocket_watchdog_task is None
    assert ctx.pre_market_health_check_task is None
    assert ctx.opening_position_reconcile_task is None
    assert ctx.notification_queue_task is None
    assert ctx.after_market_reconcile_task is patched_service_container_deps["AfterMarketReconcileTask"].return_value
    assert ctx.post_market_replay_audit_task is patched_service_container_deps["PostMarketReplayAuditTask"].return_value
    assert ctx.newhigh_strategy_coverage_backtest_task is patched_service_container_deps[
        "NewHighStrategyCoverageBacktestTask"
    ].return_value
    assert ctx.daily_price_collector_task is patched_service_container_deps["DailyPriceCollectorTask"].return_value
    assert ctx.order_execution_service is patched_service_container_deps["OrderExecutionService"].return_value


def test_service_container_web_mode_keeps_realtime_and_skips_trading_batch_tasks(patched_service_container_deps):
    """WEB 단독은 화면/API용 realtime chain 과 알림 task만 만들고 장중/장마감 task는 건너뛴다."""
    from view.web.bootstrap.service_container import ServiceContainer

    ctx = _make_fake_context()
    ctx.runtime_mode = RuntimeMode.WEB
    ServiceContainer(ctx).run()

    assert ctx.streaming_service is patched_service_container_deps["StreamingService"].return_value
    assert ctx.price_stream_service is patched_service_container_deps["PriceStreamService"].return_value
    assert ctx.price_subscription_service is patched_service_container_deps["PriceSubscriptionService"].return_value
    assert ctx.websocket_watchdog_task is patched_service_container_deps["WebSocketWatchdogTask"].return_value
    assert ctx.notification_queue_task is patched_service_container_deps["NotificationQueueTask"].return_value

    patched_service_container_deps["PreMarketHealthCheckTask"].assert_not_called()
    patched_service_container_deps["OpeningPositionReconcileTask"].assert_not_called()
    patched_service_container_deps["MinerviniUpdateTask"].assert_not_called()
    patched_service_container_deps["DailyPriceCollectorTask"].assert_not_called()
    patched_service_container_deps["OhlcvUpdateTask"].assert_not_called()
    patched_service_container_deps["AfterMarketReconcileTask"].assert_not_called()
    patched_service_container_deps["ThemeDailyLeaderReportTask"].assert_not_called()
    patched_service_container_deps["ThemeIntradayLeaderAlertTask"].assert_not_called()
    patched_service_container_deps["PostMarketReplayAuditTask"].assert_not_called()

    assert ctx.pre_market_health_check_task is None
    assert ctx.opening_position_reconcile_task is None
    assert ctx.minervini_update_task is None
    assert ctx.daily_price_collector_task is None
    assert ctx.ohlcv_update_task is None
    assert ctx.after_market_reconcile_task is None
    assert ctx.theme_intraday_leader_alert_task is None
    assert ctx.order_execution_service is patched_service_container_deps["OrderExecutionService"].return_value


def test_service_container_trading_mode_keeps_realtime_intraday_and_skips_web_batch_tasks(patched_service_container_deps):
    """TRADING 단독은 realtime chain 과 장중 task만 만들고 웹 알림/장마감 task는 건너뛴다."""
    from view.web.bootstrap.service_container import ServiceContainer

    ctx = _make_fake_context()
    ctx.runtime_mode = RuntimeMode.TRADING
    ServiceContainer(ctx).run()

    assert ctx.streaming_service is patched_service_container_deps["StreamingService"].return_value
    assert ctx.price_stream_service is patched_service_container_deps["PriceStreamService"].return_value
    assert ctx.price_subscription_service is patched_service_container_deps["PriceSubscriptionService"].return_value
    assert ctx.websocket_watchdog_task is patched_service_container_deps["WebSocketWatchdogTask"].return_value
    assert ctx.pre_market_health_check_task is patched_service_container_deps["PreMarketHealthCheckTask"].return_value
    assert ctx.opening_position_reconcile_task is patched_service_container_deps["OpeningPositionReconcileTask"].return_value
    assert ctx.cache_warmup_task is patched_service_container_deps["CacheWarmupTask"].return_value
    assert ctx.theme_intraday_leader_alert_task is patched_service_container_deps[
        "ThemeIntradayLeaderAlertTask"
    ].return_value

    patched_service_container_deps["NotificationQueueTask"].assert_not_called()
    patched_service_container_deps["MinerviniUpdateTask"].assert_not_called()
    patched_service_container_deps["DailyPriceCollectorTask"].assert_not_called()
    patched_service_container_deps["OhlcvUpdateTask"].assert_not_called()
    patched_service_container_deps["AfterMarketReconcileTask"].assert_not_called()
    patched_service_container_deps["ThemeDailyLeaderReportTask"].assert_not_called()
    patched_service_container_deps["StrategyLogReportTask"].assert_not_called()
    patched_service_container_deps["PostMarketReplayAuditTask"].assert_not_called()
    patched_service_container_deps["NewHighStrategyCoverageBacktestTask"].assert_not_called()

    assert ctx.notification_queue_task is None
    assert ctx.minervini_update_task is None
    assert ctx.daily_price_collector_task is None
    assert ctx.ohlcv_update_task is None
    assert ctx.after_market_reconcile_task is None
    assert ctx.theme_daily_leader_report_task is None
    assert ctx.theme_intraday_leader_alert_task is patched_service_container_deps[
        "ThemeIntradayLeaderAlertTask"
    ].return_value
    assert ctx.strategy_log_report_task is None
    assert ctx.post_market_replay_audit_task is None
    assert ctx.newhigh_strategy_coverage_backtest_task is None
    assert ctx.order_execution_service is patched_service_container_deps["OrderExecutionService"].return_value
    assert ctx.oneil_universe_service is patched_service_container_deps["OneilUniverseService"].return_value


def test_service_container_creates_universe_and_tasks(patched_service_container_deps):
    """OneilUniverseService, RankingTask, ThemeDailyLeaderReportTask, StrategyLogReportTask 가 생성된다."""
    from view.web.bootstrap.service_container import ServiceContainer

    ctx = _make_fake_context()
    ServiceContainer(ctx).run()

    assert ctx.oneil_universe_service is patched_service_container_deps["OneilUniverseService"].return_value
    assert ctx.ranking_task is patched_service_container_deps["RankingTask"].return_value
    ranking_kwargs = patched_service_container_deps["RankingTask"].call_args.kwargs
    assert "theme_daily_leader_service" not in ranking_kwargs
    assert ctx.theme_daily_leader_report_task is patched_service_container_deps[
        "ThemeDailyLeaderReportTask"
    ].return_value
    theme_report_kwargs = patched_service_container_deps["ThemeDailyLeaderReportTask"].call_args.kwargs
    assert theme_report_kwargs["ranking_task"] is ctx.ranking_task
    assert theme_report_kwargs["theme_daily_leader_service"] is ctx.theme_daily_leader_service
    assert ctx.theme_intraday_leader_alert_task is patched_service_container_deps[
        "ThemeIntradayLeaderAlertTask"
    ].return_value
    intraday_theme_kwargs = patched_service_container_deps[
        "ThemeIntradayLeaderAlertTask"
    ].call_args.kwargs
    assert intraday_theme_kwargs["ranking_task"] is ctx.ranking_task
    assert intraday_theme_kwargs["theme_daily_leader_service"] is ctx.theme_daily_leader_service
    assert ctx.strategy_log_report_task is patched_service_container_deps["StrategyLogReportTask"].return_value
    assert ctx.market_cap_gap_service is patched_service_container_deps["MarketCapGapService"].build_default.return_value
    assert patched_service_container_deps["MarketCapGapReportTask"].call_count == 2


def test_service_container_disables_kr_cap_gap_task_via_config(patched_service_container_deps):
    """config 플래그로 한국장 시총갭 리포트를 끄면 kr_task 만 None 이 된다."""
    from view.web.bootstrap.service_container import ServiceContainer

    ctx = _make_fake_context()
    ctx.full_config = {"market_cap_gap_report_kr_enabled": False}
    ServiceContainer(ctx).run()

    assert ctx.market_cap_gap_report_kr_task is None
    assert ctx.market_cap_gap_report_us_task is patched_service_container_deps["MarketCapGapReportTask"].return_value
    assert patched_service_container_deps["MarketCapGapReportTask"].call_count == 1


def test_service_container_disables_us_cap_gap_task_via_config(patched_service_container_deps):
    """config 플래그로 미국장 시총갭 리포트를 끄면 us_task 만 None 이 된다."""
    from view.web.bootstrap.service_container import ServiceContainer

    ctx = _make_fake_context()
    ctx.full_config = {"market_cap_gap_report_us_enabled": False}
    ServiceContainer(ctx).run()

    assert ctx.market_cap_gap_report_us_task is None
    assert ctx.market_cap_gap_report_kr_task is patched_service_container_deps["MarketCapGapReportTask"].return_value
    assert patched_service_container_deps["MarketCapGapReportTask"].call_count == 1


def test_service_container_does_not_wire_minervini_circular_pair(patched_service_container_deps):
    """MinerviniStage ↔ MinerviniUpdate 후주입은 WiringPhase 책임 — ServiceContainer 는 인스턴스만 만든다."""
    from view.web.bootstrap.service_container import ServiceContainer

    ctx = _make_fake_context()
    ServiceContainer(ctx).run()

    stage_instance = patched_service_container_deps["MinerviniStageService"].return_value
    update_instance = patched_service_container_deps["MinerviniUpdateTask"].return_value
    assert ctx.minervini_stage_service is stage_instance
    assert ctx.minervini_update_task is update_instance


def test_service_container_wires_overseas_dryrun_position_sizing(patched_service_container_deps):
    """overseas_us 모드의 VBO dry-run 에 고정 USD 슬롯 사이징을 주입한다."""
    from config.config_loader import AppConfig
    from view.web.bootstrap.service_container import ServiceContainer

    ctx = _make_fake_context()
    ctx.market_mode = "overseas_us"
    ctx.full_config = AppConfig(
        web={"host": "localhost", "port": 8080},
        market_mode="overseas_us",
        overseas_stock={"dryrun_slot_usd": 750.0, "dryrun_max_qty": 4},
    )
    ctx.overseas_stock_code_repository = MagicMock()

    with patch("view.web.bootstrap.service_container.OverseasPositionSizingService", autospec=True) as sizing_cls, \
         patch("view.web.bootstrap.service_container.OverseasCandidateService", autospec=True) as candidate_cls, \
         patch("view.web.bootstrap.service_container.OverseasVBODryRunService", autospec=True) as dryrun_cls, \
         patch("view.web.bootstrap.service_container.OverseasDryRunTask", autospec=True):
        ServiceContainer(ctx).run()

    sizing_cls.assert_called_once_with(
        slot_usd=750.0,
        max_qty=4,
        logger=ctx.logger,
    )
    dryrun_kwargs = dryrun_cls.call_args.kwargs
    assert dryrun_kwargs["candidate_service"] is candidate_cls.return_value
    assert dryrun_kwargs["position_sizing_service"] is sizing_cls.return_value
    # FX provider 배선: KIS 해외 잔고에서 USD/KRW 환율 추출(읽기 전용, 실주문 없음)
    fx_provider = dryrun_kwargs["fx_provider"]
    assert callable(fx_provider)


def test_service_container_wires_overseas_dryrun_us_market_clock(patched_service_container_deps):
    """overseas_us dry-run 태스크는 미국장 cron으로, 한국 캘린더/티켓 큐 없이 배선한다."""
    from config.config_loader import AppConfig
    from view.web.bootstrap.service_container import ServiceContainer

    ctx = _make_fake_context()
    ctx.market_mode = "overseas_us"
    ctx.full_config = AppConfig(
        web={"host": "localhost", "port": 8080},
        market_mode="overseas_us",
        overseas_stock={"dryrun_slot_usd": 1000.0},
    )
    ctx.overseas_stock_code_repository = MagicMock()

    with patch("view.web.bootstrap.service_container.OverseasPositionSizingService", autospec=True), \
         patch("view.web.bootstrap.service_container.OverseasCandidateService", autospec=True), \
         patch("view.web.bootstrap.service_container.OverseasVBODryRunService", autospec=True), \
         patch("view.web.bootstrap.service_container.OverseasDryRunTask", autospec=True) as task_cls:
        ServiceContainer(ctx).run()

    task_kwargs = task_cls.call_args.kwargs
    # O-1: 규칙 기반 NYSE 캘린더가 주입된다 (미국 휴장일 스킵).
    us_calendar = patched_service_container_deps["USMarketCalendarService"].return_value
    assert task_kwargs["market_calendar_service"] is us_calendar
    # 미국 정규장 클럭 주입 (America/New_York)
    assert task_kwargs["market_clock"].timezone_name == "America/New_York"
    # Ticket-driven 전환: 미국장 TimeDispatcher(time_dispatcher_us)가 NY 마감 후
    # delay 만큼 대기 뒤 티켓을 발행 → WorkerPool 이 execute() 를 호출한다.
    assert task_kwargs["worker_pool"] is ctx.worker_pool
    assert task_kwargs["notification_service"] is ctx.notification_service
    # 미국장 전용 TimeDispatcher 가 NYSE 캘린더(mcs) + 미국장 클럭으로 생성된다.
    assert ctx.time_dispatcher_us is not None
    td_us_calls = [
        c for c in patched_service_container_deps["TimeDispatcher"].call_args_list
        if c.kwargs.get("db_path") == "data/time_dispatcher_state_us.db"
    ]
    assert len(td_us_calls) == 1
    assert td_us_calls[0].kwargs["mcs"] is us_calendar
    assert td_us_calls[0].kwargs["market_clock"].timezone_name == "America/New_York"


def test_domestic_active_with_overseas_enabled_builds_dryrun_task(patched_service_container_deps):
    """active=domestic 이라도 enabled_market_modes 에 overseas_us 가 있으면 dry-run 태스크를 공존 조립한다."""
    from view.web.bootstrap.service_container import ServiceContainer

    ctx = _make_fake_context()  # market_mode 미설정 → domestic active (국내 전 서비스 조립)
    ctx.enabled_market_modes = ["domestic", "overseas_us"]
    ctx.overseas_stock_code_repository = MagicMock()

    with patch("view.web.bootstrap.service_container.OverseasPositionSizingService", autospec=True), \
         patch("view.web.bootstrap.service_container.OverseasCandidateService", autospec=True), \
         patch("view.web.bootstrap.service_container.OverseasVBODryRunService", autospec=True), \
         patch("view.web.bootstrap.service_container.OverseasDryRunTask", autospec=True) as task_cls:
        ServiceContainer(ctx).run()

    # 국내 active 서비스가 살아 있으면서(국내 fail-close 아님) 해외 dry-run 태스크도 조립된다.
    assert ctx.stock_query_service is patched_service_container_deps["StockQueryService"].return_value
    assert ctx.overseas_dryrun_task is task_cls.return_value


def test_domestic_active_without_overseas_enabled_skips_dryrun_task(patched_service_container_deps):
    """overseas_us 가 enabled 에 없으면 dry-run 태스크는 조립되지 않는다(None)."""
    from view.web.bootstrap.service_container import ServiceContainer

    ctx = _make_fake_context()  # enabled 미설정 → domestic 단독
    ctx.overseas_stock_code_repository = MagicMock()
    ServiceContainer(ctx).run()

    assert ctx.overseas_dryrun_task is None


@pytest.mark.asyncio
async def test_overseas_fx_provider_extracts_rate_from_balance(patched_service_container_deps):
    """배선된 fx_provider 는 broker 잔고 응답에서 USD/KRW 환율을 추출한다(실패 시 None)."""
    from config.config_loader import AppConfig
    from view.web.bootstrap.service_container import ServiceContainer

    ctx = _make_fake_context()
    ctx.market_mode = "overseas_us"
    ctx.full_config = AppConfig(
        web={"host": "localhost", "port": 8080},
        market_mode="overseas_us",
        overseas_stock={"dryrun_slot_usd": 750.0},
    )
    ctx.overseas_stock_code_repository = MagicMock()
    ctx.broker.get_overseas_balance = AsyncMock(
        return_value=SimpleNamespace(data={"output2": {"frst_bltn_exrt": "1357.5"}})
    )

    with patch("view.web.bootstrap.service_container.OverseasPositionSizingService", autospec=True), \
         patch("view.web.bootstrap.service_container.OverseasCandidateService", autospec=True), \
         patch("view.web.bootstrap.service_container.OverseasVBODryRunService", autospec=True) as dryrun_cls, \
         patch("view.web.bootstrap.service_container.OverseasDryRunTask", autospec=True):
        ServiceContainer(ctx).run()

    fx_provider = dryrun_cls.call_args.kwargs["fx_provider"]
    assert await fx_provider() == 1357.5

    # 잔고 조회 실패 시 None → KRW 환산 생략
    ctx.broker.get_overseas_balance = AsyncMock(side_effect=RuntimeError("boom"))
    assert await fx_provider() is None


def test_service_container_wires_execution_strength_recorder(patched_service_container_deps):
    """체결강도 recorder(repo)가 생성되어 PriceStreamService에 주입된다 (todo 1-5)."""
    from view.web.bootstrap.service_container import ServiceContainer

    ctx = _make_fake_context()
    ServiceContainer(ctx).run()

    es_repo = patched_service_container_deps["ExecutionStrengthRepository"].return_value
    assert ctx.execution_strength_repo is es_repo
    kwargs = patched_service_container_deps["PriceStreamService"].call_args.kwargs
    assert kwargs["execution_strength_recorder"] is es_repo


def test_service_container_disables_execution_strength_capture_via_config(
    patched_service_container_deps,
):
    from view.web.bootstrap.service_container import ServiceContainer

    ctx = _make_fake_context()
    ctx.full_config = {"execution_strength_capture_enabled": False}
    ServiceContainer(ctx).run()

    patched_service_container_deps["ExecutionStrengthRepository"].assert_not_called()
    assert ctx.execution_strength_repo is None
    kwargs = patched_service_container_deps["PriceStreamService"].call_args.kwargs
    assert kwargs["execution_strength_recorder"] is None
