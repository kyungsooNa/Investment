"""StockQueryService.price_lookup_stats_snapshot 단위 테스트 (P2 2-2 2차)."""
from __future__ import annotations

from unittest.mock import MagicMock

from services.stock_query_service import StockQueryService


def _make_sqs() -> StockQueryService:
    return StockQueryService(
        market_data_service=MagicMock(),
        logger=MagicMock(),
        market_clock=MagicMock(),
    )


def test_price_lookup_stats_snapshot_returns_copy():
    sqs = _make_sqs()

    snap = sqs.price_lookup_stats_snapshot()
    snap["snapshot_hit"] = 999
    snap["new_key"] = 1

    later = sqs.price_lookup_stats_snapshot()
    assert later["snapshot_hit"] == 0
    assert "new_key" not in later


def test_price_lookup_stats_snapshot_reflects_increments():
    sqs = _make_sqs()
    sqs._count_price_lookup("snapshot_hit")
    sqs._count_price_lookup("snapshot_hit")
    sqs._count_price_lookup("rest_fallback")

    snap = sqs.price_lookup_stats_snapshot()
    assert snap["snapshot_hit"] == 2
    assert snap["rest_fallback"] == 1
    assert snap["no_tick_fallback"] == 0


def test_price_lookup_stats_snapshot_contains_known_keys():
    sqs = _make_sqs()
    snap = sqs.price_lookup_stats_snapshot()

    expected_keys = {
        "snapshot_hit",
        "no_tick_fallback",
        "stale_fallback",
        "rest_fallback",
        "force_fresh_bypass",
        "full_output_required",
        "stream_unavailable_fallback",
        "conclusion_hit",
        "conclusion_stale_fallback",
        "conclusion_missing_fallback",
    }
    assert expected_keys.issubset(snap.keys())
