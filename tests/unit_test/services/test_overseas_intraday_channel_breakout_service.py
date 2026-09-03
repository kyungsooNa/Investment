"""해외 장중 Channel Breakout 라이브(paper) 경로 서비스 테스트.

CB 는 완성봉에서 채널상단·평균거래량·ADX 를 뽑고 당일 봉에서는 close/volume 만
쓰므로, 장중에는 폴링가 + 누적거래량 환산으로 같은 판정을 재현할 수 있다.
"""
from types import SimpleNamespace
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytz

from common.overseas_types import OverseasExchange
from common.types import ErrorCode, ResCommonResponse
from services.overseas_intraday_channel_breakout_service import (
    OverseasIntradayChannelBreakoutService,
)
from services.us_session_volume_service import USSessionVolumeService

NY = pytz.timezone("America/New_York")
TRADE_DATE = "20260818"


def _at(h, m):
    return NY.localize(datetime(2026, 8, 18, h, m))


def _bar(d, o, h, l, c, v=1_000_000):
    return {"date": d, "open": o, "high": h, "low": l, "close": c, "volume": v}


def _history(n=40, *, high=100.0, volume=1_000_000):
    """완성봉 n개 — 고가 100 고정, 거래량 균일."""
    return [_bar(f"202607{i+1:02d}" if i < 30 else f"202608{i-29:02d}",
                 high - 2, high, high - 4, high - 1, volume) for i in range(n)]


def _ok(data):
    return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="ok", data=data)


def _svc(*, rows=None, adx=30.0, adx_rising=True, now=None, regime=None,
         candidates=None, max_positions=5):
    candidate_service = MagicMock()
    candidate_service.get_candidates = AsyncMock(return_value=candidates if candidates is not None else [
        {"code": "AAA", "name": "Aaa", "exchange": "NASD", "avg_trading_value": 10_000_000.0},
    ])
    sqs = MagicMock()
    sqs.get_recent_daily_ohlcv = AsyncMock(return_value=_ok(rows if rows is not None else _history()))

    indicator = MagicMock()
    indicator.calc_adx_sync = MagicMock(return_value={"adx": adx, "adx_rising": adx_rising})

    orders = MagicMock()
    orders.place_entry = AsyncMock(return_value=_ok({"would_be": True}))
    orders.place_exit = AsyncMock(return_value=_ok({"would_be": True}))

    calendar = MagicMock()
    calendar.get_close_time_str.return_value = "16:00"
    session = USSessionVolumeService(us_market_calendar_service=calendar, logger=MagicMock())

    clock = MagicMock()
    clock.get_current_kst_time.return_value = now or _at(12, 45)

    service = OverseasIntradayChannelBreakoutService(
        candidate_service=candidate_service,
        stock_query_service=sqs,
        indicator_service=indicator,
        order_execution_service=orders,
        session_volume_service=session,
        market_clock=clock,
        market_regime_service=regime,
        logger=MagicMock(),
        max_positions=max_positions,
    )
    return SimpleNamespace(service=service, sqs=sqs, orders=orders, indicator=indicator,
                           clock=clock, candidate_service=candidate_service, regime=regime)


# ── 세션 준비 ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_prepare_session_computes_channel_and_volume_constants():
    s = _svc()
    watched = await s.service.prepare_session(TRADE_DATE)

    assert watched == 1
    setup = s.service.get_state()["watch"]["AAA"]
    assert setup["channel_high"] == pytest.approx(100.0)
    assert setup["avg_volume"] == pytest.approx(1_000_000)
    assert setup["adx"] == pytest.approx(30.0)


@pytest.mark.asyncio
async def test_prepare_session_excludes_weak_adx():
    """ADX 는 완성봉 기반이라 장중 불변 — 미달 종목은 감시에서 아예 뺀다."""
    s = _svc(adx=18.0)
    assert await s.service.prepare_session(TRADE_DATE) == 0
    assert s.service.watch_codes() == []


@pytest.mark.asyncio
async def test_prepare_session_excludes_non_rising_adx():
    s = _svc(adx_rising=False)
    assert await s.service.prepare_session(TRADE_DATE) == 0


