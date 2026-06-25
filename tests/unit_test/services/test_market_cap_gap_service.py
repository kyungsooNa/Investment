from types import SimpleNamespace
from unittest.mock import AsyncMock
import sqlite3

import pytest

from common.types import ErrorCode, ResCommonResponse
from services.market_cap_gap_service import (
    MarketCapGapService,
    MarketCapQuote,
    PykrxKoreanMarketCapProvider,
    StaticUsMarketCapProvider,
    YahooUsMarketCapProvider,
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
    service = MarketCapGapService(broker=broker, us_provider=provider, korean_provider=None)

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
    service = MarketCapGapService(broker=broker, us_provider=provider, korean_provider=None)

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


@pytest.mark.asyncio
async def test_build_report_uses_korean_fallback_when_broker_market_cap_unavailable():
    broker = SimpleNamespace()
    broker.get_market_cap = AsyncMock(side_effect=[
        ResCommonResponse(rt_cd=ErrorCode.API_ERROR.value, msg1="paper unsupported", data=None),
        ResCommonResponse(rt_cd=ErrorCode.API_ERROR.value, msg1="paper unsupported", data=None),
    ])
    korean_provider = SimpleNamespace()
    korean_provider.fetch_market_caps = AsyncMock(return_value={
        "005930": 480_000_000_000_000,
        "000660": 250_000_000_000_000,
    })
    provider = StaticUsMarketCapProvider(
        quotes=[MarketCapQuote(symbol="NVDA", name="NVIDIA", currency="USD", market_cap=3_000_000_000_000)],
        usdkrw=1400.0,
    )
    service = MarketCapGapService(
        broker=broker,
        us_provider=provider,
        korean_provider=korean_provider,
    )

    report = await service.build_report(report_date="20260625", trigger="kr_close")

    korean_provider.fetch_market_caps.assert_awaited_once_with(["005930", "000660"], "20260625")
    assert [item["symbol"] for item in report["korean"]] == ["005930", "000660"]
    assert report["korean"][0]["market_cap_krw"] == 480_000_000_000_000
    assert len(report["comparisons"]) == 2


@pytest.mark.asyncio
async def test_yahoo_provider_retries_quote_with_crumb_after_unauthorized(monkeypatch):
    calls = []

    class FakeResponse:
        def __init__(self, status_code, payload=None, text=""):
            self.status_code = status_code
            self._payload = payload or {}
            self.text = text
            self.request = None

        def json(self):
            return self._payload

        def raise_for_status(self):
            if self.status_code >= 400:
                raise RuntimeError(f"status={self.status_code}")

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, params=None):
            calls.append((url, dict(params or {})))
            if "v7/finance/quote" in url and not (params or {}).get("crumb"):
                return FakeResponse(401)
            if "fc.yahoo.com" in url:
                return FakeResponse(404)
            if "getcrumb" in url:
                return FakeResponse(200, text="crumb-token")
            if "v7/finance/quote" in url:
                return FakeResponse(200, payload={
                    "quoteResponse": {
                        "result": [{
                            "symbol": "NVDA",
                            "shortName": "NVIDIA",
                            "currency": "USD",
                            "marketCap": 3_000_000_000_000,
                        }]
                    }
                })
            return FakeResponse(500)

    monkeypatch.setattr("services.market_cap_gap_service.httpx.AsyncClient", FakeClient)
    provider = YahooUsMarketCapProvider()

    quotes = await provider.fetch_quotes(["NVDA"])

    assert [quote.symbol for quote in quotes] == ["NVDA"]
    quote_calls = [call for call in calls if "v7/finance/quote" in call[0]]
    assert quote_calls[0][1].get("crumb") is None
    assert quote_calls[1][1]["crumb"] == "crumb-token"


@pytest.mark.asyncio
async def test_korean_fallback_provider_reads_local_daily_prices_in_ukwon(tmp_path):
    db_path = tmp_path / "stocks.db"
    con = sqlite3.connect(db_path)
    con.execute(
        "CREATE TABLE daily_prices (code TEXT, trade_date TEXT, market_cap INTEGER)"
    )
    con.execute(
        "INSERT INTO daily_prices VALUES (?, ?, ?)",
        ("005930", "20260624", 20_000_000),
    )
    con.execute(
        "INSERT INTO daily_prices VALUES (?, ?, ?)",
        ("005930", "20260625", 21_000_000),
    )
    con.execute(
        "INSERT INTO daily_prices VALUES (?, ?, ?)",
        ("000660", "20260625", 15_000_000),
    )
    con.commit()
    con.close()
    provider = PykrxKoreanMarketCapProvider(db_path=db_path)

    caps = await provider.fetch_market_caps(["005930", "000660"], "20260625")

    assert caps == {
        "005930": 21_000_000 * 100_000_000,
        "000660": 15_000_000 * 100_000_000,
    }
