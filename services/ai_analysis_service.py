"""OpenAI Responses API 기반 얇은 분석 서비스."""
from __future__ import annotations

import inspect
import json
import logging
import os
from typing import Any, Mapping, Sequence

from common.types import ErrorCode, ResCommonResponse


DEFAULT_OPENAI_ANALYSIS_MODEL = "gpt-5.5"

_LEADING_STOCK_INSTRUCTIONS = """
너는 한국 주식 자동매매 시스템의 주도주 후보 분석 보조자다.
제공된 데이터만 근거로 판단하고, 데이터에 없는 뉴스/재무/수급을 추측하지 않는다.
매수/매도 지시가 아니라 후보 해설, 강한 근거, 약한 근거, 리스크, 추가 확인 포인트만 작성한다.
불확실하거나 데이터가 부족하면 그 사실을 명확히 말한다.
응답은 한국어로 간결하게 작성한다.
""".strip()


class AIAnalysisService:
    """정량 스캐너 결과를 AI 분석 텍스트로 변환하는 얇은 어댑터."""

    def __init__(
        self,
        client: Any | None = None,
        model: str | None = None,
        logger=None,
    ):
        self._client = client or self._create_default_client()
        self._model = model or os.getenv("OPENAI_ANALYSIS_MODEL") or DEFAULT_OPENAI_ANALYSIS_MODEL
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
            result = self._client.responses.create(
                model=self._model,
                instructions=_LEADING_STOCK_INSTRUCTIONS,
                input=input_text,
            )
            if inspect.isawaitable(result):
                result = await result

            output_text = self._extract_output_text(result)
            if not output_text:
                return ResCommonResponse(
                    rt_cd=ErrorCode.API_ERROR.value,
                    msg1="OpenAI 응답에서 분석 텍스트를 찾을 수 없습니다.",
                    data=None,
                )

            return ResCommonResponse(
                rt_cd=ErrorCode.SUCCESS.value,
                msg1="AI 분석 성공",
                data={
                    "analysis": output_text,
                    "model": self._model,
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
