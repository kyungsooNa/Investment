"""
가상 매매 관련 테스트 (virtual.html).
"""
import time
import pytest
from unittest.mock import MagicMock
from common.types import ResCommonResponse
from view.web import web_api


@pytest.mark.asyncio
async def test_virtual_endpoints(web_client, mock_web_ctx):
    """모의투자 관련 엔드포인트 테스트"""
    web_api._PRICE_CACHE.clear()
    # Summary
    mock_web_ctx.virtual_manager.get_summary.return_value = {"total_trades": 10}
    response = web_client.get("/api/virtual/summary")
    assert response.status_code == 200
    assert response.json()["total_trades"] == 10

    # Strategies
    mock_web_ctx.virtual_manager.get_all_strategies.return_value = ["StrategyA"]
    response = web_client.get("/api/virtual/strategies")
    assert response.status_code == 200
    assert response.json() == ["StrategyA"]

    # History
    mock_web_ctx.virtual_manager.get_all_trades.return_value = [
        {"code": "005930", "strategy": "StrategyA", "buy_price": 1000, "return_rate": 10.0, "status": "HOLD"}
    ]

    mock_web_ctx.virtual_manager.get_daily_change.return_value = (0.0, None)
    mock_web_ctx.virtual_manager.get_weekly_change.return_value = (0.0, None)
    mock_web_ctx.virtual_manager._load_data.return_value = {}
    mock_web_ctx.virtual_manager.save_daily_snapshot.return_value = None

    mock_web_ctx.stock_code_mapper = MagicMock()
    mock_web_ctx.stock_code_mapper.get_name_by_code.return_value = "삼성전자"

    async def mock_multi_price(codes):
        return ResCommonResponse(rt_cd="0", msg1="OK", data=[
            {"stck_shrn_iscd": "005930", "stck_prpr": "1100", "prdy_ctrt": "10.0"}
        ])
    mock_web_ctx.stock_query_service.get_multi_price = mock_multi_price

    response = web_client.get("/api/virtual/history")
    assert response.status_code == 200
    assert "trades" in response.json()
    assert len(response.json()["trades"]) == 1
    assert response.json()["trades"][0]["stock_name"] == "삼성전자"


@pytest.mark.asyncio
async def test_get_virtual_summary_no_manager(web_client, mock_web_ctx):
    """GET /api/virtual/summary 매니저 없음 테스트"""
    if hasattr(mock_web_ctx, 'virtual_manager'): del mock_web_ctx.virtual_manager
    response = web_client.get("/api/virtual/summary")
    assert response.status_code == 200
    assert response.json()["total_trades"] == 0


@pytest.mark.asyncio
async def test_get_strategy_chart(web_client, mock_web_ctx):
    """GET /api/virtual/chart/{strategy_name} 엔드포인트 테스트"""
    mock_web_ctx.virtual_manager.get_all_strategies.return_value = ["StrategyA"]
    mock_web_ctx.virtual_manager.get_strategy_return_history.return_value = [
        {"date": "2025-01-01", "return_rate": 1.0},
        {"date": "2025-01-02", "return_rate": 2.0}
    ]

    mock_web_ctx.stock_query_service.get_ohlcv_range.return_value = ResCommonResponse(
        rt_cd="0", msg1="Success", data=[
            {"date": "20250101", "close": 30000},
            {"date": "20250102", "close": 30300}
        ]
    )

    response = web_client.get("/api/virtual/chart/StrategyA")
    assert response.status_code == 200
    data = response.json()
    assert "histories" in data
    assert "benchmarks" in data
    assert "StrategyA" in data["histories"]
    assert "KOSPI200" in data["benchmarks"]
    assert len(data["benchmarks"]["KOSPI200"]) == 2


@pytest.mark.asyncio
async def test_get_strategy_chart_all_and_failure(web_client, mock_web_ctx):
    """GET /api/virtual/chart/ALL 및 벤치마크 실패 테스트"""
    mock_web_ctx.virtual_manager.get_all_strategies.return_value = ["StratA"]
    mock_web_ctx.virtual_manager.get_strategy_return_history.return_value = [{"date": "2025-01-01", "return_rate": 0}]

    mock_web_ctx.stock_query_service.get_ohlcv_range.return_value = ResCommonResponse(
        rt_cd="1", msg1="Fail", data=None
    )

    response = web_client.get("/api/virtual/chart/ALL")
    assert response.status_code == 200
    data = response.json()
    assert "StratA" in data["histories"]
    assert data["benchmarks"]["KOSPI200"][0]["return_rate"] == 0


