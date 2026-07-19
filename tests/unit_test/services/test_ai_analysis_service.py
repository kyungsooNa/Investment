import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from common.types import ErrorCode
from services.ai_analysis_service import AIAnalysisService


@pytest.mark.asyncio
async def test_analyze_leading_stocks_uses_shared_ai_client():
    ai_client = MagicMock()
    ai_client.complete = AsyncMock(
        return_value="삼성전자는 거래대금과 RS가 강합니다."
    )
    service = AIAnalysisService(
        ai_client,
        provider_name="gemini",
        model="gemini-test",
        max_tokens=1536,
    )

    resp = await service.analyze_leading_stocks(
        candidates=[
            {
                "code": "005930",
                "name": "삼성전자",
                "change_rate": 4.2,
                "rs_rating": 92,
                "trading_value_rank": 3,
            }
        ],
        market_context={"trade_date": "20260707", "market": "KRX"},
    )

    assert resp.rt_cd == ErrorCode.SUCCESS.value
    assert resp.data == {
        "analysis": "삼성전자는 거래대금과 RS가 강합니다.",
        "provider": "gemini",
        "model": "gemini-test",
        "candidate_count": 1,
    }
    call = ai_client.complete.await_args.kwargs
    assert "제공된 데이터만" in call["system"]
    assert call["max_tokens"] == 1536
    assert call["temperature"] == 0.2
    payload = json.loads(call["user"])
    assert payload["analysis_type"] == "leading_stock_candidates"
    assert payload["market_context"]["trade_date"] == "20260707"
    assert payload["candidates"][0]["code"] == "005930"


@pytest.mark.asyncio
async def test_analyze_leading_stocks_limits_candidates_before_api_call():
    ai_client = MagicMock()
    ai_client.complete = AsyncMock(return_value="분석 결과")
    service = AIAnalysisService(
        ai_client,
        provider_name="gemini",
        model="gemini-test",
    )
    candidates = [
        {"code": "000001", "name": "A"},
        {"code": "000002", "name": "B"},
        {"code": "000003", "name": "C"},
    ]

    resp = await service.analyze_leading_stocks(candidates, max_candidates=2)

    assert resp.rt_cd == ErrorCode.SUCCESS.value
    payload = json.loads(ai_client.complete.await_args.kwargs["user"])
    assert [item["code"] for item in payload["candidates"]] == ["000001", "000002"]


@pytest.mark.asyncio
async def test_analyze_leading_stocks_returns_empty_values_without_candidates():
    ai_client = MagicMock()
    ai_client.complete = AsyncMock()
    service = AIAnalysisService(
        ai_client,
        provider_name="gemini",
        model="gemini-test",
    )

    resp = await service.analyze_leading_stocks([])

    assert resp.rt_cd == ErrorCode.EMPTY_VALUES.value
    ai_client.complete.assert_not_awaited()


@pytest.mark.asyncio
async def test_analyze_leading_stocks_returns_generic_api_error():
    ai_client = MagicMock()
    ai_client.complete = AsyncMock(side_effect=RuntimeError("secret provider detail"))
    logger = MagicMock()
    service = AIAnalysisService(
        ai_client,
        provider_name="gemini",
        model="gemini-test",
        logger=logger,
    )

    resp = await service.analyze_leading_stocks(
        [{"code": "005930", "name": "삼성전자"}]
    )

    assert resp.rt_cd == ErrorCode.API_ERROR.value
    assert resp.msg1 == "AI 분석 요청에 실패했습니다."
    assert "secret provider detail" not in resp.msg1
    logger.exception.assert_called_once()


@pytest.mark.asyncio
async def test_analyze_leading_stocks_returns_api_error_when_output_text_missing():
    ai_client = MagicMock()
    ai_client.complete = AsyncMock(return_value=" ")
    service = AIAnalysisService(
        ai_client,
        provider_name="gemini",
        model="gemini-test",
    )

    resp = await service.analyze_leading_stocks(
        [{"code": "005930", "name": "삼성전자"}]
    )

    assert resp.rt_cd == ErrorCode.API_ERROR.value
    assert resp.data is None
