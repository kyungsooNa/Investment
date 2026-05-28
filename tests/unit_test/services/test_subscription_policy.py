import pytest
import time
from unittest.mock import AsyncMock, MagicMock, call, patch
from services.subscription_policy import SubscriptionPolicy, SubscriptionPriority
from repositories.streaming_stock_repo import StreamingType

@pytest.fixture
def mock_streaming():
    svc = MagicMock()
    svc.connect_websocket = AsyncMock(return_value=True)
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
def mock_streaming_logger():
    logger = MagicMock()
    logger.log_summary = MagicMock()
    logger.log_subscribe = MagicMock()
    logger.log_unsubscribe = MagicMock()
    # 새롭게 추가된 구체화된 로깅 메서드들 Mocking
    logger.log_clear_active_state = MagicMock()
    logger.log_add_subscription_rejection = MagicMock()
    logger.log_subscribe_pending = MagicMock()
    logger.log_subscribe_failure = MagicMock()
    logger.log_unsubscribe_failure = MagicMock()
    logger.debug = MagicMock()
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
def mock_market_calendar():
    calendar = MagicMock()
    calendar.is_market_open_now = AsyncMock(return_value=True) # 기본적으로 장중으로 모사
    return calendar

@pytest.fixture
def policy(mock_streaming, mock_stock_repo, mock_streaming_logger, mock_streaming_stock_repo, mock_market_calendar):
    return SubscriptionPolicy(
        streaming_service=mock_streaming,
        stock_repo=mock_stock_repo,
        logger=MagicMock(),
        streaming_logger=mock_streaming_logger,
        streaming_stock_repo=mock_streaming_stock_repo,
        market_calendar=mock_market_calendar
    )

def test_init(mock_streaming, mock_stock_repo, policy):
    """초기화 상태 검증"""
    assert policy._streaming == mock_streaming
    assert policy._stock_repo == mock_stock_repo
    assert policy.MAX_WS_SLOTS == 40
    assert len(policy._active_codes_price) == 0
    assert len(policy._active_codes_pt) == 0
    assert len(policy._refs) == 0

def test_clear_active_state(policy, mock_streaming_logger):
    """내부 활성 구독 집합 클리어 확인"""
    policy._active_codes_price = {"005930"}
    policy._active_codes_pt = {"000660"}
    policy.clear_active_state()
    assert len(policy._active_codes_price) == 0
    assert len(policy._active_codes_pt) == 0
    # 구체화된 로깅 메서드 호출 검증
    assert mock_streaming_logger.log_clear_active_state.call_count == 2

@pytest.mark.asyncio
async def test_add_remove_subscription(policy, mock_streaming_stock_repo):
    """구독 요청 및 해지 로직 검증 (_calculate_used_slots 모킹 필요)"""
    policy._calculate_used_slots = MagicMock(return_value=0)
    
    # Add
    result = await policy.add_subscription("005930", SubscriptionPriority.HIGH, "portfolio", StreamingType.UNIFIED_PRICE)
    assert result is True
    assert "005930" in policy._refs
    assert policy._refs["005930"]["portfolio"]["priority"] == SubscriptionPriority.HIGH
    assert policy._refs["005930"]["portfolio"]["type"] == StreamingType.UNIFIED_PRICE
    
    # Remove
    await policy.remove_subscription("005930", "portfolio")
    assert "005930" not in policy._refs
    mock_streaming_stock_repo.unmark_desired.assert_awaited_once_with("005930", StreamingType.UNIFIED_PRICE)

@pytest.mark.asyncio
async def test_add_subscription_critical_rejection(policy, mock_streaming_logger):
    """프로그램 매매(CRITICAL) 슬롯 부족 시 거절(Rejection) 동작 검증"""
    policy._calculate_used_slots = MagicMock(return_value=39) # 남은 슬롯 1개 (PT는 2슬롯 필요)
    
    result = await policy.add_subscription("000660", SubscriptionPriority.CRITICAL, "pt_req", StreamingType.PROGRAM_TRADING)
    assert result is False
    assert "000660" not in policy._refs
    # 구체화된 Rejection 로깅 검증
    mock_streaming_logger.log_add_subscription_rejection.assert_called()
    
@pytest.mark.asyncio
async def test_remove_subscription_not_in_refs(policy, mock_streaming_stock_repo):
    """_refs에 없는 종목 삭제 요청 처리 검증"""
    await policy.remove_subscription("999999", "portfolio")
    mock_streaming_stock_repo.unmark_desired.assert_not_called()

