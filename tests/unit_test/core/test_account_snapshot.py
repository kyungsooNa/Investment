"""AccountSnapshotCache 단위 테스트."""
import asyncio
import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from core.account_snapshot import AccountSnapshotCache, AccountSnapshot
from common.types import ResCommonResponse, ErrorCode


def _ok_response(total_equity=10_000_000, available_cash=5_000_000, positions=None):
    positions = positions or []
    return ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK",
        data={
            "output2": {
                "tot_evlu_amt": str(total_equity),
                "ord_psbl_cash": str(available_cash),
            },
            "output1": positions,
        }
    )


def _make_broker(response=None):
    broker = AsyncMock()
    broker.get_account_balance.return_value = response or _ok_response()
    return broker


# ── cache hit ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cache_hit_no_api_call():
    broker = _make_broker()
    cache = AccountSnapshotCache(broker_api_wrapper=broker, ttl_sec=60)
    snap1 = await cache.get()
    snap2 = await cache.get()
    assert snap1 is snap2
    assert broker.get_account_balance.call_count == 1


# ── cache miss / TTL ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ttl_expired_triggers_refresh():
    broker = _make_broker()
    cache = AccountSnapshotCache(broker_api_wrapper=broker, ttl_sec=1)
    await cache.get()

    # TTL 만료 시뮬레이션: fetched_at을 과거로 설정
    cache._snapshot.fetched_at = datetime.now() - timedelta(seconds=2)

    await cache.get()
    assert broker.get_account_balance.call_count == 2


# ── invalidate ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_invalidate_triggers_refresh():
    broker = _make_broker()
    cache = AccountSnapshotCache(broker_api_wrapper=broker, ttl_sec=60)
    await cache.get()
    cache.invalidate()
    assert cache._snapshot is None
    await cache.get()
    assert broker.get_account_balance.call_count == 2


# ── warm_up ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_warm_up_fetches_and_caches():
    broker = _make_broker()
    cache = AccountSnapshotCache(broker_api_wrapper=broker, ttl_sec=60)
    await cache.warm_up()
    assert broker.get_account_balance.call_count == 1
    snap = await cache.get()
    assert broker.get_account_balance.call_count == 1  # still cached
    assert snap.total_equity == 10_000_000


# ── data parsing ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_parses_positions():
    resp = _ok_response(positions=[
        {"pdno": "005930", "evlu_amt": "500000"},
        {"pdno": "000660", "evlu_amt": "300000"},
    ])
    broker = _make_broker(response=resp)
    cache = AccountSnapshotCache(broker_api_wrapper=broker, ttl_sec=60)
    snap = await cache.get()
    assert snap.positions == {"005930": 500_000, "000660": 300_000}
    assert snap.total_equity == 10_000_000
    assert snap.available_cash == 5_000_000


# ── API failure → stale snapshot / empty fallback ─────────────────────

@pytest.mark.asyncio
async def test_api_failure_returns_empty_snapshot():
    broker = AsyncMock()
    broker.get_account_balance.return_value = ResCommonResponse(
        rt_cd="1", msg1="error", data={}
    )
    cache = AccountSnapshotCache(broker_api_wrapper=broker, ttl_sec=60)
    snap = await cache.get()
    assert snap.total_equity == 0
    assert snap.available_cash == 0
    assert snap.positions == {}


@pytest.mark.asyncio
async def test_api_failure_mid_ttl_preserves_previous_snapshot():
    """TTL 만료(invalidate 없음) 후 API 실패 시 이전 스냅샷을 유지한다."""
    broker = _make_broker(_ok_response(total_equity=8_000_000))
    cache = AccountSnapshotCache(broker_api_wrapper=broker, ttl_sec=60)
    await cache.get()

    # API 실패로 교체
    broker.get_account_balance.return_value = ResCommonResponse(
        rt_cd="1", msg1="error", data={}
    )
    # TTL 만료 시뮬레이션 (invalidate가 아니므로 _snapshot은 남아있음)
    cache._snapshot.fetched_at = cache._snapshot.fetched_at.replace(year=2000)

    snap = await cache.get()
    assert snap.total_equity == 8_000_000  # 이전 스냅샷 유지


