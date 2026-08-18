from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, call

import pytest

from repositories.streaming_stock_repo import StreamingType
from services.subscription_policy import SubscriptionPriority
from task.background.intraday.program_capture_subscription_task import (
    ProgramCaptureSubscriptionTask,
)

CATEGORY = ProgramCaptureSubscriptionTask.CATEGORY_KEY
PRICE_CATEGORY = ProgramCaptureSubscriptionTask.PRICE_CATEGORY_KEY


class _FakeStore:
    """save_keyed/load_keyed 만 흉내내는 인메모리 스토어 (잔재 정리 검증용)."""

    def __init__(self):
        self._data = {}

    def save_keyed(self, key, value):
        self._data[key] = value

    def load_keyed(self, key):
        return self._data.get(key)


def _clock(open_now=True, date="20260703"):
    clock = MagicMock()
    clock.get_current_kst_time.return_value = datetime(
        int(date[:4]), int(date[4:6]), int(date[6:]), 10, 0, 0
    )
    clock.get_current_kst_date_str.return_value = date
    clock.is_market_operating_hours.return_value = open_now
    return clock


def _mcs(business_day=True):
    mcs = MagicMock()
    mcs.is_business_day = AsyncMock(return_value=business_day)
    return mcs


def _make_task(**kwargs):
    policy = kwargs.pop("policy", None)
    if policy is None:
        policy = MagicMock()
        policy.sync_subscriptions = AsyncMock()
    universe_service = kwargs.pop("universe_service", None)
    if universe_service is None:
        universe_service = MagicMock()
        universe_service.get_watchlist = AsyncMock(
            return_value={"000660": MagicMock(), "035420": MagicMock()}
        )
    virtual_trade_service = kwargs.pop("virtual_trade_service", None)
    if virtual_trade_service is None:
        virtual_trade_service = MagicMock()
        virtual_trade_service.get_holds = MagicMock(return_value=[{"code": "005930"}])
    defaults = dict(
        subscription_policy=policy,
        universe_service=universe_service,
        virtual_trade_service=virtual_trade_service,
        market_calendar_service=_mcs(),
        market_clock=_clock(),
        scheduler_store=_FakeStore(),
        logger=MagicMock(),
    )
    defaults.update(kwargs)
    return ProgramCaptureSubscriptionTask(**defaults), policy


@pytest.mark.asyncio
async def test_open_tick_syncs_candidates_low_priority_pt_and_persists():
    store = _FakeStore()
    task, policy = _make_task(scheduler_store=store)

    await task._tick()

    assert policy.sync_subscriptions.await_args_list == [
        call(
            ["005930", "000660", "035420"], CATEGORY,
            SubscriptionPriority.LOW, StreamingType.PROGRAM_TRADING,
        ),
        call(
            ["005930", "000660", "035420"],
            PRICE_CATEGORY,
            SubscriptionPriority.LOW,
            StreamingType.UNIFIED_PRICE,
        ),
    ]
    assert store.load_keyed("program_capture_subscribed_codes") == "005930,000660,035420"
    assert store.load_keyed("price_capture_subscribed_codes") == "005930,000660,035420"
    assert task.get_progress()["synced_date"] == "20260703"


@pytest.mark.asyncio
async def test_same_day_second_tick_does_not_resync():
    task, policy = _make_task()

    await task._tick()
    await task._tick()

    assert policy.sync_subscriptions.await_count == 2


@pytest.mark.asyncio
async def test_market_close_clears_category_and_store():
    store = _FakeStore()
    clock = _clock(open_now=True)
    task, policy = _make_task(scheduler_store=store, market_clock=clock)

    await task._tick()
    clock.is_market_operating_hours.return_value = False
    await task._tick()

    assert policy.sync_subscriptions.await_args_list[-2:] == [
        call([], CATEGORY, SubscriptionPriority.LOW, StreamingType.PROGRAM_TRADING),
        call([], PRICE_CATEGORY, SubscriptionPriority.LOW, StreamingType.UNIFIED_PRICE),
    ]
    assert store.load_keyed("program_capture_subscribed_codes") == ""
    assert task.get_progress()["synced_date"] is None

    # 이후 장외 tick 은 no-op (해지 반복 없음)
    await task._tick()
    assert policy.sync_subscriptions.await_count == 4


