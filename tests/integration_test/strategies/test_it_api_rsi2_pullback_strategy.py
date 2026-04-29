import pytest
import uuid
from unittest.mock import MagicMock, AsyncMock
from datetime import datetime, timedelta
from pytz import timezone

from strategies.rsi2_pullback_strategy import RSI2PullbackStrategy
from strategies.rsi2_pullback_types import RSI2PullbackConfig
from strategies.oneil_common_types import OSBWatchlistItem
from services.oneil_universe_service import OneilUniverseService
from common.types import ResCommonResponse


def _make_rsi2_oversold_ohlcv(code, days=30, base_close=10000):
    """RSI(2) ≤ 10 이 되도록 마지막 2일을 강한 음봉으로 만든 30일 OHLCV.

    IndicatorService._to_dataframe 가 plain dict (date/open/high/low/close/volume)을
    기대하므로 그 포맷으로 반환한다.
    """
    base_dt = datetime(2026, 3, 7)
    rows = []
    for i in range(days):
        dt = base_dt - timedelta(days=days - 1 - i)
        date_str = dt.strftime("%Y%m%d")
        if i < days - 2:
            # 강한 우상향 (Stage 2)
            price = int(base_close * (1.0 + 0.005 * i))
            vol = 500000
        else:
            # 마지막 2영업일 큰 음봉 (-3%) → RSI(2)가 한 자릿수까지 하락
            prev = rows[-1]["close"]
            price = int(prev * 0.97)
            vol = 900000
        rows.append({
            "date": date_str,
            "open": price - 50, "high": price + 100,
            "low": price - 100, "close": price, "volume": vol,
        })
    return rows


@pytest.mark.asyncio
async def test_rsi2_scan_emits_buy_when_stage2_and_rsi_oversold(deep_paper_ctx, mocker):
    """Stage 2 우량주가 RSI(2) ≤ 10에 진입하면 15:10 이후 BUY 시그널 발행."""
    uid = uuid.uuid4().int
    code_a = str(uid % 1000000).zfill(6)

    # 캐시 TTL 만료 방지
    mocker.patch("time.time", return_value=1600000000.0)

    # IndicatorService는 OHLCV → RSI/MA 계산을 자체적으로 수행하므로
    # 서비스 레벨에서 OHLCV만 mock하면 RSI(2)와 MA가 자연스럽게 산출된다.
    ohlcv = _make_rsi2_oversold_ohlcv(code_a)
    last_close = ohlcv[-1]["close"]

    mock_ohlcv_resp = MagicMock()
    mock_ohlcv_resp.rt_cd = "0"
    mock_ohlcv_resp.data = ohlcv

    mocker.patch.object(
        deep_paper_ctx.stock_query_service, "get_ohlcv",
        new_callable=AsyncMock, return_value=mock_ohlcv_resp,
    )

    # 현재가: 마지막 종가 그대로
    mock_price_resp = MagicMock()
    mock_price_resp.rt_cd = "0"
    mock_price_resp.data = {"output": {
        "stck_prpr": str(last_close),
        "stck_oprc": str(last_close),
        "stck_hgpr": str(last_close + 100),
        "stck_lwpr": str(last_close - 100),
        "stck_prdy_clpr": str(int(last_close / 0.97)),
        "prdy_vrss": "-300",
        "prdy_vrss_sign": "5",
        "acml_vol": "900000",
        "acml_tr_pbmn": "500000000000",
    }}
    mocker.patch.object(
        deep_paper_ctx.stock_query_service, "get_current_price",
        new_callable=AsyncMock, return_value=mock_price_resp,
    )

    # universe 모킹: Stage 2 등급의 단일 종목 반환
    mock_universe = MagicMock(spec=OneilUniverseService)
    watchlist_item = OSBWatchlistItem(
        code=code_a, name="테스트종목RSI2", market="KOSPI",
        high_20d=int(last_close * 1.5), ma_20d=float(last_close * 1.05),
        ma_50d=float(last_close * 1.02), avg_vol_20d=600000.0,
        bb_width_min_20d=0.03, prev_bb_width=0.04,
        w52_hgpr=int(last_close * 1.6), avg_trading_value_5d=50_000_000_000,
        market_cap=400_000_000_000,
        ma_200d=float(last_close * 0.85),  # 현재가 > 200MA (Stage 2 가정 유지)
        minervini_stage=2,
    )
    mock_universe.get_watchlist = AsyncMock(return_value={code_a: watchlist_item})
    mock_universe.is_market_timing_ok = AsyncMock(return_value=True)

    # 시간 모킹: 15:15 (entry cutoff 15:10 통과)
    kst = timezone("Asia/Seoul")
    mock_tm = MagicMock()
    mock_tm.get_current_kst_time.return_value = datetime(2026, 3, 9, 15, 15, tzinfo=kst)
    mock_tm.get_market_open_time.return_value = datetime(2026, 3, 9, 9, 0, tzinfo=kst)
    mock_tm.get_market_close_time.return_value = datetime(2026, 3, 9, 15, 30, tzinfo=kst)

    strategy = RSI2PullbackStrategy(
        stock_query_service=deep_paper_ctx.stock_query_service,
        universe_service=mock_universe,
        indicator_service=deep_paper_ctx.indicator_service,
        market_clock=mock_tm,
        config=RSI2PullbackConfig(),
    )
    strategy._save_state = MagicMock()  # 디스크 쓰기 차단

    signals = await strategy.scan()

    assert len(signals) == 1
    sig = signals[0]
    assert sig.code == code_a
    assert sig.action == "BUY"
    assert sig.strategy_name == "RSI2눌림목"
    assert "RSI" in sig.reason