@pytest.mark.asyncio
async def test_calculate_benchmark_zero_base_price(web_client, mock_web_ctx):
    """_calculate_benchmark 기준가 0일 때 테스트"""
    mock_web_ctx.virtual_manager.get_all_strategies.return_value = ["StratA"]
    mock_web_ctx.virtual_manager.get_strategy_return_history.return_value = [
        {"date": "2025-01-01", "return_rate": 0}
    ]

    mock_web_ctx.stock_query_service.get_ohlcv_range.return_value = ResCommonResponse(
        rt_cd="0", msg1="Success", data=[{"date": "20250101", "close": 0}]
    )

    response = web_client.get("/api/virtual/chart/ALL")
    assert response.status_code == 200
    data = response.json()
    assert data["benchmarks"]["KOSPI200"][0]["return_rate"] == 0


@pytest.mark.asyncio
async def test_calculate_benchmark_invalid_base_price(web_client, mock_web_ctx):
    """_calculate_benchmark 기준가가 유효하지 않을 때 테스트"""
    mock_web_ctx.virtual_manager.get_all_strategies.return_value = ["StratA"]
    mock_web_ctx.virtual_manager.get_strategy_return_history.return_value = [
        {"date": "2025-01-01", "return_rate": 0}
    ]

    mock_web_ctx.stock_query_service.get_ohlcv_range.return_value = ResCommonResponse(
        rt_cd="0", msg1="Success", data=[{"date": "20250101", "close": "invalid"}]
    )

    response = web_client.get("/api/virtual/chart/ALL")
    assert response.status_code == 200
    data = response.json()
    assert data["benchmarks"]["KOSPI200"][0]["return_rate"] == 0


@pytest.mark.asyncio
async def test_get_virtual_history_complex(web_client, mock_web_ctx):
    """GET /api/virtual/history 복합 테스트 (캐시, SOLD 보정, 매니저 없음)"""
    web_api._PRICE_CACHE.clear()
    # 1. 매니저 없음
    if hasattr(mock_web_ctx, 'virtual_manager'):
        vm = mock_web_ctx.virtual_manager
        del mock_web_ctx.virtual_manager
    resp = web_client.get("/api/virtual/history")
    assert resp.json()["trades"] == []

    # 매니저 복구
    mock_web_ctx.virtual_manager = vm

    # 2. 캐시 히트 및 SOLD 보정 테스트
    mock_web_ctx.virtual_manager.get_all_trades.return_value = [
        {"code": "005930", "status": "HOLD", "buy_price": 1000, "strategy": "A"},
        {"code": "000660", "status": "SOLD", "sell_price": 0, "buy_price": 1000, "strategy": "A"}
    ]

    # 캐시 설정 (005930은 캐시 히트)
    web_api._PRICE_CACHE["005930"] = (50000, 5.0, time.time())

    # get_multi_price Mock (000660은 API 호출 -> SOLD 가격 보정용)
    async def mock_multi_price(codes):
        items = []
        for code in codes:
            if code == "000660":
                items.append({"stck_shrn_iscd": "000660", "stck_prpr": "1100", "prdy_ctrt": "10.0"})
        return ResCommonResponse(rt_cd="0", msg1="OK", data=items)
    mock_web_ctx.stock_query_service.get_multi_price = mock_multi_price
    mock_web_ctx.virtual_manager.fix_sell_price = MagicMock()

    # 스냅샷 관련 Mock
    mock_web_ctx.virtual_manager.save_daily_snapshot = MagicMock()
    mock_web_ctx.virtual_manager._load_data.return_value = {}
    mock_web_ctx.virtual_manager.get_daily_change.return_value = (0.0, None)
    mock_web_ctx.virtual_manager.get_weekly_change.return_value = (0.0, None)

    response = web_client.get("/api/virtual/history")
    assert response.status_code == 200

    trades = response.json()["trades"]
    # 005930: 캐시된 값 확인
    hold_trade = next(t for t in trades if t["code"] == "005930")
    assert hold_trade["current_price"] == 50000
    assert hold_trade["is_cached"] is False

    # 000660: SOLD 가격 보정 확인 (0 -> 1100)
    sold_trade = next(t for t in trades if t["code"] == "000660")
    assert sold_trade["sell_price"] == 1100
    mock_web_ctx.virtual_manager.fix_sell_price.assert_called()

    # Cleanup
    web_api._PRICE_CACHE.clear()


