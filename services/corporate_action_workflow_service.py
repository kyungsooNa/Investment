"""기업행사 미리보기·승인·백업·적용·대사 워크플로."""
from __future__ import annotations

import hashlib
import json
import math
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

from services.corporate_action_service import CorporateActionService


class CorporateActionWorkflowService:
    def __init__(self, *, service: CorporateActionService, db_path, state_paths=(), backup_root=None):
        self._service = service
        self._db_path = Path(db_path)
        self._state_paths = tuple(Path(path) for path in state_paths)
        self._backup_root = Path(backup_root) if backup_root else self._db_path.parent / "corporate_action_backups"

    def preview_split(self, symbol, effective_date, *, numerator, denominator, external_id="") -> dict:
        symbol = self._service._symbol(symbol)
        effective_date = self._service._date(effective_date)
        numerator, denominator = int(numerator), int(denominator)
        if numerator <= 0 or denominator <= 0:
            raise ValueError("분할 비율은 양의 정수여야 합니다")
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT id, qty, buy_price FROM overseas_trades "
                "WHERE symbol=? AND status='HOLD' AND date(buy_date) < date(?) ORDER BY id",
                (symbol, effective_date),
            ).fetchall()
        lots = []
        for row in rows:
            scaled = int(row["qty"]) * numerator
            if scaled % denominator:
                raise ValueError(f"소수주가 발생하는 병합은 자동 반영할 수 없습니다: trade_id={row['id']}")
            lots.append({
                "trade_id": int(row["id"]),
                "qty_before": int(row["qty"]),
                "qty_after": scaled // denominator,
                "price_before": float(row["buy_price"]),
                "price_after": float(row["buy_price"]) * denominator / numerator,
            })
        preview = {
            "phase": "preview",
            "action_type": "SPLIT",
            "symbol": symbol,
            "effective_date": effective_date,
            "numerator": numerator,
            "denominator": denominator,
            "external_id": str(external_id or ""),
            "action_key": self._service._action_key(
                external_id, "SPLIT", symbol, effective_date, str(numerator), str(denominator)
            ),
            "affected_lots": len(lots),
            "lots": lots,
            "state_files": [str(path) for path in self._state_paths if path.is_file()],
        }
        preview["approval_token"] = self._token(preview)
        return preview

    def preview_dividend(self, symbol, effective_date, *, amount_per_share, external_id="") -> dict:
        symbol = self._service._symbol(symbol)
        effective_date = self._service._date(effective_date)
        amount = float(amount_per_share)
        if not math.isfinite(amount) or amount <= 0:
            raise ValueError("주당 배당금은 양수여야 합니다")
        with sqlite3.connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(qty), 0) FROM overseas_trades WHERE symbol=? "
                "AND date(buy_date) < date(?) AND (sell_date IS NULL OR date(sell_date) >= date(?)) "
                "AND status IN ('HOLD', 'SOLD')",
                (symbol, effective_date, effective_date),
            ).fetchone()
        eligible_qty = int(row[0] or 0)
        preview = {
            "phase": "preview",
            "action_type": "CASH_DIVIDEND",
            "symbol": symbol,
            "effective_date": effective_date,
            "amount_per_share": amount,
            "external_id": str(external_id or ""),
            "action_key": self._service._action_key(
                external_id, "CASH_DIVIDEND", symbol, effective_date, str(amount)
            ),
            "eligible_qty": eligible_qty,
            "cash_amount": amount * eligible_qty,
        }
        preview["approval_token"] = self._token(preview)
        return preview

    def apply_split(self, *, approval_token: str, **kwargs) -> dict:
        preview = self.preview_split(**kwargs)
        self._approve(preview, approval_token)
        backup = self._backup(preview)
        result = self._service.apply_split(**kwargs)
        reconciliation = self._reconcile_split(preview)
        return {**result, "phase": "applied", "backup": backup, "reconciliation": reconciliation}

    def apply_dividend(self, *, approval_token: str, **kwargs) -> dict:
        preview = self.preview_dividend(**kwargs)
        self._approve(preview, approval_token)
        backup = self._backup(preview)
        result = self._service.record_cash_dividend(**kwargs)
        actions = self._service.list_actions(symbol=preview["symbol"])
        passed = any(
            row["action_type"] == "CASH_DIVIDEND"
            and row["effective_date"] == preview["effective_date"]
            and int(row["eligible_qty"]) == preview["eligible_qty"]
            for row in actions
        )
        return {
            **result,
            "phase": "applied",
            "backup": backup,
            "reconciliation": {"passed": passed},
        }

    @staticmethod
    def _token(preview: dict) -> str:
        payload = {key: value for key, value in preview.items() if key != "approval_token"}
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()[:24]

    @staticmethod
    def _approve(preview: dict, supplied: str) -> None:
        if not supplied or supplied != preview["approval_token"]:
            raise ValueError("승인 토큰이 현재 원장 미리보기와 일치하지 않습니다")

    def _backup(self, preview: dict) -> dict:
        backup_id = f"{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}_{preview['approval_token'][:8]}"
        backup_dir = self._backup_root / backup_id
        backup_dir.mkdir(parents=True, exist_ok=False)
        db_target = backup_dir / self._db_path.name
        source = sqlite3.connect(f"file:{self._db_path.resolve().as_posix()}?mode=ro", uri=True)
        target = sqlite3.connect(db_target)
        try:
            source.backup(target)
            integrity = target.execute("PRAGMA integrity_check").fetchone()
        finally:
            target.close()
            source.close()
        if not integrity or integrity[0] != "ok":
            raise sqlite3.DatabaseError(f"기업행사 사전 백업 무결성 실패: {integrity}")
        copied_states = []
        for path in self._state_paths:
            if path.is_file():
                state_target = backup_dir / "states" / path.name
                state_target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, state_target)
                copied_states.append(path.name)
        manifest = {
            "status": "passed",
            "backup_id": backup_id,
            "db": self._db_path.name,
            "state_files": copied_states,
            "approval_token": preview["approval_token"],
        }
        (backup_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return {**manifest, "backup_dir": str(backup_dir)}

    def _reconcile_split(self, preview: dict) -> dict:
        expected = {lot["trade_id"]: lot for lot in preview["lots"]}
        actual = {}
        with sqlite3.connect(self._db_path) as conn:
            for trade_id in expected:
                row = conn.execute(
                    "SELECT qty, buy_price FROM overseas_trades WHERE id=?", (trade_id,)
                ).fetchone()
                if row:
                    actual[trade_id] = {"qty": int(row[0]), "price": float(row[1])}
        mismatches = []
        for trade_id, lot in expected.items():
            row = actual.get(trade_id)
            if row is None or row["qty"] != lot["qty_after"] or abs(row["price"] - lot["price_after"]) > 1e-9:
                mismatches.append(trade_id)
        audit_present = any(
            row["action_key"] == preview["action_key"]
            for row in self._service.list_actions(symbol=preview["symbol"])
        )
        return {
            "passed": not mismatches and audit_present,
            "checked_lots": len(expected),
            "mismatched_trade_ids": mismatches,
            "audit_present": audit_present,
        }
