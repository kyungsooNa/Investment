"""해외 RSI2 dry-run 신호 서비스 테스트.

완성 일봉 기준으로 장기 상승추세 + RSI(2) 과매도 조건을 판정하고 shadow 저널에
would-be 신호만 기록한다. 해외 자동 주문 경로는 열지 않는다.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from common.overseas_types import OverseasExchange
from common.types import ErrorCode, ResCommonResponse
from services.overseas_rsi2_dryrun_service import OverseasRSI2DryRunService


def _bar(d, o, h, l, c, v=1000):
    return {"date": d, "open": o, "high": h, "low": l, "close": c, "volume": v}


def _ohlcv(bars):
    return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="ok", data=bars)


# KIS 해외 일봉은 1회 호출로 end_date 기준 마지막 100봉만 반환한다(2026-08 실측,
# scripts/fetch_overseas_ohlcv.py 참고). 이보다 긴 이력을 요구하면 실운영에서 영구 0건이 된다.
OVERSEAS_DAILY_BAR_CEILING = 100


def _ceiling_bars():
    """실제 상한(100봉)만으로 구성한 얕은 눌림목 시나리오 — 마지막 종가는 추세선 위."""
    bars = []
    for i in range(1, 99):
        price = 80.0 + 0.5 * i
        bars.append(_bar(f"2026{i:04d}", price - 0.2, price + 0.3, price - 0.3, price, 10_000))
    bars.append(_bar("20260908", 129.0, 129.2, 127.5, 128.0, 12_000))
    bars.append(_bar("20260909", 128.0, 128.3, 126.5, 127.0, 13_000))
    return bars


def _rsi2_bars(*, last_close=178.0, last_open=180.0):
    bars = []
    price = 80.0
    for i in range(205):
        price += 0.5
        bars.append(_bar(f"2025{i + 1:04d}", price - 0.2, price + 0.5, price - 0.5, price, 10_000))
    # RSI(2)를 낮추기 위한 연속 하락 2일. 눌림이 얕아 마지막 종가는 추세MA 위에 남는다.
    bars.append(_bar("20260520", 182.0, 183.0, 179.0, 180.0, 12_000))
    bars.append(_bar("20260521", last_open, 181.0, 177.0, last_close, 13_000))
    return bars


@pytest.fixture
def svc():
    candidate_service = MagicMock()
    candidate_service.get_candidates = AsyncMock(return_value=[
        {"code": "MSFT", "name": "Microsoft", "exchange": "NASD", "avg_trading_value": 100_000_000.0},
    ])
    sqs = MagicMock()
    journal = MagicMock()
    service = OverseasRSI2DryRunService(
        candidate_service=candidate_service,
        stock_query_service=sqs,
        shadow_journal=journal,
        logger=MagicMock(),
    )
    return SimpleNamespace(service=service, candidate_service=candidate_service, sqs=sqs, journal=journal)


@pytest.mark.asyncio
async def test_scan_emits_buy_on_rsi2_pullback(svc):
    svc.sqs.get_recent_daily_ohlcv = AsyncMock(return_value=_ohlcv(_rsi2_bars()))

    signals = await svc.service.scan_dry_run(exchange=OverseasExchange.NASD)

    assert len(signals) == 1
    sig = signals[0]
    assert sig["strategy"] == "RSI2Pullback_overseas"
    assert sig["code"] == "MSFT"
    assert sig["action"] == "BUY"
    assert sig["entry_reason"] == "overseas_rsi2_pullback"
    assert sig["rsi2"] <= 10.0
    assert sig["trend_ma"] > 0
    assert sig["entry_price"] == 178.0
    assert sig["stop_price"] == pytest.approx(169.1)
    assert sig["target_ma_period"] == 5


@pytest.mark.asyncio
async def test_scan_requests_history_within_overseas_daily_ceiling(svc):
    """해외 일봉 상한(100봉)보다 많이 요구하면 어떤 종목도 판정에 도달하지 못한다."""
    svc.sqs.get_recent_daily_ohlcv = AsyncMock(return_value=_ohlcv(_ceiling_bars()))

    await svc.service.scan_dry_run(exchange=OverseasExchange.NASD)

    requested = svc.sqs.get_recent_daily_ohlcv.call_args.kwargs["limit"]
    assert requested <= OVERSEAS_DAILY_BAR_CEILING


@pytest.mark.asyncio
async def test_scan_emits_buy_with_only_ceiling_history(svc):
    """실제로 받을 수 있는 100봉만으로 추세 필터와 RSI 판정이 성립해야 한다."""
    svc.sqs.get_recent_daily_ohlcv = AsyncMock(return_value=_ohlcv(_ceiling_bars()))

    signals = await svc.service.scan_dry_run(exchange=OverseasExchange.NASD)

    assert len(signals) == 1
    sig = signals[0]
    assert sig["rsi2"] <= 10.0
    assert sig["entry_price"] == 127.0
    assert sig["trend_ma"] < 127.0
    assert sig["trend_ma_period"] == 50


@pytest.mark.asyncio
async def test_scan_skips_when_not_enough_history(svc):
    svc.sqs.get_recent_daily_ohlcv = AsyncMock(return_value=_ohlcv(_rsi2_bars()[-20:]))

    signals = await svc.service.scan_dry_run(exchange=OverseasExchange.NASD)

    assert signals == []
    svc.journal.record.assert_not_called()


@pytest.mark.asyncio
async def test_scan_skips_when_rsi_above_threshold(svc):
    svc.sqs.get_recent_daily_ohlcv = AsyncMock(return_value=_ohlcv(_rsi2_bars(last_close=185.0, last_open=180.0)))

    signals = await svc.service.scan_dry_run(exchange=OverseasExchange.NASD)

    assert signals == []


@pytest.mark.asyncio
async def test_scan_records_to_shadow_journal_with_rsi2_source(svc):
    svc.sqs.get_recent_daily_ohlcv = AsyncMock(return_value=_ohlcv(_rsi2_bars()))

    await svc.service.scan_dry_run(exchange=OverseasExchange.NYSE)

    svc.journal.record.assert_called_once()
    _, kwargs = svc.journal.record.call_args
    assert kwargs["code"] == "MSFT"
    assert kwargs["strategy_name"] == "RSI2Pullback_overseas"
    assert kwargs["signal_source"] == "overseas_rsi2_dryrun"
    assert kwargs["snapshot"]["exchange"] == "NYSE"
    assert kwargs["snapshot"]["bar"]["date"] == "20260521"


@pytest.mark.asyncio
async def test_scan_includes_qty_when_sizing_injected():
    candidate_service = MagicMock()
    candidate_service.get_candidates = AsyncMock(return_value=[
        {"code": "MSFT", "name": "Microsoft", "exchange": "NASD", "avg_trading_value": 100_000_000.0},
    ])
    sqs = MagicMock()
    sqs.get_recent_daily_ohlcv = AsyncMock(return_value=_ohlcv(_rsi2_bars()))
    sizing = MagicMock()
    sizing.size = MagicMock(return_value={"qty": 7, "notional_usd": 728.0, "reason": "slot"})

    service = OverseasRSI2DryRunService(
        candidate_service=candidate_service,
        stock_query_service=sqs,
        shadow_journal=MagicMock(),
        logger=MagicMock(),
        position_sizing_service=sizing,
    )

    signals = await service.scan_dry_run(exchange=OverseasExchange.NASD)

    assert signals[0]["qty"] == 7
    assert signals[0]["notional_usd"] == 728.0
    assert sizing.size.call_args.kwargs["limit_price_usd"] == signals[0]["entry_price"]
    assert not hasattr(service, "_order_execution_service")


@pytest.mark.asyncio
async def test_scan_copies_fx_fields_from_sizing_result(svc):
    """사이징이 환율/원화 노출을 돌려주면 신호에 그대로 옮겨 담는다."""
    svc.sqs.get_recent_daily_ohlcv = AsyncMock(return_value=_ohlcv(_rsi2_bars()))
    sizing = MagicMock()
    sizing.size = MagicMock(return_value={
        "qty": 2,
        "notional_usd": 200.0,
        "fx_krw_per_usd": 1380.0,
        "krw_exposure": 276_000.0,
    })
    svc.service._sizing_service = sizing
    svc.service._fx_provider = AsyncMock(return_value=1380.0)

    signals = await svc.service.scan_dry_run()

    assert signals[0]["qty"] == 2
    assert signals[0]["fx_krw_per_usd"] == 1380.0
    assert signals[0]["krw_exposure"] == 276_000.0
    assert sizing.size.call_args.kwargs["fx_krw_per_usd"] == 1380.0


@pytest.mark.asyncio
async def test_scan_omits_fx_fields_when_sizing_returns_none(svc):
    svc.sqs.get_recent_daily_ohlcv = AsyncMock(return_value=_ohlcv(_rsi2_bars()))
    sizing = MagicMock()
    sizing.size = MagicMock(return_value={
        "qty": 2,
        "notional_usd": 200.0,
        "fx_krw_per_usd": None,
        "krw_exposure": None,
    })
    svc.service._sizing_service = sizing

    signals = await svc.service.scan_dry_run()

    assert "fx_krw_per_usd" not in signals[0]
    assert "krw_exposure" not in signals[0]


@pytest.mark.asyncio
async def test_scan_can_skip_shadow_journal_recording(svc):
    svc.sqs.get_recent_daily_ohlcv = AsyncMock(return_value=_ohlcv(_rsi2_bars()))

    signals = await svc.service.scan_dry_run(record=False)

    assert len(signals) == 1
    svc.journal.record.assert_not_called()
