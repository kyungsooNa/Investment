"""Build an as-of KRX universe snapshot including known delisted stocks."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any, Optional, Sequence

from scripts.audit_delisting_universe import (
    fetch_fdr_delisting_listing,
    normalize_fdr_delistings,
)
from services.point_in_time_universe_service import (
    PointInTimeUniverseRecord,
    build_point_in_time_snapshot,
    compare_current_to_point_in_time,
    normalize_current_listings,
    normalize_delisted_listings,
)


def fetch_current_listing():
    import FinanceDataReader as fdr

    return fdr.StockListing("KRX")


def _split_csv_arg(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in str(value).split(",") if part.strip())


def _records_to_dicts(records: Sequence[PointInTimeUniverseRecord]) -> list[dict[str, Any]]:
    return [record.to_dict() for record in records]


def build_snapshot_payload(
    *,
    as_of_date: str,
    markets: Sequence[str] = ("KOSPI", "KOSDAQ"),
    exclude_spac: bool = False,
) -> dict[str, Any]:
    current_records = normalize_current_listings(fetch_current_listing())
    delisted_rows = normalize_fdr_delistings(
        fetch_fdr_delisting_listing(),
        markets=markets,
        secu_groups=("주권",),
        exclude_spac=exclude_spac,
    )
    delisted_records = normalize_delisted_listings(delisted_rows)
    snapshot = build_point_in_time_snapshot(
        current_records + delisted_records,
        as_of_date,
        markets=markets,
        exclude_spac=exclude_spac,
    )
    return {
        "config": {
            "as_of_date": as_of_date,
            "markets": list(markets),
            "exclude_spac": exclude_spac,
        },
        "totals": {
            "current_records": len(current_records),
            "delisted_records": len(delisted_records),
            "snapshot_records": len(snapshot),
        },
        "survivorship_gap": compare_current_to_point_in_time(
            current_records,
            snapshot,
        ),
        "records": _records_to_dicts(snapshot),
    }


def write_csv(records: Sequence[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "symbol",
        "name",
        "market",
        "listing_date",
        "delisting_date",
        "source",
        "secu_group",
        "kind",
        "reason",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(records)


def format_markdown_report(payload: dict[str, Any]) -> str:
    cfg = payload["config"]
    totals = payload["totals"]
    gap = payload["survivorship_gap"]
    lines = [
        "# Point-in-Time Universe Snapshot",
        "",
        f"- as_of_date: `{cfg['as_of_date']}`",
        f"- markets: `{', '.join(cfg['markets'])}`",
        f"- exclude_spac: `{cfg['exclude_spac']}`",
        "",
        "## Summary",
        "",
        f"- current records: {totals['current_records']}",
        f"- delisted records: {totals['delisted_records']}",
        f"- snapshot records: {totals['snapshot_records']}",
        "",
        "## Survivorship Gap",
        "",
        f"- delisted-only in point-in-time snapshot: {gap['delisted_only_count']}",
        f"- current-only excluded from point-in-time snapshot: {gap['current_only_count']}",
        "",
        "## First Delisted-Only Records",
        "",
    ]
    for row in gap["delisted_only"][:20]:
        lines.append(
            f"- `{row['symbol']}` {row['name']} {row['market']} "
            f"{row['listing_date']}~{row['delisting_date']} {row['reason']}"
        )
    if not gap["delisted_only"]:
        lines.append("- None")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a point-in-time KRX universe snapshot.",
    )
    parser.add_argument("--as-of-date", required=True, help="YYYYMMDD or YYYY-MM-DD")
    parser.add_argument("--markets", default="KOSPI,KOSDAQ")
    parser.add_argument("--exclude-spac", action="store_true")
    parser.add_argument("--output-json")
    parser.add_argument("--output-csv")
    parser.add_argument("--output-markdown")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    payload = build_snapshot_payload(
        as_of_date=args.as_of_date,
        markets=_split_csv_arg(args.markets),
        exclude_spac=args.exclude_spac,
    )

    wrote = False
    if args.output_json:
        path = Path(args.output_json)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[INFO] JSON snapshot: {path}")
        wrote = True
    if args.output_csv:
        path = Path(args.output_csv)
        write_csv(payload["records"], path)
        print(f"[INFO] CSV snapshot: {path}")
        wrote = True
    if args.output_markdown:
        path = Path(args.output_markdown)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(format_markdown_report(payload), encoding="utf-8")
        print(f"[INFO] Markdown report: {path}")
        wrote = True
    if not wrote:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