# ── singleflight (동시 요청 단일 fetch) ─────────────────────────────

@pytest.mark.asyncio
async def test_concurrent_get_calls_api_once():
    fetch_count = 0

    async def slow_balance(**kwargs):
        nonlocal fetch_count
        fetch_count += 1
        await asyncio.sleep(0)
        return _ok_response()

    broker = AsyncMock()
    broker.get_account_balance.side_effect = slow_balance

    cache = AccountSnapshotCache(broker_api_wrapper=broker, ttl_sec=60)
    results = await asyncio.gather(*[cache.get() for _ in range(5)])
    assert fetch_count == 1
    assert all(r.total_equity == 10_000_000 for r in results)


@pytest.mark.asyncio
async def test_parses_kis_output2_list_shape():
    """KIS 잔고 API의 실제 output2=[{...}] 형태를 파싱한다."""
    resp = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value,
        msg1="OK",
        data={
            "output1": [
                {"pdno": "007810", "evlu_amt": "672000"},
            ],
            "output2": [
                {
                    "dnca_tot_amt": "496594461",
                    "prvs_rcdl_excc_amt": "496594461",
                    "tot_evlu_amt": "509521941",
                    "nass_amt": "509521941",
                }
            ],
        },
    )
    broker = _make_broker(response=resp)
    cache = AccountSnapshotCache(broker_api_wrapper=broker, ttl_sec=60)

    snap = await cache.get()

    assert snap.total_equity == 509_521_941
    assert snap.available_cash == 496_594_461
    assert snap.positions == {"007810": 672_000}


@pytest.mark.asyncio
async def test_parses_total_equity_from_nass_amt_fallback():
    resp = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value,
        msg1="OK",
        data={
            "output1": [],
            "output2": [
                {
                    "dnca_tot_amt": "1,500,000",
                    "tot_evlu_amt": "",
                    "nass_amt": "2,000,000",
                }
            ],
        },
    )
    broker = _make_broker(response=resp)
    cache = AccountSnapshotCache(broker_api_wrapper=broker, ttl_sec=60)

    snap = await cache.get()

    assert snap.total_equity == 2_000_000
    assert snap.available_cash == 1_500_000


@pytest.mark.asyncio
async def test_get_returns_fresh_snapshot_without_fetching_again():
    broker = _make_broker(_ok_response(total_equity=8_000_000))
    cache = AccountSnapshotCache(broker_api_wrapper=broker, ttl_sec=60)

    first = await cache.get()
    second = await cache.get()

    assert second is first
    broker.get_account_balance.assert_awaited_once()


@pytest.mark.asyncio
async def test_fetch_exception_returns_empty_snapshot_and_preserves_previous():
    logger = MagicMock()
    broker = AsyncMock()
    broker.get_account_balance.side_effect = RuntimeError("boom")
    cache = AccountSnapshotCache(broker_api_wrapper=broker, logger=logger, ttl_sec=60)

    empty = await cache.get()
    assert empty.total_equity == 0
    assert empty.available_cash == 0
    logger.error.assert_called_once()

    broker.get_account_balance.side_effect = None
    broker.get_account_balance.return_value = _ok_response(total_equity=7_000_000)
    await cache.warm_up()
    cache._snapshot.fetched_at = cache._snapshot.fetched_at.replace(year=2000)
    broker.get_account_balance.side_effect = RuntimeError("again")

    stale = await cache.get()

    assert stale.total_equity == 7_000_000


def test_account_snapshot_parse_helpers_handle_invalid_shapes():
    assert AccountSnapshotCache._parse_int(None, "x") == 0
    assert AccountSnapshotCache._parse_int({"x": "bad"}, "x") == 0
    assert AccountSnapshotCache._first_dict([]) == {}
    assert AccountSnapshotCache._first_dict([{"a": 1}]) == {"a": 1}
    assert AccountSnapshotCache._get_str(None, "pdno") == ""