@pytest.mark.asyncio
async def test_prepare_session_drops_todays_partial_bar_from_history():
    """당일 미완성 봉이 섞여 오면 채널/평균거래량 계산에서 빼야 한다."""
    rows = _history() + [_bar(TRADE_DATE, 99, 130, 98, 129, 50)]  # 당일 고가 130 (미완성)
    s = _svc(rows=rows)
    await s.service.prepare_session(TRADE_DATE)

    # 당일 봉을 뺐다면 채널 상단은 여전히 100
    assert s.service.get_state()["watch"]["AAA"]["channel_high"] == pytest.approx(100.0)


@pytest.mark.asyncio
async def test_prepare_session_skips_insufficient_history():
    s = _svc(rows=_history(n=5))
    assert await s.service.prepare_session(TRADE_DATE) == 0


@pytest.mark.asyncio
async def test_prepare_session_is_idempotent_for_same_date():
    s = _svc()
    await s.service.prepare_session(TRADE_DATE)
    await s.service.prepare_session(TRADE_DATE)
    assert s.candidate_service.get_candidates.await_count == 1


# ── 진입 ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_breakout_with_projected_volume_enters():
    """채널 돌파 + 환산 거래량이 평균×1.5 이상이면 진입한다."""
    s = _svc(now=_at(12, 45))
    await s.service.prepare_session(TRADE_DATE)

    # 12:45 → progress 0.5. 누적 800,000 → 환산 1,600,000 >= 1,000,000 * 1.5
    action = await s.service.on_price("AAA", 101.0, volume=800_000)

    assert action["action"] == "BUY"
    kwargs = s.orders.place_entry.call_args.kwargs
    assert kwargs["code"] == "AAA"
    assert kwargs["limit_price"] == 101.0


@pytest.mark.asyncio
async def test_price_at_or_below_channel_high_does_not_enter():
    s = _svc()
    await s.service.prepare_session(TRADE_DATE)

    assert await s.service.on_price("AAA", 100.0, volume=5_000_000) is None
    s.orders.place_entry.assert_not_awaited()


@pytest.mark.asyncio
async def test_breakout_without_volume_does_not_enter():
    """돌파해도 환산 거래량이 허들 미달이면 진입하지 않는다."""
    s = _svc(now=_at(12, 45))
    await s.service.prepare_session(TRADE_DATE)

    # 누적 500,000 → 환산 1,000,000 < 1,500,000
    assert await s.service.on_price("AAA", 101.0, volume=500_000) is None
    s.orders.place_entry.assert_not_awaited()


@pytest.mark.asyncio
async def test_morning_actual_volume_floor_blocks_projection_blowup():
    """오전장 환산은 뻥튀기되므로 실거래량 절대 하한도 넘어야 한다."""
    s = _svc(now=_at(10, 0))
    await s.service.prepare_session(TRADE_DATE)

    # 10:00 → progress 30/390. 누적 100,000 이면 환산은 1,300,000 으로 커지지만
    # 실거래량 하한(평균 50% = 500,000)에 걸린다.
    assert await s.service.on_price("AAA", 101.0, volume=100_000) is None
    s.orders.place_entry.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_volume_does_not_enter():
    """거래량이 안 오면 판정 근거가 없다 — 진입하지 않는다(fail-closed)."""
    s = _svc()
    await s.service.prepare_session(TRADE_DATE)

    assert await s.service.on_price("AAA", 101.0, volume=None) is None
    s.orders.place_entry.assert_not_awaited()


@pytest.mark.asyncio
async def test_does_not_reenter_same_day():
    s = _svc()
    await s.service.prepare_session(TRADE_DATE)
    await s.service.on_price("AAA", 101.0, volume=800_000)
    await s.service.on_price("AAA", 102.0, volume=900_000)

    assert s.orders.place_entry.await_count == 1


@pytest.mark.asyncio
async def test_max_positions_blocks_entry():
    cands = [{"code": c, "name": c, "exchange": "NASD"} for c in ("AAA", "BBB")]
    s = _svc(candidates=cands, max_positions=1)
    await s.service.prepare_session(TRADE_DATE)

    await s.service.on_price("AAA", 101.0, volume=800_000)
    assert await s.service.on_price("BBB", 101.0, volume=800_000) is None
    assert s.orders.place_entry.await_count == 1


