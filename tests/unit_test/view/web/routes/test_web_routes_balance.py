"""
계좌 잔고 관련 테스트 (balance.html).
"""
import pytest
from unittest.mock import MagicMock
from common.types import ResCommonResponse


@pytest.mark.asyncio
async def test_get_balance(web_client, mock_web_ctx):
    """GET /api/balance 엔드포인트 테스트"""
    mock_web_ctx.stock_query_service.handle_get_account_balance.return_value = ResCommonResponse(
        rt_cd="0", msg1="Success", data={"output1": [], "output2": []}
    )

    response = web_client.get("/api/balance")
    assert response.status_code == 200
    json_resp = response.json()
    assert json_resp["rt_cd"] == "0"
    assert "account_info" in json_resp
    assert json_resp["account_info"]["type"] == "모의투자"


@pytest.mark.asyncio
async def test_get_balance_fallback_env(web_client, mock_web_ctx):
    """GET /api/balance 환경 설정 폴백 테스트"""
    mock_web_ctx.stock_query_service.handle_get_account_balance.return_value = ResCommonResponse(
        rt_cd="0", msg1="Success", data={}
    )

    # ctx.env 제거 (임시)
    original_env = mock_web_ctx.env
    del mock_web_ctx.env

    # broker.env 설정
    mock_web_ctx.broker.env = MagicMock()
    mock_web_ctx.broker.env.active_config = {"stock_account_number": "9999"}
    mock_web_ctx.broker.env.is_paper_trading = True

    try:
        response = web_client.get("/api/balance")
        assert response.status_code == 200
        assert response.json()["account_info"]["number"] == "9999"
    finally:
        mock_web_ctx.env = original_env


@pytest.mark.asyncio
async def test_get_balance_no_env(web_client, mock_web_ctx):
    """GET /api/balance 환경 설정 없음 테스트"""
    if hasattr(mock_web_ctx, 'env'): del mock_web_ctx.env
    if hasattr(mock_web_ctx, 'broker'): del mock_web_ctx.broker

    mock_web_ctx.stock_query_service.handle_get_account_balance.return_value = ResCommonResponse(
        rt_cd="0", msg1="Success", data={}
    )

    response = web_client.get("/api/balance")
    assert response.status_code == 200
    assert response.json()["account_info"]["type"] == "Env Not Found"


@pytest.mark.asyncio
async def test_get_balance_full_config_fallback(web_client, mock_web_ctx):
    """GET /api/balance active_config 없음 -> get_full_config 폴백 테스트"""
    mock_web_ctx.env.active_config = None
    mock_web_ctx.env.get_full_config = MagicMock(return_value={"stock_account_number": "1234-fallback"})

    mock_web_ctx.stock_query_service.handle_get_account_balance.return_value = ResCommonResponse(
        rt_cd="0", msg1="Success", data={}
    )

    response = web_client.get("/api/balance")
    assert response.status_code == 200
    assert response.json()["account_info"]["number"] == "1234-fallback"


@pytest.mark.asyncio
async def test_get_balance_full_config_exception(web_client, mock_web_ctx):
    """GET /api/balance get_full_config 예외 발생 테스트"""
    mock_web_ctx.env.active_config = None
    mock_web_ctx.env.get_full_config.side_effect = Exception("Config Error")
    mock_web_ctx.env.stock_account_number = None
    mock_web_ctx.env.paper_stock_account_number = None

    mock_web_ctx.stock_query_service.handle_get_account_balance.return_value = ResCommonResponse(
        rt_cd="0", msg1="Success", data={}
    )

    response = web_client.get("/api/balance")
    assert response.status_code == 200
    assert response.json()["account_info"]["number"] == "번호없음"
