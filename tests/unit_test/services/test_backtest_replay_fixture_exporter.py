import json
import sqlite3
from pathlib import Path

import pytest

from services.backtest_replay_fixture_exporter import BacktestReplayFixtureExporter


REPLAY_FIXTURE_PATH = (
    Path(__file__).resolve().parents[2]
    / "fixtures"
    / "backtest"
    / "replay_20260512_sample.json"
)


def _create_db(path):
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE daily_prices (
                code TEXT NOT NULL,
                trade_date TEXT NOT NULL,
                name TEXT,
                current_price INTEGER,
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
        conn.executemany(
            """
            INSERT INTO daily_prices
                (code, trade_date, name, current_price, trading_value)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                ("000001", "20260512", "A", 10000, 20_000_000_000),
                ("000002", "20260512", "B", 20000, 30_000_000_000),
            ],
        )
        for code in ("000001", "000002"):
            conn.executemany(
                """
                INSERT INTO ohlcv
                    (code, date, open, high, low, close, volume)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (code, f"202605{day:02d}", 1000 + day, 1100 + day, 900 + day, 1050 + day, day * 100)
                    for day in range(1, 13)
                ],
            )
        conn.execute(
            "INSERT INTO rs_ratings (trade_date, code, rs_rating, weighted_rs) VALUES (?, ?, ?, ?)",
            ("20260512", "000001", 92, 10.5),
        )


def test_export_fixture_includes_daily_rs_and_bounded_ohlcv(tmp_path):
    db_path = tmp_path / "stocks.db"
    _create_db(db_path)

    exporter = BacktestReplayFixtureExporter(db_path)
    payload = exporter.export_fixture(
        trade_date="20260512",
        codes=["000002", "000001"],
        ohlcv_lookback_days=3,
    )

    assert payload["metadata"]["trade_date"] == "20260512"
    assert payload["metadata"]["codes"] == ["000002", "000001"]
    assert payload["metadata"]["row_counts"] == {
        "daily_prices": 2,
        "ohlcv": 6,
        "rs_ratings": 1,
    }
    assert [row["code"] for row in payload["daily_prices"]] == ["000002", "000001"]
    assert [row["code"] for row in payload["rs_ratings"]] == ["000001"]
    assert [row["date"] for row in payload["ohlcv"]["000001"]] == [
        "20260510",
        "20260511",
        "20260512",
    ]


def test_export_fixture_uses_sample_codes_when_codes_are_omitted(tmp_path):
    db_path = tmp_path / "stocks.db"
    _create_db(db_path)

    exporter = BacktestReplayFixtureExporter(
        db_path,
        min_trading_value=10_000_000_000,
        min_ohlcv_days=3,
        sample_code_count=1,
    )
    payload = exporter.export_fixture(trade_date="20260512", ohlcv_lookback_days=3)

    assert payload["metadata"]["codes"] == ["000002"]
    assert payload["daily_prices"][0]["code"] == "000002"


def test_export_fixture_missing_daily_rows_raises(tmp_path):
    db_path = tmp_path / "stocks.db"
    _create_db(db_path)

    exporter = BacktestReplayFixtureExporter(db_path)

    with pytest.raises(ValueError, match="daily snapshot rows not found"):
        exporter.export_fixture(trade_date="20260512", codes=["999999"])


def test_committed_20260512_replay_fixture_shape_is_stable():
    payload = json.loads(REPLAY_FIXTURE_PATH.read_text(encoding="utf-8"))

    assert payload["metadata"]["trade_date"] == "20260512"
    assert payload["metadata"]["fixture_type"] == "historical_replay_snapshot"
    assert payload["metadata"]["codes"] == [
        "000660",
        "005930",
        "005380",
        "010170",
        "009150",
    ]
    assert payload["metadata"]["row_counts"] == {
        "daily_prices": 5,
        "ohlcv": 300,
        "rs_ratings": 5,
    }
    assert {row["code"] for row in payload["daily_prices"]} == set(payload["metadata"]["codes"])
    assert {row["code"] for row in payload["rs_ratings"]} == set(payload["metadata"]["codes"])
    assert all(
        len(payload["ohlcv"][code]) == 60
        for code in payload["metadata"]["codes"]
    )
