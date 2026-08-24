"""WebAppContext 의 상태 파일·구독 초기화 방어 경로 테스트.

전체 조립은 기존 테스트가 다루므로, 여기서는 조립을 건너뛴 인스턴스에 필요한
속성만 채워 개별 메서드를 직접 부른다. 자금 한도 상태 파일 입출력, 초기 구독
3단계(보유/프리미엄/관심종목)의 실패 격리, 공개·데모 모드 조기 반환처럼 정상
기동에서는 지나가지 않는 분기를 채운다.
"""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from repositories.streaming_stock_repo import StreamingType
from view.web.bootstrap.runtime_mode import RuntimeMode
from view.web.web_app_initializer import WebAppContext


def _ctx(**attrs):
    """조립을 건너뛴 빈 컨텍스트에 테스트가 쓰는 속성만 채운다."""
    ctx = WebAppContext.__new__(WebAppContext)
    ctx.logger = MagicMock()
    for key, value in attrs.items():
        setattr(ctx, key, value)
    return ctx


# --- 자금 한도 상태 파일 -------------------------------------------------------

def test_state_load_is_skipped_when_the_file_is_absent(tmp_path, monkeypatch):
    ctx = _ctx(full_config=MagicMock())
    monkeypatch.chdir(tmp_path)

    ctx._load_position_sizing_state()

    ctx.logger.info.assert_not_called()


