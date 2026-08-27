"""해외 장중 전략 포지션 영속화 테스트 (P0-1).

paper 경로에서는 in-memory 로 충분했지만, 실주문 경로에서는 장중 재시작 한 번에
보유 포지션과 손절가가 사라진다 — 실제 포지션은 계좌에 남아 있는데 시스템이
모르므로 손절도 EOD 청산도 돌지 않는다. 상태를 파일에 남겨 복원한다.
"""
import json
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


def _bar(d, o, h, l, c, v=1_000_000):
    return {"date": d, "open": o, "high": h, "low": l, "close": c, "volume": v}


def _history(n=40):
    return [_bar(f"202607{i+1:02d}" if i < 30 else f"202608{i-29:02d}",
                 98, 100, 96, 99, 1_000_000) for i in range(n)]


def _ok(data):
    return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="ok", data=data)


def _svc(state_file):
    candidate_service = MagicMock()
    candidate_service.get_candidates = AsyncMock(return_value=[
        {"code": "AAA", "name": "Aaa", "exchange": "NASD"},
    ])
    sqs = MagicMock()
    sqs.get_recent_daily_ohlcv = AsyncMock(return_value=_ok(_history()))
    indicator = MagicMock()
    indicator.calc_adx_sync = MagicMock(return_value={"adx": 30.0, "adx_rising": True})
    orders = MagicMock()
    orders.place_entry = AsyncMock(return_value=_ok({"ok": True}))
    orders.place_exit = AsyncMock(return_value=_ok({"ok": True}))
    calendar = MagicMock()
    calendar.get_close_time_str.return_value = "16:00"
    clock = MagicMock()
    clock.get_current_kst_time.return_value = NY.localize(datetime(2026, 8, 18, 12, 45))

    svc = OverseasIntradayChannelBreakoutService(
        candidate_service=candidate_service,
        stock_query_service=sqs,
        indicator_service=indicator,
        order_execution_service=orders,
        session_volume_service=USSessionVolumeService(
            us_market_calendar_service=calendar, logger=MagicMock()),
        market_clock=clock,
        logger=MagicMock(),
        state_file=str(state_file),
    )
    return SimpleNamespace(service=svc, orders=orders)


@pytest.mark.asyncio
async def test_entry_is_persisted_to_state_file(tmp_path):
    state = tmp_path / "cb_state.json"
    s = _svc(state)
    await s.service.prepare_session(TRADE_DATE)

    await s.service.on_price("AAA", 101.0, volume=800_000)
    await s.service.flush_state()

    saved = json.loads(state.read_text(encoding="utf-8"))
    assert saved["session_date"] == TRADE_DATE
    assert "AAA" in saved["positions"]
    assert saved["positions"]["AAA"]["entry_price"] == pytest.approx(101.0)
    assert saved["positions"]["AAA"]["stop_price"] == pytest.approx(96.0)
    assert saved["positions"]["AAA"]["exchange"] == "NASD"
    assert saved["entered_today"] == ["AAA"]


@pytest.mark.asyncio
async def test_restart_restores_position_and_stop(tmp_path):
    """재시작해도 보유와 손절가가 살아 있어야 손절이 돈다."""
    state = tmp_path / "cb_state.json"
    first = _svc(state)
    await first.service.prepare_session(TRADE_DATE)
    await first.service.on_price("AAA", 101.0, volume=800_000)
    await first.service.flush_state()

    # 프로세스 재시작 상당 — 새 인스턴스가 같은 상태 파일을 읽는다
    second = _svc(state)
    await second.service.prepare_session(TRADE_DATE)

    held = second.service.get_state()["positions"]["AAA"]
    assert held["entry_price"] == pytest.approx(101.0)
    assert held["stop_price"] == pytest.approx(96.0)
    assert held["exchange"] == OverseasExchange.NASD  # enum 으로 복원돼야 주문이 나간다

    action = await second.service.on_price("AAA", 95.0, volume=900_000)
    assert action["action"] == "SELL"
    assert action["exit_reason"] == "stop"


@pytest.mark.asyncio
async def test_restart_does_not_reenter_same_symbol(tmp_path):
    """재시작 후 이미 진입했던 종목을 다시 사면 중복 포지션이 된다."""
    state = tmp_path / "cb_state.json"
    first = _svc(state)
    await first.service.prepare_session(TRADE_DATE)
    await first.service.on_price("AAA", 101.0, volume=800_000)
    await first.service.on_price("AAA", 103.0, volume=900_000)   # 보유 중
    await first.service.close_all(reason="eod")                   # 청산 → 보유 없음
    await first.service.flush_state()

    second = _svc(state)
    await second.service.prepare_session(TRADE_DATE)

    # 같은 날 재진입 금지가 재시작 후에도 유지돼야 한다
    assert await second.service.on_price("AAA", 101.0, volume=800_000) is None
    second.orders.place_entry.assert_not_awaited()


@pytest.mark.asyncio
async def test_exit_is_persisted(tmp_path):
    state = tmp_path / "cb_state.json"
    s = _svc(state)
    await s.service.prepare_session(TRADE_DATE)
    await s.service.on_price("AAA", 101.0, volume=800_000)
    await s.service.close_all(reason="eod")
    await s.service.flush_state()

    saved = json.loads(state.read_text(encoding="utf-8"))
    assert saved["positions"] == {}
    assert saved["entered_today"] == ["AAA"]


