"""PriceStreamService → StrategyEventRouter hook 테스트 (P2 2-4 PR-1).

검증 항목:
- event_router 미주입 시 기존 동작 유지 (이전 테스트들이 보장)
- event_router 주입 시 on_price_tick 후 router.on_price_tick 이 스케줄됨
- 필수 필드 누락 / quality 검증 실패 tick 은 router 로 전달되지 않음
- set_event_router 로 late injection 가능
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.price_stream_service import PriceStreamService


async def _drain_pending():
    """현재 task 를 제외한 pending task 를 모두 완료시킨다."""
    current = asyncio.current_task()
    pending = [t for t in asyncio.all_tasks() if t is not current and not t.done()]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


@pytest.fixture
def stock_repo():
    return MagicMock()


@pytest.fixture
def logger():
    return MagicMock()


def _valid_tick():
    return {
        "유가증권단축종목코드": "005930",
        "주식현재가": "75000",
        "전일대비": "1000",
        "전일대비율": "1.35",
        "전일대비부호": "2",
        "누적거래량": "1500000",
    }


@pytest.mark.asyncio
async def test_event_router_invoked_with_code_and_snapshot(stock_repo, logger):
    router = MagicMock()
    router.on_price_tick = AsyncMock()
    service = PriceStreamService(stock_repo=stock_repo, logger=logger, event_router=router)

    service.on_price_tick(_valid_tick())
    await _drain_pending()  # let scheduled task run

    router.on_price_tick.assert_awaited_once()
    args, kwargs = router.on_price_tick.call_args
    assert args[0] == "005930"
    snapshot = args[1]
    assert snapshot["price"] == "75000"
    assert "received_at" in snapshot


@pytest.mark.asyncio
async def test_event_router_not_invoked_when_code_missing(stock_repo, logger):
    router = MagicMock()
    router.on_price_tick = AsyncMock()
    service = PriceStreamService(stock_repo=stock_repo, logger=logger, event_router=router)

    service.on_price_tick({"주식현재가": "75000"})
    await _drain_pending()

    router.on_price_tick.assert_not_called()


@pytest.mark.asyncio
async def test_event_router_not_invoked_when_quality_fails(stock_repo, logger):
    dq = MagicMock()
    fail_result = MagicMock()
    fail_result.ok = False
    fail_result.severity = "error"
    fail_result.reason = "test_fail"
    fail_result.code = "005930"
    fail_result.latency_sec = 0.0
    fail_result.metadata = {}
    fail_result.to_dict.return_value = {}
    dq.validate_price_tick = MagicMock(return_value=fail_result)

    router = MagicMock()
    router.on_price_tick = AsyncMock()
    service = PriceStreamService(
        stock_repo=stock_repo,
        logger=logger,
        data_quality_service=dq,
        event_router=router,
    )

    service.on_price_tick(_valid_tick())
    await _drain_pending()

    router.on_price_tick.assert_not_called()


@pytest.mark.asyncio
async def test_set_event_router_late_injection(stock_repo, logger):
    router = MagicMock()
    router.on_price_tick = AsyncMock()
    service = PriceStreamService(stock_repo=stock_repo, logger=logger)
    service.set_event_router(router)

    service.on_price_tick(_valid_tick())
    await _drain_pending()

    router.on_price_tick.assert_awaited_once()


def test_router_no_running_loop_does_not_raise(stock_repo, logger):
    """sync 컨텍스트에서 이벤트 루프 없으면 router fire 는 silently skip 되어야 한다."""
    router = MagicMock()
    router.on_price_tick = AsyncMock()
    service = PriceStreamService(stock_repo=stock_repo, logger=logger, event_router=router)

    # 동기 호출 — running loop 없음. 예외 없이 정상 종료해야 한다.
    service.on_price_tick(_valid_tick())

    # router 는 호출되지 않아야 한다 (loop 없으므로 schedule 불가).
    router.on_price_tick.assert_not_called()
