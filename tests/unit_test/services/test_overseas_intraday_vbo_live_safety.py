"""해외 장중 VBO 실전 전환 안전장치 테스트 (P0-1 / P0-4 / P1-4).

VBO 는 공통 베이스(`OverseasIntradayStrategyBase`) 이전의 독립 구현이라 다른 장중
전략에 배선된 안전장치가 빠져 있었다 — 포지션 영속화가 없고, 날이 바뀌면 보유를
지우며, 청산 거부 시 재시도가 없고, 사이징에 손절가를 넘기지 않았다. 실주문 잠금을
푸는 순간 EOD 청산 실패 포지션이 조용히 사라지는 구조라 베이스로 이관한다.
"""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from common.types import ErrorCode, ResCommonResponse
from services.overseas_intraday_strategy_base import OverseasIntradayStrategyBase
from services.overseas_intraday_vbo_service import OverseasIntradayVBOService


def _bar(d, o, h, l, c, v=1000):
    return {"date": d, "open": o, "high": h, "low": l, "close": c, "volume": v}


def _ok(data=None):
    return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="ok", data=data)


def _fail():
    return ResCommonResponse(rt_cd=ErrorCode.API_ERROR.value, msg1="rejected", data=None)


def _svc(state_file=None, *, sizing=None):
    candidate_service = MagicMock()
    candidate_service.get_candidates = AsyncMock(return_value=[{"code": "AAA", "name": "Aaa"}])
    sqs = MagicMock()
    sqs.get_recent_daily_ohlcv = AsyncMock(return_value=_ok([
        _bar("20260511", 100, 110, 100, 105), _bar("20260512", 100, 101, 99, 100),
    ]))
    orders = MagicMock()
    orders.place_entry = AsyncMock(return_value=_ok({"would_be": True}))
    orders.place_exit = AsyncMock(return_value=_ok({"would_be": True}))
    svc = OverseasIntradayVBOService(
        candidate_service=candidate_service,
        stock_query_service=sqs,
        order_execution_service=orders,
        position_sizing_service=sizing,
        logger=MagicMock(),
        k_value=0.5,
        stop_loss_pct=-3.0,
        state_file=str(state_file) if state_file else None,
    )
    return SimpleNamespace(service=svc, orders=orders, sqs=sqs)


def test_vbo_uses_the_common_intraday_base():
    assert issubclass(OverseasIntradayVBOService, OverseasIntradayStrategyBase)


@pytest.mark.asyncio
async def test_vbo_entry_is_persisted_and_restored_after_restart(tmp_path):
    state = tmp_path / "vbo.json"
    first = _svc(state)
    await first.service.prepare_session("20260512")
    await first.service.on_price("AAA", 106.0)
    await first.service.flush_state()

    saved = json.loads(state.read_text(encoding="utf-8"))
    assert saved["positions"]["AAA"]["stop_price"] == pytest.approx(106.0 * 0.97)

    second = _svc(state)
    await second.service.prepare_session("20260512")
    action = await second.service.on_price("AAA", 102.0)
    assert action["exit_reason"] == "stop"


@pytest.mark.asyncio
async def test_vbo_keeps_unclosed_position_across_trading_day(tmp_path):
    """전일 EOD 청산이 실패한 보유를 날이 바뀌었다고 지우면 손절 없는 오버나이트가 된다."""
    state = tmp_path / "vbo.json"
    first = _svc(state)
    await first.service.prepare_session("20260512")
    await first.service.on_price("AAA", 106.0)
    await first.service.flush_state()

    second = _svc(state)
    await second.service.prepare_session("20260513")
    assert "AAA" in second.service.get_state()["positions"]
    assert "AAA" in second.service.watch_codes()


@pytest.mark.asyncio
async def test_vbo_exit_retries_with_lower_limit_when_rejected():
    s = _svc()
    await s.service.prepare_session("20260512")
    await s.service.on_price("AAA", 106.0)
    s.orders.place_exit = AsyncMock(side_effect=[_fail(), _ok({"would_be": True})])

    actions = await s.service.close_all(reason="eod")

    assert len(actions) == 1
    limits = [c.kwargs["limit_price"] for c in s.orders.place_exit.await_args_list]
    assert limits[0] == pytest.approx(106.0)
    assert limits[1] == pytest.approx(106.0 * 0.997)


@pytest.mark.asyncio
async def test_vbo_sizing_receives_stop_price():
    sizing = MagicMock()
    sizing.size = MagicMock(return_value={"qty": 4})
    s = _svc(sizing=sizing)
    await s.service.prepare_session("20260512")
    await s.service.on_price("AAA", 106.0)

    kwargs = sizing.size.call_args.kwargs
    assert kwargs["limit_price_usd"] == pytest.approx(106.0)
    assert kwargs["stop_price_usd"] == pytest.approx(106.0 * 0.97)