@pytest.mark.asyncio
async def test_remove_category(policy, mock_streaming_stock_repo):
    """특정 카테고리 일괄 구독 해지 로직 검증"""
    policy._refs = {
        "005930": {
            "cat1": {"priority": SubscriptionPriority.HIGH, "type": StreamingType.UNIFIED_PRICE}, 
            "cat2": {"priority": SubscriptionPriority.MEDIUM, "type": StreamingType.UNIFIED_PRICE}
        },
        "000660": {
            "cat1": {"priority": SubscriptionPriority.LOW, "type": StreamingType.UNIFIED_PRICE}
        },
        "035420": {
            "cat2": {"priority": SubscriptionPriority.HIGH, "type": StreamingType.UNIFIED_PRICE}
        }
    }
    await policy.remove_category("cat1")
    
    assert "cat1" not in policy._refs["005930"]
    assert "cat2" in policy._refs["005930"]
    assert "000660" not in policy._refs
    assert "035420" in policy._refs

@pytest.mark.asyncio
async def test_sync_subscriptions(policy, mock_streaming_stock_repo):
    """원자적 동기화(sync_subscriptions) 검증"""
    policy._refs = {
        "A": {"cat1": {"priority": SubscriptionPriority.HIGH, "type": StreamingType.UNIFIED_PRICE}},
        "B": {
            "cat1": {"priority": SubscriptionPriority.MEDIUM, "type": StreamingType.UNIFIED_PRICE}, 
            "cat2": {"priority": SubscriptionPriority.LOW, "type": StreamingType.UNIFIED_PRICE}
        }
    }
    
    await policy.sync_subscriptions(["B", "C"], "cat1", SubscriptionPriority.HIGH)
    
    assert "A" not in policy._refs
    assert "cat1" in policy._refs["B"]
    assert policy._refs["B"]["cat1"]["priority"] == SubscriptionPriority.HIGH
    assert policy._refs["B"]["cat2"]["priority"] == SubscriptionPriority.LOW
    assert "C" in policy._refs
    assert policy._refs["C"]["cat1"]["priority"] == SubscriptionPriority.HIGH

def test_is_streaming_and_get_status(policy):
    """스트리밍 여부 판별 및 구독 현황 조회 로직 검증"""
    policy._active_codes_price = {"005930"}
    policy._active_codes_pt = {"000660"}
    policy._refs = {
        "005930": {"portfolio": {"priority": SubscriptionPriority.HIGH, "type": StreamingType.UNIFIED_PRICE}},
        "000660": {"strategy": {"priority": SubscriptionPriority.CRITICAL, "type": StreamingType.PROGRAM_TRADING}},
        "035420": {"ui": {"priority": SubscriptionPriority.LOW, "type": StreamingType.UNIFIED_PRICE}}
    }
    
    assert policy.is_streaming("005930") is True
    assert policy.is_streaming("000660") is True
    assert policy.is_streaming("035420") is False
    
    status = policy.get_status()
    assert status["active_count"] == 2
    assert status["pending_count"] == 3
    assert "005930" in status["active_codes_price"]
    assert "000660" in status["active_codes_pt"]

@pytest.mark.asyncio
async def test_rebalance_slot_allocation(policy, mock_streaming_logger):
    """슬롯 한도 초과 시 타입에 따른 할당 및 Dropped 로직 검증"""
    policy.MAX_WS_SLOTS = 3 # PT 1개(2슬롯) + Price 1개(1슬롯) 들어가면 꽉 참
    policy._refs = {
        "PT_CODE": {"pt": {"priority": SubscriptionPriority.CRITICAL, "type": StreamingType.PROGRAM_TRADING}},
        "PRICE_1": {"p1": {"priority": SubscriptionPriority.HIGH, "type": StreamingType.UNIFIED_PRICE}},
        "PRICE_2": {"p2": {"priority": SubscriptionPriority.LOW, "type": StreamingType.UNIFIED_PRICE}} # 슬롯 부족으로 밀려나야 함
    }
    
    await policy._rebalance()
    
    # PT_CODE(2) + PRICE_1(1) = 3슬롯 사용 완료
    assert "PT_CODE" in policy._active_codes_pt
    assert "PRICE_1" in policy._active_codes_price
    assert "PRICE_2" not in policy._active_codes_price
    mock_streaming_logger.log_dropped_subscriptions.assert_called() # Dropped 경고 로깅