@pytest.mark.asyncio
async def test_tick_records_price_subscribed_candidates_for_the_day():
    # ES/호가 시계열은 PRICE 체결 틱에 무임승차하므로, 결손 종목이 '미구독'인지
    # '구독했는데 무틱'인지 사후 구분하려면 당일 PRICE 활성 종목을 남겨야 한다.
    store = _FakeStore()
    policy = MagicMock()
    policy.sync_subscriptions = AsyncMock()
    policy.get_status = MagicMock(
        return_value={"active_codes_price": ["005930", "999999"]}
    )
    task, _ = _make_task(scheduler_store=store, policy=policy)

    await task._tick()

    # 후보(005930/000660/035420) 와 PRICE 활성의 교집합만 기록 — 후보 밖 999999 는 제외
    assert store.load_keyed("capture_price_observed_20260703") == "005930"


@pytest.mark.asyncio
async def test_price_observation_accumulates_on_rotation_skipped_tick():
    # 로테이션 윈도우가 같아 재동기화를 건너뛰는 tick 에서도 관측은 누적돼야
    # 하루 중 잠깐만 구독된 종목이 '미구독' 으로 오분류되지 않는다.
    store = _FakeStore()
    policy = MagicMock()
    policy.sync_subscriptions = AsyncMock()
    policy.get_status = MagicMock(return_value={"active_codes_price": ["005930"]})
    task, _ = _make_task(scheduler_store=store, policy=policy)

    await task._tick()
    policy.get_status.return_value = {"active_codes_price": ["000660"]}
    await task._tick()

    assert store.load_keyed("capture_price_observed_20260703") == "000660,005930"


@pytest.mark.asyncio
async def test_restart_leftover_is_adopted_then_cleared_when_closed():
    # 크래시로 store 에 구독 잔재가 남은 채 장외에 재시작 →
    # 잔재를 카테고리로 재편입(sync)한 뒤 해지(sync [])해 pt_subscriptions 오염을 정리한다.
    store = _FakeStore()
    store.save_keyed("program_capture_subscribed_codes", "005930,000660")
    task, policy = _make_task(scheduler_store=store, market_clock=_clock(open_now=False))

    await task._tick()

    assert policy.sync_subscriptions.await_args_list == [
        call(
            ["005930", "000660"], CATEGORY,
            SubscriptionPriority.LOW, StreamingType.PROGRAM_TRADING,
        ),
        call([], CATEGORY, SubscriptionPriority.LOW, StreamingType.PROGRAM_TRADING),
        call([], PRICE_CATEGORY, SubscriptionPriority.LOW, StreamingType.UNIFIED_PRICE),
    ]
    assert store.load_keyed("program_capture_subscribed_codes") == ""


@pytest.mark.asyncio
async def test_adopt_prunes_orphan_pt_desired_with_stored_batch():
    # 재시작 시 store 에 남은 배치를 소유 목록으로 넘겨 나머지 PT desired 잔재를 정리한다.
    store = _FakeStore()
    store.save_keyed("program_capture_subscribed_codes", "005930,000660")
    repo = MagicMock()
    repo.get_desired.return_value = set()
    repo.get_pt_subscription_sources.return_value = {}
    repo.prune_orphan_pt_desired = AsyncMock(return_value=["035420"])
    task, _policy = _make_task(scheduler_store=store, streaming_stock_repo=repo)

    await task._tick()

    repo.prune_orphan_pt_desired.assert_awaited_once_with(["005930", "000660"])


