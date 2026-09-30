"""거래 운영 복구에 필요한 소형 상태 파일을 백업하고 복원 가능성을 검증한다."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable


DEFAULT_SQLITE_PATHS = (
    "VirtualTradeRepository/virtual_trade.db",
    "OverseasTradeRepository/overseas_trade.db",
    "StrategyScheduler/scheduler.db",
    "time_dispatcher_state.db",
    "time_dispatcher_state_us.db",
)
DEFAULT_JSON_GLOBS = (
    "*_position_state.json",
    "overseas_intraday_*_state.json",
    "kill_switch_state.json",
    "operator_alert_state.json",
    "position_sizing_state.json",
    "paper_account_reconcile_state.json",
    "favorite_price_alert_state.json",
    "overseas_favorite_price_alert_state.json",
    "inverse_etf_regime_state.json",
)
_BACKUP_DIR_PATTERN = re.compile(r"^\d{8}_\d{6}$")


class OperationalBackupService:
    """SQLite online backup + JSON snapshot + restore rehearsal orchestration."""

    def __init__(
        self,
        *,
        data_dir: str | Path = "data",
        backup_root: str | Path | None = None,
        sqlite_relative_paths: Iterable[str] = DEFAULT_SQLITE_PATHS,
        json_globs: Iterable[str] = DEFAULT_JSON_GLOBS,
        retention_count: int = 14,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self._data_dir = Path(data_dir)
        self._backup_root = Path(backup_root) if backup_root else self._data_dir / "operational_backups"
        self._sqlite_relative_paths = tuple(sqlite_relative_paths)
        self._json_globs = tuple(json_globs)
        self._retention_count = max(int(retention_count), 1)
        self._now = now_provider or datetime.now

    def create_backup(self) -> dict:
        timestamp = self._now().strftime("%Y%m%d_%H%M%S")
        backup_dir = self._backup_root / timestamp
        files_dir = backup_dir / "files"
        files_dir.mkdir(parents=True, exist_ok=False)

        entries: list[dict] = []
        missing: list[str] = []
        for relative in self._sqlite_relative_paths:
            source = self._safe_source(relative)
            if not source.is_file():
                missing.append(Path(relative).as_posix())
                continue
            target = files_dir / Path(relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            self._backup_sqlite(source, target)
            entries.append(self._entry(relative, target, "sqlite"))

        json_sources: dict[str, Path] = {}
        for pattern in self._json_globs:
            for source in self._data_dir.glob(pattern):
                if source.is_file():
                    json_sources[source.relative_to(self._data_dir).as_posix()] = source
        for relative, source in sorted(json_sources.items()):
            target = files_dir / Path(relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            payload = source.read_bytes()
            json.loads(payload.decode("utf-8"))
            target.write_bytes(payload)
            entries.append(self._entry(relative, target, "json"))

        manifest = {
            "version": 1,
            "created_at": self._now().astimezone().isoformat(),
            "backup_dir": str(backup_dir),
            "files": entries,
            "missing_sources": sorted(missing),
        }
        manifest_path = backup_dir / "manifest.json"
        self._write_manifest(manifest_path, manifest)
        verification = self.verify_backup(backup_dir)
        manifest["restore_verification"] = verification
        manifest["status"] = "passed" if verification["passed"] and entries else "failed"
        self._write_manifest(manifest_path, manifest)
        self._prune_old_backups()
        return manifest

    def verify_backup(self, backup_dir: str | Path) -> dict:
        backup_dir = Path(backup_dir)
        try:
            manifest = json.loads((backup_dir / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            return {"passed": False, "verified_count": 0, "errors": [{"reason": "manifest_invalid", "detail": str(exc)}]}

        errors: list[dict] = []
        verified = 0
        with tempfile.TemporaryDirectory(prefix="investment_restore_verify_") as temp_dir:
            restore_root = Path(temp_dir)
            for entry in manifest.get("files", []):
                relative_backup = Path(str(entry.get("backup_path") or ""))
                source = (backup_dir / relative_backup).resolve()
                if not self._is_within(source, backup_dir.resolve()) or not source.is_file():
                    errors.append({"source": entry.get("source"), "reason": "backup_file_missing"})
                    continue
                if self._sha256(source) != entry.get("sha256"):
                    errors.append({"source": entry.get("source"), "reason": "checksum_mismatch"})
                    continue
                restored = (restore_root / Path(str(entry.get("source") or "unknown"))).resolve()
                if not self._is_within(restored, restore_root.resolve()):
                    errors.append({"source": entry.get("source"), "reason": "restore_path_invalid"})
                    continue
                restored.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, restored)
                try:
                    if entry.get("type") == "sqlite":
                        conn = sqlite3.connect(restored)
                        try:
                            result = conn.execute("PRAGMA integrity_check").fetchone()
                        finally:
                            conn.close()
                        if not result or result[0] != "ok":
                            raise ValueError(f"integrity_check={result}")
                    elif entry.get("type") == "json":
                        json.loads(restored.read_text(encoding="utf-8"))
                    else:
                        raise ValueError("unsupported_type")
                except (OSError, sqlite3.Error, UnicodeError, json.JSONDecodeError, ValueError) as exc:
                    errors.append({"source": entry.get("source"), "reason": "restore_invalid", "detail": str(exc)})
                    continue
                verified += 1
        return {"passed": not errors, "verified_count": verified, "errors": errors}

    def _safe_source(self, relative: str) -> Path:
        source = (self._data_dir / relative).resolve()
        if not self._is_within(source, self._data_dir.resolve()):
            raise ValueError(f"data_dir 밖의 백업 경로는 허용되지 않습니다: {relative}")
        return source

    @staticmethod
    def _backup_sqlite(source: Path, target: Path) -> None:
        src = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
        dst = sqlite3.connect(target)
        try:
            src.backup(dst)
            result = dst.execute("PRAGMA integrity_check").fetchone()
        finally:
            dst.close()
            src.close()
        if not result or result[0] != "ok":
            raise sqlite3.DatabaseError(f"backup integrity_check failed: {result}")

    def _entry(self, source_relative: str, target: Path, kind: str) -> dict:
        return {
            "source": Path(source_relative).as_posix(),
            "backup_path": (Path("files") / Path(source_relative)).as_posix(),
            "type": kind,
            "size": target.stat().st_size,
            "sha256": self._sha256(target),
        }

    @staticmethod
    def _write_manifest(path: Path, manifest: dict) -> None:
        path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _is_within(path: Path, root: Path) -> bool:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            return False

    def _prune_old_backups(self) -> None:
        if not self._backup_root.is_dir():
            return
        candidates = sorted(
            path for path in self._backup_root.iterdir()
            if path.is_dir() and _BACKUP_DIR_PATTERN.fullmatch(path.name)
        )
        for path in candidates[:-self._retention_count]:
            resolved = path.resolve()
            if resolved.parent == self._backup_root.resolve():
                shutil.rmtree(resolved)
