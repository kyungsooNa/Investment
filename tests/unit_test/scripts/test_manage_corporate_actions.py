import json
import sqlite3

from scripts.manage_corporate_actions import main


def test_cli_applies_split_and_prints_audit_result(tmp_path, capsys):
    db_path = tmp_path / "overseas.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            "CREATE TABLE overseas_trades ("
            "id INTEGER PRIMARY KEY, symbol TEXT, buy_date TEXT, buy_price REAL, "
            "qty INTEGER, sell_date TEXT, status TEXT);"
            "INSERT INTO overseas_trades VALUES "
            "(1, 'AAPL', '2026-08-01 10:00:00', 200.0, 2, NULL, 'HOLD');"
        )

    exit_code = main([
        "--db-path", str(db_path),
        "split", "AAPL", "2026-08-15",
        "--numerator", "4",
        "--no-state-sync",
    ])

    result = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert result["applied"] is True
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT buy_price, qty FROM overseas_trades").fetchone() == (50.0, 8)
