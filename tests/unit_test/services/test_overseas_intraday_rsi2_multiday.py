"""해외 장중 RSI2 멀티데이 보유 테스트.

RSI2 는 마감 15분 전부터 진입 판정을 하는데, 폴링 태스크는 마감 10분 전에 전 전략을
EOD 청산한다 — 같은 날 최대 5분 보유로 끝나 장중 표본이 구조적으로 '0 − 비용' 이
됐다. 엣지 근거(dry-run 멀티데이 재구성: hard stop / trailing 8% / time stop 20일)와
같은 조건으로 검증하려면 장중 경로도 며칠 보유해야 한다.
"""
import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytz

from common.types import ErrorCode, ResCommonResponse
from common.overseas_types import OverseasExchange
from services.overseas_intraday_rsi2_service import OverseasIntradayRSI2Service, OverseasIntradayRSI2ShadowService
from services.us_market_calendar_service import USMarketCalendarService

NY = pytz.timezone("America/New_York")


@pytest.mark.asyncio
async def test_shadow_uses_primary_watch_but_separate_positions_and_skips_regime(tmp_path):
    primary = _svc(tmp_path / "primary.json", now=_at(20, 15, 50)).service
    primary._session_date = "20261120"
    primary._watch = {"MSFT": {"closes": [90.0] * 200 + [110.0, 100.0], "exchange": OverseasExchange.NASD}}
    regime = MagicMock()
    regime.classify = AsyncMock()
    shadow_orders = MagicMock()
    shadow_orders.place_entry = AsyncMock(return_value=_ok({"would_be": True}))
    shadow_orders.place_exit = AsyncMock(return_value=_ok({"would_be": True}))
    shadow = OverseasIntradayRSI2ShadowService(
        primary_service=primary,
        candidate_service=primary._candidate_service,
        stock_query_service=primary._sqs,
        order_execution_service=shadow_orders,
        market_clock=primary._clock,
        market_regime_service=regime,
        state_file=str(tmp_path / "shadow.json"),
    )
    assert await shadow.prepare_session("20261120") == 1
    assert shadow._watch == primary._watch
    assert shadow._watch is not primary._watch
    assert await shadow._is_regime_ok("MSFT") is True
    regime.classify.assert_not_awaited()
    assert shadow._state_file != primary._state_file
    assert (await shadow.on_price("MSFT", 99.0))["action"] == "BUY"
    shadow_orders.place_entry.assert_awaited_once()
    assert (await shadow.on_price("MSFT", 93.0))["action"] == "SELL"
    shadow_orders.place_exit.assert_awaited_once()
    assert primary._positions == {}


def _ok(data):
    return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="ok", data=data)


@pytest.mark.asyncio
async def test_shadow_retries_when_primary_watch_appears_after_open(tmp_path):
    primary = _svc(tmp_path / "primary.json", now=_at(20, 10, 0)).service
    primary._session_date = "20261120"
    shadow = OverseasIntradayRSI2ShadowService(
        primary_service=primary,
        candidate_service=primary._candidate_service,
        stock_query_service=primary._sqs,
        order_execution_service=primary._orders,
        state_file=str(tmp_path / "shadow.json"),
    )
    assert await shadow.prepare_session("20261120") == 0
    primary._watch = {"MSFT": {"closes": [90.0] * 202, "exchange": OverseasExchange.NASD}}
    assert await shadow.prepare_session("20261120") == 1
    assert shadow.watch_codes() == ["MSFT"]


def _write_state(path, *, session_date, entry_date, entry=100.0, stop=95.0, peak=None):
    path.write_text(json.dumps({
        "strategy": OverseasIntradayRSI2Service.STRATEGY_NAME,
        "session_date": session_date,
        "positions": {"HELD": {
            "qty": 3, "entry_price": entry, "stop_price": stop,
            "last_price": entry, "exchange": "NASD",
            "entry_date": entry_date, "peak_price": peak or entry,
        }},
        "entered_today": ["HELD"],
    }), encoding="utf-8")


def _svc(state_file, *, now):
    candidate_service = MagicMock()
    candidate_service.get_candidates = AsyncMock(return_value=[])
    sqs = MagicMock()
    sqs.get_recent_daily_ohlcv = AsyncMock(return_value=_ok([]))
    orders = MagicMock()
    orders.place_entry = AsyncMock(return_value=_ok({"would_be": True}))
    orders.place_exit = AsyncMock(return_value=_ok({"would_be": True}))
    clock = MagicMock()
    clock.get_current_kst_time.return_value = now
    svc = OverseasIntradayRSI2Service(
        candidate_service=candidate_service,
        stock_query_service=sqs,
        order_execution_service=orders,
        market_clock=clock,
        logger=MagicMock(),
        state_file=str(state_file),
        us_market_calendar_service=USMarketCalendarService(market_clock=MagicMock(), logger=MagicMock()),
    )
    return SimpleNamespace(service=svc, orders=orders, clock=clock)


def _at(day, h, m):
    return NY.localize(datetime(2026, 11, day, h, m))


