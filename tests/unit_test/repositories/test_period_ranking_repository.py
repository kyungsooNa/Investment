"""PeriodRankingRepository — 기간수급 랭킹 SQLite 영속화 테스트."""
import json
import sqlite3

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


def test_legacy_calculation_results_are_invalidated(tmp_path):
    """시장 거래일 필터 도입 전 계산 결과는 재시작 시 복원하지 않는다."""
    db_path = tmp_path / "period_ranking.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE period_ranking ("
            "trade_date TEXT NOT NULL, days INTEGER NOT NULL, results TEXT NOT NULL, "
            "updated_at TEXT NOT NULL, PRIMARY KEY (trade_date, days))"
        )
        conn.execute(
            "INSERT INTO period_ranking VALUES (?, ?, ?, datetime('now'))",
            ("20260721", 5, json.dumps([{"earliest_trading_date": "20260623"}])),
        )

    migrated_repo = PeriodRankingRepository(db_path=db_path)

    assert migrated_repo.get("20260721", 5) is None
