"""Gemini YouTube URL fallback analyzer.

`youtube-transcript-api` 가 IP 차단을 맞았을 때 원문 자막 수집 대신 공개
YouTube URL을 Gemini video input으로 넘겨 최소 리포트를 만든다.
"""
from __future__ import annotations

import logging
from typing import Any, Optional, Sequence

import httpx

from common.types import ErrorCode, ResCommonResponse

_INTERACTIONS_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"

_VIDEO_PROMPT = (
    "이 영상은 한국 주식 유튜브 방송이다. 자막 원문을 보존할 수 없는 장애 fallback "
    "상황이므로, 영상의 발언만 근거로 한국어 요약을 작성한다. "
    "매수/매도 지시를 하지 말고 출연자 발언 정리임을 유지한다. "
    "'핵심 주장', '언급 종목과 이유', '시장 전망', '리스크 언급' 순서로 짧게 정리한다."
)


class GeminiYoutubeVideoAnalyzerService:
    """공개 YouTube URL을 Gemini 네이티브 video input으로 요약한다."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "gemini-2.5-flash",
        http_client: Optional[httpx.AsyncClient] = None,
        timeout_sec: float = 60.0,
        usage_limiter=None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._api_key = str(api_key or "").strip()
        self._model = str(model or "gemini-2.5-flash")
        self._http_client = http_client
        self._timeout_sec = float(timeout_sec)
        self._usage_limiter = usage_limiter
        self._logger = logger or logging.getLogger(__name__)

    async def build_digest(
        self, videos: Sequence[dict], *, report_date: str
    ) -> ResCommonResponse:
        candidates = [video for video in (videos or []) if video.get("url")]
        if not candidates:
            return ResCommonResponse(
                rt_cd=ErrorCode.EMPTY_VALUES.value,
                msg1="Gemini fallback 대상 YouTube URL이 없습니다.",
                data=None,
            )

        summarized: list[dict] = []
        failed = 0
        for video in candidates:
            try:
                summary = await self._summarize_url(str(video.get("url") or ""))
            except Exception as exc:
                failed += 1
                self._logger.warning(
                    "Gemini YouTube fallback summary failed: video_id=%s error=%s",
                    video.get("video_id"),
                    exc,
                )
                continue
            summarized.append(
                {
                    "video_id": str(video.get("video_id") or ""),
                    "title": str(video.get("title") or ""),
                    "channel_title": str(video.get("channel_title") or ""),
                    "url": str(video.get("url") or ""),
                    "published": str(video.get("published") or ""),
                    "summary": summary,
                }
            )

        if not summarized:
            return ResCommonResponse(
                rt_cd=ErrorCode.API_ERROR.value,
                msg1="Gemini fallback 영상 요약이 모두 실패했습니다.",
                data=None,
            )

        digest_text = self._format_digest_text(summarized)
        return ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value,
            msg1="정상처리 되었습니다.",
            data={
                "report_date": str(report_date),
                "source": "gemini_video_url",
                "video_count": len(summarized),
                "failed_summary_count": failed,
                # 자막 원문이 없으므로 기존 deterministic 종목 카운트는 수행하지 않는다.
                "mentions": [],
                "videos": summarized,
                "digest_text": digest_text,
            },
        )

    async def _summarize_url(self, url: str) -> str:
        if not self._api_key:
            raise ValueError("Gemini API key가 필요합니다.")
        if not self._api_key.isascii():
            raise ValueError("Gemini API key에 ASCII 외 문자가 섞여 있습니다.")
        payload = {
            "model": self._model,
            "input": [
                {"type": "video", "uri": url},
                {"type": "text", "text": _VIDEO_PROMPT},
            ],
        }
        headers = {"x-goog-api-key": self._api_key}
        if self._usage_limiter is not None:
            await self._usage_limiter.reserve("youtube")
        if self._http_client is not None:
            response = await self._http_client.post(
                _INTERACTIONS_URL,
                headers=headers,
                json=payload,
                timeout=self._timeout_sec,
            )
            return self._parse_response(response)
        async with httpx.AsyncClient(timeout=self._timeout_sec) as client:
            response = await client.post(
                _INTERACTIONS_URL,
                headers=headers,
                json=payload,
                timeout=self._timeout_sec,
            )
            return self._parse_response(response)

    @staticmethod
    def _parse_response(response: Any) -> str:
        response.raise_for_status()
        payload = response.json()
        text = payload.get("output_text") if isinstance(payload, dict) else None
        if isinstance(text, str) and text.strip():
            return text.strip()
        steps = payload.get("steps") if isinstance(payload, dict) else None
        extracted = GeminiYoutubeVideoAnalyzerService._extract_text_from_steps(steps)
        if extracted:
            return extracted
        raise ValueError("Gemini 응답 본문이 비어 있습니다.")

    @staticmethod
    def _extract_text_from_steps(steps: Any) -> str:
        parts: list[str] = []
        if not isinstance(steps, list):
            return ""
        for step in steps:
            content = step.get("content") if isinstance(step, dict) else None
            if not isinstance(content, list):
                continue
            for item in content:
                text = item.get("text") if isinstance(item, dict) else None
                if isinstance(text, str) and text.strip():
                    parts.append(text.strip())
        return "\n".join(parts).strip()

    @staticmethod
    def _format_digest_text(videos: Sequence[dict]) -> str:
        lines = [
            "Gemini YouTube URL fallback 리포트",
            "자막 원문 수집이 차단되어 영상 URL 분석 결과로 대체했습니다.",
            "",
        ]
        for video in videos:
            title = video.get("title") or video.get("video_id") or "영상"
            channel = video.get("channel_title") or "채널"
            lines.append(f"[{channel}] {title}")
            lines.append(str(video.get("summary") or "").strip())
            lines.append("")
        return "\n".join(lines).strip()
