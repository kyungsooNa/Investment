import json
from types import SimpleNamespace

import pytest

from common.types import ErrorCode
from services.ai_analysis_service import (
    AIAnalysisService,
    GeminiAnalysisProvider,
    OpenAIAnalysisProvider,
)


class _FakeProvider:
    name = "fake"
    model = "fake-model"

    def __init__(self, output_text="분석 결과"):
        self.output_text = output_text
        self.calls = []

    async def generate_analysis(self, instructions: str, input_text: str) -> str:
        self.calls.append({"instructions": instructions, "input_text": input_text})
        return self.output_text


class _FakeOpenAIResponses:
    def __init__(self, output_text="분석 결과"):
        self.output_text = output_text
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(output_text=self.output_text)


class _FakeOpenAIClient:
    def __init__(self, output_text="분석 결과"):
        self.responses = _FakeOpenAIResponses(output_text=output_text)


class _FakeGeminiModels:
    def __init__(self, output_text="분석 결과"):
        self.output_text = output_text
        self.calls = []

    async def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(text=self.output_text)


class _FakeGeminiAio:
    def __init__(self, output_text="분석 결과"):
        self.models = _FakeGeminiModels(output_text=output_text)


class _FakeGeminiClient:
    def __init__(self, output_text="분석 결과"):
        self.aio = _FakeGeminiAio(output_text=output_text)


@pytest.mark.asyncio
async def test_analyze_leading_stocks_sends_compact_json_to_provider():
    provider = _FakeProvider(output_text="삼성전자는 거래대금과 RS가 강합니다.")
    service = AIAnalysisService(provider=provider)

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
    assert resp.data["analysis"] == "삼성전자는 거래대금과 RS가 강합니다."
    assert resp.data["model"] == "fake-model"
    assert resp.data["provider"] == "fake"
    assert resp.data["candidate_count"] == 1

    call = provider.calls[0]
    assert "제공된 데이터만" in call["instructions"]

    payload = json.loads(call["input_text"])
    assert payload["analysis_type"] == "leading_stock_candidates"
    assert payload["market_context"]["trade_date"] == "20260707"
    assert payload["candidates"][0]["code"] == "005930"


@pytest.mark.asyncio
async def test_analyze_leading_stocks_limits_candidates_before_api_call():
    provider = _FakeProvider()
    service = AIAnalysisService(provider=provider)

    candidates = [
        {"code": "000001", "name": "A"},
        {"code": "000002", "name": "B"},
        {"code": "000003", "name": "C"},
    ]
    resp = await service.analyze_leading_stocks(candidates, max_candidates=2)

    assert resp.rt_cd == ErrorCode.SUCCESS.value
    payload = json.loads(provider.calls[0]["input_text"])
    assert [item["code"] for item in payload["candidates"]] == ["000001", "000002"]


@pytest.mark.asyncio
async def test_analyze_leading_stocks_returns_empty_values_without_candidates():
    provider = _FakeProvider()
    service = AIAnalysisService(provider=provider)

    resp = await service.analyze_leading_stocks([])

    assert resp.rt_cd == ErrorCode.EMPTY_VALUES.value
    assert provider.calls == []


@pytest.mark.asyncio
async def test_analyze_leading_stocks_returns_api_error_when_output_text_missing():
    provider = _FakeProvider(output_text="")
    service = AIAnalysisService(provider=provider)

    resp = await service.analyze_leading_stocks([{"code": "005930", "name": "삼성전자"}])

    assert resp.rt_cd == ErrorCode.API_ERROR.value
    assert resp.data is None


@pytest.mark.asyncio
async def test_openai_provider_uses_responses_api():
    client = _FakeOpenAIClient(output_text="OpenAI 분석")
    provider = OpenAIAnalysisProvider(client=client, model="gpt-test")

    result = await provider.generate_analysis("지침", "입력")

    assert result == "OpenAI 분석"
    assert provider.name == "openai"
    assert provider.model == "gpt-test"
    assert client.responses.calls == [{
        "model": "gpt-test",
        "instructions": "지침",
        "input": "입력",
    }]


@pytest.mark.asyncio
async def test_gemini_provider_uses_generate_content_with_system_instruction():
    client = _FakeGeminiClient(output_text="Gemini 분석")
    provider = GeminiAnalysisProvider(client=client, model="gemini-test")

    result = await provider.generate_analysis("지침", "입력")

    assert result == "Gemini 분석"
    assert provider.name == "gemini"
    assert provider.model == "gemini-test"

    call = client.aio.models.calls[0]
    assert call["model"] == "gemini-test"
    assert call["contents"] == "입력"
    assert call["config"]["system_instruction"] == "지침"


def test_default_provider_is_gemini(monkeypatch):
    monkeypatch.delenv("AI_ANALYSIS_PROVIDER", raising=False)

    assert AIAnalysisService.resolve_provider_name() == "gemini"


def test_provider_name_can_be_overridden_by_env(monkeypatch):
    monkeypatch.setenv("AI_ANALYSIS_PROVIDER", "openai")

    assert AIAnalysisService.resolve_provider_name() == "openai"
