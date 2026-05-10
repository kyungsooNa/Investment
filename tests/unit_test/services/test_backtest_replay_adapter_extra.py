import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from common.types import ErrorCode, ResCommonResponse
from services.backtest_replay_adapter import (
    StockQueryBacktestReplayService,
    StockQueryIntradayReplayBarProvider,
)
from services.backtest_execution_simulator import BacktestBar


@pytest.mark.asyncio
async def test_get_current_price_no_rows():
    sq = Mock()
    sq.get_day_intraday_minutes_list = AsyncMock(return_value=[])
    svc = StockQueryBacktestReplayService(sq)
    svc.set_backtest_date("20220101")

    resp = await svc.get_current_price("0001")
    assert resp.rt_cd == ErrorCode.EMPTY_VALUES.value
    assert "intraday rows not found" in resp.msg1


@pytest.mark.asyncio
async def test_get_current_price_with_rows_and_program_provider():
    rows = [
        {
            "stck_prpr": "1000",
            "cntg_vol": "10",
            "stck_oprc": "900",
            "stck_hgpr": "1100",
            "stck_lwpr": "800",
            "acml_vol": "10",
            "acml_tr_pbmn": "10000",
            "stck_sdpr": "950",
            "stck_bsop_date": "20220101",
            "stck_cntg_hour": "093000",
        }
    ]
    sq = Mock()
    sq.get_day_intraday_minutes_list = AsyncMock(return_value=rows)
    program = Mock()
    program.get_program_trade_by_stock_daily = AsyncMock(return_value={"pgtr_ntby_qty": "123"})

    svc = StockQueryBacktestReplayService(sq, program_provider=program)
    svc.set_backtest_date("20220101")

    resp = await svc.get_current_price("0001")
    assert resp.rt_cd == ErrorCode.SUCCESS.value
    out = resp.data["output"]
    assert out["pgtr_ntby_qty"] == "123"
    assert out["stck_prpr"] == "1000"


@pytest.mark.asyncio
async def test_get_stock_conclusion_branches():
    sq = Mock()
    sq.get_day_intraday_minutes_list = AsyncMock(return_value=[])
    svc = StockQueryBacktestReplayService(sq)
    svc.set_backtest_date("20220101")

    r = await svc.get_stock_conclusion("0001")
    assert r.rt_cd == ErrorCode.EMPTY_VALUES.value

    # create a fresh service to avoid cached empty rows from earlier call
    sq.get_day_intraday_minutes_list = AsyncMock(return_value=[{"tday_rltv": "7"}])
    svc2 = StockQueryBacktestReplayService(sq)
    svc2.set_backtest_date("20220101")
    r2 = await svc2.get_stock_conclusion("0001")
    assert r2.rt_cd == ErrorCode.SUCCESS.value
    assert r2.data["output"][0]["tday_rltv"] == "7"


@pytest.mark.asyncio
async def test_get_day_intraday_minutes_list_defaults_and_intraday_normalization():
    sq = Mock()
    sq.get_day_intraday_minutes_list = AsyncMock(return_value="not-a-seq")
    svc = StockQueryBacktestReplayService(sq, session="AFTER")
    svc.set_backtest_date("20220102")

    # defaults applied
    await svc.get_day_intraday_minutes_list("0002")
    sq.get_day_intraday_minutes_list.assert_awaited_with("0002", date_ymd="20220102", session="AFTER")

    # non-sequence becomes empty list in _get_intraday_rows
    res = await svc._get_intraday_rows("0003", "20220103")
    assert res == []


@pytest.mark.asyncio
async def test_row_to_bar_and_price_reached_and_get_bar_next_policy():
    sq = Mock()
    prov = StockQueryIntradayReplayBarProvider(sq, session="REGULAR")

    # non-dict row
    assert prov._row_to_bar("x", default_date="20220101") is None

    # dict without close
    assert prov._row_to_bar({"stck_oprc": "1000"}, default_date="20220101") is None

    # valid row
    row = {
        "stck_prpr": "1000",
        "stck_oprc": "900",
        "stck_hgpr": "1100",
        "stck_lwpr": "800",
        "cntg_vol": "15",
        "stck_bsop_date": "20220102",
        "stck_cntg_hour": "093000",
    }
    bar = prov._row_to_bar(row, default_date="20220102")
    assert isinstance(bar, BacktestBar)
    assert bar.timestamp == "20220102 093000"
    assert bar.open == 900.0
    assert bar.close == 1000.0

    # price_reached false when target_price <= 0
    assert prov._price_reached(bar, 0.0, "BUY") is False

    # test get_bar next_bar policy by patching _get_bars
    bar1 = BacktestBar(timestamp="20220102 093000", open=10, high=20, low=5, close=15, volume=100)
    bar2 = BacktestBar(timestamp="20220102 093100", open=16, high=21, low=14, close=20, volume=120)
    prov._get_bars = AsyncMock(return_value=[bar1, bar2])
    signal = SimpleNamespace(code="X", price="15", action="BUY")

    result = await prov.get_bar(signal=signal, date_ymd="20220102", side="BUY", execution_policy="next_bar")
    assert result is bar2