@pytest.mark.asyncio
async def test_get_virtual_history_force_update(web_client, mock_web_ctx):
    """GET /api/virtual/history force_code 테스트"""
    web_api._PRICE_CACHE.clear()
    mock_web_ctx.virtual_manager.get_all_trades.return_value = [
        {"code": "005930", "status": "HOLD", "buy_price": 1000, "strategy": "A"}
    ]

    # 캐시 설정 (최신)
    web_api._PRICE_CACHE["005930"] = (50000, 5.0, time.time())

    # get_multi_price Mock
    async def mock_multi_price(codes):
        return ResCommonResponse(rt_cd="0", msg1="OK", data=[
            {"stck_shrn_iscd": "005930", "stck_prpr": "51000", "prdy_ctrt": "2.0"}
        ])
    mock_web_ctx.stock_query_service.get_multi_price = mock_multi_price

    # force_code 지정 -> 캐시 무시하고 API 호출 예상
    response = web_client.get("/api/virtual/history?force_code=005930")
    assert response.status_code == 200

    trades = response.json()["trades"]
    assert trades[0]["current_price"] == 51000


@pytest.mark.asyncio
async def test_get_virtual_history_api_exception(web_client, mock_web_ctx):
    """GET /api/virtual/history API 예외 발생 테스트"""
    web_api._PRICE_CACHE.clear()

    mock_web_ctx.virtual_manager.get_all_trades.return_value = [
        {"code": "005930", "status": "HOLD", "buy_price": 1000, "strategy": "A"}
    ]

    async def mock_multi_price_error(codes):
        raise Exception("API Error")
    mock_web_ctx.stock_query_service.get_multi_price = mock_multi_price_error

    response = web_client.get("/api/virtual/history")
    assert response.status_code == 200

    trades = response.json()["trades"]
    assert "current_price" not in trades[0] or trades[0]["current_price"] is None


@pytest.mark.asyncio
async def test_get_virtual_history_price_parsing_error(web_client, mock_web_ctx):
    """GET /api/virtual/history 가격 파싱 에러 테스트"""
    web_api._PRICE_CACHE.clear()

    mock_web_ctx.virtual_manager.get_all_trades.return_value = [
        {"code": "005930", "status": "HOLD", "buy_price": 1000, "strategy": "A"}
    ]

    # stck_prpr에 유효하지 않은 값 → price_val=0 → price_map에 추가 안 됨
    async def mock_multi_price_invalid(codes):
        return ResCommonResponse(rt_cd="0", msg1="OK", data=[
            {"stck_shrn_iscd": "005930", "stck_prpr": "invalid", "prdy_ctrt": "0.0"}
        ])
    mock_web_ctx.stock_query_service.get_multi_price = mock_multi_price_invalid

    response = web_client.get("/api/virtual/history")
    assert response.status_code == 200
    trades = response.json()["trades"]
    assert "current_price" not in trades[0] or trades[0]["current_price"] is None


@pytest.mark.asyncio
async def test_get_virtual_history_fallback_to_cache(web_client, mock_web_ctx):
    """GET /api/virtual/history API 실패 시 캐시 폴백 테스트"""
    web_api._PRICE_CACHE.clear()
    # 1. Setup
    mock_web_ctx.virtual_manager.get_all_trades.return_value = [
        {"code": "005930", "status": "HOLD", "buy_price": 1000, "strategy": "A"}
    ]

    # 캐시에 데이터 존재 (만료된 상태로 설정하여 API 호출 유도 -> API 실패 -> 폴백 확인)
    web_api._PRICE_CACHE["005930"] = (60000, 2.0, time.time() - 100)

    # API 호출은 빈 데이터 반환 (실패 시뮬레이션)
    async def mock_multi_price_empty(codes):
        return ResCommonResponse(rt_cd="0", msg1="OK", data=[])
    mock_web_ctx.stock_query_service.get_multi_price = mock_multi_price_empty

    # 2. Execute
    response = web_client.get("/api/virtual/history")

    # 3. Assert
    assert response.status_code == 200
    trades = response.json()["trades"]
    # API 실패했으나 캐시값(60000)을 사용했는지 확인
    assert trades[0]["current_price"] == 60000
    assert trades[0]["is_cached"] is True
    web_api._PRICE_CACHE.clear()


