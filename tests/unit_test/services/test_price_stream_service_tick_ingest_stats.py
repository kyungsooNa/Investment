"""PriceStreamService tick-ingest 진단 카운터 테스트 (P2 2-4 shadow no-tick 분기).

목적: event-shadow 후보가 구독돼도 신호 0건인 원인을
  (a1) WebSocket 프레임 자체 미수신 (received=0)
  (a2) 프레임은 오나 quality 게이트 전량 거절 (received>0, quality_reject≈received, dispatched=0)
로 구분하기 위한 종목별 누적 카운터를 검증한다.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.price_stream_service import PriceStreamService
from services.data_quality_service import DataQualityResult


async def _drain_pending():
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


def _valid_tick(code="005930"):
    return {
        "유가증권단축종목코드": code,
        "주식현재가": "75000",
        "전일대비": "1000",
        "전일대비율": "1.35",
        "전일대비부호": "2",
        "누적거래량": "1500000",
    }


@pytest.mark.asyncio
async def test_received_and_dispatched_counted_on_valid_tick(stock_repo, logger):
    router = MagicMock()
    router.on_price_tick = AsyncMock()
    svc = PriceStreamService(stock_repo=stock_repo, logger=logger, event_router=router)

    svc.on_price_tick(_valid_tick("005930"))
    svc.on_price_tick(_valid_tick("005930"))
    await _drain_pending()

    snap = svc.tick_ingest_stats_snapshot(["005930"])
    assert snap["005930"]["received"] == 2
    assert snap["005930"]["dispatched"] == 2
    assert snap["005930"]["quality_reject"] == 0


def test_quality_reject_counted_not_dispatched(stock_repo, logger):
    dq = MagicMock()
    dq.validate_price_tick.return_value = DataQualityResult(
        ok=False, severity="error", reason="invalid_tick", code="005930"
    )
    router = MagicMock()
    router.on_price_tick = AsyncMock()
    svc = PriceStreamService(
        stock_repo=stock_repo, logger=logger, data_quality_service=dq, event_router=router
    )

    svc.on_price_tick(_valid_tick("005930"))

    snap = svc.tick_ingest_stats_snapshot(["005930"])
    assert snap["005930"]["received"] == 1
    assert snap["005930"]["quality_reject"] == 1
    assert snap["005930"]["dispatched"] == 0


def test_no_router_no_dispatch_but_received(stock_repo, logger):
    svc = PriceStreamService(stock_repo=stock_repo, logger=logger)

    svc.on_price_tick(_valid_tick("005930"))

    snap = svc.tick_ingest_stats_snapshot(["005930"])
    assert snap["005930"]["received"] == 1
    assert snap["005930"]["dispatched"] == 0
    assert snap["005930"]["quality_reject"] == 0


def test_missing_payload_counted_as_malformed(stock_repo, logger):
    svc = PriceStreamService(stock_repo=stock_repo, logger=logger)

    svc.on_price_tick({"주식현재가": "75000"})  # 코드 누락
    svc.on_price_tick({"유가증권단축종목코드": "005930"})  # 현재가 누락

    snap = svc.tick_ingest_stats_snapshot(["005930", "__unknown__"])
    assert snap["005930"] == {"received": 0, "quality_reject": 0, "dispatched": 0, "malformed": 1}
    assert snap["__unknown__"] == {"received": 0, "quality_reject": 0, "dispatched": 0, "malformed": 1}


def test_snapshot_zero_fills_requested_unseen_codes(stock_repo, logger):
    svc = PriceStreamService(stock_repo=stock_repo, logger=logger)

    svc.on_price_tick(_valid_tick("005930"))

    snap = svc.tick_ingest_stats_snapshot(["005930", "403870"])
    assert snap["005930"]["received"] == 1
    # 구독은 됐으나 tick 미수신인 종목 → 0으로 표면화 (a1 진단)
    assert snap["403870"] == {"received": 0, "quality_reject": 0, "dispatched": 0, "malformed": 0}
