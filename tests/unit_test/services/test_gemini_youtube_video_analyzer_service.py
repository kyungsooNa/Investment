"""Gemini YouTube URL fallback 분석 서비스 단위 테스트."""
from unittest.mock import AsyncMock, MagicMock

import pytest

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


async def test_build_digest_fails_when_api_key_missing():
    svc = GeminiYoutubeVideoAnalyzerService(api_key="", http_client=AsyncMock())

    result = await svc.build_digest([_video()], report_date="20260810")

    assert result.rt_cd == ErrorCode.API_ERROR.value
    assert result.data is None


async def test_build_digest_fails_when_api_key_has_non_ascii_characters():
    svc = GeminiYoutubeVideoAnalyzerService(api_key="키-한글", http_client=AsyncMock())

    result = await svc.build_digest([_video()], report_date="20260810")

    assert result.rt_cd == ErrorCode.API_ERROR.value


async def test_build_digest_reports_empty_when_video_list_is_none():
    svc = GeminiYoutubeVideoAnalyzerService(api_key="test-key", http_client=AsyncMock())

    result = await svc.build_digest(None, report_date="20260810")

    assert result.rt_cd == ErrorCode.EMPTY_VALUES.value


async def test_summarize_url_uses_own_http_client_when_none_injected(monkeypatch):
    posted = {}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            posted["init"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, headers=None, json=None, timeout=None):
            posted["url"] = url
            posted["headers"] = headers
            return _Response({"output_text": "자체 클라이언트 요약"})

    monkeypatch.setattr(
        "services.gemini_youtube_video_analyzer_service.httpx.AsyncClient", FakeClient
    )
    svc = GeminiYoutubeVideoAnalyzerService(api_key="test-key")

    result = await svc.build_digest([_video()], report_date="20260810")

    assert result.rt_cd == ErrorCode.SUCCESS.value
    assert result.data["videos"][0]["summary"] == "자체 클라이언트 요약"
    assert posted["url"].endswith("/v1beta/interactions")
    assert posted["headers"]["x-goog-api-key"] == "test-key"


async def test_build_digest_falls_back_to_step_texts_when_output_text_missing():
    http = AsyncMock()
    http.post = AsyncMock(return_value=_Response({
        "steps": [
            {"content": [{"text": " 첫 문단 "}, {"text": ""}, {"no_text": 1}]},
            {"content": "리스트 아님"},
            "스텝 아님",
            {"content": [{"text": "둘째 문단"}]},
        ]
    }))
    svc = GeminiYoutubeVideoAnalyzerService(api_key="test-key", http_client=http)

    result = await svc.build_digest([_video()], report_date="20260810")

    assert result.rt_cd == ErrorCode.SUCCESS.value
    assert result.data["videos"][0]["summary"] == "첫 문단\n둘째 문단"


async def test_build_digest_fails_when_response_body_is_empty():
    http = AsyncMock()
    http.post = AsyncMock(return_value=_Response({"output_text": "   "}))
    svc = GeminiYoutubeVideoAnalyzerService(api_key="test-key", http_client=http)

    result = await svc.build_digest([_video()], report_date="20260810")

    assert result.rt_cd == ErrorCode.API_ERROR.value


async def test_build_digest_fails_on_http_error_status():
    http = AsyncMock()
    http.post = AsyncMock(return_value=_Response({}, status_code=500))
    svc = GeminiYoutubeVideoAnalyzerService(api_key="test-key", http_client=http)

    result = await svc.build_digest([_video()], report_date="20260810")

    assert result.rt_cd == ErrorCode.API_ERROR.value


@pytest.mark.parametrize("steps", ["문자열", None, {}, 3])
def test_extract_text_from_steps_returns_empty_for_non_list(steps):
    assert GeminiYoutubeVideoAnalyzerService._extract_text_from_steps(steps) == ""


def test_parse_response_returns_empty_string_summary_for_non_dict_payload():
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json = MagicMock(return_value=["not", "a", "dict"])

    with pytest.raises(ValueError):
        GeminiYoutubeVideoAnalyzerService._parse_response(response)


def test_format_digest_text_uses_fallback_labels():
    text = GeminiYoutubeVideoAnalyzerService._format_digest_text([
        {"video_id": "v9", "summary": "요약"},
    ])

    assert "[채널] v9" in text
    assert text.endswith("요약")
