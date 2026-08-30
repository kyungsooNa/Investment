"""해외 VBO dry-run 신호 서비스 테스트 (Phase 3).

후보(Phase 1-3) → 일봉(Phase 1-1 어댑터) → VBO 일봉 진입 규칙(Phase 2) → shadow 저널.
**주문 경로 없음**: order_execution 미주입, 저널 기록만. 해외 주문은 실전 TR만
존재하므로 dry-run 단계에서 실주문이 절대 발생하지 않음을 보장한다.
"""
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from services.overseas_vbo_dryrun_service import OverseasVBODryRunService
from services.overseas_position_sizing_service import OverseasPositionSizingService
from common.types import ErrorCode, ResCommonResponse
from common.overseas_types import OverseasExchange


def _bar(d, o, h, l, c, v=1000):
    return {"date": d, "open": o, "high": h, "low": l, "close": c, "volume": v}


def _ohlcv(bars):
    return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="ok", data=bars)


def _macd_breakout_bars(n=60):
    bars = []
    for i in range(n - 2):
        close = 100 + i
        bars.append(_bar(f"202604{i+1:02d}", close, close + 2, close - 2, close))
    bars.append(_bar("20260511", 150, 160, 140, 155))
    bars.append(_bar("20260512", 150, 170, 148, 166))
    return bars


@pytest.fixture
def svc():
    candidate_service = MagicMock()
    candidate_service.get_candidates = AsyncMock(return_value=[
        {"code": "AAA", "name": "Aaa", "exchange": "NASD", "avg_trading_value": 10_000_000.0},
    ])
    sqs = MagicMock()
    journal = MagicMock()

    service = OverseasVBODryRunService(
        candidate_service=candidate_service,
        stock_query_service=sqs,
        shadow_journal=journal,
        logger=MagicMock(),
        k_value=0.5,
        stop_loss_pct=-3.0,
    )
    return SimpleNamespace(service=service, candidate_service=candidate_service, sqs=sqs, journal=journal)


@pytest.mark.asyncio
async def test_scan_emits_buy_on_breakout(svc):
    # prev range 10 → target = 100+5 = 105, 당일고 120 >= 105 → BUY
    bars = [_bar("20260511", 100, 110, 100, 105), _bar("20260512", 100, 120, 104, 115)]
    svc.sqs.get_recent_daily_ohlcv = AsyncMock(return_value=_ohlcv(bars))

    signals = await svc.service.scan_dry_run(exchange=OverseasExchange.NASD)

    assert len(signals) == 1
    assert signals[0]["code"] == "AAA"
    assert signals[0]["action"] == "BUY"
    assert signals[0]["target"] == 105.0


@pytest.mark.asyncio
async def test_scan_emits_same_day_eod_exit(svc):
    """돌파 후 당일저가 손절가 미터치 → 판정 가능(eod) 청산. bracket 양끝이 같다."""
    # target=105, stop=105*0.97=101.85, 당일저 104 > stop → eod 청산(종가 115)
    bars = [_bar("20260511", 100, 110, 100, 105), _bar("20260512", 100, 120, 104, 115)]
    svc.sqs.get_recent_daily_ohlcv = AsyncMock(return_value=_ohlcv(bars))

    signals = await svc.service.scan_dry_run(exchange=OverseasExchange.NASD)

    expected = (115.0 / 105.0 - 1) * 100
    assert signals[0]["exit_reason"] == "eod"
    assert signals[0]["exit_decidable"] is True
    assert signals[0]["exit_price"] == 115.0
    assert signals[0]["realized_pct"] == pytest.approx(expected)
    assert signals[0]["realized_pct_pessimistic"] == pytest.approx(expected)
    assert signals[0]["realized_pct_optimistic"] == pytest.approx(expected)


