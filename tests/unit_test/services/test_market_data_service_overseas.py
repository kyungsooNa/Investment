"""해외주식 일봉 어댑터 라우팅/정규화 테스트 (Phase 1-1).

기존 전략(LarryWilliamsVBO 등)이 호출하는 get_recent_daily_ohlcv / get_ohlcv_range 가
overseas exchange 인자를 받으면 get_overseas_dailyprice 로 위임하고
국내와 동일한 {date,open,high,low,close,volume} 스키마로 정규화하는지 고정한다.
국내 경로(KRX 기본값)는 건드리지 않는다(별도 파일로 격리).
"""
import pytest
from types import SimpleNamespace
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

from services.market_data_service import MarketDataService
from common.types import ErrorCode, ResCommonResponse
from common.overseas_types import OverseasExchange, OverseasPriceSummary


@pytest.fixture
def mds():
    broker = MagicMock()
    broker.inquire_daily_itemchartprice = AsyncMock()
    broker.get_overseas_dailyprice = AsyncMock()
    broker.get_overseas_price = AsyncMock()
    broker.get_current_price = AsyncMock()
    broker.get_price_summary = AsyncMock()

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


def _price_resp(price=150.5, vol=1000, chg=1.2):
    return ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value,
        msg1="정상",
        data=OverseasPriceSummary(
            symbol="AAPL", exchange=OverseasExchange.NASD,
            price=price, change_rate=chg, volume=vol,
        ),
    )


@pytest.mark.asyncio
async def test_get_current_price_overseas_routes_and_maps(mds):
    mds.broker.get_overseas_price.return_value = _price_resp()

    resp = await mds.service.get_current_price("AAPL", exchange=OverseasExchange.NASD)

    # 국내 현재가 경로/캐시는 타지 않는다
    mds.broker.get_current_price.assert_not_called()
    mds.broker.get_overseas_price.assert_awaited_once()
    # 전략이 읽는 키(stck_prpr / price / output.stck_prpr) 모두 매핑
    assert resp.rt_cd == ErrorCode.SUCCESS.value
    assert resp.data["stck_prpr"] == 150.5
    assert resp.data["price"] == 150.5
    assert resp.data["acml_vol"] == 1000
    assert resp.data["output"]["stck_prpr"] == 150.5


@pytest.mark.asyncio
async def test_get_current_price_overseas_accepts_string_exchange(mds):
    mds.broker.get_overseas_price.return_value = _price_resp(price=42.0)

    resp = await mds.service.get_current_price("MSFT", exchange="NYSE")

    mds.broker.get_overseas_price.assert_awaited_once()
    assert resp.data["output"]["stck_prpr"] == 42.0


@pytest.mark.asyncio
async def test_get_current_price_overseas_failure_propagates(mds):
    fail = ResCommonResponse(rt_cd=ErrorCode.API_ERROR.value, msg1="fail", data=None)
    mds.broker.get_overseas_price.return_value = fail

    resp = await mds.service.get_current_price("AAPL", exchange=OverseasExchange.AMEX)

    assert resp.rt_cd == ErrorCode.API_ERROR.value


@pytest.mark.asyncio
async def test_get_price_summary_overseas_maps_current(mds):
    mds.broker.get_overseas_price.return_value = _price_resp(price=200.0, vol=500, chg=-0.8)

    resp = await mds.service.get_price_summary("AAPL", exchange=OverseasExchange.NASD)

    mds.broker.get_price_summary.assert_not_called()
    assert resp.data["current"] == 200.0
    assert resp.data["change_rate"] == -0.8
    assert resp.data["volume"] == 500


@pytest.mark.asyncio
async def test_normalize_handles_both_domestic_and_overseas_keys(mds):
    domestic = [{"stck_bsop_date": "20260101", "stck_oprc": "10", "stck_hgpr": "12",
                 "stck_lwpr": "9", "stck_clpr": "11", "acml_vol": "300"}]
    overseas = [{"xymd": "20260102", "open": "10", "high": "12", "low": "9", "clos": "11", "tvol": "300"}]

    d_rows = mds.service._normalize_ohlcv_rows(domestic)
    o_rows = mds.service._normalize_ohlcv_rows(overseas)

    assert d_rows[0] == {"date": "20260101", "open": 10.0, "high": 12.0, "low": 9.0, "close": 11.0, "volume": 300}
    assert o_rows[0] == {"date": "20260102", "open": 10.0, "high": 12.0, "low": 9.0, "close": 11.0, "volume": 300}


