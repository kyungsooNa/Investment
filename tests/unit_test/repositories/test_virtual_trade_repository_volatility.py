# tests/unit_test/repositories/test_virtual_trade_repository_volatility.py
"""volatility_20d_annualized 컬럼 보존/마이그레이션 검증."""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from repositories.virtual_trade_repository import VirtualTradeRepository


@pytest.fixture
def temp_db(tmp_path):
    db_dir = tmp_path / "data" / "VirtualTradeRepository"
    db_dir.mkdir(parents=True)
    return str(db_dir / "virtual_trade.db")


@pytest.fixture
def mock_market_clock():
    tm = MagicMock()
    tm.get_current_kst_time.return_value = datetime(2025, 1, 1, 12, 0, 0)
    return tm


@pytest.fixture
def repo(temp_db, mock_market_clock):
    return VirtualTradeRepository(db_path=temp_db, market_clock=mock_market_clock)


def test_log_buy_persists_volatility(repo):
    repo.log_buy("OSB", "005930", 70_000, qty=10, volatility_20d_annualized=0.3217)
    df = repo._read()
    assert len(df) == 1
    assert df.iloc[0]["volatility_20d_annualized"] == pytest.approx(0.3217)


def test_log_buy_persists_config_hash(repo):
    repo.log_buy("OSB", "005930", 70_000, qty=10, config_hash="abc123def456")
    df = repo._read()
    assert len(df) == 1
    assert df.iloc[0]["config_hash"] == "abc123def456"


def test_standard_journal_includes_config_hash(repo):
    repo.log_buy("OSB", "005930", 70_000, qty=10, config_hash="abc123def456")
    records = repo.get_standard_journal_records()
    assert records[0]["config_hash"] == "abc123def456"


def test_log_buy_without_volatility_stores_none(repo):
    repo.log_buy("Manual", "005930", 70_000)
    df = repo._read()
    assert len(df) == 1
    # SQLite NULL → pandas NaN
    val = df.iloc[0]["volatility_20d_annualized"]
    assert val is None or (isinstance(val, float) and val != val)  # NaN


def test_log_sell_preserves_buy_time_volatility(repo):
    repo.log_buy("OSB", "005930", 10_000, qty=1, volatility_20d_annualized=0.42)
    repo.log_sell("005930", 11_000, qty=1, reason="익절")
    df = repo._read()
    assert len(df) == 1
    assert df.iloc[0]["status"] == "SOLD"
    assert df.iloc[0]["volatility_20d_annualized"] == pytest.approx(0.42)


def test_ddl_includes_volatility_column(temp_db, mock_market_clock):
    VirtualTradeRepository(db_path=temp_db, market_clock=mock_market_clock)
    conn = sqlite3.connect(temp_db)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(trades)").fetchall()}
    conn.close()
    assert "volatility_20d_annualized" in cols
    assert "config_hash" in cols


def test_alter_table_migrates_legacy_db_without_column(tmp_path, mock_market_clock):
    """기존 컬럼 없는 DB → 재초기화 시 ALTER TABLE 로 추가되고 기존 row 보존."""
    db_dir = tmp_path / "data" / "VirtualTradeRepository"
    db_dir.mkdir(parents=True)
    db_path = str(db_dir / "virtual_trade.db")

    # 레거시 schema 로 직접 trades 테이블 생성 (volatility/config_hash 컬럼 없음)
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy TEXT NOT NULL,
            code TEXT NOT NULL,
            buy_date TEXT NOT NULL,
            buy_price REAL NOT NULL,
            qty INTEGER NOT NULL DEFAULT 1,
            sell_date TEXT,
            sell_price REAL,
            return_rate REAL NOT NULL DEFAULT 0.0,
            status TEXT NOT NULL,
            reason TEXT NOT NULL DEFAULT ''
        )
    """)
    conn.execute(
        "INSERT INTO trades (strategy, code, buy_date, buy_price, qty, status, reason) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("Legacy", "005930", "2024-12-01 10:00:00", 50_000, 5, "HOLD", "")
    )
    conn.commit()
    conn.close()

    # Repository 재초기화 → _ensure_trade_columns 가 ALTER TABLE 실행
    repo = VirtualTradeRepository(db_path=db_path, market_clock=mock_market_clock)
    cols = {row[1] for row in repo._db.execute("PRAGMA table_info(trades)").fetchall()}
    assert "volatility_20d_annualized" in cols
    assert "config_hash" in cols

    df = repo._read()
    assert len(df) == 1
    assert df.iloc[0]["strategy"] == "Legacy"
    val = df.iloc[0]["volatility_20d_annualized"]
    # 기존 row 는 NULL/NaN
    assert val is None or (isinstance(val, float) and val != val)
    config_hash = df.iloc[0]["config_hash"]
    assert config_hash is None or (isinstance(config_hash, float) and config_hash != config_hash)


@pytest.mark.asyncio
async def test_log_buy_async_propagates_volatility(repo):
    await repo.log_buy_async("OSB", "005930", 70_000, qty=1, volatility_20d_annualized=0.21)
    df = repo._read()
    assert len(df) == 1
    assert df.iloc[0]["volatility_20d_annualized"] == pytest.approx(0.21)


@pytest.mark.asyncio
async def test_log_buy_async_propagates_config_hash(repo):
    await repo.log_buy_async("OSB", "005930", 70_000, qty=1, config_hash="abc123def456")
    df = repo._read()
    assert len(df) == 1
    assert df.iloc[0]["config_hash"] == "abc123def456"
