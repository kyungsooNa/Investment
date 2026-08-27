"""해외 장중 전략 4종(RSI2/BGU/OSB/PP) 서비스 테스트.

공통 베이스(`OverseasIntradayStrategyBase`)의 감시목록·포지션·주문·게이트는
CB/VBO 테스트가 이미 덮으므로, 여기서는 각 전략의 **세션 상수 산출과 진입 판정**에
집중한다.
"""
from types import SimpleNamespace
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytz

from common.types import ErrorCode, ResCommonResponse
from services.overseas_intraday_buyable_gap_up_service import (
    OverseasIntradayBuyableGapUpService,
)
from services.overseas_intraday_pocket_pivot_service import OverseasIntradayPocketPivotService
from services.overseas_intraday_rsi2_service import OverseasIntradayRSI2Service
from services.overseas_intraday_squeeze_breakout_service import (
    OverseasIntradaySqueezeBreakoutService,
)
from services.us_session_volume_service import USSessionVolumeService

NY = pytz.timezone("America/New_York")
TRADE_DATE = "20260818"


def _at(h, m):
    return NY.localize(datetime(2026, 8, 18, h, m))


def _bar(d, o, h, l, c, v=1_000_000):
    return {"date": d, "open": o, "high": h, "low": l, "close": c, "volume": v}


def _ok(data):
    return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="ok", data=data)


def _build(cls, rows, *, now=None, **kwargs):
    candidate_service = MagicMock()
    candidate_service.get_candidates = AsyncMock(return_value=[
        {"code": "AAA", "name": "Aaa", "exchange": "NASD"},
    ])
    sqs = MagicMock()
    sqs.get_recent_daily_ohlcv = AsyncMock(return_value=_ok(rows))
    orders = MagicMock()
    orders.place_entry = AsyncMock(return_value=_ok({"would_be": True}))
    orders.place_exit = AsyncMock(return_value=_ok({"would_be": True}))
    calendar = MagicMock()
    calendar.get_close_time_str.return_value = "16:00"
    clock = MagicMock()
    clock.get_current_kst_time.return_value = now or _at(12, 45)

    svc = cls(
        candidate_service=candidate_service,
        stock_query_service=sqs,
        order_execution_service=orders,
        session_volume_service=USSessionVolumeService(
            us_market_calendar_service=calendar, logger=MagicMock()),
        market_clock=clock,
        logger=MagicMock(),
        **kwargs,
    )
    return SimpleNamespace(service=svc, orders=orders, sqs=sqs, clock=clock)


# ══════════════════ RSI2 ══════════════════

def _rsi2_rows(n=205, *, tail=None):
    """상승 추세(200MA 위) + 마지막 며칠 급락으로 RSI(2) 를 낮춘다."""
    rows = [_bar(f"{20250101 + i}", 100 + i * 0.5, 100 + i * 0.5, 100 + i * 0.5,
                 100 + i * 0.5) for i in range(n)]
    if tail:
        for i, c in enumerate(tail):
            rows[-(len(tail) - i)] = _bar(rows[-(len(tail) - i)]["date"], c, c, c, c)
    return rows


@pytest.mark.asyncio
async def test_rsi2_only_evaluates_in_close_window():
    """장중 가격으로 RSI 를 돌리면 종가 신호와 달라진다 — 마감 직전에만 판정한다."""
    rows = _rsi2_rows(tail=[210, 209, 208])
    s = _build(OverseasIntradayRSI2Service, rows, now=_at(12, 45))
    await s.service.prepare_session(TRADE_DATE)

    assert await s.service.on_price("AAA", 190.0, volume=1_000_000) is None
    s.orders.place_entry.assert_not_awaited()


@pytest.mark.asyncio
async def test_rsi2_enters_near_close_when_rsi_is_low_and_above_200ma():
    rows = _rsi2_rows(tail=[210, 209, 208])
    s = _build(OverseasIntradayRSI2Service, rows, now=_at(15, 50))
    await s.service.prepare_session(TRADE_DATE)

    action = await s.service.on_price("AAA", 190.0, volume=1_000_000)

    assert action is not None
    assert action["action"] == "BUY"
    assert action["reason"] == "rsi2_intraday_pullback"


@pytest.mark.asyncio
async def test_rsi2_skips_when_below_200ma():
    rows = _rsi2_rows(tail=[210, 209, 208])
    s = _build(OverseasIntradayRSI2Service, rows, now=_at(15, 50))
    await s.service.prepare_session(TRADE_DATE)

    # 200MA 한참 아래 → 추세 조건 실패
    assert await s.service.on_price("AAA", 50.0, volume=1_000_000) is None