@pytest.mark.asyncio
async def test_scan_marks_stop_touch_as_undecided(svc):
    """당일저가가 손절가 이하 → 저가 시점이 진입 전/후인지 일봉만으론 판정 불가.

    손절 확정으로 단정하지 않고 비관(손절)·낙관(종가) bracket 을 함께 동봉한다.
    """
    # target=105, stop=101.85, 당일저 100 <= stop → 판정 불가
    bars = [_bar("20260511", 100, 110, 100, 105), _bar("20260512", 100, 120, 100, 102)]
    svc.sqs.get_recent_daily_ohlcv = AsyncMock(return_value=_ohlcv(bars))

    signals = await svc.service.scan_dry_run(exchange=OverseasExchange.NASD)

    assert signals[0]["exit_reason"] == "undecided"
    assert signals[0]["exit_decidable"] is False
    assert signals[0]["realized_pct_pessimistic"] == pytest.approx(-3.0)
    assert signals[0]["realized_pct_optimistic"] == pytest.approx((102.0 / 105.0 - 1) * 100)
    # 하위호환: realized_pct/exit_price 는 비관(손절) 값을 유지한다.
    assert signals[0]["realized_pct"] == pytest.approx(-3.0)
    assert signals[0]["exit_price"] == pytest.approx(101.85)


@pytest.mark.asyncio
async def test_scan_does_not_force_stop_when_target_far_above_open(svc):
    """회귀: 손절가가 시가 이상이면 당일저가는 항상 손절가 이하 → 구모델은 무조건 손절.

    0.5×prev_range 가 시가의 3%(손절폭) 를 넘는 돌파는 진입 전 가격대만으로 손절이
    강제 판정돼 통계가 하향 편향됐다. 이런 건은 판정 불가로 분류돼야 한다.
    """
    # prev range 20 → target = 100+10 = 110, stop = 106.7 > 시가 100 → low<=stop 자동 성립
    bars = [_bar("20260511", 100, 120, 100, 110), _bar("20260512", 100, 130, 99, 128)]
    svc.sqs.get_recent_daily_ohlcv = AsyncMock(return_value=_ohlcv(bars))

    signals = await svc.service.scan_dry_run(exchange=OverseasExchange.NASD)

    assert signals[0]["exit_reason"] == "undecided"
    assert signals[0]["exit_decidable"] is False
    # 종가 128 은 진입가 110 대비 +16% — 손절 확정으로 단정하면 통째로 버려지는 구간이다.
    assert signals[0]["realized_pct_optimistic"] == pytest.approx((128.0 / 110.0 - 1) * 100)


@pytest.mark.asyncio
async def test_scan_records_bar_ohlc_in_snapshot(svc):
    """저널 snapshot 에 당일 일봉 OHLC 를 동봉해 청산 모델을 사후 재계산 가능하게 한다."""
    bars = [_bar("20260511", 100, 110, 100, 105), _bar("20260512", 100, 120, 104, 115)]
    svc.sqs.get_recent_daily_ohlcv = AsyncMock(return_value=_ohlcv(bars))

    await svc.service.scan_dry_run(exchange=OverseasExchange.NASD)

    snapshot = svc.journal.record.call_args.kwargs["snapshot"]
    assert snapshot["bar"] == {
        "date": "20260512", "open": 100, "high": 120, "low": 104, "close": 115,
    }


@pytest.mark.asyncio
async def test_scan_no_signal_when_no_breakout(svc):
    bars = [_bar("20260511", 100, 110, 100, 105), _bar("20260512", 100, 104, 100, 103)]
    svc.sqs.get_recent_daily_ohlcv = AsyncMock(return_value=_ohlcv(bars))

    signals = await svc.service.scan_dry_run(exchange=OverseasExchange.NASD)

    assert signals == []
    svc.journal.record.assert_not_called()


@pytest.mark.asyncio
async def test_scan_macd_filter_enabled_fetches_enough_bars_and_blocks_weak_histogram(svc):
    """MACD 필터 ON이면 추가 일봉을 조회하고 히스토그램 조건 미달 신호를 막는다."""
    svc.service._macd_filter_enabled = True
    svc.service._macd_min_histogram = 999.0
    svc.sqs.get_recent_daily_ohlcv = AsyncMock(return_value=_ohlcv(_macd_breakout_bars()))

    signals = await svc.service.scan_dry_run(exchange=OverseasExchange.NASD)

    assert signals == []
    _, kwargs = svc.sqs.get_recent_daily_ohlcv.await_args
    assert kwargs["limit"] > 3
    svc.journal.record.assert_not_called()


