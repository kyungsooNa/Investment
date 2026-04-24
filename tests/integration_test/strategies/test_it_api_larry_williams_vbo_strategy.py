import pytest
import uuid
from unittest.mock import MagicMock, AsyncMock
from datetime import datetime
from pytz import timezone

from strategies.larry_williams_vbo_strategy import LarryWilliamsVBOStrategy, LarryWilliamsVBOConfig
from common.types import ResCommonResponse


@pytest.mark.asyncio
async def test_vbo_scan_cache_behavior_reduces_api_calls(deep_paper_ctx, mocker):
    """VBO 전략 스캔 시, Memory Cache Hit 여부에 따라 실제 브로커 API 호출이 어떻게 감소하는지 검증.

    [상황 A] Cache Miss: 현재가·일봉·체결강도 API 모두 호출됨.
    [상황 B] Cache Hit:
      - 현재가 API → BrokerAPIWrapper 메모리 캐시 적중 → 호출 0
      - 일봉 API   → 전략 내 _RangeCache 당일 적중    → 호출 0
      - 체결강도   → 항상 실시간 조회                  → 1차와 동일 횟수 호출
    """

    uid = uuid.uuid4().int
    code_a = str(uid % 1_000_000).zfill(6)

    # 테스트 실행 환경이 느려 캐시 TTL이 만료되는 것을 방지하기 위해 시간 고정
    mocker.patch("time.time", return_value=1_600_000_000.0)

    # 1. Broker 계층 및 캐시 저장소 추출
    md_service = deep_paper_ctx.stock_query_service.market_data_service
    broker = md_service._broker_api_wrapper
    stock_repo = md_service._stock_repo
    mock_sqs = deep_paper_ctx.stock_query_service

    # 2. Pool B 로드 (universe_service=None fallback 경로 → sqs.get_top_trading_value_stocks)
    mocker.patch.object(
        mock_sqs, "get_top_trading_value_stocks", new_callable=AsyncMock,
        return_value=ResCommonResponse(rt_cd="0", msg1="ok", data=[
            {"mksc_shrn_iscd": code_a, "hts_kor_isnm": "테스트종목A", "stck_avls": "500000000000"},
        ])
    )

    # 3. 현재가 API 모킹 (Broker 레벨 — 캐시 계층 통과)
    # Range = 72000 - 70000 = 2000, K=0.5 → Target = 70000 + 1000 = 71000
    # current=72000 > 71000 → 돌파 ✓
    # pgtr_ntby_qty=700000 × 72000원 = 50.4B / 거래대금 50B = 100.8% ≥ 10% ✓
    mock_get_price = mocker.patch.object(
        broker, "get_current_price",
        new_callable=AsyncMock,
        return_value=ResCommonResponse(rt_cd="0", msg1="ok", data={
            "output": {
                "stck_prpr":     "72000",
                "stck_oprc":     "70000",
                "stck_hgpr":     "73000",
                "stck_lwpr":     "69500",
                "prdy_vrss":     "2000",
                "prdy_vrss_sign": "2",
                "acml_vol":      "3000000",
                "pgtr_ntby_qty": "700000",
                "acml_tr_pbmn":  "50000000000",
                "hts_avls":      "10000",
                "stck_llam":     "10000",
            }
        })
    )

    # 4. 전일 일봉 API 모킹 (Broker 레벨 — Range 계산용)
    # high=72000, low=70000 → Range=2000
    mock_get_ohlcv = mocker.patch.object(
        broker, "inquire_daily_itemchartprice",
        new_callable=AsyncMock,
        return_value=ResCommonResponse(rt_cd="0", msg1="ok", data=[
            {
                "stck_bsop_date": "20260307",
                "stck_oprc": "70100", "open": 70100,
                "stck_hgpr": "72000", "high": 72000,
                "stck_lwpr": "70000", "low": 70000,
                "stck_clpr": "71000", "close": 71000,
                "acml_vol":  "2000000", "volume": 2000000,
            },
            {
                "stck_bsop_date": "20260306",
                "stck_oprc": "69000", "open": 69000,
                "stck_hgpr": "70500", "high": 70500,
                "stck_lwpr": "68500", "low": 68500,
                "stck_clpr": "70000", "close": 70000,
                "acml_vol":  "1800000", "volume": 1800000,
            },
        ])
    )

    # 5. 체결강도 API 모킹 (Broker 레벨 — 120% 이상)
    mock_get_conclusion = mocker.patch.object(
        broker, "get_stock_conclusion",
        new_callable=AsyncMock,
        return_value=ResCommonResponse(rt_cd="0", msg1="ok", data={"output": [{"tday_rltv": "135.00"}]})
    )

    # 6. 시간 모킹 (09:10~14:00 진입 가능 시간대)
    kst = timezone("Asia/Seoul")
    mock_tm = MagicMock()
    mock_tm.get_current_kst_time.return_value = datetime(2026, 3, 8, 10, 0, tzinfo=kst)
    mock_tm.get_market_open_time.return_value = datetime(2026, 3, 8, 9, 0, tzinfo=kst)
    mock_tm.get_market_close_time.return_value = datetime(2026, 3, 8, 15, 30, tzinfo=kst)

    # 7. 전략 생성 (universe_service=None → fallback 경로, 유효성 필터 임계값 0으로 비활성)
    strategy = LarryWilliamsVBOStrategy(
        stock_query_service=mock_sqs,
        market_clock=mock_tm,
        config=LarryWilliamsVBOConfig(
            k_value=0.5,
            min_market_cap=0,
            min_5d_trading_value=0,
            confidence_threshold=120.0,
            program_buy_ratio=0.10,
            stop_loss_pct=-3.0,
        ),
    )

    # ── [상황 A] 초기 스캔 (Cache Miss) ─────────────────────────────────
    stock_repo._price_repo._price_cache.clear()
    stock_repo._ohlcv_repo._ohlcv_cache.clear()

    signals = await strategy.scan()

    calls_price_on_miss      = mock_get_price.call_count
    calls_ohlcv_on_miss      = mock_get_ohlcv.call_count
    calls_conclusion_on_miss = mock_get_conclusion.call_count

    assert calls_price_on_miss >= 1,      "초기 스캔 시 현재가 API가 호출되어야 함"
    assert calls_ohlcv_on_miss >= 1,      "초기 스캔 시 일봉 API(Range 계산)가 호출되어야 함"
    assert calls_conclusion_on_miss >= 1, "초기 스캔 시 체결강도 API가 호출되어야 함"
    assert len(signals) >= 1,             "돌파 조건 충족 시 BUY 신호가 생성되어야 함"
    assert signals[0].action == "BUY"
    assert signals[0].code == code_a

    # ── [상황 B] 두 번째 스캔 (Cache Hit + Range Cache Hit) ──────────────
    mock_get_price.reset_mock()
    mock_get_ohlcv.reset_mock()
    mock_get_conclusion.reset_mock()
    strategy._bought_today.clear()  # 재진입 허용하여 Cache Hit 여부만 검증

    await strategy.scan()

    assert mock_get_price.call_count == 0,    "캐시 적중 시 현재가 API는 호출되지 않아야 함"
    assert mock_get_ohlcv.call_count == 0,    "당일 Range 캐시 적중 시 일봉 API는 재호출되지 않아야 함"
    assert mock_get_conclusion.call_count == calls_conclusion_on_miss, \
        "체결강도 실시간 API는 캐시와 무관하게 항상 호출되어야 함"
    assert stock_repo.get_cache_stats()["hits"] > 0