@pytest.mark.asyncio
async def test_eod_close_all_keeps_rsi2_positions_overnight(tmp_path):
    state = tmp_path / "rsi2.json"
    _write_state(state, session_date="20261117", entry_date="20261117")
    s = _svc(state, now=_at(17, 15, 50))
    await s.service.prepare_session("20261117")

    assert await s.service.close_all(reason="eod") == []
    s.orders.place_exit.assert_not_awaited()
    assert "HELD" in s.service.get_state()["positions"]


@pytest.mark.asyncio
async def test_held_position_is_polled_even_when_not_in_watch_list(tmp_path):
    """다음 날 감시목록에 없어도 보유 종목에는 틱이 가야 손절·청산이 돈다."""
    state = tmp_path / "rsi2.json"
    _write_state(state, session_date="20261117", entry_date="20261117")
    s = _svc(state, now=_at(18, 10, 0))
    await s.service.prepare_session("20261118")

    assert "HELD" in s.service.watch_codes()


@pytest.mark.asyncio
async def test_next_day_hard_stop_exits(tmp_path):
    state = tmp_path / "rsi2.json"
    _write_state(state, session_date="20261117", entry_date="20261117")
    s = _svc(state, now=_at(18, 10, 0))
    await s.service.prepare_session("20261118")

    action = await s.service.on_price("HELD", 94.9)
    assert action["action"] == "SELL"
    assert action["exit_reason"] == "stop"


@pytest.mark.asyncio
async def test_trailing_stop_exits_after_peak_drawdown(tmp_path):
    state = tmp_path / "rsi2.json"
    _write_state(state, session_date="20261117", entry_date="20261117")
    s = _svc(state, now=_at(18, 10, 0))
    await s.service.prepare_session("20261118")

    assert await s.service.on_price("HELD", 120.0) is None   # 고점 갱신
    assert await s.service.on_price("HELD", 111.0) is None   # 120×0.92=110.4 위
    action = await s.service.on_price("HELD", 110.0)
    assert action["exit_reason"] == "trailing"


@pytest.mark.asyncio
async def test_trailing_not_armed_while_trail_below_hard_stop(tmp_path):
    """분석기와 같이 trail 이 hard stop 보다 높을 때만 trailing 이 동작한다."""
    state = tmp_path / "rsi2.json"
    _write_state(state, session_date="20261117", entry_date="20261117")
    s = _svc(state, now=_at(18, 10, 0))
    await s.service.prepare_session("20261118")

    await s.service.on_price("HELD", 102.0)      # trail 93.84 < stop 95
    assert await s.service.on_price("HELD", 96.0) is None


@pytest.mark.asyncio
async def test_peak_is_persisted_across_restart(tmp_path):
    state = tmp_path / "rsi2.json"
    _write_state(state, session_date="20261117", entry_date="20261117")
    s = _svc(state, now=_at(18, 10, 0))
    await s.service.prepare_session("20261118")
    await s.service.on_price("HELD", 120.0)
    await s.service.flush_state()

    saved = json.loads(state.read_text(encoding="utf-8"))
    assert saved["positions"]["HELD"]["peak_price"] == pytest.approx(120.0)
    assert saved["positions"]["HELD"]["entry_date"] == "20261117"


@pytest.mark.asyncio
async def test_time_stop_exits_in_close_window_after_20_trading_days(tmp_path):
    # 20261020 → 20261117: 20 거래일 경과 (주말 제외, 해당 기간 NYSE 휴장 없음)
    state = tmp_path / "rsi2.json"
    _write_state(state, session_date="20261116", entry_date="20261020")
    s = _svc(state, now=_at(17, 12, 0))
    await s.service.prepare_session("20261117")

    assert await s.service.on_price("HELD", 101.0) is None   # 마감 창 전에는 유지
    s.clock.get_current_kst_time.return_value = _at(17, 15, 46)
    action = await s.service.on_price("HELD", 101.0)
    assert action["exit_reason"] == "time"


@pytest.mark.asyncio
async def test_time_stop_waits_before_20_trading_days(tmp_path):
    state = tmp_path / "rsi2.json"
    _write_state(state, session_date="20261116", entry_date="20261021")
    s = _svc(state, now=_at(17, 15, 46))
    await s.service.prepare_session("20261117")

    assert await s.service.on_price("HELD", 101.0) is None


@pytest.mark.asyncio
async def test_new_entry_records_entry_date(tmp_path):
    state = tmp_path / "rsi2.json"
    s = _svc(state, now=_at(17, 15, 46))
    s.service._session_date = "20261117"
    await s.service._enter("NEW", 100.0, {"exchange": s.service._default_exchange})

    held = s.service.get_state()["positions"]["NEW"]
    assert held["entry_date"] == "20261117"
    assert held["peak_price"] == pytest.approx(100.0)


def test_strategy_version_bumped_for_holding_model_change():
    assert OverseasIntradayRSI2Service.STRATEGY_VERSION == "2"