@pytest.mark.asyncio
async def test_rsi2_skips_when_rsi_not_oversold():
    rows = _rsi2_rows()
    s = _build(OverseasIntradayRSI2Service, rows, now=_at(15, 50))
    await s.service.prepare_session(TRADE_DATE)

    # 계속 상승 중 → RSI(2) 높음
    assert await s.service.on_price("AAA", 250.0, volume=1_000_000) is None


@pytest.mark.asyncio
async def test_rsi2_requires_long_history():
    s = _build(OverseasIntradayRSI2Service, _rsi2_rows(n=50), now=_at(15, 50))
    assert await s.service.prepare_session(TRADE_DATE) == 0


# ══════════════════ Buyable Gap-Up ══════════════════

def _bgu_rows(*, gap_pct=6.0, n=60):
    rows = [_bar(f"{20260601 + i}", 100, 101, 99, 100, 1_000_000) for i in range(n)]
    open_ = 100 * (1 + gap_pct / 100)
    rows.append(_bar(TRADE_DATE, open_, open_ + 1, open_ - 0.5, open_ + 0.5, 500_000))
    return rows


@pytest.mark.asyncio
async def test_bgu_watches_only_gapped_symbols():
    s = _build(OverseasIntradayBuyableGapUpService, _bgu_rows(gap_pct=6.0))
    assert await s.service.prepare_session(TRADE_DATE) == 1

    weak = _build(OverseasIntradayBuyableGapUpService, _bgu_rows(gap_pct=1.0))
    assert await weak.service.prepare_session(TRADE_DATE) == 0


@pytest.mark.asyncio
async def test_bgu_requires_today_bar_for_open():
    """당일 봉이 없으면 시가 미확정 — 추정으로 갭을 계산하지 않는다."""
    rows = [_bar(f"{20260601 + i}", 100, 101, 99, 100) for i in range(60)]
    s = _build(OverseasIntradayBuyableGapUpService, rows)
    assert await s.service.prepare_session(TRADE_DATE) == 0


@pytest.mark.asyncio
async def test_bgu_enters_on_volume_while_holding_gap():
    s = _build(OverseasIntradayBuyableGapUpService, _bgu_rows(), now=_at(12, 45))
    await s.service.prepare_session(TRADE_DATE)

    # 12:45 progress 0.5 → 누적 1,600,000 환산 3,200,000 >= 1,000,000 * 3
    action = await s.service.on_price("AAA", 107.0, volume=1_600_000)

    assert action["action"] == "BUY"
    assert action["reason"] == "bgu_intraday_gap_up"


@pytest.mark.asyncio
async def test_bgu_does_not_enter_below_open():
    """갭을 메우는 음봉이면 진입하지 않는다."""
    s = _build(OverseasIntradayBuyableGapUpService, _bgu_rows(), now=_at(12, 45))
    await s.service.prepare_session(TRADE_DATE)

    assert await s.service.on_price("AAA", 104.0, volume=5_000_000) is None


@pytest.mark.asyncio
async def test_bgu_does_not_enter_without_volume():
    s = _build(OverseasIntradayBuyableGapUpService, _bgu_rows(), now=_at(12, 45))
    await s.service.prepare_session(TRADE_DATE)

    assert await s.service.on_price("AAA", 107.0, volume=1_000_000) is None


# ══════════════════ Squeeze Breakout ══════════════════

def _osb_rows(*, squeezed=True, n=60):
    """스퀴즈 판정은 **상대적**이다 — 현재 볼린저 폭 vs 최근 20개 창의 최소폭.

    squeezed=True : 내내 좁음 → 현재 폭 ≈ 최소폭 → 스퀴즈
    squeezed=False: 앞은 좁고 최근 20봉이 확장 → 현재 폭 >> 최소폭 → 스퀴즈 아님
    """
    rows = []
    for i in range(n):
        expanded = (not squeezed) and i >= n - 20
        c = 100.0 + (i % 2) * (8.0 if expanded else 0.1)
        rows.append(_bar(f"{20260601 + i}", c, c + 0.2, c - 0.2, c, 1_000_000))
    return rows


@pytest.mark.asyncio
async def test_osb_watches_only_squeezed_symbols():
    s = _build(OverseasIntradaySqueezeBreakoutService, _osb_rows(squeezed=True))
    assert await s.service.prepare_session(TRADE_DATE) == 1

    wide = _build(OverseasIntradaySqueezeBreakoutService, _osb_rows(squeezed=False))
    assert await wide.service.prepare_session(TRADE_DATE) == 0


