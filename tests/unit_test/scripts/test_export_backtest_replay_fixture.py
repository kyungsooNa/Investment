import json
import sqlite3

from scripts.export_backtest_replay_fixture import main


def _create_db(path):
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE daily_prices (
                code TEXT NOT NULL,
                trade_date TEXT NOT NULL,
                current_price INTEGER,
                trading_value INTEGER,
                PRIMARY KEY (code, trade_date)
            );
            CREATE TABLE ohlcv (
                code TEXT NOT NULL,
                date TEXT NOT NULL,
                close INTEGER,
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
        conn.execute(
            "INSERT INTO daily_prices (code, trade_date, current_price, trading_value) VALUES (?, ?, ?, ?)",
            ("000001", "20260512", 10000, 20_000_000_000),
        )
        conn.executemany(
            "INSERT INTO ohlcv (code, date, close) VALUES (?, ?, ?)",
            [("000001", f"202605{day:02d}", 10000 + day) for day in range(1, 13)],
        )
        conn.execute(
            "INSERT INTO rs_ratings (trade_date, code, rs_rating, weighted_rs) VALUES (?, ?, ?, ?)",
            ("20260512", "000001", 90, 12.0),
        )


def test_main_writes_replay_fixture_json(tmp_path, capsys):
    db_path = tmp_path / "stocks.db"
    output_path = tmp_path / "fixture.json"
    _create_db(db_path)

    exit_code = main([
        "--db-path",
        str(db_path),
        "--date",
        "20260512",
        "--codes",
        "000001",
        "--ohlcv-lookback-days",
        "3",
        "--output-file",
        str(output_path),
    ])

    assert exit_code == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["metadata"]["trade_date"] == "20260512"
    assert payload["metadata"]["codes"] == ["000001"]
    assert len(payload["ohlcv"]["000001"]) == 3
    assert str(output_path) in capsys.readouterr().out