@pytest.mark.asyncio
async def test_get_virtual_history_internal_exceptions(web_client, mock_web_ctx):
    """GET /api/virtual/history 내부 로직 예외 처리 테스트 (fix_sell_price, snapshot, first_dates)"""
    web_api._PRICE_CACHE.clear()
    # 1. Setup
    # buy_date를 정수가 아닌 타입으로 설정하여 슬라이싱 에러 유도 (Block 5 예외)
    mock_web_ctx.virtual_manager.get_all_trades.return_value = [
        {"code": "005930", "status": "SOLD", "sell_price": 0, "buy_price": 1000, "strategy": "A", "buy_date": 12345}
    ]

    # API 정상 응답 (SOLD 가격 보정 트리거)
    async def mock_multi_price(codes):
        return ResCommonResponse(rt_cd="0", msg1="OK", data=[
            {"stck_shrn_iscd": "005930", "stck_prpr": "50000", "prdy_ctrt": "0.0"}
        ])
    mock_web_ctx.stock_query_service.get_multi_price = mock_multi_price

    # fix_sell_price 예외 발생 설정 (Block 3 예외)
    mock_web_ctx.virtual_manager.fix_sell_price.side_effect = Exception("Fix Error")

    # save_daily_snapshot 예외 발생 설정 (Block 4 예외)
    mock_web_ctx.virtual_manager.save_daily_snapshot.side_effect = Exception("Snapshot Error")

    # 2. Execute
    response = web_client.get("/api/virtual/history")

    # 3. Assert
    assert response.status_code == 200
    trades = response.json()["trades"]
    assert len(trades) == 1
    # 예외 발생으로 first_dates는 비어있음
    assert response.json()["first_dates"] == {}


@pytest.mark.asyncio
async def test_get_virtual_history_missing_services(web_client, mock_web_ctx):
    """GET /api/virtual/history 필수 서비스 누락 테스트"""
    web_api._PRICE_CACHE.clear()
    # Mapper 없음, QueryService 없음
    if hasattr(mock_web_ctx, 'stock_code_mapper'): del mock_web_ctx.stock_code_mapper
    if hasattr(mock_web_ctx, 'stock_query_service'): del mock_web_ctx.stock_query_service

    mock_web_ctx.virtual_manager.get_all_trades.return_value = [
        {"code": "005930", "status": "HOLD", "buy_price": 1000, "strategy": "A"}
    ]

    response = web_client.get("/api/virtual/history")
    assert response.status_code == 200
    trades = response.json()["trades"]
    # Mapper 없어서 빈 문자열
    assert trades[0]["stock_name"] == ""
    # QueryService 없어서 현재가 업데이트 안됨
    assert "current_price" not in trades[0]


@pytest.mark.asyncio
async def test_get_strategy_chart_empty_history(web_client, mock_web_ctx):
    """GET /api/virtual/chart/{strategy_name} 히스토리 없음 테스트"""
    mock_web_ctx.virtual_manager.get_strategy_return_history.return_value = []

    response = web_client.get("/api/virtual/chart/StrategyA")
    assert response.status_code == 200
    data = response.json()
    assert data["histories"] == {}
    assert data["benchmarks"] == {}


@pytest.mark.asyncio
async def test_calculate_benchmark_invalid_price_in_ohlcv(web_client, mock_web_ctx):
    """_calculate_benchmark OHLCV 데이터 중 비수치 데이터 포함 테스트"""
    mock_web_ctx.virtual_manager.get_all_strategies.return_value = ["StratA"]
    mock_web_ctx.virtual_manager.get_strategy_return_history.return_value = [
        {"date": "2025-01-01", "return_rate": 0},
        {"date": "2025-01-02", "return_rate": 0}
    ]

    # 20250102의 close가 문자열이거나 None
    mock_web_ctx.stock_query_service.get_ohlcv_range.return_value = ResCommonResponse(
        rt_cd="0", msg1="Success", data=[
            {"date": "20250101", "close": 100},
            {"date": "20250102", "close": "invalid"}
        ]
    )

    response = web_client.get("/api/virtual/chart/ALL")
    assert response.status_code == 200
    data = response.json()
    # 20250102는 가격을 못 가져와서 last_price(100) 유지 -> 수익률 0
    bench = data["benchmarks"]["KOSPI200"]
    assert len(bench) == 2
    assert bench[1]["return_rate"] == 0.0

    
