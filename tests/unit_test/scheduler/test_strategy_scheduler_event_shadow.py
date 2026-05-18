"""StrategyScheduler event-shadow 구독 라이프사이클 단위 테스트 (P2 2-4 PR-2).

scheduler 가 cfg.event_driven_shadow=True 인 전략에 대해:
  - scan() 직후 strategy.current_candidate_codes() 와 비교해 router 구독을 갱신한다
  - 구독 evaluator wrapper 는 strategy.evaluate_single 의 결과를 shadow journal 에
    기록하고 None 을 반환 (실 주문 미발생)
  - 미적용 (event_driven_shadow=False) 인 전략은 구독 없음
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from common.types import TradeSignal
from scheduler.strategy_scheduler import StrategyScheduler, StrategySchedulerConfig


def _make_scheduler(event_router=None, event_shadow_journal=None):
    return StrategyScheduler(
        virtual_trade_service=MagicMock(),
        order_execution_service=MagicMock(),
        stock_query_service=MagicMock(),
        stock_code_repository=MagicMock(),
        market_clock=MagicMock(),
        market_calendar_service=MagicMock(),
        logger=MagicMock(),
        dry_run=True,
        store=MagicMock(),
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
    scheduler._refresh_event_shadow_subscriptions(cfg)

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
    scheduler._refresh_event_shadow_subscriptions(cfg)
    router.subscribe.reset_mock()

    # 다음 scan: 005930 빠지고 035720 추가
    cfg.strategy.current_candidate_codes.return_value = ["000660", "035720"]
    scheduler._refresh_event_shadow_subscriptions(cfg)

    router.unsubscribe.assert_called_once_with("005930", "VBO")
    router.subscribe.assert_called_once()
    assert router.subscribe.call_args.args[0] == "035720"


@pytest.mark.asyncio
async def test_refresh_subscriptions_noop_when_flag_off():
    router = MagicMock()
    scheduler = _make_scheduler(event_router=router, event_shadow_journal=MagicMock())

    cfg = _make_strategy_cfg("VBO", event_driven_shadow=False, codes=["005930"])
    scheduler._refresh_event_shadow_subscriptions(cfg)

    router.subscribe.assert_not_called()
    router.unsubscribe.assert_not_called()


@pytest.mark.asyncio
async def test_refresh_subscriptions_noop_when_router_missing():
    journal = MagicMock()
    scheduler = _make_scheduler(event_router=None, event_shadow_journal=journal)

    cfg = _make_strategy_cfg("VBO", event_driven_shadow=True, codes=["005930"])
    # router 없이도 예외 없이 종료해야 한다
    scheduler._refresh_event_shadow_subscriptions(cfg)


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

    scheduler._refresh_event_shadow_subscriptions(cfg)
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


@pytest.mark.asyncio
async def test_shadow_evaluator_does_not_record_when_no_signal():
    router = MagicMock()
    router.subscribe = MagicMock()
    journal = MagicMock()
    journal.record = MagicMock()
    scheduler = _make_scheduler(event_router=router, event_shadow_journal=journal)

    cfg = _make_strategy_cfg("VBO", event_driven_shadow=True, codes=["005930"])
    cfg.strategy.evaluate_single = AsyncMock(return_value=None)

    scheduler._refresh_event_shadow_subscriptions(cfg)
    evaluator = router.subscribe.call_args.kwargs["evaluator"]

    result = await evaluator("005930", {"price": "70000"})

    assert result is None
    journal.record.assert_not_called()
