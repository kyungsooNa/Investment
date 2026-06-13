"""
종목 조회 관련 테스트 (index.html — 현재가, 차트, 지표, 환경 전환).
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from common.overseas_types import OverseasExchange
from common.types import ResCommonResponse


def test_get_status(web_client, mock_web_ctx):
    """GET /api/status 엔드포인트 테스트"""
    response = web_client.get("/api/status")

    assert response.status_code == 200
    assert response.json() == {
        "market_open": True,
        "is_paper_trading": True,
        "env_type": "모의투자",
        "current_time": "2025-01-01 12:00:00",
        "initialized": True,
        "market_mode": "domestic",
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
    from common.types import Exchange
    mock_web_ctx.stock_query_service.handle_get_current_stock_price.assert_awaited_once_with("005930", caller="stock.py - get_stock_price", exchange=Exchange.KRX)


@pytest.mark.asyncio
async def test_get_stock_detail_success(web_client, mock_web_ctx):
    """GET /api/stock/{code}/detail — force_fresh=True로 handle_get_current_stock_price 호출"""
    mock_web_ctx.stock_query_service.handle_get_current_stock_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="Success",
        data={"code": "005930", "price": 70000, "per": 12.5, "bps": 50000}
    )

    response = web_client.get("/api/stock/005930/detail")

    assert response.status_code == 200
    json_resp = response.json()
    assert json_resp["rt_cd"] == "0"
    assert json_resp["data"]["per"] == 12.5

    from common.types import Exchange
    mock_web_ctx.stock_query_service.handle_get_current_stock_price.assert_awaited_once_with(
        "005930",
        caller="stock.py - get_stock_detail",
        exchange=Exchange.KRX,
        force_fresh=True,
    )


@pytest.mark.asyncio
async def test_get_stock_detail_api_failure(web_client, mock_web_ctx):
    """GET /api/stock/{code}/detail — 증권사 API 실패 시 rt_cd != 0 반환"""
    mock_web_ctx.stock_query_service.handle_get_current_stock_price.return_value = ResCommonResponse(
        rt_cd="1", msg1="API 오류", data=None
    )

    response = web_client.get("/api/stock/005930/detail")

    assert response.status_code == 200
    assert response.json()["rt_cd"] == "1"


@pytest.mark.asyncio
async def test_get_stock_detail_timeout(web_client, mock_web_ctx):
    """GET /api/stock/{code}/detail — 타임아웃 시 에러 메시지 반환"""
    import asyncio
    mock_web_ctx.stock_query_service.handle_get_current_stock_price.side_effect = asyncio.TimeoutError()

    response = web_client.get("/api/stock/005930/detail")

    assert response.status_code == 200
    json_resp = response.json()
    assert json_resp["rt_cd"] == "1"
    assert "초과" in json_resp["msg1"]


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

    response = web_client.post(
        "/api/environment",
        json={"is_paper": False, "real_mode_confirmation": "REAL"},
    )
    assert response.status_code == 200
    assert response.json()["success"] is True
    assert response.json()["env_type"] == "실전투자"
    mock_web_ctx.initialize_services.assert_awaited_once_with(is_paper_trading=False)


@pytest.mark.asyncio
async def test_change_environment_to_real_requires_confirmation(web_client, mock_web_ctx):
    mock_web_ctx.initialize_services = AsyncMock(return_value=True)

    response = web_client.post("/api/environment", json={"is_paper": False})

    assert response.status_code == 400
    mock_web_ctx.initialize_services.assert_not_awaited()


@pytest.mark.asyncio
async def test_change_environment_to_paper_does_not_require_confirmation(web_client, mock_web_ctx):
    mock_web_ctx.initialize_services = AsyncMock(return_value=True)
    mock_web_ctx.get_env_type.return_value = "paper"

    response = web_client.post("/api/environment", json={"is_paper": True})

    assert response.status_code == 200
    mock_web_ctx.initialize_services.assert_awaited_once_with(is_paper_trading=True)


@pytest.mark.asyncio
async def test_change_environment_failure(web_client, mock_web_ctx):
    """POST /api/environment 실패 테스트"""
    mock_web_ctx.initialize_services = AsyncMock(return_value=False)
    response = web_client.post("/api/environment", json={"is_paper": True})
    assert response.status_code == 500


@pytest.mark.asyncio
async def test_get_status_ctx_none(web_client, monkeypatch):
    """GET /api/status - ctx가 None일 때의 동작 테스트"""
    import view.web.routes.stock as stock
    monkeypatch.setattr(stock, "_get_ctx", lambda: None)
    
    response = web_client.get("/api/status")
    
    assert response.status_code == 200
    assert response.json() == {
        "market_open": False,
        "env_type": "미설정",
        "is_paper_trading": True,
        "current_time": "",
        "initialized": False,
        "market_mode": "domestic",
    }


@pytest.mark.asyncio
async def test_get_status_cached(web_client, mock_web_ctx):
    """GET /api/status - 캐시된 status가 갱신되어 반환되는지 테스트"""
    from view.web.routes import stock
    import time
    
    # 캐시 강제 설정 (TTL 이내)
    stock._status_cache = {
        "market_open": True,
        "env_type": "테스트",
        "current_time": "old_time",
        "initialized": True
    }
    stock._status_cache_ts = time.monotonic()
    
    mock_web_ctx.get_current_time_str.return_value = "new_time"
    response = web_client.get("/api/status")
    
    assert response.status_code == 200
    json_resp = response.json()
    assert json_resp["env_type"] == "테스트"
    # 캐시를 반환하되 시간은 갱신되었는지 확인
    assert json_resp["current_time"] == "new_time"
    assert json_resp["market_mode"] == "domestic"


@pytest.mark.asyncio
async def test_get_market_mode(web_client, mock_web_ctx):
    """GET /api/market-mode는 현재 market_mode를 반환한다."""
    mock_web_ctx.market_mode = "overseas_us"

    response = web_client.get("/api/market-mode")

    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "market_mode": "overseas_us",
        "requires_reinitialize": False,
    }


@pytest.mark.asyncio
async def test_change_market_mode_marks_reinitialize(web_client, mock_web_ctx):
    """POST /api/market-mode는 허용된 mode만 반영하고 재초기화 필요 여부를 반환한다."""
    response = web_client.post("/api/market-mode", json={"market_mode": "overseas_us"})

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert response.json()["market_mode"] == "overseas_us"
    assert response.json()["requires_reinitialize"] is True
    assert mock_web_ctx.market_mode == "overseas_us"


@pytest.mark.asyncio
async def test_change_market_mode_invalid(web_client, mock_web_ctx):
    """POST /api/market-mode는 domestic/overseas_us 외 값을 거부한다."""
    response = web_client.post("/api/market-mode", json={"market_mode": "crypto"})

    assert response.status_code == 400
    assert mock_web_ctx.market_mode == "domestic"


@pytest.mark.asyncio
async def test_get_overseas_stock_price_calls_service(web_client, mock_web_ctx):
    """GET /api/overseas/stock/{symbol}은 해외 조회 서비스를 호출한다."""
    mock_web_ctx.stock_query_service.get_overseas_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="Success", data={"symbol": "AAPL", "price": 190.0}
    )

    response = web_client.get("/api/overseas/stock/AAPL?exchange=NASD")

    assert response.status_code == 200
    assert response.json()["data"]["symbol"] == "AAPL"
    mock_web_ctx.stock_query_service.get_overseas_price.assert_awaited_once_with(
        "AAPL", exchange=OverseasExchange.NASD
    )


@pytest.mark.asyncio
async def test_get_overseas_stock_chart_calls_service(web_client, mock_web_ctx):
    """GET /api/overseas/chart/{symbol}은 해외 일봉 조회 서비스를 호출한다."""
    mock_web_ctx.stock_query_service.get_overseas_dailyprice.return_value = ResCommonResponse(
        rt_cd="0", msg1="Success", data=[{"date": "20250101", "close": 190.0}]
    )

    response = web_client.get("/api/overseas/chart/AAPL?exchange=NYSE&period=W")

    assert response.status_code == 200
    assert response.json()["data"][0]["close"] == 190.0
    mock_web_ctx.stock_query_service.get_overseas_dailyprice.assert_awaited_once_with(
        "AAPL",
        exchange=OverseasExchange.NYSE,
        period="W",
        start_date="",
        end_date="",
    )


@pytest.mark.asyncio
async def test_get_stocks_list(web_client, mock_web_ctx):
    """GET /api/stocks/list 엔드포인트 테스트"""
    mock_web_ctx.stock_code_repository.name_to_code = {"삼성전자": "005930", "카카오": "035720"}
    response = web_client.get("/api/stocks/list")
    assert response.status_code == 200
    json_resp = response.json()
    assert json_resp["count"] == 2
    assert {"c": "005930", "n": "삼성전자"} in json_resp["stocks"]


@pytest.mark.asyncio
async def test_search_stock_by_name(web_client, mock_web_ctx):
    """GET /api/stock/search 엔드포인트 테스트 (빈 쿼리 및 정상 쿼리)"""
    # 빈 문자열 검색
    response_empty = web_client.get("/api/stock/search?q=   ")
    assert response_empty.status_code == 200
    assert response_empty.json()["results"] == []

    # 정상 검색
    mock_web_ctx.stock_code_repository.search_by_name.return_value = [{"c": "005930", "n": "삼성전자"}]
    response_valid = web_client.get("/api/stock/search?q=삼성")
    assert response_valid.status_code == 200
    assert response_valid.json()["results"] == [{"c": "005930", "n": "삼성전자"}]
    mock_web_ctx.stock_code_repository.search_by_name.assert_called_once_with("삼성")


@pytest.mark.asyncio
async def test_get_stock_price_by_name_not_found(web_client, mock_web_ctx):
    """GET /api/stock/{name} - 이름으로 검색 실패 시 에러 반환 테스트"""
    mock_web_ctx.stock_code_repository.get_code_by_name.return_value = None
    response = web_client.get("/api/stock/없는종목")
    
    assert response.status_code == 200
    json_resp = response.json()
    assert json_resp["rt_cd"] == "1"
    assert "찾을 수 없습니다" in json_resp["msg1"]


@pytest.mark.asyncio
async def test_change_environment_ctx_none(web_client, monkeypatch):
    """POST /api/environment - ctx가 None일 때의 503 동작 테스트"""
    import view.web.api_common as api_common
    monkeypatch.setattr(api_common, "_ctx", None)
    
    response = web_client.post(
        "/api/environment",
        json={"is_paper": False, "real_mode_confirmation": "REAL"},
    )
    assert response.status_code == 503
    assert response.json()["detail"] == "서비스가 초기화되지 않았습니다."


@pytest.mark.asyncio
async def test_get_stock_rs_rating_disabled_and_success(web_client, mock_web_ctx):
    """RS Rating 서비스 비활성화/정상 응답 분기 검증."""
    mock_web_ctx.rs_rating_service = None
    response = web_client.get("/api/stock/005930/rs_rating")
    assert response.status_code == 200
    assert "비활성화" in response.json()["msg1"]

    mock_web_ctx.rs_rating_service = MagicMock()
    mock_web_ctx.rs_rating_service.get_rating = AsyncMock(
        return_value=ResCommonResponse(rt_cd="0", msg1="OK", data={"code": "005930", "rating": 95})
    )
    response = web_client.get("/api/stock/005930/rs_rating")
    assert response.status_code == 200
    assert response.json()["data"]["rating"] == 95


@pytest.mark.asyncio
async def test_get_stock_stage_disabled_and_success(web_client, mock_web_ctx):
    """Minervini Stage 서비스 비활성화/정상 응답 분기 검증."""
    mock_web_ctx.minervini_stage_service = None
    response = web_client.get("/api/stock/005930/stage")
    assert response.status_code == 200
    assert "비활성화" in response.json()["msg1"]

    mock_web_ctx.minervini_stage_service = MagicMock()
    mock_web_ctx.minervini_stage_service.get_stage_for_code = AsyncMock(return_value=(2, "상승 추세"))
    response = web_client.get("/api/stock/005930/stage")
    assert response.status_code == 200
    assert response.json()["data"]["stage"] == 2
    assert response.json()["data"]["reason"] == "상승 추세"


@pytest.mark.asyncio
async def test_get_stock_detail_invalid_exchange_falls_back_to_krx(web_client, mock_web_ctx):
    """상세조회 exchange 값이 잘못되면 KRX로 폴백한다."""
    mock_web_ctx.stock_query_service.handle_get_current_stock_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="Success", data={"code": "005930"}
    )
    response = web_client.get("/api/stock/005930/detail?exchange=INVALID")
    assert response.status_code == 200

    from common.types import Exchange
    mock_web_ctx.stock_query_service.handle_get_current_stock_price.assert_awaited_once_with(
        "005930",
        caller="stock.py - get_stock_detail",
        exchange=Exchange.KRX,
        force_fresh=True,
    )


@pytest.mark.asyncio
async def test_get_stock_price_timeout_and_invalid_exchange(web_client, mock_web_ctx):
    """현재가 조회 타임아웃과 exchange 폴백 분기 검증."""
    import asyncio
    mock_web_ctx.stock_query_service.handle_get_current_stock_price.side_effect = asyncio.TimeoutError()
    response = web_client.get("/api/stock/005930?exchange=BAD")
    assert response.status_code == 200
    assert "초과" in response.json()["msg1"]


@pytest.mark.asyncio
async def test_get_stock_price_by_name_and_background_tasks(web_client, mock_web_ctx):
    """종목명 해석 및 background task 생성 분기 검증."""
    mock_web_ctx.stock_code_repository.get_code_by_name.return_value = "005930"
    mock_web_ctx.stock_query_service.handle_get_current_stock_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="Success", data={"price": 70000}
    )
    mock_web_ctx.price_subscription_service = MagicMock()
    mock_web_ctx.price_subscription_service.add_subscription = AsyncMock()
    mock_web_ctx.stock_query_service.get_ohlcv = AsyncMock()

    created = []
    def fake_create_task(coro):
        created.append(coro)
        coro.close()
        return MagicMock()

    with patch("view.web.routes.stock.asyncio.create_task", side_effect=fake_create_task):
        response = web_client.get("/api/stock/삼성전자")

    assert response.status_code == 200
    assert len(created) == 2


@pytest.mark.asyncio
async def test_get_stock_chart_invalid_exchange_fallback(web_client, mock_web_ctx):
    """차트 조회 exchange 값이 잘못되면 KRX로 폴백한다."""
    mock_web_ctx.stock_query_service.get_ohlcv.return_value = ResCommonResponse(
        rt_cd="0", msg1="Success", data=[]
    )
    response = web_client.get("/api/chart/005930?exchange=BAD")
    assert response.status_code == 200

    from common.types import Exchange
    mock_web_ctx.stock_query_service.get_ohlcv.assert_awaited_once_with(
        "005930", "D", caller="stock.py - get_stock_chart", exchange=Exchange.KRX
    )
