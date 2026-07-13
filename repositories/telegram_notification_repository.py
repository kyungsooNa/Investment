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