@pytest.mark.asyncio
async def test_scan_macd_filter_enabled_records_metrics_when_passing(svc):
    """MACD 필터 통과 시 would-be 신호에 판정 지표를 동봉한다."""
    svc.service._macd_filter_enabled = True
    svc.service._macd_min_histogram = -999.0
    svc.service._macd_histogram_rising_bars = 0
    svc.sqs.get_recent_daily_ohlcv = AsyncMock(return_value=_ohlcv(_macd_breakout_bars()))

    signals = await svc.service.scan_dry_run(exchange=OverseasExchange.NASD)

    assert len(signals) == 1
    assert signals[0]["macd_filter_enabled"] is True
    assert signals[0]["macd_histogram"] is not None
    assert signals[0]["macd_signal"] is not None


@pytest.mark.asyncio
async def test_scan_records_to_shadow_journal_with_dryrun_source(svc):
    bars = [_bar("20260511", 100, 110, 100, 105), _bar("20260512", 100, 120, 104, 115)]
    svc.sqs.get_recent_daily_ohlcv = AsyncMock(return_value=_ohlcv(bars))

    await svc.service.scan_dry_run(exchange=OverseasExchange.NASD)

    svc.journal.record.assert_called_once()
    _, kwargs = svc.journal.record.call_args
    assert kwargs["signal_source"] == "overseas_dryrun"
    assert kwargs["code"] == "AAA"


@pytest.mark.asyncio
async def test_scan_skips_insufficient_bars(svc):
    svc.sqs.get_recent_daily_ohlcv = AsyncMock(return_value=_ohlcv([_bar("20260512", 100, 120, 104, 115)]))

    signals = await svc.service.scan_dry_run(exchange=OverseasExchange.NASD)

    assert signals == []


@pytest.mark.asyncio
async def test_scan_passes_overseas_exchange_downstream(svc):
    bars = [_bar("20260511", 100, 110, 100, 105), _bar("20260512", 100, 120, 104, 115)]
    svc.sqs.get_recent_daily_ohlcv = AsyncMock(return_value=_ohlcv(bars))

    await svc.service.scan_dry_run(exchange=OverseasExchange.NYSE)

    # 후보 조회와 일봉 조회 모두 해외 거래소 인자로 위임
    _, cand_kwargs = svc.candidate_service.get_candidates.call_args
    assert cand_kwargs.get("exchange") == OverseasExchange.NYSE or svc.candidate_service.get_candidates.call_args[0][0] == OverseasExchange.NYSE
    _, ohlcv_kwargs = svc.sqs.get_recent_daily_ohlcv.await_args
    assert ohlcv_kwargs.get("exchange") == OverseasExchange.NYSE


@pytest.mark.asyncio
async def test_scan_has_no_order_path(svc):
    """서비스는 order_execution 의존을 갖지 않는다(실주문 불가 보장)."""
    assert not hasattr(svc.service, "_order_execution_service")
    assert not hasattr(svc.service, "_order_service")


@pytest.mark.asyncio
async def test_scan_omits_qty_when_no_sizing(svc):
    """사이징 서비스 미주입 시 신호에 qty 를 넣지 않는다(하위 호환)."""
    bars = [_bar("20260511", 100, 110, 100, 105), _bar("20260512", 100, 120, 104, 115)]
    svc.sqs.get_recent_daily_ohlcv = AsyncMock(return_value=_ohlcv(bars))

    signals = await svc.service.scan_dry_run(exchange=OverseasExchange.NASD)

    assert "qty" not in signals[0]


@pytest.mark.asyncio
async def test_scan_includes_qty_when_sizing_injected():
    """사이징 서비스 주입 시 would-be qty/notional 을 신호에 동봉한다(주문 경로 없음)."""
    candidate_service = MagicMock()
    candidate_service.get_candidates = AsyncMock(return_value=[
        {"code": "AAA", "name": "Aaa", "exchange": "NASD", "avg_trading_value": 10_000_000.0},
    ])
    sqs = MagicMock()
    bars = [_bar("20260511", 100, 110, 100, 105), _bar("20260512", 100, 120, 104, 115)]
    sqs.get_recent_daily_ohlcv = AsyncMock(return_value=_ohlcv(bars))

    sizing = MagicMock()
    sizing.size = MagicMock(return_value={"qty": 9, "notional_usd": 945.0, "reason": "slot"})

    service = OverseasVBODryRunService(
        candidate_service=candidate_service,
        stock_query_service=sqs,
        shadow_journal=MagicMock(),
        logger=MagicMock(),
        position_sizing_service=sizing,
    )

    signals = await service.scan_dry_run(exchange=OverseasExchange.NASD)

    assert signals[0]["qty"] == 9
    assert signals[0]["notional_usd"] == 945.0
    # entry_price(=target 105) 로 사이징 호출
    _, kwargs = sizing.size.call_args
    assert kwargs["limit_price_usd"] == 105.0
    # 사이징 주입돼도 order_execution 의존은 없다
    assert not hasattr(service, "_order_execution_service")