@pytest.mark.asyncio
async def test_get_virtual_history_snapshot_dates_populated(web_client, mock_web_ctx):
    """GET /api/virtual/history 스냅샷 날짜(d_date, w_date)가 있을 때 응답 검증"""
    web_api._PRICE_CACHE.clear()
    # Setup
    mock_web_ctx.virtual_manager.get_all_trades.return_value = [
        {"code": "005930", "status": "HOLD", "buy_price": 1000, "strategy": "A", "return_rate": 10.0}
    ]

    # Mock snapshot return values with actual dates
    mock_web_ctx.virtual_manager.get_daily_change.return_value = (1.5, "2025-01-02")
    mock_web_ctx.virtual_manager.get_weekly_change.return_value = (5.0, "2024-12-25")
    mock_web_ctx.virtual_manager._load_data.return_value = {}  # Dummy data
    mock_web_ctx.virtual_manager.save_daily_snapshot.return_value = None

    # Execute
    response = web_client.get("/api/virtual/history")

    # Assert
    assert response.status_code == 200
    data = response.json()

    # Check if ref dates are populated
    assert data["daily_ref_dates"]["A"] == "2025-01-02"
    assert data["weekly_ref_dates"]["A"] == "2024-12-25"
    # ALL is also calculated
    assert data["daily_ref_dates"]["ALL"] == "2025-01-02"


@pytest.mark.asyncio
async def test_get_virtual_history_first_dates_calculation(web_client, mock_web_ctx):
    """GET /api/virtual/history 최초 매매일 계산 로직 검증 (earlier date update)"""
    web_api._PRICE_CACHE.clear()
    # Setup: 3 trades.
    # 1. Strat A, 2025-02-01
    # 2. Strat A, 2025-01-01 (Should update Strat A and ALL)
    # 3. Strat B, 2025-03-01 (Should set Strat B, but ALL is already 2025-01-01 so no update for ALL)
    mock_web_ctx.virtual_manager.get_all_trades.return_value = [
        {"code": "001", "strategy": "A", "buy_date": "2025-02-01 10:00:00"},
        {"code": "002", "strategy": "A", "buy_date": "2025-01-01 10:00:00"},
        {"code": "003", "strategy": "B", "buy_date": "2025-03-01 10:00:00"},
    ]

    # Execute
    response = web_client.get("/api/virtual/history")

    # Assert
    assert response.status_code == 200
    data = response.json()
    first_dates = data["first_dates"]

    assert first_dates["A"] == "2025-01-01"
    assert first_dates["B"] == "2025-03-01"
    assert first_dates["ALL"] == "2025-01-01"


@pytest.mark.asyncio
async def test_get_virtual_history_asset_weighted_calculation(web_client, mock_web_ctx):
    """GET /api/virtual/history 자산 가중 평균 수익률 및 집계 데이터 반환 테스트"""
    # 캐시 초기화 (다른 테스트의 영향 방지)
    web_api._PRICE_CACHE.clear()

    # 1. Mock Trades 설정
    # StratA: 100만원 매수 (1000원 * 1000주) -> 10% 수익
    # StratB: 1만원 매수 (1000원 * 10주) -> -50% 손실
    mock_web_ctx.virtual_manager.get_all_trades.return_value = [
        {"code": "005930", "strategy": "StratA", "buy_price": 1000, "qty": 1000, "status": "HOLD", "buy_date": "2025-01-01"},
        {"code": "000660", "strategy": "StratB", "buy_price": 1000, "qty": 10, "status": "HOLD", "buy_date": "2025-01-01"}
    ]
    
    # 2. 현재가 Mocking
    # 005930: 1100원 (+10%) -> 평가금 1,100,000
    # 000660: 500원 (-50%) -> 평가금 5,000
    async def mock_multi_price(codes):
        data = []
        for code in codes:
            if code == "005930":
                data.append({"stck_shrn_iscd": "005930", "stck_prpr": "1100", "prdy_ctrt": "10.0"})
            elif code == "000660":
                data.append({"stck_shrn_iscd": "000660", "stck_prpr": "500", "prdy_ctrt": "-50.0"})
        return ResCommonResponse(rt_cd="0", msg1="OK", data=data)
    
    mock_web_ctx.stock_query_service.get_multi_price = mock_multi_price
    
    # 3. Manager Mocking
    mock_web_ctx.virtual_manager.save_daily_snapshot = MagicMock()
    mock_web_ctx.virtual_manager._load_data.return_value = {}
    mock_web_ctx.virtual_manager.get_daily_change.return_value = (None, None)
    mock_web_ctx.virtual_manager.get_weekly_change.return_value = (None, None)
    
    # 4. API 호출
    response = web_client.get("/api/virtual/history")
    assert response.status_code == 200
    data = response.json()
    
    # 5. 검증
    # summary_agg 확인
    agg = data["summary_agg"]
    assert "ALL" in agg
    assert agg["ALL"]["buy_sum"] == 1010000.0  # 1,000,000 + 10,000
    assert agg["ALL"]["eval_sum"] == 1105000.0 # 1,100,000 + 5,000
    
    # cumulative_returns 확인 (자산 가중 평균)
    # (1,105,000 - 1,010,000) / 1,010,000 * 100 = 9.4059... -> 9.41%
    assert data["cumulative_returns"]["ALL"] == 9.41
    assert data["cumulative_returns"]["StratA"] == 10.0
    assert data["cumulative_returns"]["StratB"] == -50.0


