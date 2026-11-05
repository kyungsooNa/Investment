from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from common.overseas_types import OverseasExchange
from common.types import ErrorCode, ResCommonResponse
from services.overseas_inverse_etf_dryrun_service import OverseasInverseEtfDryRunService


def _bars(*, last_open=21.0, last_close=22.0):
    rows = []
    for index in range(20):
        close = 20.0 + index * 0.05
        rows.append({
            "date": f"202610{index + 1:02d}",
            "open": close - 0.1,
            "high": close + 0.2,
            "low": close - 0.2,
            "close": close,
            "volume": 1_000_000,
        })
    rows.append({
        "date": "20261021",
        "open": last_open,
        "high": max(last_open, last_close) + 0.2,
        "low": min(last_open, last_close) - 0.2,
        "close": last_close,
        "volume": 1_200_000,
    })
    return rows


def _response(rows):
    return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="ok", data=rows)


@pytest.fixture
def svc():
    regime = MagicMock()
    regime.MARKET = "US"
    regime.classify = AsyncMock(return_value=SimpleNamespace(regime_label="bear"))
    sqs = MagicMock()
    sqs.get_recent_daily_ohlcv = AsyncMock(return_value=_response(_bars()))
    journal = MagicMock()
    sizing = MagicMock()
    sizing.size.return_value = {"qty": 4, "notional_usd": 88.0}
    service = OverseasInverseEtfDryRunService(
        stock_query_service=sqs,
        market_regime_service=regime,
        shadow_journal=journal,
        position_sizing_service=sizing,
        logger=MagicMock(),
    )
    return SimpleNamespace(
        service=service,
        regime=regime,
        sqs=sqs,
        journal=journal,
        sizing=sizing,
    )


@pytest.mark.asyncio
async def test_scan_emits_psq_buy_only_in_bear_regime_with_confirmed_trend(svc):
    signals = await svc.service.scan_dry_run()

    assert len(signals) == 1
    signal = signals[0]
    assert signal["strategy"] == "InverseETFRegime_overseas"
    assert signal["code"] == "PSQ"
    assert signal["action"] == "BUY"
    assert signal["entry_price"] == 22.0
    assert signal["entry_reason"] == "us_bear_regime_inverse_etf"
    assert signal["market_regime"] == "bear"
    assert signal["trend_ma_period"] == 20
    assert signal["qty"] == 4
    assert signal["stop_price"] == pytest.approx(20.9)
    svc.regime.classify.assert_awaited_once_with("US")
    svc.sqs.get_recent_daily_ohlcv.assert_awaited_once_with(
        "PSQ", limit=21, exchange=OverseasExchange.NASD,
    )


@pytest.mark.asyncio
async def test_scan_skips_without_bear_regime(svc):
    svc.regime.classify.return_value = SimpleNamespace(regime_label="sideways")

    signals = await svc.service.scan_dry_run()

    assert signals == []
    svc.sqs.get_recent_daily_ohlcv.assert_not_awaited()
    svc.journal.record.assert_not_called()


@pytest.mark.asyncio
async def test_scan_fails_closed_when_regime_lookup_fails(svc):
    svc.regime.classify.side_effect = RuntimeError("regime unavailable")

    signals = await svc.service.scan_dry_run()

    assert signals == []
    svc.sqs.get_recent_daily_ohlcv.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("last_open", "last_close"),
    [(21.0, 20.5), (22.0, 21.5)],
)
async def test_scan_skips_when_psq_trend_is_not_confirmed(svc, last_open, last_close):
    svc.sqs.get_recent_daily_ohlcv.return_value = _response(
        _bars(last_open=last_open, last_close=last_close)
    )

    signals = await svc.service.scan_dry_run()

    assert signals == []
    svc.journal.record.assert_not_called()


@pytest.mark.asyncio
async def test_scan_records_shadow_signal_without_order_dependency(svc):
    await svc.service.scan_dry_run()

    svc.journal.record.assert_called_once()
    kwargs = svc.journal.record.call_args.kwargs
    assert kwargs["strategy_name"] == "InverseETFRegime_overseas"
    assert kwargs["code"] == "PSQ"
    assert kwargs["signal_source"] == "overseas_inverse_etf_dryrun"
    assert kwargs["snapshot"]["exchange"] == "NASD"
    assert kwargs["snapshot"]["market_regime"] == "bear"
    assert kwargs["snapshot"]["bar"]["date"] == "20261021"
    assert not hasattr(svc.service, "_order_execution_service")


@pytest.mark.asyncio
async def test_scan_can_skip_recording(svc):
    signals = await svc.service.scan_dry_run(record=False)

    assert len(signals) == 1
    svc.journal.record.assert_not_called()
