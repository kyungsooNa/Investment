"""Capture microstructure overlays for historical replay fixtures."""
from __future__ import annotations

import json
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
        execution_strength_db_path: str | Path = "data/execution_strength/execution_strength.db",
    ) -> None:
        self._sqs = stock_query_service
        self._program_provider = program_provider
        self._program_db_path = Path(program_db_path)
        self._execution_strength_db_path = Path(execution_strength_db_path)

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
        execution_strength_source: str = "rest_scalar",
        candidate_sources: dict[str, list[str]] | None = None,
    ) -> dict[str, Any]:
        selected_codes = [code.strip() for code in codes if code.strip()]
        intraday, stale_minute_rows_dropped = await self._capture_intraday(
            selected_codes,
            date_ymd=date_ymd,
            start_hhmmss=start_hhmmss,
            end_hhmmss=end_hhmmss,
            session=session,
        ) if include_intraday else ({code: [] for code in selected_codes}, {})
        execution_strength = await self._capture_execution_strength(
            selected_codes
        ) if include_execution_strength else {code: None for code in selected_codes}
        execution_strength_intraday, execution_strength_fallback_codes = (
            self._capture_execution_strength_intraday(
                selected_codes,
                date_ymd=date_ymd,
                source=execution_strength_source,
                start_hhmmss=start_hhmmss,
                end_hhmmss=end_hhmmss,
            )
        )
        program_trades, program_fallback_codes = await self._capture_program_trades(
            selected_codes,
            date_ymd=date_ymd,
            source=program_source,
            start_hhmmss=start_hhmmss,
            end_hhmmss=end_hhmmss,
        )
        empty_minute_codes = (
            [code for code in selected_codes if not intraday.get(code)]
            if include_intraday else []
        )

        metadata: dict[str, Any] = {
                "schema_version": 1,
                "capture_type": "backtest_microstructure_overlay",
                "trade_date": date_ymd,
                "codes": selected_codes,
                "start_hhmmss": start_hhmmss,
                "end_hhmmss": end_hhmmss,
                "session": session,
                "program_source": program_source,
                "program_fallback_codes": program_fallback_codes,
                "execution_strength_source": execution_strength_source,
                "execution_strength_fallback_codes": execution_strength_fallback_codes,
                "quality": {
                    "empty_minute_codes": empty_minute_codes,
                    "stale_minute_rows_dropped": stale_minute_rows_dropped,
                },
                "row_counts": {
                    "intraday_minutes": sum(len(rows) for rows in intraday.values()),
                    "execution_strength": sum(
                        1 for value in execution_strength.values()
                        if value is not None
                    ),
                    "execution_strength_intraday_rows": sum(
                        len(rows) for rows in execution_strength_intraday.values()
                    ),
                    "program_trades": sum(
                        1 for value in program_trades.values()
                        if value is not None
                    ),
                },
        }
        if candidate_sources is not None:
            metadata["candidate_sources"] = candidate_sources
        return {
            "metadata": metadata,
            "intraday_minutes": intraday,
            "execution_strength": execution_strength,
            "execution_strength_intraday": execution_strength_intraday,
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
    ) -> tuple[dict[str, list[dict]], dict[str, int]]:
        result: dict[str, list[dict]] = {}
        stale_dropped: dict[str, int] = {}
        for code in codes:
            rows = await self._sqs.get_day_intraday_minutes_list(
                code,
                date_ymd=date_ymd,
                session=session,
                start_hhmmss=start_hhmmss,
                end_hhmmss=end_hhmmss,
            )
            rows = rows if isinstance(rows, list) else []
            # 무거래/정지 종목에서 API 가 직전 거래일 분봉을 반환할 수 있다 —
            # 캡처 대상일과 다른 날짜 행은 버린다 (날짜 필드 없는 행은 보존).
            kept = [
                row for row in rows
                if not (
                    isinstance(row, dict)
                    and row.get("stck_bsop_date") not in (None, "", date_ymd)
                )
            ]
            if len(kept) != len(rows):
                stale_dropped[code] = len(rows) - len(kept)
            result[code] = kept
        return result, stale_dropped

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

    def _capture_execution_strength_intraday(
        self,
        codes: list[str],
        *,
        date_ymd: str,
        source: str,
        start_hhmmss: str,
        end_hhmmss: str,
    ) -> tuple[dict[str, list[dict]], list[str]]:
        if source not in ("rest_scalar", "es_db"):
            raise ValueError(f"unsupported execution_strength_source: {source}")
        result: dict[str, list[dict]] = {code: [] for code in codes}
        if source == "rest_scalar":
            return result, []
        # es_db: 장중 WS 틱 샘플링 DB(es_history). 미스 종목(무틱/미구독)은
        # 기존 REST 스칼라(execution_strength)가 폴백 역할을 한다.
        if self._execution_strength_db_path.exists():
            with sqlite3.connect(self._execution_strength_db_path) as conn:
                for code in codes:
                    rows = conn.execute(
                        """
                        SELECT trade_time, strength
                        FROM es_history
                        WHERE code = ?
                          AND trade_date = ?
                          AND trade_time >= ?
                          AND trade_time <= ?
                        ORDER BY trade_time ASC, id ASC
                        """,
                        (code, date_ymd, start_hhmmss, end_hhmmss),
                    ).fetchall()
                    result[code] = [
                        {"time": row[0], "strength": row[1]} for row in rows
                    ]
        fallback_codes = [code for code in codes if not result[code]]
        return result, fallback_codes

    async def _capture_program_trades(
        self,
        codes: list[str],
        *,
        date_ymd: str,
        source: str,
        start_hhmmss: str,
        end_hhmmss: str,
    ) -> tuple[dict[str, dict | None], list[str]]:
        if source == "none":
            return {code: None for code in codes}, []
        if source == "program_db":
            result = self._capture_program_trades_from_db(
                codes,
                start_hhmmss=start_hhmmss,
                end_hhmmss=end_hhmmss,
            )
            # 프로그램 WS 구독(pt_subscriptions)이 후보 종목을 커버하지 못하면
            # DB 행이 없다 — 미스 종목만 daily_rest 로 폴백해 overlay 전량 null 을 막는다.
            missing = [code for code in codes if result.get(code) is None]
            fallback_codes: list[str] = []
            if missing:
                fallback = await self._capture_program_trades_from_daily_rest(
                    missing, date_ymd
                )
                for code in missing:
                    if fallback.get(code) is not None:
                        result[code] = fallback[code]
                        fallback_codes.append(code)
            return result, fallback_codes
        if source != "daily_rest":
            raise ValueError(f"unsupported program_source: {source}")
        return await self._capture_program_trades_from_daily_rest(codes, date_ymd), []

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

    @staticmethod
    def write_overlay_files(payload: dict[str, Any], output_dir: Path) -> dict[str, Path]:
        """capture() payload를 replay fixture 규약 파일명 4종으로 저장한다.

        CLI(scripts.capture_backtest_microstructure)와 after-market 태스크가
        동일한 파일 레이아웃을 쓰도록 단일 소스로 유지한다.
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        trade_date = payload["metadata"]["trade_date"]
        capture_path = output_dir / f"replay_microstructure_{trade_date}.json"
        execution_strength_path = output_dir / f"replay_execution_strength_{trade_date}.json"
        execution_strength_intraday_path = (
            output_dir / f"replay_execution_strength_intraday_{trade_date}.json"
        )
        program_trades_path = output_dir / f"replay_program_trades_{trade_date}.json"
        intraday_path = output_dir / f"replay_intraday_minutes_{trade_date}.json"

        _write_json(capture_path, payload)
        _write_json(execution_strength_path, payload.get("execution_strength", {}))
        _write_json(
            execution_strength_intraday_path,
            payload.get("execution_strength_intraday", {}),
        )
        _write_json(program_trades_path, _flatten_program_trades(payload.get("program_trades", {})))
        _write_json(intraday_path, payload.get("intraday_minutes", {}))

        return {
            "capture": capture_path,
            "execution_strength": execution_strength_path,
            "execution_strength_intraday": execution_strength_intraday_path,
            "program_trades": program_trades_path,
            "intraday_minutes": intraday_path,
        }


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


def _flatten_program_trades(program_trades: dict[str, Any]) -> dict[str, int | None]:
    flattened: dict[str, int | None] = {}
    for code, row in program_trades.items():
        flattened[code] = (
            row.get("program_net_buy_qty")
            if isinstance(row, dict)
            else None
        )
    return flattened


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