@pytest.mark.asyncio
async def test_get_virtual_history_counts_logic(web_client, mock_web_ctx):
    """GET /api/virtual/history 포지션 현황(counts) 집계 로직 테스트"""
    web_api._PRICE_CACHE.clear()
    
    from datetime import datetime, timezone, timedelta
    KST = timezone(timedelta(hours=9))
    real_today = datetime.now(KST).strftime("%Y-%m-%d")
    
    # 1. Mock Trades
    trades = [
        {"code": "A1", "strategy": "StratA", "status": "HOLD", "buy_date": f"{real_today} 09:00:00"},
        {"code": "A2", "strategy": "StratA", "status": "SOLD", "buy_date": "2000-01-01 09:00:00", "sell_date": f"{real_today} 10:00:00"},
        {"code": "B1", "strategy": "StratB", "status": "HOLD", "buy_date": "2000-01-01 09:00:00"},
        {"code": "B2", "strategy": "StratB", "status": "SOLD", "buy_date": "2000-01-01 09:00:00", "sell_date": "2000-01-02 10:00:00"},
    ]
    mock_web_ctx.virtual_manager.get_all_trades.return_value = trades
    
    # 2. Mock Snapshot (오늘 날짜 포함 -> 휴장일 로직 미발동)
    mock_web_ctx.virtual_manager._load_data.return_value = {
        "daily": {
            "2000-01-01": {},
            real_today: {} 
        }
    }
    mock_web_ctx.virtual_manager.save_daily_snapshot = MagicMock()
    mock_web_ctx.virtual_manager.get_daily_change.return_value = (None, None)
    mock_web_ctx.virtual_manager.get_weekly_change.return_value = (None, None)
    
    async def mock_multi_price(codes):
        return ResCommonResponse(rt_cd="0", msg1="OK", data=[])
    mock_web_ctx.stock_query_service.get_multi_price = mock_multi_price
    
    # 3. API 호출
    response = web_client.get("/api/virtual/history")
    assert response.status_code == 200
    data = response.json()
    
    counts = data["counts"]
    
    # StratA
    assert counts["StratA"]["hold"] == 1
    assert counts["StratA"]["today_buy"] == 1
    assert counts["StratA"]["today_sell"] == 1
    
    # StratB
    assert counts["StratB"]["hold"] == 1
    assert counts["StratB"]["today_buy"] == 0
    assert counts["StratB"]["today_sell"] == 0
    
    # ALL
    assert counts["ALL"]["hold"] == 2
    assert counts["ALL"]["today_buy"] == 1
    assert counts["ALL"]["today_sell"] == 1


@pytest.mark.asyncio
async def test_get_virtual_history_counts_holiday_logic(web_client, mock_web_ctx):
    """GET /api/virtual/history 휴장일(주말 등) 카운트 집계 로직 테스트"""
    web_api._PRICE_CACHE.clear()
    
    # 시나리오: 스냅샷의 마지막 날짜가 과거임 -> 오늘이 휴장일이라고 판단 -> 기준일이 과거 날짜로 변경됨
    last_open_day = "2020-01-01" # 확실한 과거
    
    # 1. Snapshot 설정
    mock_web_ctx.virtual_manager._load_data.return_value = {
        "daily": {
            last_open_day: {}
        }
    }
    mock_web_ctx.virtual_manager.save_daily_snapshot = MagicMock()
    mock_web_ctx.virtual_manager.get_daily_change.return_value = (None, None)
    mock_web_ctx.virtual_manager.get_weekly_change.return_value = (None, None)
    
    async def mock_multi_price(codes):
        return ResCommonResponse(rt_cd="0", msg1="OK", data=[])
    mock_web_ctx.stock_query_service.get_multi_price = mock_multi_price
    
    # 2. Trades 설정
    # T1: last_open_day 매수 (휴장일 조회 시 today_buy로 잡혀야 함)
    trades = [
        {"code": "A1", "strategy": "StratA", "status": "HOLD", "buy_date": f"{last_open_day} 09:00:00"},
    ]
    mock_web_ctx.virtual_manager.get_all_trades.return_value = trades
    
    # 3. API 호출
    response = web_client.get("/api/virtual/history")
    assert response.status_code == 200
    data = response.json()
    
    counts = data["counts"]
    
    # StratA: hold=1, today_buy=1 (last_open_day 기준)
    assert counts["StratA"]["hold"] == 1
    assert counts["StratA"]["today_buy"] == 1


