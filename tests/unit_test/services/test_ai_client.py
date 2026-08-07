import asyncio
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


async def test_reasoning_effort_is_sent_when_configured():
    """thinking 예산을 제한해 max_tokens 를 본문에 남긴다 (Gemini 2.5 계열)."""
    http_client = AsyncMock()
    http_client.post.return_value = _response(_completion("요약입니다."))
    client = AiClient(
        base_url="https://example.com/v1",
        api_key="secret",
        model="gemini-2.5-flash",
        http_client=http_client,
        reasoning_effort="low",
    )

    await client.complete(system="s", user="u")

    assert http_client.post.await_args.kwargs["json"]["reasoning_effort"] == "low"


async def test_reasoning_effort_is_omitted_when_blank():
    """미지원 provider(Ollama 등)에 알 수 없는 파라미터를 보내지 않는다."""
    http_client = AsyncMock()
    http_client.post.return_value = _response(_completion("요약입니다."))
    client = AiClient(
        base_url="https://example.com/v1",
        api_key="secret",
        model="llama3",
        http_client=http_client,
        reasoning_effort="",
    )

    await client.complete(system="s", user="u")

    assert "reasoning_effort" not in http_client.post.await_args.kwargs["json"]


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


async def test_read_timeout_is_not_retried_and_reports_timeout_seconds():
    """같은 제한 시간으로 다시 걸면 결과가 같으므로 타임아웃은 재시도하지 않는다."""
    http_client = AsyncMock()
    http_client.post.side_effect = httpx.ReadTimeout("timed out")
    client = AiClient(
        base_url="https://example.com/v1",
        api_key="secret",
        model="gemini-2.5-flash",
        http_client=http_client,
        timeout_sec=45,
        max_retries=2,
        retry_backoff_sec=0,
    )

    with pytest.raises(AiClientError, match="45초") as exc_info:
        await client.complete(system="s", user="u")

    assert exc_info.value.status == "TIMEOUT"
    http_client.post.assert_awaited_once()


async def test_aborted_completion_is_retried_then_succeeds():
    """Gemini가 HTTP 200으로 돌려주는 내부 중단 문구도 일시 오류로 재시도한다."""
    http_client = AsyncMock()
    http_client.post.side_effect = [
        _response(_completion("signal is aborted without reason")),
        _response(_completion("복구된 응답")),
    ]
    client = AiClient(
        base_url="https://example.com/v1",
        api_key="secret",
        model="gemini-2.5-flash",
        http_client=http_client,
        retry_backoff_sec=0,
    )

    assert await client.complete(system="s", user="u") == "복구된 응답"
    assert http_client.post.await_count == 2


async def test_aborted_completion_with_provider_metadata_is_retried():
    """문구 앞뒤에 공급자 메타데이터가 붙어도 내부 중단으로 처리한다."""
    http_client = AsyncMock()
    http_client.post.side_effect = [
        _response(_completion("[internal] signal is aborted without reason\n")),
        _response(_completion("복구된 응답")),
    ]
    client = AiClient(
        base_url="https://example.com/v1",
        api_key="secret",
        model="gemini-2.5-flash",
        http_client=http_client,
        retry_backoff_sec=0,
    )

    assert await client.complete(system="s", user="u") == "복구된 응답"
    assert http_client.post.await_count == 2


async def test_repeated_aborted_completion_raises_clear_error():
    http_client = AsyncMock()
    http_client.post.return_value = _response(
        _completion("signal is aborted without reason")
    )
    client = AiClient(
        base_url="https://example.com/v1",
        api_key="secret",
        model="gemini-2.5-flash",
        http_client=http_client,
        max_retries=1,
        retry_backoff_sec=0,
    )

    with pytest.raises(AiClientError, match="일시적으로 중단") as exc_info:
        await client.complete(system="s", user="u")

    assert exc_info.value.status == "UPSTREAM_ABORTED"
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


async def test_length_finish_reason_appends_truncation_notice():
    """thinking 모델이 max_tokens 를 소진해 잘린 응답은 조용히 넘기지 않는다."""
    http_client = AsyncMock()
    http_client.post.return_value = _response(
        {
            "choices": [
                {
                    "message": {"role": "assistant", "content": "한줄 요약: 잘린 분석"},
                    "finish_reason": "length",
                }
            ]
        }
    )
    client = AiClient(
        base_url="https://example.com/v1",
        api_key="secret",
        model="gemini-2.5-flash",
        http_client=http_client,
    )

    result = await client.complete(system="s", user="u")

    assert result.startswith("한줄 요약: 잘린 분석")
    assert "max_tokens" in result  # 잘림 안내가 덧붙는다
    assert "잘려" in result


