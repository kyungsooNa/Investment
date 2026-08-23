import sys
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
import sqlite3

import pandas as pd
import pytest

from common.types import ErrorCode, ResCommonResponse
from common.overseas_types import OverseasExchange, OverseasPriceSummary
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
async def test_build_report_includes_skhynix_adr_price_gap_with_symbol_fallback():
    broker = SimpleNamespace()
    broker.get_market_cap = AsyncMock(side_effect=[
        _domestic_response(500_000_000_000_000),
        _domestic_response(200_000_000_000_000),
    ])
    broker.get_current_price = AsyncMock(return_value=ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value,
        msg1="OK",
        data=SimpleNamespace(stck_prpr="2076000"),
    ))
    broker.get_overseas_price = AsyncMock(side_effect=[
        ResCommonResponse(rt_cd=ErrorCode.API_ERROR.value, msg1="not found", data=None),
        ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value,
            msg1="OK",
            data=OverseasPriceSummary(
                symbol="SKHYV",
                exchange=OverseasExchange.NASD,
                price=176.0,
            ),
        ),
    ])
    provider = StaticUsMarketCapProvider(quotes=[], usdkrw=1380.0)
    service = MarketCapGapService(broker=broker, us_provider=provider, korean_provider=None)

    report = await service.build_report(report_date="20260710", trigger="us_close")

    assert report["adr_gap"] == {
        "korean_symbol": "000660",
        "korean_name": "SK하이닉스",
        "korean_price_krw": 2_076_000,
        "adr_symbol": "SKHYV",
        "adr_price_usd": 176.0,
        "ads_per_common_share": 10,
        "fx_rate": 1380.0,
        "implied_korean_price_krw": 2_428_800,
        "gap_krw": 352_800,
        "gap_percent": 16.99,
    }
    assert [call.args[0] for call in broker.get_overseas_price.await_args_list] == ["SKHY", "SKHYV"]


@pytest.mark.asyncio
async def test_build_report_reads_skhynix_price_from_kis_output_wrapper():
    broker = SimpleNamespace()
    broker.get_market_cap = AsyncMock(side_effect=[
        _domestic_response(500_000_000_000_000),
        _domestic_response(200_000_000_000_000),
    ])
    broker.get_current_price = AsyncMock(return_value=ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value,
        msg1="OK",
        data={"output": SimpleNamespace(stck_prpr="2076000")},
    ))
    broker.get_overseas_price = AsyncMock(return_value=ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value,
        msg1="OK",
        data=OverseasPriceSummary(
            symbol="SKHY",
            exchange=OverseasExchange.NASD,
            price=176.0,
        ),
    ))
    provider = StaticUsMarketCapProvider(quotes=[], usdkrw=1380.0)
    service = MarketCapGapService(broker=broker, us_provider=provider, korean_provider=None)

    report = await service.build_report(report_date="20260710", trigger="us_close")

    assert report["adr_gap"] is not None
    assert report["adr_gap"]["korean_price_krw"] == 2_076_000
    assert report["adr_gap"]["adr_symbol"] == "SKHY"
    broker.get_overseas_price.assert_awaited_once_with("SKHY")


@pytest.mark.asyncio
async def test_build_report_omits_adr_gap_when_price_data_is_missing():
    broker = SimpleNamespace()
    broker.get_market_cap = AsyncMock(side_effect=[
        _domestic_response(500_000_000_000_000),
        _domestic_response(200_000_000_000_000),
    ])
    broker.get_current_price = AsyncMock(return_value=ResCommonResponse(
        rt_cd=ErrorCode.API_ERROR.value, msg1="failed", data=None,
    ))
    broker.get_overseas_price = AsyncMock()
    provider = StaticUsMarketCapProvider(quotes=[], usdkrw=1380.0)
    service = MarketCapGapService(broker=broker, us_provider=provider, korean_provider=None)

    report = await service.build_report(report_date="20260710", trigger="kr_close")

    assert report["adr_gap"] is None
    broker.get_overseas_price.assert_not_awaited()


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
async def test_get_us_market_caps_does_not_query_domestic_market_cap():
    broker = SimpleNamespace()
    broker.get_market_cap = AsyncMock()
    provider = StaticUsMarketCapProvider(
        quotes=[
            MarketCapQuote(symbol="AAPL", name="Apple", currency="USD", market_cap=2_000_000_000_000),
            MarketCapQuote(symbol="NVDA", name="NVIDIA", currency="USD", market_cap=3_000_000_000_000),
        ],
        usdkrw=1400.0,
    )
    service = MarketCapGapService(broker=broker, us_provider=provider, korean_provider=None)

    summary = await service.get_us_market_caps()

    assert summary["fx_rate"] == 1400.0
    assert [item["symbol"] for item in summary["items"]] == ["NVDA", "AAPL"]
    assert summary["items"][0]["market_cap_krw"] == 4_200_000_000_000_000
    broker.get_market_cap.assert_not_awaited()


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


