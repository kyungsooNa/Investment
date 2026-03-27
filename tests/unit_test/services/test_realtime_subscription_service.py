"""
RealtimeSubscriptionService 단위 테스트.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock
from services.realtime_subscription_service import RealtimeSubscriptionService, SubscriptionPriority


@pytest.fixture
def mock_streaming():
    svc = MagicMock()
    svc.subscribe_unified_price = AsyncMock(return_value=True)
    svc.unsubscribe_unified_price = AsyncMock(return_value=True)
    svc.subscribe_program_trading = AsyncMock(return_value=True)
    svc.unsubscribe_program_trading = AsyncMock(return_value=True)
    return svc


@pytest.fixture
def mock_stock_repo():
    repo = MagicMock()
    repo.mark_streaming = MagicMock()
    repo.unmark_streaming = MagicMock()
    return repo


@pytest.fixture
def svc(mock_streaming, mock_stock_repo):
    return RealtimeSubscriptionService(
        streaming_service=mock_streaming,
        stock_repo=mock_stock_repo,
    )


# ── 기본 구독 / 해지 ────────────────────────────────────────────────────

async def test_add_subscription_calls_subscribe_and_mark(svc, mock_streaming, mock_stock_repo):
    """구독 등록 시 subscribe_unified_price + mark_streaming 호출 확인."""
    await svc.add_subscription("005930", SubscriptionPriority.HIGH, "portfolio")

    mock_streaming.subscribe_unified_price.assert_called_once_with("005930")
    mock_stock_repo.mark_streaming.assert_called_once_with("005930")
    assert svc.is_streaming("005930")


async def test_remove_subscription_calls_unsubscribe_and_unmark(svc, mock_streaming, mock_stock_repo):
    """구독 해지 시 unsubscribe_unified_price + unmark_streaming 호출 확인."""
    await svc.add_subscription("005930", SubscriptionPriority.HIGH, "portfolio")
    mock_streaming.reset_mock()
    mock_stock_repo.reset_mock()

    await svc.remove_subscription("005930", "portfolio")

    mock_streaming.unsubscribe_unified_price.assert_called_once_with("005930")
    mock_stock_repo.unmark_streaming.assert_called_once_with("005930")
    assert not svc.is_streaming("005930")


# ── 참조 카운팅 ──────────────────────────────────────────────────────────

async def test_reference_counting_keeps_subscription_alive(svc, mock_streaming):
    """2개 카테고리 중 1개만 제거해도 구독이 유지되어야 한다."""
    await svc.add_subscription("005930", SubscriptionPriority.HIGH, "portfolio")
    await svc.add_subscription("005930", SubscriptionPriority.MEDIUM, "strategy_oneil")

    # 카테고리 1개 제거 — 아직 다른 카테고리가 있으므로 해지 불가
    await svc.remove_subscription("005930", "strategy_oneil")

    mock_streaming.unsubscribe_unified_price.assert_not_called()
    assert svc.is_streaming("005930")


async def test_last_category_removed_triggers_unsubscribe(svc, mock_streaming):
    """마지막 카테고리 제거 시 실제 구독 해지가 발생해야 한다."""
    await svc.add_subscription("005930", SubscriptionPriority.HIGH, "portfolio")
    await svc.add_subscription("005930", SubscriptionPriority.MEDIUM, "strategy_oneil")

    await svc.remove_subscription("005930", "portfolio")
    await svc.remove_subscription("005930", "strategy_oneil")

    mock_streaming.unsubscribe_unified_price.assert_called_once_with("005930")
    assert not svc.is_streaming("005930")


# ── MAX 한도 우선순위 퇴거 ────────────────────────────────────────────────

async def test_max_limit_evicts_low_priority(mock_streaming, mock_stock_repo):
    """MAX 초과 시 LOW 우선순위 종목이 퇴거되고 HIGH 종목이 진입해야 한다."""
    svc = RealtimeSubscriptionService(
        streaming_service=mock_streaming,
        stock_repo=mock_stock_repo,
    )
    svc.MAX_SUBSCRIPTIONS = 2  # 테스트용으로 한도 축소

    await svc.add_subscription("A", SubscriptionPriority.LOW, "ui_view")
    await svc.add_subscription("B", SubscriptionPriority.LOW, "ui_view")
    # 한도=2이므로 A, B 모두 활성 상태

    # HIGH 종목 추가 → LOW 종목 중 1개 퇴거
    await svc.add_subscription("C", SubscriptionPriority.HIGH, "portfolio")

    active = svc._active_codes
    assert "C" in active          # HIGH는 반드시 포함
    assert len(active) == 2       # 한도 유지


async def test_max_limit_deterministic_code_sort(mock_streaming, mock_stock_repo):
    """동일 우선순위에서 종목코드 오름차순으로 결정적 선택이 이루어져야 한다."""
    svc = RealtimeSubscriptionService(
        streaming_service=mock_streaming,
        stock_repo=mock_stock_repo,
    )
    svc.MAX_SUBSCRIPTIONS = 2

    await svc.add_subscription("C", SubscriptionPriority.LOW, "ui_view")
    await svc.add_subscription("A", SubscriptionPriority.LOW, "ui_view")
    await svc.add_subscription("B", SubscriptionPriority.LOW, "ui_view")

    # 한도 2 → 코드 오름차순 A, B 가 활성 (C 퇴거)
    assert "A" in svc._active_codes
    assert "B" in svc._active_codes
    assert "C" not in svc._active_codes


async def test_pt_slots_reduce_available_price_slots(mock_streaming, mock_stock_repo):
    """PT 구독 시 해당 슬롯만큼 체결가 구독 한도가 줄어야 한다."""
    svc = RealtimeSubscriptionService(
        streaming_service=mock_streaming,
        stock_repo=mock_stock_repo,
    )
    svc.MAX_SUBSCRIPTIONS = 3  # 전체 한도 3

    # PT 구독 1개 →
    #  - H0STPGM0 1슬롯 (len(_pt_codes)=1)
    #  - available_price_slots = 3 - 1 = 2
    #  - 005930 CRITICAL 가격 구독도 price 풀에서 1슬롯 소비
    # → 비-PT LOW 종목은 1슬롯만 남음 (A만 활성)
    await svc.add_program_trading("005930")
    mock_streaming.reset_mock()

    await svc.add_subscription("A", SubscriptionPriority.LOW, "ui_view")
    await svc.add_subscription("B", SubscriptionPriority.LOW, "ui_view")  # 한도 초과 → 탈락
    await svc.add_subscription("C", SubscriptionPriority.LOW, "ui_view")  # 한도 초과 → 탈락

    # 005930(CRITICAL) + A(LOW) = 2 슬롯 → B, C 탈락
    assert "005930" in svc._active_codes
    assert "A" in svc._active_codes
    assert "B" not in svc._active_codes
    assert "C" not in svc._active_codes


# ── remove_category ───────────────────────────────────────────────────────

async def test_remove_category_removes_all_in_category(svc, mock_streaming):
    """remove_category 호출 시 해당 카테고리 전체 종목이 해지되어야 한다."""
    await svc.add_subscription("005930", SubscriptionPriority.MEDIUM, "strategy_oneil")
    await svc.add_subscription("035720", SubscriptionPriority.MEDIUM, "strategy_oneil")
    await svc.add_subscription("000660", SubscriptionPriority.HIGH, "portfolio")

    await svc.remove_category("strategy_oneil")

    assert not svc.is_streaming("005930")
    assert not svc.is_streaming("035720")
    assert svc.is_streaming("000660")  # portfolio 카테고리는 유지


# ── sync_subscriptions ────────────────────────────────────────────────────

async def test_sync_subscriptions_atomic_replace(svc, mock_streaming):
    """sync_subscriptions는 이전 목록을 완전히 교체해야 한다."""
    await svc.sync_subscriptions(["A", "B"], "strategy_oneil", SubscriptionPriority.MEDIUM)
    assert svc.is_streaming("A") and svc.is_streaming("B")

    # B 제거, C 추가
    await svc.sync_subscriptions(["A", "C"], "strategy_oneil", SubscriptionPriority.MEDIUM)

    assert svc.is_streaming("A")
    assert not svc.is_streaming("B")
    assert svc.is_streaming("C")


async def test_sync_subscriptions_calls_rebalance_once(svc, mock_streaming):
    """sync_subscriptions는 _rebalance를 1회만 호출해야 한다 (subscribe 횟수로 검증)."""
    await svc.sync_subscriptions(["X", "Y", "Z"], "strategy_oneil", SubscriptionPriority.MEDIUM)

    # X, Y, Z 각 1번씩 총 3번 subscribe 호출 (rebalance 1회 → 3번 do_subscribe)
    assert mock_streaming.subscribe_unified_price.call_count == 3


# ── PT 구독 ───────────────────────────────────────────────────────────────

async def test_add_program_trading_calls_subscribe_pt_and_price(svc, mock_streaming):
    """PT 구독 등록 시 subscribe_program_trading + 체결가 구독 호출 확인."""
    await svc.add_program_trading("005930")

    mock_streaming.subscribe_program_trading.assert_called_once_with("005930")
    # 체결가도 CRITICAL로 등록됨 (subscribe_unified_price 호출)
    mock_streaming.subscribe_unified_price.assert_called_once_with("005930")
    assert "005930" in svc._pt_codes
    assert svc.is_streaming("005930")


async def test_add_program_trading_duplicate_skipped(svc, mock_streaming):
    """이미 PT 구독 중인 종목은 중복 구독하지 않아야 한다."""
    await svc.add_program_trading("005930")
    mock_streaming.reset_mock()

    result = await svc.add_program_trading("005930")

    assert result is True
    mock_streaming.subscribe_program_trading.assert_not_called()


async def test_remove_program_trading_unsubscribes_both(svc, mock_streaming):
    """PT 구독 해지 시 H0STPGM0 해지 + 체결가 ref-count 감소."""
    await svc.add_program_trading("005930")
    mock_streaming.reset_mock()

    await svc.remove_program_trading("005930")

    mock_streaming.unsubscribe_program_trading.assert_called_once_with("005930")
    assert "005930" not in svc._pt_codes


async def test_get_program_trading_codes(svc):
    """get_program_trading_codes가 구독 중인 PT 종목 목록을 반환해야 한다."""
    await svc.add_program_trading("005930")
    await svc.add_program_trading("000660")

    codes = svc.get_program_trading_codes()

    assert codes == ["000660", "005930"]  # 정렬됨


# ── get_status ────────────────────────────────────────────────────────────

async def test_get_status_reflects_current_state(svc):
    """get_status가 활성 구독 현황을 정확히 반환해야 한다."""
    await svc.add_subscription("005930", SubscriptionPriority.HIGH, "portfolio")
    await svc.add_subscription("035720", SubscriptionPriority.MEDIUM, "strategy_oneil")

    status = svc.get_status()

    assert status["price_count"] == 2
    assert status["pt_count"] == 0
    assert status["total_count"] == 2
    assert "005930" in status["price_codes"]
    assert "035720" in status["price_codes"]
    assert status["max_subscriptions"] == RealtimeSubscriptionService.MAX_SUBSCRIPTIONS

    high_codes = [i["code"] for i in status["by_priority"]["HIGH"]]
    medium_codes = [i["code"] for i in status["by_priority"]["MEDIUM"]]
    assert "005930" in high_codes
    assert "035720" in medium_codes

    # active 여부 및 subscribed_at 포함 확인
    high_item = next(i for i in status["by_priority"]["HIGH"] if i["code"] == "005930")
    assert high_item["active"] is True
    assert high_item["subscribed_at"] is not None


# ── subscribe 실패 처리 ───────────────────────────────────────────────────

async def test_subscribe_failure_does_not_add_to_active(mock_stock_repo):
    """subscribe_unified_price가 False 반환 시 active_codes에 추가되지 않아야 한다."""
    failing_streaming = MagicMock()
    failing_streaming.subscribe_unified_price = AsyncMock(return_value=False)
    failing_streaming.subscribe_program_trading = AsyncMock(return_value=True)
    failing_streaming.unsubscribe_program_trading = AsyncMock(return_value=True)
    failing_streaming.unsubscribe_unified_price = AsyncMock(return_value=True)

    svc = RealtimeSubscriptionService(streaming_service=failing_streaming, stock_repo=mock_stock_repo)
    await svc.add_subscription("005930", SubscriptionPriority.HIGH, "portfolio")

    assert not svc.is_streaming("005930")
    mock_stock_repo.mark_streaming.assert_not_called()
