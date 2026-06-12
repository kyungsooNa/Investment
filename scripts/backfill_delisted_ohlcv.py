"""Backfill OHLCV CSV files for KRX delisted stocks."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any, Optional, Sequence

import pandas as pd

from scripts.audit_delisting_universe import (
    fetch_fdr_delisting_listing,
    normalize_fdr_delistings,
)


def _normalize_date(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    ts = pd.to_datetime(value, errors="coerce")
    if pd.isna(ts):
        return str(value).strip()
    return ts.strftime("%Y-%m-%d")


def _split_csv_arg(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in str(value).split(",") if part.strip())


def _safe_int(value: Any) -> Optional[int]:
    if value is None or pd.isna(value):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _get_value(row: pd.Series, *columns: str) -> Any:
    for column in columns:
        if column in row.index and not pd.isna(row[column]):
            return row[column]
    return None


def fetch_delisted_ohlcv(symbol: str, start: str, end: str) -> pd.DataFrame:
    import FinanceDataReader as fdr

    try:
        return fdr.DataReader(symbol, start, end, exchange="KRX-DELISTING")
    except TypeError:
        return fdr.DataReader(symbol, start, end)


def normalize_ohlcv_frame(symbol: str, df: pd.DataFrame) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if df is None or df.empty:
        return rows

    frame = df.copy()
    if "Date" not in frame.columns:
        frame = frame.reset_index().rename(columns={"index": "Date"})

    for _, row in frame.iterrows():
        date = _normalize_date(_get_value(row, "Date", "날짜"))
        if not date:
            continue
        rows.append({
            "date": date,
            "symbol": symbol,
            "open": _safe_int(_get_value(row, "Open", "시가")),
            "high": _safe_int(_get_value(row, "High", "고가")),
            "low": _safe_int(_get_value(row, "Low", "저가")),
            "close": _safe_int(_get_value(row, "Close", "종가")),
            "volume": _safe_int(_get_value(row, "Volume", "거래량")),
            "change": _safe_int(_get_value(row, "Change", "Changes", "대비")),
        })
    rows.sort(key=lambda r: r["date"])
    return rows


def write_ohlcv_csv(rows: Sequence[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["date", "symbol", "open", "high", "low", "close", "volume", "change"]
    with path.open("w", encoding="utf-8-sig", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def backfill_records(
    records: Sequence[dict[str, Any]],
    *,
    output_dir: Path,
    date_from: str,
    date_to: str,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    index_records: list[dict[str, Any]] = []
    for record in records:
        symbol = str(record.get("symbol") or "").strip()
        if not symbol:
            continue
        start = max(_normalize_date(date_from), record.get("listing_date") or _normalize_date(date_from))
        end = min(_normalize_date(date_to), record.get("delisting_date") or _normalize_date(date_to))
        df = fetch_delisted_ohlcv(symbol, start, end)
        rows = normalize_ohlcv_frame(symbol, df)
        if not rows:
            index_records.append({
                **record,
                "row_count": 0,
                "output_csv": "",
                "status": "empty",
            })
            continue
        csv_path = output_dir / f"{symbol}.csv"
        write_ohlcv_csv(rows, csv_path)
        index_records.append({
            **record,
            "row_count": len(rows),
            "output_csv": str(csv_path),
            "status": "written",
        })
    payload = {
        "config": {
            "date_from": _normalize_date(date_from),
            "date_to": _normalize_date(date_to),
            "output_dir": str(output_dir),
        },
        "totals": {
            "symbols": len(index_records),
            "written": sum(1 for r in index_records if r["status"] == "written"),
            "empty": sum(1 for r in index_records if r["status"] == "empty"),
        },
        "records": index_records,
    }
    (output_dir / "index.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Backfill KRX delisted OHLCV files from FinanceDataReader.",
    )
    parser.add_argument("--date-from", required=True, help="Delisting date lower bound")
    parser.add_argument("--date-to", required=True, help="Delisting date upper bound")
    parser.add_argument("--markets", default="KOSPI,KOSDAQ")
    parser.add_argument("--exclude-spac", action="store_true")
    parser.add_argument("--output-dir", required=True)
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    records = normalize_fdr_delistings(
        fetch_fdr_delisting_listing(),
        date_from=args.date_from,
        date_to=args.date_to,
        markets=_split_csv_arg(args.markets),
        secu_groups=("주권",),
        exclude_spac=args.exclude_spac,
    )
    payload = backfill_records(
        records,
        output_dir=Path(args.output_dir),
        date_from=args.date_from,
        date_to=args.date_to,
    )
    print(
        "[INFO] Delisted OHLCV backfill: "
        f"{payload['totals']['written']}/{payload['totals']['symbols']} written -> {args.output_dir}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
