import json
import shutil
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

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


def test_get_health_reports_latest_verified_backup_and_history(tmp_path):
    data_dir = tmp_path / "data"
    state_path = data_dir / "kill_switch_state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text("{}", encoding="utf-8")
    now = datetime(2026, 10, 1, 18, 0, 0)
    service = OperationalBackupService(
        data_dir=data_dir,
        sqlite_relative_paths=(),
        json_globs=("kill_switch_state.json",),
        now_provider=lambda: now,
    )
    service.create_backup()

    health = service.get_health(history_limit=5, stale_after_hours=26)

    assert health["status"] == "healthy"
    assert health["latest"]["backup_id"] == "20261001_180000"
    assert health["latest"]["verified_count"] == 1
    assert health["history"][0]["status"] == "passed"


def test_restore_backup_requires_matching_confirmation_and_empty_staging_dir(tmp_path):
    data_dir = tmp_path / "data"
    state_path = data_dir / "kill_switch_state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text('{"tripped": true}', encoding="utf-8")
    service = OperationalBackupService(
        data_dir=data_dir,
        sqlite_relative_paths=(),
        json_globs=("kill_switch_state.json",),
        now_provider=lambda: datetime(2026, 10, 1, 18, 0, 0),
    )
    service.create_backup()

    import pytest
    with pytest.raises(ValueError, match="확인 ID"):
        service.restore_backup(
            "20261001_180000",
            confirm_backup_id="wrong",
            destination_dir=tmp_path / "restore",
        )

    result = service.restore_backup(
        "20261001_180000",
        confirm_backup_id="20261001_180000",
        destination_dir=tmp_path / "restore",
    )

    assert result["status"] == "restored"
    assert result["restored_count"] == 1
    assert json.loads((tmp_path / "restore" / "kill_switch_state.json").read_text(encoding="utf-8")) == {
        "tripped": True
    }


def test_restore_backup_refuses_live_data_directory(tmp_path):
    data_dir = tmp_path / "data"
    state_path = data_dir / "kill_switch_state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text("{}", encoding="utf-8")
    service = OperationalBackupService(
        data_dir=data_dir,
        sqlite_relative_paths=(),
        json_globs=("kill_switch_state.json",),
        now_provider=lambda: datetime(2026, 10, 1, 18, 0, 0),
    )
    service.create_backup()

    import pytest
    with pytest.raises(ValueError, match="라이브 data_dir"):
        service.restore_backup(
            "20261001_180000",
            confirm_backup_id="20261001_180000",
            destination_dir=data_dir,
        )


def _make_corpus_file(data_dir, name, payload):
    path = data_dir / "backtest_microstructure" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_create_backup_archives_microstructure_corpus_incrementally(tmp_path):
    data_dir = tmp_path / "data"
    backup_root = tmp_path / "offdisk"
    _make_corpus_file(data_dir, "replay_microstructure_20261001.json", {"d": 1})
    _make_corpus_file(data_dir, "replay_quality_20261001.json", {"valid_for_backtest": True})
    (data_dir / "backtest_microstructure" / "notes.txt").write_text("skip", encoding="utf-8")
    (data_dir / "kill_switch_state.json").write_text("{}", encoding="utf-8")
    service = OperationalBackupService(
        data_dir=data_dir,
        backup_root=backup_root,
        sqlite_relative_paths=(),
        json_globs=("kill_switch_state.json",),
        now_provider=lambda: datetime(2026, 10, 1, 16, 30, 0),
    )

    first = service.create_backup()

    archive_dir = backup_root / "archive" / "backtest_microstructure"
    assert sorted(p.name for p in archive_dir.iterdir()) == [
        "replay_microstructure_20261001.json",
        "replay_quality_20261001.json",
    ]
    assert first["archive"]["copied"] == 2
    assert first["archive"]["errors"] == []
    assert first["status"] == "passed"
    # 스냅샷 디렉터리에는 코퍼스를 복제하지 않는다(14개 스냅샷 × 코퍼스 중복 방지).
    assert not (backup_root / "20261001_163000" / "files" / "backtest_microstructure").exists()

    _make_corpus_file(data_dir, "replay_microstructure_20261002.json", {"d": 2})
    _make_corpus_file(data_dir, "replay_quality_20261001.json", {"valid_for_backtest": False})
    service._now = lambda: datetime(2026, 10, 2, 16, 30, 0)
    second = service.create_backup()

    assert second["archive"]["copied"] == 2
    assert second["archive"]["unchanged"] == 1
    assert second["archive"]["total"] == 3
    assert json.loads((archive_dir / "replay_quality_20261001.json").read_text(encoding="utf-8")) == {
        "valid_for_backtest": False
    }


def test_archive_never_deletes_files_removed_from_source(tmp_path):
    data_dir = tmp_path / "data"
    backup_root = tmp_path / "offdisk"
    source = _make_corpus_file(data_dir, "replay_microstructure_20261001.json", {"d": 1})
    service = OperationalBackupService(
        data_dir=data_dir,
        backup_root=backup_root,
        sqlite_relative_paths=(),
        json_globs=(),
        now_provider=lambda: datetime(2026, 10, 1, 16, 30, 0),
    )
    service.create_backup()
    source.unlink()
    service._now = lambda: datetime(2026, 10, 2, 16, 30, 0)

    service.create_backup()

    assert (backup_root / "archive" / "backtest_microstructure" / "replay_microstructure_20261001.json").exists()


def test_create_backup_fails_when_archive_copy_fails(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    state_path = data_dir / "kill_switch_state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text("{}", encoding="utf-8")
    _make_corpus_file(data_dir, "replay_microstructure_20261001.json", {"d": 1})
    service = OperationalBackupService(
        data_dir=data_dir,
        backup_root=tmp_path / "offdisk",
        sqlite_relative_paths=(),
        json_globs=("kill_switch_state.json",),
        now_provider=lambda: datetime(2026, 10, 1, 16, 30, 0),
    )

    real_copy = shutil.copy2

    def _broken_archive_copy(src, dst, *args, **kwargs):
        if "archive" in Path(dst).parts:
            raise OSError("disk full")
        return real_copy(src, dst, *args, **kwargs)

    monkeypatch.setattr("services.operational_backup_service.shutil.copy2", _broken_archive_copy)

    result = service.create_backup()

    assert result["status"] == "failed"
    assert result["archive"]["errors"][0]["reason"] == "archive_copy_failed"
