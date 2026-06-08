"""Event-shadow 파이프라인 end-to-end 계약 검증 (P2 2-4, #498 flush 수정 후).

기존 shadow 테스트들은 각 고리를 경계에서 mock 으로 막고 개별 검증한다:
  - PriceStreamService → router  (router=Mock)
  - router → evaluator           (evaluator=Mock)
  - evaluator → jsonl            (evaluator 직접 호출)

이 파일은 그 사이의 **실제 컴포넌트 계약 드리프트**를 잡는다. 실제 KIS realtime
필드(`주식현재가`/`주식시가`)로 만든 틱이
  PriceStreamService.on_price_tick
    → 실제 StrategyEventRouter.on_price_tick
      → 실제 LarryWilliamsVBOStrategy.evaluate_single / evaluate_exit_single
        → 실제 EventShadowJournalService
          → <log_root>/event_shadow/<YYYYMMDD>.jsonl
까지 한 줄로 흐르는지(=5거래일 수집 시작 전 de-risk) 검증한다. 실 주문은 발생하지
않는다(shadow evaluator 는 항상 None 반환).

핵심으로 잡는 회귀: snapshot 키 모양(`price`=str, `open`=float|None)이 VBO 소비
(`int(snapshot["price"])`, `float(snapshot["open"] or 0)`)와 어긋나면 배선이 전부
"통과"해도 레코드가 영영 안 생긴다 — mock 경계 테스트로는 보이지 않는다.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from scheduler.strategy_scheduler import StrategyScheduler, StrategySchedulerConfig
from services.event_shadow_journal_service import EventShadowJournalService
from services.price_stream_service import PriceStreamService
from services.strategy_event_router import StrategyEventRouter
from strategies.larry_williams_vbo_strategy import (
    LarryWilliamsVBOConfig,
    LarryWilliamsVBOStrategy,
)


async def _drain_pending() -> None:
    """price_stream 이 loop.create_task 로 띄운 router dispatch task 를 완료시킨다."""
    current = asyncio.current_task()
    pending = [t for t in asyncio.all_tasks() if t is not current and not t.done()]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


def _make_clock():
    """VBO 진입창(09:10~14:00) + router 장중 게이트를 동시에 통과하는 공용 clock.

    프로덕션에서도 strategy._tm 과 router._mc 는 같은 MarketClock 인스턴스다.
    """
    clock = MagicMock()
    clock.get_current_kst_time.return_value = datetime(2026, 5, 20, 10, 30, 0)
    clock.is_market_open_now.return_value = True
    return clock


def _make_scheduler(clock, *, event_router, event_shadow_journal):
    return StrategyScheduler(
        virtual_trade_service=MagicMock(),
        order_execution_service=MagicMock(),
        stock_query_service=MagicMock(),
        stock_code_repository=MagicMock(),
        market_clock=clock,
        market_calendar_service=MagicMock(),
        logger=MagicMock(),
        dry_run=True,
        store=MagicMock(),
        price_subscription_service=None,  # sync_subscriptions no-op (틱은 직접 주입)
        event_router=event_router,
        event_shadow_journal=event_shadow_journal,
    )


def _make_vbo(clock, *, candidates, ranges):
    vbo = LarryWilliamsVBOStrategy(
        stock_query_service=MagicMock(),
        market_clock=clock,
        config=LarryWilliamsVBOConfig(k_value=0.5),
        logger=MagicMock(),
    )
    # scan() 이 채우는 상태를 직접 주입 (evaluate_single 은 sqs 미사용, 순수 snapshot+state).
    vbo._current_candidate_codes_set = set(candidates)
    vbo._range_cache.ranges.update(ranges)
    return vbo


def _build_pipeline(clock, journal, *, kill_switch_service=None):
    router = StrategyEventRouter(
        market_clock=clock,
        kill_switch_service=kill_switch_service,
        logger=MagicMock(),
        throttle_sec=0.0,
        stale_snapshot_sec=5.0,
        signal_sink=None,  # shadow 운영은 sink 없음
    )
    price_stream = PriceStreamService(
        stock_repo=MagicMock(),
        logger=MagicMock(),
        event_router=router,
    )
    return router, price_stream


@pytest.mark.asyncio
async def test_live_tick_flows_to_entry_shadow_jsonl(tmp_path):
    """돌파 틱 → 실제 체인 → event_shadow jsonl 에 BUY 레코드 1건."""
    clock = _make_clock()
    journal = EventShadowJournalService(log_root=tmp_path)
    scheduler = _make_scheduler(clock, event_router=None, event_shadow_journal=journal)
    # 실제 router/price_stream 으로 교체 (scheduler 는 evaluator 빌드/기록 경로용).
    router, price_stream = _build_pipeline(clock, journal)
    scheduler._event_router = router

    # Open 74,000 + Range 2,000 × K 0.5 = Target 75,000.
    vbo = _make_vbo(clock, candidates=["005930"], ranges={"005930": 2000.0})
    cfg = StrategySchedulerConfig(strategy=vbo, event_driven_shadow=True)

    # scan 직후 배선과 동일: 후보를 router 에 구독 + (price_sub None 이라) 가격 sync no-op.
    await scheduler._refresh_event_shadow_subscriptions(cfg)
    assert "래리윌리엄스VBO" in router.subscribers_for("005930")

    # 실제 KIS realtime_price 필드 모양의 돌파 틱 (현재가 == Target).
    price_stream.on_price_tick({
        "유가증권단축종목코드": "005930",
        "주식현재가": "75000",
        "주식시가": "74000",
        "주식최고가": "75200",
        "주식최저가": "73900",
        "누적거래량": "1500000",
        "전일대비": "1000",
        "전일대비율": "1.35",
        "전일대비부호": "2",
    })
    await _drain_pending()

    path = tmp_path / "event_shadow" / "20260520.jsonl"
    assert path.exists(), "라이브 틱이 event_shadow jsonl 까지 도달하지 못했다"
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").strip().splitlines()]
    signal_records = [r for r in records if r.get("signal_source") == "event_shadow"]
    assert len(signal_records) == 1
    rec = signal_records[0]
    assert rec["signal_source"] == "event_shadow"
    assert rec["code"] == "005930"
    assert rec["strategy"] == "래리윌리엄스VBO"
    # 계약: price_stream snapshot 모양이 그대로 evaluator 까지 전달된다.
    assert rec["snapshot"]["price"] == "75000"   # 원본 str 보존
    assert rec["snapshot"]["open"] == 74000.0    # _sf 로 float 변환
    # 계약: VBO 가 그 snapshot 으로 BUY 신호를 만들었다.
    assert rec["signal"]["action"] == "BUY"
    assert rec["signal"]["price"] == 75000


@pytest.mark.asyncio
async def test_below_target_tick_writes_no_entry_record(tmp_path):
    """Target 미달 틱은 신호 없음 → 파일 미생성(빈 신호가 기록되지 않는다)."""
    clock = _make_clock()
    journal = EventShadowJournalService(log_root=tmp_path)
    scheduler = _make_scheduler(clock, event_router=None, event_shadow_journal=journal)
    router, price_stream = _build_pipeline(clock, journal)
    scheduler._event_router = router

    vbo = _make_vbo(clock, candidates=["005930"], ranges={"005930": 2000.0})
    cfg = StrategySchedulerConfig(strategy=vbo, event_driven_shadow=True)
    await scheduler._refresh_event_shadow_subscriptions(cfg)

    price_stream.on_price_tick({
        "유가증권단축종목코드": "005930",
        "주식현재가": "74500",   # Target 75,000 미달
        "주식시가": "74000",
        "누적거래량": "1500000",
    })
    await _drain_pending()

    path = tmp_path / "event_shadow" / "20260520.jsonl"
    assert path.exists()
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [r for r in records if r.get("signal_source") == "event_shadow"] == []
    assert journal.get_records() == []


@pytest.mark.asyncio
async def test_live_tick_flows_to_exit_shadow_jsonl(tmp_path):
    """보유 종목 손절 틱 → 실제 체인 → event_shadow_exit SELL 레코드 1건."""
    clock = _make_clock()
    journal = EventShadowJournalService(log_root=tmp_path)
    scheduler = _make_scheduler(clock, event_router=None, event_shadow_journal=journal)
    router, price_stream = _build_pipeline(clock, journal)
    scheduler._event_router = router

    vbo = _make_vbo(clock, candidates=[], ranges={})
    holdings_by_code = {
        "005930": {"code": "005930", "buy_price": 80000, "qty": 1, "name": "삼성전자"},
    }
    # exit evaluator 는 holdings 를 closure 로 받는다(= _refresh_exit_shadow_subscriptions
    # 가 vts 조회 후 빌드하는 것과 동일 형태). vts mock 부담을 피해 직접 빌드/구독한다.
    evaluator = scheduler._build_exit_shadow_evaluator(vbo, holdings_by_code)
    router.subscribe("005930", strategy_name="래리윌리엄스VBO__exit", evaluator=evaluator)

    # 80,000 매수 대비 75,000 → net 손절(-3%)보다 큰 손실 → SELL.
    price_stream.on_price_tick({
        "유가증권단축종목코드": "005930",
        "주식현재가": "75000",
        "주식시가": "79000",
        "누적거래량": "900000",
    })
    await _drain_pending()

    path = tmp_path / "event_shadow" / "20260520.jsonl"
    assert path.exists()
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["signal_source"] == "event_shadow_exit"
    assert rec["code"] == "005930"
    assert rec["signal"]["action"] == "SELL"
    assert rec["signal"]["price"] == 75000
