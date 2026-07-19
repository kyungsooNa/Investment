from unittest.mock import AsyncMock, MagicMock

from scripts import check_ai_key, check_disclosure_ai


async def test_check_ai_key_allows_empty_api_key_for_ollama(monkeypatch):
    ai_client = MagicMock()
    ai_client.complete = AsyncMock(return_value="연결 성공")
    ai_client_factory = MagicMock(return_value=ai_client)
    monkeypatch.setattr(
        check_ai_key,
        "_load_ai_config",
        lambda: {
            "provider": "ollama",
            "base_url": "http://localhost:11434/v1",
            "api_key": "",
            "model": "qwen2.5",
        },
    )
    monkeypatch.setattr(check_ai_key, "AiClient", ai_client_factory)

    await check_ai_key._main()

    ai_client_factory.assert_called_once_with(
        base_url="http://localhost:11434/v1",
        api_key="",
        model="qwen2.5",
        timeout_sec=20,
    )
    ai_client.complete.assert_awaited_once()


def test_disclosure_dry_run_builds_ollama_analyzer_without_api_key():
    ai_client, analyzer = check_disclosure_ai._build_ai(
        {
            "enabled": True,
            "provider": "ollama",
            "base_url": "http://localhost:11434/v1",
            "api_key": "",
            "model": "qwen2.5",
        }
    )

    assert ai_client is not None
    assert analyzer is not None
