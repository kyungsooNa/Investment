"""StrategyScheduler event-shadow 구독 라이프사이클 단위 테스트 (P2 2-4 PR-2).

scheduler 가 cfg.event_driven_shadow=True 인 전략에 대해:
  - scan() 직후 strategy.current_candidate_codes() 와 비교해 router 구독을 갱신한다
  - 구독 evaluator wrapper 는 strategy.evaluate_single 의 결과를 shadow journal 에
    기록하고 None 을 반환 (실 주문 미발생)
  - 미적용 (event_driven_shadow=False) 인 전략은 구독 없음
"""
from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from repositories.streaming_stock_repo import StreamingType
from common.types import TradeSignal
from scheduler.strategy_scheduler import StrategyScheduler, StrategySchedulerConfig
from services.event_shadow_journal_service import EventShadowJournalService
from services.price_subscription_service import SubscriptionPriority


def _make_scheduler(event_router=None, event_shadow_journal=None, price_subscription_service=None):
    market_clock = MagicMock()
    market_clock.get_current_kst_time.return_value = datetime(2026, 5, 20, 10, 30, 0)
    return StrategyScheduler(
        virtual_trade_service=MagicMock(),
        order_execution_service=MagicMock(),
        stock_query_service=MagicMock(),
        stock_code_repository=MagicMock(),
        market_clock=market_clock,
        market_calendar_service=MagicMock(),
        logger=MagicMock(),
        dry_run=True,
        store=MagicMock(),
        price_subscription_service=price_subscription_service,
        event_router=event_router,
        event_shadow_journal=event_shadow_journal,
    )


def _make_strategy_cfg(name: str, event_driven_shadow: bool, codes=None) -> StrategySchedulerConfig:
    strategy = MagicMock()
    strategy.name = name
    strategy.current_candidate_codes = MagicMock(return_value=codes or [])
    strategy.evaluate_single = AsyncMock(return_value=None)
    return StrategySchedulerConfig(
        strategy=strategy,
        event_driven_shadow=event_driven_shadow,
    )


@pytest.mark.asyncio
async def test_refresh_subscriptions_adds_codes_from_candidate_set():
    router = MagicMock()
    router.subscribe = MagicMock()
    router.unsubscribe = MagicMock()
    journal = MagicMock()
    scheduler = _make_scheduler(event_router=router, event_shadow_journal=journal)

    cfg = _make_strategy_cfg("VBO", event_driven_shadow=True, codes=["005930", "000660"])
    await scheduler._refresh_event_shadow_subscriptions(cfg)

    assert router.subscribe.call_count == 2
    subscribed_codes = sorted(call.args[0] for call in router.subscribe.call_args_list)
    assert subscribed_codes == ["000660", "005930"]
    for call in router.subscribe.call_args_list:
        assert call.kwargs["strategy_name"] == "VBO"
        assert callable(call.kwargs["evaluator"])


@pytest.mark.asyncio
async def test_refresh_subscriptions_diffs_against_previous_set():
    router = MagicMock()
    router.subscribe = MagicMock()
    router.unsubscribe = MagicMock()
    scheduler = _make_scheduler(event_router=router, event_shadow_journal=MagicMock())

    cfg = _make_strategy_cfg("VBO", event_driven_shadow=True, codes=["005930", "000660"])
    await scheduler._refresh_event_shadow_subscriptions(cfg)
    router.subscribe.reset_mock()

    # 다음 scan: 005930 빠지고 035720 추가
    cfg.strategy.current_candidate_codes.return_value = ["000660", "035720"]
    await scheduler._refresh_event_shadow_subscriptions(cfg)

    router.unsubscribe.assert_called_once_with("005930", "VBO")
    router.subscribe.assert_called_once()
    assert router.subscribe.call_args.args[0] == "035720"


