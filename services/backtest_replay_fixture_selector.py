"""Select historical replay dates that are good fixture candidates."""
from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class ReplayFixtureSampleCandidate:
    trade_date: str
    daily_rows: int
    liquid_rows: int
    replay_ready_rows: int
    rs_rows: int
    sample_codes: tuple[str, ...]

    @property
    def replay_ready_ratio(self) -> float:
        return self.replay_ready_rows / self.daily_rows if self.daily_rows else 0.0

    @property
    def rs_coverage_ratio(self) -> float:
        return self.rs_rows / self.daily_rows if self.daily_rows else 0.0

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["sample_codes"] = list(self.sample_codes)
        payload["replay_ready_ratio"] = round(self.replay_ready_ratio, 4)
        payload["rs_coverage_ratio"] = round(self.rs_coverage_ratio, 4)
        return payload


class BacktestReplayFixtureSelector:
    """Pick sample dates from the local historical SQLite store.

    The selector is intentionally data-availability oriented. It does not run
    strategies; it finds dates where replay adapters are likely to have enough
    daily snapshot, OHLCV warmup, liquidity, and RS rating coverage.
    """

    def __init__(
        self,
        db_path: str | Path = "data/stocks.db",
        *,
        min_trading_value: int = 10_000_000_000,
        min_ohlcv_days: int = 60,
        sample_code_count: int = 5,
    ) -> None:
        self._db_path = Path(db_path)
        self._min_trading_value = int(min_trading_value)
        self._min_ohlcv_days = int(min_ohlcv_days)
        self._sample_code_count = int(sample_code_count)

    def select_sample_dates(
        self,
        *,
        start_date: str | None = None,
        end_date: str | None = None,
        limit: int = 5,
    ) -> list[ReplayFixtureSampleCandidate]:
        if not self._db_path.exists():
            raise FileNotFoundError(f"backtest replay DB not found: {self._db_path}")

        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            self._validate_schema(conn)
            rows = conn.execute(
                self._candidate_sql(start_date=start_date, end_date=end_date),
                self._candidate_params(start_date=start_date, end_date=end_date),
            ).fetchall()

            candidates = [
                ReplayFixtureSampleCandidate(
                    trade_date=str(row["trade_date"]),
                    daily_rows=int(row["daily_rows"] or 0),
                    liquid_rows=int(row["liquid_rows"] or 0),
                    replay_ready_rows=int(row["replay_ready_rows"] or 0),
                    rs_rows=int(row["rs_rows"] or 0),
                    sample_codes=self._sample_codes(conn, str(row["trade_date"])),
                )
                for row in rows
            ]

        candidates.sort(
            key=lambda item: (
                item.replay_ready_rows,
                item.rs_rows,
                item.liquid_rows,
                item.trade_date,
            ),
            reverse=True,
        )
        return candidates[: int(limit)]

    def _sample_codes(self, conn: sqlite3.Connection, trade_date: str) -> tuple[str, ...]:
        rows = conn.execute(
            """
            SELECT d.code
            FROM daily_prices d
            WHERE d.trade_date = ?
              AND d.current_price > 0
              AND d.trading_value >= ?
              AND (
                  SELECT COUNT(*)
                  FROM ohlcv o
                  WHERE o.code = d.code
                    AND o.date <= d.trade_date
              ) >= ?
            ORDER BY d.trading_value DESC, d.code
            LIMIT ?
            """,
            (trade_date, self._min_trading_value, self._min_ohlcv_days, self._sample_code_count),
        ).fetchall()
        return tuple(str(row["code"]) for row in rows)

    def _candidate_sql(self, *, start_date: str | None, end_date: str | None) -> str:
        filters = []
        if start_date:
            filters.append("d.trade_date >= :start_date")
        if end_date:
            filters.append("d.trade_date <= :end_date")
        where = f"WHERE {' AND '.join(filters)}" if filters else ""
        return f"""
            SELECT
                d.trade_date,
                COUNT(*) AS daily_rows,
                SUM(CASE
                    WHEN d.current_price > 0
                     AND d.trading_value >= :min_trading_value
                    THEN 1 ELSE 0 END
                ) AS liquid_rows,
                SUM(CASE
                    WHEN d.current_price > 0
                     AND d.trading_value >= :min_trading_value
                     AND (
                         SELECT COUNT(*)
                         FROM ohlcv o
                         WHERE o.code = d.code
                           AND o.date <= d.trade_date
                     ) >= :min_ohlcv_days
                    THEN 1 ELSE 0 END
                ) AS replay_ready_rows,
                COUNT(r.code) AS rs_rows
            FROM daily_prices d
            LEFT JOIN rs_ratings r
              ON r.trade_date = d.trade_date
             AND r.code = d.code
            {where}
            GROUP BY d.trade_date
        """

    def _candidate_params(self, *, start_date: str | None, end_date: str | None) -> dict:
        params = {
            "min_trading_value": self._min_trading_value,
            "min_ohlcv_days": self._min_ohlcv_days,
        }
        if start_date:
            params["start_date"] = str(start_date)
        if end_date:
            params["end_date"] = str(end_date)
        return params

    @staticmethod
    def _validate_schema(conn: sqlite3.Connection) -> None:
        required = {"daily_prices", "ohlcv", "rs_ratings"}
        existing = {
            str(row[0])
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        missing = sorted(required - existing)
        if missing:
            raise ValueError(f"missing replay DB tables: {', '.join(missing)}")