def _snapshot_client_factory(calls, payload_by_symbol):
    """symbols 파라미터에 맞춰 quoteResponse 를 돌려주는 FakeClient 를 만든다."""

    class FakeResponse:
        def __init__(self, status_code, payload=None, text=""):
            self.status_code = status_code
            self._payload = payload or {}
            self.text = text

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
            symbols = (params or {}).get("symbols", "").split(",")
            calls.append(symbols)
            if any(symbol == "BOOM" for symbol in symbols):
                raise RuntimeError("chunk failed")
            rows = [payload_by_symbol[s] for s in symbols if s in payload_by_symbol]
            return FakeResponse(200, payload={"quoteResponse": {"result": rows}})

    return FakeClient


@pytest.mark.asyncio
async def test_fetch_snapshots_returns_price_rate_volume_and_cap(monkeypatch):
    calls = []
    payload = {
        "AAPL": {
            "symbol": "AAPL", "shortName": "Apple Inc.", "currency": "USD",
            "regularMarketPrice": 190.5, "regularMarketChangePercent": 1.23,
            "regularMarketVolume": 50_000_000, "marketCap": 3_000_000_000_000,
        },
    }
    monkeypatch.setattr(
        "services.market_cap_gap_service.httpx.AsyncClient",
        _snapshot_client_factory(calls, payload),
    )

    snapshots = await YahooUsMarketCapProvider().fetch_snapshots(["aapl"])

    assert len(snapshots) == 1
    snap = snapshots[0]
    assert snap.symbol == "AAPL"
    assert snap.name == "Apple Inc."
    assert snap.price == 190.5
    assert snap.change_rate == 1.23
    assert snap.volume == 50_000_000
    assert snap.market_cap == 3_000_000_000_000


@pytest.mark.asyncio
async def test_fetch_snapshots_splits_symbols_into_chunks(monkeypatch):
    calls = []
    symbols = [f"S{i}" for i in range(5)]
    payload = {
        s: {"symbol": s, "shortName": s, "regularMarketPrice": 1.0, "marketCap": 1}
        for s in symbols
    }
    monkeypatch.setattr(
        "services.market_cap_gap_service.httpx.AsyncClient",
        _snapshot_client_factory(calls, payload),
    )

    snapshots = await YahooUsMarketCapProvider().fetch_snapshots(symbols, chunk_size=2)

    assert [len(chunk) for chunk in calls] == [2, 2, 1]
    assert {snap.symbol for snap in snapshots} == set(symbols)


@pytest.mark.asyncio
async def test_fetch_snapshots_keeps_other_chunks_when_one_fails(monkeypatch):
    calls = []
    payload = {
        "AAPL": {"symbol": "AAPL", "shortName": "Apple", "regularMarketPrice": 1.0, "marketCap": 1},
        "MSFT": {"symbol": "MSFT", "shortName": "Microsoft", "regularMarketPrice": 2.0, "marketCap": 2},
    }
    monkeypatch.setattr(
        "services.market_cap_gap_service.httpx.AsyncClient",
        _snapshot_client_factory(calls, payload),
    )

    snapshots = await YahooUsMarketCapProvider().fetch_snapshots(
        ["AAPL", "BOOM", "MSFT"], chunk_size=1
    )

    assert {snap.symbol for snap in snapshots} == {"AAPL", "MSFT"}


