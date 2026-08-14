"""
유튜브 일일 다이제스트 저장소 - SQLite 기반 (data/youtube_digest.db).

**자막 원문은 저장하지 않는다.** 영상 1편이 최대 34,000자(2026-08-10 실측)라
원문까지 쌓으면 DB가 빠르게 비대해지는데, 리포트 조회에는 요약만 있으면 된다.

같은 날 재실행은 덮어쓴다. 옛 언급이 남아 새 집계와 섞이면 순위가 오염된다.
"""
import aiosqlite
from pathlib import Path

_CREATE_DIGESTS = """
CREATE TABLE IF NOT EXISTS youtube_digests (
    report_date          TEXT PRIMARY KEY,
    digest_text          TEXT NOT NULL DEFAULT '',
    source               TEXT NOT NULL DEFAULT 'transcript',
    video_count          INTEGER NOT NULL DEFAULT 0,
    failed_summary_count INTEGER NOT NULL DEFAULT 0,
    created_at           TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
)
"""

# rank 컬럼으로 언급 순위를 보존한다. count/video_count 로 다시 정렬하면
# 동점 처리 규칙이 서비스와 어긋날 수 있다.
_CREATE_MENTIONS = """
CREATE TABLE IF NOT EXISTS youtube_digest_mentions (
    report_date TEXT NOT NULL,
    rank        INTEGER NOT NULL,
    name        TEXT NOT NULL,
    code        TEXT NOT NULL DEFAULT '',
    count       INTEGER NOT NULL DEFAULT 0,
    video_count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (report_date, rank)
)
"""

_CREATE_VIDEOS = """
CREATE TABLE IF NOT EXISTS youtube_digest_videos (
    report_date   TEXT NOT NULL,
    ord           INTEGER NOT NULL,
    video_id      TEXT NOT NULL DEFAULT '',
    channel_title TEXT NOT NULL DEFAULT '',
    title         TEXT NOT NULL DEFAULT '',
    url           TEXT NOT NULL DEFAULT '',
    published     TEXT NOT NULL DEFAULT '',
    summary       TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (report_date, ord)
)
"""


class YoutubeDigestRepository:
    DB_PATH = Path("data/youtube_digest.db")

    def __init__(self, db_path: Path | None = None):
        self._db_path = db_path or self.DB_PATH
        self._db_path.parent.mkdir(parents=True, exist_ok=True)

    async def _setup(self, conn: aiosqlite.Connection) -> None:
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute(_CREATE_DIGESTS)
        await self._ensure_source_column(conn)
        await conn.execute(_CREATE_MENTIONS)
        await conn.execute(_CREATE_VIDEOS)
        await conn.commit()

    async def _ensure_source_column(self, conn: aiosqlite.Connection) -> None:
        async with conn.execute("PRAGMA table_info(youtube_digests)") as cur:
            rows = await cur.fetchall()
        columns = {row[1] for row in rows}
        if "source" not in columns:
            await conn.execute(
                "ALTER TABLE youtube_digests"
                " ADD COLUMN source TEXT NOT NULL DEFAULT 'transcript'"
            )

    async def save(self, payload: dict) -> None:
        """하루치 리포트를 저장한다. 같은 날짜가 있으면 통째로 교체한다."""
        report_date = str(payload.get("report_date") or "")
        if not report_date:
            raise ValueError("report_date 가 필요합니다.")
        async with aiosqlite.connect(self._db_path) as conn:
            await self._setup(conn)
            await conn.execute(
                "DELETE FROM youtube_digest_mentions WHERE report_date = ?", (report_date,)
            )
            await conn.execute(
                "DELETE FROM youtube_digest_videos WHERE report_date = ?", (report_date,)
            )
            await conn.execute(
                "INSERT OR REPLACE INTO youtube_digests"
                " (report_date, digest_text, source, video_count, failed_summary_count)"
                " VALUES (?, ?, ?, ?, ?)",
                (
                    report_date,
                    str(payload.get("digest_text") or ""),
                    str(payload.get("source") or "transcript"),
                    int(payload.get("video_count") or 0),
                    int(payload.get("failed_summary_count") or 0),
                ),
            )
            await conn.executemany(
                "INSERT INTO youtube_digest_mentions"
                " (report_date, rank, name, code, count, video_count)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (
                        report_date,
                        rank,
                        str(mention.get("name") or ""),
                        str(mention.get("code") or ""),
                        int(mention.get("count") or 0),
                        int(mention.get("video_count") or 0),
                    )
                    for rank, mention in enumerate(payload.get("mentions") or [])
                ],
            )
            await conn.executemany(
                "INSERT INTO youtube_digest_videos"
                " (report_date, ord, video_id, channel_title, title, url, published, summary)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        report_date,
                        order,
                        str(video.get("video_id") or ""),
                        str(video.get("channel_title") or ""),
                        str(video.get("title") or ""),
                        str(video.get("url") or ""),
                        str(video.get("published") or ""),
                        str(video.get("summary") or ""),
                    )
                    for order, video in enumerate(payload.get("videos") or [])
                ],
            )
            await conn.commit()

    async def get(self, report_date: str) -> dict | None:
        async with aiosqlite.connect(self._db_path) as conn:
            await self._setup(conn)
            async with conn.execute(
                "SELECT report_date, digest_text, source, video_count, failed_summary_count,"
                " created_at FROM youtube_digests WHERE report_date = ?",
                (report_date,),
            ) as cur:
                row = await cur.fetchone()
            if row is None:
                return None
            async with conn.execute(
                "SELECT name, code, count, video_count FROM youtube_digest_mentions"
                " WHERE report_date = ? ORDER BY rank ASC",
                (report_date,),
            ) as cur:
                mention_rows = await cur.fetchall()
            async with conn.execute(
                "SELECT video_id, channel_title, title, url, published, summary"
                " FROM youtube_digest_videos WHERE report_date = ? ORDER BY ord ASC",
                (report_date,),
            ) as cur:
                video_rows = await cur.fetchall()
        return {
            "report_date": row[0],
            "digest_text": row[1],
            "source": row[2],
            "video_count": row[3],
            "failed_summary_count": row[4],
            "created_at": row[5],
            "mentions": [
                {"name": m[0], "code": m[1], "count": m[2], "video_count": m[3]}
                for m in mention_rows
            ],
            "videos": [
                {
                    "video_id": v[0],
                    "channel_title": v[1],
                    "title": v[2],
                    "url": v[3],
                    "published": v[4],
                    "summary": v[5],
                }
                for v in video_rows
            ],
        }

    async def list_recent(self, limit: int = 30) -> list[dict]:
        """목록 화면용 요약. 본문·영상 요약은 담지 않는다 (응답 비대 방지)."""
        async with aiosqlite.connect(self._db_path) as conn:
            await self._setup(conn)
            async with conn.execute(
                "SELECT report_date, source, video_count, failed_summary_count, created_at"
                " FROM youtube_digests ORDER BY report_date DESC LIMIT ?",
                (max(1, int(limit)),),
            ) as cur:
                rows = await cur.fetchall()
        return [
            {
                "report_date": row[0],
                "source": row[1],
                "video_count": row[2],
                "failed_summary_count": row[3],
                "created_at": row[4],
            }
            for row in rows
        ]

    async def latest(self) -> dict | None:
        recent = await self.list_recent(limit=1)
        if not recent:
            return None
        return await self.get(recent[0]["report_date"])
