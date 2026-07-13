"""SchedulerBootstrap 단위 테스트.

`WebAppContext._bootstrap_schedulers()` 본문에서 추출된 SchedulerBootstrap이
TimeDispatcher 태스크 등록, BackgroundScheduler / ForegroundScheduler 생성을
정상 수행하는지 검증한다.

또한 `WebAppContext.runtime_mode` 별로 task 등록이 그룹 단위로 분기되는지
검증한다. StrategySchedulerTaskAdapter 는 StrategyFactory 책임이라 여기서
다루지 않는다.
"""
import contextlib
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from view.web.bootstrap.runtime_mode import RuntimeMode


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


def _make_fake_context(runtime_mode: RuntimeMode = RuntimeMode.ALL):
    ctx = SimpleNamespace()
    ctx.runtime_mode = runtime_mode
    ctx.logger = MagicMock()
    ctx.pm = None
    ctx.worker_pool = MagicMock()
    ctx.time_dispatcher = MagicMock()
    # 모든 태스크는 MagicMock — task_name 속성 자동 제공.
    for name in [
        "ranking_task", "minervini_update_task", "daily_price_collector_task",
        "ohlcv_update_task", "premium_watchlist_generator_task", "newhigh_task",
        "log_cleanup_task", "strategy_log_report_task", "theme_classification_task",
        "theme_daily_leader_report_task",
        "opening_position_reconcile_task", "after_market_reconcile_task",
        "post_market_replay_audit_task", "newhigh_strategy_coverage_backtest_task",
        "websocket_watchdog_task", "pre_market_health_check_task",
        "cache_warmup_task", "notification_queue_task",
        "market_cap_gap_report_kr_task", "market_cap_gap_report_us_task",
        "microstructure_capture_task", "program_capture_subscription_task",
        "theme_intraday_leader_alert_task",
        "ytd_ranking_report_task",
    ]:
        task = MagicMock()
        task.task_name = name
        setattr(ctx, name, task)
    ctx.price_subscription_service = None  # _initialize_price_subscriptions 에서 즉시 종료
    ctx._initialize_price_subscriptions = MagicMock()
    return ctx


def _run(ctx):
    from view.web.bootstrap.scheduler_bootstrap import SchedulerBootstrap
    SchedulerBootstrap(ctx).run()


# ---------- 인프라 생성 (mode 무관) ----------

def test_creates_background_and_foreground_in_all_mode(patched_scheduler_deps):
    ctx = _make_fake_context()
    _run(ctx)
    assert ctx.background_scheduler is patched_scheduler_deps["BackgroundScheduler"].return_value
    assert ctx.foreground_scheduler is patched_scheduler_deps["ForegroundScheduler"].return_value


def test_creates_foreground_even_in_web_only_mode(patched_scheduler_deps):
    """WEB 단독에서도 ForegroundScheduler 가 생성되어야 한다 (rate-limit middleware 의존)."""
    ctx = _make_fake_context(RuntimeMode.WEB)
    _run(ctx)
    assert ctx.foreground_scheduler is patched_scheduler_deps["ForegroundScheduler"].return_value


def test_creates_foreground_even_in_batch_only_mode(patched_scheduler_deps):
    ctx = _make_fake_context(RuntimeMode.BATCH)
    _run(ctx)
    assert ctx.foreground_scheduler is patched_scheduler_deps["ForegroundScheduler"].return_value


# ---------- mode=ALL 회귀 (현행 동작 100% 유지) ----------

def test_all_mode_registers_24_tasks_to_background(patched_scheduler_deps):
    ctx = _make_fake_context(RuntimeMode.ALL)
    _run(ctx)
    bg = patched_scheduler_deps["BackgroundScheduler"].return_value
    assert bg.register.call_count == 24


def test_all_mode_registers_15_tasks_to_time_dispatcher(patched_scheduler_deps):
    ctx = _make_fake_context(RuntimeMode.ALL)
    _run(ctx)
    assert ctx.time_dispatcher.register_task.call_count == 15


# ---------- mode 별 task 등록 ----------

def _registered_bg_task_names(patched_scheduler_deps) -> set:
    bg = patched_scheduler_deps["BackgroundScheduler"].return_value
    return {call.args[0].task_name for call in bg.register.call_args_list}


def test_web_only_registers_notification_and_watchdog(patched_scheduler_deps):
    ctx = _make_fake_context(RuntimeMode.WEB)
    _run(ctx)
    names = _registered_bg_task_names(patched_scheduler_deps)
    assert names == {"notification_queue_task", "websocket_watchdog_task"}


def test_overseas_us_registers_dryrun_task(patched_scheduler_deps):
    ctx = _make_fake_context(RuntimeMode.WEB)
    ctx.market_mode = "overseas_us"
    ctx.time_dispatcher_us = MagicMock()
    task = MagicMock()
    task.task_name = "overseas_vbo_dryrun"
    ctx.overseas_dryrun_task = task
    _run(ctx)
    names = _registered_bg_task_names(patched_scheduler_deps)
    assert "overseas_vbo_dryrun" in names
    # 미국장 dispatcher 에 priority 와 함께 등록되고 KST dispatcher 에는 등록되지 않는다.
    us_dispatched = [c.args[0] for c in ctx.time_dispatcher_us.register_task.call_args_list]
    kst_dispatched = [c.args[0] for c in ctx.time_dispatcher.register_task.call_args_list]
    assert "overseas_vbo_dryrun" in us_dispatched
    assert "overseas_vbo_dryrun" not in kst_dispatched