@pytest.mark.asyncio
async def test_fetch_snapshots_skips_rows_without_price(monkeypatch):
    calls = []
    payload = {
        "AAPL": {"symbol": "AAPL", "shortName": "Apple", "regularMarketPrice": 190.0, "marketCap": 1},
        "DEAD": {"symbol": "DEAD", "shortName": "Delisted", "marketCap": 0},
    }
    monkeypatch.setattr(
        "services.market_cap_gap_service.httpx.AsyncClient",
        _snapshot_client_factory(calls, payload),
    )

    snapshots = await YahooUsMarketCapProvider().fetch_snapshots(["AAPL", "DEAD"])

    assert [snap.symbol for snap in snapshots] == ["AAPL"]


@pytest.mark.asyncio
async def test_fetch_snapshots_empty_symbols_makes_no_request(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "services.market_cap_gap_service.httpx.AsyncClient",
        _snapshot_client_factory(calls, {}),
    )

    assert await YahooUsMarketCapProvider().fetch_snapshots([]) == []
    assert calls == []


# ---------------------------------------------------------------------------
# PykrxKoreanMarketCapProvider — 로컬 DB / pykrx fallback 경로
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_korean_fallback_provider_returns_empty_for_empty_codes(tmp_path):
    provider = PykrxKoreanMarketCapProvider(db_path=tmp_path / "missing.db")

    assert await provider.fetch_market_caps([], "20260625") == {}


@pytest.mark.asyncio
async def test_korean_fallback_provider_skips_local_db_when_file_missing(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "pykrx", None)
    provider = PykrxKoreanMarketCapProvider(
        db_path=tmp_path / "missing.db", logger=MagicMock()
    )

    caps = await provider.fetch_market_caps(["005930"], "20260625")

    assert caps == {}
    provider._logger.warning.assert_called_once()


@pytest.mark.asyncio
async def test_korean_fallback_provider_logs_when_local_db_query_fails(tmp_path, monkeypatch):
    db_path = tmp_path / "stocks.db"
    sqlite3.connect(db_path).close()  # daily_prices 테이블이 없는 DB
    monkeypatch.setitem(sys.modules, "pykrx", None)
    provider = PykrxKoreanMarketCapProvider(db_path=db_path, logger=MagicMock())

    caps = await provider.fetch_market_caps(["005930"], "20260625")

    assert caps == {}
    assert provider._logger.warning.call_count == 2


@pytest.mark.asyncio
async def test_korean_fallback_provider_skips_invalid_and_nonpositive_caps(tmp_path, monkeypatch):
    db_path = tmp_path / "stocks.db"
    con = sqlite3.connect(db_path)
    con.execute("CREATE TABLE daily_prices (code TEXT, trade_date TEXT, market_cap INTEGER)")
    con.executemany(
        "INSERT INTO daily_prices VALUES (?, ?, ?)",
        [
            ("005930", "20260625", "not-a-number"),
            ("035420", "20260625", 0.5),
            ("000660", "20260625", 5_000_000_000_000),
        ],
    )
    con.commit()
    con.close()
    monkeypatch.setitem(sys.modules, "pykrx", None)
    provider = PykrxKoreanMarketCapProvider(db_path=db_path, logger=MagicMock())

    caps = await provider.fetch_market_caps(["005930", "035420", "000660"], "20260625")

    # 억 단위 미만이 아니므로 그대로 사용하고, 파싱 실패/0 이하 종목은 제외한다.
    assert caps == {"000660": 5_000_000_000_000}


