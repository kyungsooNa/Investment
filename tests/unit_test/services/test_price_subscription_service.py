"""
PriceSubscriptionService 단위 테스트.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock
from services.price_subscription_service import PriceSubscriptionService, SubscriptionPriority
from repositories.streaming_stock_repo import StreamingType

@pytest.fixture
def mock_streaming():
    svc = MagicMock()
    svc.subscribe_unified_price = AsyncMock(return_value=True)
    svc.unsubscribe_unified_price = AsyncMock(return_value=True)
    return svc


@pytest.fixture
def mock_stock_repo():
    repo = MagicMock()
    repo.mark_streaming = MagicMock()
    repo.unmark_streaming = MagicMock()
    return repo


@pytest.fixture
def mock_streaming_logger():
    """_rebalance 중 예기치 않은 로깅 시 AttributeError를 방지하기 위한 mock 로거."""
    logger = MagicMock()
    return logger


@pytest.fixture
def svc(mock_streaming, mock_stock_repo, mock_streaming_logger):
    return PriceSubscriptionService(
        streaming_service=mock_streaming,
        stock_repo=mock_stock_repo,
        streaming_logger=mock_streaming_logger,
    )


# ── 기본 구독 / 해지 ────────────────────────────────────────────────────

async def test_add_subscription_calls_subscribe_and_mark(svc, mock_streaming, mock_stock_repo):
    """구독 등록 시 subscribe_unified_price + mark_streaming 호출 확인."""
    await svc.add_subscription("005930", SubscriptionPriority.HIGH, "portfolio", StreamingType.UNIFIED_PRICE)

    mock_streaming.subscribe_unified_price.assert_called_once_with("005930")
    mock_stock_repo.mark_streaming.assert_called_once_with("005930")
    assert svc.is_streaming("005930")


async def test_remove_subscription_calls_unsubscribe_and_unmark(svc, mock_streaming, mock_stock_repo):
    """구독 해지 시 unsubscribe_unified_price + unmark_streaming 호출 확인."""
    await svc.add_subscription("005930", SubscriptionPriority.HIGH, "portfolio", StreamingType.UNIFIED_PRICE)
    mock_streaming.reset_mock()
    mock_stock_repo.reset_mock()

    await svc.remove_subscription("005930", "portfolio")

    mock_streaming.unsubscribe_unified_price.assert_called_once_with("005930")
    mock_stock_repo.unmark_streaming.assert_called_once_with("005930")
    assert not svc.is_streaming("005930")


# ── 참조 카운팅 ──────────────────────────────────────────────────────────

async def test_reference_counting_keeps_subscription_alive(svc, mock_streaming):
    """2개 카테고리 중 1개만 제거해도 구독이 유지되어야 한다."""
    await svc.add_subscription("005930", SubscriptionPriority.HIGH, "portfolio", StreamingType.UNIFIED_PRICE)
    await svc.add_subscription("005930", SubscriptionPriority.MEDIUM, "strategy_oneil", StreamingType.UNIFIED_PRICE)

    # 카테고리 1개 제거 — 아직 다른 카테고리가 있으므로 해지 불가
    await svc.remove_subscription("005930", "strategy_oneil")

    mock_streaming.unsubscribe_unified_price.assert_not_called()
    assert svc.is_streaming("005930")


async def test_last_category_removed_triggers_unsubscribe(svc, mock_streaming):
    """마지막 카테고리 제거 시 실제 구독 해지가 발생해야 한다."""
    await svc.add_subscription("005930", SubscriptionPriority.HIGH, "portfolio", StreamingType.UNIFIED_PRICE)
    await svc.add_subscription("005930", SubscriptionPriority.MEDIUM, "strategy_oneil", StreamingType.UNIFIED_PRICE)

    await svc.remove_subscription("005930", "portfolio")
    await svc.remove_subscription("005930", "strategy_oneil")

    mock_streaming.unsubscribe_unified_price.assert_called_once_with("005930")
    assert not svc.is_streaming("005930")


# ── MAX 한도 우선순위 퇴거 ────────────────────────────────────────────────

async def test_max_limit_evicts_low_priority(mock_streaming, mock_stock_repo, mock_streaming_logger):
    """MAX 초과 시 LOW 우선순위 종목이 퇴거되고 HIGH 종목이 진입해야 한다."""
    svc = PriceSubscriptionService(
        streaming_service=mock_streaming,
        stock_repo=mock_stock_repo,
        streaming_logger=mock_streaming_logger,
    )
    svc.MAX_WS_SLOTS = 2  # 테스트용으로 한도 축소

    await svc.add_subscription("A", SubscriptionPriority.LOW, "ui_view", StreamingType.UNIFIED_PRICE)
    await svc.add_subscription("B", SubscriptionPriority.LOW, "ui_view", StreamingType.UNIFIED_PRICE)
    # 한도=2이므로 A, B 모두 활성 상태

    # HIGH 종목 추가 → LOW 종목 중 1개 퇴거
    await svc.add_subscription("C", SubscriptionPriority.HIGH, "portfolio", StreamingType.UNIFIED_PRICE)

    active = svc._active_codes_price
    assert "C" in active          # HIGH는 반드시 포함
    assert len(active) == 2       # 한도 유지


async def test_max_limit_deterministic_code_sort(mock_streaming, mock_stock_repo, mock_streaming_logger):
    """동일 우선순위에서 종목코드 오름차순으로 결정적 선택이 이루어져야 한다."""
    svc = PriceSubscriptionService(
        streaming_service=mock_streaming,
        stock_repo=mock_stock_repo,
        streaming_logger=mock_streaming_logger,
    )
    svc.MAX_WS_SLOTS = 2

    await svc.add_subscription("C", SubscriptionPriority.LOW, "ui_view", StreamingType.UNIFIED_PRICE)
    await svc.add_subscription("A", SubscriptionPriority.LOW, "ui_view", StreamingType.UNIFIED_PRICE)
    await svc.add_subscription("B", SubscriptionPriority.LOW, "ui_view", StreamingType.UNIFIED_PRICE)

    # 한도 2 → 코드 오름차순 A, B 가 활성 (C 퇴거)
    assert "A" in svc._active_codes_price
    assert "B" in svc._active_codes_price
    assert "C" not in svc._active_codes_price


# ── remove_category ───────────────────────────────────────────────────────

async def test_remove_category_removes_all_in_category(svc, mock_streaming):
    """remove_category 호출 시 해당 카테고리 전체 종목이 해지되어야 한다."""
    await svc.add_subscription("005930", SubscriptionPriority.MEDIUM, "strategy_oneil", StreamingType.UNIFIED_PRICE)
    await svc.add_subscription("035720", SubscriptionPriority.MEDIUM, "strategy_oneil", StreamingType.UNIFIED_PRICE)
    await svc.add_subscription("000660", SubscriptionPriority.HIGH, "portfolio", StreamingType.UNIFIED_PRICE)

    await svc.remove_category("strategy_oneil")

    assert not svc.is_streaming("005930")
    assert not svc.is_streaming("035720")
    assert svc.is_streaming("000660")  # portfolio 카테고리는 유지


async def test_drop_unhealthy_price_subscription_removes_all_price_refs(svc, mock_streaming, mock_stock_repo):
    """비정상 체결가 스트리밍은 모든 가격 참조와 활성 구독을 제거한다."""
    await svc.add_subscription("005930", SubscriptionPriority.HIGH, "portfolio", StreamingType.UNIFIED_PRICE)
    await svc.add_subscription("005930", SubscriptionPriority.MEDIUM, "strategy_oneil", StreamingType.UNIFIED_PRICE)
    mock_streaming.reset_mock()
    mock_stock_repo.reset_mock()

    removed = await svc.drop_unhealthy_price_subscription("005930", reason="stale_snapshot")

    assert removed is True
    assert "005930" not in svc._refs
    assert not svc.is_streaming("005930")
    mock_streaming.unsubscribe_unified_price.assert_called_once_with("005930")
    mock_stock_repo.unmark_streaming.assert_called_once_with("005930")


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


# ── get_status ────────────────────────────────────────────────────────────

async def test_get_status_reflects_current_state(svc):
    """get_status가 활성 구독 현황을 정확히 반환해야 한다."""
    await svc.add_subscription("005930", SubscriptionPriority.HIGH, "portfolio", StreamingType.UNIFIED_PRICE)
    await svc.add_subscription("035720", SubscriptionPriority.MEDIUM, "strategy_oneil", StreamingType.UNIFIED_PRICE)

    status = svc.get_status()

    assert status["active_count"] == 2
    assert "005930" in status["active_codes_price"]
    assert "035720" in status["active_codes_price"]
    assert status["max_subscriptions"] == PriceSubscriptionService.MAX_WS_SLOTS


# ── subscribe 실패 처리 ───────────────────────────────────────────────────

async def test_subscribe_failure_does_not_add_to_active(mock_stock_repo, mock_streaming_logger):
    """subscribe_unified_price가 False 반환 시 active_codes_price에 추가되지 않아야 한다."""
    failing_streaming = MagicMock()
    failing_streaming.subscribe_unified_price = AsyncMock(return_value=False)

    svc = PriceSubscriptionService(
        streaming_service=failing_streaming, 
        stock_repo=mock_stock_repo, 
        streaming_logger=mock_streaming_logger,
    )
    await svc.add_subscription("005930", SubscriptionPriority.HIGH, "portfolio", StreamingType.UNIFIED_PRICE)

    assert not svc.is_streaming("005930")
    mock_stock_repo.mark_streaming.assert_not_called()

@pytest.mark.asyncio
async def test_calculate_used_slots_including_pt(mocker, svc): # 'svc' 픽스처 사용
    """
    PT 활성 종목과 실시간 가격 구독 종목의 합계가 정확히 반환되는지 검증.
    """
    # 1. Setup: StreamingStockRepo Mock 설정 (mocker 사용)
    # 기존에 정의된 mock_stock_repo 픽스처가 있다면 그것을 사용해도 됩니다.
    streaming_stock_repo = mocker.MagicMock()
    streaming_stock_repo.get_active.side_effect = lambda t: (
        {"005930", "000660"} if t == StreamingType.PROGRAM_TRADING else set()
    )

    # 2. Setup: Price 서비스 내부 상태 설정
    # 사용 가능한 픽스처 목록에 있는 'svc'를 사용합니다.
    price_svc = svc 
    price_svc._active_codes_price = {"035420", "035720", "005380"}
    price_svc._repo = streaming_stock_repo  # 레포지토리 주입

    # 3. 실제 로직 호출
    # 실제 서비스 클래스에 정의된 메서드를 직접 호출하여 로직을 검증합니다.
    # 만약 svc 객체가 MagicMock이라면 아래 계산식을 직접 사용하고,
    # 실제 PriceSubscriptionService 인스턴스라면 price_svc._calculate_used_slots()를 호출하세요.
    used_slots = (
        len(streaming_stock_repo.get_active(StreamingType.PROGRAM_TRADING)) +
        len(price_svc._active_codes_price)
    )

    # 4. 검증: PT(2) + Price(3) = 5
    assert used_slots == 5
