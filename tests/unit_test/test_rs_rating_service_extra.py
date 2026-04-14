import pytest
from unittest.mock import AsyncMock, MagicMock
import pandas as pd

from common.types import ErrorCode, ResCommonResponse
from services.rs_rating_service import RSRatingService


def _make_ohlcv_list(dates, closes):
    return [{"date": d, "close": c} for d, c in zip(dates, closes)]


@pytest.mark.asyncio
async def test_get_rs_line_empty_data_returns_empty():
    ohlcv_repo = AsyncMock()
    # stock missing
    ohlcv_repo.get_stock_data.side_effect = [None, None]
    svc = RSRatingService(ohlcv_repo, AsyncMock(), MagicMock())

    resp = await svc.get_rs_line("005930")
    assert resp.rt_cd == ErrorCode.EMPTY_VALUES.value


@pytest.mark.asyncio
async def test_get_rs_line_no_common_dates_returns_empty():
    ohlcv_repo = AsyncMock()
    stock = {"ohlcv": _make_ohlcv_list(["20260101"], [100])}
    bench = {"ohlcv": _make_ohlcv_list(["20260102"], [200])}
    ohlcv_repo.get_stock_data.side_effect = [stock, bench]

    svc = RSRatingService(ohlcv_repo, AsyncMock(), MagicMock())
    resp = await svc.get_rs_line("005930", benchmark_code="069500", limit=10)
    assert resp.rt_cd == ErrorCode.EMPTY_VALUES.value


@pytest.mark.asyncio
async def test_get_rs_line_success_and_new_high_detection():
    # Create aligned OHLCV with increasing stock/bench such that rs_line changes
    dates = [f"202601{day:02d}" for day in range(1, 6)]
    # stock closes: 10, 11, 12, 11, 13
    stock_closes = [10, 11, 12, 11, 13]
    # bench closes: 5, 5, 5, 5, 5 (flat)
    bench_closes = [5, 5, 5, 5, 5]

    ohlcv_repo = AsyncMock()
    stock = {"ohlcv": _make_ohlcv_list(dates, stock_closes)}
    bench = {"ohlcv": _make_ohlcv_list(dates, bench_closes)}
    ohlcv_repo.get_stock_data.side_effect = [stock, bench]

    svc = RSRatingService(ohlcv_repo, AsyncMock(), MagicMock())
    resp = await svc.get_rs_line("005930", benchmark_code="069500", limit=10)
    assert resp.rt_cd == ErrorCode.SUCCESS.value
    assert isinstance(resp.data, list)
    # there should be an entry per common date
    assert len(resp.data) == len(dates)
    # check structure and types
    for item in resp.data:
        assert "date" in item and "close" in item and "rs_line" in item and "rs_line_new_high" in item
    # ensure new high detection: last close 13 > previous 12 -> likely new high True
    assert any(item["rs_line_new_high"] for item in resp.data)


@pytest.mark.asyncio
async def test__fetch_weighted_rs_handles_exception_and_returns_none():
    ohlcv_repo = AsyncMock()
    async def raise_exc(code, ohlcv_limit=None):
        raise Exception("db error")
    ohlcv_repo.get_stock_data.side_effect = raise_exc

    svc = RSRatingService(ohlcv_repo, AsyncMock(), MagicMock())
    val = await svc._fetch_weighted_rs("005930")
    assert val is None


@pytest.mark.asyncio
async def test_compute_and_store_ratings_upsert_failure_returns_unknown_error():
    # Prepare repos: one code available, OHLCV yields valid series
    code_repo = MagicMock()
    code_repo.code_to_name = {"005930": "삼성전자"}

    ohlcv_repo = AsyncMock()
    # 64 candles required; create minimal valid series (first=100 last=110)
    closes = [100] + [100] * 62 + [110]
    ohlcv_repo.get_stock_data.return_value = {"ohlcv": [{"close": c} for c in closes]}

    rs_repo = AsyncMock()
    async def upsert_fail(records):
        raise Exception("db write failure")
    rs_repo.upsert_batch.side_effect = upsert_fail

    svc = RSRatingService(ohlcv_repo, rs_repo, code_repo)
    resp = await svc.compute_and_store_ratings("20260101")
    assert resp.rt_cd == ErrorCode.UNKNOWN_ERROR.value


@pytest.mark.asyncio
async def test_get_rating_handles_repo_exception_and_returns_unknown_error():
    rs_repo = AsyncMock()
    async def raise_latest():
        raise Exception("db error")
    rs_repo.get_latest_date.side_effect = raise_latest
    svc = RSRatingService(AsyncMock(), rs_repo, MagicMock())
    resp = await svc.get_rating("005930")
    assert resp.rt_cd == ErrorCode.UNKNOWN_ERROR.value