@pytest.mark.asyncio
async def test_adopt_prune_failure_does_not_break_tick():
    # 잔재 정리 실패는 경고만 남기고 이후 동기화를 막지 않는다.
    repo = MagicMock()
    repo.get_desired.return_value = set()
    repo.get_pt_subscription_sources.return_value = {}
    repo.prune_orphan_pt_desired = AsyncMock(side_effect=Exception("db down"))
    task, policy = _make_task(streaming_stock_repo=repo)

    await task._tick()

    assert policy.sync_subscriptions.await_count == 2


@pytest.mark.asyncio
async def test_excludes_manual_pt_desired_and_caps_max_codes():
    # 수동 UI 로 이미 PT desired 인 종목은 제외 (해지/영속 상태 간섭 방지), cap 적용
    repo = MagicMock()
    repo.get_desired.return_value = {"000660"}
    repo.get_pt_subscription_sources.return_value = {"000660": "manual"}
    repo.prune_orphan_pt_desired = AsyncMock(return_value=[])
    universe_service = MagicMock()
    universe_service.get_watchlist = AsyncMock(
        return_value={"000660": MagicMock(), "035420": MagicMock(), "084370": MagicMock()}
    )
    task, policy = _make_task(
        streaming_stock_repo=repo,
        universe_service=universe_service,
        max_codes=2,
    )

    await task._tick()

    repo.get_desired.assert_called_with(StreamingType.PROGRAM_TRADING)
    assert policy.sync_subscriptions.await_args_list == [
        call(
            ["035420", "084370"], CATEGORY,
            SubscriptionPriority.LOW, StreamingType.PROGRAM_TRADING,
        ),
        call(
            ["035420", "084370"],
            PRICE_CATEGORY,
            SubscriptionPriority.LOW,
            StreamingType.UNIFIED_PRICE,
        ),
    ]


@pytest.mark.asyncio
async def test_program_sourced_pt_desired_is_still_a_capture_candidate():
    # program 출처(캡처 태스크가 남긴) desired 는 후보에서 빼지 않는다 —
    # 빼면 어떤 카테고리도 소유하지 않는 잔재로 굳어 영구 누적된다.
    repo = MagicMock()
    repo.get_desired.return_value = {"000660"}
    repo.get_pt_subscription_sources.return_value = {"000660": "program"}
    repo.prune_orphan_pt_desired = AsyncMock(return_value=[])
    universe_service = MagicMock()
    universe_service.get_watchlist = AsyncMock(
        return_value={"000660": MagicMock(), "035420": MagicMock()}
    )
    task, policy = _make_task(
        streaming_stock_repo=repo,
        universe_service=universe_service,
    )

    await task._tick()

    pt_call = policy.sync_subscriptions.await_args_list[0]
    assert sorted(pt_call.args[0]) == ["000660", "005930", "035420"]


@pytest.mark.asyncio
async def test_preferred_stocks_use_price_only_instead_of_program_subscription():
    """프로그램매매 tick이 없는 우선주는 PRICE 캡처로 분리한다."""
    universe_service = MagicMock()
    universe_service.get_watchlist = AsyncMock(
        return_value={
            "005935": MagicMock(),  # 삼성전자우
            "000660": MagicMock(),
            "051915": MagicMock(),  # LG화학우
        }
    )
    virtual_trade_service = MagicMock()
    virtual_trade_service.get_holds = MagicMock(
        return_value=[{"code": "005930"}, {"code": "000885"}]  # 한화우
    )
    task, policy = _make_task(
        universe_service=universe_service,
        virtual_trade_service=virtual_trade_service,
    )

    await task._tick()

    assert policy.sync_subscriptions.await_args_list == [
        call(
            ["005930", "000660"], CATEGORY,
            SubscriptionPriority.LOW, StreamingType.PROGRAM_TRADING,
        ),
        call(
            ["005930", "000885", "005935", "000660", "051915"], PRICE_CATEGORY,
            SubscriptionPriority.LOW, StreamingType.UNIFIED_PRICE,
        ),
    ]


