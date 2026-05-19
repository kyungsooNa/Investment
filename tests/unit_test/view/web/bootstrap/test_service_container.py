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
from unittest.mock import MagicMock, patch

import pytest

from view.web.bootstrap.runtime_mode import RuntimeMode


SERVICE_CONTAINER_PATCH_NAMES = [
    "CacheStore", "StockRepository", "RSRatingRepository", "RSRatingService",
    "MarketDataService", "IndicatorService", "MessageBroker", "DlqManager",
    "WorkerPool", "TimeDispatcher", "DataQualityService", "RankingTask",
    "StockQueryService", "MinerviniStageService", "MinerviniUpdateTask",
    "StreamingService", "PriceStreamService", "StreamingStockRepo",
    "PriceSubscriptionService", "WebSocketWatchdogTask", "PreMarketHealthCheckTask",
    "DailyPriceCollectorTask", "OhlcvUpdateTask", "AccountSnapshotCache",
    "PositionSizingService", "RiskGateService", "ExecutionFlowService",
    "OrderPolicyService", "DeferredOrderQueue", "OrderExecutionService",
    "OneilUniverseService", "NaverFinanceScraperService",
    "PremiumWatchlistGeneratorTask", "CacheWarmupTask", "LogCleanupTask",
    "NewHighTask", "NewHighService", "StrategyLogReportTask",
    "StrategyLogReportService", "NotificationQueueTask",
    "AfterMarketReconcileTask", "OpeningPositionReconcileTask",
    "OpeningPositionReconcileService", "PerformanceProfiler",
]


@pytest.fixture
def patched_service_container_deps():
    """ServiceContainer 가 호출하는 모든 클래스를 mock 한다."""
    targets = [
        (name, patch(f"view.web.bootstrap.service_container.{name}", autospec=True))
        for name in SERVICE_CONTAINER_PATCH_NAMES
    ]
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

    assert ctx.pre_market_health_check_task is None
    assert ctx.opening_position_reconcile_task is None
    assert ctx.minervini_update_task is None
    assert ctx.daily_price_collector_task is None
    assert ctx.ohlcv_update_task is None
    assert ctx.after_market_reconcile_task is None
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

    patched_service_container_deps["NotificationQueueTask"].assert_not_called()
    patched_service_container_deps["MinerviniUpdateTask"].assert_not_called()
    patched_service_container_deps["DailyPriceCollectorTask"].assert_not_called()
    patched_service_container_deps["OhlcvUpdateTask"].assert_not_called()
    patched_service_container_deps["AfterMarketReconcileTask"].assert_not_called()
    patched_service_container_deps["StrategyLogReportTask"].assert_not_called()

    assert ctx.notification_queue_task is None
    assert ctx.minervini_update_task is None
    assert ctx.daily_price_collector_task is None
    assert ctx.ohlcv_update_task is None
    assert ctx.after_market_reconcile_task is None
    assert ctx.strategy_log_report_task is None
    assert ctx.order_execution_service is patched_service_container_deps["OrderExecutionService"].return_value
    assert ctx.oneil_universe_service is patched_service_container_deps["OneilUniverseService"].return_value


def test_service_container_creates_universe_and_tasks(patched_service_container_deps):
    """OneilUniverseService, RankingTask, StrategyLogReportTask 가 생성된다."""
    from view.web.bootstrap.service_container import ServiceContainer

    ctx = _make_fake_context()
    ServiceContainer(ctx).run()

    assert ctx.oneil_universe_service is patched_service_container_deps["OneilUniverseService"].return_value
    assert ctx.ranking_task is patched_service_container_deps["RankingTask"].return_value
    assert ctx.strategy_log_report_task is patched_service_container_deps["StrategyLogReportTask"].return_value


def test_service_container_does_not_wire_minervini_circular_pair(patched_service_container_deps):
    """MinerviniStage ↔ MinerviniUpdate 후주입은 WiringPhase 책임 — ServiceContainer 는 인스턴스만 만든다."""
    from view.web.bootstrap.service_container import ServiceContainer

    ctx = _make_fake_context()
    ServiceContainer(ctx).run()

    stage_instance = patched_service_container_deps["MinerviniStageService"].return_value
    update_instance = patched_service_container_deps["MinerviniUpdateTask"].return_value
    assert ctx.minervini_stage_service is stage_instance
    assert ctx.minervini_update_task is update_instance
