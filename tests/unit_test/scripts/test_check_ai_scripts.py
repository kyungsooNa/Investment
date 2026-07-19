from unittest.mock import AsyncMock, MagicMock

from scripts import check_ai_key, check_disclosure_ai, check_news_ai


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


def test_news_dry_run_returns_no_analyzer_when_ai_disabled():
    assert check_news_ai._build_ai({"enabled": False}) == (None, None)


def test_news_dry_run_attaches_usage_limiter_so_script_usage_is_counted():
    """스크립트가 쓴 요청도 앱과 같은 일일 한도에 집계되어야 한다."""
    ai_client, analyzer = check_news_ai._build_ai(
        {
            "enabled": True,
            "base_url": "http://localhost:11434/v1",
            "api_key": "",
            "model": "qwen2.5",
            "daily_request_limit": 100,
            "disclosure_reserve": 20,
        }
    )

    assert analyzer is not None
    assert ai_client._usage_limiter is not None


async def test_news_dry_run_skips_ai_without_explicit_codes(monkeypatch, capsys):
    """--codes 없이 관심종목 전체를 돌 때는 한도를 소비하지 않는다."""
    monkeypatch.setattr(check_news_ai, "_load_config", lambda: {"ai_analysis": {"enabled": True}})
    monkeypatch.setattr(check_news_ai.sys, "argv", ["check_news_ai.py"])
    build_ai = MagicMock(return_value=(None, None))
    monkeypatch.setattr(check_news_ai, "_build_ai", build_ai)

    favorite_repo = MagicMock()
    favorite_repo.get_all = AsyncMock(return_value=["005930"])
    monkeypatch.setattr(check_news_ai, "FavoriteRepository", lambda: favorite_repo)

    collector = MagicMock()
    collector.collect = AsyncMock(return_value=[])
    monkeypatch.setattr(check_news_ai, "StockNewsCollectorService", lambda: collector)

    await check_news_ai._main()

    build_ai.assert_not_called()
    assert "AI 검토=OFF" in capsys.readouterr().out


async def test_news_dry_run_no_ai_flag_skips_analyzer(monkeypatch):
    monkeypatch.setattr(check_news_ai, "_load_config", lambda: {"ai_analysis": {"enabled": True}})
    monkeypatch.setattr(
        check_news_ai.sys, "argv", ["check_news_ai.py", "--codes", "005930", "--no-ai"]
    )
    build_ai = MagicMock(return_value=(None, None))
    monkeypatch.setattr(check_news_ai, "_build_ai", build_ai)

    collector = MagicMock()
    collector.collect = AsyncMock(return_value=[])
    monkeypatch.setattr(check_news_ai, "StockNewsCollectorService", lambda: collector)

    await check_news_ai._main()

    build_ai.assert_not_called()
    collector.collect.assert_awaited_once_with("005930", limit=15)