def _sizing_svc(slot_usd=1000.0):
    return OverseasPositionSizingService(slot_usd=slot_usd)


def _breakout_service(*, fx_provider=None, sizing=None):
    candidate_service = MagicMock()
    candidate_service.get_candidates = AsyncMock(return_value=[
        {"code": "AAA", "name": "Aaa", "exchange": "NASD", "avg_trading_value": 10_000_000.0},
    ])
    sqs = MagicMock()
    bars = [_bar("20260511", 100, 110, 100, 105), _bar("20260512", 100, 120, 104, 115)]
    sqs.get_recent_daily_ohlcv = AsyncMock(return_value=_ohlcv(bars))
    return OverseasVBODryRunService(
        candidate_service=candidate_service,
        stock_query_service=sqs,
        shadow_journal=MagicMock(),
        logger=MagicMock(),
        position_sizing_service=sizing if sizing is not None else _sizing_svc(),
        fx_provider=fx_provider,
    )


@pytest.mark.asyncio
async def test_scan_passes_fx_to_sizing_and_records_krw_exposure():
    """fx_provider 주입 시 scan당 FX를 조회해 사이징에 전달하고 krw_exposure 를 동봉한다."""
    fx_provider = AsyncMock(return_value=1350.0)
    service = _breakout_service(fx_provider=fx_provider)

    signals = await service.scan_dry_run(exchange=OverseasExchange.NASD)

    # entry=105 → qty=floor(1000/105)=9, notional=945, krw=945*1350
    assert signals[0]["qty"] == 9
    assert signals[0]["fx_krw_per_usd"] == 1350.0
    assert signals[0]["krw_exposure"] == pytest.approx(945.0 * 1350.0)


@pytest.mark.asyncio
async def test_scan_omits_krw_when_fx_provider_returns_none():
    """fx_provider 가 None 반환 시 KRW 환산을 생략한다(qty 는 유지, 하위 호환)."""
    service = _breakout_service(fx_provider=AsyncMock(return_value=None))

    signals = await service.scan_dry_run(exchange=OverseasExchange.NASD)

    assert signals[0]["qty"] == 9
    assert "krw_exposure" not in signals[0]
    assert "fx_krw_per_usd" not in signals[0]


@pytest.mark.asyncio
async def test_scan_tolerates_fx_provider_error():
    """fx_provider 예외는 삼키고 KRW 없이 신호를 산출한다(실주문/수집 중단 없음)."""
    service = _breakout_service(fx_provider=AsyncMock(side_effect=RuntimeError("boom")))

    signals = await service.scan_dry_run(exchange=OverseasExchange.NASD)

    assert signals[0]["qty"] == 9
    assert "krw_exposure" not in signals[0]


@pytest.mark.asyncio
async def test_scan_fetches_fx_once_per_scan():
    """후보가 여러 개여도 FX 조회는 scan당 1회만 수행한다."""
    fx_provider = AsyncMock(return_value=1350.0)
    service = _breakout_service(fx_provider=fx_provider)
    service._candidate_service.get_candidates = AsyncMock(return_value=[
        {"code": "AAA", "avg_trading_value": 1.0},
        {"code": "BBB", "avg_trading_value": 1.0},
    ])

    await service.scan_dry_run(exchange=OverseasExchange.NASD)

    fx_provider.assert_awaited_once()


