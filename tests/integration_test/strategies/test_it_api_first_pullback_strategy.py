import pytest
from unittest.mock import MagicMock, AsyncMock
from datetime import datetime
from pytz import timezone

from strategies.first_pullback_strategy import FirstPullbackStrategy
from strategies.first_pullback_types import FirstPullbackConfig
from strategies.oneil_common_types import OSBWatchlistItem, OneilUniverseConfig
from services.oneil_universe_service import OneilUniverseService
from services.indicator_service import IndicatorService
from repositories.stock_code_repository import StockCodeRepository
from common.types import ResCommonResponse

@pytest.mark.asyncio
async def test_fp_scan_cache_behavior_reduces_api_calls(deep_paper_ctx, mocker):
    """전략 스캔 시, Memory Cache Hit 여부에 따라 실제 브로커 API 호출이 어떻게 감소하는지 검증."""
    
    # 테스트 실행 환경이 느려 캐시 TTL이 만료되는 것을 방지하기 위해 시간 고정
    mocker.patch("time.time", return_value=1600000000.0)

    # 1. 대상 추출 (Broker 계층 및 캐시 저장소)
    md_service = deep_paper_ctx.stock_query_service.market_data_service
    broker = md_service._broker_api_wrapper
    stock_repo = md_service._stock_repo

    # 2. Broker API 모킹 (현재가 조회: 양봉 전환 등 Trigger 조건 통과 세팅)
    mock_get_price = mocker.patch.object(
        broker, "get_current_price",
        new_callable=AsyncMock,
        return_value=ResCommonResponse(rt_cd="0", msg1="ok", data={
            "output": {
                "stck_prpr": "96000",
                "stck_oprc": "94000",
                "stck_lwpr": "94000",
                "prdy_vrss": "2000",
                "prdy_vrss_sign": "2",
            }
        })
    )
    
    # 30일치 OHLCV 데이터 세팅 (최근 20일 내 30% 급등 후 거래량 고갈 지지 연출)
    dummy_ohlcv = []
    from datetime import timedelta
    base_dt = datetime(2026, 3, 7)
    for i in range(30):
        dt = base_dt - timedelta(days=29 - i)
        date_str = dt.strftime("%Y%m%d")
        if i == 15: # 15일 전에 상한가(30% 급등)
            price = 100000
            vol = 5000000
        elif i > 15:
            price = 100000 - (i - 15) * 500 # 서서히 하락
            vol = 100000 # 거래량 고갈
        else:
            price = 70000 + i * 500
            vol = 500000
            
        dummy_ohlcv.append({
            "stck_bsop_date": date_str,
            "stck_clpr": str(price), "close": price,
            "stck_hgpr": str(price + 500), "high": price + 500,
            "stck_lwpr": str(price - 500), "low": price - 500,
            "stck_oprc": str(price), "open": price,
            "acml_vol": str(vol), "volume": vol
        })
        
    # 상한가 검증을 위해 14일차 15일차 종가 비율 조작
    dummy_ohlcv[14]['close'] = 77000
    dummy_ohlcv[14]['stck_clpr'] = '77000'
    dummy_ohlcv[15]['close'] = 100100 # 30% 이상 급등
    dummy_ohlcv[15]['stck_clpr'] = '100100'

    mock_get_ohlcv = mocker.patch.object(
        broker, "inquire_daily_itemchartprice",
        new_callable=AsyncMock,
        return_value=ResCommonResponse(rt_cd="0", msg1="ok", data=dummy_ohlcv)
    )
    
    # 체결강도 모킹
    mock_get_conclusion = mocker.patch.object(
        broker, "get_stock_conclusion",
        new_callable=AsyncMock,
        return_value=ResCommonResponse(rt_cd="0", msg1="ok", data={"output": [{"tday_rltv": "150.00"}]})
    )

    # 3. 워치리스트 빌드용 API 모킹
    mock_sqs = deep_paper_ctx.stock_query_service
    mocker.patch.object(mock_sqs, 'get_top_trading_value_stocks', new_callable=AsyncMock, 
                        return_value=ResCommonResponse(rt_cd="0", msg1="ok", data=[{"mksc_shrn_iscd": "999991", "hts_kor_isnm": "테스트종목A"}]))
    mocker.patch.object(mock_sqs, 'get_top_rise_fall_stocks', new_callable=AsyncMock, return_value=ResCommonResponse(rt_cd="0", msg1="ok", data=[]))
    mocker.patch.object(mock_sqs, 'get_top_volume_stocks', new_callable=AsyncMock, return_value=ResCommonResponse(rt_cd="0", msg1="ok", data=[]))

    # 4. 시간 및 환경 모킹
    kst = timezone("Asia/Seoul")
    mock_tm = MagicMock()
    mock_tm.get_current_kst_time.return_value = datetime(2026, 3, 8, 10, 0, tzinfo=kst)
    mock_tm.get_market_open_time.return_value = datetime(2026, 3, 8, 9, 0, tzinfo=kst)
    mock_tm.get_market_close_time.return_value = datetime(2026, 3, 8, 15, 30, tzinfo=kst)

    indicator_service = IndicatorService()
    stock_code_repo = MagicMock(spec=StockCodeRepository)
    stock_code_repo.is_kosdaq.return_value = False
    
    universe_service = OneilUniverseService(
        stock_query_service=mock_sqs,
        indicator_service=indicator_service,
        stock_code_repository=stock_code_repo,
        market_clock=mock_tm,
        config=OneilUniverseConfig()
    )

    premium_item = OSBWatchlistItem(
        code="999991", name="테스트종목A", market="KOSPI",
        high_20d=100100, ma_20d=95000.0, ma_50d=85000.0, 
        avg_vol_20d=600000.0, bb_width_min_20d=0.03, prev_bb_width=0.04, 
        w52_hgpr=100100, avg_trading_value_5d=50000000000, market_cap=400_000_000_000_000,
        rs_return_3m=10.0, profit_growth_score=0, smart_money_score=0, rs_score=0, total_score=0
    )
    mocker.patch.object(universe_service, '_load_premium_stocks', return_value=[premium_item])
    mocker.patch.object(universe_service, 'is_market_timing_ok', new_callable=AsyncMock, return_value=True)

    strategy = FirstPullbackStrategy(
        stock_query_service=mock_sqs, universe_service=universe_service,
        market_clock=mock_tm, config=FirstPullbackConfig(execution_strength_min=120.0)
    )
    
    # ma_uptrend 확인은 복잡한 데이터 조작이 필요하므로 모킹으로 통과
    mocker.patch.object(strategy, '_check_ma_uptrend', return_value=True)

    # [상황 A] 초기 스캔 (Cache Miss)
    stock_repo._price_repo._price_cache.clear()
    stock_repo._ohlcv_repo._ohlcv_cache.clear()
    await strategy.scan()
    
    # [상황 B] 두 번째 스캔 (Cache Hit)
    mock_get_price.reset_mock()
    mock_get_ohlcv.reset_mock()
    await strategy.scan()
    
    assert mock_get_price.call_count == 0, "캐시 적중 시 현재가 API는 호출되지 않아야 함"
    assert mock_get_ohlcv.call_count == 0, "캐시 적중 시 OHLCV API는 호출되지 않아야 함"