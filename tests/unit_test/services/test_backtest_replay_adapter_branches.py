"""backtest_replay_adapter 의 분기/헬퍼 경로 커버리지 보강 테스트."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from common.types import ErrorCode, ResCommonResponse, TradeSignal
from services.backtest_execution_simulator import BacktestBar
from services.data_quality_service import DataQualityService
from services.backtest_replay_adapter import (
    StockQueryBacktestReplayService,
    StockQueryIntradayReplayBarProvider,
    StockQueryDailyMtmBarProvider,
)

SESSION = "REGULAR"


def _replay(sqs=None, market_clock=None):
    svc = StockQueryBacktestReplayService(sqs or AsyncMock(), market_clock=market_clock)
    svc.set_backtest_date("20260505")
    return svc


# --- 정적 헬퍼 ---

def test_response_has_rows_non_rescommon():
    assert StockQueryBacktestReplayService._response_has_rows([1]) is True
    assert StockQueryBacktestReplayService._response_has_rows(None) is False


def test_to_int_edge_cases():
    assert StockQueryBacktestReplayService._to_int("abc") is None
    assert StockQueryBacktestReplayService._to_int(None) is None
    assert StockQueryBacktestReplayService._to_int("1,234") == 1234


def test_require_date_raises_when_unset():
    svc = StockQueryBacktestReplayService(AsyncMock())
    with pytest.raises(ValueError, match="backtest date is not set"):
        svc._require_date()


def test_cutoff_hhmmss_handles_clock_error():
    clock = MagicMock()
    clock.get_current_kst_time.side_effect = RuntimeError("clock down")
    svc = _replay(market_clock=clock)
    assert svc._cutoff_hhmmss() == ""


def test_getattr_delegates_to_underlying_service():
    sqs = MagicMock()
    sqs.some_custom_attr = "delegated"
    svc = _replay(sqs=sqs)
    assert svc.some_custom_attr == "delegated"


# --- get_market_snapshot ---

def test_market_snapshot_force_fresh_returns_missing():
    svc = _replay()
    snap, reason = svc.get_market_snapshot("005930", force_fresh=True)
    assert snap is None
    assert reason == DataQualityService.REASON_SNAPSHOT_MISSING


def test_market_snapshot_cache_miss_returns_missing():
    svc = _replay()
    snap, reason = svc.get_market_snapshot("005930")
    assert snap is None
    assert reason == DataQualityService.REASON_SNAPSHOT_MISSING


def test_market_snapshot_cache_hit_builds_snapshot():
    svc = _replay()  # market_clock None → cutoff ""
    key = ("005930", "20260505", SESSION, "")
    svc._row_cache[key] = [
        {"stck_prpr": "100", "stck_hgpr": "110", "stck_lwpr": "90",
         "acml_vol": "1000", "acml_tr_pbmn": "5000"},
    ]
    snap, reason = svc.get_market_snapshot("005930")
    assert reason is None
    assert snap.price == 100.0
    assert snap.high == 110.0
    assert snap.low == 90.0
    assert snap.acml_vol == 1000
    assert snap.source == "backtest_replay"


# --- get_conclusion_snapshot ---

@pytest.mark.asyncio
async def test_conclusion_snapshot_no_date_returns_missing():
    svc = StockQueryBacktestReplayService(AsyncMock())
    snap, reason = await svc.get_conclusion_snapshot("005930")
    assert snap is None
    assert reason == DataQualityService.REASON_CONCLUSION_MISSING


@pytest.mark.asyncio
async def test_conclusion_snapshot_from_cache():
    svc = _replay()
    key = ("005930", "20260505", SESSION, "")
    svc._row_cache[key] = [{"tday_rltv": "150.5"}]
    snap, reason = await svc.get_conclusion_snapshot("005930")
    assert reason is None
    assert snap.execution_strength_pct == 150.5


@pytest.mark.asyncio
async def test_conclusion_snapshot_from_cache_invalid_strength_defaults_zero():
    svc = _replay()
    key = ("005930", "20260505", SESSION, "")
    svc._row_cache[key] = [{"tday_rltv": "abc"}]
    snap, _ = await svc.get_conclusion_snapshot("005930")
    assert snap.execution_strength_pct == 0.0


@pytest.mark.asyncio
async def test_conclusion_snapshot_fallback_to_get_stock_conclusion():
    sqs = AsyncMock()
    sqs.get_day_intraday_minutes_list.return_value = [{"tday_rltv": "133.0"}]
    svc = _replay(sqs=sqs)
    snap, reason = await svc.get_conclusion_snapshot("005930")
    assert reason is None
    assert snap.execution_strength_pct == 133.0


@pytest.mark.asyncio
async def test_conclusion_snapshot_fallback_missing_returns_reason():
    sqs = AsyncMock()
    sqs.get_day_intraday_minutes_list.return_value = []  # 체결강도 없음 → 실패 응답
    svc = _replay(sqs=sqs)
    snap, reason = await svc.get_conclusion_snapshot("005930")
    assert snap is None
    assert reason == DataQualityService.REASON_CONCLUSION_MISSING


# --- StockQueryIntradayReplayBarProvider ---

@pytest.mark.asyncio
async def test_intraday_bar_provider_raises_when_rows_not_sequence():
    sqs = AsyncMock()
    sqs.get_day_intraday_minutes_list.return_value = None  # 비-Sequence → []
    provider = StockQueryIntradayReplayBarProvider(sqs)
    signal = TradeSignal(code="005930", name="t", action="BUY", price=100, qty=1,
                         reason="r", strategy_name="s")
    with pytest.raises(ValueError, match="intraday rows not found"):
        await provider.get_bar(signal=signal, date_ymd="20260505", side="BUY")


def test_intraday_price_reached_fallback_side():
    bar = BacktestBar(timestamp="20260505 090000", open=95, high=110, low=90, close=100, volume=1)
    # side 가 BUY/SELL 이 아니어도 범위 검사로 폴백
    assert StockQueryIntradayReplayBarProvider._price_reached(bar, 100.0, "OTHER") is True
    assert StockQueryIntradayReplayBarProvider._price_reached(bar, 0.0, "OTHER") is False


# --- StockQueryDailyMtmBarProvider ---

@pytest.mark.asyncio
async def test_daily_provider_returns_empty_when_start_after_end():
    provider = StockQueryDailyMtmBarProvider(AsyncMock())
    assert await provider.get_holding_bars(code="005930", start_ymd="20260510", end_ymd="20260505") == []


@pytest.mark.asyncio
async def test_daily_provider_uses_cache_and_handles_non_sequence_rows():
    sqs = AsyncMock()
    sqs.get_recent_daily_ohlcv.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="ok", data=None  # 비-Sequence → []
    )
    provider = StockQueryDailyMtmBarProvider(sqs)
    first = await provider.get_holding_bars(code="005930", start_ymd="20260501", end_ymd="20260510")
    second = await provider.get_holding_bars(code="005930", start_ymd="20260501", end_ymd="20260510")
    assert first == [] and second == []
    # 캐시 적중으로 한 번만 조회
    sqs.get_recent_daily_ohlcv.assert_awaited_once()


def test_daily_provider_lookup_limit_handles_invalid_date():
    provider = StockQueryDailyMtmBarProvider(AsyncMock(), lookback_padding_days=10)
    assert provider._lookup_limit("bad", "alsobad") == 70


def test_daily_provider_row_to_bar_none_branches():
    provider = StockQueryDailyMtmBarProvider(AsyncMock())
    assert provider._row_to_bar("not-a-dict") is None       # 비-dict
    assert provider._row_to_bar({"open": "1"}) is None        # close 없음
    assert provider._row_to_bar({"close": "100"}) is None     # date 없음
    bar = provider._row_to_bar({"close": "100", "date": "20260505"})
    assert bar.close == 100.0


def test_daily_provider_first_and_to_float_helpers():
    assert StockQueryDailyMtmBarProvider._first({"a": "", "b": "-"}, "a", "b") is None
    assert StockQueryDailyMtmBarProvider._to_float(None) is None
    assert StockQueryDailyMtmBarProvider._to_float("abc") is None
    assert StockQueryDailyMtmBarProvider._to_float("1,234.5") == 1234.5
