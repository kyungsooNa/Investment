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

    mock_web_ctx.virtual_manager.get_daily_change.return_value = 0.0
    mock_web_ctx.virtual_manager.get_weekly_change.return_value = 0.0
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
    mock_web_ctx.virtual_manager.get_daily_change.return_value = 0.0
    mock_web_ctx.virtual_manager.get_weekly_change.return_value = 0.0

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
