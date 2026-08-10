"""유튜브 채널의 최근 영상 자막을 수집하는 서비스.

2026-08-10 실측으로 확인한 제약 — 코드를 바꾸기 전에 반드시 읽을 것:

- 채널 페이지 HTML에는 `"channelId"` 가 아니라 **`"externalId"`** 키에 UC... 가 들어 있다.
- 영상 목록은 RSS(`feeds/videos.xml?channel_id=`)로 받는다. API 키·쿼터가 필요 없고
  최근 15개를 준다. 하루 1회 배치에는 이걸로 충분하다.
- watch 페이지의 `captionTracks[].baseUrl` 은 **HTTP 200 + 빈 본문**을 돌려준다.
  innertube WEB 은 UNPLAYABLE, ANDROID/IOS 는 HTTP 400. 즉 직접 스크래핑은 죽은 경로다.
  실제로 동작하는 유일한 방법이 `youtube-transcript-api` 라서 의존성으로 넣었다.
- 라이브 방송은 `VideoUnplayable` 로 실패한다. 실패가 아니라 **스킵**으로 처리한다.
- 짧은 시간에 반복 호출하면 `IpBlocked` 로 IP가 막힌다(실측: 연속 실행 후 15편 전량 실패).
  그래서 (1) 영상 사이에 간격을 두고, (2) 차단은 영상별 스킵이 아니라 **배치 중단**으로
  다룬다. 차단을 스킵으로 흘리면 "오늘은 볼 영상이 없었다"는 정상 리포트처럼 보인다.
"""
from __future__ import annotations

import asyncio
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional, Sequence

import httpx

_CHANNEL_URL = "https://www.youtube.com/@{handle}"
_FEED_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
_WATCH_URL = "https://www.youtube.com/watch?v={video_id}"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9",
}

_NS = {
    "a": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
}

_RE_EXTERNAL_ID = re.compile(r'"externalId":"(UC[\w-]{22})"')
_RE_OG_TITLE = re.compile(r'<meta property="og:title" content="([^"]*)"')
_RE_HANDLE = re.compile(r"(?:youtube\.com/)?@?([\w.-]+)/?$")

# 라이브·자막 없는 영상을 지나쳐 목표 편수를 채우기 위해 더 보는 후보 수.
_SKIP_ALLOWANCE = 3

# youtube-transcript-api 가 IP 차단 시 올리는 예외 이름. 내부 클래스를 import 하면
# 라이브러리 버전에 묶이므로 이름으로 판별한다.
_BLOCKED_EXCEPTIONS = frozenset({"IpBlocked", "RequestBlocked"})


class TranscriptUnavailable(RuntimeError):
    """자막을 가져올 수 없는 영상 (라이브/자막 비활성)."""


class TranscriptBlocked(TranscriptUnavailable):
    """YouTube 가 IP를 차단했다. 영상 문제가 아니라 배치 전체가 진행 불가인 상태."""


