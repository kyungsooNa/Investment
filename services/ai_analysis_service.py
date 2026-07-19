"""랭킹 후보를 공통 AI 클라이언트로 해설하는 서비스."""
from __future__ import annotations

import json
import logging
from typing import Any, Mapping, Sequence

from common.types import ErrorCode, ResCommonResponse
from services.ai_usage_limiter import AiUsageLimitExceeded


_LEADING_STOCK_INSTRUCTIONS = """
너는 한국 주식 자동매매 시스템의 주도주 후보 분석 보조자다.
제공된 데이터만 근거로 판단하고, 데이터에 없는 뉴스/재무/수급을 추측하지 않는다.
매수/매도 지시가 아니라 후보 해설, 강한 근거, 약한 근거, 리스크, 추가 확인 포인트만 작성한다.
불확실하거나 데이터가 부족하면 그 사실을 명확히 말한다.
응답은 한국어로 간결하게 작성한다.
""".strip()


class AIAnalysisService:
    """정량 스캐너 결과를 공통 OpenAI 호환 AI 클라이언트로 해설한다."""

    def __init__(
        self,
        ai_client,
        *,
        provider_name: str,
        model: str,
        logger=None,
        max_tokens: int = 2048,
    ) -> None:
        self._client = ai_client
        self._provider_name = str(provider_name or "")
        self._model = str(model or "")
        self._logger = logger or logging.getLogger(__name__)
        self._max_tokens = int(max_tokens)

    async def analyze_leading_stocks(
        self,
        candidates: Sequence[Mapping[str, Any]],
        *,
        market_context: Mapping[str, Any] | None = None,
        max_candidates: int = 20,
    ) -> ResCommonResponse:
        candidate_list = [dict(item) for item in candidates or []]
        if not candidate_list:
            return ResCommonResponse(
                rt_cd=ErrorCode.EMPTY_VALUES.value,
                msg1="AI 분석 대상 후보가 없습니다.",
                data=None,
            )

        limited_candidates = candidate_list[: max(1, int(max_candidates))]
        input_text = json.dumps(
            {
                "analysis_type": "leading_stock_candidates",
                "candidate_count": len(limited_candidates),
                "market_context": dict(market_context or {}),
                "candidates": limited_candidates,
            },
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        try:
            output_text = await self._client.complete(
                system=_LEADING_STOCK_INSTRUCTIONS,
                user=input_text,
                max_tokens=self._max_tokens,
                temperature=0.2,
                usage_type="ranking",
            )
            output_text = str(output_text or "").strip()
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
                    "provider": self._provider_name,
                    "model": self._model,
                    "candidate_count": len(limited_candidates),
                },
            )
        except AiUsageLimitExceeded:
            raise
        except Exception as exc:
            self._logger.exception(
                f"AIAnalysisService.analyze_leading_stocks 오류: {exc}"
            )
            return ResCommonResponse(
                rt_cd=ErrorCode.API_ERROR.value,
                msg1="AI 분석 요청에 실패했습니다.",
                data=None,
            )
