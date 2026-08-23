"""EventShadowManager 단위 테스트.

event_driven_shadow 전략의 entry/exit router 구독 갱신과 shadow journal 기록만
담당한다. 어떤 경로에서도 evaluator 는 None 을 돌려주어 실 주문이 나가지 않아야 한다.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from scheduler.event_shadow_manager import EventShadowManager


def _cfg(strategy=None, *, shadow=True):
    if strategy is None:
        strategy = MagicMock()
        strategy.name = "EventShadowStrategy"
        strategy.current_candidate_codes = MagicMock(return_value=["005930", "000660"])
        strategy.evaluate_single = AsyncMock(return_value=None)
        strategy.evaluate_exit_single = AsyncMock(return_value=None)
    return SimpleNamespace(strategy=strategy, event_driven_shadow=shadow)


def _manager(**kwargs):
    kwargs.setdefault("event_router", MagicMock())
    kwargs.setdefault("logger", MagicMock())
    journal = kwargs.pop("journal", "__default__")
    if journal == "__default__":
        journal = MagicMock()
        journal.flush_to_file = MagicMock()
    clock = kwargs.pop("market_clock", "__default__")
    if clock == "__default__":
        clock = MagicMock()
        clock.get_current_kst_time.return_value = SimpleNamespace(
            strftime=lambda _fmt: "20260821"
        )
    return EventShadowManager(journal=journal, market_clock=clock, **kwargs)


# --- entry 구독 갱신 --------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_is_a_noop_when_shadow_flag_is_off():
    manager = _manager()

    await manager._refresh_event_shadow_subscriptions(_cfg(shadow=False))

    manager._event_router.subscribe.assert_not_called()


@pytest.mark.asyncio
async def test_refresh_is_a_noop_without_journal():
    manager = _manager(journal=None)

    await manager._refresh_event_shadow_subscriptions(_cfg())

    manager._event_router.subscribe.assert_not_called()


@pytest.mark.asyncio
async def test_refresh_records_status_when_router_missing():
    manager = _manager(event_router=None)

    await manager._refresh_event_shadow_subscriptions(_cfg())

    manager._event_shadow_journal.record_status.assert_called_once()
    kwargs = manager._event_shadow_journal.record_status.call_args.kwargs
    assert kwargs["event"] == "subscriptions_skipped"
    assert kwargs["details"]["reason"] == "event_router_missing"


@pytest.mark.asyncio
async def test_refresh_records_status_when_candidate_lookup_raises():
    strategy = MagicMock()
    strategy.name = "S"
    strategy.current_candidate_codes = MagicMock(side_effect=RuntimeError("후보 조회 실패"))
    manager = _manager()

    await manager._refresh_event_shadow_subscriptions(_cfg(strategy))

    manager._logger.warning.assert_called_once()
    kwargs = manager._event_shadow_journal.record_status.call_args.kwargs
    assert kwargs["details"]["reason"] == "candidate_codes_error"


@pytest.mark.asyncio
async def test_refresh_subscribes_new_codes_and_unsubscribes_dropped_ones():
    manager = _manager()
    cfg = _cfg()
    manager._event_shadow_subscriptions["EventShadowStrategy"] = {"005930", "035420"}

    await manager._refresh_event_shadow_subscriptions(cfg)

    manager._event_router.unsubscribe.assert_called_once_with("035420", "EventShadowStrategy")
    subscribed = {call.args[0] for call in manager._event_router.subscribe.call_args_list}
    assert subscribed == {"000660"}
    assert manager._event_shadow_subscriptions["EventShadowStrategy"] == {"005930", "000660"}

    details = manager._event_shadow_journal.record_status.call_args.kwargs["details"]
    assert details["candidate_count"] == 2
    assert details["added_codes"] == ["000660"]
    assert details["removed_codes"] == ["035420"]


@pytest.mark.asyncio
async def test_refresh_absorbs_router_subscribe_and_unsubscribe_failures():
    router = MagicMock()
    router.unsubscribe = MagicMock(side_effect=RuntimeError("unsub 실패"))
    router.subscribe = MagicMock(side_effect=RuntimeError("sub 실패"))
    manager = _manager(event_router=router)
    manager._event_shadow_subscriptions["EventShadowStrategy"] = {"035420"}

    await manager._refresh_event_shadow_subscriptions(_cfg())

    assert manager._logger.warning.call_count == 3


# --- tick ingest 스냅샷 -----------------------------------------------------


def test_tick_ingest_snapshot_is_none_without_stream_service():
    assert _manager()._tick_ingest_snapshot_for({"005930"}) is None


def test_tick_ingest_snapshot_is_none_without_snapshot_method():
    manager = _manager(price_stream_svc=MagicMock(spec=[]))

    assert manager._tick_ingest_snapshot_for({"005930"}) is None


def test_tick_ingest_snapshot_failure_is_logged_and_returns_none():
    svc = MagicMock()
    svc.tick_ingest_stats_snapshot = MagicMock(side_effect=RuntimeError("스냅샷 실패"))
    manager = _manager(price_stream_svc=svc)

    assert manager._tick_ingest_snapshot_for({"005930"}) is None
    manager._logger.warning.assert_called_once()


@pytest.mark.asyncio
async def test_tick_ingest_snapshot_is_attached_to_status_details():
    svc = MagicMock()
    svc.tick_ingest_stats_snapshot = MagicMock(return_value={"005930": 12})
    manager = _manager(price_stream_svc=svc)

    await manager._refresh_event_shadow_subscriptions(_cfg())

    details = manager._event_shadow_journal.record_status.call_args.kwargs["details"]
    assert details["tick_ingest"] == {"005930": 12}


# --- 가격 구독 동기화 -------------------------------------------------------


@pytest.mark.asyncio
async def test_price_subscription_sync_is_skipped_without_service_or_method():
    manager = _manager()
    await manager._sync_event_shadow_price_subscriptions("S", {"005930"})

    manager._price_sub_svc = MagicMock(spec=[])
    await manager._sync_event_shadow_price_subscriptions("S", {"005930"})


@pytest.mark.asyncio
async def test_price_subscription_sync_awaits_coroutine_result():
    svc = MagicMock()
    svc.sync_subscriptions = MagicMock(return_value=AsyncMock()())
    manager = _manager(price_sub_svc=svc)

    await manager._sync_event_shadow_price_subscriptions("S", {"005930"})

    assert svc.sync_subscriptions.call_args.args[1] == "event_shadow_S"


@pytest.mark.asyncio
async def test_price_subscription_sync_retries_with_legacy_signature():
    calls = []

    def _sync(codes, category, priority, streaming_type=None):
        calls.append(streaming_type)
        if streaming_type is not None:
            raise TypeError("legacy signature")
        return None

    svc = MagicMock()
    svc.sync_subscriptions = _sync
    manager = _manager(price_sub_svc=svc)

    await manager._sync_event_shadow_price_subscriptions("S", {"005930"})

    assert len(calls) == 2
    manager._logger.warning.assert_not_called()


@pytest.mark.asyncio
async def test_price_subscription_sync_logs_when_both_signatures_fail():
    svc = MagicMock()
    svc.sync_subscriptions = MagicMock(side_effect=TypeError("legacy"))
    manager = _manager(price_sub_svc=svc)

    await manager._sync_event_shadow_price_subscriptions("S", {"005930"})

    manager._logger.warning.assert_called_once()


@pytest.mark.asyncio
async def test_price_subscription_sync_logs_unexpected_error():
    svc = MagicMock()
    svc.sync_subscriptions = MagicMock(side_effect=RuntimeError("구독 실패"))
    manager = _manager(price_sub_svc=svc)

    await manager._sync_event_shadow_price_subscriptions("S", {"005930"})

    manager._logger.warning.assert_called_once()


# --- entry evaluator --------------------------------------------------------


@pytest.mark.asyncio
async def test_entry_evaluator_always_returns_none_and_records_signal():
    signal = SimpleNamespace(action="BUY", code="005930")
    strategy = MagicMock()
    strategy.name = "S"
    strategy.evaluate_single = AsyncMock(return_value=signal)
    manager = _manager()

    evaluator = manager._build_shadow_evaluator(strategy)
    assert await evaluator("005930", {"price": 70000}) is None

    kwargs = manager._event_shadow_journal.record.call_args.kwargs
    assert kwargs["code"] == "005930"
    assert kwargs["signal"]["action"] == "BUY"
    assert kwargs["signal_source"] is None


@pytest.mark.asyncio
async def test_entry_evaluator_prefers_model_dump_payload():
    signal = MagicMock()
    signal.model_dump = MagicMock(return_value={"action": "BUY", "code": "005930"})
    strategy = MagicMock()
    strategy.name = "S"
    strategy.evaluate_single = AsyncMock(return_value=signal)
    manager = _manager()

    await manager._build_shadow_evaluator(strategy)("005930", {})

    assert manager._event_shadow_journal.record.call_args.kwargs["signal"] == {
        "action": "BUY",
        "code": "005930",
    }


@pytest.mark.asyncio
async def test_entry_evaluator_falls_back_when_payload_extraction_fails():
    signal = MagicMock()
    signal.model_dump = MagicMock(side_effect=RuntimeError("직렬화 실패"))
    signal.action = "BUY"
    signal.code = "005930"
    strategy = MagicMock()
    strategy.name = "S"
    strategy.evaluate_single = AsyncMock(return_value=signal)
    manager = _manager()

    await manager._build_shadow_evaluator(strategy)("005930", {})

    assert manager._event_shadow_journal.record.call_args.kwargs["signal"] == {
        "action": "BUY",
        "code": "005930",
    }


@pytest.mark.asyncio
async def test_entry_evaluator_swallows_strategy_and_journal_errors():
    strategy = MagicMock()
    strategy.name = "S"
    strategy.evaluate_single = AsyncMock(side_effect=RuntimeError("평가 실패"))
    manager = _manager()

    assert await manager._build_shadow_evaluator(strategy)("005930", {}) is None
    manager._logger.warning.assert_called_once()

    strategy.evaluate_single = AsyncMock(return_value=SimpleNamespace(action="BUY", code="005930"))
    manager._event_shadow_journal.record = MagicMock(side_effect=RuntimeError("기록 실패"))

    assert await manager._build_shadow_evaluator(strategy)("005930", {}) is None
    assert manager._logger.warning.call_count == 2


@pytest.mark.asyncio
async def test_entry_evaluator_returns_none_when_strategy_has_no_signal():
    strategy = MagicMock()
    strategy.name = "S"
    strategy.evaluate_single = AsyncMock(return_value=None)
    manager = _manager()

    assert await manager._build_shadow_evaluator(strategy)("005930", {}) is None
    manager._event_shadow_journal.record.assert_not_called()


# --- 저널 기록 헬퍼 ---------------------------------------------------------


@pytest.mark.asyncio
async def test_signal_and_status_recording_are_noops_without_journal():
    manager = _manager(journal=None)

    await manager._record_event_shadow_signal(
        strategy_name="S", code="005930", signal={}, snapshot={}
    )
    await manager._record_event_shadow_status(strategy_name="S", event="e")


@pytest.mark.asyncio
async def test_status_recording_is_skipped_when_journal_lacks_record_status():
    journal = MagicMock(spec=["record", "flush_to_file"])
    manager = _manager(journal=journal)

    await manager._record_event_shadow_status(strategy_name="S", event="e")

    journal.flush_to_file.assert_not_called()


@pytest.mark.asyncio
async def test_flush_is_skipped_when_journal_lacks_flush_method():
    journal = MagicMock(spec=["record"])
    manager = _manager(journal=journal)

    await manager._flush_event_shadow_journal()


def test_date_string_falls_back_to_system_clock_when_market_clock_fails():
    manager = _manager(market_clock=None)

    assert len(manager._event_shadow_date_str()) == 8


def test_category_and_subscriber_keys_are_namespaced():
    assert EventShadowManager._event_shadow_category_key("S") == "event_shadow_S"
    assert EventShadowManager._exit_shadow_category_key("S") == "event_shadow_exit_S"
    assert EventShadowManager._exit_shadow_subscriber_name("S") == "S__exit"


# --- exit 구독 갱신 ---------------------------------------------------------


@pytest.mark.asyncio
async def test_exit_refresh_is_a_noop_when_flag_off_or_dependencies_missing():
    manager = _manager()
    await manager._refresh_exit_shadow_subscriptions(_cfg(shadow=False))
    manager._event_router.subscribe.assert_not_called()

    no_router = _manager(event_router=None)
    await no_router._refresh_exit_shadow_subscriptions(_cfg())

    no_journal = _manager(journal=None)
    await no_journal._refresh_exit_shadow_subscriptions(_cfg())
    no_journal._event_router.subscribe.assert_not_called()


@pytest.mark.asyncio
async def test_exit_refresh_returns_when_holdings_lookup_raises():
    manager = _manager(get_strategy_holdings=MagicMock(side_effect=RuntimeError("보유 조회 실패")))

    await manager._refresh_exit_shadow_subscriptions(_cfg())

    manager._logger.warning.assert_called_once()
    manager._event_router.subscribe.assert_not_called()


@pytest.mark.asyncio
async def test_exit_refresh_subscribes_holdings_and_drops_sold_codes():
    manager = _manager()
    cfg = _cfg()
    manager._exit_shadow_subscriptions["EventShadowStrategy"] = {"035420"}

    await manager._refresh_exit_shadow_subscriptions(
        cfg, holdings=[{"code": "005930"}, {"code": ""}, {"code": " 000660 "}]
    )

    manager._event_router.unsubscribe.assert_called_once_with(
        "035420", "EventShadowStrategy__exit"
    )
    subscribed = {call.args[0] for call in manager._event_router.subscribe.call_args_list}
    assert subscribed == {"005930", "000660"}


@pytest.mark.asyncio
async def test_exit_refresh_absorbs_router_failures():
    router = MagicMock()
    router.unsubscribe = MagicMock(side_effect=RuntimeError("unsub 실패"))
    router.subscribe = MagicMock(side_effect=RuntimeError("sub 실패"))
    manager = _manager(event_router=router)
    manager._exit_shadow_subscriptions["EventShadowStrategy"] = {"035420"}

    await manager._refresh_exit_shadow_subscriptions(_cfg(), holdings=[{"code": "005930"}])

    assert manager._logger.warning.call_count == 2


@pytest.mark.asyncio
async def test_exit_refresh_uses_injected_holdings_provider():
    provider = MagicMock(return_value=[{"code": "005930"}])
    manager = _manager(get_strategy_holdings=provider)

    await manager._refresh_exit_shadow_subscriptions(_cfg())

    provider.assert_called_once()
    assert manager._exit_shadow_subscriptions["EventShadowStrategy"] == {"005930"}


# --- exit evaluator ---------------------------------------------------------


@pytest.mark.asyncio
async def test_exit_evaluator_ignores_codes_that_are_not_held():
    strategy = MagicMock()
    strategy.name = "S"
    manager = _manager()

    evaluator = manager._build_exit_shadow_evaluator(strategy, {})

    assert await evaluator("005930", {}) is None
    strategy.evaluate_exit_single.assert_not_called()


@pytest.mark.asyncio
async def test_exit_evaluator_records_signal_with_exit_source():
    strategy = MagicMock()
    strategy.name = "S"
    strategy.evaluate_exit_single = AsyncMock(
        return_value=SimpleNamespace(action="SELL", code="005930")
    )
    manager = _manager()

    evaluator = manager._build_exit_shadow_evaluator(strategy, {"005930": {"code": "005930"}})
    assert await evaluator("005930", {"price": 1}) is None

    kwargs = manager._event_shadow_journal.record.call_args.kwargs
    assert kwargs["signal_source"] == "event_shadow_exit"
    assert kwargs["signal"]["action"] == "SELL"


@pytest.mark.asyncio
async def test_exit_evaluator_swallows_strategy_and_journal_errors():
    strategy = MagicMock()
    strategy.name = "S"
    strategy.evaluate_exit_single = AsyncMock(side_effect=RuntimeError("청산 평가 실패"))
    manager = _manager()
    holdings = {"005930": {"code": "005930"}}

    assert await manager._build_exit_shadow_evaluator(strategy, holdings)("005930", {}) is None
    manager._logger.warning.assert_called_once()

    strategy.evaluate_exit_single = AsyncMock(
        return_value=SimpleNamespace(action="SELL", code="005930")
    )
    manager._event_shadow_journal.record = MagicMock(side_effect=RuntimeError("기록 실패"))

    assert await manager._build_exit_shadow_evaluator(strategy, holdings)("005930", {}) is None
    assert manager._logger.warning.call_count == 2


@pytest.mark.asyncio
async def test_exit_evaluator_falls_back_when_payload_extraction_fails():
    signal = MagicMock()
    signal.model_dump = MagicMock(side_effect=RuntimeError("직렬화 실패"))
    signal.action = "SELL"
    signal.code = "005930"
    strategy = MagicMock()
    strategy.name = "S"
    strategy.evaluate_exit_single = AsyncMock(return_value=signal)
    manager = _manager()

    holdings = {"005930": {"code": "005930", "qty": 1}}
    await manager._build_exit_shadow_evaluator(strategy, holdings)("005930", {})

    assert manager._event_shadow_journal.record.call_args.kwargs["signal"] == {
        "action": "SELL",
        "code": "005930",
    }


@pytest.mark.asyncio
async def test_exit_evaluator_returns_none_when_strategy_has_no_signal():
    strategy = MagicMock()
    strategy.name = "S"
    strategy.evaluate_exit_single = AsyncMock(return_value=None)
    manager = _manager()

    evaluator = manager._build_exit_shadow_evaluator(strategy, {"005930": {"qty": 1}})

    assert await evaluator("005930", {}) is None
    manager._event_shadow_journal.record.assert_not_called()


# --- teardown ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_teardown_is_a_noop_without_router():
    manager = _manager(event_router=None)

    await manager.teardown_strategy("S")


@pytest.mark.asyncio
async def test_teardown_unsubscribes_entry_and_exit_codes():
    manager = _manager()
    manager._event_shadow_subscriptions["S"] = {"005930"}
    manager._exit_shadow_subscriptions["S"] = {"000660"}

    await manager.teardown_strategy("S")

    manager._event_router.unsubscribe.assert_any_call("005930", "S")
    manager._event_router.unsubscribe.assert_any_call("000660", "S__exit")
    assert "S" not in manager._event_shadow_subscriptions
    assert "S" not in manager._exit_shadow_subscriptions


@pytest.mark.asyncio
async def test_teardown_absorbs_unsubscribe_failures():
    router = MagicMock()
    router.unsubscribe = MagicMock(side_effect=RuntimeError("unsub 실패"))
    manager = _manager(event_router=router)
    manager._event_shadow_subscriptions["S"] = {"005930"}
    manager._exit_shadow_subscriptions["S"] = {"000660"}

    await manager.teardown_strategy("S")

    assert manager._logger.warning.call_count == 2
