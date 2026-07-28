"""
FavoriteRepository 단위 테스트.
"""
import pytest
import aiosqlite
from repositories.favorite_repository import (
    FavoriteRepository,
    MARKET_DOMESTIC,
    MARKET_OVERSEAS_US,
)


@pytest.fixture
async def repo(tmp_path):
    return FavoriteRepository(db_path=tmp_path / "favorites.db")


async def test_initial_empty(repo):
    assert await repo.get_all() == []


async def test_add_new(repo):
    result = await repo.add("005930")
    assert result is True
    assert await repo.get_all() == ["005930"]


async def test_add_duplicate(repo):
    await repo.add("005930")
    result = await repo.add("005930")
    assert result is False
    assert (await repo.get_all()).count("005930") == 1


async def test_add_multiple_preserves_order(repo):
    await repo.add("005930")
    await repo.add("000660")
    await repo.add("035720")
    assert await repo.get_all() == ["005930", "000660", "035720"]


async def test_remove_existing(repo):
    await repo.add("005930")
    result = await repo.remove("005930")
    assert result is True
    assert await repo.get_all() == []


async def test_remove_nonexistent(repo):
    result = await repo.remove("999999")
    assert result is False


async def test_is_favorite_true(repo):
    await repo.add("005930")
    assert await repo.is_favorite("005930") is True


async def test_is_favorite_false(repo):
    assert await repo.is_favorite("005930") is False


async def test_persists_to_db(repo, tmp_path):
    await repo.add("005930")
    db_path = tmp_path / "favorites.db"
    assert db_path.exists()
    async with aiosqlite.connect(db_path) as conn:
        async with conn.execute("SELECT code FROM favorites") as cur:
            rows = await cur.fetchall()
    assert ("005930",) in rows


async def test_idempotent_add(repo):
    """동일 코드 여러 번 추가해도 중복 없음 (멱등성)."""
    for _ in range(3):
        await repo.add("005930")
    assert await repo.get_all() == ["005930"]


async def test_add_and_remove_cycle(repo):
    """추가 → 제거 → 재추가 정상 동작."""
    await repo.add("005930")
    await repo.remove("005930")
    result = await repo.add("005930")
    assert result is True
    assert await repo.get_all() == ["005930"]


async def test_overseas_market_is_separate_from_domestic(repo):
    """미국장 심볼은 국내 목록에 섞이지 않는다."""
    await repo.add("005930")
    await repo.add("AAPL", market=MARKET_OVERSEAS_US)

    assert await repo.get_all() == ["005930"]
    assert await repo.get_all(market=MARKET_OVERSEAS_US) == ["AAPL"]


async def test_same_code_can_exist_in_both_markets(repo):
    """동일 코드라도 market이 다르면 각각 등록된다."""
    assert await repo.add("TEST") is True
    assert await repo.add("TEST", market=MARKET_OVERSEAS_US) is True

    assert await repo.get_all(market=MARKET_DOMESTIC) == ["TEST"]
    assert await repo.get_all(market=MARKET_OVERSEAS_US) == ["TEST"]


async def test_remove_and_is_favorite_scoped_by_market(repo):
    await repo.add("AAPL", market=MARKET_OVERSEAS_US)

    assert await repo.is_favorite("AAPL") is False
    assert await repo.is_favorite("AAPL", market=MARKET_OVERSEAS_US) is True
    assert await repo.remove("AAPL") is False
    assert await repo.remove("AAPL", market=MARKET_OVERSEAS_US) is True
    assert await repo.get_all(market=MARKET_OVERSEAS_US) == []


async def test_legacy_schema_migrates_to_domestic(tmp_path):
    """market 컬럼이 없던 기존 DB는 domestic으로 마이그레이션된다."""
    db_path = tmp_path / "legacy.db"
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(
            "CREATE TABLE favorites ("
            " code TEXT PRIMARY KEY,"
            " created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')))"
        )
        await conn.execute("INSERT INTO favorites (code) VALUES ('005930')")
        await conn.commit()

    repo = FavoriteRepository(db_path=db_path)

    assert await repo.get_all() == ["005930"]
    assert await repo.get_all(market=MARKET_OVERSEAS_US) == []
    assert await repo.add("AAPL", market=MARKET_OVERSEAS_US) is True


def test_init_with_default_db_path(monkeypatch, tmp_path):
    """db_path를 지정하지 않았을 때 기본 DB_PATH를 사용하는지 검증 (Coverage 100% 달성용)."""
    default_path = tmp_path / "default_data" / "favorites.db"
    monkeypatch.setattr(FavoriteRepository, "DB_PATH", default_path)
    repo = FavoriteRepository()
    assert repo._db_path == default_path
    assert default_path.parent.exists()
