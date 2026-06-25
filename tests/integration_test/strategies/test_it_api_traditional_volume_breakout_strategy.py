import pytest
import uuid
from unittest.mock import MagicMock, AsyncMock
from datetime import datetime
from pytz import timezone

from strategies.traditional_volume_breakout_strategy import TraditionalVolumeBreakoutStrategy, TraditionalVBConfig
from repositories.stock_code_repository import StockCodeRepository
from common.types import ResCommonResponse

@pytest.mark.asyncio
async def test_tvb_scan_cache_behavior_reduces_api_calls(deep_paper_ctx, mocker):
    """전략 스캔 시, Memory Cache Hit 여부에 따라 실제 브로커 API 호출이 어떻게 감소하는지 검증."""

    uid = uuid.uuid4().int
    code_a = str(uid % 1000000).zfill(6)
    
    # 테스트 실행 환경이 느려 캐시 TTL이 만료되는 것을 방지하기 위해 시간 고정
    mocker.patch("time.time", return_value=1600000000.0)

    # 1. 대상 추출 (Broker 계층 및 캐시 저장소)
    md_service = deep_paper_ctx.stock_query_service.market_data_service
    broker = md_service._broker_api_wrapper
    stock_repo = md_service._stock_repo
    mocker.patch.object(stock_repo._ohlcv_repo, "get_stock_data", new_callable=AsyncMock, return_value=None)

    # 2. Broker API 모킹 (현재가 조회: 가격 돌파와 거래량 돌파 조건을 통과하도록 데이터 세팅)
    mock_get_price = mocker.patch.object(
        broker, "get_current_price",
        new_callable=AsyncMock,
        return_value=ResCommonResponse(rt_cd="0", msg1="ok", data={
            "output": {
                "stck_prpr": "75000",
                "stck_oprc": "74000",
                "stck_hgpr": "76000",
                "stck_lwpr": "74000",
                "prdy_vrss": "1000",
                "prdy_vrss_sign": "2",
                "acml_vol": "3000000",
                "hts_avls": "10000",
                "stck_llam": "10000"
            }
        })
    )

    # 25일치 OHLCV 데이터 세팅 (20일 최고가 72400)
    dummy_ohlcv = []
    from datetime import timedelta
    base_dt = datetime(2026, 3, 7)
    for i in range(25):
        dt = base_dt - timedelta(days=24 - i)
        date_str = dt.strftime("%Y%m%d")
        price = 70000 + i * 100
        dummy_ohlcv.append({
            "stck_bsop_date": date_str,
            "stck_clpr": str(price), "close": price,
            "stck_hgpr": str(price + 500), "high": price + 500,
            "stck_lwpr": str(price - 500), "low": price - 500,
            "stck_oprc": str(price), "open": price,
            "acml_vol": "1000000", "volume": 1000000
        })

    mock_get_ohlcv = mocker.patch.object(
        broker, "inquire_daily_itemchartprice",
        new_callable=AsyncMock,
        return_value=ResCommonResponse(rt_cd="0", msg1="ok", data=dummy_ohlcv)
    )

    # 워치리스트 빌드용 API 모킹
    mock_sqs = deep_paper_ctx.stock_query_service
    mocker.patch.object(mock_sqs, 'get_top_trading_value_stocks', new_callable=AsyncMock, 
                        return_value=ResCommonResponse(rt_cd="0", msg1="ok", data=[{"mksc_shrn_iscd": code_a, "hts_kor_isnm": "테스트종목"}]))

    # 3. 시간 모킹
    kst = timezone("Asia/Seoul")
    mock_tm = MagicMock()
    mock_tm.get_current_kst_time.return_value = datetime(2026, 3, 8, 10, 0, tzinfo=kst)
    mock_tm.get_market_open_time.return_value = datetime(2026, 3, 8, 9, 0, tzinfo=kst)
    mock_tm.get_market_close_time.return_value = datetime(2026, 3, 8, 15, 30, tzinfo=kst)

    stock_code_repo = MagicMock(spec=StockCodeRepository)
    stock_code_repo.get_name_by_code.return_value = "테스트종목"

    strategy = TraditionalVolumeBreakoutStrategy(
        stock_query_service=mock_sqs,
        stock_code_repository=stock_code_repo,
        market_clock=mock_tm,
        config=TraditionalVBConfig(min_avg_trading_value_5d=0, near_high_pct=100.0)
    )

    # [상황 A] 완전 초기화 상태 (Cache Miss)
    stock_repo._price_repo._price_cache.clear()
    stock_repo._ohlcv_repo._ohlcv_cache.clear()

    await strategy.scan()
    assert mock_get_price.call_count >= 1

    # [상황 B] 연속 실행 (PriceStream snapshot hit)
    mock_get_price.reset_mock()
    strategy._position_state.clear()
    snapshot_hits_before = mock_sqs._price_lookup_stats["snapshot_hit"]
    await strategy.scan()
    assert mock_get_price.call_count == 0, "캐시 적중 시 현재가 API는 호출되지 않아야 함"
    assert mock_sqs._price_lookup_stats["snapshot_hit"] > snapshot_hits_before
