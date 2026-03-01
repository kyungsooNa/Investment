"""
종목 조회 관련 테스트 (index.html — 현재가, 차트, 지표, 환경 전환).
"""
import pytest
from unittest.mock import AsyncMock
from common.types import ResCommonResponse


def test_get_status(web_client, mock_web_ctx):
    """GET /api/status 엔드포인트 테스트"""
    response = web_client.get("/api/status")

    assert response.status_code == 200
    assert response.json() == {
        "market_open": True,
        "env_type": "모의투자",
        "current_time": "2025-01-01 12:00:00",
        "initialized": True
    }


@pytest.mark.asyncio
async def test_get_stock_price(web_client, mock_web_ctx):
    """GET /api/stock/{code} 엔드포인트 테스트"""
    mock_web_ctx.stock_query_service.handle_get_current_stock_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="Success", data={"code": "005930", "price": 70000}
    )

    response = web_client.get("/api/stock/005930")

    assert response.status_code == 200
    json_resp = response.json()
    assert json_resp["rt_cd"] == "0"
    assert json_resp["data"]["price"] == 70000
    mock_web_ctx.stock_query_service.handle_get_current_stock_price.assert_awaited_once_with("005930")


@pytest.mark.asyncio
async def test_get_stock_chart(web_client, mock_web_ctx):
    """GET /api/chart/{code} 엔드포인트 테스트"""
    mock_web_ctx.stock_query_service.get_ohlcv.return_value = ResCommonResponse(
        rt_cd="0", msg1="Success", data=[{"date": "20250101", "close": 100}]
    )
    response = web_client.get("/api/chart/005930?period=D")
    assert response.status_code == 200
    assert response.json()["data"][0]["close"] == 100


@pytest.mark.asyncio
async def test_get_stock_chart_indicators(web_client, mock_web_ctx):
    """GET /api/chart/{code} indicators=True 테스트"""
    mock_web_ctx.stock_query_service.get_ohlcv_with_indicators.return_value = ResCommonResponse(
        rt_cd="0", msg1="Success", data={}
    )
    response = web_client.get("/api/chart/005930?period=D&indicators=true")
    assert response.status_code == 200
    mock_web_ctx.stock_query_service.get_ohlcv_with_indicators.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_bollinger_bands(web_client, mock_web_ctx):
    """GET /api/indicator/bollinger/{code} 엔드포인트 테스트"""
    from common.types import ResBollingerBand

    mock_web_ctx.indicator_service = AsyncMock()

    mock_band = ResBollingerBand(
        code="005930", date="20250101", close=70000.0,
        middle=69000.0, upper=71000.0, lower=67000.0
    )

    mock_web_ctx.indicator_service.get_bollinger_bands.return_value = ResCommonResponse(
        rt_cd="0", msg1="Success", data=[mock_band]
    )

    response = web_client.get("/api/indicator/bollinger/005930?period=20&std_dev=2.0")

    assert response.status_code == 200
    json_resp = response.json()
    assert json_resp["rt_cd"] == "0"
    assert isinstance(json_resp["data"], list)
    assert json_resp["data"][0]["code"] == "005930"
    assert json_resp["data"][0]["upper"] == 71000.0

    mock_web_ctx.indicator_service.get_bollinger_bands.assert_awaited_once_with("005930", 20, 2.0)


@pytest.mark.asyncio
async def test_get_rsi(web_client, mock_web_ctx):
    """GET /api/indicator/rsi/{code} 엔드포인트 테스트"""
    from common.types import ResRSI

    mock_web_ctx.indicator_service = AsyncMock()

    mock_rsi = ResRSI(code="005930", date="20250101", close=70000.0, rsi=65.5)

    mock_web_ctx.indicator_service.get_rsi.return_value = ResCommonResponse(
        rt_cd="0", msg1="Success", data=mock_rsi
    )

    response = web_client.get("/api/indicator/rsi/005930?period=14")

    assert response.status_code == 200
    assert response.json()["data"]["rsi"] == 65.5
    mock_web_ctx.indicator_service.get_rsi.assert_awaited_once_with("005930", 14)


@pytest.mark.asyncio
async def test_get_moving_average(web_client, mock_web_ctx):
    """GET /api/indicator/ma/{code} 엔드포인트 테스트"""
    if not hasattr(mock_web_ctx, 'indicator_service') or not isinstance(mock_web_ctx.indicator_service, AsyncMock):
        mock_web_ctx.indicator_service = AsyncMock()

    mock_web_ctx.indicator_service.get_moving_average.return_value = ResCommonResponse(
        rt_cd="0", msg1="Success", data=[{"ma": 100}]
    )
    response = web_client.get("/api/indicator/ma/005930")
    assert response.status_code == 200
    assert response.json()["data"][0]["ma"] == 100


@pytest.mark.asyncio
async def test_change_environment(web_client, mock_web_ctx):
    """POST /api/environment 엔드포인트 테스트"""
    mock_web_ctx.initialize_services = AsyncMock(return_value=True)
    mock_web_ctx.get_env_type.return_value = "실전투자"

    response = web_client.post("/api/environment", json={"is_paper": False})
    assert response.status_code == 200
    assert response.json()["success"] is True
    assert response.json()["env_type"] == "실전투자"
    mock_web_ctx.initialize_services.assert_awaited_once_with(is_paper_trading=False)


@pytest.mark.asyncio
async def test_change_environment_failure(web_client, mock_web_ctx):
    """POST /api/environment 실패 테스트"""
    mock_web_ctx.initialize_services = AsyncMock(return_value=False)
    response = web_client.post("/api/environment", json={"is_paper": True})
    assert response.status_code == 500