@pytest.mark.asyncio
async def test_scan_skips_fx_when_no_sizing_service():
    """사이징 미주입 시 fx_provider 를 호출하지 않는다(KRW 산출 불가)."""
    fx_provider = AsyncMock(return_value=1350.0)
    candidate_service = MagicMock()
    candidate_service.get_candidates = AsyncMock(return_value=[
        {"code": "AAA", "avg_trading_value": 1.0},
    ])
    sqs = MagicMock()
    bars = [_bar("20260511", 100, 110, 100, 105), _bar("20260512", 100, 120, 104, 115)]
    sqs.get_recent_daily_ohlcv = AsyncMock(return_value=_ohlcv(bars))
    service = OverseasVBODryRunService(
        candidate_service=candidate_service,
        stock_query_service=sqs,
        shadow_journal=MagicMock(),
        logger=MagicMock(),
        fx_provider=fx_provider,
    )

    await service.scan_dry_run(exchange=OverseasExchange.NASD)

    fx_provider.assert_not_awaited()


@pytest.mark.asyncio
async def test_scan_skips_candidates_without_a_code(svc):
    svc.candidate_service.get_candidates = AsyncMock(return_value=[{"name": "코드없음"}])
    svc.sqs.get_recent_daily_ohlcv = AsyncMock()

    assert await svc.service.scan_dry_run() == []
    svc.sqs.get_recent_daily_ohlcv.assert_not_awaited()


@pytest.mark.asyncio
async def test_scan_logs_and_skips_when_the_ohlcv_lookup_raises(svc):
    svc.sqs.get_recent_daily_ohlcv = AsyncMock(side_effect=RuntimeError("조회 실패"))

    assert await svc.service.scan_dry_run() == []
    svc.service._logger.warning.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        None,
        ResCommonResponse(rt_cd=ErrorCode.API_ERROR.value, msg1="fail", data=None),
        ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="ok", data=[]),
    ],
)
async def test_scan_skips_unusable_ohlcv_responses(svc, response):
    svc.sqs.get_recent_daily_ohlcv = AsyncMock(return_value=response)

    assert await svc.service.scan_dry_run() == []


@pytest.mark.asyncio
async def test_scan_skips_when_only_one_bar_is_available(svc):
    svc.sqs.get_recent_daily_ohlcv = AsyncMock(
        return_value=_ohlcv([_bar("20260512", 100, 120, 95, 110)])
    )

    assert await svc.service.scan_dry_run() == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "prev_bar, cur_bar",
    [
        # 전일 range 가 0 이면 target 을 만들 수 없다.
        (("20260511", 100, 100, 100, 100), ("20260512", 100, 130, 95, 120)),
        # 당일 시가가 0 이면 판정하지 않는다.
        (("20260511", 100, 120, 90, 110), ("20260512", 0, 130, 95, 120)),
    ],
)
async def test_scan_skips_bars_that_cannot_form_a_target(svc, prev_bar, cur_bar):
    svc.sqs.get_recent_daily_ohlcv = AsyncMock(
        return_value=_ohlcv([_bar(*prev_bar), _bar(*cur_bar)])
    )

    assert await svc.service.scan_dry_run() == []


@pytest.mark.asyncio
async def test_fx_rate_is_none_without_sizing_or_provider(svc):
    assert await svc.service._resolve_fx_rate() is None


@pytest.mark.asyncio
async def test_fx_provider_error_is_logged_and_yields_none(svc):
    svc.service._sizing_service = MagicMock()
    svc.service._fx_provider = AsyncMock(side_effect=RuntimeError("환율 실패"))

    assert await svc.service._resolve_fx_rate() is None
    svc.service._logger.warning.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raw, expected", [(1380.5, 1380.5), ("1400", 1400.0), (None, None), ("환율없음", None), (0, None)]
)
async def test_fx_rate_accepts_only_positive_numbers(svc, raw, expected):
    svc.service._sizing_service = MagicMock()
    svc.service._fx_provider = AsyncMock(return_value=raw)

    assert await svc.service._resolve_fx_rate() == expected


def test_float_coercion_defaults_to_zero():
    from services.overseas_vbo_dryrun_service import OverseasVBODryRunService as Svc

    assert Svc._f(None) == 0.0
    assert Svc._f("120") == 120.0
    assert Svc._f("가격") == 0.0
    assert Svc._f(object()) == 0.0
