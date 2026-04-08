"""
FavoriteRepository 단위 테스트.
"""
import pytest
import aiosqlite
from repositories.favorite_repository import FavoriteRepository


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