async def test_stop_finish_reason_returns_content_unchanged():
    http_client = AsyncMock()
    http_client.post.return_value = _response(
        {
            "choices": [
                {
                    "message": {"role": "assistant", "content": "정상 분석"},
                    "finish_reason": "stop",
                }
            ]
        }
    )
    client = AiClient(
        base_url="https://example.com/v1",
        api_key="secret",
        model="m",
        http_client=http_client,
    )

    assert await client.complete(system="s", user="u") == "정상 분석"


# ── 운영 하드닝: 입력 예산 · 동시성 · 재호출 간격 · 계측 (todo X-2) ──────────


async def test_input_budget_truncates_oversized_user_prompt():
    """컨텍스트가 커질수록 토큰 비용이 선형으로 늘어 예산 상한이 필요하다."""
    http_client = AsyncMock()
    http_client.post.return_value = _response(_completion("요약"))
    logger = MagicMock()
    client = AiClient(
        base_url="https://example.com/v1",
        api_key="k",
        model="m",
        http_client=http_client,
        max_input_chars=100,
        logger=logger,
    )

    await client.complete(system="s", user="가" * 500)

    sent = http_client.post.await_args.kwargs["json"]["messages"][1]["content"]
    assert len(sent) <= 100
    assert sent.startswith("가" * 50)  # 앞부분 보존 (뒤를 자른다)
    logger.warning.assert_called_once()


async def test_input_budget_leaves_prompt_within_budget_untouched():
    http_client = AsyncMock()
    http_client.post.return_value = _response(_completion("요약"))
    client = AiClient(
        base_url="https://example.com/v1",
        api_key="k",
        model="m",
        http_client=http_client,
        max_input_chars=100,
    )

    await client.complete(system="s", user="짧은 입력")

    assert http_client.post.await_args.kwargs["json"]["messages"][1]["content"] == "짧은 입력"


async def test_max_concurrent_requests_serializes_in_flight_calls():
    """동시 호출이 provider 로 한꺼번에 나가지 않도록 서버 단위로 제한한다."""
    in_flight = 0
    peak = 0

    async def _post(*args, **kwargs):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0)
        in_flight -= 1
        return _response(_completion("요약"))

    http_client = AsyncMock()
    http_client.post.side_effect = _post
    client = AiClient(
        base_url="https://example.com/v1",
        api_key="k",
        model="m",
        http_client=http_client,
        max_concurrent_requests=1,
    )

    await asyncio.gather(*(client.complete(system="s", user="u") for _ in range(4)))

    assert peak == 1


async def test_min_request_interval_paces_consecutive_requests(monkeypatch):
    """단시간 재호출이 몰리지 않도록 최소 간격을 둔다."""
    slept = []

    async def _sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", _sleep)
    http_client = AsyncMock()
    http_client.post.return_value = _response(_completion("요약"))
    client = AiClient(
        base_url="https://example.com/v1",
        api_key="k",
        model="m",
        http_client=http_client,
        min_request_interval_sec=5.0,
    )

    await client.complete(system="s", user="u")
    await client.complete(system="s", user="u")

    # 첫 호출은 대기 없음, 둘째 호출만 간격만큼 대기
    assert len(slept) == 1
    assert 0 < slept[0] <= 5.0


async def test_logs_provider_model_latency_and_token_usage():
    """provider/model·지연·토큰 사용량이 남아야 비용·성능을 사후 판단할 수 있다."""
    payload = _completion("요약")
    payload["usage"] = {"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150}
    http_client = AsyncMock()
    http_client.post.return_value = _response(payload)
    logger = MagicMock()
    client = AiClient(
        base_url="https://example.com/v1",
        api_key="k",
        model="gemini-2.5-flash",
        http_client=http_client,
        logger=logger,
    )

    await client.complete(system="s", user="u", usage_type="news")

    logged = " ".join(str(call) for call in logger.info.call_args_list)
    assert "gemini-2.5-flash" in logged
    assert "news" in logged
    assert "150" in logged
    assert "elapsed_ms" in logged


async def test_instrumentation_is_optional_without_logger():
    http_client = AsyncMock()
    http_client.post.return_value = _response(_completion("요약"))
    client = AiClient(
        base_url="https://example.com/v1",
        api_key="k",
        model="m",
        http_client=http_client,
    )

    assert await client.complete(system="s", user="u") == "요약"
