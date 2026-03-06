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

    mock_web_ctx.stock_query_service.trading_service.get_ohlcv_range.return_value = ResCommonResponse(
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

    mock_web_ctx.stock_query_service.trading_service.get_ohlcv_range.return_value = ResCommonResponse(
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

    mock_web_ctx.stock_query_service.trading_service.get_ohlcv_range.return_value = ResCommonResponse(
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

    mock_web_ctx.stock_query_service.trading_service.get_ohlcv_range.return_value = ResCommonResponse(
        rt_cd="0", msg1="Success", data=[{"date": "20250101", "close": "invalid"}]
    )

    response = web_client.get("/api/virtual/chart/ALL")
    assert response.status_code == 200
    data = response.json()
    assert data["benchmarks"]["KOSPI200"][0]["return_rate"] == 0


@pytest.mark.asyncio
async def test_get_virtual_history_complex(web_client, mock_web_ctx):
    """GET /api/virtual/history 복합 테스트 (캐시, SOLD 보정, 매니저 없음)"""
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
    mock_web_ctx.stock_query_service.trading_service.get_ohlcv_range.return_value = ResCommonResponse(
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