@pytest.mark.asyncio
async def test_osb_enters_on_breakout_with_volume():
    s = _build(OverseasIntradaySqueezeBreakoutService, _osb_rows(), now=_at(12, 45))
    await s.service.prepare_session(TRADE_DATE)

    action = await s.service.on_price("AAA", 100.5, volume=800_000)

    assert action["action"] == "BUY"
    assert action["reason"] == "osb_intraday_squeeze_breakout"


@pytest.mark.asyncio
async def test_osb_rejects_overextended_entry():
    """돌파 후 과연장(+5% 초과)은 진입하지 않는다."""
    s = _build(OverseasIntradaySqueezeBreakoutService, _osb_rows(), now=_at(12, 45))
    await s.service.prepare_session(TRADE_DATE)

    assert await s.service.on_price("AAA", 130.0, volume=5_000_000) is None


@pytest.mark.asyncio
async def test_osb_rejects_weak_candle_position():
    """세션 고점 대비 아래쪽에서 돌파하면 캔들 품질 미달로 거른다."""
    s = _build(OverseasIntradaySqueezeBreakoutService, _osb_rows(), now=_at(12, 45))
    await s.service.prepare_session(TRADE_DATE)

    await s.service.on_price("AAA", 104.0, volume=100)   # 세션 고점 104 로 올림
    assert await s.service.on_price("AAA", 100.4, volume=5_000_000) is None


# ══════════════════ Pocket Pivot ══════════════════

def _pp_rows(n=60):
    """10MA 근처에서 움직이며 하락일이 섞인 히스토리."""
    rows = []
    for i in range(n):
        is_down = i % 5 == 0
        o, c = (101.0, 99.0) if is_down else (99.0, 100.0)
        rows.append(_bar(f"{20260601 + i}", o, max(o, c) + 0.5, min(o, c) - 0.5, c,
                         2_000_000 if is_down else 800_000))
    return rows


@pytest.mark.asyncio
async def test_pp_builds_ma_and_down_volume_constants():
    s = _build(OverseasIntradayPocketPivotService, _pp_rows())
    assert await s.service.prepare_session(TRADE_DATE) == 1

    setup = s.service.get_state()["watch"]["AAA"]
    assert setup["max_down_volume"] == pytest.approx(2_000_000)
    assert setup["ma_10d"] > 0


@pytest.mark.asyncio
async def test_pp_requires_a_down_day_in_lookback():
    rows = [_bar(f"{20260601 + i}", 99, 101, 98, 100, 800_000) for i in range(60)]
    s = _build(OverseasIntradayPocketPivotService, rows)
    assert await s.service.prepare_session(TRADE_DATE) == 0


@pytest.mark.asyncio
async def test_pp_enters_near_ma_with_pivot_volume():
    s = _build(OverseasIntradayPocketPivotService, _pp_rows(), now=_at(12, 45))
    await s.service.prepare_session(TRADE_DATE)

    # 기준선 = 2,000,000 * 0.9 = 1,800,000. 12:45 → 누적 1,000,000 환산 2,000,000
    action = await s.service.on_price("AAA", 100.5, volume=1_000_000)

    assert action["action"] == "BUY"
    assert action["reason"] == "pp_intraday_pocket_pivot"
    assert action["supporting_ma"] in ("ma_10d", "ma_20d", "ma_50d")


@pytest.mark.asyncio
async def test_pp_does_not_enter_below_prev_close():
    s = _build(OverseasIntradayPocketPivotService, _pp_rows(), now=_at(12, 45))
    await s.service.prepare_session(TRADE_DATE)

    assert await s.service.on_price("AAA", 99.0, volume=5_000_000) is None


@pytest.mark.asyncio
async def test_pp_does_not_enter_far_from_supporting_ma():
    s = _build(OverseasIntradayPocketPivotService, _pp_rows(), now=_at(12, 45))
    await s.service.prepare_session(TRADE_DATE)

    # MA 대비 +4% 를 크게 벗어남
    assert await s.service.on_price("AAA", 150.0, volume=5_000_000) is None


@pytest.mark.asyncio
async def test_pp_stop_is_below_supporting_ma():
    s = _build(OverseasIntradayPocketPivotService, _pp_rows(), now=_at(12, 45))
    await s.service.prepare_session(TRADE_DATE)
    await s.service.on_price("AAA", 100.5, volume=1_000_000)

    setup = s.service.get_state()["watch"]["AAA"]
    held = s.service.get_state()["positions"]["AAA"]
    assert held["stop_price"] == pytest.approx(setup["_supporting_ma_value"] * 0.98)
