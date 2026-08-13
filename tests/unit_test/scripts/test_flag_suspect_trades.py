"""오염 의심 거래 플래그 스크립트 테스트.

핵심 계약: dry-run 이 기본이고 `--apply` 없이는 DB 가 바뀌지 않는다. 오염 기록은
소급 재구성이 불가해 **재작성이 아니라 표식만** 남기므로, 스크립트가 다른 컬럼을
건드리면 안 된다.
"""
import sqlite3

import pytest

from scripts.flag_suspect_trades import main


def _make_db(tmp_path, *, with_flag_column: bool = True) -> str:
    db_path = str(tmp_path / "virtual_trade.db")
    conn = sqlite3.connect(db_path)
    columns = (
        "id INTEGER PRIMARY KEY AUTOINCREMENT, strategy TEXT NOT NULL, code TEXT NOT NULL, "
        "buy_date TEXT NOT NULL, buy_price REAL NOT NULL, qty INTEGER NOT NULL DEFAULT 1, "
        "sell_date TEXT, sell_price REAL, return_rate REAL NOT NULL DEFAULT 0.0, "
        "status TEXT NOT NULL, reason TEXT NOT NULL DEFAULT ''"
    )
    if with_flag_column:
        columns += ", data_quality_flag TEXT"
    conn.execute(f"CREATE TABLE trades ({columns})")
    conn.executemany(
        "INSERT INTO trades (strategy, code, buy_date, buy_price, qty, sell_date, sell_price, "
        "return_rate, status, reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("래리윌리엄스VBO", "005930", "2026-06-28", 70000.0, 10, "2026-06-28", 71000.0, 1.4, "SOLD", ""),
            ("래리윌리엄스VBO", "000020", "2026-06-29", 5000.0, 20, "2026-06-29", 4900.0, -2.0, "SOLD", ""),
        ],
    )
    conn.commit()
    conn.close()
    return db_path


def _flags(db_path) -> dict:
    conn = sqlite3.connect(db_path)
    rows = conn.execute("SELECT id, data_quality_flag FROM trades ORDER BY id").fetchall()
    conn.close()
    return dict(rows)


def test_dry_run_does_not_modify_db(tmp_path, capsys):
    db_path = _make_db(tmp_path)

    main(["--trade-id", "1", "--note", "부분매도 전량기록 의심", "--db", db_path])

    assert _flags(db_path) == {1: None, 2: None}
    assert "dry-run" in capsys.readouterr().out


def test_apply_flags_only_given_ids(tmp_path):
    db_path = _make_db(tmp_path)

    main(["--trade-id", "1", "--note", "부분매도 전량기록 의심", "--db", db_path, "--apply"])

    assert _flags(db_path) == {1: "부분매도 전량기록 의심", 2: None}


def test_apply_does_not_touch_performance_columns(tmp_path):
    """재작성 금지 — 수익률/상태는 그대로 두고 표식만 붙인다."""
    db_path = _make_db(tmp_path)

    main(["--trade-id", "2", "--note", "의심", "--db", db_path, "--apply"])

    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT return_rate, status, sell_price FROM trades WHERE id=2").fetchone()
    conn.close()
    assert row == (-2.0, "SOLD", 4900.0)


def test_adds_column_when_missing(tmp_path):
    db_path = _make_db(tmp_path, with_flag_column=False)

    main(["--trade-id", "1", "--note", "의심", "--db", db_path, "--apply"])

    assert _flags(db_path) == {1: "의심", 2: None}


def test_dry_run_without_column_leaves_schema_untouched(tmp_path):
    """dry-run 은 스키마도 건드리지 않는다."""
    db_path = _make_db(tmp_path, with_flag_column=False)

    main(["--trade-id", "1", "--note", "의심", "--db", db_path])

    conn = sqlite3.connect(db_path)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(trades)").fetchall()}
    conn.close()
    assert "data_quality_flag" not in columns


def test_unknown_trade_id_aborts_without_partial_write(tmp_path):
    db_path = _make_db(tmp_path)

    with pytest.raises(SystemExit):
        main(["--trade-id", "1", "--trade-id", "999", "--note", "의심", "--db", db_path, "--apply"])

    assert _flags(db_path) == {1: None, 2: None}
