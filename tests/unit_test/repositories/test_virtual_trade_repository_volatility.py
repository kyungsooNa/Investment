# tests/unit_test/repositories/test_virtual_trade_repository_volatility.py
"""volatility_20d_annualized 컬럼 보존/마이그레이션 검증."""
from __future__ import annotations

import json
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


# ── P1 1-6 (b): price-policy 3필드 (invalidation/stop_loss/target) journal persist ──

def test_log_buy_persists_price_policy_fields(repo):
    repo.log_buy(
        "OSB", "005930", 70_000, qty=10,
        invalidation_price=68_000.0, stop_loss_price=66_500.0, target_price=80_000.0,
    )
    df = repo._read()
    assert len(df) == 1
    assert df.iloc[0]["invalidation_price"] == pytest.approx(68_000.0)
    assert df.iloc[0]["stop_loss_price"] == pytest.approx(66_500.0)
    assert df.iloc[0]["target_price"] == pytest.approx(80_000.0)


def test_standard_journal_includes_price_policy_fields(repo):
    repo.log_buy(
        "OSB", "005930", 70_000, qty=10,
        invalidation_price=68_000.0, stop_loss_price=66_500.0, target_price=80_000.0,
    )
    records = repo.get_standard_journal_records()
    meta = records[0]["metadata"]
    assert meta["invalidation_price"] == pytest.approx(68_000.0)
    assert meta["stop_loss_price"] == pytest.approx(66_500.0)
    assert meta["target_price"] == pytest.approx(80_000.0)


def test_log_buy_without_price_policy_stores_none(repo):
    repo.log_buy("Manual", "005930", 70_000)
    df = repo._read()
    assert len(df) == 1
    for col in ("invalidation_price", "stop_loss_price", "target_price"):
        val = df.iloc[0][col]
        assert val is None or (isinstance(val, float) and val != val)  # NaN


def test_ddl_includes_price_policy_columns(temp_db, mock_market_clock):
    VirtualTradeRepository(db_path=temp_db, market_clock=mock_market_clock)
    conn = sqlite3.connect(temp_db)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(trades)").fetchall()}
    conn.close()
    assert {"invalidation_price", "stop_loss_price", "target_price"} <= cols


def test_alter_table_migrates_legacy_db_without_price_policy_columns(tmp_path, mock_market_clock):
    """price-policy 컬럼 없는 레거시 DB → 재초기화 시 ALTER TABLE 추가 + 기존 row 보존."""
    db_dir = tmp_path / "data" / "VirtualTradeRepository"
    db_dir.mkdir(parents=True)
    db_path = str(db_dir / "virtual_trade.db")

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

    repo = VirtualTradeRepository(db_path=db_path, market_clock=mock_market_clock)
    cols = {row[1] for row in repo._db.execute("PRAGMA table_info(trades)").fetchall()}
    assert {"invalidation_price", "stop_loss_price", "target_price"} <= cols

    df = repo._read()
    assert len(df) == 1
    assert df.iloc[0]["strategy"] == "Legacy"
    val = df.iloc[0]["stop_loss_price"]
    assert val is None or (isinstance(val, float) and val != val)


@pytest.mark.asyncio
async def test_log_buy_async_propagates_price_policy_fields(repo):
    await repo.log_buy_async(
        "OSB", "005930", 70_000, qty=1,
        invalidation_price=68_000.0, stop_loss_price=66_500.0, target_price=80_000.0,
    )
    df = repo._read()
    assert len(df) == 1
    assert df.iloc[0]["invalidation_price"] == pytest.approx(68_000.0)
    assert df.iloc[0]["stop_loss_price"] == pytest.approx(66_500.0)
    assert df.iloc[0]["target_price"] == pytest.approx(80_000.0)


# ── P1 1-6: signal metadata 5필드 (entry_reason/trailing_rule/expected_holding_period_days/
#    confidence/required_data) journal persist ──

def test_log_buy_persists_signal_metadata_fields(repo):
    repo.log_buy(
        "OSB", "005930", 70_000, qty=10,
        entry_reason="pocket_pivot_breakout", trailing_rule="atr_2x",
        expected_holding_period_days=5, confidence=0.75,
        required_data=["ohlcv", "volume_profile"],
    )
    df = repo._read()
    assert len(df) == 1
    assert df.iloc[0]["entry_reason"] == "pocket_pivot_breakout"
    assert df.iloc[0]["trailing_rule"] == "atr_2x"
    assert int(df.iloc[0]["expected_holding_period_days"]) == 5
    assert df.iloc[0]["confidence"] == pytest.approx(0.75)
    # required_data 는 SQLite TEXT 로 JSON 직렬화 저장된다.
    assert json.loads(df.iloc[0]["required_data"]) == ["ohlcv", "volume_profile"]


def test_standard_journal_includes_signal_metadata_fields(repo):
    repo.log_buy(
        "OSB", "005930", 70_000, qty=10,
        entry_reason="pocket_pivot_breakout", trailing_rule="atr_2x",
        expected_holding_period_days=5, confidence=0.75,
        required_data=["ohlcv", "volume_profile"],
    )
    records = repo.get_standard_journal_records()
    meta = records[0]["metadata"]
    assert meta["entry_reason"] == "pocket_pivot_breakout"
    assert meta["trailing_rule"] == "atr_2x"
    assert int(meta["expected_holding_period_days"]) == 5
    assert meta["confidence"] == pytest.approx(0.75)
    assert json.loads(meta["required_data"]) == ["ohlcv", "volume_profile"]