@pytest.mark.asyncio
async def test_get_virtual_history_profit_factor_and_expectancy(web_client, mock_web_ctx):
    """GET /api/virtual/history Profit Factor & Expectancy 계산 검증"""
    web_api._PRICE_CACHE.clear()

    # 4건의 거래: 2승(+20%, +10%) 2패(-5%, -15%)
    mock_web_ctx.virtual_manager.get_all_trades.return_value = [
        {"code": "A", "strategy": "S1", "buy_price": 1000, "qty": 1, "status": "SOLD",
         "sell_price": 1200, "return_rate": 20.0, "buy_date": "2025-01-01 09:00:00"},
        {"code": "B", "strategy": "S1", "buy_price": 1000, "qty": 1, "status": "SOLD",
         "sell_price": 1100, "return_rate": 10.0, "buy_date": "2025-01-02 09:00:00"},
        {"code": "C", "strategy": "S1", "buy_price": 1000, "qty": 1, "status": "SOLD",
         "sell_price": 950, "return_rate": -5.0, "buy_date": "2025-01-03 09:00:00"},
        {"code": "D", "strategy": "S1", "buy_price": 1000, "qty": 1, "status": "SOLD",
         "sell_price": 850, "return_rate": -15.0, "buy_date": "2025-01-04 09:00:00"},
    ]

    mock_web_ctx.virtual_manager.save_daily_snapshot = MagicMock()
    mock_web_ctx.virtual_manager._load_data.return_value = {}
    mock_web_ctx.virtual_manager.get_daily_change.return_value = (None, None)
    mock_web_ctx.virtual_manager.get_weekly_change.return_value = (None, None)

    async def mock_multi_price(codes):
        return ResCommonResponse(rt_cd="0", msg1="OK", data=[])
    mock_web_ctx.stock_query_service.get_multi_price = mock_multi_price

    response = web_client.get("/api/virtual/history")
    assert response.status_code == 200
    data = response.json()

    # Profit Factor 검증
    # 총 수익금: 200 + 100 = 300, 총 손실금: 50 + 150 = 200
    # PF = 300 / 200 = 1.5
    pf = data["profit_factors"]
    assert "S1" in pf
    assert pf["S1"]["value"] == 1.5
    assert pf["S1"]["total_gain"] == 300
    assert pf["S1"]["total_loss"] == 200

    # ALL도 동일
    assert pf["ALL"]["value"] == 1.5

    # Expectancy 검증
    # 승률 50%, 평균수익금 150, 평균손실금 100
    # Expectancy = (0.5 * 150) - (0.5 * 100) = 75 - 50 = 25
    exp = data["expectancies"]
    assert "S1" in exp
    assert exp["S1"]["value"] == 25.0
    assert exp["S1"]["win_rate"] == 50.0
    assert exp["S1"]["avg_gain"] == 150
    assert exp["S1"]["avg_loss"] == 100
    assert exp["S1"]["wins"] == 2
    assert exp["S1"]["losses"] == 2


@pytest.mark.asyncio
async def test_get_virtual_history_profit_factor_no_loss(web_client, mock_web_ctx):
    """GET /api/virtual/history Profit Factor 손실 없을 때 무한대(None) 반환"""
    web_api._PRICE_CACHE.clear()

    # 전부 수익
    mock_web_ctx.virtual_manager.get_all_trades.return_value = [
        {"code": "A", "strategy": "S1", "buy_price": 1000, "qty": 1, "status": "SOLD",
         "sell_price": 1500, "return_rate": 50.0, "buy_date": "2025-01-01 09:00:00"},
    ]

    mock_web_ctx.virtual_manager.save_daily_snapshot = MagicMock()
    mock_web_ctx.virtual_manager._load_data.return_value = {}
    mock_web_ctx.virtual_manager.get_daily_change.return_value = (None, None)
    mock_web_ctx.virtual_manager.get_weekly_change.return_value = (None, None)

    async def mock_multi_price(codes):
        return ResCommonResponse(rt_cd="0", msg1="OK", data=[])
    mock_web_ctx.stock_query_service.get_multi_price = mock_multi_price

    response = web_client.get("/api/virtual/history")
    assert response.status_code == 200
    data = response.json()

    pf = data["profit_factors"]
    assert pf["S1"]["value"] is None  # 무한대
    assert pf["S1"]["total_gain"] == 500
    assert pf["S1"]["total_loss"] == 0

    exp = data["expectancies"]
    assert exp["S1"]["value"] == 500.0  # 100% 승률 * 500원 평균수익
    assert exp["S1"]["win_rate"] == 100.0
    assert exp["S1"]["wins"] == 1
    assert exp["S1"]["losses"] == 0