@pytest.mark.asyncio
async def test_korean_fallback_provider_reads_market_cap_from_pykrx(tmp_path, monkeypatch):
    frames = {
        "20260625": pd.DataFrame({"시가총액": [480_000_000_000_000]}, index=["005930"]),
    }
    calls = []

    def _fake_get_market_cap_by_ticker(date, market="ALL"):
        calls.append(date)
        if date == "20260626":
            raise RuntimeError("pykrx down")
        if date not in frames:
            return pd.DataFrame()
        return frames[date]

    monkeypatch.setattr(
        "pykrx.stock.get_market_cap_by_ticker", _fake_get_market_cap_by_ticker, raising=False
    )
    provider = PykrxKoreanMarketCapProvider(
        db_path=tmp_path / "missing.db", lookback_days=3, logger=MagicMock()
    )

    caps = await provider.fetch_market_caps(["005930"], "20260626")

    assert caps == {"005930": 480_000_000_000_000}
    assert calls[0] == "20260626"
    provider._logger.warning.assert_called_once()


@pytest.mark.asyncio
async def test_korean_fallback_provider_skips_unparsable_pykrx_rows(tmp_path, monkeypatch):
    df = pd.DataFrame({"시가총액": ["bad", 0]}, index=["005930", "000660"])
    monkeypatch.setattr(
        "pykrx.stock.get_market_cap_by_ticker",
        lambda date, market="ALL": df,
        raising=False,
    )
    provider = PykrxKoreanMarketCapProvider(
        db_path=tmp_path / "missing.db", lookback_days=0, logger=MagicMock()
    )

    # 035420 은 pykrx 결과에 아예 없는 종목이다.
    assert await provider.fetch_market_caps(["005930", "000660", "035420"], "20260625") == {}


@pytest.mark.asyncio
async def test_korean_fallback_provider_falls_back_to_today_for_invalid_report_date(
    tmp_path, monkeypatch
):
    seen = []

    def _fake(date, market="ALL"):
        seen.append(date)
        return pd.DataFrame()

    monkeypatch.setattr("pykrx.stock.get_market_cap_by_ticker", _fake, raising=False)
    provider = PykrxKoreanMarketCapProvider(
        db_path=tmp_path / "missing.db", lookback_days=0, logger=MagicMock()
    )

    assert await provider.fetch_market_caps(["005930"], "not-a-date") == {}
    assert seen == [datetime.now().strftime("%Y%m%d")]


# ---------------------------------------------------------------------------
# YahooUsMarketCapProvider — 크럼/차트/환율 경로
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"status={self.status_code}")


def _client_factory(handler):
    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, params=None):
            return handler(url, params or {})

    return FakeClient


@pytest.mark.asyncio
async def test_fetch_quotes_returns_empty_when_request_fails(monkeypatch):
    def _handler(url, params):
        raise RuntimeError("network down")

    monkeypatch.setattr(
        "services.market_cap_gap_service.httpx.AsyncClient", _client_factory(_handler)
    )
    provider = YahooUsMarketCapProvider(logger=MagicMock())

    assert await provider.fetch_quotes(["NVDA"]) == []
    provider._logger.error.assert_called_once()


@pytest.mark.asyncio
async def test_fetch_quotes_skips_rows_without_symbol_or_market_cap(monkeypatch):
    rows = [
        "문자열 행",
        {"symbol": "", "marketCap": 100},
        {"symbol": "MU", "marketCap": 0},
        {"symbol": "AAPL", "marketCap": "invalid"},
        {"symbol": "NVDA", "longName": "NVIDIA Corp", "marketCap": 3_000_000_000_000},
    ]
    monkeypatch.setattr(
        "services.market_cap_gap_service.httpx.AsyncClient",
        _client_factory(lambda url, params: _FakeResponse(
            payload={"quoteResponse": {"result": rows}}
        )),
    )
    provider = YahooUsMarketCapProvider(logger=MagicMock())

    quotes = await provider.fetch_quotes(["NVDA", "MU", "AAPL"])

    assert [quote.symbol for quote in quotes] == ["NVDA"]
    assert quotes[0].name == "NVIDIA Corp"
    assert quotes[0].currency == "USD"


@pytest.mark.asyncio
async def test_fetch_raw_returns_empty_when_result_is_not_a_list(monkeypatch):
    monkeypatch.setattr(
        "services.market_cap_gap_service.httpx.AsyncClient",
        _client_factory(lambda url, params: _FakeResponse(
            payload={"quoteResponse": {"result": {"symbol": "NVDA"}}}
        )),
    )
    provider = YahooUsMarketCapProvider(logger=MagicMock())

    assert await provider.fetch_quotes(["NVDA"]) == []


