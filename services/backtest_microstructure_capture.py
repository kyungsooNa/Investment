"""Capture microstructure overlays for historical replay fixtures."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from common.types import ErrorCode


class BacktestMicrostructureCaptureService:
    """Collect minute bars, execution strength, and program-trade overlays."""

    def __init__(
        self,
        *,
        stock_query_service: Any,
        program_provider: Any | None = None,
        program_db_path: str | Path = "data/program_subscribe/program_trading.db",
    ) -> None:
        self._sqs = stock_query_service
        self._program_provider = program_provider
        self._program_db_path = Path(program_db_path)

    async def capture(
        self,
        *,
        codes: list[str],
        date_ymd: str,
        start_hhmmss: str = "090000",
        end_hhmmss: str = "153000",
        session: str = "REGULAR",
        include_intraday: bool = True,
        include_execution_strength: bool = True,
        program_source: str = "daily_rest",
    ) -> dict[str, Any]:
        selected_codes = [code.strip() for code in codes if code.strip()]
        intraday = await self._capture_intraday(
            selected_codes,
            date_ymd=date_ymd,
            start_hhmmss=start_hhmmss,
            end_hhmmss=end_hhmmss,
            session=session,
        ) if include_intraday else {code: [] for code in selected_codes}
        execution_strength = await self._capture_execution_strength(
            selected_codes
        ) if include_execution_strength else {code: None for code in selected_codes}
        program_trades = await self._capture_program_trades(
            selected_codes,
            date_ymd=date_ymd,
            source=program_source,
            start_hhmmss=start_hhmmss,
            end_hhmmss=end_hhmmss,
        )

        return {
            "metadata": {
                "schema_version": 1,
                "capture_type": "backtest_microstructure_overlay",
                "trade_date": date_ymd,
                "codes": selected_codes,
                "start_hhmmss": start_hhmmss,
                "end_hhmmss": end_hhmmss,
                "session": session,
                "program_source": program_source,
                "row_counts": {
                    "intraday_minutes": sum(len(rows) for rows in intraday.values()),
                    "execution_strength": sum(
                        1 for value in execution_strength.values()
                        if value is not None
                    ),
                    "program_trades": sum(
                        1 for value in program_trades.values()
                        if value is not None
                    ),
                },
            },
            "intraday_minutes": intraday,
            "execution_strength": execution_strength,
            "program_trades": program_trades,
        }

    async def _capture_intraday(
        self,
        codes: list[str],
        *,
        date_ymd: str,
        start_hhmmss: str,
        end_hhmmss: str,
        session: str,
    ) -> dict[str, list[dict]]:
        result: dict[str, list[dict]] = {}
        for code in codes:
            rows = await self._sqs.get_day_intraday_minutes_list(
                code,
                date_ymd=date_ymd,
                session=session,
                start_hhmmss=start_hhmmss,
                end_hhmmss=end_hhmmss,
            )
            result[code] = rows if isinstance(rows, list) else []
        return result

    async def _capture_execution_strength(
        self,
        codes: list[str],
    ) -> dict[str, float | None]:
        result: dict[str, float | None] = {}
        for code in codes:
            try:
                resp = await self._sqs.get_stock_conclusion(code)
            except Exception:
                result[code] = None
                continue
            result[code] = _extract_execution_strength(resp)
        return result

    async def _capture_program_trades(
        self,
        codes: list[str],
        *,
        date_ymd: str,
        source: str,
        start_hhmmss: str,
        end_hhmmss: str,
    ) -> dict[str, dict | None]:
        if source == "none":
            return {code: None for code in codes}
        if source == "program_db":
            return self._capture_program_trades_from_db(
                codes,
                start_hhmmss=start_hhmmss,
                end_hhmmss=end_hhmmss,
            )
        if source != "daily_rest":
            raise ValueError(f"unsupported program_source: {source}")
        return await self._capture_program_trades_from_daily_rest(codes, date_ymd)

    async def _capture_program_trades_from_daily_rest(
        self,
        codes: list[str],
        date_ymd: str,
    ) -> dict[str, dict | None]:
        result: dict[str, dict | None] = {}
        getter = getattr(self._program_provider, "get_program_trade_by_stock_daily", None)
        if not callable(getter):
            return {code: None for code in codes}

        for code in codes:
            try:
                resp = await getter(code, date_ymd)
            except Exception:
                result[code] = None
                continue
            qty = _extract_program_net_buy_qty(resp)
            result[code] = {"program_net_buy_qty": qty} if qty is not None else None
        return result

    def _capture_program_trades_from_db(
        self,
        codes: list[str],
        *,
        start_hhmmss: str,
        end_hhmmss: str,
    ) -> dict[str, dict | None]:
        result: dict[str, dict | None] = {code: None for code in codes}
        if not self._program_db_path.exists():
            return result

        with sqlite3.connect(self._program_db_path) as conn:
            for code in codes:
                row = conn.execute(
                    """
                    SELECT net_vol
                    FROM pt_history
                    WHERE code = ?
                      AND COALESCE(trade_time, '') >= ?
                      AND COALESCE(trade_time, '') <= ?
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (code, start_hhmmss, end_hhmmss),
                ).fetchone()
                if row is None:
                    continue
                qty = _to_int(row[0])
                result[code] = {"program_net_buy_qty": qty} if qty is not None else None
        return result


def _extract_execution_strength(resp: Any) -> float | None:
    if not _is_success(resp):
        return None
    row = _first_output_row(resp)
    if row is None:
        return None
    return _to_float(_first(row, "tday_rltv", "cgld", "execution_strength"))


def _extract_program_net_buy_qty(resp: Any) -> int | None:
    if not _is_success(resp):
        return None
    row = _response_data(resp)
    return _to_int(_first(
        row,
        "whol_smtn_ntby_qty",
        "pgtr_ntby_qty",
        "program_net_buy_qty",
        "순매수체결량",
    ))


def _is_success(resp: Any) -> bool:
    return str(getattr(resp, "rt_cd", "")) == ErrorCode.SUCCESS.value


def _response_data(resp: Any) -> Any:
    return getattr(resp, "data", None)


def _first_output_row(resp: Any) -> Any | None:
    data = _response_data(resp)
    if not isinstance(data, dict):
        return None
    output = data.get("output")
    if isinstance(output, list):
        return output[0] if output else None
    return output


def _first(row: Any, *names: str) -> Any:
    if row is None:
        return None
    for name in names:
        if isinstance(row, dict):
            value = row.get(name)
        else:
            value = getattr(row, name, None)
        if value not in (None, ""):
            return value
    return None


def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None

