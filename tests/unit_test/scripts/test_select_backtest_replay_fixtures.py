import json
import sqlite3

from scripts.select_backtest_replay_fixtures import main


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
            ("000001", "20260501", 10000, 20_000_000_000),
        )
        conn.executemany(
            "INSERT INTO ohlcv (code, date, close) VALUES (?, ?, ?)",
            [("000001", str(20260400 + i), 10000 + i) for i in range(1, 62)],
        )
        conn.execute(
            "INSERT INTO rs_ratings (trade_date, code, rs_rating, weighted_rs) VALUES (?, ?, ?, ?)",
            ("20260501", "000001", 90, 12.0),
        )


def test_main_outputs_json(tmp_path, capsys):
    db_path = tmp_path / "stocks.db"
    _create_db(db_path)

    exit_code = main([
        "--db-path",
        str(db_path),
        "--limit",
        "1",
        "--output",
        "json",
    ])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["trade_date"] == "20260501"
    assert payload[0]["sample_codes"] == ["000001"]


def test_main_outputs_console(tmp_path, capsys):
    db_path = tmp_path / "stocks.db"
    _create_db(db_path)

    exit_code = main([
        "--db-path",
        str(db_path),
        "--limit",
        "1",
    ])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "[REPLAY FIXTURE SAMPLE CANDIDATES]" in output
    assert "20260501" in output