@pytest.mark.asyncio
async def test_fetch_quotes_gives_up_when_crumb_request_raises(monkeypatch):
    def _handler(url, params):
        if "fc.yahoo.com" in url:
            raise RuntimeError("cookie blocked")
        return _FakeResponse(401)

    monkeypatch.setattr(
        "services.market_cap_gap_service.httpx.AsyncClient", _client_factory(_handler)
    )
    provider = YahooUsMarketCapProvider(logger=MagicMock())

    assert await provider.fetch_quotes(["NVDA"]) == []
    assert provider._logger.warning.call_count == 1


@pytest.mark.asyncio
async def test_fetch_quotes_gives_up_when_crumb_endpoint_returns_error(monkeypatch):
    def _handler(url, params):
        if "getcrumb" in url:
            return _FakeResponse(500)
        if "fc.yahoo.com" in url:
            return _FakeResponse(200)
        return _FakeResponse(403)

    monkeypatch.setattr(
        "services.market_cap_gap_service.httpx.AsyncClient", _client_factory(_handler)
    )
    provider = YahooUsMarketCapProvider(logger=MagicMock())

    assert await provider.fetch_quotes(["NVDA"]) == []


@pytest.mark.asyncio
async def test_fetch_quotes_gives_up_when_crumb_body_is_blank(monkeypatch):
    def _handler(url, params):
        if "getcrumb" in url:
            return _FakeResponse(200, text="   ")
        if "fc.yahoo.com" in url:
            return _FakeResponse(200)
        return _FakeResponse(401)

    monkeypatch.setattr(
        "services.market_cap_gap_service.httpx.AsyncClient", _client_factory(_handler)
    )
    provider = YahooUsMarketCapProvider(logger=MagicMock())

    assert await provider.fetch_quotes(["NVDA"]) == []


@pytest.mark.asyncio
async def test_fetch_usdkrw_reads_first_positive_rate(monkeypatch):
    rows = ["문자열 행", {"regularMarketPrice": "invalid"}, {"bid": 0}, {"bid": 1380.5}]
    monkeypatch.setattr(
        "services.market_cap_gap_service.httpx.AsyncClient",
        _client_factory(lambda url, params: _FakeResponse(
            payload={"quoteResponse": {"result": rows}}
        )),
    )
    provider = YahooUsMarketCapProvider(logger=MagicMock())

    assert await provider.fetch_usdkrw() == 1380.5


@pytest.mark.asyncio
async def test_fetch_usdkrw_falls_back_to_chart_price_when_quote_fails(monkeypatch):
    def _handler(url, params):
        if "v8/finance/chart" in url:
            return _FakeResponse(payload={
                "chart": {"result": [{"meta": {"regularMarketPrice": 1375.0}}]}
            })
        raise RuntimeError("quote blocked")

    monkeypatch.setattr(
        "services.market_cap_gap_service.httpx.AsyncClient", _client_factory(_handler)
    )
    provider = YahooUsMarketCapProvider(logger=MagicMock())

    assert await provider.fetch_usdkrw() == 1375.0
    provider._logger.error.assert_called_once()


@pytest.mark.asyncio
async def test_fetch_usdkrw_falls_back_to_chart_price_when_quote_rows_are_empty(monkeypatch):
    def _handler(url, params):
        if "v8/finance/chart" in url:
            return _FakeResponse(payload={
                "chart": {"result": [{"meta": {"previousClose": 1370.0}}]}
            })
        return _FakeResponse(payload={"quoteResponse": {"result": []}})

    monkeypatch.setattr(
        "services.market_cap_gap_service.httpx.AsyncClient", _client_factory(_handler)
    )
    provider = YahooUsMarketCapProvider(logger=MagicMock())

    assert await provider.fetch_usdkrw() == 1370.0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "chart_payload",
    [
        {"chart": {"result": []}},
        {"chart": {"result": [{"meta": {"regularMarketPrice": "invalid"}}]}},
        {"chart": {"result": [{"meta": {"regularMarketPrice": 0}}]}},
        {"chart": {"result": [{}]}},
    ],
)
async def test_fetch_chart_price_returns_none_for_unusable_payloads(monkeypatch, chart_payload):
    monkeypatch.setattr(
        "services.market_cap_gap_service.httpx.AsyncClient",
        _client_factory(lambda url, params: _FakeResponse(payload=chart_payload)),
    )
    provider = YahooUsMarketCapProvider(logger=MagicMock())

    assert await provider._fetch_chart_price("KRW=X") is None


