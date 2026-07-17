"""Telegram 발송 성공 이력을 SQLite에 저장하고 조회한다."""
from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path
from typing import Union


class TelegramNotificationRepository:
    """Web 알림 센터에서 조회할 Telegram 발송 이력 저장소."""

    def __init__(self, db_path: Union[str, Path] = "data/telegram_notifications.db"):
        self._db_path = str(db_path)
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS telegram_notifications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sent_at TEXT NOT NULL,
                    source TEXT NOT NULL,
                    title TEXT NOT NULL,
                    message TEXT NOT NULL,
                    level TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_telegram_notifications_sent_at "
                "ON telegram_notifications(sent_at)"
            )

    def record(
        self,
        *,
        sent_at: str,
        source: str,
        title: str,
        message: str,
        level: str = "info",
    ) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """
                INSERT INTO telegram_notifications(sent_at, source, title, message, level)
                VALUES (?, ?, ?, ?, ?)
                """,
                (sent_at, source, title, message, level),
            )

    def get_by_date(self, target_date: date, count: int = 200) -> list[dict]:
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT id, sent_at, source, title, message, level
                FROM telegram_notifications
                WHERE substr(sent_at, 1, 10) = ?
                ORDER BY sent_at DESC, id DESC
                LIMIT ?
                """,
                (target_date.isoformat(), count),
            ).fetchall()

        return [
            {
                "id": f"telegram-{row['id']}",
                "timestamp": row["sent_at"],
                "category": "TELEGRAM",
                "level": row["level"],
                "title": row["title"],
                "message": row["message"],
                "metadata": {"source": row["source"]},
            }
            for row in rows
        ]

    def list_reports(self, limit: int = 200) -> list[dict]:
        """상세 리포트 보관함에 표시할 Telegram 발송 이력을 반환한다."""
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT id, sent_at, source, title
                FROM telegram_notifications
                ORDER BY sent_at DESC, id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [self._report_metadata(row) for row in rows]

    def get_report(self, report_id: str) -> dict | None:
        """보관함용 Telegram 발송 이력 한 건을 반환한다."""
        prefix = "telegram-"
        value = str(report_id)
        if not value.startswith(prefix) or not value[len(prefix):].isdigit():
            return None
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """
                SELECT id, sent_at, source, title, message
                FROM telegram_notifications
                WHERE id = ?
                """,
                (int(value[len(prefix):]),),
            ).fetchone()
        if row is None:
            return None
        return {**self._report_metadata(row), "content": row["message"]}

    @staticmethod
    def _report_metadata(row: sqlite3.Row) -> dict:
        return {
            "id": f"telegram-{row['id']}",
            "report_date": row["sent_at"][:10].replace("-", ""),
            "created_at": row["sent_at"],
            "kind": "telegram",
            "title": row["title"],
            "source": row["source"],
        }
