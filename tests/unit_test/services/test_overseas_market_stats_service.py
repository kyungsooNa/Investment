"""
OverseasMarketStatsService 단위 테스트.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.market_cap_gap_service import UsQuoteSnapshot
from services.overseas_market_stats_service import OverseasMarketStatsService


def _snap(symbol, price=100.0, rate=1.0, volume=1000, cap=1_000_000):
    return UsQuoteSnapshot(
        symbol=symbol,
        name=f"{symbol} Inc.",
        currency="USD",
        price=price,
        change_rate=rate,
        volume=volume,
        market_cap=cap,
    )


@pytest.fixture
def universe():
    repo = MagicMock()
    repo.all_symbols.return_value = ["AAPL", "MSFT", "XOM"]
    repo.get_meta.side_effect = lambda symbol: {
        "AAPL": {"name": "Apple Inc.", "sector": "Information Technology"},
        "MSFT": {"name": "Microsoft", "sector": "Information Technology"},
        "XOM": {"name": "Exxon Mobil", "sector": "Energy"},
    }.get(str(symbol).upper())
    return repo


@pytest.fixture
def provider():
    p = MagicMock()
    p.fetch_snapshots = AsyncMock(return_value=[
        _snap("AAPL", cap=3_000_000_000_000),
        _snap("MSFT", cap=4_000_000_000_000),
        _snap("XOM", cap=500_000_000_000),
    ])
    p.fetch_usdkrw = AsyncMock(return_value=1400.0)
    return p


@pytest.fixture
def service(universe, provider):
    return OverseasMarketStatsService(
        universe_factory=lambda: universe, provider=provider, ttl_sec=300, clock=lambda: 0.0
    )


async def test_top_market_cap_sorted_desc_with_rank(service):
    result = await service.get_top_market_cap(limit=10)

    symbols = [item["symbol"] for item in result["items"]]
    assert symbols == ["MSFT", "AAPL", "XOM"]
    assert [item["rank"] for item in result["items"]] == [1, 2, 3]


async def test_top_market_cap_applies_limit(service):
    result = await service.get_top_market_cap(limit=2)
    assert [item["symbol"] for item in result["items"]] == ["MSFT", "AAPL"]


async def test_top_market_cap_includes_sector_and_krw_conversion(service):
    result = await service.get_top_market_cap(limit=1)

    item = result["items"][0]
    assert result["fx_rate"] == 1400.0
    assert item["name"] == "Microsoft"
    assert item["sector"] == "Information Technology"
    assert item["market_cap_usd"] == 4_000_000_000_000
    assert item["market_cap_krw"] == int(4_000_000_000_000 * 1400.0)


async def test_krw_is_none_when_fx_unavailable(universe, provider):
    provider.fetch_usdkrw = AsyncMock(return_value=None)
    service = OverseasMarketStatsService(
        universe_factory=lambda: universe, provider=provider, clock=lambda: 0.0
    )

    result = await service.get_top_market_cap(limit=1)

    assert result["fx_rate"] is None
    assert result["items"][0]["market_cap_krw"] is None


async def test_symbol_without_market_cap_is_excluded(universe, provider):
    provider.fetch_snapshots = AsyncMock(return_value=[
        _snap("AAPL", cap=3_000_000_000_000),
        _snap("XOM", cap=0),
    ])
    service = OverseasMarketStatsService(
        universe_factory=lambda: universe, provider=provider, clock=lambda: 0.0
    )

    result = await service.get_top_market_cap(limit=10)

    assert [item["symbol"] for item in result["items"]] == ["AAPL"]


async def test_unknown_symbol_falls_back_to_snapshot_name(universe, provider):
    provider.fetch_snapshots = AsyncMock(return_value=[_snap("ZZZZ", cap=1)])
    service = OverseasMarketStatsService(
        universe_factory=lambda: universe, provider=provider, clock=lambda: 0.0
    )

    item = (await service.get_top_market_cap(limit=1))["items"][0]

    assert item["name"] == "ZZZZ Inc."
    assert item["sector"] == ""


async def test_snapshots_are_cached_within_ttl(universe, provider):
    now = [0.0]
    service = OverseasMarketStatsService(
        universe_factory=lambda: universe, provider=provider, ttl_sec=300, clock=lambda: now[0]
    )

    await service.get_top_market_cap()
    now[0] = 299.0
    await service.get_top_market_cap()

    assert provider.fetch_snapshots.await_count == 1
    assert provider.fetch_usdkrw.await_count == 1


async def test_snapshots_refetch_after_ttl(universe, provider):
    now = [0.0]
    service = OverseasMarketStatsService(
        universe_factory=lambda: universe, provider=provider, ttl_sec=300, clock=lambda: now[0]
    )

    await service.get_top_market_cap()
    now[0] = 301.0
    await service.get_top_market_cap()

    assert provider.fetch_snapshots.await_count == 2


async def test_empty_universe_returns_empty_without_calling_provider(provider):
    empty = MagicMock()
    empty.all_symbols.return_value = []
    service = OverseasMarketStatsService(
        universe_factory=lambda: empty, provider=provider, clock=lambda: 0.0
    )

    result = await service.get_top_market_cap()

    assert result == {"fx_rate": None, "items": []}
    provider.fetch_snapshots.assert_not_awaited()


async def test_missing_universe_repository_returns_empty(provider):
    service = OverseasMarketStatsService(
        universe_factory=lambda: None, provider=provider, clock=lambda: 0.0
    )

    assert (await service.get_top_market_cap())["items"] == []
    provider.fetch_snapshots.assert_not_awaited()


async def test_fx_failure_does_not_break_market_cap(universe, provider):
    provider.fetch_usdkrw = AsyncMock(side_effect=RuntimeError("fx down"))
    service = OverseasMarketStatsService(
        universe_factory=lambda: universe, provider=provider, clock=lambda: 0.0
    )

    result = await service.get_top_market_cap(limit=1)

    assert result["fx_rate"] is None
    assert result["items"][0]["symbol"] == "MSFT"


def test_universe_is_not_built_at_construction(provider):
    """유니버스 생성에는 외부 다운로드가 따라붙을 수 있어 웹 시작 경로에서 만들지 않는다."""
    calls = []

    def _factory():
        calls.append(1)
        return MagicMock(all_symbols=MagicMock(return_value=[]))

    OverseasMarketStatsService(universe_factory=_factory, provider=provider, clock=lambda: 0.0)

    assert calls == []


async def test_universe_is_built_once_across_calls(universe, provider):
    calls = []

    def _factory():
        calls.append(1)
        return universe

    service = OverseasMarketStatsService(
        universe_factory=_factory, provider=provider, ttl_sec=0, clock=lambda: 0.0
    )

    await service.get_top_market_cap()
    await service.get_top_market_cap()

    assert calls == [1]


async def test_universe_failure_is_not_retried_every_call(provider):
    calls = []

    def _factory():
        calls.append(1)
        raise RuntimeError("listing download failed")

    logger = MagicMock()
    service = OverseasMarketStatsService(
        universe_factory=_factory, provider=provider, logger=logger, clock=lambda: 0.0
    )

    assert (await service.get_top_market_cap())["items"] == []
    assert (await service.get_top_market_cap())["items"] == []

    assert calls == [1]
    assert logger.warning.call_count == 1
    provider.fetch_snapshots.assert_not_awaited()
