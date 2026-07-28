"""
관심종목 저장소 - SQLite 기반 (data/favorites.db).
"""
import aiosqlite
from pathlib import Path

MARKET_DOMESTIC = "domestic"
MARKET_OVERSEAS_US = "overseas_us"

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS favorites (
    code     TEXT NOT NULL,
    market   TEXT NOT NULL DEFAULT 'domestic',
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    PRIMARY KEY (code, market)
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
        await self._migrate_market_column(conn)

    async def _migrate_market_column(self, conn: aiosqlite.Connection) -> None:
        """market 컬럼이 없던 기존 DB를 복합키 스키마로 재작성한다 (기존 행은 domestic)."""
        async with conn.execute("PRAGMA table_info(favorites)") as cur:
            columns = [row[1] for row in await cur.fetchall()]
        if "market" in columns:
            return
        await conn.execute("ALTER TABLE favorites RENAME TO favorites_legacy")
        await conn.execute(_CREATE_TABLE)
        await conn.execute(
            "INSERT INTO favorites (code, market, created_at)"
            f" SELECT code, '{MARKET_DOMESTIC}', created_at FROM favorites_legacy"
        )
        await conn.execute("DROP TABLE favorites_legacy")
        await conn.commit()

    async def get_all(self, market: str = MARKET_DOMESTIC) -> list[str]:
        async with aiosqlite.connect(self._db_path) as conn:
            await self._setup(conn)
            async with conn.execute(
                "SELECT code FROM favorites WHERE market = ?"
                " ORDER BY created_at ASC, rowid ASC",
                (market,),
            ) as cur:
                rows = await cur.fetchall()
        return [row[0] for row in rows]

    async def add(self, code: str, market: str = MARKET_DOMESTIC) -> bool:
        """종목 추가. 이미 존재하면 False 반환 (멱등성 보장)."""
        async with aiosqlite.connect(self._db_path) as conn:
            await self._setup(conn)
            cur = await conn.execute(
                "INSERT OR IGNORE INTO favorites (code, market) VALUES (?, ?)",
                (code, market),
            )
            await conn.commit()
            return cur.rowcount > 0

    async def remove(self, code: str, market: str = MARKET_DOMESTIC) -> bool:
        """종목 제거. 없으면 False 반환."""
        async with aiosqlite.connect(self._db_path) as conn:
            await self._setup(conn)
            cur = await conn.execute(
                "DELETE FROM favorites WHERE code = ? AND market = ?", (code, market)
            )
            await conn.commit()
            return cur.rowcount > 0

    async def is_favorite(self, code: str, market: str = MARKET_DOMESTIC) -> bool:
        async with aiosqlite.connect(self._db_path) as conn:
            await self._setup(conn)
            async with conn.execute(
                "SELECT 1 FROM favorites WHERE code = ? AND market = ? LIMIT 1",
                (code, market),
            ) as cur:
                row = await cur.fetchone()
        return row is not None
