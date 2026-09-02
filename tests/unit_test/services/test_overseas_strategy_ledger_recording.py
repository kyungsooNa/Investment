"""미국장 자동 전략 → USD 원장 기록 회귀 테스트.

기존 결함: 장중 전략이 실제로 돌아 진입·청산 알림과 would-be 저널이 남는데도
`OverseasTradeRepository` 에 쓰는 경로가 **웹 수동주문 하나뿐**이라, 미국장 모의투자
화면(`/api/overseas/trades`)은 항상 "기록 없음" 이었다. 원장 기록은 주문 경로의
초크포인트인 `OverseasOrderExecutionService` 가 담당한다 — VBO(독립 구현)와 베이스
상속 전략 5종이 모두 그곳을 지난다.

여기서는 mock 이 아니라 **실제 저장소 + 실제 주문 서비스 + 실제 전략**으로 왕복을 돈다.
배선 하나만 mock 으로 두면 화면이 비는 결함이 그대로 재현되지 않는다.
"""
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytz

from common.types import ErrorCode, ResCommonResponse
from repositories.overseas_trade_repository import OverseasTradeRepository
from common.overseas_types import OverseasExchange
from services.overseas_intraday_channel_breakout_service import (
    OverseasIntradayChannelBreakoutService,
)
from services.overseas_order_execution_service import OverseasOrderExecutionService
from services.us_session_volume_service import USSessionVolumeService

NY = pytz.timezone("America/New_York")
TRADE_DATE = "20260818"
STRATEGY = OverseasIntradayChannelBreakoutService.STRATEGY_NAME


def _bar(d, o, h, l, c, v=1_000_000):
    return {"date": d, "open": o, "high": h, "low": l, "close": c, "volume": v}


def _history(n=40):
    return [_bar(f"202607{i+1:02d}" if i < 30 else f"202608{i-29:02d}",
                 98, 100, 96, 99, 1_000_000) for i in range(n)]


def _svc(ledger):
    candidate_service = MagicMock()
    candidate_service.get_candidates = AsyncMock(return_value=[
        {"code": "AAA", "name": "Aaa", "exchange": "NASD"},
    ])
    sqs = MagicMock()
    sqs.get_recent_daily_ohlcv = AsyncMock(return_value=ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="ok", data=_history()))
    indicator = MagicMock()
    indicator.calc_adx_sync = MagicMock(return_value={"adx": 30.0, "adx_rising": True})
    calendar = MagicMock()
    calendar.get_close_time_str.return_value = "16:00"
    clock = MagicMock()
    clock.get_current_kst_time.return_value = NY.localize(datetime(2026, 8, 18, 12, 45))

    orders = OverseasOrderExecutionService(
        broker=None, live_enabled=False, trade_repository=ledger, logger=MagicMock(),
    )
    svc = OverseasIntradayChannelBreakoutService(
        candidate_service=candidate_service,
        stock_query_service=sqs,
        indicator_service=indicator,
        order_execution_service=orders,
        session_volume_service=USSessionVolumeService(
            us_market_calendar_service=calendar, logger=MagicMock()),
        market_clock=clock,
        logger=MagicMock(),
    )
    return SimpleNamespace(service=svc, orders=orders)


@pytest.mark.asyncio
async def test_strategy_entry_lands_in_usd_ledger(tmp_path):
    ledger = OverseasTradeRepository(db_path=str(tmp_path / "overseas_trade.db"))
    s = _svc(ledger)
    await s.service.prepare_session(TRADE_DATE)

    await s.service.on_price("AAA", 101.0, volume=800_000)

    holds = ledger.get_holds()
    assert len(holds) == 1
    assert holds[0]["symbol"] == "AAA"
    assert holds[0]["buy_price"] == pytest.approx(101.0)
    assert holds[0]["source"] == STRATEGY
    assert holds[0]["exchange"] == "NASD"


@pytest.mark.asyncio
async def test_strategy_round_trip_produces_a_sold_row_with_return(tmp_path):
    """진입만 남고 청산이 안 남으면 성과 요약이 영원히 0건 청산으로 보인다."""
    ledger = OverseasTradeRepository(db_path=str(tmp_path / "overseas_trade.db"))
    s = _svc(ledger)
    await s.service.prepare_session(TRADE_DATE)
    await s.service.on_price("AAA", 101.0, volume=800_000)

    await s.service.on_price("AAA", 110.0, volume=900_000)  # 보유 갱신
    await s.service.close_all(reason="eod")

    trades = ledger.get_all_trades()
    assert len(trades) == 1
    assert trades[0]["status"] == "SOLD"
    assert trades[0]["sell_price"] == pytest.approx(110.0)
    assert trades[0]["return_rate"] > 0
    summary = ledger.get_summary()
    assert summary["total_trades"] == 1
    assert summary["sold_trades"] == 1


@pytest.mark.asyncio
async def test_strategy_exit_does_not_close_a_manual_lot(tmp_path):
    """수동 보유와 전략 보유가 같은 심볼이면, 전략 청산이 수동 lot 을 닫으면 안 된다."""
    ledger = OverseasTradeRepository(db_path=str(tmp_path / "overseas_trade.db"))
    ledger.log_buy("AAA", OverseasExchange.NASD, 90.0, 5, source="manual")
    s = _svc(ledger)
    await s.service.prepare_session(TRADE_DATE)
    await s.service.on_price("AAA", 101.0, volume=800_000)

    await s.service.close_all(reason="eod")

    holds = ledger.get_holds()
    assert len(holds) == 1
    assert holds[0]["source"] == "manual"
    assert holds[0]["qty"] == 5
