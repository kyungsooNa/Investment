from unittest.mock import AsyncMock, MagicMock

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
