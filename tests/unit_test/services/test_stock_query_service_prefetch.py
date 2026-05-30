"""StockQueryService.prefetch_prices 단위 테스트 (P2 2-5).

후보군 현재가를 batch(get_multi_price)로 미리 snapshot 캐시에 채워,
종목당 개별 REST(get_current_price fallback) 호출을 제거하는 경로를 고정한다.
"""
from __future__ import annotations

import time
from unittest.mock import MagicMock, AsyncMock

import pytest

from common.types import ErrorCode, ResCommonResponse
from services.stock_query_service import StockQueryService


class _FakePriceStream:
    """price_stream_service 의 get_cached_price / cache_price_snapshot 만 흉내내는 fake."""

    def __init__(self, fresh: dict | None = None):
        # fresh: {code: received_at} — 이미 신선한 snapshot 보유 종목
        self._store: dict[str, dict] = {}
        if fresh:
            for code, ts in fresh.items():
                self._store[code] = {"price": "1", "received_at": ts}

    def get_cached_price(self, code):
        return self._store.get(code)

    def cache_price_snapshot(self, code, price, change="0", rate="0.00", sign="3",
                             volume="0", acml_tr_pbmn=None, high=None, low=None,
                             open_price=None):
        self._store[code] = {
            "price": price,
            "change": change,
            "rate": rate,
            "sign": sign,
            "acml_vol": int(volume) if str(volume).isdigit() else 0,
            "acml_tr_pbmn": int(acml_tr_pbmn) if acml_tr_pbmn and str(acml_tr_pbmn).isdigit() else 0,
            "received_at": time.time(),
        }


def _multi_price_ok(codes):
    data = [{"stck_shrn_iscd": c, "stck_prpr": "10000"} for c in codes]
    return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="ok", data=data)


def _make_sqs(price_stream, mds):
    return StockQueryService(
        market_data_service=mds,
        logger=MagicMock(),
        market_clock=MagicMock(),
        price_stream_service=price_stream,
    )


@pytest.mark.asyncio
async def test_prefetch_batches_in_chunks_of_30():
    pss = _FakePriceStream()
    mds = MagicMock()
    calls = []

    async def fake_multi(codes):
        calls.append(list(codes))
        return _multi_price_ok(codes)

    mds.get_multi_price = fake_multi
    sqs = _make_sqs(pss, mds)

    codes = [f"{i:06d}" for i in range(60)]
    backfilled = await sqs.prefetch_prices(codes)

    assert len(calls) == 2
    assert [len(c) for c in calls] == [30, 30]
    assert backfilled == 60
    # 모든 종목이 snapshot 캐시에 backfill 됨
    assert all(pss.get_cached_price(c) is not None for c in codes)


@pytest.mark.asyncio
async def test_prefetch_skips_fresh_snapshots():
    now = time.time()
    fresh_codes = [f"{i:06d}" for i in range(20)]
    stale_codes = [f"{i:06d}" for i in range(20, 50)]
    pss = _FakePriceStream(fresh={c: now for c in fresh_codes})
    mds = MagicMock()
    calls = []

    async def fake_multi(codes):
        calls.append(list(codes))
        return _multi_price_ok(codes)

    mds.get_multi_price = fake_multi
    sqs = _make_sqs(pss, mds)

    backfilled = await sqs.prefetch_prices(fresh_codes + stale_codes)

    # 신선 종목 20개는 batch 에서 제외 → stale 30개만 1회 호출
    assert len(calls) == 1
    assert sorted(calls[0]) == sorted(stale_codes)
    assert backfilled == 30
    assert sqs.price_lookup_stats_snapshot()["batch_prefetch_skip_fresh"] == 20


@pytest.mark.asyncio
async def test_prefetch_best_effort_on_multi_price_exception():
    pss = _FakePriceStream()
    mds = MagicMock()
    mds.get_multi_price = AsyncMock(side_effect=RuntimeError("temporary API failure"))
    sqs = _make_sqs(pss, mds)

    # 예외가 전파되지 않고 best-effort 로 무시되어야 한다.
    backfilled = await sqs.prefetch_prices(["005930", "000660"])
    assert backfilled == 0


@pytest.mark.asyncio
async def test_prefetch_circuit_breaker_skips_after_repeated_failures():
    pss = _FakePriceStream()
    mds = MagicMock()
    mds.get_multi_price = AsyncMock(
        return_value=ResCommonResponse(rt_cd=ErrorCode.API_ERROR.value, msg1="fail", data=[])
    )
    sqs = _make_sqs(pss, mds)

    for _ in range(3):
        backfilled = await sqs.prefetch_prices(["005930"])
        assert backfilled == 0

    backfilled = await sqs.prefetch_prices(["005930"])

    assert backfilled == 0
    assert mds.get_multi_price.await_count == 3
    assert sqs.price_lookup_stats_snapshot()["batch_prefetch_circuit_open"] == 1


@pytest.mark.asyncio
async def test_prefetch_best_effort_on_error_response():
    pss = _FakePriceStream()
    mds = MagicMock()
    mds.get_multi_price = AsyncMock(
        return_value=ResCommonResponse(rt_cd=ErrorCode.API_ERROR.value, msg1="fail", data=[])
    )
    sqs = _make_sqs(pss, mds)

    backfilled = await sqs.prefetch_prices(["005930", "000660"])
    assert backfilled == 0


@pytest.mark.asyncio
async def test_prefetch_noop_without_price_stream():
    mds = MagicMock()
    mds.get_multi_price = AsyncMock(return_value=_multi_price_ok(["005930"]))
    sqs = _make_sqs(None, mds)

    backfilled = await sqs.prefetch_prices(["005930"])
    assert backfilled == 0
    mds.get_multi_price.assert_not_called()


@pytest.mark.asyncio
async def test_prefetch_then_lookups_hit_snapshot_no_per_code_rest():
    """P2 2-5 헤드라인: 60종목 prefetch 후 60회 get_current_price 가
    전부 snapshot_hit 이고 개별 REST(market_data_service.get_current_price)는 0회."""
    pss = _FakePriceStream()
    mds = MagicMock()
    calls = []

    async def fake_multi(codes):
        calls.append(list(codes))
        return _multi_price_ok(codes)

    mds.get_multi_price = fake_multi
    mds.get_current_price = AsyncMock(
        return_value=ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="REST", data={"output": {}})
    )
    sqs = _make_sqs(pss, mds)

    codes = [f"{i:06d}" for i in range(60)]
    await sqs.prefetch_prices(codes)

    for c in codes:
        resp = await sqs.get_current_price(c, caller="test")
        assert resp.rt_cd == ErrorCode.SUCCESS.value

    # batch 2회만 발생, 개별 REST 는 0회
    assert len(calls) == 2
    mds.get_current_price.assert_not_called()
    stats = sqs.price_lookup_stats_snapshot()
    assert stats["snapshot_hit"] == 60
    assert stats["rest_fallback"] == 0
