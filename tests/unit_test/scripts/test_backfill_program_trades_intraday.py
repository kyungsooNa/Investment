import json
import sqlite3
from datetime import datetime

from scripts import backfill_program_trades_intraday as mod


def _make_program_db(path, rows):
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE pt_history (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "code TEXT, trade_time TEXT, net_vol INTEGER, created_at REAL)"
        )
        for code, trade_time, net_vol in rows:
            created_at = datetime(2026, 10, 27, int(trade_time[:2]), 0).timestamp()
            conn.execute(
                "INSERT INTO pt_history (code, trade_time, net_vol, created_at) "
                "VALUES (?, ?, ?, ?)",
                (code, trade_time, net_vol, created_at),
            )


def _write_capture(directory, *, codes, program_trades_intraday=None):
    payload = {
        "metadata": {
            "trade_date": "20261027",
            "codes": codes,
            "start_hhmmss": "090000",
            "end_hhmmss": "153000",
            "program_source": "program_db",
            "quality_gate": {"valid_for_backtest": True, "passed": True, "issues": []},
        },
        "intraday_minutes": {code: [{"time": "090000"}] for code in codes},
        "execution_strength": {code: 110.0 for code in codes},
        "execution_strength_intraday": {},
        "program_trades": {code: {"program_net_buy_qty": 1} for code in codes},
        "orderbook_intraday": {},
    }
    if program_trades_intraday is not None:
        payload["program_trades_intraday"] = program_trades_intraday
    path = directory / "replay_microstructure_20261027.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_backfill_fills_program_intraday_and_revalidates_gate(tmp_path):
    db = tmp_path / "pt.db"
    _make_program_db(db, [("005930", "100000", 10), ("005930", "110000", 25)])
    capture_path = _write_capture(tmp_path, codes=["005930", "000660"])

    result = mod.backfill_date(
        "20261027", microstructure_dir=tmp_path, program_db_path=db, apply=True
    )

    payload = json.loads(capture_path.read_text(encoding="utf-8"))
    assert payload["program_trades_intraday"] == {
        "005930": [
            {"time": "100000", "program_net_buy_qty": 10},
            {"time": "110000", "program_net_buy_qty": 25},
        ],
        "000660": [],
    }
    gate = payload["metadata"]["quality_gate"]
    assert gate["program_intraday_db_coverage_pct"] == 50.0
    assert gate["valid_for_backtest"] is True
    assert payload["metadata"]["program_trades_intraday_backfill"]["source_db"] == str(db)
    overlay = json.loads(
        (tmp_path / "replay_program_trades_intraday_20261027.json").read_text(encoding="utf-8")
    )
    assert overlay["005930"][1]["program_net_buy_qty"] == 25
    sidecar = json.loads((tmp_path / "replay_quality_20261027.json").read_text(encoding="utf-8"))
    assert sidecar["valid_for_backtest"] is True
    assert result["status"] == "applied"
    assert result["quality_gate_passed"] is True


def test_backfill_marks_gate_invalid_when_coverage_below_threshold(tmp_path):
    db = tmp_path / "pt.db"
    _make_program_db(db, [("005930", "100000", 10)])
    capture_path = _write_capture(tmp_path, codes=["005930", "000660", "035420"])

    result = mod.backfill_date(
        "20261027", microstructure_dir=tmp_path, program_db_path=db, apply=True
    )

    gate = json.loads(capture_path.read_text(encoding="utf-8"))["metadata"]["quality_gate"]
    assert gate["valid_for_backtest"] is False
    assert "program_intraday_db_coverage_below_threshold" in gate["issues"]
    assert result["quality_gate_passed"] is False


def test_backfill_dry_run_does_not_write(tmp_path):
    db = tmp_path / "pt.db"
    _make_program_db(db, [("005930", "100000", 10)])
    capture_path = _write_capture(tmp_path, codes=["005930"])
    before = capture_path.read_text(encoding="utf-8")

    result = mod.backfill_date(
        "20261027", microstructure_dir=tmp_path, program_db_path=db, apply=False
    )

    assert capture_path.read_text(encoding="utf-8") == before
    assert not (tmp_path / "replay_program_trades_intraday_20261027.json").exists()
    assert result["status"] == "dry_run"
    assert result["program_intraday_db_coverage_pct"] == 100.0


def test_backfill_skips_capture_that_already_has_program_intraday(tmp_path):
    db = tmp_path / "pt.db"
    _make_program_db(db, [("005930", "100000", 10)])
    existing = {"005930": [{"time": "093000", "program_net_buy_qty": 3}]}
    capture_path = _write_capture(tmp_path, codes=["005930"], program_trades_intraday=existing)
    before = capture_path.read_text(encoding="utf-8")

    result = mod.backfill_date(
        "20261027", microstructure_dir=tmp_path, program_db_path=db, apply=True
    )

    assert capture_path.read_text(encoding="utf-8") == before
    assert result["status"] == "skipped_existing"


def test_main_reports_each_date_as_json(tmp_path, capsys):
    db = tmp_path / "pt.db"
    _make_program_db(db, [("005930", "100000", 10)])
    _write_capture(tmp_path, codes=["005930"])

    exit_code = mod.main([
        "--microstructure-dir", str(tmp_path),
        "--program-db", str(db),
        "--date", "20261027",
        "--date", "20261028",
    ])

    assert exit_code == 0
    report = json.loads(capsys.readouterr().out)
    assert [row["status"] for row in report] == ["dry_run", "missing_capture"]
