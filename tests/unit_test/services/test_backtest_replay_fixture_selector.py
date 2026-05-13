import sqlite3

import pytest

from services.backtest_replay_fixture_selector import BacktestReplayFixtureSelector


def _create_replay_db(path):
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE daily_prices (
                code TEXT NOT NULL,
                trade_date TEXT NOT NULL,
                name TEXT,
                current_price INTEGER,
                high_price INTEGER,
                low_price INTEGER,
                trading_value INTEGER,
                PRIMARY KEY (code, trade_date)
            );
            CREATE TABLE ohlcv (
                code TEXT NOT NULL,
                date TEXT NOT NULL,
                open INTEGER,
                high INTEGER,
                low INTEGER,
                close INTEGER,
                volume INTEGER,
                PRIMARY KEY (code, date)
            );
            CREATE TABLE rs_ratings (
                trade_date TEXT NOT NULL,
                code TEXT NOT NULL,
                rs_rating INTEGER NOT NULL,
                weighted_rs REAL NOT NULL,
                PRIMARY KEY (trade_date, code)
            );
            """
        )
        _insert_ohlcv(conn, "000001", "20260401", 70)
        _insert_ohlcv(conn, "000002", "20260401", 70)
        _insert_ohlcv(conn, "000003", "20260401", 30)
        conn.executemany(
            """
            INSERT INTO daily_prices
                (code, trade_date, name, current_price, high_price, low_price, trading_value)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ("000001", "20260501", "A", 10000, 10100, 9900, 20_000_000_000),
                ("000002", "20260501", "B", 10000, 10100, 9900, 5_000_000_000),
                ("000003", "20260501", "C", 10000, 10100, 9900, 30_000_000_000),
                ("000001", "20260502", "A", 10000, 10100, 9900, 20_000_000_000),
                ("000002", "20260502", "B", 10000, 10100, 9900, 40_000_000_000),
                ("000003", "20260502", "C", 10000, 10100, 9900, 30_000_000_000),
            ],
        )
        conn.executemany(
            "INSERT INTO rs_ratings (trade_date, code, rs_rating, weighted_rs) VALUES (?, ?, ?, ?)",
            [
                ("20260501", "000001", 90, 12.0),
                ("20260502", "000001", 90, 12.0),
                ("20260502", "000002", 80, 10.0),
            ],
        )


def _insert_ohlcv(conn, code, start_date, count):
    start = int(start_date)
    conn.executemany(
        "INSERT INTO ohlcv (code, date, open, high, low, close, volume) VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            (code, str(start + i), 10000, 10100, 9900, 10000 + i, 100000)
            for i in range(count)
        ],
    )


def test_select_sample_dates_orders_by_replay_ready_coverage(tmp_path):
    db_path = tmp_path / "stocks.db"
    _create_replay_db(db_path)

    selector = BacktestReplayFixtureSelector(
        db_path,
        min_trading_value=10_000_000_000,
        min_ohlcv_days=60,
        sample_code_count=2,
    )

    candidates = selector.select_sample_dates(limit=2)

    assert [candidate.trade_date for candidate in candidates] == ["20260502", "20260501"]
    assert candidates[0].daily_rows == 3
    assert candidates[0].liquid_rows == 3
    assert candidates[0].replay_ready_rows == 2
    assert candidates[0].rs_rows == 2
    assert candidates[0].sample_codes == ("000002", "000001")
    assert candidates[0].to_dict()["replay_ready_ratio"] == 0.6667


def test_select_sample_dates_respects_date_range(tmp_path):
    db_path = tmp_path / "stocks.db"
    _create_replay_db(db_path)

    selector = BacktestReplayFixtureSelector(db_path)

    candidates = selector.select_sample_dates(start_date="20260501", end_date="20260501")

    assert len(candidates) == 1
    assert candidates[0].trade_date == "20260501"


def test_select_sample_dates_missing_db_raises(tmp_path):
    selector = BacktestReplayFixtureSelector(tmp_path / "missing.db")

    with pytest.raises(FileNotFoundError):
        selector.select_sample_dates()


def test_select_sample_dates_missing_table_raises(tmp_path):
    db_path = tmp_path / "stocks.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE daily_prices (code TEXT, trade_date TEXT)")

    selector = BacktestReplayFixtureSelector(db_path)

    with pytest.raises(ValueError, match="missing replay DB tables"):
        selector.select_sample_dates()
