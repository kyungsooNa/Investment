import json
import sqlite3

from scripts.manage_corporate_actions import main


def test_cli_requires_preview_token_then_backs_up_applies_and_reconciles(tmp_path, capsys):
    db_path = tmp_path / "overseas.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            "CREATE TABLE overseas_trades ("
            "id INTEGER PRIMARY KEY, symbol TEXT, buy_date TEXT, buy_price REAL, "
            "qty INTEGER, sell_date TEXT, status TEXT);"
            "INSERT INTO overseas_trades VALUES "
            "(1, 'AAPL', '2026-08-01 10:00:00', 200.0, 2, NULL, 'HOLD');"
        )

    preview_exit = main([
        "--db-path", str(db_path),
        "--data-dir", str(tmp_path),
        "split", "AAPL", "2026-08-15",
        "--numerator", "4",
        "--no-state-sync",
    ])

    preview = json.loads(capsys.readouterr().out)
    assert preview_exit == 0
    assert preview["phase"] == "preview"
    assert preview["affected_lots"] == 1
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT buy_price, qty FROM overseas_trades").fetchone() == (200.0, 2)

    exit_code = main([
        "--db-path", str(db_path),
        "--data-dir", str(tmp_path),
        "split", "AAPL", "2026-08-15",
        "--numerator", "4",
        "--no-state-sync",
        "--apply",
        "--approval-token", preview["approval_token"],
    ])

    result = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert result["applied"] is True
    assert result["phase"] == "applied"
    assert result["backup"]["status"] == "passed"
    assert result["reconciliation"]["passed"] is True
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT buy_price, qty FROM overseas_trades").fetchone() == (50.0, 8)


def test_cli_rejects_stale_approval_token_when_ledger_changes(tmp_path, capsys):
    db_path = tmp_path / "overseas.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            "CREATE TABLE overseas_trades ("
            "id INTEGER PRIMARY KEY, symbol TEXT, buy_date TEXT, buy_price REAL, "
            "qty INTEGER, sell_date TEXT, status TEXT);"
            "INSERT INTO overseas_trades VALUES "
            "(1, 'AAPL', '2026-08-01 10:00:00', 200.0, 2, NULL, 'HOLD');"
        )
    base_args = [
        "--db-path", str(db_path), "--data-dir", str(tmp_path),
        "split", "AAPL", "2026-08-15", "--numerator", "2", "--no-state-sync",
    ]
    main(base_args)
    token = json.loads(capsys.readouterr().out)["approval_token"]
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE overseas_trades SET qty=3 WHERE id=1")

    import pytest
    with pytest.raises(ValueError, match="승인 토큰"):
        main([*base_args, "--apply", "--approval-token", token])

    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT buy_price, qty FROM overseas_trades").fetchone() == (200.0, 3)


def test_cli_dividend_uses_the_same_preview_and_approval_workflow(tmp_path, capsys):
    db_path = tmp_path / "overseas.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            "CREATE TABLE overseas_trades ("
            "id INTEGER PRIMARY KEY, symbol TEXT, buy_date TEXT, buy_price REAL, "
            "qty INTEGER, sell_date TEXT, status TEXT);"
            "INSERT INTO overseas_trades VALUES "
            "(1, 'AAPL', '2026-08-01 10:00:00', 200.0, 3, NULL, 'HOLD');"
        )
    base_args = [
        "--db-path", str(db_path), "--data-dir", str(tmp_path),
        "dividend", "AAPL", "2026-08-15", "--amount-per-share", "0.25",
    ]

    main(base_args)
    preview = json.loads(capsys.readouterr().out)
    assert preview["phase"] == "preview"
    assert preview["eligible_qty"] == 3
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM corporate_actions").fetchone()[0] == 0

    main([*base_args, "--apply", "--approval-token", preview["approval_token"]])
    result = json.loads(capsys.readouterr().out)
    assert result["cash_amount"] == 0.75
    assert result["backup"]["status"] == "passed"
    assert result["reconciliation"]["passed"] is True