def test_state_load_applies_both_sections(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    (tmp_path / WebAppContext._POSITION_SIZING_STATE_FILE).write_text(
        json.dumps({"max_order_amount_won": 2_000_000, "max_per_position_pct": 20.0}),
        encoding="utf-8",
    )
    risk_gate = MagicMock(max_order_amount_won=1_000_000)
    position_sizing = MagicMock(max_per_position_pct=10.0)
    ctx = _ctx(full_config=SimpleNamespace(risk_gate=risk_gate,
                                           position_sizing=position_sizing))

    ctx._load_position_sizing_state()

    assert risk_gate.max_order_amount_won == 2_000_000
    assert position_sizing.max_per_position_pct == 20.0


def test_state_load_ignores_null_entries_and_missing_sections(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    (tmp_path / WebAppContext._POSITION_SIZING_STATE_FILE).write_text(
        json.dumps({"max_order_amount_won": None}), encoding="utf-8"
    )
    ctx = _ctx(full_config=SimpleNamespace(risk_gate=None, position_sizing=None))

    ctx._load_position_sizing_state()

    ctx.logger.warning.assert_not_called()


def test_broken_state_file_is_logged_and_ignored(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    (tmp_path / WebAppContext._POSITION_SIZING_STATE_FILE).write_text(
        "{깨진 JSON", encoding="utf-8"
    )
    ctx = _ctx(full_config=MagicMock())

    ctx._load_position_sizing_state()

    ctx.logger.warning.assert_called_once()


def test_state_save_writes_both_limits(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ctx = _ctx(full_config=SimpleNamespace(
        risk_gate=MagicMock(max_order_amount_won=2_000_000),
        position_sizing=MagicMock(max_per_position_pct=20.0),
    ))

    ctx.save_position_sizing_state()

    saved = json.loads((tmp_path / WebAppContext._POSITION_SIZING_STATE_FILE).read_text())
    assert saved["max_order_amount_won"] == 2_000_000
    assert saved["max_per_position_pct"] == 20.0
    assert saved["updated_at"]


def test_state_save_writes_nulls_without_configured_sections(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ctx = _ctx(full_config=SimpleNamespace(risk_gate=None, position_sizing=None))

    ctx.save_position_sizing_state()

    saved = json.loads((tmp_path / WebAppContext._POSITION_SIZING_STATE_FILE).read_text())
    assert saved == {"max_order_amount_won": None, "max_per_position_pct": None,
                     "updated_at": saved["updated_at"]}


def test_state_save_failure_is_logged(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    ctx = _ctx(full_config=MagicMock())
    ctx.full_config.risk_gate.max_order_amount_won = object()  # JSON 직렬화 불가

    ctx.save_position_sizing_state()

    ctx.logger.warning.assert_called_once()


# --- 초기 가격 구독 -----------------------------------------------------------

def _subscription_ctx(**overrides):
    attrs = dict(
        price_subscription_service=MagicMock(
            add_subscription=AsyncMock(), sync_subscriptions=AsyncMock()
        ),
        virtual_trade_service=MagicMock(get_holds=MagicMock(return_value=[])),
        favorite_service=MagicMock(get_all=AsyncMock(return_value=[])),
    )
    attrs.update(overrides)
    return _ctx(**attrs)


@pytest.mark.asyncio
async def test_subscription_initialization_is_skipped_without_the_service():
    ctx = _subscription_ctx(price_subscription_service=None)

    await ctx._initialize_price_subscriptions()  # 조용히 지나가야 한다


@pytest.mark.asyncio
async def test_holdings_lookup_failure_does_not_stop_the_other_stages(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ctx = _subscription_ctx(
        virtual_trade_service=MagicMock(get_holds=MagicMock(side_effect=RuntimeError("db")))
    )
    ctx.favorite_service.get_all = AsyncMock(return_value=["005930"])

    await ctx._initialize_price_subscriptions()

    assert any("보유 종목 구독 초기화 실패" in str(c)
               for c in ctx.logger.warning.call_args_list)
    ctx.price_subscription_service.sync_subscriptions.assert_awaited()


@pytest.mark.asyncio
async def test_broken_premium_stock_file_is_logged_and_skipped(tmp_path, monkeypatch, mocker):
    ctx = _subscription_ctx()
    mocker.patch("os.path.exists", return_value=True)
    mocker.patch("builtins.open", mocker.mock_open(read_data="{깨진 JSON"))

    await ctx._initialize_price_subscriptions()

    assert any("프리미엄 종목 구독 초기화 실패" in str(c)
               for c in ctx.logger.warning.call_args_list)


@pytest.mark.asyncio
async def test_premium_stocks_are_subscribed_at_medium_priority(mocker):
    ctx = _subscription_ctx()
    mocker.patch("os.path.exists", return_value=True)
    mocker.patch(
        "builtins.open",
        mocker.mock_open(read_data=json.dumps(
            {"kospi": [{"code": "005930"}, {"code": ""}], "kosdaq": ["035720"]}
        )),
    )

    await ctx._initialize_price_subscriptions()

    call = ctx.price_subscription_service.sync_subscriptions.await_args_list[0]
    assert call.kwargs["category_key"] == "strategy_premium"
    assert call.kwargs["codes"] == ["005930", "035720"]


@pytest.mark.asyncio
async def test_favorite_lookup_failure_is_logged(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ctx = _subscription_ctx(
        favorite_service=MagicMock(get_all=AsyncMock(side_effect=RuntimeError("db")))
    )

    await ctx._initialize_price_subscriptions()

    assert any("관심종목 구독 초기화 실패" in str(c)
               for c in ctx.logger.warning.call_args_list)


@pytest.mark.asyncio
async def test_holdings_are_subscribed_at_high_priority(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ctx = _subscription_ctx(
        virtual_trade_service=MagicMock(
            get_holds=MagicMock(return_value=[{"code": "005930"}])
        )
    )

    await ctx._initialize_price_subscriptions()

    ctx.price_subscription_service.add_subscription.assert_awaited_once()


# --- 스케줄러 조회 헬퍼 --------------------------------------------------------

def test_enabled_strategy_names_are_none_without_a_scheduler():
    assert _ctx(scheduler=None)._get_enabled_strategy_names_for_report() is None


def test_only_enabled_strategies_are_reported():
    scheduler = SimpleNamespace(_strategies=[
        SimpleNamespace(enabled=True, strategy=SimpleNamespace(name="켜짐")),
        SimpleNamespace(enabled=False, strategy=SimpleNamespace(name="꺼짐")),
    ])

    assert _ctx(scheduler=scheduler)._get_enabled_strategy_names_for_report() == ["켜짐"]


# --- 공개/데모 모드 조기 반환 --------------------------------------------------

@pytest.mark.asyncio
async def test_background_tasks_are_skipped_in_public_mode(mocker):
    mocker.patch("view.web.deployment_policy.is_public_mode", return_value=True)
    mocker.patch("view.web.deployment_policy.is_demo_mode", return_value=False)
    ctx = _ctx(streaming_service=MagicMock(), background_scheduler=MagicMock())

    await ctx.start_background_tasks_and_wait()

    ctx.background_scheduler.start_all.assert_not_called()


@pytest.mark.asyncio
async def test_background_tasks_wait_for_the_scheduler_then_subscribe(mocker):
    mocker.patch("view.web.deployment_policy.is_public_mode", return_value=False)
    mocker.patch("view.web.deployment_policy.is_demo_mode", return_value=False)
    ctx = _ctx(streaming_service=MagicMock(),
               background_scheduler=MagicMock(start_all=AsyncMock()))
    ctx._initialize_price_subscriptions = AsyncMock()

    await ctx.start_background_tasks_and_wait()

    ctx.background_scheduler.start_all.assert_awaited_once()
    ctx._initialize_price_subscriptions.assert_awaited_once()
    assert ctx.streaming_service._callback == ctx._web_realtime_callback


def test_scheduler_initialization_is_skipped_in_demo_mode(mocker):
    mocker.patch("view.web.deployment_policy.is_demo_mode", return_value=True)
    factory = mocker.patch("view.web.bootstrap.strategy_factory.StrategyFactory")

    _ctx().initialize_scheduler()

    factory.assert_not_called()


# --- 구독 초기화 태스크 예약 ---------------------------------------------------

def test_subscription_task_is_not_scheduled_without_the_service():
    ctx = _ctx(price_subscription_service=None, runtime_mode=RuntimeMode.WEB,
               _price_subscription_init_task=None)

    ctx._schedule_price_subscription_initialization()

    assert ctx._price_subscription_init_task is None


def test_subscription_task_is_not_scheduled_outside_web_or_trading_mode():
    ctx = _ctx(price_subscription_service=MagicMock(), runtime_mode=RuntimeMode.BATCH,
               _price_subscription_init_task=None)

    ctx._schedule_price_subscription_initialization()

    assert ctx._price_subscription_init_task is None


@pytest.mark.asyncio
async def test_subscription_task_is_not_scheduled_twice():
    pending = MagicMock(done=MagicMock(return_value=False))
    ctx = _ctx(price_subscription_service=MagicMock(), runtime_mode=RuntimeMode.WEB,
               _price_subscription_init_task=pending)

    ctx._schedule_price_subscription_initialization()

    assert ctx._price_subscription_init_task is pending


# --- 독립 구독 판정 -----------------------------------------------------------

def test_blank_code_has_no_independent_subscription():
    assert _ctx(streaming_stock_repo=None)._has_independent_price_subscription("") is False


def test_repo_lookup_failure_falls_through_to_the_reference_map():
    repo = MagicMock()
    repo.get_desired.side_effect = RuntimeError("repo 오류")
    ctx = _ctx(streaming_stock_repo=repo, price_subscription_service=SimpleNamespace(
        _refs={"005930": {"portfolio": {"type": StreamingType.UNIFIED_PRICE}}}
    ))

    assert ctx._has_independent_price_subscription("005930") is True


def test_no_independent_subscription_without_a_matching_reference():
    ctx = _ctx(streaming_stock_repo=None, price_subscription_service=SimpleNamespace(
        _refs={"005930": {"pt": {"type": StreamingType.PROGRAM_TRADING}}}
    ))

    assert ctx._has_independent_price_subscription("005930") is False
