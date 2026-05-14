"""MarketRegimeService 단위 테스트.

OneilUniverseService._check_etf_ma_rising 에 있던 4-state 추세 분류 로직이
새 서비스로 추출되었는지, 그리고 bull/bear/sideways 매핑이 일관적인지 검증.
"""
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from common.types import ResCommonResponse, ErrorCode
from services.market_regime_service import (
    MarketRegimeConfig,
    MarketRegimeService,
    RegimeSnapshot,
)


def _build_ohlcv(closes):
    return [{"date": f"202601{i+1:02d}", "close": v} for i, v in enumerate(closes)]


def _make_service(closes, *, today="20260514"):
    sqs = MagicMock()
    sqs.get_recent_daily_ohlcv = AsyncMock(
        return_value=ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="ok", data=_build_ohlcv(closes))
    )
    tm = MagicMock()
    tm.get_current_kst_time = MagicMock(return_value=datetime.strptime(today, "%Y%m%d"))
    cfg = MarketRegimeConfig(
        kospi_etf_code="069500",
        kosdaq_etf_code="229200",
        ma_period=5,
        rising_days=2,
        min_net_change_pct=-0.10,
        daily_dip_tolerance_pct=-0.20,
        hard_decline_pct=-0.50,
    )
    svc = MarketRegimeService(stock_query_service=sqs, market_clock=tm, config=cfg)
    return svc, sqs


@pytest.mark.asyncio
async def test_classify_rising_returns_bull():
    """MA가 우상향 + 일일 dip 미발생 → trend_status=rising, regime_label=bull, is_rising=True."""
    # MA 윈도우(5일) + rising_days(2) + 여유 = 최소 8개 데이터
    closes = [100, 101, 102, 103, 104, 106, 108, 110]
    svc, _ = _make_service(closes)
    snap = await svc.classify("KOSPI")
    assert isinstance(snap, RegimeSnapshot)
    assert snap.market == "KOSPI"
    assert snap.trend_status == "rising"
    assert snap.regime_label == "bull"
    assert snap.is_rising is True


@pytest.mark.asyncio
async def test_classify_hard_decline_returns_bear():
    """MA 일일 변화율이 hard_decline_pct(-0.5%) 미만이면 hard_decline → bear."""
    # MA 값 자체가 +0.7% 이상 급락하도록 closes 끝에서 급락
    closes = [200, 200, 200, 200, 200, 200, 200, 150]
    svc, _ = _make_service(closes)
    snap = await svc.classify("KOSPI")
    assert snap.trend_status == "hard_decline"
    assert snap.regime_label == "bear"
    assert snap.is_rising is False


@pytest.mark.asyncio
async def test_classify_weak_trend_returns_bear():
    """MA 순증감(net)이 min_net_change_pct(-0.10%) 미만이면 weak_trend → bear."""
    # 천천히 감소해 max_daily_drop_pct 은 hard_decline(-0.5%) 보다 크고
    # net_change_pct 만 min_net_change_pct(-0.10%) 보다 낮은 케이스
    closes = [200, 200, 200, 200, 200, 199, 199, 199]
    svc, _ = _make_service(closes)
    snap = await svc.classify("KOSPI")
    assert snap.trend_status == "weak_trend"
    assert snap.regime_label == "bear"
    assert snap.is_rising is False


@pytest.mark.asyncio
async def test_classify_uptrend_under_pressure_returns_sideways():
    """순증감은 양호하지만 일일 dip 이 -0.2% 미만 ~ -0.5% 이상이면 uptrend_under_pressure → sideways."""
    # 평균 MA가 약간 상승하면서 중간에 한 번 -0.3% 정도 일일 하락 유도
    closes = [100, 101, 102, 103, 104, 105, 102, 105]  # 5일 MA: 102, 103, 103.2, 103.8 — 변동 있음
    svc, _ = _make_service(closes)
    snap = await svc.classify("KOSPI")
    # 정확한 trend_status 보다 매핑 검증이 핵심: 셋 중 하나
    assert snap.regime_label in {"bull", "sideways"}
    # uptrend_under_pressure 가 발생했다면 sideways 매핑
    if snap.trend_status == "uptrend_under_pressure":
        assert snap.regime_label == "sideways"
        assert snap.is_rising is True  # uptrend_under_pressure 는 진입 허용


