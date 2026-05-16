"""SchedulerBootstrap 단위 테스트.

`WebAppContext._bootstrap_schedulers()` 본문에서 추출된 SchedulerBootstrap이
TimeDispatcher 태스크 등록, BackgroundScheduler / ForegroundScheduler 생성을
정상 수행하는지 검증한다.
"""
import contextlib
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def patched_scheduler_deps():
    targets = [
        ("BackgroundScheduler",
         patch("view.web.bootstrap.scheduler_bootstrap.BackgroundScheduler", autospec=True)),
        ("ForegroundScheduler",
         patch("view.web.bootstrap.scheduler_bootstrap.ForegroundScheduler", autospec=True)),
        ("load_after_market_delays",
         patch("view.web.bootstrap.scheduler_bootstrap.load_after_market_delays")),
    ]
    with contextlib.ExitStack() as stack:
        mocks = {name: stack.enter_context(p) for name, p in targets}
        mocks["load_after_market_delays"].return_value = {}
        yield mocks


def _make_fake_context():
    ctx = SimpleNamespace()
    ctx.logger = MagicMock()
    ctx.pm = None
    ctx.worker_pool = MagicMock()
    ctx.time_dispatcher = MagicMock()
    # 모든 태스크는 MagicMock — task_name 속성 자동 제공.
    for name in [
        "ranking_task", "minervini_update_task", "daily_price_collector_task",
        "ohlcv_update_task", "premium_watchlist_generator_task", "newhigh_task",
        "log_cleanup_task", "strategy_log_report_task",
        "opening_position_reconcile_task", "after_market_reconcile_task",
        "websocket_watchdog_task", "pre_market_health_check_task",
        "cache_warmup_task", "notification_queue_task",
    ]:
        task = MagicMock()
        task.task_name = name
        setattr(ctx, name, task)
    ctx.price_subscription_service = None  # _initialize_price_subscriptions 에서 즉시 종료
    ctx._initialize_price_subscriptions = MagicMock()
    return ctx


def test_scheduler_bootstrap_creates_background_and_foreground(patched_scheduler_deps):
    from view.web.bootstrap.scheduler_bootstrap import SchedulerBootstrap

    ctx = _make_fake_context()
    with patch("view.web.bootstrap.scheduler_bootstrap.asyncio.create_task"):
        SchedulerBootstrap(ctx).run()

    assert ctx.background_scheduler is patched_scheduler_deps["BackgroundScheduler"].return_value
    assert ctx.foreground_scheduler is patched_scheduler_deps["ForegroundScheduler"].return_value


def test_scheduler_bootstrap_registers_time_dispatcher_tasks(patched_scheduler_deps):
    """TimeDispatcher.register_task 가 활성 태스크 수만큼 호출된다."""
    from view.web.bootstrap.scheduler_bootstrap import SchedulerBootstrap

    ctx = _make_fake_context()
    with patch("view.web.bootstrap.scheduler_bootstrap.asyncio.create_task"):
        SchedulerBootstrap(ctx).run()

    # 10개 태스크가 TimeDispatcher 에 등록된다 (`_bootstrap_schedulers` 의 첫번째 리스트).
    assert ctx.time_dispatcher.register_task.call_count == 10


def test_scheduler_bootstrap_registers_background_scheduler_tasks(patched_scheduler_deps):
    """BackgroundScheduler.register 가 14개 태스크에 대해 호출된다."""
    from view.web.bootstrap.scheduler_bootstrap import SchedulerBootstrap

    ctx = _make_fake_context()
    with patch("view.web.bootstrap.scheduler_bootstrap.asyncio.create_task"):
        SchedulerBootstrap(ctx).run()

    bg = patched_scheduler_deps["BackgroundScheduler"].return_value
    assert bg.register.call_count == 14


def test_scheduler_bootstrap_skips_none_tasks(patched_scheduler_deps):
    """opening_position_reconcile_task 가 None 이면 등록되지 않는다."""
    from view.web.bootstrap.scheduler_bootstrap import SchedulerBootstrap

    ctx = _make_fake_context()
    ctx.opening_position_reconcile_task = None
    with patch("view.web.bootstrap.scheduler_bootstrap.asyncio.create_task"):
        SchedulerBootstrap(ctx).run()

    assert ctx.time_dispatcher.register_task.call_count == 9
    bg = patched_scheduler_deps["BackgroundScheduler"].return_value
    assert bg.register.call_count == 13
