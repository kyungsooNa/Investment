from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from services.ai_client import AiClient, AiClientError


def _response(payload):
    response = MagicMock()
    response.raise_for_status = lambda: None
    response.json.return_value = payload
    return response


def _completion(content):
    return {"choices": [{"message": {"role": "assistant", "content": content}}]}


async def test_complete_posts_openai_compatible_chat_request():
    http_client = AsyncMock()
    http_client.post.return_value = _response(_completion("요약입니다."))
    client = AiClient(
        base_url="https://example.com/v1",
        api_key="secret",
        model="test-model",
        http_client=http_client,
        timeout_sec=15,
    )

    result = await client.complete(system="너는 애널리스트다", user="이 공시를 요약해줘")

    assert result == "요약입니다."
    url = http_client.post.await_args.args[0]
    assert url == "https://example.com/v1/chat/completions"
    kwargs = http_client.post.await_args.kwargs
    assert kwargs["headers"]["Authorization"] == "Bearer secret"
    payload = kwargs["json"]
    assert payload["model"] == "test-model"
    assert payload["messages"][0] == {"role": "system", "content": "너는 애널리스트다"}
    assert payload["messages"][1] == {"role": "user", "content": "이 공시를 요약해줘"}


async def test_trailing_slash_base_url_is_normalized():
    http_client = AsyncMock()
    http_client.post.return_value = _response(_completion("ok"))
    client = AiClient(
        base_url="http://localhost:11434/v1/",
        api_key="",
        model="qwen2.5",
        http_client=http_client,
    )

    await client.complete(system="s", user="u")

    url = http_client.post.await_args.args[0]
    assert url == "http://localhost:11434/v1/chat/completions"


async def test_empty_api_key_omits_authorization_header():
    http_client = AsyncMock()
    http_client.post.return_value = _response(_completion("ok"))
    client = AiClient(
        base_url="http://localhost:11434/v1",
        api_key="",
        model="qwen2.5",
        http_client=http_client,
    )

    await client.complete(system="s", user="u")

    assert "Authorization" not in http_client.post.await_args.kwargs["headers"]


async def test_api_key_surrounding_whitespace_is_stripped():
    http_client = AsyncMock()
    http_client.post.return_value = _response(_completion("ok"))
    client = AiClient(
        base_url="https://example.com/v1",
        api_key="  AQ.Ab8valid \n",
        model="m",
        http_client=http_client,
    )

    await client.complete(system="s", user="u")

    assert http_client.post.await_args.kwargs["headers"]["Authorization"] == "Bearer AQ.Ab8valid"


async def test_non_ascii_api_key_raises_clear_error_not_raw_unicodeerror():
    http_client = AsyncMock()  # 네트워크 도달 전에 막혀야 한다
    client = AiClient(
        base_url="https://example.com/v1",
        api_key="AQ.Ab8한글오염key",
        model="m",
        http_client=http_client,
    )

    with pytest.raises(AiClientError) as exc_info:
        await client.complete(system="s", user="u")

    message = str(exc_info.value)
    assert "ASCII" in message.upper() or "키" in message
    http_client.post.assert_not_awaited()


async def test_missing_choices_raises_ai_client_error():
    http_client = AsyncMock()
    http_client.post.return_value = _response({"choices": []})
    client = AiClient(
        base_url="https://example.com/v1",
        api_key="secret",
        model="m",
        http_client=http_client,
    )

    with pytest.raises(AiClientError):
        await client.complete(system="s", user="u")


async def test_blank_content_raises_ai_client_error():
    http_client = AsyncMock()
    http_client.post.return_value = _response(_completion("   "))
    client = AiClient(
        base_url="https://example.com/v1",
        api_key="secret",
        model="m",
        http_client=http_client,
    )

    with pytest.raises(AiClientError):
        await client.complete(system="s", user="u")


async def test_complete_reserves_usage_before_http_request():
    http_client = AsyncMock()
    http_client.post.return_value = _response(_completion("ok"))
    usage_limiter = MagicMock()
    usage_limiter.reserve = AsyncMock()
    client = AiClient(
        base_url="https://example.com/v1",
        api_key="secret",
        model="m",
        http_client=http_client,
        usage_limiter=usage_limiter,
    )

    await client.complete(system="s", user="u", usage_type="stock")

    usage_limiter.reserve.assert_awaited_once_with("stock")
    http_client.post.assert_awaited_once()