@pytest.mark.asyncio
async def test_restart_does_not_restore_preferred_stock_subscription():
    """재시작 저장 목록에 남은 우선주도 PT 구독으로 복원하지 않는다."""
    store = _FakeStore()
    store.save_keyed("program_capture_subscribed_codes", "005930,005935")
    task, policy = _make_task(scheduler_store=store, market_clock=_clock(open_now=False))

    await task._tick()

    assert policy.sync_subscriptions.await_args_list == [
        call(
            ["005930"], CATEGORY,
            SubscriptionPriority.LOW, StreamingType.PROGRAM_TRADING,
        ),
        call([], CATEGORY, SubscriptionPriority.LOW, StreamingType.PROGRAM_TRADING),
        call([], PRICE_CATEGORY, SubscriptionPriority.LOW, StreamingType.UNIFIED_PRICE),
    ]


@pytest.mark.asyncio
async def test_non_business_day_does_not_subscribe():
    task, policy = _make_task(market_calendar_service=_mcs(business_day=False))

    await task._tick()

    policy.sync_subscriptions.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_policy_is_noop():
    task = ProgramCaptureSubscriptionTask(
        subscription_policy=None,
        market_clock=_clock(),
        logger=MagicMock(),
    )
    await task._tick()
    assert task.get_progress()["synced_date"] is None


def test_task_identity():
    task, _ = _make_task()
    assert task.task_name == "program_capture_subscription"
    assert task.get_progress()["running"] is False


@pytest.mark.asyncio
async def test_rotates_capture_batch_every_thirty_minutes():
    universe_service = MagicMock()
    universe_service.get_watchlist = AsyncMock(
        return_value={f"{code:06d}": MagicMock() for code in range(10, 80, 10)}
    )
    virtual_trade_service = MagicMock()
    virtual_trade_service.get_holds = MagicMock(return_value=[])
    clock = _clock()
    clock.get_current_kst_time.return_value = datetime(2026, 7, 3, 9, 0)
    task, policy = _make_task(
        universe_service=universe_service,
        virtual_trade_service=virtual_trade_service,
        market_clock=clock,
        max_codes=3,
    )

    await task._tick()
    clock.get_current_kst_time.return_value = datetime(2026, 7, 3, 9, 29)
    await task._tick()
    clock.get_current_kst_time.return_value = datetime(2026, 7, 3, 9, 30)
    await task._tick()

    assert policy.sync_subscriptions.await_args_list == [
        call(
            ["000010", "000020", "000030"], CATEGORY,
            SubscriptionPriority.LOW, StreamingType.PROGRAM_TRADING,
        ),
        call(
            ["000010", "000020", "000030"],
            PRICE_CATEGORY,
            SubscriptionPriority.LOW,
            StreamingType.UNIFIED_PRICE,
        ),
        call(
            ["000040", "000050", "000060"], CATEGORY,
            SubscriptionPriority.LOW, StreamingType.PROGRAM_TRADING,
        ),
        call(
            ["000040", "000050", "000060"],
            PRICE_CATEGORY,
            SubscriptionPriority.LOW,
            StreamingType.UNIFIED_PRICE,
        ),
    ]
    assert task.get_progress()["candidate_count"] == 7


@pytest.mark.asyncio
async def test_preferred_stock_in_rotation_batch_uses_price_only_subscription():
    universe_service = MagicMock()
    universe_service.get_watchlist = AsyncMock(
        return_value={"005935": MagicMock(), "000660": MagicMock()}
    )
    virtual_trade_service = MagicMock()
    virtual_trade_service.get_holds = MagicMock(return_value=[])
    task, policy = _make_task(
        universe_service=universe_service,
        virtual_trade_service=virtual_trade_service,
    )

    await task._tick()

    assert policy.sync_subscriptions.await_args_list == [
        call(
            ["000660"], CATEGORY,
            SubscriptionPriority.LOW, StreamingType.PROGRAM_TRADING,
        ),
        call(
            ["005935", "000660"], PRICE_CATEGORY,
            SubscriptionPriority.LOW, StreamingType.UNIFIED_PRICE,
        ),
    ]
