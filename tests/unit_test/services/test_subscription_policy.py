import pytest
import time
from unittest.mock import AsyncMock, MagicMock, patch
from services.subscription_policy import SubscriptionPolicy, SubscriptionPriority
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
    logger = MagicMock()
    logger.log_summary = MagicMock()
    logger.log_subscribe = MagicMock()
    logger.log_unsubscribe = MagicMock()
    return logger

@pytest.fixture
def mock_streaming_stock_repo():
    repo = MagicMock()
    repo.mark_desired = AsyncMock()
    repo.unmark_desired = AsyncMock()
    repo.mark_active = AsyncMock()
    repo.mark_inactive = AsyncMock()
    return repo

@pytest.fixture
def policy(mock_streaming, mock_stock_repo, mock_streaming_logger, mock_streaming_stock_repo):
    return SubscriptionPolicy(
        streaming_service=mock_streaming,
        stock_repo=mock_stock_repo,
        logger=MagicMock(),
        streaming_logger=mock_streaming_logger,
        streaming_stock_repo=mock_streaming_stock_repo,
    )

def test_init(mock_streaming, mock_stock_repo):
    """초기화 상태 검증"""
    p = SubscriptionPolicy(mock_streaming, mock_stock_repo)
    assert p._streaming == mock_streaming
    assert p._stock_repo == mock_stock_repo
    assert p.MAX_SUBSCRIPTIONS == 35
    assert len(p._active_codes) == 0
    assert len(p._refs) == 0

def test_clear_active_state(policy):
    """내부 활성 구독 집합 클리어 확인"""
    policy._active_codes = {"005930", "000660"}
    policy.clear_active_state()
    assert len(policy._active_codes) == 0
    policy._logger.debug.assert_called()

@pytest.mark.asyncio
async def test_add_remove_subscription(policy, mock_streaming_stock_repo):
    """구독 요청 및 해지 로직 검증"""
    # Add
    await policy.add_subscription("005930", SubscriptionPriority.HIGH, "portfolio")
    assert "005930" in policy._refs
    assert policy._refs["005930"]["portfolio"] == SubscriptionPriority.HIGH
    mock_streaming_stock_repo.mark_desired.assert_awaited_once_with("005930", StreamingType.UNIFIED_PRICE)
    
    # Remove
    await policy.remove_subscription("005930", "portfolio")
    assert "005930" not in policy._refs
    mock_streaming_stock_repo.unmark_desired.assert_awaited_once_with("005930", StreamingType.UNIFIED_PRICE)
    
@pytest.mark.asyncio
async def test_remove_subscription_not_in_refs(policy, mock_streaming_stock_repo):
    """_refs에 없는 종목 삭제 요청 처리 검증"""
    await policy.remove_subscription("999999", "portfolio")
    mock_streaming_stock_repo.unmark_desired.assert_not_called()

@pytest.mark.asyncio
async def test_remove_category(policy, mock_streaming_stock_repo):
    """특정 카테고리 일괄 구독 해지 로직 검증"""
    policy._refs = {
        "005930": {"cat1": SubscriptionPriority.HIGH, "cat2": SubscriptionPriority.MEDIUM},
        "000660": {"cat1": SubscriptionPriority.LOW},
        "035420": {"cat2": SubscriptionPriority.HIGH}
    }
    await policy.remove_category("cat1")
    
    # 005930은 cat2가 남아있어야 함
    assert "cat1" not in policy._refs["005930"]
    assert "cat2" in policy._refs["005930"]
    
    # 000660은 cat1만 있었으므로 삭제되어야 함
    assert "000660" not in policy._refs
    mock_streaming_stock_repo.unmark_desired.assert_awaited_with("000660", StreamingType.UNIFIED_PRICE)
    
    # 035420은 그대로 유지
    assert "035420" in policy._refs

@pytest.mark.asyncio
async def test_sync_subscriptions(policy, mock_streaming_stock_repo):
    """원자적 동기화(sync_subscriptions) 검증"""
    policy._refs = {
        "A": {"cat1": SubscriptionPriority.HIGH},
        "B": {"cat1": SubscriptionPriority.MEDIUM, "cat2": SubscriptionPriority.LOW}
    }
    
    await policy.sync_subscriptions(["B", "C"], "cat1", SubscriptionPriority.HIGH)
    
    # A는 cat1만 있었으므로 삭제
    assert "A" not in policy._refs
    mock_streaming_stock_repo.unmark_desired.assert_any_await("A", StreamingType.UNIFIED_PRICE)
    
    # B는 cat1의 priority 변경, cat2 유지
    assert "cat1" in policy._refs["B"]
    assert policy._refs["B"]["cat1"] == SubscriptionPriority.HIGH
    assert policy._refs["B"]["cat2"] == SubscriptionPriority.LOW
    
    # C는 신규 추가
    assert "C" in policy._refs
    mock_streaming_stock_repo.mark_desired.assert_any_await("C", StreamingType.UNIFIED_PRICE)

