import json
import sqlite3
from datetime import datetime, timedelta

from services.operational_backup_service import OperationalBackupService


def _make_sqlite(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE ledger (id INTEGER PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO ledger(value) VALUES ('kept')")


def test_create_backup_snapshots_sqlite_json_and_verifies_restore(tmp_path):
    data_dir = tmp_path / "data"
    db_path = data_dir / "VirtualTradeRepository" / "virtual_trade.db"
    state_path = data_dir / "kill_switch_state.json"
    _make_sqlite(db_path)
    state_path.write_text(json.dumps({"tripped": True}), encoding="utf-8")
    service = OperationalBackupService(
        data_dir=data_dir,
        backup_root=data_dir / "operational_backups",
        sqlite_relative_paths=("VirtualTradeRepository/virtual_trade.db",),
        json_globs=("kill_switch_state.json",),
        now_provider=lambda: datetime(2026, 10, 1, 16, 30, 0),
    )

    result = service.create_backup()

    assert result["status"] == "passed"
    assert result["restore_verification"]["passed"] is True
    assert {entry["source"] for entry in result["files"]} == {
        "VirtualTradeRepository/virtual_trade.db",
        "kill_switch_state.json",
    }
    backup_dir = data_dir / "operational_backups" / "20261001_163000"
    with sqlite3.connect(backup_dir / "files" / "VirtualTradeRepository" / "virtual_trade.db") as conn:
        assert conn.execute("SELECT value FROM ledger").fetchone()[0] == "kept"
    assert json.loads((backup_dir / "files" / "kill_switch_state.json").read_text(encoding="utf-8")) == {
        "tripped": True
    }


def test_verify_backup_detects_checksum_corruption(tmp_path):
    data_dir = tmp_path / "data"
    state_path = data_dir / "kill_switch_state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text('{"tripped": false}', encoding="utf-8")
    service = OperationalBackupService(
        data_dir=data_dir,
        backup_root=data_dir / "operational_backups",
        sqlite_relative_paths=(),
        json_globs=("kill_switch_state.json",),
        now_provider=lambda: datetime(2026, 10, 1, 16, 30, 0),
    )
    result = service.create_backup()
    backup_dir = data_dir / "operational_backups" / "20261001_163000"
    (backup_dir / result["files"][0]["backup_path"]).write_text("corrupted", encoding="utf-8")

    verification = service.verify_backup(backup_dir)

    assert verification["passed"] is False
    assert verification["errors"][0]["reason"] == "checksum_mismatch"


def test_verify_backup_rejects_restore_path_outside_rehearsal_directory(tmp_path):
    data_dir = tmp_path / "data"
    state_path = data_dir / "kill_switch_state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text("{}", encoding="utf-8")
    service = OperationalBackupService(
        data_dir=data_dir,
        backup_root=data_dir / "operational_backups",
        sqlite_relative_paths=(),
        json_globs=("kill_switch_state.json",),
        now_provider=lambda: datetime(2026, 10, 1, 16, 30, 0),
    )
    service.create_backup()
    backup_dir = data_dir / "operational_backups" / "20261001_163000"
    manifest_path = backup_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"][0]["source"] = "../escaped.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    verification = service.verify_backup(backup_dir)

    assert verification["passed"] is False
    assert verification["errors"][0]["reason"] == "restore_path_invalid"


def test_create_backup_prunes_only_old_timestamped_backup_directories(tmp_path):
    data_dir = tmp_path / "data"
    state_path = data_dir / "kill_switch_state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text("{}", encoding="utf-8")
    backup_root = data_dir / "operational_backups"
    now = datetime(2026, 10, 1, 16, 30, 0)
    service = OperationalBackupService(
        data_dir=data_dir,
        backup_root=backup_root,
        sqlite_relative_paths=(),
        json_globs=("kill_switch_state.json",),
        retention_count=2,
        now_provider=lambda: now,
    )
    for offset in (2, 1, 0):
        service._now = lambda offset=offset: now - timedelta(days=offset)
        service.create_backup()
    keep_file = backup_root / "README.txt"
    keep_file.write_text("keep", encoding="utf-8")

    backups = sorted(path.name for path in backup_root.iterdir() if path.is_dir())

    assert backups == ["20260930_163000", "20261001_163000"]
    assert keep_file.exists()
