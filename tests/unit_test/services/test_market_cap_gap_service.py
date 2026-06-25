from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from common.types import ErrorCode, ResCommonResponse
from services.market_cap_gap_service import (
    MarketCapGapService,
    MarketCapQuote,
    StaticUsMarketCapProvider,
)


def _domestic_response(value: int):
    return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data=value)


@pytest.mark.asyncio
async def test_build_report_compares_korean_caps_to_us_caps_in_krw():
    broker = SimpleNamespace()
    broker.get_market_cap = AsyncMock(side_effect=[
        _domestic_response(500_000_000_000_000),
        _domestic_response(200_000_000_000_000),
    ])
    provider = StaticUsMarketCapProvider(
        quotes=[
            MarketCapQuote(symbol="NVDA", name="NVIDIA", currency="USD", market_cap=3_000_000_000_000),
            MarketCapQuote(symbol="MU", name="Micron", currency="USD", market_cap=150_000_000_000),
        ],
        usdkrw=1400.0,
    )
    service = MarketCapGapService(broker=broker, us_provider=provider)

    report = await service.build_report(report_date="20260625", trigger="kr_close")

    assert report["report_date"] == "20260625"
    assert report["trigger"] == "kr_close"
    assert report["fx_rate"] == 1400.0
    assert [item["symbol"] for item in report["korean"]] == ["005930", "000660"]
    assert [item["symbol"] for item in report["us"]] == ["NVDA", "MU"]

    samsung_nvda = next(
        item for item in report["comparisons"]
        if item["korean_symbol"] == "005930" and item["us_symbol"] == "NVDA"
    )
    assert samsung_nvda["gap_krw"] == 3_700_000_000_000_000
    assert samsung_nvda["ratio"] == 8.4


@pytest.mark.asyncio
async def test_build_report_skips_failed_or_empty_quotes():
    broker = SimpleNamespace()
    broker.get_market_cap = AsyncMock(side_effect=[
        _domestic_response(500_000_000_000_000),
        ResCommonResponse(rt_cd=ErrorCode.API_ERROR.value, msg1="fail", data=None),
    ])
    provider = StaticUsMarketCapProvider(
        quotes=[
            MarketCapQuote(symbol="NVDA", name="NVIDIA", currency="USD", market_cap=0),
            MarketCapQuote(symbol="MU", name="Micron", currency="USD", market_cap=150_000_000_000),
        ],
        usdkrw=1400.0,
    )
    service = MarketCapGapService(broker=broker, us_provider=provider)

    report = await service.build_report(report_date="20260625", trigger="us_close")

    assert [item["symbol"] for item in report["korean"]] == ["005930"]
    assert [item["symbol"] for item in report["us"]] == ["MU"]
    assert len(report["comparisons"]) == 1


@pytest.mark.asyncio
async def test_build_report_returns_no_comparisons_when_fx_missing():
    broker = SimpleNamespace()
    broker.get_market_cap = AsyncMock(return_value=_domestic_response(500_000_000_000_000))
    provider = StaticUsMarketCapProvider(
        quotes=[MarketCapQuote(symbol="NVDA", name="NVIDIA", currency="USD", market_cap=3_000_000_000_000)],
        usdkrw=None,
    )
    service = MarketCapGapService(broker=broker, us_provider=provider)

    report = await service.build_report(report_date="20260625", trigger="kr_close")

    assert report["fx_rate"] is None
    assert report["us"][0]["market_cap_krw"] is None
    assert report["comparisons"] == []
