"""해외 장중 VBO 라이브(paper) 경로 서비스 테스트.

dry-run(마감 후 완성 일봉 사후 평가)과 달리 장중 폴링가로 진입/청산을 판정한다.
주문은 OverseasOrderExecutionService 위임 — live_enabled=False 이면 would-be 만
기록되므로 실주문은 발생하지 않는다.
"""
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from services.overseas_intraday_vbo_service import OverseasIntradayVBOService
from common.types import ErrorCode, ResCommonResponse
from common.overseas_types import OverseasExchange


def _bar(d, o, h, l, c, v=1000):
    return {"date": d, "open": o, "high": h, "low": l, "close": c, "volume": v}


def _ok(data=None):
    return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="ok", data=data)


def _fail():
    return ResCommonResponse(rt_cd=ErrorCode.API_ERROR.value, msg1="rejected", data=None)


def _svc(*, candidates=None, bars=None, max_positions=5, sizing=None,
         regime=None, market_timing_gate=True):
    candidate_service = MagicMock()
    candidate_service.get_candidates = AsyncMock(return_value=candidates if candidates is not None else [
        {"code": "AAA", "name": "Aaa", "exchange": "NASD", "avg_trading_value": 10_000_000.0},
    ])
    sqs = MagicMock()
    # 기본: 전일 range 10(100~110), 당일 시가 100 → target = 105
    sqs.get_recent_daily_ohlcv = AsyncMock(return_value=_ok(bars if bars is not None else [
        _bar("20260511", 100, 110, 100, 105), _bar("20260512", 100, 101, 99, 100),
    ]))
    orders = MagicMock()
    orders.place_entry = AsyncMock(return_value=_ok({"would_be": True}))
    orders.place_exit = AsyncMock(return_value=_ok({"would_be": True}))

    service = OverseasIntradayVBOService(
        candidate_service=candidate_service,
        stock_query_service=sqs,
        order_execution_service=orders,
        position_sizing_service=sizing,
        logger=MagicMock(),
        k_value=0.5,
        stop_loss_pct=-3.0,
        max_positions=max_positions,
        market_regime_service=regime,
        market_timing_gate=market_timing_gate,
    )
    return SimpleNamespace(service=service, candidate_service=candidate_service, sqs=sqs,
                           orders=orders, regime=regime)


# ── 세션 준비 ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_prepare_session_computes_target_from_prev_range_and_open():
    s = _svc()

    watched = await s.service.prepare_session("20260512", exchange=OverseasExchange.NASD)

    assert watched == 1
    assert s.service.get_state()["watch"]["AAA"]["target"] == pytest.approx(105.0)


@pytest.mark.asyncio
async def test_prepare_session_skips_symbol_without_today_bar():
    """당일 봉이 아직 없으면 시가를 알 수 없다 → 감시 제외(fail-closed)."""
    s = _svc(bars=[_bar("20260508", 100, 110, 100, 105), _bar("20260511", 100, 110, 100, 105)])

    watched = await s.service.prepare_session("20260512", exchange=OverseasExchange.NASD)

    assert watched == 0
    assert s.service.get_state()["watch"] == {}


@pytest.mark.asyncio
async def test_prepare_session_is_idempotent_for_same_date():
    s = _svc()

    await s.service.prepare_session("20260512", exchange=OverseasExchange.NASD)
    await s.service.prepare_session("20260512", exchange=OverseasExchange.NASD)

    assert s.candidate_service.get_candidates.await_count == 1


# ── 진입 ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_on_price_below_target_does_not_order():
    s = _svc()
    await s.service.prepare_session("20260512")

    assert await s.service.on_price("AAA", 104.9) is None
    s.orders.place_entry.assert_not_awaited()


