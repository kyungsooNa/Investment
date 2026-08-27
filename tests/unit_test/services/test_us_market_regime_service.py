"""미장 국면(마켓타이밍) 판정 서비스 테스트.

국내 MarketRegimeService 가 KOSPI/KOSDAQ 지수를 쓰는 것과 달리, 미장은 KIS 에
해외 지수 TR 이 없어 프록시 ETF(QQQ) 일봉으로 같은 MA 추세 로직을 돌린다.
"""
import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytz

from common.overseas_types import OverseasExchange
from common.types import ErrorCode, ResCommonResponse
from services.us_market_regime_service import USMarketRegimeConfig, USMarketRegimeService


NY = pytz.timezone("America/New_York")


def _ok(data):
    return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="ok", data=data)


def _bars(closes, start_day=1):
    """종가 리스트를 오름차순 일봉 rows 로 만든다 (2026-06월 기준)."""
    return [
        {"date": f"202606{start_day + i:02d}", "open": c, "high": c, "low": c, "close": c, "volume": 1000}
        for i, c in enumerate(closes)
    ]


def _svc(closes=None, *, calendar=None, notification_service=None, now=None, rows=None):
    sqs = MagicMock()
    if rows is None:
        rows = _bars(closes if closes is not None else [])
    sqs.get_recent_daily_ohlcv = AsyncMock(return_value=_ok(rows))

    clock = MagicMock()
    clock.get_current_kst_time.return_value = now or NY.localize(datetime(2026, 6, 30, 20, 0))
    clock.is_market_operating_hours.return_value = False
    clock.get_market_open_time.return_value = NY.localize(datetime(2026, 6, 30, 9, 30))

    service = USMarketRegimeService(
        stock_query_service=sqs,
        market_clock=clock,
        us_market_calendar_service=calendar,
        notification_service=notification_service,
        logger=MagicMock(),
    )
    return service, sqs, clock


# ── 데이터 소스 ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_classify_queries_qqq_on_nasd_via_overseas_ohlcv():
    """국내 지수 API가 아니라 해외 일봉 API로 QQQ/NASD를 조회해야 한다."""
    service, sqs, _ = _svc([100.0] * 30)

    await service.classify("US")

    sqs.get_recent_daily_ohlcv.assert_awaited()
    kwargs = sqs.get_recent_daily_ohlcv.await_args.kwargs
    args = sqs.get_recent_daily_ohlcv.await_args.args
    symbol = args[0] if args else kwargs.get("stock_code")
    assert symbol == "QQQ"
    assert kwargs["exchange"] == OverseasExchange.NASD


@pytest.mark.asyncio
async def test_proxy_symbol_is_configurable():
    sqs = MagicMock()
    sqs.get_recent_daily_ohlcv = AsyncMock(return_value=_ok(_bars([100.0] * 30)))
    clock = MagicMock()
    clock.get_current_kst_time.return_value = NY.localize(datetime(2026, 6, 30, 20, 0))
    clock.is_market_operating_hours.return_value = False
    clock.get_market_open_time.return_value = NY.localize(datetime(2026, 6, 30, 9, 30))

    service = USMarketRegimeService(
        stock_query_service=sqs,
        market_clock=clock,
        config=USMarketRegimeConfig(proxy_symbol="SPY", proxy_exchange=OverseasExchange.AMEX),
        logger=MagicMock(),
    )
    await service.classify("US")

    args = sqs.get_recent_daily_ohlcv.await_args.args
    kwargs = sqs.get_recent_daily_ohlcv.await_args.kwargs
    assert (args[0] if args else kwargs.get("stock_code")) == "SPY"
    assert kwargs["exchange"] == OverseasExchange.AMEX


@pytest.mark.asyncio
async def test_unknown_market_raises():
    service, _, _ = _svc([100.0] * 30)
    with pytest.raises(ValueError):
        await service.classify("KOSPI")


# ── 국면 판정 ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_rising_ma_is_bull():
    """꾸준한 우상향이면 bull / is_rising=True."""
    service, _, _ = _svc([100.0 + i for i in range(30)])

    snap = await service.classify("US")

    assert snap.market == "US"
    assert snap.is_rising is True
    assert snap.regime_label == "bull"


@pytest.mark.asyncio
async def test_hard_declining_ma_is_bear():
    """MA가 급락하면 bear / is_rising=False."""
    service, _, _ = _svc([200.0 - i * 3 for i in range(30)])

    snap = await service.classify("US")

    assert snap.is_rising is False
    assert snap.regime_label == "bear"
    assert snap.fail_detail


@pytest.mark.asyncio
async def test_is_bull_us_shortcut():
    service, _, _ = _svc([100.0 + i for i in range(30)])
    assert await service.is_bull_us() is True