@pytest.mark.asyncio
async def test_rsi2_scan_no_signal_before_cutoff_time(deep_paper_ctx, mocker):
    """15:10 이전에는 종가 베팅 트리거를 평가하지 않아 시그널 0건."""
    uid = uuid.uuid4().int
    code_a = str(uid % 1000000).zfill(6)

    mocker.patch("time.time", return_value=1600000000.0)

    mock_universe = MagicMock(spec=OneilUniverseService)
    watchlist_item = OSBWatchlistItem(
        code=code_a, name="테스트종목RSI2", market="KOSPI",
        high_20d=12000, ma_20d=10500.0, ma_50d=10000.0, avg_vol_20d=600000.0,
        bb_width_min_20d=0.03, prev_bb_width=0.04,
        w52_hgpr=15000, avg_trading_value_5d=50_000_000_000,
        market_cap=400_000_000_000,
        ma_200d=9500.0, minervini_stage=2,
    )
    mock_universe.get_watchlist = AsyncMock(return_value={code_a: watchlist_item})
    mock_universe.is_market_timing_ok = AsyncMock(return_value=True)

    kst = timezone("Asia/Seoul")
    mock_tm = MagicMock()
    # 14:00 → cutoff(15:10) 이전
    mock_tm.get_current_kst_time.return_value = datetime(2026, 3, 9, 14, 0, tzinfo=kst)
    mock_tm.get_market_open_time.return_value = datetime(2026, 3, 9, 9, 0, tzinfo=kst)
    mock_tm.get_market_close_time.return_value = datetime(2026, 3, 9, 15, 30, tzinfo=kst)

    strategy = RSI2PullbackStrategy(
        stock_query_service=deep_paper_ctx.stock_query_service,
        universe_service=mock_universe,
        indicator_service=deep_paper_ctx.indicator_service,
        market_clock=mock_tm,
        config=RSI2PullbackConfig(),
    )
    strategy._save_state = MagicMock()

    signals = await strategy.scan()
    assert signals == []
    # cutoff 이전이면 watchlist 자체를 조회하지 않아야 함
    mock_universe.get_watchlist.assert_not_called()


@pytest.mark.asyncio
async def test_rsi2_scan_skips_when_not_stage2(deep_paper_ctx, mocker):
    """Stage 2가 아니면 RSI 조건과 무관하게 시그널 0건."""
    uid = uuid.uuid4().int
    code_a = str(uid % 1000000).zfill(6)

    mocker.patch("time.time", return_value=1600000000.0)

    mock_universe = MagicMock(spec=OneilUniverseService)
    watchlist_item = OSBWatchlistItem(
        code=code_a, name="테스트종목RSI2", market="KOSPI",
        high_20d=12000, ma_20d=10500.0, ma_50d=10000.0, avg_vol_20d=600000.0,
        bb_width_min_20d=0.03, prev_bb_width=0.04,
        w52_hgpr=15000, avg_trading_value_5d=50_000_000_000,
        market_cap=400_000_000_000,
        ma_200d=11000.0,        # 현재가 < 200MA
        minervini_stage=4,      # Stage 4 (하락)
    )
    mock_universe.get_watchlist = AsyncMock(return_value={code_a: watchlist_item})
    mock_universe.is_market_timing_ok = AsyncMock(return_value=True)

    kst = timezone("Asia/Seoul")
    mock_tm = MagicMock()
    mock_tm.get_current_kst_time.return_value = datetime(2026, 3, 9, 15, 15, tzinfo=kst)
    mock_tm.get_market_open_time.return_value = datetime(2026, 3, 9, 9, 0, tzinfo=kst)
    mock_tm.get_market_close_time.return_value = datetime(2026, 3, 9, 15, 30, tzinfo=kst)

    strategy = RSI2PullbackStrategy(
        stock_query_service=deep_paper_ctx.stock_query_service,
        universe_service=mock_universe,
        indicator_service=deep_paper_ctx.indicator_service,
        market_clock=mock_tm,
        config=RSI2PullbackConfig(),
    )
    strategy._save_state = MagicMock()

    # IndicatorService 호출이 발생하지 않도록 Stage 가드가 먼저 동작해야 함
    rsi_spy = mocker.spy(deep_paper_ctx.indicator_service, "get_rsi")

    signals = await strategy.scan()
    assert signals == []
    rsi_spy.assert_not_called()
