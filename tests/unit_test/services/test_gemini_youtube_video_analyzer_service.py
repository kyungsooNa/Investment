"""Gemini YouTube URL fallback 분석 서비스 단위 테스트."""
from unittest.mock import AsyncMock

from common.types import ErrorCode
from services.gemini_youtube_video_analyzer_service import (
    GeminiYoutubeVideoAnalyzerService,
)


class _Response:
    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def _video(video_id: str = "v1") -> dict:
    return {
        "video_id": video_id,
        "title": "아침 시황",
        "channel_title": "테스트채널",
        "url": f"https://www.youtube.com/watch?v={video_id}",
        "published": "2026-08-10T07:00:00+00:00",
    }


async def test_build_digest_summarizes_public_youtube_urls():
    http = AsyncMock()
    http.post = AsyncMock(return_value=_Response({"output_text": "반도체 중심 요약"}))
    svc = GeminiYoutubeVideoAnalyzerService(
        api_key="test-key",
        model="gemini-2.5-flash",
        http_client=http,
    )

    result = await svc.build_digest([_video()], report_date="20260810")

    assert result.rt_cd == ErrorCode.SUCCESS.value
    data = result.data
    assert data["source"] == "gemini_video_url"
    assert data["video_count"] == 1
    assert data["mentions"] == []
    assert data["videos"][0]["summary"] == "반도체 중심 요약"
    request = http.post.await_args
    assert request.args[0].endswith("/v1beta/interactions")
    assert request.kwargs["headers"]["x-goog-api-key"] == "test-key"
    assert request.kwargs["json"]["input"][0] == {
        "type": "video",
        "uri": "https://www.youtube.com/watch?v=v1",
    }


async def test_build_digest_reserves_ai_usage_before_each_video_request():
    http = AsyncMock()
    http.post = AsyncMock(return_value=_Response({"output_text": "요약"}))
    limiter = AsyncMock()
    limiter.reserve = AsyncMock()
    svc = GeminiYoutubeVideoAnalyzerService(
        api_key="test-key",
        http_client=http,
        usage_limiter=limiter,
    )

    await svc.build_digest([_video("v1"), _video("v2")], report_date="20260810")

    assert limiter.reserve.await_count == 2
    limiter.reserve.assert_any_await("youtube")


async def test_build_digest_does_not_call_gemini_when_usage_limit_blocks():
    http = AsyncMock()
    http.post = AsyncMock(return_value=_Response({"output_text": "요약"}))
    limiter = AsyncMock()
    limiter.reserve = AsyncMock(side_effect=RuntimeError("daily limit"))
    svc = GeminiYoutubeVideoAnalyzerService(
        api_key="test-key",
        http_client=http,
        usage_limiter=limiter,
    )

    result = await svc.build_digest([_video()], report_date="20260810")

    assert result.rt_cd == ErrorCode.API_ERROR.value
    http.post.assert_not_awaited()


async def test_build_digest_reports_empty_when_no_video_url():
    svc = GeminiYoutubeVideoAnalyzerService(api_key="test-key", http_client=AsyncMock())

    result = await svc.build_digest([{"video_id": "v1"}], report_date="20260810")

    assert result.rt_cd == ErrorCode.EMPTY_VALUES.value


async def test_build_digest_keeps_going_when_one_video_fails():
    http = AsyncMock()
    http.post = AsyncMock(side_effect=[
        RuntimeError("quota"),
        _Response({"output_text": "두 번째 요약"}),
    ])
    svc = GeminiYoutubeVideoAnalyzerService(api_key="test-key", http_client=http)

    result = await svc.build_digest([_video("v1"), _video("v2")], report_date="20260810")

    assert result.rt_cd == ErrorCode.SUCCESS.value
    assert result.data["video_count"] == 1
    assert result.data["failed_summary_count"] == 1
    assert result.data["videos"][0]["video_id"] == "v2"
