"""Export deterministic historical replay fixtures from the local SQLite DB."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterable

from services.backtest_replay_fixture_selector import BacktestReplayFixtureSelector


class BacktestReplayFixtureExporter:
    """Build a JSON-serializable replay fixture for selected date/codes."""

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

    def export_fixture(
        self,
        *,
        trade_date: str,
        codes: Iterable[str] | None = None,
        ohlcv_lookback_days: int = 60,
    ) -> dict:
        if not self._db_path.exists():
            raise FileNotFoundError(f"backtest replay DB not found: {self._db_path}")

        selected_codes = [str(code) for code in codes] if codes is not None else self._sample_codes(trade_date)
        if not selected_codes:
            raise ValueError(f"replay fixture codes not found: {trade_date}")

        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            BacktestReplayFixtureSelector._validate_schema(conn)
            daily_rows = self._daily_rows(conn, trade_date, selected_codes)
            if len(daily_rows) != len(selected_codes):
                raise ValueError(f"daily snapshot rows not found for all codes: {trade_date}")

            ohlcv_by_code = {
                code: self._ohlcv_rows(conn, trade_date, code, int(ohlcv_lookback_days))
                for code in selected_codes
            }
            missing_ohlcv = [code for code, rows in ohlcv_by_code.items() if not rows]
            if missing_ohlcv:
                raise ValueError(f"ohlcv rows not found: {trade_date} {','.join(missing_ohlcv)}")

            rs_rows = self._rs_rows(conn, trade_date, selected_codes)

        return {
            "metadata": {
                "schema_version": 1,
                "fixture_type": "historical_replay_snapshot",
                "trade_date": str(trade_date),
                "codes": selected_codes,
                "ohlcv_lookback_days": int(ohlcv_lookback_days),
                "row_counts": {
                    "daily_prices": len(daily_rows),
                    "ohlcv": sum(len(rows) for rows in ohlcv_by_code.values()),
                    "rs_ratings": len(rs_rows),
                },
            },
            "daily_prices": daily_rows,
            "ohlcv": ohlcv_by_code,
            "rs_ratings": rs_rows,
        }

    def _sample_codes(self, trade_date: str) -> list[str]:
        selector = BacktestReplayFixtureSelector(
            self._db_path,
            min_trading_value=self._min_trading_value,
            min_ohlcv_days=self._min_ohlcv_days,
            sample_code_count=self._sample_code_count,
        )
        candidates = selector.select_sample_dates(
            start_date=trade_date,
            end_date=trade_date,
            limit=1,
        )
        if not candidates:
            return []
        return list(candidates[0].sample_codes)

    @staticmethod
    def _daily_rows(
        conn: sqlite3.Connection,
        trade_date: str,
        codes: list[str],
    ) -> list[dict]:
        rows_by_code = {}
        for row in conn.execute(
            _in_clause_sql("SELECT * FROM daily_prices WHERE trade_date = ? AND code IN ({})", codes),
            [trade_date, *codes],
        ).fetchall():
            rows_by_code[str(row["code"])] = dict(row)
        return [rows_by_code[code] for code in codes if code in rows_by_code]

    @staticmethod
    def _ohlcv_rows(
        conn: sqlite3.Connection,
        trade_date: str,
        code: str,
        limit: int,
    ) -> list[dict]:
        rows = conn.execute(
            """
            SELECT *
            FROM ohlcv
            WHERE code = ?
              AND date <= ?
            ORDER BY date DESC
            LIMIT ?
            """,
            (code, trade_date, limit),
        ).fetchall()
        return [dict(row) for row in reversed(rows)]

    @staticmethod
    def _rs_rows(
        conn: sqlite3.Connection,
        trade_date: str,
        codes: list[str],
    ) -> list[dict]:
        rows_by_code = {}
        for row in conn.execute(
            _in_clause_sql("SELECT * FROM rs_ratings WHERE trade_date = ? AND code IN ({})", codes),
            [trade_date, *codes],
        ).fetchall():
            rows_by_code[str(row["code"])] = dict(row)
        return [rows_by_code[code] for code in codes if code in rows_by_code]


def _in_clause_sql(template: str, values: list[str]) -> str:
    if not values:
        raise ValueError("IN clause values must not be empty")
    return template.format(",".join("?" for _ in values))
