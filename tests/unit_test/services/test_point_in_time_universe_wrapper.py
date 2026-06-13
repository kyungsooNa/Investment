"""PointInTimeAugmentedUniverse — 백테스트 universe 에 상폐 종목 후보 합류 (R-1 3b).

라이브 universe 서비스는 건드리지 않는 백테스트 전용 wrapper. base watchlist 에
"그 날 상장돼 있던 상폐 종목"을 추가하되, 시가총액 데이터가 없으므로 시총 필터는
면제하고 백필 OHLCV 기반 5일 평균 거래대금 게이트만 적용한다.
"""
from __future__ import annotations

import pytest

from common.types import ErrorCode, ResCommonResponse
from services.point_in_time_universe_provider import PointInTimeUniverseProvider
from services.point_in_time_universe_wrapper import PointInTimeAugmentedUniverse


class _FakeClock:
    def __init__(self, ymd):
        self._ymd = ymd

    def get_current_kst_time(self):
        import datetime as _dt
        return _dt.datetime.strptime(self._ymd, "%Y%m%d")


class _FakeBase:
    def __init__(self, watchlist):
        self._wl = watchlist
        self.market_timing_calls = []
        self.excluded = []

    async def get_watchlist(self, logger=None):
        return dict(self._wl)

    async def is_market_timing_ok(self, market, **kwargs):
        self.market_timing_calls.append(market)
        return True

    def exclude_code_for_today(self, code, *args, **kwargs):
        self.excluded.append(code)


class _FakeSqs:
    """code -> rows(list of {date, close, volume})"""
    def __init__(self, rows_by_code):
        self._rows = rows_by_code

    async def get_recent_daily_ohlcv(self, code, limit=5, end_date=None):
        rows = self._rows.get(code, [])
        return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="ok", data=rows)


def _item(**kw):
    return {"code": kw["code"], "name": kw["name"], "market": kw["market"],
            "avg_trading_value_5d": kw["avg_trading_value_5d"]}


def _provider():
    return PointInTimeUniverseProvider.from_records_dicts([
        {"symbol": "900100", "name": "상폐기업", "market": "KOSDAQ",
         "listing_date": "2020-01-01", "delisting_date": "2026-03-15", "source": "delisted"},
        {"symbol": "900200", "name": "저유동상폐", "market": "KOSDAQ",
         "listing_date": "2020-01-01", "delisting_date": "2026-03-15", "source": "delisted"},
    ])


def _make(base_wl, rows_by_code, *, ymd="20260301", min_tv=1_000):
    return PointInTimeAugmentedUniverse(
        _FakeBase(base_wl),
        pit_provider=_provider(),
        sqs=_FakeSqs(rows_by_code),
        clock=_FakeClock(ymd),
        item_factory=_item,
        min_avg_trading_value_5d=min_tv,
    )


@pytest.mark.asyncio
async def test_adds_qualifying_delisted_code():
    rows = {"900100": [{"date": "20260227", "close": 100, "volume": 50},
                       {"date": "20260228", "close": 100, "volume": 50}]}  # tv=5000 >= 1000
    wrap = _make({"005930": {"code": "005930"}}, rows)
    wl = await wrap.get_watchlist()
    assert "005930" in wl  # base 보존
    assert wl["900100"]["avg_trading_value_5d"] == 5000.0
    assert wl["900100"]["name"] == "상폐기업"


@pytest.mark.asyncio
async def test_skips_delisted_below_min_trading_value():
    rows = {"900100": [{"date": "20260228", "close": 10, "volume": 5}]}  # tv=50 < 1000
    wrap = _make({}, rows)
    wl = await wrap.get_watchlist()
    assert "900100" not in wl


@pytest.mark.asyncio
async def test_skips_delisted_already_in_base():
    rows = {"900100": [{"date": "20260228", "close": 100, "volume": 50}]}
    wrap = _make({"900100": {"code": "900100", "src": "base"}}, rows)
    wl = await wrap.get_watchlist()
    assert wl["900100"]["src"] == "base"  # base 항목 유지(중복 추가 안 함)


@pytest.mark.asyncio
async def test_no_delisted_on_date_returns_base_unchanged():
    # 상폐일(2026-03-15) 이후 날짜 → delisted_codes_as_of 비어있음
    wrap = _make({"005930": {"code": "005930"}}, {}, ymd="20260401")
    wl = await wrap.get_watchlist()
    assert set(wl.keys()) == {"005930"}


@pytest.mark.asyncio
async def test_uses_as_of_date_as_end_date_for_ohlcv():
    captured = {}

    class _CapSqs(_FakeSqs):
        async def get_recent_daily_ohlcv(self, code, limit=5, end_date=None):
            captured[code] = (limit, end_date)
            return await super().get_recent_daily_ohlcv(code, limit=limit, end_date=end_date)

    wrap = PointInTimeAugmentedUniverse(
        _FakeBase({}),
        pit_provider=_provider(),
        sqs=_CapSqs({"900100": [{"date": "20260228", "close": 100, "volume": 50}]}),
        clock=_FakeClock("20260301"),
        item_factory=_item,
        min_avg_trading_value_5d=1_000,
    )
    await wrap.get_watchlist()
    assert captured["900100"][1] == "20260301"  # end_date = as-of date (lookahead 방지)


@pytest.mark.asyncio
async def test_delegates_unknown_methods_to_base():
    wrap = _make({}, {})
    assert await wrap.is_market_timing_ok("KOSDAQ") is True
    assert wrap._base.market_timing_calls == ["KOSDAQ"]


@pytest.mark.asyncio
async def test_exclude_code_skips_added_delisted_and_delegates():
    rows = {"900100": [{"date": "20260228", "close": 100, "volume": 50}]}
    wrap = _make({}, rows)
    wrap.exclude_code_for_today("900100")
    wl = await wrap.get_watchlist()
    assert "900100" not in wl
    assert "900100" in wrap._base.excluded  # base 에도 위임
