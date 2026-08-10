"""유튜브 리포트 대상 채널 저장소 단위 테스트."""
from pathlib import Path

import pytest

from repositories.youtube_channel_repository import (
    SEED_CHANNELS,
    YoutubeChannelRepository,
)


@pytest.fixture
def repo(tmp_path: Path) -> YoutubeChannelRepository:
    return YoutubeChannelRepository(db_path=tmp_path / "youtube_channels.db")


async def test_add_and_get_all(repo):
    assert await repo.add("UC" + "a" * 22, handle="foo", title="푸TV") is True

    rows = await repo.get_all()

    assert len(rows) == 1
    assert rows[0]["channel_id"] == "UC" + "a" * 22
    assert rows[0]["handle"] == "foo"
    assert rows[0]["title"] == "푸TV"
    assert rows[0]["enabled"] is True


async def test_add_is_idempotent(repo):
    cid = "UC" + "b" * 22
    assert await repo.add(cid, handle="bar") is True
    assert await repo.add(cid, handle="bar") is False

    assert len(await repo.get_all()) == 1


async def test_add_rejects_invalid_channel_id(repo):
    """UC 로 시작하는 24자만 허용 — 핸들이 잘못 저장되면 수집 전체가 조용히 빈다."""
    for bad in ["", "foo", "UCshort", "AB" + "c" * 22]:
        with pytest.raises(ValueError):
            await repo.add(bad)

    assert await repo.get_all() == []


async def test_remove(repo):
    cid = "UC" + "c" * 22
    await repo.add(cid)

    assert await repo.remove(cid) is True
    assert await repo.remove(cid) is False
    assert await repo.get_all() == []


async def test_set_enabled_and_enabled_only_filter(repo):
    on, off = "UC" + "d" * 22, "UC" + "e" * 22
    await repo.add(on)
    await repo.add(off)

    assert await repo.set_enabled(off, False) is True

    assert len(await repo.get_all()) == 2
    enabled = await repo.get_all(enabled_only=True)
    assert [row["channel_id"] for row in enabled] == [on]


async def test_set_enabled_returns_false_for_unknown_channel(repo):
    assert await repo.set_enabled("UC" + "f" * 22, False) is False


async def test_seed_defaults_inserts_once(repo):
    assert await repo.seed_defaults() == len(SEED_CHANNELS)
    assert await repo.seed_defaults() == 0

    rows = await repo.get_all()
    assert len(rows) == len(SEED_CHANNELS)
    assert {row["handle"] for row in rows} == {c["handle"] for c in SEED_CHANNELS}


async def test_seed_defaults_does_not_resurrect_removed_channel(repo):
    """사용자가 UI에서 지운 채널이 재시작 때 되살아나면 안 된다."""
    await repo.seed_defaults()
    removed = SEED_CHANNELS[0]["channel_id"]
    await repo.remove(removed)

    assert await repo.seed_defaults() == 0
    assert removed not in {row["channel_id"] for row in await repo.get_all()}


def test_seed_channels_are_wellformed():
    for channel in SEED_CHANNELS:
        assert channel["channel_id"].startswith("UC")
        assert len(channel["channel_id"]) == 24
        assert channel["handle"]
