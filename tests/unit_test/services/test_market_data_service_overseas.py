"""해외주식 일봉 어댑터 라우팅/정규화 테스트 (Phase 1-1).

기존 전략(LarryWilliamsVBO 등)이 호출하는 get_recent_daily_ohlcv / get_ohlcv_range 가
overseas exchange 인자를 받으면 get_overseas_dailyprice 로 위임하고
국내와 동일한 {date,open,high,low,close,volume} 스키마로 정규화하는지 고정한다.
국내 경로(KRX 기본값)는 건드리지 않는다(별도 파일로 격리).
"""
import pytest
from types import SimpleNamespace
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

from services.market_data_service import MarketDataService
from common.types import ErrorCode, ResCommonResponse
from common.overseas_types import OverseasExchange


@pytest.fixture
def mds():
    broker = MagicMock()
    broker.inquire_daily_itemchartprice = AsyncMock()
    broker.get_overseas_dailyprice = AsyncMock()

    tm = MagicMock()
    tm.get_current_kst_time.return_value = datetime(2026, 5, 13, 10, 0, 0)
    tm.to_yyyymmdd.side_effect = lambda d: d.strftime("%Y%m%d") if isinstance(d, datetime) else str(d)

    stock_repo = MagicMock()
    stock_repo.get_stock_data = AsyncMock(return_value=None)
    stock_repo.upsert_ohlcv = AsyncMock()

    service = MarketDataService(
        broker_api_wrapper=broker,
        env=MagicMock(),
        market_clock=tm,
        logger=MagicMock(),
        stock_repository=stock_repo,
    )
    return SimpleNamespace(service=service, broker=broker, stock_repo=stock_repo)


def _overseas_resp(rows):
    """KIS HHDFS76240000 응답 형태: full json 의 output2 배열에 일봉 행."""
    return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="정상", data={"output1": {}, "output2": rows})


_BARS = [
    {"xymd": "20260512", "open": "100", "high": "110", "low": "95", "clos": "105", "tvol": "1000"},
    {"xymd": "20260513", "open": "105", "high": "120", "low": "104", "clos": "118", "tvol": "2000"},
    {"xymd": "20260511", "open": "98", "high": "101", "low": "96", "clos": "99", "tvol": "500"},
]


@pytest.mark.asyncio
async def test_get_recent_daily_ohlcv_overseas_routes_and_normalizes(mds):
    mds.broker.get_overseas_dailyprice.return_value = _overseas_resp(_BARS)

    rows = await mds.service.get_recent_daily_ohlcv("AAPL", limit=3, exchange=OverseasExchange.NASD)

    # 국내 경로는 절대 타지 않는다
    mds.broker.inquire_daily_itemchartprice.assert_not_called()
    mds.broker.get_overseas_dailyprice.assert_awaited_once()
    # 날짜 오름차순 정규화 + 표준 스키마
    assert [r["date"] for r in rows] == ["20260511", "20260512", "20260513"]
    assert rows[1] == {"date": "20260512", "open": 100.0, "high": 110.0, "low": 95.0, "close": 105.0, "volume": 1000}


@pytest.mark.asyncio
async def test_get_recent_daily_ohlcv_overseas_slices_to_limit(mds):
    mds.broker.get_overseas_dailyprice.return_value = _overseas_resp(_BARS)

    rows = await mds.service.get_recent_daily_ohlcv("AAPL", limit=2, exchange=OverseasExchange.NASD)

    assert len(rows) == 2
    # 가장 최근 2개 (오름차순 정렬 후 뒤에서 2개)
    assert [r["date"] for r in rows] == ["20260512", "20260513"]


@pytest.mark.asyncio
async def test_get_recent_daily_ohlcv_overseas_accepts_string_exchange(mds):
    mds.broker.get_overseas_dailyprice.return_value = _overseas_resp(_BARS)

    rows = await mds.service.get_recent_daily_ohlcv("AAPL", limit=3, exchange="NASD")

    mds.broker.get_overseas_dailyprice.assert_awaited_once()
    assert len(rows) == 3


@pytest.mark.asyncio
async def test_get_recent_daily_ohlcv_overseas_empty_returns_empty(mds):
    mds.broker.get_overseas_dailyprice.return_value = ResCommonResponse(
        rt_cd=ErrorCode.API_ERROR.value, msg1="fail", data=None
    )

    rows = await mds.service.get_recent_daily_ohlcv("AAPL", limit=3, exchange=OverseasExchange.NYSE)

    assert rows == []


@pytest.mark.asyncio
async def test_get_ohlcv_range_overseas_normalizes_output2(mds):
    mds.broker.get_overseas_dailyprice.return_value = _overseas_resp(_BARS)

    resp = await mds.service.get_ohlcv_range(
        "AAPL", "D", "20260501", "20260513", exchange=OverseasExchange.AMEX
    )

    assert resp.rt_cd == ErrorCode.SUCCESS.value
    assert [r["date"] for r in resp.data] == ["20260511", "20260512", "20260513"]
    mds.broker.inquire_daily_itemchartprice.assert_not_called()


@pytest.mark.asyncio
async def test_normalize_handles_both_domestic_and_overseas_keys(mds):
    domestic = [{"stck_bsop_date": "20260101", "stck_oprc": "10", "stck_hgpr": "12",
                 "stck_lwpr": "9", "stck_clpr": "11", "acml_vol": "300"}]
    overseas = [{"xymd": "20260102", "open": "10", "high": "12", "low": "9", "clos": "11", "tvol": "300"}]

    d_rows = mds.service._normalize_ohlcv_rows(domestic)
    o_rows = mds.service._normalize_ohlcv_rows(overseas)

    assert d_rows[0] == {"date": "20260101", "open": 10.0, "high": 12.0, "low": 9.0, "close": 11.0, "volume": 300}
    assert o_rows[0] == {"date": "20260102", "open": 10.0, "high": 12.0, "low": 9.0, "close": 11.0, "volume": 300}
