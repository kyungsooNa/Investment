"""유튜브 자막 수집 서비스 단위 테스트 (네트워크·라이브러리 미설치와 무관하게 통과해야 함)."""
from datetime import datetime, timedelta, timezone

import pytest

from services.youtube_transcript_collector_service import (
    TranscriptBlocked,
    TranscriptUnavailable,
    YoutubeTranscriptCollectorService,
)


def _rss(entries: list[tuple[str, str, str]]) -> str:
    items = "".join(
        f"<entry><yt:videoId>{vid}</yt:videoId><title>{title}</title>"
        f"<published>{published}</published></entry>"
        for vid, title, published in entries
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015"'
        ' xmlns="http://www.w3.org/2005/Atom">'
        f"{items}</feed>"
    )


def _iso(hours_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()


class _FakeResponse:
    def __init__(self, text: str, status_code: int = 200):
        self.text = text
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeHttp:
    """httpx.AsyncClient 대역 — URL 접두사별 응답을 돌려준다."""

    def __init__(self, routes: dict):
        self._routes = routes
        self.calls: list[str] = []

    async def get(self, url, **kwargs):
        self.calls.append(url)
        for prefix, response in self._routes.items():
            if url.startswith(prefix):
                return response
        return _FakeResponse("", status_code=404)


def _service(routes: dict, fetcher=None) -> YoutubeTranscriptCollectorService:
    return YoutubeTranscriptCollectorService(
        http_client=_FakeHttp(routes),
        transcript_fetcher=fetcher,
        request_interval_sec=0,
    )


async def test_resolve_channel_extracts_external_id():
    """watch/채널 페이지는 channelId 가 아니라 externalId 키를 쓴다 (2026-08 실측)."""
    html = '<html>..."externalId":"UCXST0Hq6CAmG0dmo3jgrlEw"...' \
           '<meta property="og:title" content="체슬리TV"></html>'
    svc = _service({"https://www.youtube.com/@": _FakeResponse(html)})

    result = await svc.resolve_channel("https://www.youtube.com/@chesleytv")

    assert result["channel_id"] == "UCXST0Hq6CAmG0dmo3jgrlEw"
    assert result["handle"] == "chesleytv"
    assert result["title"] == "체슬리TV"


async def test_resolve_channel_accepts_bare_handle():
    html = '"externalId":"UC' + "a" * 22 + '"'
    svc = _service({"https://www.youtube.com/@": _FakeResponse(html)})

    result = await svc.resolve_channel("@rsangmoo")

    assert result["handle"] == "rsangmoo"


async def test_resolve_channel_returns_none_when_not_found():
    svc = _service({"https://www.youtube.com/@": _FakeResponse("<html>없음</html>")})

    assert await svc.resolve_channel("nope") is None


async def test_list_recent_videos_filters_by_age():
    feed = _rss(
        [
            ("new1", "최근 영상", _iso(3)),
            ("old1", "지난주 영상", _iso(72)),
        ]
    )
    svc = _service({"https://www.youtube.com/feeds": _FakeResponse(feed)})

    videos = await svc.list_recent_videos("UC" + "a" * 22, since_hours=24)

    assert [v["video_id"] for v in videos] == ["new1"]
    assert videos[0]["url"] == "https://www.youtube.com/watch?v=new1"


async def test_list_recent_videos_applies_limit():
    feed = _rss([(f"v{i}", f"영상{i}", _iso(1)) for i in range(5)])
    svc = _service({"https://www.youtube.com/feeds": _FakeResponse(feed)})

    videos = await svc.list_recent_videos("UC" + "a" * 22, since_hours=24, limit=2)

    assert len(videos) == 2


async def test_collect_skips_live_video_without_failing_the_batch():
    """라이브는 VideoUnplayable 로 실패한다 — 스킵하고 나머지는 계속 수집해야 한다."""
    feed = _rss([("live1", "모닝브리프 라이브", _iso(1)), ("ok1", "정규 영상", _iso(2))])

    def fetcher(video_id, languages):
        if video_id == "live1":
            raise TranscriptUnavailable("live")
        return "오늘 삼성전자 얘기를 하겠습니다."

    svc = _service({"https://www.youtube.com/feeds": _FakeResponse(feed)}, fetcher)

    items = await svc.collect([{"channel_id": "UC" + "a" * 22, "title": "체슬리TV"}])

    assert len(items) == 2
    by_id = {item["video_id"]: item for item in items}
    assert by_id["live1"]["skip_reason"] == "transcript_unavailable"
    assert by_id["live1"]["transcript"] == ""
    assert by_id["ok1"]["skip_reason"] is None
    assert "삼성전자" in by_id["ok1"]["transcript"]


async def test_collect_continues_when_one_channel_feed_fails():
    """한 채널의 RSS 실패가 다른 채널 수집을 막으면 안 된다."""
    feed = _rss([("ok1", "정규 영상", _iso(1))])
    good = "UC" + "a" * 22
    bad = "UC" + "b" * 22
    routes = {
        f"https://www.youtube.com/feeds/videos.xml?channel_id={good}": _FakeResponse(feed),
        f"https://www.youtube.com/feeds/videos.xml?channel_id={bad}": _FakeResponse("", 500),
    }
    svc = YoutubeTranscriptCollectorService(
        http_client=_FakeHttp(routes),
        transcript_fetcher=lambda video_id, languages: "본문",
    )

    items = await svc.collect(
        [{"channel_id": bad, "title": "깨진채널"}, {"channel_id": good, "title": "정상채널"}]
    )

    assert [item["video_id"] for item in items] == ["ok1"]


async def test_collect_marks_empty_transcript_as_skipped():
    feed = _rss([("v1", "자막없음", _iso(1))])
    svc = _service(
        {"https://www.youtube.com/feeds": _FakeResponse(feed)},
        lambda video_id, languages: "   ",
    )

    items = await svc.collect([{"channel_id": "UC" + "a" * 22, "title": "채널"}])

    assert items[0]["skip_reason"] == "empty_transcript"


async def test_collect_looks_past_live_videos_to_reach_the_quota():
    """채널 최신 영상이 라이브면 그 아래까지 내려가야 한다 (실측: 5채널 중 4채널이 빈손)."""
    feed = _rss(
        [
            ("live1", "LIVE 장전", _iso(1)),
            ("live2", "당일전략 라이브", _iso(2)),
            ("ok1", "정규 영상", _iso(3)),
            ("ok2", "그 전 영상", _iso(4)),
        ]
    )

    def fetcher(video_id, languages):
        if video_id.startswith("live"):
            raise TranscriptUnavailable("live")
        return "자막 본문"

    svc = _service({"https://www.youtube.com/feeds": _FakeResponse(feed)}, fetcher)

    items = await svc.collect(
        [{"channel_id": "UC" + "a" * 22, "title": "채널"}], max_videos_per_channel=1
    )

    usable = [item for item in items if not item["skip_reason"]]
    assert [item["video_id"] for item in usable] == ["ok1"]
    # 스킵한 라이브도 기록에 남아 "왜 적게 걷혔는지"가 리포트에서 보여야 한다.
    assert [item["video_id"] for item in items] == ["live1", "live2", "ok1"]


async def test_collect_respects_max_videos_per_channel():
    feed = _rss([(f"v{i}", f"영상{i}", _iso(1)) for i in range(6)])
    svc = _service(
        {"https://www.youtube.com/feeds": _FakeResponse(feed)},
        lambda video_id, languages: "본문",
    )

    items = await svc.collect(
        [{"channel_id": "UC" + "a" * 22, "title": "채널"}], max_videos_per_channel=3
    )

    assert len(items) == 3


async def test_ip_block_aborts_the_batch_instead_of_looking_like_normal_skips():
    """IP 차단(실측 IpBlocked)은 영상별 스킵이 아니라 배치 전체의 실패다.

    그냥 스킵으로 흘리면 '오늘은 볼 영상이 없었다'는 정상 리포트처럼 보인다.
    """
    feed = _rss([(f"v{i}", f"영상{i}", _iso(1)) for i in range(3)])

    def fetcher(video_id, languages):
        raise TranscriptBlocked("IpBlocked")

    good = "UC" + "a" * 22
    other = "UC" + "b" * 22
    svc = _service({"https://www.youtube.com/feeds": _FakeResponse(feed)}, fetcher)

    items = await svc.collect(
        [{"channel_id": good, "title": "채널1"}, {"channel_id": other, "title": "채널2"}]
    )

    assert [item["skip_reason"] for item in items] == ["blocked"]
    assert svc.was_blocked is True


async def test_blocked_flag_is_false_on_a_clean_run():
    feed = _rss([("v1", "영상", _iso(1))])
    svc = _service(
        {"https://www.youtube.com/feeds": _FakeResponse(feed)},
        lambda video_id, languages: "본문",
    )

    await svc.collect([{"channel_id": "UC" + "a" * 22, "title": "채널"}])

    assert svc.was_blocked is False


def test_default_request_interval_keeps_margin_against_rate_limiting():
    """기본 간격이 좁아지면 429(구글 "Sorry...")를 맞아 그날 리포트가 통째로 날아간다.

    다른 테스트는 전부 간격을 명시로 넘겨 기본값을 덮으므로 여기서만 잠긴다.
    """
    svc = YoutubeTranscriptCollectorService()

    assert svc._request_interval_sec >= 5.0


async def test_transcript_fetches_are_spaced_out():
    """연속 호출이 YouTube 차단을 부른다 — 영상 사이에 간격을 둬야 한다."""
    feed = _rss([(f"v{i}", f"영상{i}", _iso(1)) for i in range(3)])
    slept: list[float] = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    svc = YoutubeTranscriptCollectorService(
        http_client=_FakeHttp({"https://www.youtube.com/feeds": _FakeResponse(feed)}),
        transcript_fetcher=lambda video_id, languages: "본문",
        request_interval_sec=1.5,
        sleeper=fake_sleep,
    )

    await svc.collect([{"channel_id": "UC" + "a" * 22, "title": "채널"}], max_videos_per_channel=3)

    assert slept == [1.5, 1.5]  # 첫 요청 앞에는 대기하지 않는다


async def test_fetch_transcript_raises_when_library_missing():
    """라이브러리 미설치는 조용한 빈 리포트가 아니라 명시적 실패여야 한다."""
    svc = YoutubeTranscriptCollectorService(http_client=_FakeHttp({}), transcript_fetcher=None)
    svc._load_default_fetcher = lambda: None  # 설치 안 된 상황 재현

    with pytest.raises(TranscriptUnavailable):
        await svc.fetch_transcript("v1")