def test_log_buy_without_signal_metadata_stores_none(repo):
    repo.log_buy("Manual", "005930", 70_000)
    df = repo._read()
    assert len(df) == 1
    for col in ("entry_reason", "trailing_rule", "expected_holding_period_days",
                "confidence", "required_data"):
        val = df.iloc[0][col]
        assert val is None or (isinstance(val, float) and val != val)  # NaN


def test_ddl_includes_signal_metadata_columns(temp_db, mock_market_clock):
    VirtualTradeRepository(db_path=temp_db, market_clock=mock_market_clock)
    conn = sqlite3.connect(temp_db)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(trades)").fetchall()}
    conn.close()
    assert {"entry_reason", "trailing_rule", "expected_holding_period_days",
            "confidence", "required_data"} <= cols


def test_alter_table_migrates_legacy_db_without_signal_metadata_columns(tmp_path, mock_market_clock):
    """signal metadata 컬럼 없는 레거시 DB → 재초기화 시 ALTER TABLE 추가 + 기존 row 보존."""
    db_dir = tmp_path / "data" / "VirtualTradeRepository"
    db_dir.mkdir(parents=True)
    db_path = str(db_dir / "virtual_trade.db")

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

    repo = VirtualTradeRepository(db_path=db_path, market_clock=mock_market_clock)
    cols = {row[1] for row in repo._db.execute("PRAGMA table_info(trades)").fetchall()}
    assert {"entry_reason", "trailing_rule", "expected_holding_period_days",
            "confidence", "required_data"} <= cols

    df = repo._read()
    assert len(df) == 1
    assert df.iloc[0]["strategy"] == "Legacy"
    val = df.iloc[0]["confidence"]
    assert val is None or (isinstance(val, float) and val != val)


@pytest.mark.asyncio
async def test_log_buy_async_propagates_signal_metadata_fields(repo):
    await repo.log_buy_async(
        "OSB", "005930", 70_000, qty=1,
        entry_reason="pocket_pivot_breakout", trailing_rule="atr_2x",
        expected_holding_period_days=5, confidence=0.75,
        required_data=["ohlcv"],
    )
    df = repo._read()
    assert len(df) == 1
    assert df.iloc[0]["entry_reason"] == "pocket_pivot_breakout"
    assert int(df.iloc[0]["expected_holding_period_days"]) == 5
    assert df.iloc[0]["confidence"] == pytest.approx(0.75)
    assert json.loads(df.iloc[0]["required_data"]) == ["ohlcv"]


# ── R-2: market_regime (regime별 전략 성과 분해용) journal persist ──

_REGIME = {"kospi": "bull", "kosdaq": "sideways", "stock_market": "KOSPI"}


def test_log_buy_persists_market_regime(repo):
    repo.log_buy("OSB", "005930", 70_000, qty=10, market_regime=_REGIME)
    df = repo._read()
    assert len(df) == 1
    # market_regime 은 SQLite TEXT 로 JSON 직렬화 저장된다.
    assert json.loads(df.iloc[0]["market_regime"]) == _REGIME


def test_standard_journal_surfaces_market_regime_as_dict(repo):
    """normalize_virtual_trade 가 market_regime 을 dict 로 surfacing —
    compute_performance_by_regime 입력 계약(isinstance Mapping)을 만족해야 한다."""
    repo.log_buy("OSB", "005930", 70_000, qty=10, market_regime=_REGIME)
    records = repo.get_standard_journal_records()
    regime = records[0]["market_regime"]
    assert isinstance(regime, dict)
    assert regime == _REGIME


def test_log_buy_without_market_regime_stores_none(repo):
    repo.log_buy("Manual", "005930", 70_000)
    df = repo._read()
    val = df.iloc[0]["market_regime"]
    assert val is None or (isinstance(val, float) and val != val)  # NaN


def test_ddl_includes_market_regime_column(temp_db, mock_market_clock):
    VirtualTradeRepository(db_path=temp_db, market_clock=mock_market_clock)
    conn = sqlite3.connect(temp_db)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(trades)").fetchall()}
    conn.close()
    assert "market_regime" in cols


def test_alter_table_migrates_legacy_db_without_market_regime_column(tmp_path, mock_market_clock):
    """market_regime 컬럼 없는 레거시 DB → 재초기화 시 ALTER TABLE 추가 + 기존 row 보존."""
    db_dir = tmp_path / "data" / "VirtualTradeRepository"
    db_dir.mkdir(parents=True)
    db_path = str(db_dir / "virtual_trade.db")

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

    repo = VirtualTradeRepository(db_path=db_path, market_clock=mock_market_clock)
    cols = {row[1] for row in repo._db.execute("PRAGMA table_info(trades)").fetchall()}
    assert "market_regime" in cols

    df = repo._read()
    assert len(df) == 1
    assert df.iloc[0]["strategy"] == "Legacy"


@pytest.mark.asyncio
async def test_log_buy_async_propagates_market_regime(repo):
    await repo.log_buy_async(
        "OSB", "005930", 70_000, qty=1,
        market_regime={"kospi": "bear", "kosdaq": "bear", "stock_market": "KOSDAQ"},
    )
    records = repo.get_standard_journal_records()
    assert records[0]["market_regime"] == {
        "kospi": "bear", "kosdaq": "bear", "stock_market": "KOSDAQ"
    }
