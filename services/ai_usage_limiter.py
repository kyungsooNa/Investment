"""AI API 일일 요청 한도와 공시 예비량을 SQLite로 영속 관리한다."""
from __future__ import annotations

import asyncio
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Callable

import aiosqlite
import pytz


_PACIFIC = pytz.timezone("America/Los_Angeles")
_DDL = """
CREATE TABLE IF NOT EXISTS ai_usage_daily (
    period_key TEXT NOT NULL,
    usage_type TEXT NOT NULL,
    request_count INTEGER NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (period_key, usage_type)
)
"""


class AiUsageLimitExceeded(RuntimeError):
    def __init__(
        self,
        *,
        limit_kind: str,
        daily_limit: int,
        used: int,
        reset_at: str,
        interactive_limit: int | None = None,
        disclosure_reserve: int = 0,
    ) -> None:
        self.limit_kind = str(limit_kind)
        self.daily_limit = int(daily_limit)
        self.used = int(used)
        self.reset_at = str(reset_at)
        self.interactive_limit = (
            None if interactive_limit is None else int(interactive_limit)
        )
        self.disclosure_reserve = int(disclosure_reserve)
        if self.limit_kind == "interactive":
            message = (
                f"AI 일반 분석 일일 한도({self.interactive_limit}회)에 도달했습니다. "
                f"공시 요약용 {self.disclosure_reserve}회는 보호됩니다."
            )
        else:
            message = f"AI API 일일 한도({self.daily_limit}회)에 도달했습니다."
        super().__init__(message)


class AiUsageLimiter:
    DB_PATH = Path("data/ai_usage.db")

    def __init__(
        self,
        *,
        daily_request_limit: int = 100,
        disclosure_reserve: int = 20,
        db_path: str | Path | None = None,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self._daily_limit = max(0, int(daily_request_limit))
        self._disclosure_reserve = min(
            self._daily_limit, max(0, int(disclosure_reserve))
        )
        self._db_path = Path(db_path or self.DB_PATH)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._now_provider = now_provider or (lambda: datetime.now(pytz.utc))
        self._lock = asyncio.Lock()

    async def reserve(self, usage_type: str) -> None:
        """실제 외부 AI 요청 1회를 원자적으로 예약한다."""
        if self._daily_limit <= 0:
            return
        usage_type = str(usage_type or "general").strip().lower() or "general"
        period_key, reset_at = self._period()
        interactive_limit = self._daily_limit - self._disclosure_reserve

        async with self._lock:
            async with aiosqlite.connect(self._db_path, timeout=10) as conn:
                await conn.execute(_DDL)
                await conn.commit()
                await conn.execute("BEGIN IMMEDIATE")
                total, disclosure_used = await self._counts(conn, period_key)
                interactive_used = total - disclosure_used

                if total >= self._daily_limit:
                    await conn.rollback()
                    raise AiUsageLimitExceeded(
                        limit_kind="daily",
                        daily_limit=self._daily_limit,
                        used=total,
                        reset_at=reset_at,
                        interactive_limit=interactive_limit,
                        disclosure_reserve=self._disclosure_reserve,
                    )
                if (
                    usage_type != "disclosure"
                    and interactive_used >= interactive_limit
                ):
                    await conn.rollback()
                    raise AiUsageLimitExceeded(
                        limit_kind="interactive",
                        daily_limit=self._daily_limit,
                        used=interactive_used,
                        reset_at=reset_at,
                        interactive_limit=interactive_limit,
                        disclosure_reserve=self._disclosure_reserve,
                    )

                await conn.execute(
                    """
                    INSERT INTO ai_usage_daily(
                        period_key, usage_type, request_count, updated_at
                    ) VALUES (?, ?, 1, ?)
                    ON CONFLICT(period_key, usage_type) DO UPDATE SET
                        request_count = request_count + 1,
                        updated_at = excluded.updated_at
                    """,
                    (period_key, usage_type, self._now().isoformat()),
                )
                await conn.commit()

    async def get_snapshot(self) -> dict:
        period_key, reset_at = self._period()
        interactive_limit = max(
            0, self._daily_limit - self._disclosure_reserve
        )
        if self._daily_limit <= 0:
            return {
                "enabled": False,
                "period_key": period_key,
                "used": 0,
                "interactive_used": 0,
                "disclosure_used": 0,
                "daily_limit": 0,
                "interactive_limit": 0,
                "disclosure_reserve": 0,
                "remaining": None,
                "reset_at": reset_at,
            }
        async with self._lock:
            async with aiosqlite.connect(self._db_path, timeout=10) as conn:
                await conn.execute(_DDL)
                await conn.commit()
                total, disclosure_used = await self._counts(conn, period_key)
        return {
            "enabled": True,
            "period_key": period_key,
            "used": total,
            "interactive_used": total - disclosure_used,
            "disclosure_used": disclosure_used,
            "daily_limit": self._daily_limit,
            "interactive_limit": interactive_limit,
            "disclosure_reserve": self._disclosure_reserve,
            "remaining": max(0, self._daily_limit - total),
            "reset_at": reset_at,
        }

    async def _counts(
        self, conn: aiosqlite.Connection, period_key: str
    ) -> tuple[int, int]:
        async with conn.execute(
            """
            SELECT
                COALESCE(SUM(request_count), 0),
                COALESCE(SUM(
                    CASE WHEN usage_type = 'disclosure' THEN request_count ELSE 0 END
                ), 0)
            FROM ai_usage_daily
            WHERE period_key = ?
            """,
            (period_key,),
        ) as cursor:
            row = await cursor.fetchone()
        return int(row[0]), int(row[1])

    def _now(self) -> datetime:
        now = self._now_provider()
        if now.tzinfo is None:
            now = pytz.utc.localize(now)
        return now.astimezone(_PACIFIC)

    def _period(self) -> tuple[str, str]:
        now = self._now()
        next_date = now.date() + timedelta(days=1)
        reset_at = _PACIFIC.localize(datetime.combine(next_date, time.min))
        return now.date().isoformat(), reset_at.isoformat()