# ── 청산 ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_stop_uses_channel_low_when_higher_than_pct_stop():
    """손절가 = max(20일 채널 저점, 진입가 -7%)."""
    s = _svc()
    await s.service.prepare_session(TRADE_DATE)
    await s.service.on_price("AAA", 101.0, volume=800_000)

    held = s.service.get_state()["positions"]["AAA"]
    # 채널 저점 96 vs 101*0.93 = 93.93 → 96
    assert held["stop_price"] == pytest.approx(96.0)


@pytest.mark.asyncio
async def test_stop_loss_exits_position():
    s = _svc()
    await s.service.prepare_session(TRADE_DATE)
    await s.service.on_price("AAA", 101.0, volume=800_000)

    action = await s.service.on_price("AAA", 95.0, volume=900_000)

    assert action["action"] == "SELL"
    assert action["exit_reason"] == "stop"


@pytest.mark.asyncio
async def test_close_all_exits_at_last_price():
    s = _svc()
    await s.service.prepare_session(TRADE_DATE)
    await s.service.on_price("AAA", 101.0, volume=800_000)
    await s.service.on_price("AAA", 104.0, volume=900_000)

    actions = await s.service.close_all(reason="eod")

    assert len(actions) == 1
    assert actions[0]["exit_price"] == pytest.approx(104.0)


# ── 마켓타이밍 게이트 ────────────────────────────────────────────────────

def _regime_stub(is_rising):
    r = MagicMock()
    r.classify = AsyncMock(return_value=SimpleNamespace(
        market="US", is_rising=is_rising,
        regime_label="bull" if is_rising else "bear", fail_detail="" if is_rising else "추세 꺾임",
    ))
    return r


@pytest.mark.asyncio
async def test_bear_regime_blocks_entry():
    s = _svc(regime=_regime_stub(False))
    await s.service.prepare_session(TRADE_DATE)

    assert await s.service.on_price("AAA", 101.0, volume=800_000) is None
    s.orders.place_entry.assert_not_awaited()


@pytest.mark.asyncio
async def test_regime_failure_is_fail_closed():
    regime = MagicMock()
    regime.classify = AsyncMock(side_effect=RuntimeError("조회 실패"))
    s = _svc(regime=regime)
    await s.service.prepare_session(TRADE_DATE)

    assert await s.service.on_price("AAA", 101.0, volume=800_000) is None


@pytest.mark.asyncio
async def test_stop_exit_runs_in_bear_regime():
    """게이트는 신규 진입만 막는다 — 보유분 손절은 국면과 무관하다."""
    regime = _regime_stub(True)
    s = _svc(regime=regime)
    await s.service.prepare_session(TRADE_DATE)
    await s.service.on_price("AAA", 101.0, volume=800_000)

    regime.classify = AsyncMock(return_value=SimpleNamespace(
        market="US", is_rising=False, regime_label="bear", fail_detail="추세 꺾임"))
    action = await s.service.on_price("AAA", 95.0, volume=900_000)

    assert action["exit_reason"] == "stop"


# ── 빈 세션 재시도 (공통 베이스) ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_prepare_session_retries_while_watch_is_empty():
    """후보/일봉이 늦게 준비돼 감시목록이 비면 다음 패스에서 다시 만든다."""
    s = _svc(adx=18.0)
    assert await s.service.prepare_session(TRADE_DATE) == 0

    s.indicator.calc_adx_sync = MagicMock(return_value={"adx": 30.0, "adx_rising": True})

    assert await s.service.prepare_session(TRADE_DATE) == 1


@pytest.mark.asyncio
async def test_prepare_session_empty_retry_is_bounded():
    s = _svc(adx=18.0)
    limit = OverseasIntradayChannelBreakoutService.EMPTY_SESSION_RETRY_PASSES

    for _ in range(limit + 3):
        assert await s.service.prepare_session(TRADE_DATE) == 0

    assert s.candidate_service.get_candidates.await_count == limit