@pytest.mark.asyncio
async def test_refresh_subscriptions_noop_when_flag_off():
    router = MagicMock()
    scheduler = _make_scheduler(event_router=router, event_shadow_journal=MagicMock())

    cfg = _make_strategy_cfg("VBO", event_driven_shadow=False, codes=["005930"])
    await scheduler._refresh_event_shadow_subscriptions(cfg)

    router.subscribe.assert_not_called()
    router.unsubscribe.assert_not_called()


@pytest.mark.asyncio
async def test_refresh_subscriptions_noop_when_router_missing():
    journal = MagicMock()
    scheduler = _make_scheduler(event_router=None, event_shadow_journal=journal)

    cfg = _make_strategy_cfg("VBO", event_driven_shadow=True, codes=["005930"])
    # router 없이도 예외 없이 종료해야 한다
    await scheduler._refresh_event_shadow_subscriptions(cfg)


@pytest.mark.asyncio
async def test_refresh_subscriptions_syncs_price_subscription_category():
    router = MagicMock()
    router.subscribe = MagicMock()
    router.unsubscribe = MagicMock()
    price_sub = AsyncMock()
    scheduler = _make_scheduler(
        event_router=router,
        event_shadow_journal=MagicMock(),
        price_subscription_service=price_sub,
    )

    cfg = _make_strategy_cfg("래리윌리엄스VBO", event_driven_shadow=True, codes=["005930", "000660"])
    await scheduler._refresh_event_shadow_subscriptions(cfg)

    price_sub.sync_subscriptions.assert_awaited_once_with(
        ["000660", "005930"],
        "event_shadow_래리윌리엄스VBO",
        SubscriptionPriority.MEDIUM,
        StreamingType.UNIFIED_PRICE,
    )


@pytest.mark.asyncio
async def test_refresh_subscriptions_writes_status_record_to_daily_jsonl(tmp_path):
    router = MagicMock()
    router.subscribe = MagicMock()
    router.unsubscribe = MagicMock()
    journal = EventShadowJournalService(log_root=tmp_path)
    scheduler = _make_scheduler(event_router=router, event_shadow_journal=journal)

    cfg = _make_strategy_cfg("래리윌리엄스VBO", event_driven_shadow=True, codes=["005930", "000660"])
    await scheduler._refresh_event_shadow_subscriptions(cfg)

    path = tmp_path / "event_shadow" / "20260520.jsonl"
    assert path.exists()
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert records[-1]["signal_source"] == "event_shadow_status"
    assert records[-1]["event"] == "subscriptions_refreshed"
    assert records[-1]["strategy"] == "래리윌리엄스VBO"
    assert records[-1]["details"]["candidate_count"] == 2
    assert records[-1]["details"]["added_count"] == 2
    assert sorted(records[-1]["details"]["candidate_codes"]) == ["000660", "005930"]


@pytest.mark.asyncio
async def test_shadow_evaluator_records_signal_and_returns_none():
    router = MagicMock()
    router.subscribe = MagicMock()
    journal = MagicMock()
    journal.record = MagicMock()
    scheduler = _make_scheduler(event_router=router, event_shadow_journal=journal)

    sig = TradeSignal(
        code="005930", name="삼성전자", action="BUY", price=75000,
        qty=1, reason="test", strategy_name="VBO",
    )
    cfg = _make_strategy_cfg("VBO", event_driven_shadow=True, codes=["005930"])
    cfg.strategy.evaluate_single = AsyncMock(return_value=sig)

    await scheduler._refresh_event_shadow_subscriptions(cfg)
    journal.record.reset_mock()
    journal.flush_to_file.reset_mock()
    evaluator = router.subscribe.call_args.kwargs["evaluator"]

    snapshot = {"price": "75000", "open": 74000.0}
    result = await evaluator("005930", snapshot)

    assert result is None  # shadow 는 절대 실 주문으로 전파되지 않음
    journal.record.assert_called_once()
    call_kwargs = journal.record.call_args.kwargs
    assert call_kwargs["strategy_name"] == "VBO"
    assert call_kwargs["code"] == "005930"
    assert call_kwargs["signal"]["action"] == "BUY"
    assert call_kwargs["snapshot"] == snapshot
    journal.flush_to_file.assert_called_once_with("20260520")


