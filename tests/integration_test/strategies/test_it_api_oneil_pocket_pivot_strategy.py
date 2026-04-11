import pytest
import uuid
from unittest.mock import MagicMock, AsyncMock
from datetime import datetime
from pytz import timezone

from strategies.oneil_pocket_pivot_strategy import OneilPocketPivotStrategy
from strategies.oneil_common_types import OneilPocketPivotConfig, OSBWatchlistItem, OneilUniverseConfig
from services.oneil_universe_service import OneilUniverseService
from services.indicator_service import IndicatorService
from repositories.stock_code_repository import StockCodeRepository
from common.types import ResCommonResponse

@pytest.mark.asyncio
async def test_pocket_pivot_scan_cache_behavior_reduces_api_calls(deep_paper_ctx, mocker):
    """전략 스캔 시, Memory Cache Hit 여부에 따라 실제 브로커 API 호출이 어떻게 감소하는지 검증."""
    
    # DB 잔존 데이터(이전 테스트 실행 결과)로 인한 캐시 히트를 방지하기 위해 매 실행마다 고유한 6자리 종목 코드 생성
    uid = uuid.uuid4().int
    code_a = str(uid % 1000000).zfill(6)
    code_b = str((uid // 1000000) % 1000000).zfill(6)
    
    # 테스트 실행 환경이 느려 캐시 TTL(3초)이 만료되는 것을 방지하기 위해 시간 고정
    mocker.patch("time.time", return_value=1600000000.0)

    # 1. 대상 추출 (Broker 계층 및 캐시 저장소)
    md_service = deep_paper_ctx.stock_query_service.market_data_service
    broker = md_service._broker_api_wrapper
    stock_repo = md_service._stock_repo

    # 2. Broker API 모킹 (현재가 조회: 52주 고가, 시가총액 조건 통과를 위한 데이터 세팅)
    mock_get_price = mocker.patch.object(
        broker, "get_current_price",
        new_callable=AsyncMock,
        return_value=ResCommonResponse(rt_cd="0", msg1="ok", data={
            "output": {
                "stck_prpr": "127000",
                "w52_hgpr": "130000",  # 현재가와 근접 (이격도 15% 이내)
                "hts_avls": "10000",   # 1조 원 (2천억 ~ 20조 사이)
                "stck_llam": "10000",
                "acml_vol": "2000000",
                "pgtr_ntby_qty": "100000",
                "acml_tr_pbmn": "500000000000",
                "stck_oprc": "125000",
                "stck_hgpr": "128000",
                "stck_lwpr": "125000",
                "prdy_vrss": "2000",
                "prdy_vrss_sign": "2"
            }
        })
    )
    
    # 90일치 완벽한 정배열 및 최근 구간 박스권(BB 스퀴즈)을 연출하는 OHLCV 데이터
    dummy_ohlcv = []
    from datetime import timedelta
    base_dt = datetime(2026, 3, 7)
    for i in range(90):
        # 과거(인덱스가 작을수록) 가격이 낮고 최근이 높은 우상향 추세 생성
        dt = base_dt - timedelta(days=89 - i)
        date_str = dt.strftime("%Y%m%d")
        price = 100000 + (90 - i) * 300
        dummy_ohlcv.append({
            "stck_bsop_date": date_str,
            "stck_clpr": str(price), "close": price,
            "stck_hgpr": str(price + 500), "high": price + 500,
            "stck_lwpr": str(price - 500), "low": price - 500,
            "stck_oprc": str(price), "open": price,
            "acml_vol": "2000000", "volume": 2000000 # 거래대금 통과를 위한 풍부한 거래량
        })
        
    mock_get_ohlcv = mocker.patch.object(
        broker, "inquire_daily_itemchartprice",
        new_callable=AsyncMock,
        return_value=ResCommonResponse(rt_cd="0", msg1="ok", data=dummy_ohlcv)
    )
    
    # 체결강도 등 캐시되지 않는 API 모킹
    mock_get_conclusion = mocker.patch.object(
        broker, "get_stock_conclusion",
        new_callable=AsyncMock,
        return_value=ResCommonResponse(rt_cd="0", msg1="ok", data={"output": [{"tday_rltv": "100.00"}]})
    )

    # 3. 실시간 급등주 랭킹 API 모킹 (_build_daily_surge_pool 용)
    mock_sqs = deep_paper_ctx.stock_query_service
    mocker.patch.object(mock_sqs, 'get_top_trading_value_stocks', new_callable=AsyncMock, 
                        return_value=ResCommonResponse(rt_cd="0", msg1="ok", data=[{"mksc_shrn_iscd": code_b, "hts_kor_isnm": "테스트종목B"}]))
    mocker.patch.object(mock_sqs, 'get_top_rise_fall_stocks', new_callable=AsyncMock, 
                        return_value=ResCommonResponse(rt_cd="0", msg1="ok", data=[]))
    mocker.patch.object(mock_sqs, 'get_top_volume_stocks', new_callable=AsyncMock, 
                        return_value=ResCommonResponse(rt_cd="0", msg1="ok", data=[]))

    # 4. 시간 모킹
    kst = timezone("Asia/Seoul")
    mock_tm = MagicMock()
    mock_tm.get_current_kst_time.return_value = datetime(2026, 3, 8, 10, 0, tzinfo=kst)
    mock_tm.get_market_open_time.return_value = datetime(2026, 3, 8, 9, 0, tzinfo=kst)
    mock_tm.get_market_close_time.return_value = datetime(2026, 3, 8, 15, 30, tzinfo=kst)

    # 5. OneilUniverseService 구성 (실제 객체 생성)
    indicator_service = IndicatorService()
    stock_code_repo = MagicMock(spec=StockCodeRepository)
    stock_code_repo.is_kosdaq.return_value = False
    
    universe_service = OneilUniverseService(
        stock_query_service=mock_sqs,
        indicator_service=indicator_service,
        stock_code_repository=stock_code_repo,
        market_clock=mock_tm,
        config=OneilUniverseConfig(
            premium_stocks_cap_min=200_0000_0000, 
            premium_stocks_cap_max=20_0000_0000_0000, 
            near_52w_high_pct=15.0,
            squeeze_tolerance=1.5,
            daily_surge_size=10
        )
    )

    # _load_premium_stocks는 테스트종목A 1개를 반환하도록 모킹 (Pool A)
    premium_item = OSBWatchlistItem(
        code=code_a, name="테스트종목A", market="KOSPI",
        high_20d=72000, ma_20d=70000.0, ma_50d=68000.0, 
        avg_vol_20d=600000.0, bb_width_min_20d=0.03, prev_bb_width=0.04, 
        w52_hgpr=77000, avg_trading_value_5d=50000000000, market_cap=400_000_000_000_000,
        rs_return_3m=10.0, profit_growth_score=0, smart_money_score=0, rs_score=0, total_score=0
    )
    mocker.patch.object(universe_service, '_load_premium_stocks', return_value=[premium_item])
    mocker.patch.object(universe_service, 'is_market_timing_ok', new_callable=AsyncMock, return_value=True)

    strategy = OneilPocketPivotStrategy(
        stock_query_service=mock_sqs,
        universe_service=universe_service,
        market_clock=mock_tm,
        config=OneilPocketPivotConfig()
    )

    # entry 조건을 우회하여 체결강도 조회가 발생하도록 모킹
    mocker.patch.object(strategy, "_check_pocket_pivot", return_value=("PP", "20", 0, {"proj_vol": 100, "max_down_vol": 50}))
    mocker.patch.object(strategy, "_check_smart_money", return_value=True)

    # ==========================================================
    # [상황 A] 완전 초기화 상태 (Cache Miss)
    # ==========================================================
    stock_repo._price_repo._price_cache.clear()
    stock_repo._ohlcv_repo._ohlcv_cache.clear()
    
    await strategy.scan()
    
    calls_price_on_miss = mock_get_price.call_count
    calls_ohlcv_on_miss = mock_get_ohlcv.call_count
    calls_conclusion_on_miss = mock_get_conclusion.call_count
    
    # 테스트종목A(Pool A) + 테스트종목B(Pool B) 두 종목이 모두 검증을 통과해야 함
    assert calls_price_on_miss >= 2, f"초기 스캔 시 현재가 API가 호출되어야 함 ({calls_price_on_miss})"
    assert calls_ohlcv_on_miss >= 2, f"초기 스캔 시 일봉 API가 호출되어야 함 ({calls_ohlcv_on_miss})"

    # ==========================================================
    # [상황 B] 연속 실행 (Memory Cache Hit)
    # ==========================================================
    mock_get_price.reset_mock()
    mock_get_ohlcv.reset_mock()
    mock_get_conclusion.reset_mock()
    
    # 메모리에 적재된 상태에서 다시 스캔
    strategy._position_state.clear()
    await strategy.scan()
    
    # [검증 1] 캐시를 타는 현재가 API는 호출되지 않아야 함
    assert mock_get_price.call_count == 0, "캐시 적중 시 현재가 외부 API는 호출되지 않아야 합니다."
    
    # [검증 2] end_date가 명시되어도 캐시에 충분한 데이터가 있다면 외부 API는 호출되지 않아야 함
    assert mock_get_ohlcv.call_count == 0, "캐시 적중 시 OHLCV 외부 API는 호출되지 않아야 합니다."
    
    # [검증 3] 실시간 API(체결강도)는 항상 호출되어야 함
    assert mock_get_conclusion.call_count == calls_conclusion_on_miss, "캐시 대상이 아닌 체결강도 API는 항상 호출되어야 합니다."

    # 저장소 내부 통계값 검증
    stats = stock_repo.get_cache_stats()
    assert stats["hits"] > 0, "캐시 히트 카운트가 정상적으로 누적되어야 합니다."