"""AI provider 기반 얇은 분석 서비스."""
from __future__ import annotations

import inspect
import json
import logging
import os
from typing import Any, Mapping, Protocol, Sequence

from common.types import ErrorCode, ResCommonResponse


DEFAULT_AI_ANALYSIS_PROVIDER = "gemini"
DEFAULT_GEMINI_ANALYSIS_MODEL = "gemini-3.1-flash-lite"
DEFAULT_OPENAI_ANALYSIS_MODEL = "gpt-5.5"

_LEADING_STOCK_INSTRUCTIONS = """
너는 한국 주식 자동매매 시스템의 주도주 후보 분석 보조자다.
제공된 데이터만 근거로 판단하고, 데이터에 없는 뉴스/재무/수급을 추측하지 않는다.
매수/매도 지시가 아니라 후보 해설, 강한 근거, 약한 근거, 리스크, 추가 확인 포인트만 작성한다.
불확실하거나 데이터가 부족하면 그 사실을 명확히 말한다.
응답은 한국어로 간결하게 작성한다.
""".strip()


class AITextProvider(Protocol):
    name: str
    model: str

    async def generate_analysis(self, instructions: str, input_text: str) -> str:
        """분석 지침과 입력 텍스트를 받아 분석 결과 텍스트를 반환한다."""


class OpenAIAnalysisProvider:
    """OpenAI Responses API provider."""

    name = "openai"

    def __init__(self, client: Any | None = None, model: str | None = None):
        self._client = client or self._create_default_client()
        self.model = (
            model
            or os.getenv("AI_ANALYSIS_MODEL")
            or os.getenv("OPENAI_ANALYSIS_MODEL")
            or DEFAULT_OPENAI_ANALYSIS_MODEL
        )

    async def generate_analysis(self, instructions: str, input_text: str) -> str:
        result = self._client.responses.create(
            model=self.model,
            instructions=instructions,
            input=input_text,
        )
        if inspect.isawaitable(result):
            result = await result
        return self._extract_output_text(result)

    @staticmethod
    def _extract_output_text(response: Any) -> str:
        output_text = getattr(response, "output_text", None)
        if output_text:
            return str(output_text).strip()
        return ""

    @staticmethod
    def _create_default_client() -> Any:
        try:
            from openai import AsyncOpenAI
        except ImportError as e:
            raise RuntimeError("openai 패키지가 필요합니다. requirements.txt를 설치하세요.") from e
        return AsyncOpenAI()


class GeminiAnalysisProvider:
    """Google GenAI SDK 기반 Gemini provider."""

    name = "gemini"

    def __init__(self, client: Any | None = None, model: str | None = None):
        self._client = client or self._create_default_client()
        self.model = (
            model
            or os.getenv("AI_ANALYSIS_MODEL")
            or os.getenv("GEMINI_ANALYSIS_MODEL")
            or DEFAULT_GEMINI_ANALYSIS_MODEL
        )

    async def generate_analysis(self, instructions: str, input_text: str) -> str:
        result = self._client.aio.models.generate_content(
            model=self.model,
            contents=input_text,
            config={"system_instruction": instructions},
        )
        if inspect.isawaitable(result):
            result = await result
        return self._extract_output_text(result)

    @staticmethod
    def _extract_output_text(response: Any) -> str:
        output_text = getattr(response, "text", None)
        if output_text:
            return str(output_text).strip()
        return ""

    @staticmethod
    def _create_default_client() -> Any:
        try:
            from google import genai
        except ImportError as e:
            raise RuntimeError("google-genai 패키지가 필요합니다. requirements.txt를 설치하세요.") from e

        api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if api_key:
            return genai.Client(api_key=api_key)
        return genai.Client()


class AIAnalysisService:
    """정량 스캐너 결과를 AI 분석 텍스트로 변환하는 얇은 어댑터."""

    def __init__(
        self,
        provider: AITextProvider | None = None,
        client: Any | None = None,
        model: str | None = None,
        provider_name: str | None = None,
        logger=None,
    ):
        self._provider = provider or self._create_provider(
            provider_name=self.resolve_provider_name(
                provider_name=provider_name,
                default_to_openai=client is not None,
            ),
            client=client,
            model=model,
        )
        self._logger = logger or logging.getLogger(__name__)

    async def analyze_leading_stocks(
        self,
        candidates: Sequence[Mapping[str, Any]],
        *,
        market_context: Mapping[str, Any] | None = None,
        max_candidates: int = 20,
    ) -> ResCommonResponse:
        """주도주 후보 목록을 OpenAI Responses API로 해설한다."""
        candidate_list = [dict(item) for item in candidates or []]
        if not candidate_list:
            return ResCommonResponse(
                rt_cd=ErrorCode.EMPTY_VALUES.value,
                msg1="AI 분석 대상 후보가 없습니다.",
                data=None,
            )

        limited_candidates = candidate_list[:max(1, max_candidates)]
        payload = {
            "analysis_type": "leading_stock_candidates",
            "candidate_count": len(limited_candidates),
            "market_context": dict(market_context or {}),
            "candidates": limited_candidates,
        }
        input_text = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)

        try:
            output_text = await self._provider.generate_analysis(
                _LEADING_STOCK_INSTRUCTIONS,
                input_text,
            )
            if not output_text:
                return ResCommonResponse(
                    rt_cd=ErrorCode.API_ERROR.value,
                    msg1="AI 응답에서 분석 텍스트를 찾을 수 없습니다.",
                    data=None,
                )

            return ResCommonResponse(
                rt_cd=ErrorCode.SUCCESS.value,
                msg1="AI 분석 성공",
                data={
                    "analysis": output_text,
                    "provider": self._provider.name,
                    "model": self._provider.model,
                    "candidate_count": len(limited_candidates),
                },
            )
        except Exception as e:
            self._logger.exception(f"AIAnalysisService.analyze_leading_stocks 오류: {e}")
            return ResCommonResponse(
                rt_cd=ErrorCode.API_ERROR.value,
                msg1=str(e),
                data=None,
            )

    @staticmethod
    def resolve_provider_name(
        provider_name: str | None = None,
        *,
        default_to_openai: bool = False,
    ) -> str:
        raw = provider_name or os.getenv("AI_ANALYSIS_PROVIDER")
        if raw:
            return raw.strip().lower()
        if default_to_openai:
            return "openai"
        return DEFAULT_AI_ANALYSIS_PROVIDER

    @staticmethod
    def _create_provider(
        *,
        provider_name: str,
        client: Any | None = None,
        model: str | None = None,
    ) -> AITextProvider:
        if provider_name == "openai":
            return OpenAIAnalysisProvider(client=client, model=model)
        if provider_name == "gemini":
            return GeminiAnalysisProvider(client=client, model=model)
        raise ValueError(f"지원하지 않는 AI 분석 provider입니다: {provider_name}")
