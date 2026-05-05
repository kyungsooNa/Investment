"""File-backed repository for normalized backtest trade journal runs."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


class BacktestJournalRepository:
    """Persist backtest journal records so live-vs-backtest reports can reload them."""

    def __init__(self, base_dir: str | Path = "data/backtest_journals") -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def save_run(
        self,
        records: Iterable[dict[str, Any]],
        *,
        run_id: str | None = None,
        strategy: str = "",
        target_date: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        records_list = list(records)
        saved_at = datetime.now(timezone.utc).isoformat()
        safe_run_id = self._safe_run_id(
            run_id or self._default_run_id(strategy, target_date, saved_at)
        )
        payload = {
            "run_id": safe_run_id,
            "strategy": strategy,
            "target_date": target_date,
            "record_count": len(records_list),
            "saved_at": saved_at,
            "metadata": metadata or {},
            "records": records_list,
        }
        self._run_path(safe_run_id).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return self._summary(payload)

    def list_runs(self, *, limit: int | None = 50) -> list[dict[str, Any]]:
        runs = []
        for path in self.base_dir.glob("*.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            runs.append(self._summary(payload))

        runs.sort(key=lambda row: str(row.get("saved_at") or ""), reverse=True)
        if limit is not None and limit > 0:
            return runs[:limit]
        return runs

    def load_records(self, run_id: str) -> list[dict[str, Any]]:
        safe_run_id = self._safe_run_id(run_id)
        path = self._run_path(safe_run_id)
        if not path.exists():
            return []
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        records = payload.get("records")
        return records if isinstance(records, list) else []

    def load_records_for_date(
        self,
        target_date: str,
        *,
        strategy: str | None = None,
    ) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        target = str(target_date)
        for run in self.list_runs(limit=None):
            if str(run.get("target_date") or "") != target:
                continue
            if strategy and str(run.get("strategy") or "") != strategy:
                continue
            records.extend(self.load_records(str(run.get("run_id") or "")))
        return records

    def _run_path(self, run_id: str) -> Path:
        return self.base_dir / f"{run_id}.json"

    @staticmethod
    def _safe_run_id(run_id: str) -> str:
        sanitized = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(run_id).strip())
        sanitized = sanitized.strip("._")
        return sanitized or "backtest_run"

    @staticmethod
    def _default_run_id(strategy: str, target_date: str, saved_at: str) -> str:
        timestamp = "".join(ch for ch in saved_at if ch.isdigit())[:14]
        parts = [strategy or "backtest", target_date or "unknown", timestamp]
        return "_".join(parts)

    @staticmethod
    def _summary(payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "run_id": str(payload.get("run_id") or ""),
            "strategy": str(payload.get("strategy") or ""),
            "target_date": str(payload.get("target_date") or ""),
            "record_count": int(payload.get("record_count") or 0),
            "saved_at": str(payload.get("saved_at") or ""),
            "metadata": payload.get("metadata") or {},
        }
