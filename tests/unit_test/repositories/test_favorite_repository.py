"""
FavoriteRepository 단위 테스트.
"""
import json
import pytest
from repositories.favorite_repository import FavoriteRepository


@pytest.fixture
def repo(tmp_path):
    """임시 경로를 사용하는 FavoriteRepository 픽스처."""
    r = FavoriteRepository.__new__(FavoriteRepository)
    import threading
    r._lock = threading.Lock()
    r.FILE_PATH = tmp_path / "favorites.json"
    r._ensure_file()
    return r


def test_initial_empty(repo):
    assert repo.get_all() == []


def test_add_new(repo):
    result = repo.add("005930")
    assert result is True
    assert repo.get_all() == ["005930"]


def test_add_duplicate(repo):
    repo.add("005930")
    result = repo.add("005930")
    assert result is False
    assert repo.get_all().count("005930") == 1


def test_add_multiple_preserves_order(repo):
    repo.add("005930")
    repo.add("000660")
    repo.add("035720")
    assert repo.get_all() == ["005930", "000660", "035720"]


def test_remove_existing(repo):
    repo.add("005930")
    result = repo.remove("005930")
    assert result is True
    assert repo.get_all() == []


def test_remove_nonexistent(repo):
    result = repo.remove("999999")
    assert result is False


def test_is_favorite_true(repo):
    repo.add("005930")
    assert repo.is_favorite("005930") is True


def test_is_favorite_false(repo):
    assert repo.is_favorite("005930") is False


def test_persists_to_file(repo, tmp_path):
    repo.add("005930")
    raw = json.loads((tmp_path / "favorites.json").read_text(encoding="utf-8"))
    assert "005930" in raw["favorites"]
    assert "updated_at" in raw


def test_corrupt_file_returns_empty(repo, tmp_path):
    (tmp_path / "favorites.json").write_text("not json", encoding="utf-8")
    assert repo.get_all() == []