@pytest.mark.asyncio
async def test_shadow_evaluator_does_not_record_when_no_signal():
    router = MagicMock()
    router.subscribe = MagicMock()
    journal = MagicMock()
    journal.record = MagicMock()
    scheduler = _make_scheduler(event_router=router, event_shadow_journal=journal)

    cfg = _make_strategy_cfg("VBO", event_driven_shadow=True, codes=["005930"])
    cfg.strategy.evaluate_single = AsyncMock(return_value=None)

    await scheduler._refresh_event_shadow_subscriptions(cfg)
    journal.record.reset_mock()
    journal.flush_to_file.reset_mock()
    evaluator = router.subscribe.call_args.kwargs["evaluator"]

    result = await evaluator("005930", {"price": "70000"})

    assert result is None
    journal.record.assert_not_called()
    journal.flush_to_file.assert_not_called()


@pytest.mark.asyncio
async def test_shadow_evaluator_persists_signal_to_event_shadow_jsonl(tmp_path):
    router = MagicMock()
    router.subscribe = MagicMock()
    journal = EventShadowJournalService(log_root=tmp_path)
    scheduler = _make_scheduler(event_router=router, event_shadow_journal=journal)

    sig = TradeSignal(
        code="005930", name="삼성전자", action="BUY", price=75000,
        qty=1, reason="test", strategy_name="VBO",
    )
    cfg = _make_strategy_cfg("VBO", event_driven_shadow=True, codes=["005930"])
    cfg.strategy.evaluate_single = AsyncMock(return_value=sig)

    await scheduler._refresh_event_shadow_subscriptions(cfg)
    evaluator = router.subscribe.call_args.kwargs["evaluator"]
    await evaluator("005930", {"price": "75000", "open": 74000.0})

    path = tmp_path / "event_shadow" / "20260520.jsonl"
    assert path.exists()
    assert '"signal_source": "event_shadow"' in path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_exit_shadow_evaluator_flushes_record_to_daily_jsonl():
    journal = MagicMock()
    journal.record = MagicMock()
    scheduler = _make_scheduler(event_router=MagicMock(), event_shadow_journal=journal)

    sell = TradeSignal(
        code="005930", name="삼성전자", action="SELL", price=69000,
        qty=1, reason="stop", strategy_name="VBO",
    )
    strategy = MagicMock()
    strategy.name = "VBO"
    strategy.evaluate_exit_single = AsyncMock(return_value=sell)

    evaluator = scheduler._build_exit_shadow_evaluator(
        strategy,
        {"005930": {"code": "005930", "buy_price": 71000, "qty": 1}},
    )

    result = await evaluator("005930", {"price": "69000"})

    assert result is None
    journal.record.assert_called_once()
    assert journal.record.call_args.kwargs["signal_source"] == "event_shadow_exit"
    journal.flush_to_file.assert_called_once_with("20260520")


@pytest.mark.asyncio
async def test_stop_strategy_removes_event_shadow_price_subscription_category():
    price_sub = AsyncMock()
    scheduler = _make_scheduler(
        event_router=MagicMock(),
        event_shadow_journal=MagicMock(),
        price_subscription_service=price_sub,
    )
    cfg = _make_strategy_cfg("래리윌리엄스VBO", event_driven_shadow=True, codes=["005930"])
    scheduler.register(cfg)

    assert await scheduler.stop_strategy("래리윌리엄스VBO")

    assert price_sub.remove_category.await_args_list[0].args == ("scheduler_래리윌리엄스VBO",)
    assert price_sub.remove_category.await_args_list[1].args == ("event_shadow_래리윌리엄스VBO",)