async def test_usage_limit_block_prevents_http_request():
    from services.ai_usage_limiter import AiUsageLimitExceeded

    http_client = AsyncMock()
    usage_limiter = MagicMock()
    usage_limiter.reserve = AsyncMock(
        side_effect=AiUsageLimitExceeded(
            limit_kind="interactive",
            daily_limit=100,
            used=80,
            reset_at="2026-07-20T00:00:00-07:00",
        )
    )
    client = AiClient(
        base_url="https://example.com/v1",
        api_key="secret",
        model="m",
        http_client=http_client,
        usage_limiter=usage_limiter,
    )

    with pytest.raises(AiUsageLimitExceeded):
        await client.complete(system="s", user="u", usage_type="ranking")

    http_client.post.assert_not_awaited()


def _status_response(status_code):
    """실제 httpx.Response 로 raise_for_status 동작을 그대로 재현한다."""
    request = httpx.Request("POST", "https://example.com/v1/chat/completions")
    return httpx.Response(status_code, request=request, json={"error": "upstream"})


async def test_transient_503_is_retried_then_succeeds():
    http_client = AsyncMock()
    http_client.post.side_effect = [
        _status_response(503),
        _response(_completion("복구된 응답")),
    ]
    client = AiClient(
        base_url="https://example.com/v1",
        api_key="secret",
        model="m",
        http_client=http_client,
        retry_backoff_sec=0,
    )

    result = await client.complete(system="s", user="u")

    assert result == "복구된 응답"
    assert http_client.post.await_count == 2


async def test_transient_network_error_is_retried_then_succeeds():
    http_client = AsyncMock()
    http_client.post.side_effect = [
        httpx.ConnectError("connection reset"),
        _response(_completion("ok")),
    ]
    client = AiClient(
        base_url="https://example.com/v1",
        api_key="secret",
        model="m",
        http_client=http_client,
        retry_backoff_sec=0,
    )

    assert await client.complete(system="s", user="u") == "ok"
    assert http_client.post.await_count == 2


async def test_client_error_4xx_is_not_retried():
    http_client = AsyncMock()
    http_client.post.return_value = _status_response(401)
    client = AiClient(
        base_url="https://example.com/v1",
        api_key="bad",
        model="m",
        http_client=http_client,
        retry_backoff_sec=0,
    )

    with pytest.raises(httpx.HTTPStatusError):
        await client.complete(system="s", user="u")

    http_client.post.assert_awaited_once()


async def test_retry_exhaustion_raises_last_error():
    http_client = AsyncMock()
    http_client.post.return_value = _status_response(503)
    client = AiClient(
        base_url="https://example.com/v1",
        api_key="secret",
        model="m",
        http_client=http_client,
        max_retries=2,
        retry_backoff_sec=0,
    )

    with pytest.raises(httpx.HTTPStatusError):
        await client.complete(system="s", user="u")

    assert http_client.post.await_count == 3  # 최초 1회 + 재시도 2회


async def test_retry_does_not_consume_extra_usage_quota():
    http_client = AsyncMock()
    http_client.post.side_effect = [
        _status_response(503),
        _status_response(503),
        _response(_completion("ok")),
    ]
    usage_limiter = MagicMock()
    usage_limiter.reserve = AsyncMock()
    client = AiClient(
        base_url="https://example.com/v1",
        api_key="secret",
        model="m",
        http_client=http_client,
        usage_limiter=usage_limiter,
        retry_backoff_sec=0,
    )

    await client.complete(system="s", user="u", usage_type="stock")

    usage_limiter.reserve.assert_awaited_once_with("stock")
    assert http_client.post.await_count == 3


async def test_backoff_grows_exponentially_between_attempts(monkeypatch):
    slept = []

    async def _fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr("asyncio.sleep", _fake_sleep)
    http_client = AsyncMock()
    http_client.post.return_value = _status_response(503)
    client = AiClient(
        base_url="https://example.com/v1",
        api_key="secret",
        model="m",
        http_client=http_client,
        max_retries=2,
        retry_backoff_sec=0.5,
    )

    with pytest.raises(httpx.HTTPStatusError):
        await client.complete(system="s", user="u")

    assert slept == [0.5, 1.0]
