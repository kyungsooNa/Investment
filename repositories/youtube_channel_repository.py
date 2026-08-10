"""
유튜브 일일 다이제스트 대상 채널 저장소 - SQLite 기반 (data/youtube_channels.db).

채널은 웹 UI에서 추가/삭제/on-off 하므로, 시드는 "최초 1회"만 넣는다.
사용자가 지운 채널이 재시작 때 되살아나지 않도록 seeded 플래그를 meta 테이블에 남긴다.
"""
import re
from pathlib import Path

import aiosqlite

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS youtube_channels (
    channel_id TEXT PRIMARY KEY,
    handle     TEXT NOT NULL DEFAULT '',
    title      TEXT NOT NULL DEFAULT '',
    enabled    INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
)
"""

_CREATE_META = """
CREATE TABLE IF NOT EXISTS youtube_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
)
"""

_SEEDED_KEY = "seeded"

# 2026-08-10 실측으로 channelId·한국어 자막(ASR) 존재를 확인한 채널.
SEED_CHANNELS = [
    {"channel_id": "UCXST0Hq6CAmG0dmo3jgrlEw", "handle": "chesleytv", "title": "체슬리TV"},
    {"channel_id": "UCdOjVxkj5JA0iDu3_xcsTyQ", "handle": "koreanstockrider", "title": "증시각도기TV"},
    {"channel_id": "UCiDmfbYvuMEVbRxPmFP4sng", "handle": "rsangmoo", "title": "상무니tv"},
    {"channel_id": "UCrPcpr8lzO1GTSSZjm2lV2w", "handle": "Power_bus2", "title": "세력버스"},
    {"channel_id": "UC9Cilvlvu_7YWYQGhSCrFOg", "handle": "10min_stock", "title": "10분 주식"},
]

_RE_CHANNEL_ID = re.compile(r"^UC[\w-]{22}$")


class YoutubeChannelRepository:
    DB_PATH = Path("data/youtube_channels.db")

    def __init__(self, db_path: Path | None = None):
        self._db_path = db_path or self.DB_PATH
        self._db_path.parent.mkdir(parents=True, exist_ok=True)

    async def _setup(self, conn: aiosqlite.Connection) -> None:
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute(_CREATE_TABLE)
        await conn.execute(_CREATE_META)
        await conn.commit()

    async def get_all(self, enabled_only: bool = False) -> list[dict]:
        query = "SELECT channel_id, handle, title, enabled FROM youtube_channels"
        if enabled_only:
            query += " WHERE enabled = 1"
        query += " ORDER BY created_at ASC, rowid ASC"
        async with aiosqlite.connect(self._db_path) as conn:
            await self._setup(conn)
            async with conn.execute(query) as cur:
                rows = await cur.fetchall()
        return [
            {
                "channel_id": row[0],
                "handle": row[1],
                "title": row[2],
                "enabled": bool(row[3]),
            }
            for row in rows
        ]

    async def add(self, channel_id: str, handle: str = "", title: str = "") -> bool:
        """채널 추가. 이미 있으면 False (멱등성 보장)."""
        channel_id = str(channel_id or "").strip()
        if not _RE_CHANNEL_ID.match(channel_id):
            raise ValueError(f"유효한 채널 ID가 아닙니다 (UC + 22자): {channel_id!r}")
        async with aiosqlite.connect(self._db_path) as conn:
            await self._setup(conn)
            cur = await conn.execute(
                "INSERT OR IGNORE INTO youtube_channels (channel_id, handle, title)"
                " VALUES (?, ?, ?)",
                (channel_id, str(handle or ""), str(title or "")),
            )
            await conn.commit()
            return cur.rowcount > 0

    async def remove(self, channel_id: str) -> bool:
        """채널 제거. 없으면 False."""
        async with aiosqlite.connect(self._db_path) as conn:
            await self._setup(conn)
            cur = await conn.execute(
                "DELETE FROM youtube_channels WHERE channel_id = ?", (channel_id,)
            )
            await conn.commit()
            return cur.rowcount > 0

    async def set_enabled(self, channel_id: str, enabled: bool) -> bool:
        """수집 on/off 전환. 없는 채널이면 False."""
        async with aiosqlite.connect(self._db_path) as conn:
            await self._setup(conn)
            cur = await conn.execute(
                "UPDATE youtube_channels SET enabled = ? WHERE channel_id = ?",
                (1 if enabled else 0, channel_id),
            )
            await conn.commit()
            return cur.rowcount > 0

    async def seed_defaults(self) -> int:
        """최초 1회만 기본 채널을 넣고, 넣은 개수를 반환한다."""
        async with aiosqlite.connect(self._db_path) as conn:
            await self._setup(conn)
            async with conn.execute(
                "SELECT 1 FROM youtube_meta WHERE key = ?", (_SEEDED_KEY,)
            ) as cur:
                if await cur.fetchone():
                    return 0
            inserted = 0
            for channel in SEED_CHANNELS:
                cur = await conn.execute(
                    "INSERT OR IGNORE INTO youtube_channels (channel_id, handle, title)"
                    " VALUES (?, ?, ?)",
                    (channel["channel_id"], channel["handle"], channel["title"]),
                )
                inserted += cur.rowcount > 0
            await conn.execute(
                "INSERT OR REPLACE INTO youtube_meta (key, value) VALUES (?, '1')",
                (_SEEDED_KEY,),
            )
            await conn.commit()
        return inserted
