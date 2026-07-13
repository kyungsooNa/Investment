"""ConfigBootstrap 단위 테스트.

`WebAppContext.load_config_and_env()` 본문에서 추출된 ConfigBootstrap이
컨텍스트 필드(env, market_clock, notification_service 등)를 동일하게
채우는지 검증한다.
"""
import contextlib
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def patched_bootstrap_deps():
    """ConfigBootstrap 의존 모듈을 모두 mock 한다."""
    patch_targets = [
        ("load_configs", patch("view.web.bootstrap.config_bootstrap.load_configs")),
        ("env_cls", patch("view.web.bootstrap.config_bootstrap.KoreaInvestApiEnv", autospec=True)),
        ("clock_cls", patch("view.web.bootstrap.config_bootstrap.MarketClock", autospec=True)),
        ("notif_cls", patch("view.web.bootstrap.config_bootstrap.NotificationService", autospec=True)),
        ("telegram_history_cls", patch("view.web.bootstrap.config_bootstrap.TelegramNotificationRepository", autospec=True)),
        ("op_alert_cls", patch("view.web.bootstrap.config_bootstrap.OperatorAlertService", autospec=True)),
        ("kill_cls", patch("view.web.bootstrap.config_bootstrap.KillSwitchService", autospec=True)),
        ("rej_cls", patch("view.web.bootstrap.config_bootstrap.RejectionDistributionService", autospec=True)),
        ("tn_cls", patch("view.web.bootstrap.config_bootstrap.TelegramNotifier", autospec=True)),
        ("tr_cls", patch("view.web.bootstrap.config_bootstrap.TelegramReporter", autospec=True)),
        ("mcs_cls", patch("view.web.bootstrap.config_bootstrap.MarketCalendarService", autospec=True)),
    ]
    with contextlib.ExitStack() as stack:
        mocks = {name: stack.enter_context(p) for name, p in patch_targets}
        mocks["load_configs"].return_value = {
            "market_open_time": "09:00",
            "market_close_time": "15:40",
            "market_timezone": "Asia/Seoul",
        }
        yield mocks


def _make_fake_context():
    """ConfigBootstrap.run 이 mutate 할 컨텍스트 stub."""
    ctx = SimpleNamespace()
    ctx.logger = MagicMock()
    ctx.full_config = {}
    ctx.virtual_repo = MagicMock()
    ctx.virtual_trade_service = MagicMock()
    ctx.pm = None
    ctx._load_position_sizing_state = MagicMock()
    return ctx


def test_config_bootstrap_populates_env_and_market_clock(patched_bootstrap_deps):
    """env, market_clock 필드가 컨텍스트에 주입된다."""
    from view.web.bootstrap.config_bootstrap import ConfigBootstrap

    ctx = _make_fake_context()
    ConfigBootstrap(ctx).run()

    patched_bootstrap_deps["load_configs"].assert_called_once()
    patched_bootstrap_deps["env_cls"].assert_called_once()
    patched_bootstrap_deps["clock_cls"].assert_called_once()
    assert ctx.env is patched_bootstrap_deps["env_cls"].return_value
    assert ctx.market_clock is patched_bootstrap_deps["clock_cls"].return_value


def test_config_bootstrap_creates_notification_alert_killswitch(patched_bootstrap_deps):
    """notification_service, operator_alert_service, kill_switch_service 가 생성된다."""
    from view.web.bootstrap.config_bootstrap import ConfigBootstrap

    ctx = _make_fake_context()
    ConfigBootstrap(ctx).run()

    assert ctx.notification_service is patched_bootstrap_deps["notif_cls"].return_value
    assert ctx.telegram_notification_repository is patched_bootstrap_deps["telegram_history_cls"].return_value
    assert ctx.operator_alert_service is patched_bootstrap_deps["op_alert_cls"].return_value
    assert ctx.kill_switch_service is patched_bootstrap_deps["kill_cls"].return_value


def test_config_bootstrap_attaches_rejection_distribution(patched_bootstrap_deps):
    """rejection_distribution_service 가 생성되고 logger 에 attach 된다."""
    from view.web.bootstrap.config_bootstrap import ConfigBootstrap

    ctx = _make_fake_context()
    ConfigBootstrap(ctx).run()

    rej_instance = patched_bootstrap_deps["rej_cls"].return_value
    assert ctx.rejection_distribution_service is rej_instance
    rej_instance.attach_to_strategy_logger.assert_called_once()


def test_config_bootstrap_propagates_market_clock_to_virtual_repo(patched_bootstrap_deps):
    """virtual_repo.tm, virtual_trade_service.tm 가 market_clock 으로 갱신된다."""
    from view.web.bootstrap.config_bootstrap import ConfigBootstrap

    ctx = _make_fake_context()
    ConfigBootstrap(ctx).run()

    expected = patched_bootstrap_deps["clock_cls"].return_value
    assert ctx.virtual_repo.tm is expected
    assert ctx.virtual_trade_service.tm is expected


def test_config_bootstrap_skips_telegram_when_config_missing(patched_bootstrap_deps):
    """텔레그램 토큰이 누락되면 TelegramNotifier 가 생성되지 않는다."""
    from view.web.bootstrap.config_bootstrap import ConfigBootstrap

    # load_configs 기본 반환값에는 텔레그램 토큰이 없다
    ctx = _make_fake_context()
    ConfigBootstrap(ctx).run()

    patched_bootstrap_deps["tn_cls"].assert_not_called()
    patched_bootstrap_deps["tr_cls"].assert_not_called()


def test_config_bootstrap_registers_telegram_when_tokens_present(patched_bootstrap_deps):
    """텔레그램 토큰 4종이 모두 있으면 TelegramNotifier 와 Reporter 가 생성된다."""
    from view.web.bootstrap.config_bootstrap import ConfigBootstrap

    patched_bootstrap_deps["load_configs"].return_value = {
        "market_open_time": "09:00",
        "market_close_time": "15:40",
        "market_timezone": "Asia/Seoul",
        "telegram_backlog_bot_token": "b1",
        "telegram_strategy_bot_token": "s1",
        "telegram_report_bot_token": "r1",
        "telegram_chat_id": "c1",
    }
    ctx = _make_fake_context()
    ConfigBootstrap(ctx).run()

    patched_bootstrap_deps["tn_cls"].assert_called_once()
    patched_bootstrap_deps["tr_cls"].assert_called_once()
    history = patched_bootstrap_deps["telegram_history_cls"].return_value
    assert patched_bootstrap_deps["tn_cls"].call_args.kwargs["history_repository"] is history
    assert patched_bootstrap_deps["tr_cls"].call_args.kwargs["history_repository"] is history
    notif_instance = patched_bootstrap_deps["notif_cls"].return_value
    notif_instance.register_external_handler.assert_called_once()


def test_config_bootstrap_creates_market_calendar_service(patched_bootstrap_deps):
    """MarketCalendarService 가 마지막 단계에서 생성된다."""
    from view.web.bootstrap.config_bootstrap import ConfigBootstrap

    ctx = _make_fake_context()
    ConfigBootstrap(ctx).run()

    patched_bootstrap_deps["mcs_cls"].assert_called_once()
    assert ctx._mcs is patched_bootstrap_deps["mcs_cls"].return_value