# ══════════════ 해외 일봉 분할 수집 ══════════════
#
# KIS 해외 일봉 TR 은 start_date 를 무시하고 end_date 기준 마지막 ~100봉만 돌려준다
# (2026-08 실측, scripts/fetch_overseas_ohlcv.py). 국내 경로에는 100일 단위로 끊어
# 반복 호출하는 루프가 있지만 해외 경로에는 없어, 200MA 처럼 100봉을 넘는 지표는
# 어떤 종목에서도 계산되지 않았다.

_PAGE_ANCHOR = datetime(2026, 5, 13)


def _paged_dailyprice(*, total_days: int, page: int = 100):
    """end_date 기준 마지막 `page` 봉만 돌려주는 KIS 해외 일봉 동작을 모사한다."""
    calendar = sorted(
        (_PAGE_ANCHOR - timedelta(days=i)).strftime("%Y%m%d") for i in range(total_days)
    )

    def _handler(symbol, *, exchange=None, start_date="", end_date="", period="D"):
        available = [d for d in calendar if d <= end_date]
        rows = [
            {"xymd": d, "open": "10", "high": "11", "low": "9", "clos": "10", "tvol": "1"}
            for d in available[-page:]
        ]
        return _overseas_resp(rows)

    return _handler


@pytest.mark.asyncio
async def test_get_recent_daily_ohlcv_overseas_pages_back_beyond_one_response(mds):
    """limit 이 1회 응답 상한을 넘으면 end_date 를 과거로 옮겨가며 이어붙인다."""
    mds.broker.get_overseas_dailyprice.side_effect = _paged_dailyprice(total_days=400)

    rows = await mds.service.get_recent_daily_ohlcv("AAPL", limit=250, exchange=OverseasExchange.NASD)

    assert len(rows) == 250
    assert rows == sorted(rows, key=lambda r: r["date"])
    assert rows[-1]["date"] == "20260513"
    assert mds.broker.get_overseas_dailyprice.await_count == 3

    # 두 번째 호출부터는 직전 페이지의 최고(最古) 봉 하루 전을 end_date 로 쓴다.
    ends = [c.kwargs["end_date"] for c in mds.broker.get_overseas_dailyprice.await_args_list]
    assert ends[0] == "20260513"
    assert ends[1] < ends[0]
    assert ends[2] < ends[1]


@pytest.mark.asyncio
async def test_get_recent_daily_ohlcv_overseas_uses_one_call_within_a_page(mds):
    """100봉 안에서 끝나는 기존 호출자(VBO/CB/BGU/OSB/PP)는 호출 수가 늘지 않는다."""
    mds.broker.get_overseas_dailyprice.side_effect = _paged_dailyprice(total_days=400)

    rows = await mds.service.get_recent_daily_ohlcv("AAPL", limit=60, exchange=OverseasExchange.NASD)

    assert len(rows) == 60
    assert mds.broker.get_overseas_dailyprice.await_count == 1


@pytest.mark.asyncio
async def test_get_recent_daily_ohlcv_overseas_stops_when_history_exhausted(mds):
    """상장 이력이 짧아 더 과거가 없으면 빈 페이지에서 멈춘다 (무한 루프 방지)."""
    mds.broker.get_overseas_dailyprice.side_effect = _paged_dailyprice(total_days=150)

    rows = await mds.service.get_recent_daily_ohlcv("AAPL", limit=400, exchange=OverseasExchange.NASD)

    assert len(rows) == 150
    assert mds.broker.get_overseas_dailyprice.await_count == 3


@pytest.mark.asyncio
async def test_get_recent_daily_ohlcv_overseas_caps_page_count(mds):
    """예산 보호 — 아무리 긴 limit 이라도 페이지 수에 상한을 둔다."""
    mds.broker.get_overseas_dailyprice.side_effect = _paged_dailyprice(total_days=3000)

    rows = await mds.service.get_recent_daily_ohlcv("AAPL", limit=2000, exchange=OverseasExchange.NASD)

    assert mds.broker.get_overseas_dailyprice.await_count == MarketDataService.OVERSEAS_DAILY_MAX_PAGES
    assert len(rows) == 100 * MarketDataService.OVERSEAS_DAILY_MAX_PAGES
