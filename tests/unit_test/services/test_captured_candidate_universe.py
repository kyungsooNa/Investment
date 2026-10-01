import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.captured_candidate_universe import (
    CapturedCandidateProvider,
    CapturedCandidateUniverse,
)


def test_provider_reads_only_requested_candidate_source(tmp_path):
    (tmp_path / "replay_microstructure_20260929.json").write_text(
        json.dumps({
            "metadata": {
                "trade_date": "20260929",
                "candidate_sources": {
                    "base": ["005930", "000660"],
                    "ranking_supplement": ["035420"],
                },
            }
        }),
        encoding="utf-8",
    )

    provider = CapturedCandidateProvider.from_replay_dir(tmp_path)

    assert provider.codes_as_of("20260929", source="base") == {"005930", "000660"}
    assert provider.codes_as_of("20260929", source="ranking_supplement") == {"035420"}


@pytest.mark.asyncio
async def test_universe_filters_current_watchlist_and_synthesizes_missing_captured_code():
    base_item = SimpleNamespace(code="005930")
    base = SimpleNamespace(get_watchlist=AsyncMock(return_value={"005930": base_item, "035420": object()}))
    provider = SimpleNamespace(codes_as_of=lambda date, source: {"005930", "000660"})
    clock = SimpleNamespace(get_current_kst_time=lambda: datetime(2026, 9, 29, 12, 0, 0))
    sqs = SimpleNamespace(get_recent_daily_ohlcv=AsyncMock(return_value=[
        {"close": 100, "volume": 10},
        {"close": 120, "volume": 10},
    ]))
    synthesized = object()
    factory_calls = []

    def factory(**kwargs):
        factory_calls.append(kwargs)
        return synthesized

    universe = CapturedCandidateUniverse(
        base,
        provider=provider,
        sqs=sqs,
        clock=clock,
        item_factory=factory,
    )

    watchlist = await universe.get_watchlist()

    assert watchlist == {"005930": base_item, "000660": synthesized}
    assert factory_calls[0]["avg_trading_value_5d"] == 1100


@pytest.mark.asyncio
async def test_universe_fails_closed_when_capture_has_no_base_candidates():
    base = SimpleNamespace(get_watchlist=AsyncMock(return_value={"005930": object()}))
    provider = SimpleNamespace(codes_as_of=lambda date, source: set())
    clock = SimpleNamespace(get_current_kst_time=lambda: datetime(2026, 9, 29, 12, 0, 0))
    universe = CapturedCandidateUniverse(
        base,
        provider=provider,
        sqs=SimpleNamespace(),
        clock=clock,
        item_factory=lambda **kwargs: object(),
    )

    assert await universe.get_watchlist() == {}
