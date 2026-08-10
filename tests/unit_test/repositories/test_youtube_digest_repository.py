"""유튜브 일일 다이제스트 저장소 단위 테스트."""
from pathlib import Path

import pytest

from repositories.youtube_digest_repository import YoutubeDigestRepository


@pytest.fixture
def repo(tmp_path: Path) -> YoutubeDigestRepository:
    return YoutubeDigestRepository(db_path=tmp_path / "youtube_digest.db")


def _payload(report_date: str = "20260810", **kwargs) -> dict:
    base = {
        "report_date": report_date,
        "video_count": 2,
        "failed_summary_count": 0,
        "digest_text": "오늘의 공통 화두는 반도체다.",
        "mentions": [
            {"name": "삼성전자", "code": "005930", "count": 16, "video_count": 4},
            {"name": "SK하이닉스", "code": "000660", "count": 8, "video_count": 3},
        ],
        "videos": [
            {
                "video_id": "v1",
                "title": "영상1",
                "channel_title": "체슬리TV",
                "url": "https://www.youtube.com/watch?v=v1",
                "published": "2026-08-10T07:00:00+00:00",
                "summary": "v1 요약",
            }
        ],
    }
    base.update(kwargs)
    return base


async def test_save_then_get_roundtrip(repo):
    await repo.save(_payload())

    stored = await repo.get("20260810")

    assert stored["report_date"] == "20260810"
    assert stored["digest_text"] == "오늘의 공통 화두는 반도체다."
    assert stored["video_count"] == 2
    assert stored["failed_summary_count"] == 0
    assert [m["name"] for m in stored["mentions"]] == ["삼성전자", "SK하이닉스"]
    assert stored["mentions"][0]["code"] == "005930"
    assert stored["videos"][0]["summary"] == "v1 요약"


async def test_get_returns_none_for_missing_date(repo):
    assert await repo.get("20991231") is None


async def test_mentions_keep_ranking_order(repo):
    """언급 순위는 리포트의 핵심이라 저장·조회를 거쳐도 순서가 보존돼야 한다."""
    mentions = [
        {"name": f"종목{i}", "code": f"{i:06d}", "count": 10 - i, "video_count": 5 - i}
        for i in range(5)
    ]
    await repo.save(_payload(mentions=mentions))

    stored = await repo.get("20260810")

    assert [m["name"] for m in stored["mentions"]] == [m["name"] for m in mentions]


async def test_save_is_idempotent_and_replaces_previous_run(repo):
    """같은 날 재실행하면 덮어써야 한다 — 옛 언급이 남아 섞이면 안 된다."""
    await repo.save(_payload(digest_text="1차", mentions=[
        {"name": "옛종목", "code": "000001", "count": 3, "video_count": 1}
    ]))

    await repo.save(_payload(digest_text="2차"))

    stored = await repo.get("20260810")
    assert stored["digest_text"] == "2차"
    assert [m["name"] for m in stored["mentions"]] == ["삼성전자", "SK하이닉스"]


async def test_save_replaces_previous_videos(repo):
    await repo.save(_payload(videos=[
        {
            "video_id": "old",
            "title": "옛영상",
            "channel_title": "채널",
            "url": "u",
            "published": "p",
            "summary": "옛 요약",
        }
    ]))

    await repo.save(_payload())

    stored = await repo.get("20260810")
    assert [v["video_id"] for v in stored["videos"]] == ["v1"]


async def test_list_recent_returns_newest_first(repo):
    for date in ["20260808", "20260810", "20260809"]:
        await repo.save(_payload(date))

    recent = await repo.list_recent(limit=2)

    assert [row["report_date"] for row in recent] == ["20260810", "20260809"]


async def test_list_recent_is_a_summary_without_heavy_fields(repo):
    """목록 화면이 리포트 본문·영상 요약까지 끌어오면 응답이 무거워진다."""
    await repo.save(_payload())

    row = (await repo.list_recent())[0]

    assert row["video_count"] == 2
    assert "videos" not in row
    assert "digest_text" not in row


async def test_list_recent_on_empty_db(repo):
    assert await repo.list_recent() == []


async def test_latest_returns_most_recent_report(repo):
    await repo.save(_payload("20260809", digest_text="어제"))
    await repo.save(_payload("20260810", digest_text="오늘"))

    assert (await repo.latest())["digest_text"] == "오늘"


async def test_latest_returns_none_on_empty_db(repo):
    assert await repo.latest() is None
