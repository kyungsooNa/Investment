import pytest
import uuid
from unittest.mock import MagicMock, AsyncMock
from datetime import datetime
from pytz import timezone

from strategies.oneil_squeeze_breakout_strategy import OneilSqueezeBreakoutStrategy
from strategies.oneil_common_types import OneilBreakoutConfig, OSBWatchlistItem, OneilUniverseConfig
from services.oneil_universe_service import OneilUniverseService
from services.indicator_service import IndicatorService
from repositories.stock_code_repository import StockCodeRepository
from common.types import ResCommonResponse

@pytest.mark.asyncio
async def test_osb_scan_cache_behavior_reduces_api_calls(deep_paper_ctx, mocker):
    """전략 스캔 시, Memory Cache Hit 여부에 따라 실제 브로커 API 호출이 어떻게 감소하는지 검증."""

    uid = uuid.uuid4().int
    code_a = str(uid % 1000000).zfill(6)
    code_b = str((uid // 1000000) % 1000000).zfill(6)
    
    # 테스트 실행 환경이 느려 캐시 TTL이 만료되는 것을 방지하기 위해 시간 고정
    mocker.patch("time.time", return_value=1600000000.0)

    # 1. 대상 추출 (Broker 계층 및 캐시 저장소)
    md_service = deep_paper_ctx.stock_query_service.market_data_service
    broker = md_service._broker_api_wrapper
    stock_repo = md_service._stock_repo

    # 2. Broker API 모킹 (현재가 조회: 20일 고가 돌파, 거래량/프로그램 매수 충족)
    mock_get_price = mocker.patch.object(
        broker, "get_current_price",
        new_callable=AsyncMock,
        return_value=ResCommonResponse(rt_cd="0", msg1="ok", data={
            "output": {
                "stck_prpr": "75500",  # 캔들 품질 검증(0.7 이상)을 통과하기 위해 75000 -> 75500으로 상향 (상대 위치 0.75)
                "stck_oprc": "74000",
                "stck_hgpr": "76000",
                "stck_lwpr": "74000",
                "prdy_vrss": "1000",
                "prdy_vrss_sign": "2",
                "acml_vol": "3000000",
                "pgtr_ntby_qty": "100000",
                "acml_tr_pbmn": "500000000000",
                "hts_avls": "10000",
                "stck_llam": "10000"
            }
        })
    )

    # 체결강도 모킹 (120% 이상)
    mock_get_conclusion = mocker.patch.object(
        broker, "get_stock_conclusion",
        new_callable=AsyncMock,
        return_value=ResCommonResponse(rt_cd="0", msg1="ok", data={"output": [{"tday_rltv": "135.00"}]})
    )

    # 3. 실시간 급등주 랭킹 API 모킹 (_build_daily_surge_pool 용)
    mock_sqs = deep_paper_ctx.stock_query_service
    mocker.patch.object(mock_sqs, 'get_top_trading_value_stocks', new_callable=AsyncMock, 
                        return_value=ResCommonResponse(rt_cd="0", msg1="ok", data=[{"mksc_shrn_iscd": code_b, "hts_kor_isnm": "테스트종목B"}]))
    mocker.patch.object(mock_sqs, 'get_top_rise_fall_stocks', new_callable=AsyncMock, return_value=ResCommonResponse(rt_cd="0", msg1="ok", data=[]))
    mocker.patch.object(mock_sqs, 'get_top_volume_stocks', new_callable=AsyncMock, return_value=ResCommonResponse(rt_cd="0", msg1="ok", data=[]))

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
        config=OneilUniverseConfig()
    )

    # _load_premium_stocks는 테스트종목A 1개를 반환하도록 모킹 (Pool A)
    premium_item = OSBWatchlistItem(
        code=code_a, name="테스트종목A", market="KOSPI",
        high_20d=75000, ma_20d=70000.0, ma_50d=68000.0,  # current=75500, max_entry=75000*1.02=76500 (within 2%)
        avg_vol_20d=600000.0, bb_width_min_20d=0.03, prev_bb_width=0.035,  # squeeze gate: 0.035 <= 0.03*1.2
        w52_hgpr=77000, avg_trading_value_5d=50000000000, market_cap=400_000_000_000_000,
        rs_return_3m=10.0, profit_growth_score=0, smart_money_score=0, rs_score=0, total_score=0
    )
    mocker.patch.object(universe_service, '_load_premium_stocks', return_value=[premium_item])
    mocker.patch.object(universe_service, 'is_market_timing_ok', new_callable=AsyncMock, return_value=True)

    strategy = OneilSqueezeBreakoutStrategy(
        stock_query_service=mock_sqs,
        universe_service=universe_service,
        market_clock=mock_tm,
        config=OneilBreakoutConfig(
            program_net_buy_min=0,
            program_to_trade_value_pct=0.0,
            program_to_market_cap_pct=0.0,
        )
    )

    # [상황 A] 완전 초기화 상태 (Cache Miss)
    stock_repo._price_repo._price_cache.clear()
    
    await strategy.scan()
    
    calls_price_on_miss = mock_get_price.call_count
    calls_conclusion_on_miss = mock_get_conclusion.call_count
    
    assert calls_price_on_miss >= 1, "초기 스캔 시 현재가 API가 호출되어야 함"
    assert calls_conclusion_on_miss >= 1, "초기 스캔 시 체결강도 API가 호출되어야 함"

    # [상황 B] 연속 실행 (Memory Cache Hit)
    mock_get_price.reset_mock()
    mock_get_conclusion.reset_mock()
    strategy._position_state.clear()
    
    await strategy.scan()
    
    assert mock_get_price.call_count == 0, "캐시 적중 시 현재가 외부 API는 호출되지 않아야 함"
    assert mock_get_conclusion.call_count == calls_conclusion_on_miss, "체결강도 실시간 API는 항상 호출되어야 함"
    assert deep_paper_ctx.stock_query_service.price_stream_service.get_cached_price(code_a) is not None, \
        "브로커 응답이 price_stream_service에 backfill 되어야 함"