class YoutubeTranscriptCollectorService:
    """채널 해석 → 최근 영상 목록 → 자막 취득까지 담당한다."""

    def __init__(
        self,
        logger: Optional[logging.Logger] = None,
        timeout_sec: float = 20.0,
        http_client=None,
        transcript_fetcher: Optional[Callable[[str, Sequence[str]], str]] = None,
        languages: Sequence[str] = ("ko",),
        request_interval_sec: float = 2.0,
        sleeper: Optional[Callable[[float], Any]] = None,
    ):
        self._logger = logger or logging.getLogger(__name__)
        self._timeout_sec = float(timeout_sec)
        self._http_client = http_client
        self._transcript_fetcher = transcript_fetcher
        self._languages = tuple(languages)
        self._request_interval_sec = max(0.0, float(request_interval_sec))
        self._sleeper = sleeper or asyncio.sleep
        self.was_blocked = False

    # --- HTTP ---------------------------------------------------------------

    async def _get(self, url: str) -> str:
        if self._http_client is not None:
            response = await self._http_client.get(url, headers=_HEADERS)
            response.raise_for_status()
            return response.text
        async with httpx.AsyncClient(
            follow_redirects=True, timeout=self._timeout_sec
        ) as client:
            response = await client.get(url, headers=_HEADERS)
            response.raise_for_status()
            return response.text

    # --- 채널 -----------------------------------------------------------------

    async def resolve_channel(self, handle_or_url: str) -> Optional[dict]:
        """핸들/URL 을 channel_id 로 해석한다. 실패 시 None."""
        raw = str(handle_or_url or "").strip()
        matched = _RE_HANDLE.search(raw)
        if not matched:
            return None
        handle = matched.group(1)
        try:
            html = await self._get(_CHANNEL_URL.format(handle=handle))
        except Exception as e:
            self._logger.warning(f"유튜브 채널 페이지 조회 실패 (@{handle}): {e}")
            return None
        found = _RE_EXTERNAL_ID.search(html)
        if not found:
            self._logger.warning(f"채널 ID 추출 실패 (@{handle}) — 페이지 구조 변경 의심")
            return None
        title = _RE_OG_TITLE.search(html)
        return {
            "channel_id": found.group(1),
            "handle": handle,
            "title": title.group(1) if title else "",
        }

    async def list_recent_videos(
        self, channel_id: str, *, since_hours: float = 24.0, limit: int = 10
    ) -> list[dict]:
        """RSS 에서 since_hours 이내 업로드분만 최신순으로 돌려준다."""
        xml_text = await self._get(_FEED_URL.format(channel_id=channel_id))
        root = ET.fromstring(xml_text)
        cutoff = datetime.now(timezone.utc) - timedelta(hours=float(since_hours))
        videos: list[dict] = []
        for entry in root.findall("a:entry", _NS):
            video_id = entry.findtext("yt:videoId", "", _NS)
            published = entry.findtext("a:published", "", _NS)
            if not video_id or not self._is_recent(published, cutoff):
                continue
            videos.append(
                {
                    "video_id": video_id,
                    "title": entry.findtext("a:title", "", _NS),
                    "published": published,
                    "url": _WATCH_URL.format(video_id=video_id),
                }
            )
            if len(videos) >= max(1, int(limit)):
                break
        return videos

    @staticmethod
    def _is_recent(published: str, cutoff: datetime) -> bool:
        try:
            return datetime.fromisoformat(published) >= cutoff
        except ValueError:
            return False

    # --- 자막 -----------------------------------------------------------------

    def _load_default_fetcher(self) -> Optional[Callable[[str, Sequence[str]], str]]:
        """youtube-transcript-api 를 지연 import 한다 (미설치 시 None)."""
        try:
            from youtube_transcript_api import YouTubeTranscriptApi
        except ImportError:
            return None

        api = YouTubeTranscriptApi()

        def fetch(video_id: str, languages: Sequence[str]) -> str:
            fetched = api.fetch(video_id, languages=list(languages))
            return " ".join(snippet.text for snippet in fetched)

        return fetch

    async def fetch_transcript(self, video_id: str) -> str:
        """자막 텍스트를 반환한다. 취득 불가면 TranscriptUnavailable."""
        fetcher = self._transcript_fetcher or self._load_default_fetcher()
        if fetcher is None:
            raise TranscriptUnavailable(
                "youtube-transcript-api 가 설치되어 있지 않습니다 "
                "(pip install youtube-transcript-api)"
            )
        try:
            # 라이브러리가 동기 API 라 이벤트 루프를 막지 않도록 스레드로 넘긴다.
            return await asyncio.to_thread(fetcher, video_id, self._languages)
        except TranscriptUnavailable:
            raise
        except Exception as e:
            name = type(e).__name__
            if name in _BLOCKED_EXCEPTIONS:
                raise TranscriptBlocked(f"{name}: {e}") from e
            raise TranscriptUnavailable(f"{name}: {e}") from e

    # --- 수집 -----------------------------------------------------------------

    async def collect(
        self,
        channels: Sequence[dict],
        *,
        since_hours: float = 24.0,
        max_videos_per_channel: int = 5,
    ) -> list[dict]:
        """채널 목록의 최근 영상 자막을 모은다.

        `max_videos_per_channel` 은 후보 수가 아니라 **자막을 확보한 편수** 상한이다.
        채널의 최신 영상이 라이브인 경우가 흔해(실측: 5채널 중 4채널) 후보를 상한만큼만
        보면 그 채널이 통째로 빈손이 된다. 아래로 몇 편 더 내려가며 채운다.

        한 채널·한 영상의 실패가 배치 전체를 멈추지 않는다. 실패는 skip_reason 으로
        남겨 리포트에서 "몇 편을 못 읽었는지"가 보이게 한다.
        """
        quota = max(1, int(max_videos_per_channel))
        self.was_blocked = False
        items: list[dict] = []
        requested = 0
        for channel in channels or []:
            channel_id = channel.get("channel_id")
            if not channel_id:
                continue
            try:
                videos = await self.list_recent_videos(
                    channel_id,
                    since_hours=since_hours,
                    limit=quota + _SKIP_ALLOWANCE,
                )
            except Exception as e:
                self._logger.warning(f"유튜브 RSS 조회 실패 ({channel_id}): {e}")
                continue
            collected = 0
            for video in videos:
                if requested:
                    await self._pace()
                requested += 1
                item = await self._collect_one(channel, video)
                items.append(item)
                if item["skip_reason"] == "blocked":
                    self.was_blocked = True
                    self._logger.error(
                        "YouTube 가 IP를 차단해 자막 수집을 중단합니다 — 잠시 후 재시도하세요."
                    )
                    return items
                if item["skip_reason"] is None:
                    collected += 1
                    if collected >= quota:
                        break
        return items

    async def _pace(self) -> None:
        if self._request_interval_sec > 0:
            await self._sleeper(self._request_interval_sec)

    async def _collect_one(self, channel: dict, video: dict) -> dict:
        item = {
            "channel_id": channel.get("channel_id", ""),
            "channel_title": channel.get("title", ""),
            "transcript": "",
            "skip_reason": None,
            **video,
        }
        try:
            transcript = await self.fetch_transcript(video["video_id"])
        except TranscriptBlocked as e:
            self._logger.warning(f"자막 차단 ({video['video_id']}): {e}")
            item["skip_reason"] = "blocked"
            return item
        except TranscriptUnavailable as e:
            self._logger.info(f"자막 스킵 ({video['video_id']}): {e}")
            item["skip_reason"] = "transcript_unavailable"
            return item
        if not str(transcript or "").strip():
            item["skip_reason"] = "empty_transcript"
            return item
        item["transcript"] = transcript
        return item
