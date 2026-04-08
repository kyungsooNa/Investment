"""
관심종목 저장소 - SQLite 기반 (data/favorites.db).
"""
import aiosqlite
from pathlib import Path

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS favorites (
    code     TEXT PRIMARY KEY,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
)
"""


class FavoriteRepository:
    DB_PATH = Path("data/favorites.db")

    def __init__(self, db_path: Path | None = None):
        self._db_path = db_path or self.DB_PATH
        self._db_path.parent.mkdir(parents=True, exist_ok=True)

    async def _setup(self, conn: aiosqlite.Connection) -> None:
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute(_CREATE_TABLE)
        await conn.commit()

    async def get_all(self) -> list[str]:
        async with aiosqlite.connect(self._db_path) as conn:
            await self._setup(conn)
            async with conn.execute(
                "SELECT code FROM favorites ORDER BY created_at ASC, rowid ASC"
            ) as cur:
                rows = await cur.fetchall()
        return [row[0] for row in rows]

    async def add(self, code: str) -> bool:
        """종목 추가. 이미 존재하면 False 반환 (멱등성 보장)."""
        async with aiosqlite.connect(self._db_path) as conn:
            await self._setup(conn)
            cur = await conn.execute(
                "INSERT OR IGNORE INTO favorites (code) VALUES (?)", (code,)
            )
            await conn.commit()
            return cur.rowcount > 0

    async def remove(self, code: str) -> bool:
        """종목 제거. 없으면 False 반환."""
        async with aiosqlite.connect(self._db_path) as conn:
            await self._setup(conn)
            cur = await conn.execute(
                "DELETE FROM favorites WHERE code = ?", (code,)
            )
            await conn.commit()
            return cur.rowcount > 0

    async def is_favorite(self, code: str) -> bool:
        async with aiosqlite.connect(self._db_path) as conn:
            await self._setup(conn)
            async with conn.execute(
                "SELECT 1 FROM favorites WHERE code = ? LIMIT 1", (code,)
            ) as cur:
                row = await cur.fetchone()
        return row is not None