@pytest.mark.asyncio
async def test_on_price_breakout_places_entry_at_polled_price():
    """라이브 경로의 진입가는 목표가가 아니라 실제 폴링가다(체결 가정 정직화)."""
    s = _svc()
    await s.service.prepare_session("20260512")

    action = await s.service.on_price("AAA", 106.0)

    assert action["action"] == "BUY"
    _, kwargs = s.orders.place_entry.call_args
    assert kwargs["code"] == "AAA"
    assert kwargs["limit_price"] == 106.0
    assert kwargs["qty"] == 1  # 사이징 미주입 시 1주
    assert s.service.get_state()["positions"]["AAA"]["stop_price"] == pytest.approx(106.0 * 0.97)


@pytest.mark.asyncio
async def test_on_price_does_not_reenter_same_day():
    s = _svc()
    await s.service.prepare_session("20260512")
    await s.service.on_price("AAA", 106.0)
    await s.service.on_price("AAA", 107.0)

    assert s.orders.place_entry.await_count == 1


@pytest.mark.asyncio
async def test_entry_uses_position_sizing_when_injected():
    sizing = MagicMock()
    sizing.size = MagicMock(return_value={"qty": 9, "notional_usd": 954.0})
    s = _svc(sizing=sizing)
    await s.service.prepare_session("20260512")

    await s.service.on_price("AAA", 106.0)

    assert s.orders.place_entry.call_args.kwargs["qty"] == 9
    assert sizing.size.call_args.kwargs["limit_price_usd"] == 106.0


@pytest.mark.asyncio
async def test_entry_not_registered_when_order_rejected():
    s = _svc()
    s.orders.place_entry = AsyncMock(return_value=_fail())
    await s.service.prepare_session("20260512")

    action = await s.service.on_price("AAA", 106.0)

    assert action is None
    assert s.service.get_state()["positions"] == {}


@pytest.mark.asyncio
async def test_max_positions_blocks_new_entry():
    s = _svc(candidates=[
        {"code": "AAA", "exchange": "NASD"}, {"code": "BBB", "exchange": "NASD"},
    ], max_positions=1)
    await s.service.prepare_session("20260512")

    await s.service.on_price("AAA", 106.0)
    action = await s.service.on_price("BBB", 106.0)

    assert action is None
    assert s.orders.place_entry.await_count == 1


# ── 청산 ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_stop_loss_exit_when_price_breaks_stop():
    s = _svc()
    await s.service.prepare_session("20260512")
    await s.service.on_price("AAA", 100.0 + 6.0)  # 진입 106 → stop 102.82

    action = await s.service.on_price("AAA", 102.0)

    assert action["action"] == "SELL"
    assert s.orders.place_exit.call_args.kwargs["reason"] == "stop"
    assert s.service.get_state()["positions"] == {}


@pytest.mark.asyncio
async def test_close_all_exits_open_positions_with_reason():
    s = _svc()
    await s.service.prepare_session("20260512")
    await s.service.on_price("AAA", 106.0)

    actions = await s.service.close_all(reason="eod")

    assert len(actions) == 1
    assert s.orders.place_exit.call_args.kwargs["reason"] == "eod"
    assert s.service.get_state()["positions"] == {}


@pytest.mark.asyncio
async def test_close_all_is_noop_without_positions():
    s = _svc()

    assert await s.service.close_all(reason="eod") == []
    s.orders.place_exit.assert_not_awaited()


# ── 안전 계약 ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_service_has_no_direct_broker_dependency():
    """실주문 잠금은 OverseasOrderExecutionService 가 단독으로 관장한다."""
    s = _svc()
    assert not hasattr(s.service, "_broker")


@pytest.mark.asyncio
async def test_prepare_session_skips_candidates_without_a_code():
    deps = _svc(candidates=[{"name": "코드없음"}])

    assert await deps.service.prepare_session("20260512") == 0
    deps.sqs.get_recent_daily_ohlcv.assert_not_awaited()


