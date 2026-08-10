from unittest.mock import ANY, AsyncMock, MagicMock

import pytest

from services.ai_usage_limiter import AiUsageLimitExceeded

from scripts import check_ai_key, check_disclosure_ai, check_news_ai, check_youtube_ai


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
        usage_limiter=ANY,
    )
    ai_client.complete.assert_awaited_once()


def test_check_ai_key_attaches_usage_limiter_so_script_usage_is_counted():
    """스크립트가 쓴 요청도 앱과 같은 일일 한도에 집계되어야 한다."""
    client = check_ai_key._build_client(
        {
            "base_url": "http://localhost:11434/v1",
            "api_key": "",
            "model": "qwen2.5",
            "daily_request_limit": 100,
            "disclosure_reserve": 20,
        }
    )

    assert client._usage_limiter is not None


async def test_check_ai_key_distinguishes_usage_limit_from_key_failure(
    monkeypatch, capsys
):
    """한도 소진은 키·모델·네트워크 문제가 아니므로 안내가 달라야 한다."""
    ai_client = MagicMock()
    ai_client.complete = AsyncMock(
        side_effect=AiUsageLimitExceeded(
            limit_kind="interactive",
            daily_limit=100,
            used=80,
            reset_at="2026-08-08T00:00:00-07:00",
            interactive_limit=80,
            disclosure_reserve=20,
        )
    )
    monkeypatch.setattr(
        check_ai_key,
        "_load_ai_config",
        lambda: {"api_key": "AIzaXXXX", "model": "gemini-2.5-flash"},
    )
    monkeypatch.setattr(check_ai_key, "AiClient", MagicMock(return_value=ai_client))

    with pytest.raises(SystemExit):
        await check_ai_key._main()

    out = capsys.readouterr().out
    assert "한도" in out
    assert "키 형식" not in out


def test_disclosure_dry_run_attaches_usage_limiter_so_script_usage_is_counted():
    """공시 진단 소비도 /api/ai/usage 집계에 포함돼야 한다."""
    ai_client, analyzer = check_disclosure_ai._build_ai(
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


def test_youtube_dry_run_returns_no_digest_when_ai_disabled():
    assert check_youtube_ai._build_ai({"enabled": False}) == (None, None)


def test_youtube_dry_run_attaches_usage_limiter_so_script_usage_is_counted():
    """스크립트가 쓴 요청도 앱과 같은 일일 한도에 집계되어야 한다."""
    ai_client, digest = check_youtube_ai._build_ai(
        {
            "enabled": True,
            "base_url": "http://localhost:11434/v1",
            "api_key": "",
            "model": "qwen2.5",
            "daily_request_limit": 100,
            "disclosure_reserve": 20,
        }
    )

    assert digest is not None
    assert ai_client._usage_limiter is not None


def test_youtube_dry_run_chunk_stays_under_input_budget():
    """청크가 max_input_chars 를 넘으면 AiClient 가 뒤를 잘라 요약이 깨진다."""
    _, digest = check_youtube_ai._build_ai(
        {
            "enabled": True,
            "base_url": "http://localhost:11434/v1",
            "api_key": "",
            "model": "qwen2.5",
            "max_input_chars": 24000,
        }
    )

    assert digest._chunk_chars < 24000


async def test_youtube_dry_run_skips_ai_without_flag(monkeypatch, capsys):
    """기본 실행은 수집·집계만 하고 일일 한도를 소비하지 않는다."""
    monkeypatch.setattr(
        check_youtube_ai, "_load_config", lambda: {"ai_analysis": {"enabled": True}}
    )
    monkeypatch.setattr(check_youtube_ai.sys, "argv", ["check_youtube_ai.py"])
    build_ai = MagicMock(return_value=(None, None))
    monkeypatch.setattr(check_youtube_ai, "_build_ai", build_ai)

    repo = MagicMock()
    repo.seed_defaults = AsyncMock(return_value=0)
    repo.get_all = AsyncMock(return_value=[{"channel_id": "UC" + "a" * 22, "title": "채널"}])
    monkeypatch.setattr(check_youtube_ai, "YoutubeChannelRepository", lambda: repo)

    collector = MagicMock()
    collector.collect = AsyncMock(return_value=[])
    monkeypatch.setattr(
        check_youtube_ai, "YoutubeTranscriptCollectorService", lambda: collector
    )
    monkeypatch.setattr(check_youtube_ai, "StockCodeRepository", MagicMock())

    await check_youtube_ai._main()

    build_ai.assert_not_called()
    assert "AI 요약=OFF" in capsys.readouterr().out


async def test_youtube_dry_run_stops_early_without_channels(monkeypatch, capsys):
    monkeypatch.setattr(
        check_youtube_ai, "_load_config", lambda: {"ai_analysis": {"enabled": True}}
    )
    monkeypatch.setattr(check_youtube_ai.sys, "argv", ["check_youtube_ai.py"])

    repo = MagicMock()
    repo.seed_defaults = AsyncMock(return_value=0)
    repo.get_all = AsyncMock(return_value=[])
    monkeypatch.setattr(check_youtube_ai, "YoutubeChannelRepository", lambda: repo)

    collector = MagicMock()
    collector.collect = AsyncMock(return_value=[])
    monkeypatch.setattr(
        check_youtube_ai, "YoutubeTranscriptCollectorService", lambda: collector
    )

    await check_youtube_ai._main()

    collector.collect.assert_not_awaited()
    assert "등록된 채널이 없습니다" in capsys.readouterr().out
