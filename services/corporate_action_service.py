"""확정된 해외주식 기업행사를 거래 원장과 전략 상태에 반영한다."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Callable, Iterable


_DDL = """
CREATE TABLE IF NOT EXISTS corporate_actions (
    action_key       TEXT PRIMARY KEY,
    symbol           TEXT NOT NULL,
    action_type      TEXT NOT NULL,
    effective_date   TEXT NOT NULL,
    ratio_numerator  INTEGER,
    ratio_denominator INTEGER,
    amount_per_share REAL,
    eligible_qty     INTEGER NOT NULL DEFAULT 0,
    cash_amount      REAL NOT NULL DEFAULT 0,
    affected_lots    INTEGER NOT NULL DEFAULT 0,
    affected_sources TEXT NOT NULL DEFAULT '[]',
    applied_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_corporate_actions_symbol_date
    ON corporate_actions(symbol, effective_date);
"""


class CorporateActionService:
    """분할/병합과 현금배당을 감사 가능한 방식으로 한 번만 반영한다."""

    def __init__(
        self,
        *,
        db_path: str | Path = "data/OverseasTradeRepository/overseas_trade.db",
        state_paths: Iterable[str | Path] = (),
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self._db_path = Path(db_path)
        self._state_paths = tuple(Path(path) for path in state_paths)
        self._now = now_provider or datetime.now
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_DDL)

    def apply_split(
        self,
        symbol: str,
        effective_date: str,
        *,
        numerator: int,
        denominator: int,
        external_id: str = "",
    ) -> dict:
        symbol = self._symbol(symbol)
        effective_date = self._date(effective_date)
        numerator = int(numerator)
        denominator = int(denominator)
        if numerator <= 0 or denominator <= 0:
            raise ValueError("분할 비율은 양의 정수여야 합니다")
        action_key = self._action_key(
            external_id,
            "SPLIT",
            symbol,
            effective_date,
            str(numerator),
            str(denominator),
        )
        with self._connect() as conn:
            existing = self._find_action(conn, action_key)
            if existing is not None:
                self._assert_same_action(
                    existing,
                    action_type="SPLIT",
                    symbol=symbol,
                    effective_date=effective_date,
                    numerator=numerator,
                    denominator=denominator,
                )
                applied = False
                affected_lots = int(existing["affected_lots"])
                affected_sources = set(json.loads(existing["affected_sources"] or "[]"))
            else:
                source_column = ", source" if self._has_column(conn, "overseas_trades", "source") else ""
                rows = conn.execute(
                    f"SELECT id, qty{source_column} FROM overseas_trades "
                    "WHERE symbol=? AND status='HOLD' AND date(buy_date) < date(?) "
                    "ORDER BY id",
                    (symbol, effective_date),
                ).fetchall()
                adjusted: list[tuple[int, float, int]] = []
                affected_sources = set()
                for row in rows:
                    scaled_qty = int(row["qty"]) * numerator
                    if scaled_qty % denominator:
                        raise ValueError(
                            f"소수주가 발생하는 병합은 자동 반영할 수 없습니다: trade_id={row['id']}"
                        )
                    adjusted.append((scaled_qty // denominator, denominator / numerator, int(row["id"])))
                    if source_column and str(row["source"] or "").strip():
                        affected_sources.add(str(row["source"]).strip())

                state_updates = self._prepare_state_updates(
                    action_key,
                    symbol,
                    numerator,
                    denominator,
                    affected_lots=len(adjusted),
                    affected_sources=affected_sources,
                )

                with conn:
                    for qty, price_factor, trade_id in adjusted:
                        conn.execute(
                            "UPDATE overseas_trades SET qty=?, buy_price=buy_price * ? WHERE id=?",
                            (qty, price_factor, trade_id),
                        )
                    conn.execute(
                        "INSERT INTO corporate_actions "
                        "(action_key, symbol, action_type, effective_date, ratio_numerator, "
                        "ratio_denominator, affected_lots, affected_sources, applied_at) "
                        "VALUES (?, ?, 'SPLIT', ?, ?, ?, ?, ?, ?)",
                        (
                            action_key,
                            symbol,
                            effective_date,
                            numerator,
                            denominator,
                            len(adjusted),
                            json.dumps(sorted(affected_sources), ensure_ascii=False),
                            self._now().astimezone().isoformat(),
                        ),
                    )
                applied = True
                affected_lots = len(adjusted)

        if existing is not None:
            state_updates = self._prepare_state_updates(
                action_key,
                symbol,
                numerator,
                denominator,
                affected_lots=affected_lots,
                affected_sources=affected_sources,
            )

        for path, payload in state_updates:
            self._write_json_atomic(path, payload)
        return {
            "action_key": action_key,
            "action_type": "SPLIT",
            "symbol": symbol,
            "effective_date": effective_date,
            "applied": applied,
            "affected_lots": affected_lots,
            "state_files_updated": len(state_updates),
        }

    def record_cash_dividend(
        self,
        symbol: str,
        effective_date: str,
        *,
        amount_per_share: float,
        external_id: str = "",
    ) -> dict:
        symbol = self._symbol(symbol)
        effective_date = self._date(effective_date)
        try:
            amount = Decimal(str(amount_per_share))
        except InvalidOperation as exc:
            raise ValueError("주당 배당금이 올바르지 않습니다") from exc
        if not amount.is_finite() or amount <= 0:
            raise ValueError("주당 배당금은 양수여야 합니다")
        action_key = self._action_key(
            external_id,
            "CASH_DIVIDEND",
            symbol,
            effective_date,
            format(amount.normalize(), "f"),
        )

        with self._connect() as conn:
            existing = self._find_action(conn, action_key)
            if existing is not None:
                self._assert_same_action(
                    existing,
                    action_type="CASH_DIVIDEND",
                    symbol=symbol,
                    effective_date=effective_date,
                    amount_per_share=float(amount),
                )
                return self._dividend_result(existing, applied=False)
            row = conn.execute(
                "SELECT COALESCE(SUM(qty), 0) AS eligible_qty FROM overseas_trades "
                "WHERE symbol=? AND date(buy_date) < date(?) "
                "AND (sell_date IS NULL OR date(sell_date) >= date(?)) "
                "AND status IN ('HOLD', 'SOLD')",
                (symbol, effective_date, effective_date),
            ).fetchone()
            eligible_qty = int(row["eligible_qty"] or 0)
            cash_amount = amount * eligible_qty
            with conn:
                conn.execute(
                    "INSERT INTO corporate_actions "
                    "(action_key, symbol, action_type, effective_date, amount_per_share, "
                    "eligible_qty, cash_amount, applied_at) "
                    "VALUES (?, ?, 'CASH_DIVIDEND', ?, ?, ?, ?, ?)",
                    (
                        action_key,
                        symbol,
                        effective_date,
                        float(amount),
                        eligible_qty,
                        float(cash_amount),
                        self._now().astimezone().isoformat(),
                    ),
                )
            stored = self._find_action(conn, action_key)
        return self._dividend_result(stored, applied=True)

    def list_actions(self, symbol: str | None = None) -> list[dict]:
        with self._connect() as conn:
            if symbol:
                rows = conn.execute(
                    "SELECT * FROM corporate_actions WHERE symbol=? "
                    "ORDER BY effective_date, applied_at",
                    (self._symbol(symbol),),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM corporate_actions ORDER BY effective_date, applied_at"
                ).fetchall()
        return [dict(row) for row in rows]

    def _prepare_state_updates(
        self,
        action_key: str,
        symbol: str,
        numerator: int,
        denominator: int,
        *,
        affected_lots: int,
        affected_sources: set[str],
    ) -> list[tuple[Path, dict]]:
        updates: list[tuple[Path, dict]] = []
        if affected_lots <= 0:
            return updates
        for path in self._state_paths:
            if not path.is_file():
                continue
            payload = json.loads(path.read_text(encoding="utf-8"))
            markers = payload.get("applied_corporate_actions") or []
            if action_key in markers:
                continue
            strategy = str(payload.get("strategy") or "").strip()
            if affected_sources and strategy and strategy not in affected_sources:
                continue
            positions = payload.get("positions") or {}
            held = positions.get(symbol)
            if isinstance(held, dict):
                scaled_qty = int(held.get("qty") or 0) * numerator
                if scaled_qty % denominator:
                    raise ValueError(f"소수주가 발생하는 전략 상태는 자동 반영할 수 없습니다: {path}")
                held["qty"] = scaled_qty // denominator
                price_factor = denominator / numerator
                for key in ("entry_price", "stop_price", "last_price"):
                    if held.get(key) is not None:
                        held[key] = float(held[key]) * price_factor
            payload["applied_corporate_actions"] = [*markers, action_key]
            updates.append((path, payload))
        return updates

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _find_action(conn: sqlite3.Connection, action_key: str):
        return conn.execute(
            "SELECT * FROM corporate_actions WHERE action_key=?",
            (action_key,),
        ).fetchone()

    @staticmethod
    def _has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
        return any(row[1] == column for row in conn.execute(f"PRAGMA table_info({table})"))

    @staticmethod
    def _assert_same_action(
        row,
        *,
        action_type: str,
        symbol: str,
        effective_date: str,
        numerator: int | None = None,
        denominator: int | None = None,
        amount_per_share: float | None = None,
    ) -> None:
        same = (
            row["action_type"] == action_type
            and row["symbol"] == symbol
            and row["effective_date"] == effective_date
        )
        if action_type == "SPLIT":
            same = same and row["ratio_numerator"] == numerator and row["ratio_denominator"] == denominator
        else:
            same = same and row["amount_per_share"] == amount_per_share
        if not same:
            raise ValueError("같은 external_id를 다른 기업행사에 재사용할 수 없습니다")

    @staticmethod
    def _dividend_result(row, *, applied: bool) -> dict:
        return {
            "action_key": row["action_key"],
            "action_type": row["action_type"],
            "symbol": row["symbol"],
            "effective_date": row["effective_date"],
            "applied": applied,
            "eligible_qty": int(row["eligible_qty"]),
            "cash_amount": float(row["cash_amount"]),
        }

    @staticmethod
    def _action_key(external_id: str, *parts: str) -> str:
        if str(external_id or "").strip():
            return f"external:{str(external_id).strip()}"
        raw = "|".join(parts).encode("utf-8")
        return f"generated:{hashlib.sha256(raw).hexdigest()}"

    @staticmethod
    def _symbol(value: str) -> str:
        symbol = str(value or "").strip().upper()
        if not symbol:
            raise ValueError("종목 심볼은 필수입니다")
        return symbol

    @staticmethod
    def _date(value: str) -> str:
        try:
            return date.fromisoformat(str(value)).isoformat()
        except ValueError as exc:
            raise ValueError("기준일은 YYYY-MM-DD 형식이어야 합니다") from exc

    @staticmethod
    def _write_json_atomic(path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_name, path)
        except Exception:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
            raise