def test_domestic_mode_does_not_register_overseas_task(patched_scheduler_deps):
    ctx = _make_fake_context(RuntimeMode.WEB)  # market_mode 미설정 → domestic, overseas 미활성
    ctx.overseas_dryrun_task = MagicMock(task_name="overseas_vbo_dryrun")
    _run(ctx)
    names = _registered_bg_task_names(patched_scheduler_deps)
    assert "overseas_vbo_dryrun" not in names


def test_domestic_active_with_overseas_enabled_registers_dryrun_task(patched_scheduler_deps):
    """active=domestic 이라도 enabled_market_modes 에 overseas_us 가 있으면 공존 등록한다."""
    ctx = _make_fake_context(RuntimeMode.WEB)
    ctx.market_mode = "domestic"
    ctx.enabled_market_modes = ["domestic", "overseas_us"]
    ctx.time_dispatcher_us = MagicMock()
    ctx.overseas_dryrun_task = MagicMock(task_name="overseas_vbo_dryrun")
    _run(ctx)
    names = _registered_bg_task_names(patched_scheduler_deps)
    assert "overseas_vbo_dryrun" in names
    # 공존 모드에서도 dry-run 은 KST dispatcher 가 아닌 US dispatcher 에만 등록된다.
    dispatched_names = [
        call.args[0] for call in ctx.time_dispatcher.register_task.call_args_list
    ]
    assert "overseas_vbo_dryrun" not in dispatched_names
    us_dispatched = [c.args[0] for c in ctx.time_dispatcher_us.register_task.call_args_list]
    assert "overseas_vbo_dryrun" in us_dispatched


def test_trading_only_registers_intraday_and_watchdog(patched_scheduler_deps):
    ctx = _make_fake_context(RuntimeMode.TRADING)
    _run(ctx)
    names = _registered_bg_task_names(patched_scheduler_deps)
    assert names == {
        "pre_market_health_check_task",
        "opening_position_reconcile_task",
        "cache_warmup_task",
        "theme_intraday_leader_alert_task",
        "websocket_watchdog_task",
    }


def test_batch_only_registers_after_market_tasks_no_watchdog(patched_scheduler_deps):
    ctx = _make_fake_context(RuntimeMode.BATCH)
    _run(ctx)
    names = _registered_bg_task_names(patched_scheduler_deps)
    expected = {
        "ranking_task", "minervini_update_task", "daily_price_collector_task",
        "ohlcv_update_task", "premium_watchlist_generator_task", "newhigh_task",
        "log_cleanup_task", "post_market_replay_audit_task",
        "newhigh_strategy_coverage_backtest_task",
        "strategy_log_report_task", "after_market_reconcile_task",
        "theme_classification_task", "theme_daily_leader_report_task",
        "market_cap_gap_report_kr_task", "market_cap_gap_report_us_task",
        "microstructure_capture_task", "program_capture_subscription_task",
        "ytd_ranking_report_task",
    }
    assert names == expected
    assert "websocket_watchdog_task" not in names


def test_batch_registers_theme_classification_task(patched_scheduler_deps):
    """테마 분류 수집 태스크가 BATCH(장마감 후) 그룹에 등록되어야 한다."""
    ctx = _make_fake_context(RuntimeMode.BATCH)
    _run(ctx)
    names = _registered_bg_task_names(patched_scheduler_deps)
    assert "theme_classification_task" in names


def test_web_and_trading_registers_watchdog_exactly_once(patched_scheduler_deps):
    """websocket_watchdog 가 WEB|TRADING 양쪽 mode 에서 중복 등록되지 않는다."""
    ctx = _make_fake_context(RuntimeMode.WEB | RuntimeMode.TRADING)
    _run(ctx)
    bg = patched_scheduler_deps["BackgroundScheduler"].return_value
    watchdog_calls = [
        c for c in bg.register.call_args_list if c.args[0].task_name == "websocket_watchdog_task"
    ]
    assert len(watchdog_calls) == 1


# ---------- _initialize_price_subscriptions lifecycle ----------

def test_price_subscriptions_not_started_during_scheduler_bootstrap(patched_scheduler_deps):
    """초기 가격 구독 task는 bootstrap이 아니라 start_background_tasks에서 시작한다."""
    for mode in [RuntimeMode.WEB, RuntimeMode.TRADING, RuntimeMode.WEB | RuntimeMode.TRADING, RuntimeMode.ALL]:
        ctx = _make_fake_context(mode)
        _run(ctx)
        assert not hasattr(ctx, "_price_subscription_init_task"), f"mode={mode}"


def test_price_subscriptions_skipped_in_batch_only(patched_scheduler_deps):
    ctx = _make_fake_context(RuntimeMode.BATCH)
    _run(ctx)
    assert not hasattr(ctx, "_price_subscription_init_task")


# ---------- None task skip ----------

def test_skips_none_tasks(patched_scheduler_deps):
    ctx = _make_fake_context()
    ctx.opening_position_reconcile_task = None
    _run(ctx)
    # opening_position_reconcile_task 는 TRADING 그룹 + TimeDispatcher 등록 대상이었으므로
    # 둘 다 -1 감소한다.
    assert ctx.time_dispatcher.register_task.call_count == 14
    bg = patched_scheduler_deps["BackgroundScheduler"].return_value
    assert bg.register.call_count == 23