@pytest.mark.asyncio
async def test_stale_position_from_previous_day_is_kept_and_flagged(tmp_path):
    """전일 EOD 청산이 실패해 남은 포지션은 **버리지 않는다** — 실계좌엔 남아 있다."""
    state = tmp_path / "cb_state.json"
    state.write_text(json.dumps({
        "session_date": "20260817",
        "positions": {"AAA": {"qty": 3, "entry_price": 100.0, "stop_price": 95.0,
                              "last_price": 99.0, "exchange": "NASD"}},
        "entered_today": ["AAA"],
    }), encoding="utf-8")

    s = _svc(state)
    await s.service.prepare_session(TRADE_DATE)

    held = s.service.get_state()["positions"].get("AAA")
    assert held is not None, "전일 미청산 포지션을 조용히 버리면 실포지션이 방치된다"
    assert held["stop_price"] == pytest.approx(95.0)
    s.service._logger.warning.assert_called()

    # 새 거래일이므로 청산은 여전히 가능해야 한다
    actions = await s.service.close_all(reason="eod")
    assert len(actions) == 1


@pytest.mark.asyncio
async def test_new_day_clears_entered_today(tmp_path):
    """entered_today 는 당일 재진입 방지용 — 날이 바뀌면 초기화돼야 한다."""
    state = tmp_path / "cb_state.json"
    state.write_text(json.dumps({
        "session_date": "20260817", "positions": {}, "entered_today": ["AAA"],
    }), encoding="utf-8")

    s = _svc(state)
    await s.service.prepare_session(TRADE_DATE)

    assert s.service.get_state()["entered_today"] == []
    assert await s.service.on_price("AAA", 101.0, volume=800_000) is not None


@pytest.mark.asyncio
async def test_missing_state_file_starts_clean(tmp_path):
    s = _svc(tmp_path / "does_not_exist.json")
    assert await s.service.prepare_session(TRADE_DATE) == 1
    assert s.service.get_state()["positions"] == {}


@pytest.mark.asyncio
async def test_corrupt_state_file_does_not_block_session(tmp_path):
    """상태 파일이 깨져도 세션을 못 열면 그날 전체가 멈춘다 — 경고 후 진행한다."""
    state = tmp_path / "cb_state.json"
    state.write_text("{ 깨진 json", encoding="utf-8")

    s = _svc(state)
    assert await s.service.prepare_session(TRADE_DATE) == 1
    s.service._logger.warning.assert_called()


@pytest.mark.asyncio
async def test_no_state_file_configured_keeps_in_memory_behavior(tmp_path):
    """state_file 미주입(기존 paper 배선)에서는 파일 IO 없이 기존대로 동작한다."""
    s = _svc(tmp_path / "x.json")
    s.service._state_file = None
    await s.service.prepare_session(TRADE_DATE)
    await s.service.on_price("AAA", 101.0, volume=800_000)
    await s.service.flush_state()

    assert not (tmp_path / "x.json").exists()


# ── EOD 청산 폴백 (P0-4) ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_eod_exit_retries_with_aggressive_price_when_rejected(tmp_path):
    """해외는 지정가만 지원한다 — 마지막 폴링가 지정가가 거부되면 오버나이트가 남는다.

    거부 시 공격적 지정가(아래로 슬리피지 허용)로 재시도해야 한다.
    """
    s = _svc(tmp_path / "cb.json")
    await s.service.prepare_session(TRADE_DATE)
    await s.service.on_price("AAA", 101.0, volume=800_000)

    s.orders.place_exit = AsyncMock(side_effect=[
        ResCommonResponse(rt_cd=ErrorCode.API_ERROR.value, msg1="거부", data=None),
        _ok({"ok": True}),
    ])

    actions = await s.service.close_all(reason="eod")

    assert len(actions) == 1, "재시도로 청산이 성사돼야 한다"
    assert s.orders.place_exit.await_count == 2
    first = s.orders.place_exit.await_args_list[0].kwargs["limit_price"]
    second = s.orders.place_exit.await_args_list[1].kwargs["limit_price"]
    assert second < first, "재시도는 더 공격적인(낮은) 지정가여야 체결된다"
    assert s.service.get_state()["positions"] == {}


@pytest.mark.asyncio
async def test_eod_exit_keeps_position_and_alerts_when_all_retries_fail(tmp_path):
    """끝내 실패하면 포지션을 지우지 않는다 — 실계좌엔 남아 있다."""
    s = _svc(tmp_path / "cb.json")
    await s.service.prepare_session(TRADE_DATE)
    await s.service.on_price("AAA", 101.0, volume=800_000)

    s.orders.place_exit = AsyncMock(return_value=ResCommonResponse(
        rt_cd=ErrorCode.API_ERROR.value, msg1="거부", data=None))

    actions = await s.service.close_all(reason="eod")

    assert actions == []
    assert "AAA" in s.service.get_state()["positions"]
    s.service._logger.error.assert_called()


@pytest.mark.asyncio
async def test_stop_exit_also_retries(tmp_path):
    """손절도 미체결로 흘러가면 손실이 커진다 — 같은 폴백을 적용한다."""
    s = _svc(tmp_path / "cb.json")
    await s.service.prepare_session(TRADE_DATE)
    await s.service.on_price("AAA", 101.0, volume=800_000)

    s.orders.place_exit = AsyncMock(side_effect=[
        ResCommonResponse(rt_cd=ErrorCode.API_ERROR.value, msg1="거부", data=None),
        _ok({"ok": True}),
    ])

    action = await s.service.on_price("AAA", 95.0, volume=900_000)

    assert action is not None
    assert s.orders.place_exit.await_count == 2