@pytest.mark.asyncio
async def test_setup_build_absorbs_an_ohlcv_lookup_error():
    deps = _svc()
    deps.sqs.get_recent_daily_ohlcv = AsyncMock(side_effect=RuntimeError("조회 실패"))

    assert await deps.service.prepare_session("20260512") == 0
    deps.service._logger.warning.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bars",
    [
        None,                                                  # 실패 응답
        [],                                                    # 빈 데이터
        [_bar("20260512", 100, 101, 99, 100)],                 # 봉 1개
        # 전일 range 0
        [_bar("20260511", 100, 100, 100, 100), _bar("20260512", 100, 101, 99, 100)],
        # 당일 시가 0
        [_bar("20260511", 100, 110, 100, 105), _bar("20260512", 0, 101, 99, 100)],
    ],
)
async def test_setup_is_skipped_for_unusable_bars(bars):
    deps = _svc()
    deps.sqs.get_recent_daily_ohlcv = AsyncMock(
        return_value=_fail() if bars is None else _ok(bars)
    )

    assert await deps.service.prepare_session("20260512") == 0


@pytest.mark.asyncio
async def test_watch_codes_lists_the_prepared_setups():
    deps = _svc()
    await deps.service.prepare_session("20260512")

    assert deps.service.watch_codes() == ["AAA"]


@pytest.mark.asyncio
async def test_non_positive_price_ticks_are_ignored():
    deps = _svc()
    await deps.service.prepare_session("20260512")

    assert await deps.service.on_price("AAA", 0) is None
    assert await deps.service.on_price("AAA", "가격아님") is None


@pytest.mark.asyncio
async def test_ticks_for_unwatched_codes_are_ignored():
    deps = _svc()
    await deps.service.prepare_session("20260512")

    assert await deps.service.on_price("ZZZ", 200.0) is None


@pytest.mark.asyncio
async def test_a_held_position_ignores_ticks_above_the_stop():
    deps = _svc()
    await deps.service.prepare_session("20260512")
    await deps.service.on_price("AAA", 106.0)

    assert await deps.service.on_price("AAA", 105.0) is None


@pytest.mark.asyncio
async def test_rejected_exit_keeps_the_position(caplog):
    deps = _svc()
    await deps.service.prepare_session("20260512")
    await deps.service.on_price("AAA", 106.0)
    deps.orders.place_exit = AsyncMock(return_value=_fail())

    assert await deps.service.on_price("AAA", 1.0) is None
    assert "AAA" in deps.service.get_state()["positions"]


@pytest.mark.asyncio
async def test_exit_is_a_noop_for_a_code_that_is_not_held():
    deps = _svc()

    assert await deps.service._exit("AAA", 100.0, reason="stop") is None


def test_sizing_errors_and_unparsable_quantities_block_entry():
    sizing = MagicMock()
    sizing.size = MagicMock(side_effect=RuntimeError("사이징 실패"))
    deps = _svc(sizing=sizing)

    assert deps.service._resolve_qty(100.0) == 0
    deps.service._logger.warning.assert_called_once()

    sizing.size = MagicMock(return_value={"qty": "수량아님"})
    assert deps.service._resolve_qty(100.0) == 0

    sizing.size = MagicMock(return_value={})
    assert deps.service._resolve_qty(100.0) == 0


def test_float_coercion_defaults_to_zero():
    from services.overseas_intraday_vbo_service import OverseasIntradayVBOService as Svc

    assert Svc._f(None) == 0.0
    assert Svc._f("120") == 120.0
    assert Svc._f("가격") == 0.0
    assert Svc._f(object()) == 0.0


# ── 마켓타이밍 게이트 ────────────────────────────────────────────────────

def _regime(*, is_rising=True, error=None):
    """USMarketRegimeService 스텁. error 를 주면 classify 가 예외를 던진다."""
    svc = MagicMock()
    if error is not None:
        svc.classify = AsyncMock(side_effect=error)
    else:
        svc.classify = AsyncMock(return_value=SimpleNamespace(
            market="US",
            is_rising=is_rising,
            regime_label="bull" if is_rising else "bear",
            fail_detail="" if is_rising else "MA hard decline: -1.20% < -0.50%",
        ))
    return svc