@pytest.mark.asyncio
async def test_fetch_chart_price_returns_none_when_request_fails(monkeypatch):
    def _handler(url, params):
        raise RuntimeError("chart blocked")

    monkeypatch.setattr(
        "services.market_cap_gap_service.httpx.AsyncClient", _client_factory(_handler)
    )
    provider = YahooUsMarketCapProvider(logger=MagicMock())

    assert await provider._fetch_chart_price("KRW=X") is None
    provider._logger.warning.assert_called_once()


@pytest.mark.parametrize(
    "row",
    [
        "문자열 행",
        {"regularMarketPrice": 10.0},
        {"symbol": "AAPL", "regularMarketPrice": "invalid"},
        {"symbol": "AAPL", "regularMarketPrice": 0},
    ],
)
def test_to_snapshot_returns_none_for_unusable_rows(row):
    assert YahooUsMarketCapProvider._to_snapshot(row) is None


def test_to_snapshot_defaults_unparsable_numbers():
    snapshot = YahooUsMarketCapProvider._to_snapshot({
        "symbol": "aapl",
        "regularMarketPrice": 189.5,
        "regularMarketChangePercent": "invalid",
        "regularMarketVolume": "invalid",
        "marketCap": "invalid",
    })

    assert snapshot.symbol == "AAPL"
    assert snapshot.name == "AAPL"
    assert snapshot.change_rate == 0.0
    assert snapshot.volume == 0
    assert snapshot.market_cap == 0


# ---------------------------------------------------------------------------
# MarketCapGapService — 국내 시총/ADR/비교 경로
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_korean_caps_use_fallback_when_broker_raises_or_returns_bad_values():
    broker = SimpleNamespace()
    broker.get_market_cap = AsyncMock(side_effect=[
        RuntimeError("broker down"),
        _domestic_response("not-a-number"),
    ])
    korean_provider = SimpleNamespace()
    korean_provider.fetch_market_caps = AsyncMock(return_value={"005930": 480_000_000_000_000})
    service = MarketCapGapService(
        broker=broker,
        us_provider=StaticUsMarketCapProvider(quotes=[], usdkrw=None),
        korean_provider=korean_provider,
        logger=MagicMock(),
    )

    items = await service._fetch_korean_caps("20260625")

    assert [item["symbol"] for item in items] == ["005930"]
    korean_provider.fetch_market_caps.assert_awaited_once_with(["005930", "000660"], "20260625")
    service._logger.warning.assert_called_once()


@pytest.mark.asyncio
async def test_korean_caps_drop_nonpositive_broker_values():
    broker = SimpleNamespace()
    broker.get_market_cap = AsyncMock(return_value=_domestic_response(0))
    service = MarketCapGapService(
        broker=broker,
        us_provider=StaticUsMarketCapProvider(quotes=[], usdkrw=None),
        korean_provider=None,
        logger=MagicMock(),
    )

    assert await service._fetch_korean_caps("20260625") == []


@pytest.mark.asyncio
async def test_korean_caps_ignore_nonpositive_fallback_values():
    broker = SimpleNamespace()
    broker.get_market_cap = AsyncMock(return_value=ResCommonResponse(
        rt_cd=ErrorCode.API_ERROR.value, msg1="fail", data=None
    ))
    korean_provider = SimpleNamespace()
    korean_provider.fetch_market_caps = AsyncMock(return_value={"005930": 0, "000660": None})
    service = MarketCapGapService(
        broker=broker,
        us_provider=StaticUsMarketCapProvider(quotes=[], usdkrw=None),
        korean_provider=korean_provider,
        logger=MagicMock(),
    )

    assert await service._fetch_korean_caps("20260625") == []


