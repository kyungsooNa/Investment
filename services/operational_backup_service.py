"""거래 운영 복구에 필요한 소형 상태 파일을 백업하고 복원 가능성을 검증한다."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import sqlite3
import tempfile
from datetime import datetime, timedelta
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

    def get_health(self, *, history_limit: int = 10, stale_after_hours: int = 26) -> dict:
        """최근 백업 manifest를 읽어 운영 대시보드용 건강도를 반환한다."""
        history: list[dict] = []
        if self._backup_root.is_dir():
            candidates = sorted(
                (
                    path for path in self._backup_root.iterdir()
                    if path.is_dir() and _BACKUP_DIR_PATTERN.fullmatch(path.name)
                ),
                reverse=True,
            )
            for backup_dir in candidates[:max(int(history_limit), 1)]:
                summary = self._manifest_summary(backup_dir)
                history.append(summary)

        latest = history[0] if history else None
        if latest is None:
            status = "missing"
        elif latest.get("status") != "passed":
            status = "failed"
        else:
            try:
                created_at = datetime.fromisoformat(str(latest["created_at"]))
                current = self._now().astimezone(created_at.tzinfo) if created_at.tzinfo else self._now()
                status = "stale" if current - created_at > timedelta(hours=stale_after_hours) else "healthy"
            except (KeyError, TypeError, ValueError):
                status = "failed"
        return {
            "status": status,
            "latest": latest,
            "history": history,
            "stale_after_hours": stale_after_hours,
        }

    def restore_backup(
        self,
        backup_id: str,
        *,
        confirm_backup_id: str,
        destination_dir: str | Path,
    ) -> dict:
        """검증된 백업을 비어 있는 별도 복구 디렉터리에 복원한다.

        라이브 data_dir 덮어쓰기는 의도적으로 지원하지 않는다. 운영자는 복구
        결과를 점검한 뒤 별도 배포 절차로 반영해야 한다.
        """
        if backup_id != confirm_backup_id:
            raise ValueError("백업 확인 ID가 일치하지 않습니다")
        if not _BACKUP_DIR_PATTERN.fullmatch(backup_id):
            raise ValueError("유효하지 않은 백업 ID입니다")

        backup_dir = (self._backup_root / backup_id).resolve()
        if backup_dir.parent != self._backup_root.resolve() or not backup_dir.is_dir():
            raise ValueError("백업 ID를 찾을 수 없습니다")
        verification = self.verify_backup(backup_dir)
        if not verification["passed"]:
            raise ValueError("복원 전 백업 검증에 실패했습니다")

        destination = Path(destination_dir).resolve()
        data_root = self._data_dir.resolve()
        if destination == data_root or self._is_within(destination, data_root):
            raise ValueError("라이브 data_dir 내부에는 복원할 수 없습니다")
        if destination.exists() and any(destination.iterdir()):
            raise ValueError("복원 대상 디렉터리는 비어 있어야 합니다")
        destination.mkdir(parents=True, exist_ok=True)

        manifest = json.loads((backup_dir / "manifest.json").read_text(encoding="utf-8"))
        restored = 0
        for entry in manifest.get("files", []):
            source = (backup_dir / Path(str(entry["backup_path"]))).resolve()
            target = (destination / Path(str(entry["source"]))).resolve()
            if not self._is_within(source, backup_dir) or not self._is_within(target, destination):
                raise ValueError("복원 경로 검증에 실패했습니다")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            restored += 1
        return {
            "status": "restored",
            "backup_id": backup_id,
            "destination_dir": str(destination),
            "restored_count": restored,
            "verification": verification,
        }

    @staticmethod
    def _manifest_summary(backup_dir: Path) -> dict:
        try:
            manifest = json.loads((backup_dir / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            return {
                "backup_id": backup_dir.name,
                "status": "failed",
                "file_count": 0,
                "verified_count": 0,
                "error": f"manifest_invalid: {exc}",
            }
        verification = manifest.get("restore_verification") or {}
        return {
            "backup_id": backup_dir.name,
            "created_at": manifest.get("created_at"),
            "status": manifest.get("status", "failed"),
            "file_count": len(manifest.get("files") or []),
            "verified_count": verification.get("verified_count", 0),
            "missing_sources": manifest.get("missing_sources") or [],
            "errors": verification.get("errors") or [],
        }

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