@pytest.mark.asyncio
async def test_bear_regime_blocks_entry_on_breakout():
    """돌파해도 미국장이 하락 국면이면 신규 진입하지 않는다."""
    s = _svc(regime=_regime(is_rising=False))
    await s.service.prepare_session("20260512")

    assert await s.service.on_price("AAA", 106.0) is None
    s.orders.place_entry.assert_not_awaited()
    assert s.service.get_state()["positions"] == {}


@pytest.mark.asyncio
async def test_bull_regime_allows_entry():
    s = _svc(regime=_regime(is_rising=True))
    await s.service.prepare_session("20260512")

    action = await s.service.on_price("AAA", 106.0)

    assert action["action"] == "BUY"
    s.orders.place_entry.assert_awaited_once()
    s.regime.classify.assert_awaited_with("US")


@pytest.mark.asyncio
async def test_regime_lookup_failure_is_fail_closed():
    """국면 조회가 실패하면 진입을 막는다 — 모르는 상태로 발사하지 않는다."""
    s = _svc(regime=_regime(error=RuntimeError("조회 실패")))
    await s.service.prepare_session("20260512")

    assert await s.service.on_price("AAA", 106.0) is None
    s.orders.place_entry.assert_not_awaited()
    s.service._logger.warning.assert_called()


@pytest.mark.asyncio
async def test_gate_disabled_ignores_bear_regime():
    s = _svc(regime=_regime(is_rising=False), market_timing_gate=False)
    await s.service.prepare_session("20260512")

    assert await s.service.on_price("AAA", 106.0) is not None
    s.orders.place_entry.assert_awaited_once()
    s.regime.classify.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_regime_service_keeps_existing_behavior():
    """게이트 미배선 환경(regime=None)에서는 기존과 동일하게 진입한다."""
    s = _svc()
    await s.service.prepare_session("20260512")

    assert await s.service.on_price("AAA", 106.0) is not None
    s.orders.place_entry.assert_awaited_once()


@pytest.mark.asyncio
async def test_stop_loss_exit_runs_even_in_bear_regime():
    """게이트는 신규 진입만 막는다 — 보유분 손절은 국면과 무관하게 실행된다."""
    regime = _regime(is_rising=True)
    s = _svc(regime=regime)
    await s.service.prepare_session("20260512")
    await s.service.on_price("AAA", 106.0)

    regime.classify = AsyncMock(return_value=SimpleNamespace(
        market="US", is_rising=False, regime_label="bear", fail_detail="추세 꺾임",
    ))
    action = await s.service.on_price("AAA", 100.0)  # stop = 106 * 0.97 = 102.82

    assert action["action"] == "SELL"
    assert action["exit_reason"] == "stop"
    s.orders.place_exit.assert_awaited_once()


@pytest.mark.asyncio
async def test_eod_close_all_runs_even_in_bear_regime():
    regime = _regime(is_rising=True)
    s = _svc(regime=regime)
    await s.service.prepare_session("20260512")
    await s.service.on_price("AAA", 106.0)

    regime.classify = AsyncMock(return_value=SimpleNamespace(
        market="US", is_rising=False, regime_label="bear", fail_detail="추세 꺾임",
    ))
    actions = await s.service.close_all(reason="eod")

    assert len(actions) == 1
    assert actions[0]["exit_reason"] == "eod"


@pytest.mark.asyncio
async def test_gate_not_consulted_when_price_below_target():
    """돌파 전에는 국면을 조회하지 않는다 — 틱마다 불필요한 판정을 돌리지 않는다."""
    s = _svc(regime=_regime(is_rising=True))
    await s.service.prepare_session("20260512")

    await s.service.on_price("AAA", 104.9)

    s.regime.classify.assert_not_awaited()