@pytest.mark.asyncio
async def test_get_virtual_history_profit_factor_multi_strategy(web_client, mock_web_ctx):
    """GET /api/virtual/history 다중 전략 시 ALL 합산 PF/Expectancy 검증"""
    web_api._PRICE_CACHE.clear()

    mock_web_ctx.virtual_manager.get_all_trades.return_value = [
        # S1: 수익 200
        {"code": "A", "strategy": "S1", "buy_price": 1000, "qty": 1, "status": "SOLD",
         "sell_price": 1200, "return_rate": 20.0, "buy_date": "2025-01-01 09:00:00"},
        # S2: 손실 300
        {"code": "B", "strategy": "S2", "buy_price": 1000, "qty": 1, "status": "SOLD",
         "sell_price": 700, "return_rate": -30.0, "buy_date": "2025-01-01 09:00:00"},
    ]

    mock_web_ctx.virtual_manager.save_daily_snapshot = MagicMock()
    mock_web_ctx.virtual_manager._load_data.return_value = {}
    mock_web_ctx.virtual_manager.get_daily_change.return_value = (None, None)
    mock_web_ctx.virtual_manager.get_weekly_change.return_value = (None, None)

    async def mock_multi_price(codes):
        return ResCommonResponse(rt_cd="0", msg1="OK", data=[])
    mock_web_ctx.stock_query_service.get_multi_price = mock_multi_price

    response = web_client.get("/api/virtual/history")
    assert response.status_code == 200
    data = response.json()

    pf = data["profit_factors"]
    # S1: PF = 200/0 = None (무한대, 손실 없음)
    assert pf["S1"]["value"] is None
    # S2: PF = 0/300 = 0.0 (수익 없음)
    assert pf["S2"]["value"] == 0.0
    # ALL: PF = 200/300 = 0.67
    assert pf["ALL"]["value"] == 0.67
    assert pf["ALL"]["total_gain"] == 200
    assert pf["ALL"]["total_loss"] == 300

    exp = data["expectancies"]
    # ALL: 승률 50%, 평균수익금 200, 평균손실금 300
    # = (0.5 * 200) - (0.5 * 300) = 100 - 150 = -50
    assert exp["ALL"]["value"] == -50.0
    assert exp["ALL"]["win_rate"] == 50.0


@pytest.mark.asyncio
async def test_get_virtual_history_pf_with_hold_trades(web_client, mock_web_ctx):
    """GET /api/virtual/history HOLD 상태 거래도 PF/Expectancy에 포함 (현재가 기준)"""
    web_api._PRICE_CACHE.clear()

    mock_web_ctx.virtual_manager.get_all_trades.return_value = [
        {"code": "005930", "strategy": "S1", "buy_price": 1000, "qty": 10, "status": "HOLD",
         "return_rate": 0, "buy_date": "2025-01-01 09:00:00"},
    ]

    # 현재가 1200원 -> 수익 2000원 (200원 * 10주)
    async def mock_multi_price(codes):
        return ResCommonResponse(rt_cd="0", msg1="OK", data=[
            {"stck_shrn_iscd": "005930", "stck_prpr": "1200", "prdy_ctrt": "20.0"}
        ])
    mock_web_ctx.stock_query_service.get_multi_price = mock_multi_price

    mock_web_ctx.virtual_manager.save_daily_snapshot = MagicMock()
    mock_web_ctx.virtual_manager._load_data.return_value = {}
    mock_web_ctx.virtual_manager.get_daily_change.return_value = (None, None)
    mock_web_ctx.virtual_manager.get_weekly_change.return_value = (None, None)

    response = web_client.get("/api/virtual/history")
    assert response.status_code == 200
    data = response.json()

    pf = data["profit_factors"]
    # 수익만 있으므로 PF = None (무한대)
    assert pf["S1"]["value"] is None
    assert pf["S1"]["total_gain"] == 2000  # (1200-1000) * 10

    exp = data["expectancies"]
    assert exp["S1"]["value"] == 2000.0  # 1건, 100% 승률, 수익 2000
    assert exp["S1"]["wins"] == 1
    assert exp["S1"]["losses"] == 0
