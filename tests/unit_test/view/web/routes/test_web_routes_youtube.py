"""유튜브 다이제스트 API 엔드포인트 테스트 (youtube.html)."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _patch_ctx(mock_web_ctx):
    return patch("view.web.routes.youtube._get_ctx", return_value=mock_web_ctx)


def _channel_repo(mock_web_ctx, **overrides):
    repo = MagicMock()
    repo.get_all = AsyncMock(return_value=[
        {"channel_id": "UC1", "handle": "chesleytv", "title": "체슬리TV", "enabled": True},
    ])
    repo.add = AsyncMock(return_value=True)
    repo.remove = AsyncMock(return_value=True)
    repo.set_enabled = AsyncMock(return_value=True)
    for key, value in overrides.items():
        setattr(repo, key, value)
    mock_web_ctx.youtube_channel_repository = repo
    return repo


def _digest_repo(mock_web_ctx, **overrides):
    repo = MagicMock()
    repo.list_recent = AsyncMock(return_value=[{"report_date": "20260810", "video_count": 6}])
    repo.get = AsyncMock(return_value={"report_date": "20260810", "digest_text": "본문"})
    repo.latest = AsyncMock(return_value={"report_date": "20260810", "digest_text": "본문"})
    for key, value in overrides.items():
        setattr(repo, key, value)
    mock_web_ctx.youtube_digest_repository = repo
    return repo


# --- 채널 관리 ---------------------------------------------------------------


async def test_list_channels(web_client, mock_web_ctx):
    with _patch_ctx(mock_web_ctx):
        _channel_repo(mock_web_ctx)

        response = web_client.get("/api/youtube/channels")

        assert response.status_code == 200
        assert response.json()[0]["handle"] == "chesleytv"


async def test_add_channel_resolves_handle_to_channel_id(web_client, mock_web_ctx):
    """사용자는 URL/핸들만 안다 — 서버가 channel_id 로 해석해야 한다."""
    with _patch_ctx(mock_web_ctx):
        repo = _channel_repo(mock_web_ctx)
        collector = MagicMock()
        collector.resolve_channel = AsyncMock(return_value={
            "channel_id": "UC" + "a" * 22, "handle": "rsangmoo", "title": "상무니tv",
        })
        mock_web_ctx.youtube_transcript_collector = collector

        response = web_client.post(
            "/api/youtube/channels", json={"url": "https://www.youtube.com/@rsangmoo"}
        )

        assert response.status_code == 200
        assert response.json()["added"] is True
        repo.add.assert_awaited_once_with(
            "UC" + "a" * 22, handle="rsangmoo", title="상무니tv"
        )


async def test_add_channel_reports_unresolvable_handle(web_client, mock_web_ctx):
    with _patch_ctx(mock_web_ctx):
        _channel_repo(mock_web_ctx)
        collector = MagicMock()
        collector.resolve_channel = AsyncMock(return_value=None)
        mock_web_ctx.youtube_transcript_collector = collector

        response = web_client.post("/api/youtube/channels", json={"url": "@없는채널"})

        assert response.status_code == 400


async def test_add_channel_rejects_blank_url(web_client, mock_web_ctx):
    with _patch_ctx(mock_web_ctx):
        _channel_repo(mock_web_ctx)
        mock_web_ctx.youtube_transcript_collector = MagicMock()

        response = web_client.post("/api/youtube/channels", json={"url": "   "})

        assert response.status_code == 400


async def test_add_channel_reports_duplicate(web_client, mock_web_ctx):
    with _patch_ctx(mock_web_ctx):
        repo = _channel_repo(mock_web_ctx, add=AsyncMock(return_value=False))
        collector = MagicMock()
        collector.resolve_channel = AsyncMock(return_value={
            "channel_id": "UC1", "handle": "chesleytv", "title": "체슬리TV",
        })
        mock_web_ctx.youtube_transcript_collector = collector

        response = web_client.post("/api/youtube/channels", json={"url": "@chesleytv"})

        assert response.status_code == 200
        assert response.json()["added"] is False


async def test_delete_channel(web_client, mock_web_ctx):
    with _patch_ctx(mock_web_ctx):
        repo = _channel_repo(mock_web_ctx)

        response = web_client.delete("/api/youtube/channels/UC1")

        assert response.status_code == 200
        repo.remove.assert_awaited_once_with("UC1")


async def test_delete_unknown_channel_returns_404(web_client, mock_web_ctx):
    with _patch_ctx(mock_web_ctx):
        _channel_repo(mock_web_ctx, remove=AsyncMock(return_value=False))

        response = web_client.delete("/api/youtube/channels/UC9")

        assert response.status_code == 404


async def test_toggle_channel(web_client, mock_web_ctx):
    with _patch_ctx(mock_web_ctx):
        repo = _channel_repo(mock_web_ctx)

        response = web_client.patch(
            "/api/youtube/channels/UC1", json={"enabled": False}
        )

        assert response.status_code == 200
        repo.set_enabled.assert_awaited_once_with("UC1", False)


# --- 리포트 조회 -------------------------------------------------------------


async def test_list_reports(web_client, mock_web_ctx):
    with _patch_ctx(mock_web_ctx):
        _digest_repo(mock_web_ctx)

        response = web_client.get("/api/youtube/reports")

        assert response.status_code == 200
        assert response.json()[0]["report_date"] == "20260810"


async def test_get_report_by_date(web_client, mock_web_ctx):
    with _patch_ctx(mock_web_ctx):
        repo = _digest_repo(mock_web_ctx)

        response = web_client.get("/api/youtube/reports/20260810")

        assert response.status_code == 200
        repo.get.assert_awaited_once_with("20260810")


async def test_get_missing_report_returns_404(web_client, mock_web_ctx):
    with _patch_ctx(mock_web_ctx):
        _digest_repo(mock_web_ctx, get=AsyncMock(return_value=None))

        response = web_client.get("/api/youtube/reports/20991231")

        assert response.status_code == 404


async def test_latest_report(web_client, mock_web_ctx):
    with _patch_ctx(mock_web_ctx):
        _digest_repo(mock_web_ctx)

        response = web_client.get("/api/youtube/reports/latest")

        assert response.status_code == 200
        assert response.json()["report"]["report_date"] == "20260810"


async def test_latest_report_is_wrapped_in_an_object(web_client, mock_web_ctx):
    """bare null 은 프런트 공용 readJsonResponse 가 오류로 처리한다 — 객체로 감싸야 한다."""
    with _patch_ctx(mock_web_ctx):
        _digest_repo(mock_web_ctx, latest=AsyncMock(return_value=None))

        response = web_client.get("/api/youtube/reports/latest")

        assert response.status_code == 200
        assert response.json() == {"report": None}


# --- 수동 실행 ---------------------------------------------------------------


async def test_run_now_triggers_the_task(web_client, mock_web_ctx):
    with _patch_ctx(mock_web_ctx):
        task = MagicMock()
        task.run_once = AsyncMock()
        task.get_progress.return_value = {"running": False, "last_report_date": "20260810"}
        mock_web_ctx.youtube_digest_task = task

        response = web_client.post("/api/youtube/run")

        assert response.status_code == 200
        task.run_once.assert_awaited_once()


async def test_run_now_returns_503_without_task(web_client, mock_web_ctx):
    """AI 비활성이면 태스크가 없다 — 조용히 성공한 척하면 안 된다."""
    with _patch_ctx(mock_web_ctx):
        mock_web_ctx.youtube_digest_task = None

        response = web_client.post("/api/youtube/run")

        assert response.status_code == 503


async def test_status_reports_task_progress(web_client, mock_web_ctx):
    with _patch_ctx(mock_web_ctx):
        task = MagicMock()
        task.get_progress.return_value = {"running": False, "last_error": "blocked"}
        mock_web_ctx.youtube_digest_task = task

        response = web_client.get("/api/youtube/status")

        assert response.status_code == 200
        body = response.json()
        assert body["enabled"] is True
        assert body["progress"]["last_error"] == "blocked"


async def test_status_without_task_says_disabled(web_client, mock_web_ctx):
    with _patch_ctx(mock_web_ctx):
        mock_web_ctx.youtube_digest_task = None

        response = web_client.get("/api/youtube/status")

        assert response.status_code == 200
        assert response.json()["enabled"] is False
