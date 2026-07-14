"""PeriodRankingRepository — 기간수급 랭킹 SQLite 영속화 테스트."""
import pytest

from repositories.period_ranking_repository import PeriodRankingRepository


@pytest.fixture
def repo(tmp_path):
    return PeriodRankingRepository(db_path=tmp_path / "period_ranking.db")


def test_get_returns_none_when_empty(repo):
    assert repo.get("20260714", 5) is None


def test_save_and_get_roundtrip(repo):
    results = [
        {"stck_shrn_iscd": "005930", "combined_period_ntby_tr_pbmn_won": "123"},
        {"stck_shrn_iscd": "000660", "combined_period_ntby_tr_pbmn_won": "45"},
    ]
    repo.save("20260714", 5, results)
    assert repo.get("20260714", 5) == results


def test_save_replaces_same_key(repo):
    repo.save("20260714", 5, [{"a": "1"}])
    repo.save("20260714", 5, [{"a": "2"}])
    assert repo.get("20260714", 5) == [{"a": "2"}]


def test_days_variants_kept_for_same_date(repo):
    repo.save("20260714", 5, [{"a": "d5"}])
    repo.save("20260714", 10, [{"a": "d10"}])
    assert repo.get("20260714", 5) == [{"a": "d5"}]
    assert repo.get("20260714", 10) == [{"a": "d10"}]


def test_save_prunes_older_trade_dates(repo):
    repo.save("20260713", 5, [{"a": "old"}])
    repo.save("20260714", 5, [{"a": "new"}])
    assert repo.get("20260713", 5) is None
    assert repo.get("20260714", 5) == [{"a": "new"}]