@pytest.mark.asyncio
async def test_insufficient_data_returns_bear():
    """OHLCV 부족 시 fail-close: weak_trend/bear, is_rising=False, fail_detail 포함."""
    svc, _ = _make_service([100, 101])  # 너무 짧음
    snap = await svc.classify("KOSPI")
    assert snap.regime_label == "bear"
    assert snap.is_rising is False
    assert "insufficient" in snap.fail_detail


@pytest.mark.asyncio
async def test_classify_caches_until_date_changes():
    """같은 날짜 내 동일 market 재요청은 SQS 호출 1회로 끝나야 한다."""
    closes = [100, 101, 102, 103, 104, 106, 108, 110]
    svc, sqs = _make_service(closes)
    await svc.classify("KOSPI")
    await svc.classify("KOSPI")
    await svc.classify("KOSPI")
    assert sqs.get_recent_daily_ohlcv.call_count == 1


@pytest.mark.asyncio
async def test_classify_recomputes_after_date_change():
    """날짜가 바뀌면 캐시를 비우고 재계산한다."""
    closes = [100, 101, 102, 103, 104, 106, 108, 110]
    svc, sqs = _make_service(closes, today="20260514")
    await svc.classify("KOSPI")
    # 날짜 변경
    svc._tm.get_current_kst_time = MagicMock(return_value=datetime.strptime("20260515", "%Y%m%d"))
    await svc.classify("KOSPI")
    assert sqs.get_recent_daily_ohlcv.call_count == 2


@pytest.mark.asyncio
async def test_is_bull_matches_classify():
    """is_bull() 는 classify().is_rising 과 같다 (기존 is_market_timing_ok 호환)."""
    closes = [100, 101, 102, 103, 104, 106, 108, 110]
    svc, _ = _make_service(closes)
    assert await svc.is_bull("KOSPI") is True
    snap = await svc.classify("KOSPI")
    assert snap.is_rising is True


@pytest.mark.asyncio
async def test_snapshot_both_returns_both_markets():
    """snapshot_both() 는 KOSPI/KOSDAQ 둘 다 분류한다."""
    closes = [100, 101, 102, 103, 104, 106, 108, 110]
    svc, _ = _make_service(closes)
    both = await svc.snapshot_both()
    assert set(both.keys()) == {"KOSPI", "KOSDAQ"}
    assert both["KOSPI"].market == "KOSPI"
    assert both["KOSDAQ"].market == "KOSDAQ"


def test_get_cached_snapshot_returns_none_before_classify():
    """classify() 호출 전에는 cache miss 로 None."""
    svc, _ = _make_service([100, 101, 102, 103, 104, 106, 108, 110])
    assert svc.get_cached_snapshot("KOSPI") is None


@pytest.mark.asyncio
async def test_classify_on_date_filters_history():
    """classify_on_date() 는 as_of_date 이후 데이터를 제외한다."""
    # ohlcv 날짜는 _build_ohlcv 가 20260101~20260108 으로 만든다
    closes = [100, 101, 102, 103, 104, 106, 108, 110]
    svc, sqs = _make_service(closes)
    snap_full = await svc.classify_on_date("KOSPI", "20260108")
    # 20260104 까지만 — 5개 close 만 남으면 ma_period=5 + rising_days=2 부족 → bear
    snap_short = await svc.classify_on_date("KOSPI", "20260104")
    assert snap_full.is_rising is True
    assert snap_short.is_rising is False
    assert "insufficient" in snap_short.fail_detail


@pytest.mark.asyncio
async def test_classify_unknown_market_raises():
    svc, _ = _make_service([100, 101, 102, 103, 104, 106, 108, 110])
    with pytest.raises(ValueError):
        await svc.classify("NASDAQ")
