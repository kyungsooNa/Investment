"""StrategyFactory 단위 테스트.

`WebAppContext.initialize_scheduler()` 본문에서 추출된 StrategyFactory가
StrategyScheduler 와 7개 전략을 정상 등록하는지 검증한다.
"""
import contextlib
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from view.web.bootstrap.runtime_mode import RuntimeMode


STRATEGY_CLASS_NAMES = [
    "OneilSqueezeBreakoutStrategy",
    "OneilPocketPivotStrategy",
    "HighTightFlagStrategy",
    "FirstPullbackStrategy",
    "LarryWilliamsVBOStrategy",
    "RSI2PullbackStrategy",
    "LarryWilliamsChannelBreakoutStrategy",
]


@pytest.fixture
def patched_factory_deps():
    targets = [
        ("StrategyScheduler",
         patch("view.web.bootstrap.strategy_factory.StrategyScheduler", autospec=True)),
        ("StrategySchedulerTaskAdapter",
         patch("view.web.bootstrap.strategy_factory.StrategySchedulerTaskAdapter", autospec=True)),
    ]
    for cls_name in STRATEGY_CLASS_NAMES:
        targets.append((cls_name,
                        patch(f"view.web.bootstrap.strategy_factory.{cls_name}", autospec=True)))

    with contextlib.ExitStack() as stack:
        mocks = {name: stack.enter_context(p) for name, p in targets}
        yield mocks


def _make_fake_context(runtime_mode: RuntimeMode = RuntimeMode.ALL):
    ctx = SimpleNamespace()
    ctx.runtime_mode = runtime_mode
    ctx.logger = MagicMock()
    ctx.pm = None
    ctx.virtual_trade_service = MagicMock()
    ctx.order_execution_service = MagicMock()
    ctx.stock_query_service = MagicMock()
    ctx.stock_code_repository = MagicMock()
    ctx.market_clock = MagicMock()
    ctx._mcs = MagicMock()
    ctx.notification_service = MagicMock()
    ctx.price_subscription_service = MagicMock()
    ctx.kill_switch_service = MagicMock()
    ctx.account_snapshot_cache = MagicMock()
    ctx.position_sizing_service = MagicMock()
    ctx.indicator_service = MagicMock()
    ctx.oneil_universe_service = MagicMock()
    ctx.background_scheduler = MagicMock()
    return ctx


def test_strategy_factory_creates_scheduler(patched_factory_deps):
    from view.web.bootstrap.strategy_factory import StrategyFactory

    ctx = _make_fake_context()
    StrategyFactory(ctx).build()

    assert ctx.scheduler is patched_factory_deps["StrategyScheduler"].return_value


def test_strategy_factory_registers_seven_strategies(patched_factory_deps):
    from view.web.bootstrap.strategy_factory import StrategyFactory

    ctx = _make_fake_context()
    StrategyFactory(ctx).build()

    assert ctx.scheduler.register.call_count == 7


def test_strategy_factory_sets_legacy_osb_reference(patched_factory_deps):
    """웹 API 하위 호환용 osb_strategy / oneil_universe_service_ref 속성이 주입된다."""
    from view.web.bootstrap.strategy_factory import StrategyFactory

    ctx = _make_fake_context()
    StrategyFactory(ctx).build()

    assert ctx.osb_strategy is patched_factory_deps["OneilSqueezeBreakoutStrategy"].return_value
    assert ctx.oneil_universe_service_ref is ctx.oneil_universe_service


def test_strategy_factory_registers_adapter_to_background_scheduler(patched_factory_deps):
    """StrategySchedulerTaskAdapter 가 BackgroundScheduler 에 등록된다."""
    from view.web.bootstrap.strategy_factory import StrategyFactory

    ctx = _make_fake_context()
    StrategyFactory(ctx).build()

    patched_factory_deps["StrategySchedulerTaskAdapter"].assert_called_once_with(
        ctx.scheduler, market_clock=ctx.market_clock
    )
    ctx.background_scheduler.register.assert_called_once_with(
        patched_factory_deps["StrategySchedulerTaskAdapter"].return_value
    )


def test_strategy_factory_skips_adapter_when_background_scheduler_missing(patched_factory_deps):
    from view.web.bootstrap.strategy_factory import StrategyFactory

    ctx = _make_fake_context()
    ctx.background_scheduler = None
    StrategyFactory(ctx).build()

    patched_factory_deps["StrategySchedulerTaskAdapter"].assert_not_called()


# ---------- runtime_mode TRADING gating ----------

def test_strategy_factory_noop_when_trading_disabled_batch_only(patched_factory_deps):
    """mode=BATCH (TRADING 비포함) 이면 StrategyScheduler / 전략 / adapter 모두 미생성."""
    from view.web.bootstrap.strategy_factory import StrategyFactory

    ctx = _make_fake_context(RuntimeMode.BATCH)
    StrategyFactory(ctx).build()

    patched_factory_deps["StrategyScheduler"].assert_not_called()
    patched_factory_deps["StrategySchedulerTaskAdapter"].assert_not_called()
    assert not hasattr(ctx, "scheduler") or ctx.scheduler is None or isinstance(ctx.scheduler, MagicMock) is False
    ctx.background_scheduler.register.assert_not_called()


def test_strategy_factory_noop_when_trading_disabled_web_only(patched_factory_deps):
    """mode=WEB (TRADING 비포함) 이면 StrategyScheduler / 전략 / adapter 모두 미생성."""
    from view.web.bootstrap.strategy_factory import StrategyFactory

    ctx = _make_fake_context(RuntimeMode.WEB)
    StrategyFactory(ctx).build()

    patched_factory_deps["StrategyScheduler"].assert_not_called()
    patched_factory_deps["StrategySchedulerTaskAdapter"].assert_not_called()
    for cls_name in STRATEGY_CLASS_NAMES:
        patched_factory_deps[cls_name].assert_not_called()


def test_strategy_factory_builds_when_trading_enabled_via_combination(patched_factory_deps):
    """mode=WEB|TRADING 이면 StrategyScheduler + 전략 + adapter 모두 생성."""
    from view.web.bootstrap.strategy_factory import StrategyFactory

    ctx = _make_fake_context(RuntimeMode.WEB | RuntimeMode.TRADING)
    StrategyFactory(ctx).build()

    assert ctx.scheduler is patched_factory_deps["StrategyScheduler"].return_value
    assert ctx.scheduler.register.call_count == 7
    patched_factory_deps["StrategySchedulerTaskAdapter"].assert_called_once()