@pytest.mark.asyncio
async def test_get_recent_daily_ohlcv_and_program_errors():
    sq = Mock()
    sq.get_recent_daily_ohlcv = AsyncMock(return_value={"ok": True})
    svc = StockQueryBacktestReplayService(sq)
    svc.set_backtest_date("20220105")

    # when end_date None -> uses backtest_date
    await svc.get_recent_daily_ohlcv("0001")
    sq.get_recent_daily_ohlcv.assert_awaited_with("0001", limit=60, end_date="20220105")

    # program provider returns ResCommonResponse with error -> pgtr_ntby_qty falls back to 0
    rows = [{"stck_prpr": "100", "stck_bsop_date": "20220105", "stck_cntg_hour": "100000"}]
    sq.get_day_intraday_minutes_list = AsyncMock(return_value=rows)
    program = Mock()
    program.get_program_trade_by_stock_daily = AsyncMock(return_value=ResCommonResponse(rt_cd=ErrorCode.API_ERROR.value, msg1="err", data={}))
    svc2 = StockQueryBacktestReplayService(sq, program_provider=program)
    svc2.set_backtest_date("20220105")
    resp = await svc2.get_current_price("0001")
    assert resp.rt_cd == ErrorCode.SUCCESS.value
    assert resp.data["output"]["pgtr_ntby_qty"] == "0"


@pytest.mark.asyncio
async def test_get_program_net_buy_qty_getter_not_callable_and_to_int():
    sq = Mock()
    program = Mock()
    program.get_program_trade_by_stock_daily = "not-callable"
    svc = StockQueryBacktestReplayService(sq, program_provider=program)
    svc.set_backtest_date("20220106")
    val = await svc._get_program_net_buy_qty("C", "20220106")
    assert val is None


@pytest.mark.asyncio
async def test_get_bars_cache_and_helpers():
    rows = [
        {"stck_prpr": "10", "stck_bsop_date": "20220107", "stck_cntg_hour": "090000"},
        {"stck_prpr": "12", "stck_bsop_date": "20220107", "stck_cntg_hour": "091000"},
    ]
    sq = Mock()
    sq.get_day_intraday_minutes_list = AsyncMock(return_value=rows)
    prov = StockQueryIntradayReplayBarProvider(sq)

    # first call populates cache
    bars1 = await prov._get_bars("X", "20220107")
    assert len(bars1) == 2
    sq.get_day_intraday_minutes_list.assert_awaited_once()

    # second call uses cache (no additional await)
    sq.get_day_intraday_minutes_list.reset_mock()
    bars2 = await prov._get_bars("X", "20220107")
    assert bars2 == bars1
    sq.get_day_intraday_minutes_list.assert_not_awaited()

    # helpers: float and int conversion
    assert prov._to_float("1,234.5") == 1234.5
    assert prov._to_float("-") is None
    assert prov._to_int("1,200") == 1200


def test_policy_value_variants():
    from services.backtest_replay_adapter import _policy_value

    assert _policy_value(SimpleNamespace(value="next_bar")) == "next_bar"
    assert _policy_value(None) == "current_bar"


@pytest.mark.asyncio
async def test_build_current_price_prev_zero_and_program_success():
    rows = [
        {"stck_prpr": "0", "stck_bsop_date": "20220108", "stck_cntg_hour": "090000"},
    ]
    sq = Mock()
    sq.get_day_intraday_minutes_list = AsyncMock(return_value=rows)
    program = Mock()
    program.get_program_trade_by_stock_daily = AsyncMock(return_value=ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="ok", data={"pgtr_ntby_qty": "77"}))
    svc = StockQueryBacktestReplayService(sq, program_provider=program)
    svc.set_backtest_date("20220108")

    resp = await svc.get_current_price("0001")
    out = resp.data["output"]
    assert out["pgtr_ntby_qty"] == "77"
    assert out["prdy_ctrt"] == "0.0"


@pytest.mark.asyncio
async def test_price_reached_variants_and_last_bar():
    sq = Mock()
    prov = StockQueryIntradayReplayBarProvider(sq)

    # create bars manually
    b1 = BacktestBar(timestamp="20220109 090000", open=10, high=12, low=9, close=11, volume=100)
    b2 = BacktestBar(timestamp="20220109 091000", open=13, high=15, low=12, close=14, volume=80)
    prov._get_bars = AsyncMock(return_value=[b1, b2])

    sig_buy = SimpleNamespace(code="A", price="11", action="BUY")
    got = await prov.get_bar(signal=sig_buy, date_ymd="20220109", side="BUY", execution_policy="current_bar")
    assert got is b1

    sig_sell = SimpleNamespace(code="A", price="14", action="SELL")
    got2 = await prov.get_bar(signal=sig_sell, date_ymd="20220109", side="SELL", execution_policy="current_bar")
    assert got2 is b2

    # price not reached -> last bar
    sig_far = SimpleNamespace(code="A", price="1000", action="BUY")
    got3 = await prov.get_bar(signal=sig_far, date_ymd="20220109", side="BUY", execution_policy="current_bar")
    assert got3 is b2


def test_to_float_exception_and_to_int_none_and_first_valid_int_and_sorting():
    sq = Mock()
    prov = StockQueryIntradayReplayBarProvider(sq)

    class Bad:
        def __str__(self):
            raise TypeError()

    assert prov._to_float(Bad()) is None
    assert prov._to_int(Bad()) is None

    svc = StockQueryBacktestReplayService(sq)
    rows = [
        {"stck_oprc": "", "oprc": "", "open": ""},
        {"stck_oprc": "50", "stck_bsop_date": "20220110", "stck_cntg_hour": "100000"},
    ]
    assert svc._first_valid_int(rows, "stck_oprc", "oprc", "open") == 50

    # sorting check
    unsorted = [
        {"stck_bsop_date": "20220111", "stck_cntg_hour": "100500"},
        {"stck_bsop_date": "20220111", "stck_cntg_hour": "090000"},
    ]
    sq.get_day_intraday_minutes_list = AsyncMock(return_value=unsorted)
    svc.set_backtest_date("20220111")
    import asyncio
    rows_out = asyncio.get_event_loop().run_until_complete(svc._get_intraday_rows("X", "20220111"))
    assert rows_out[0]["stck_cntg_hour"] == "090000"