@pytest.mark.asyncio
async def test_rebalance_connects_websocket_before_subscribe(policy, mock_streaming):
    """장중 신규 구독은 WebSocket 연결을 먼저 보장한 뒤 subscribe를 보낸다."""
    policy._refs = {
        "005930": {"portfolio": {"priority": SubscriptionPriority.HIGH, "type": StreamingType.UNIFIED_PRICE}}
    }

    await policy._rebalance()

    mock_streaming.connect_websocket.assert_awaited_once()
    assert mock_streaming.mock_calls.index(call.connect_websocket()) < mock_streaming.mock_calls.index(
        call.subscribe_unified_price("005930")
    )


@pytest.mark.asyncio
async def test_rebalance_skips_subscribe_when_websocket_connect_fails(
    policy, mock_streaming, mock_streaming_logger
):
    """장중 연결 보장 실패 시 subscribe를 보내지 않고 desired 상태로 남겨 다음 rebalance에서 재시도한다."""
    mock_streaming.connect_websocket.return_value = False
    policy._refs = {
        "005930": {"portfolio": {"priority": SubscriptionPriority.HIGH, "type": StreamingType.UNIFIED_PRICE}}
    }

    await policy._rebalance()

    mock_streaming.subscribe_unified_price.assert_not_awaited()
    assert "005930" not in policy._active_codes_price
    mock_streaming_logger.log_subscribe_failure.assert_called()


@pytest.mark.asyncio
async def test_do_subscribe_success_and_fail(policy, mock_streaming, mock_stock_repo, mock_streaming_stock_repo, mock_streaming_logger):
    """_do_subscribe 성공, 실패 시 동작 검증"""
    # 1. Success case (Price)
    mock_streaming.subscribe_unified_price.return_value = True
    await policy._do_subscribe("A", StreamingType.UNIFIED_PRICE)
    
    mock_stock_repo.mark_streaming.assert_called_with("A")
    mock_streaming_stock_repo.mark_active.assert_awaited_with("A", StreamingType.UNIFIED_PRICE)
    mock_streaming_logger.log_subscribe.assert_called()
    
    # 2. Failure case (False returned - PT)
    mock_streaming.subscribe_program_trading.return_value = False
    await policy._do_subscribe("B", StreamingType.PROGRAM_TRADING)
    mock_streaming_logger.log_add_subscription_rejection.assert_called()

async def test_do_subscribe_market_closed(policy, mock_streaming, mock_streaming_logger, mock_market_calendar):
    """장 외 시간에는 구독 요청이 보류되어야 한다."""
    # 장이 닫힌 상태로 모킹
    mock_market_calendar.is_market_open_now.return_value = False

    # 내부 메서드 직접 호출 (또는 add_subscription을 통한 간접 호출)
    from repositories.streaming_stock_repo import StreamingType
    await policy._do_subscribe("A", StreamingType.UNIFIED_PRICE)

    # streaming_service에는 구독 요청이 가지 않아야 함
    mock_streaming.subscribe_unified_price.assert_not_called()
    
    # logger에는 보류 로그가 남아야 함 (키워드 인자 사용)
    mock_streaming_logger.log_subscribe_pending.assert_called_once_with(
        code="A", 
        message="SubscriptionPolicy: 장 외 시간 — 구독 보류"
    )

@pytest.mark.asyncio
async def test_do_subscribe_exception(policy, mock_streaming, mock_streaming_logger):
    """_do_subscribe 예외 발생 시 로깅 검증 (stream_type 파라미터 추가)"""
    mock_streaming.subscribe_unified_price.side_effect = Exception("Conn Error")
    await policy._do_subscribe("A", StreamingType.UNIFIED_PRICE)
    # 구체화된 에러 로깅 검증
    mock_streaming_logger.log_subscribe_failure.assert_called()

@pytest.mark.asyncio
async def test_do_unsubscribe_success_and_exception(policy, mock_streaming, mock_streaming_logger):
    """_do_unsubscribe 성공 및 예외 처리 검증 (stream_type 파라미터 추가)"""
    policy._active_codes_price.add("A")
    policy._active_codes_pt.add("B")
    
    # 1. Success Case
    await policy._do_unsubscribe("A", StreamingType.UNIFIED_PRICE)
    assert "A" not in policy._active_codes_price
    mock_streaming.unsubscribe_unified_price.assert_awaited_with("A")
    mock_streaming_logger.log_unsubscribe.assert_called()
    
    # 2. Exception Case
    mock_streaming.unsubscribe_program_trading.side_effect = Exception("Unsub Error")
    await policy._do_unsubscribe("B", StreamingType.PROGRAM_TRADING)
    # 구체화된 에러 로깅 검증
    mock_streaming_logger.log_unsubscribe_failure.assert_called()