@pytest.mark.asyncio
async def test_insufficient_data_is_fail_closed():
    """일봉이 모자라면 진입을 허용하지 않는다(fail-closed)."""
    service, _, _ = _svc([100.0] * 5)

    snap = await service.classify("US")

    assert snap.is_rising is False
    assert snap.fail_detail == "insufficient data"


@pytest.mark.asyncio
async def test_ohlcv_api_failure_is_fail_closed():
    sqs = MagicMock()
    sqs.get_recent_daily_ohlcv = AsyncMock(
        return_value=ResCommonResponse(rt_cd=ErrorCode.API_ERROR.value, msg1="err", data=None)
    )
    clock = MagicMock()
    clock.get_current_kst_time.return_value = NY.localize(datetime(2026, 6, 30, 20, 0))
    clock.is_market_operating_hours.return_value = False
    clock.get_market_open_time.return_value = NY.localize(datetime(2026, 6, 30, 9, 30))
    service = USMarketRegimeService(
        stock_query_service=sqs, market_clock=clock, logger=MagicMock(),
    )

    snap = await service.classify("US")
    assert snap.is_rising is False


@pytest.mark.asyncio
async def test_classify_is_cached_per_us_date():
    service, sqs, _ = _svc([100.0 + i for i in range(30)])

    await service.classify("US")
    await service.classify("US")

    assert sqs.get_recent_daily_ohlcv.await_count == 1


# ── 미국 거래일 계산 ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_previous_trading_day_skips_us_holiday():
    """장중/개장 전 조회는 전 거래일까지만 본다 — 미국 휴장일을 건너뛰어야 한다."""
    calendar = MagicMock()
    # 7/3(금)은 조기폐장이지만 거래일, 7/4(토)·7/5(일)은 휴장 가정
    calendar.is_trading_day.side_effect = lambda ds: ds not in {"20260705", "20260704"}

    now = NY.localize(datetime(2026, 7, 6, 9, 0))  # 월요일 개장 전
    service, sqs, clock = _svc([100.0 + i for i in range(30)], calendar=calendar, now=now)
    clock.is_market_operating_hours.return_value = False
    clock.get_market_open_time.return_value = NY.localize(datetime(2026, 7, 6, 9, 30))

    await service.classify("US")

    assert sqs.get_recent_daily_ohlcv.await_args.kwargs["end_date"] == "20260703"


@pytest.mark.asyncio
async def test_after_close_uses_today_as_end_date():
    """정규장 마감 후에는 당일 봉이 확정이므로 당일을 end_date 로 쓴다."""
    calendar = MagicMock()
    calendar.is_trading_day.return_value = True
    now = NY.localize(datetime(2026, 6, 30, 20, 0))
    service, sqs, clock = _svc([100.0 + i for i in range(30)], calendar=calendar, now=now)
    clock.is_market_operating_hours.return_value = False
    clock.get_market_open_time.return_value = NY.localize(datetime(2026, 6, 30, 9, 30))

    await service.classify("US")

    assert sqs.get_recent_daily_ohlcv.await_args.kwargs["end_date"] == "20260630"


# ── 일일 갱신/알림 ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_refresh_market_timing_emits_notification_and_returns_state():
    notif = MagicMock()
    notif.emit = AsyncMock()
    service, sqs, _ = _svc([100.0 + i for i in range(30)], notification_service=notif)

    result = await service.refresh_market_timing(caller="us_market_timing_daily_update")

    assert result == {"US": True}
    notif.emit.assert_awaited_once()
    kwargs = notif.emit.await_args.kwargs
    assert "QQQ" in kwargs["message"]
    assert kwargs["metadata"]["event"] == "us_market_timing_updated"
    assert kwargs["metadata"]["market"] == "US"


@pytest.mark.asyncio
async def test_refresh_market_timing_invalidates_cache():
    service, sqs, _ = _svc([100.0 + i for i in range(30)])

    await service.classify("US")
    await service.refresh_market_timing()

    assert sqs.get_recent_daily_ohlcv.await_count == 2


@pytest.mark.asyncio
async def test_refresh_market_timing_without_notification_service_is_noop():
    service, _, _ = _svc([100.0 + i for i in range(30)])
    assert await service.refresh_market_timing() == {"US": True}


@pytest.mark.asyncio
async def test_refresh_market_timing_bear_message_has_reason():
    notif = MagicMock()
    notif.emit = AsyncMock()
    service, _, _ = _svc([200.0 - i * 3 for i in range(30)], notification_service=notif)

    result = await service.refresh_market_timing()

    assert result == {"US": False}
    message = notif.emit.await_args.kwargs["message"]
    assert "매수 부적합" in message