def test_is_streaming_and_get_status(policy):
    """스트리밍 여부 판별 및 구독 현황 조회 로직 검증"""
    policy._active_codes = {"005930"}
    policy._refs = {
        "005930": {"portfolio": SubscriptionPriority.HIGH},
        "000660": {"strategy": SubscriptionPriority.MEDIUM},
        "035420": {"ui": SubscriptionPriority.LOW}
    }
    
    assert policy.is_streaming("005930") is True
    assert policy.is_streaming("000660") is False
    
    status = policy.get_status()
    assert status["active_count"] == 1
    assert status["pending_count"] == 3
    assert "005930" in status["pending_by_priority"]["HIGH"]
    assert "000660" in status["pending_by_priority"]["MEDIUM"]
    assert "035420" in status["pending_by_priority"]["LOW"]

@pytest.mark.asyncio
async def test_rebalance_drops_over_max_limit(policy):
    """MAX_SUBSCRIPTIONS 초과 시 낮은 우선순위 대기 및 경고 로깅 검증"""
    policy.MAX_SUBSCRIPTIONS = 2
    policy._refs = {
        "A": {"c": SubscriptionPriority.LOW},
        "B": {"c": SubscriptionPriority.LOW},
        "C": {"c": SubscriptionPriority.HIGH}
    }
    await policy._rebalance()
    
    assert "C" in policy._active_codes  # HIGH 우선 처리
    assert len(policy._active_codes) == 2
    policy._logger.warning.assert_called()

@pytest.mark.asyncio
async def test_rebalance_throttle_summary_log(policy, mock_streaming_logger):
    """동시다발적인 rebalance 호출 시 log_summary가 스로틀(throttle) 처리되는지 검증"""
    policy._refs = {"A": {"c": SubscriptionPriority.HIGH}}
    
    with patch("time.monotonic", side_effect=[10.0, 10.5]):
        await policy._rebalance()
        assert mock_streaming_logger.log_summary.call_count == 1
        
        policy._refs["B"] = {"c": SubscriptionPriority.HIGH}
        await policy._rebalance()
        # 0.5초만 지났으므로 _SUMMARY_THROTTLE_SEC(2.0) 조건을 만족하지 않아 호출 횟수가 늘어나면 안 됨
        assert mock_streaming_logger.log_summary.call_count == 1

@pytest.mark.asyncio
async def test_do_subscribe_success_and_fail(policy, mock_streaming, mock_stock_repo, mock_streaming_stock_repo, mock_streaming_logger):
    """_do_subscribe 성공, 실패 시 동작 검증"""
    # 1. Success case
    mock_streaming.subscribe_unified_price.return_value = True
    await policy._do_subscribe("A")
    
    assert "A" in policy._active_codes
    mock_stock_repo.mark_streaming.assert_called_with("A")
    mock_streaming_stock_repo.mark_active.assert_awaited_with("A", StreamingType.UNIFIED_PRICE)
    mock_streaming_logger.log_subscribe.assert_called_once()
    
    # 2. Failure case (False returned)
    mock_streaming.subscribe_unified_price.return_value = False
    await policy._do_subscribe("B")
    assert "B" not in policy._active_codes
    policy._logger.warning.assert_called()

@pytest.mark.asyncio
async def test_do_subscribe_exception(policy, mock_streaming):
    mock_streaming.subscribe_unified_price.side_effect = Exception("Conn Error")
    await policy._do_subscribe("A")
    policy._logger.error.assert_called()

@pytest.mark.asyncio
async def test_do_unsubscribe_success_and_exception(policy, mock_streaming, mock_stock_repo, mock_streaming_stock_repo, mock_streaming_logger):
    """_do_unsubscribe 성공 및 예외 처리 검증"""
    policy._active_codes.add("A")
    await policy._do_unsubscribe("A")
    
    assert "A" not in policy._active_codes
    mock_streaming.unsubscribe_unified_price.assert_awaited_with("A")
    
    policy._active_codes.add("B")
    mock_streaming.unsubscribe_unified_price.side_effect = Exception("Unsub Error")
    await policy._do_unsubscribe("B")
    policy._logger.error.assert_called()