def test_read_price_skips_unparsable_and_nonpositive_values():
    assert MarketCapGapService._read_price({"a": "bad", "b": 0, "c": "1500"}, "a", "b", "c") == 1500.0
    assert MarketCapGapService._read_price({"a": "bad"}, "a", "missing") == 0.0
    assert MarketCapGapService._read_price(SimpleNamespace(price=None), "price") == 0.0


@pytest.mark.asyncio
async def test_adr_gap_is_none_without_fx_rate():
    service = MarketCapGapService(
        broker=SimpleNamespace(),
        us_provider=StaticUsMarketCapProvider(quotes=[], usdkrw=None),
        korean_provider=None,
    )

    assert await service._build_adr_gap(None) is None


@pytest.mark.asyncio
async def test_adr_gap_is_none_when_domestic_price_lookup_raises():
    broker = SimpleNamespace()
    broker.get_current_price = AsyncMock(side_effect=RuntimeError("broker down"))
    service = MarketCapGapService(
        broker=broker,
        us_provider=StaticUsMarketCapProvider(quotes=[], usdkrw=1380.0),
        korean_provider=None,
        logger=MagicMock(),
    )

    assert await service._build_adr_gap(1380.0) is None
    service._logger.warning.assert_called_once()


@pytest.mark.asyncio
async def test_adr_gap_is_none_when_domestic_price_is_zero():
    broker = SimpleNamespace()
    broker.get_current_price = AsyncMock(return_value=ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data={"output": {"stck_prpr": "0"}}
    ))
    broker.get_overseas_price = AsyncMock()
    service = MarketCapGapService(
        broker=broker,
        us_provider=StaticUsMarketCapProvider(quotes=[], usdkrw=1380.0),
        korean_provider=None,
        logger=MagicMock(),
    )

    assert await service._build_adr_gap(1380.0) is None
    broker.get_overseas_price.assert_not_awaited()


@pytest.mark.asyncio
async def test_adr_gap_is_none_when_every_adr_symbol_lookup_fails():
    broker = SimpleNamespace()
    broker.get_current_price = AsyncMock(return_value=ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data={"stck_prpr": "2076000"}
    ))
    broker.get_overseas_price = AsyncMock(side_effect=[
        RuntimeError("overseas down"),
        ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value,
            msg1="OK",
            data=SimpleNamespace(price=0, last=0, last_price=0),
        ),
    ])
    service = MarketCapGapService(
        broker=broker,
        us_provider=StaticUsMarketCapProvider(quotes=[], usdkrw=1380.0),
        korean_provider=None,
        logger=MagicMock(),
    )

    assert await service._build_adr_gap(1380.0) is None
    assert broker.get_overseas_price.await_count == 2
    service._logger.warning.assert_called_once()


def test_build_comparisons_skips_nonpositive_korean_and_missing_us_caps():
    korean = [
        {"symbol": "005930", "name": "삼성전자", "market_cap_krw": 0},
        {"symbol": "000660", "name": "SK하이닉스", "market_cap_krw": 200_000_000_000_000},
    ]
    us = [
        {"symbol": "MU", "name": "Micron", "market_cap_krw": None},
        {"symbol": "NVDA", "name": "NVIDIA", "market_cap_krw": 400_000_000_000_000},
    ]

    comparisons = MarketCapGapService._build_comparisons(korean, us)

    assert [item["us_symbol"] for item in comparisons] == ["NVDA"]
    assert comparisons[0]["ratio"] == 2.0


def test_build_default_uses_yahoo_and_pykrx_providers():
    service = MarketCapGapService.build_default(broker=SimpleNamespace(), logger=MagicMock())

    assert isinstance(service._us_provider, YahooUsMarketCapProvider)
    assert isinstance(service._korean_provider, PykrxKoreanMarketCapProvider)
