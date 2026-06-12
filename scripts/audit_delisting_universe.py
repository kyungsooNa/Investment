"""Audit KRX delisting universe inputs for survivorship-bias work.

This script is intentionally one step before backtest integration. It builds a
filtered delisting snapshot from FinanceDataReader and can cross-check recent
rows against KIND's official delisting status page.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

import pandas as pd


_DEFAULT_MARKETS = ("KOSPI", "KOSDAQ")
_DEFAULT_SECU_GROUPS = ("주권",)
_KIND_URL = "https://kind.krx.co.kr/investwarn/delcompany.do"
_KIND_MARKET_TYPES = {"KOSPI": "1", "KOSDAQ": "2", "KONEX": "6"}


def _normalize_date(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    ts = pd.to_datetime(value, errors="coerce")
    if pd.isna(ts):
        text = str(value).strip()
        digits = re.sub(r"\D", "", text)
        if len(digits) == 8:
            return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
        return text
    return ts.strftime("%Y-%m-%d")


def _compact_date(value: Optional[str]) -> str:
    if not value:
        return ""
    digits = re.sub(r"\D", "", str(value))
    return digits[:8] if len(digits) >= 8 else digits


def _as_text(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    return str(value).strip()


def _is_spac(record: Dict[str, Any]) -> bool:
    name = record.get("name", "")
    reason = record.get("reason", "")
    return "스팩" in name or "스팩" in reason


def fetch_fdr_delisting_listing() -> pd.DataFrame:
    import FinanceDataReader as fdr

    return fdr.StockListing("KRX-DELISTING")


def normalize_fdr_delistings(
    df: pd.DataFrame,
    *,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    markets: Sequence[str] = _DEFAULT_MARKETS,
    secu_groups: Sequence[str] = _DEFAULT_SECU_GROUPS,
    exclude_spac: bool = False,
) -> List[Dict[str, Any]]:
    start = _compact_date(date_from)
    end = _compact_date(date_to)
    market_set = set(markets or [])
    secu_group_set = set(secu_groups or [])

    records: List[Dict[str, Any]] = []
    for _, row in df.iterrows():
        delisting_date = _normalize_date(row.get("DelistingDate"))
        delisting_compact = _compact_date(delisting_date)
        if start and delisting_compact < start:
            continue
        if end and delisting_compact > end:
            continue

        record = {
            "symbol": _as_text(row.get("Symbol")),
            "name": _as_text(row.get("Name")),
            "market": _as_text(row.get("Market")),
            "secu_group": _as_text(row.get("SecuGroup")),
            "kind": _as_text(row.get("Kind")),
            "listing_date": _normalize_date(row.get("ListingDate")),
            "delisting_date": delisting_date,
            "reason": _as_text(row.get("Reason")),
            "to_symbol": _as_text(row.get("ToSymbol")),
            "to_name": _as_text(row.get("ToName")),
        }
        if market_set and record["market"] not in market_set:
            continue
        if secu_group_set and record["secu_group"] not in secu_group_set:
            continue
        if exclude_spac and _is_spac(record):
            continue
        records.append(record)

    records.sort(key=lambda r: (r["delisting_date"], r["symbol"]), reverse=True)
    return records


def parse_kind_delisting_rows(html: str) -> List[Dict[str, str]]:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    rows: List[Dict[str, str]] = []
    for tr in soup.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in tr.find_all("td")]
        if len(cells) < 4:
            continue
        if not cells[2] or not re.match(r"\d{4}[-./]?\d{2}[-./]?\d{2}", cells[2]):
            continue
        rows.append({
            "name": cells[1],
            "delisting_date": _normalize_date(cells[2]),
            "reason": cells[3],
            "note": cells[4] if len(cells) > 4 else "",
        })
    return rows


def fetch_kind_delisting_rows(
    date_from: str,
    date_to: str,
    market_type: str = "",
) -> List[Dict[str, str]]:
    import requests

    market_types = _split_csv_arg(market_type) or ("",)
    rows_by_key: Dict[tuple[str, str], Dict[str, str]] = {}
    for mt in market_types:
        payload = {
            "method": "searchDelCompanySub",
            "currentPageSize": "3000",
            "pageIndex": "1",
            "orderMode": "2",
            "orderStat": "D",
            "tabType": "1",
            "searchMode": "",
            "searchCodeType": "",
            "searchCorpName": "",
            "repIsuSrtCd": "",
            "forward": "delcompany_sub",
            "searchType": "",
            "marketType": mt,
            "searchCorpNameTmp": "",
            "fromDate": _compact_date(date_from),
            "toDate": _compact_date(date_to),
        }
        resp = requests.post(
            _KIND_URL,
            data=payload,
            timeout=30,
            headers={"Referer": f"{_KIND_URL}?method=searchDelCompanyMain"},
        )
        resp.raise_for_status()
        for row in parse_kind_delisting_rows(resp.text):
            rows_by_key[_kind_key(row)] = row
    return list(rows_by_key.values())


def _kind_key(row: Dict[str, Any]) -> tuple[str, str]:
    return (_as_text(row.get("name")), _normalize_date(row.get("delisting_date")))


def compare_with_kind(
    fdr_records: List[Dict[str, Any]],
    kind_rows: List[Dict[str, str]],
) -> Dict[str, Any]:
    kind_by_key = {_kind_key(row): row for row in kind_rows}
    fdr_by_key = {_kind_key(row): row for row in fdr_records}

    matched = []
    fdr_only = []
    for key, record in fdr_by_key.items():
        if key in kind_by_key:
            matched.append({"fdr": record, "kind": kind_by_key[key]})
        else:
            fdr_only.append(record)

    kind_only = []
    for key, row in kind_by_key.items():
        if key not in fdr_by_key:
            kind_only.append(row)

    fdr_only.sort(key=lambda r: (r["delisting_date"], r["symbol"]), reverse=True)
    kind_only.sort(key=lambda r: (r["delisting_date"], r["name"]), reverse=True)
    return {
        "matched_count": len(matched),
        "fdr_only_count": len(fdr_only),
        "kind_only_count": len(kind_only),
        "fdr_only": fdr_only,
        "kind_only": kind_only,
    }


def _count(records: Iterable[Dict[str, Any]], key: str) -> Dict[str, int]:
    return dict(Counter(_as_text(r.get(key)) for r in records))


def build_report(
    fdr_records: List[Dict[str, Any]],
    *,
    date_from: str,
    date_to: str,
    kind_rows: Optional[List[Dict[str, str]]] = None,
    markets: Sequence[str] = _DEFAULT_MARKETS,
    secu_groups: Sequence[str] = _DEFAULT_SECU_GROUPS,
    exclude_spac: bool = False,
) -> Dict[str, Any]:
    report = {
        "config": {
            "date_from": _normalize_date(date_from),
            "date_to": _normalize_date(date_to),
            "markets": list(markets),
            "secu_groups": list(secu_groups),
            "exclude_spac": exclude_spac,
        },
        "totals": {
            "fdr_filtered": len(fdr_records),
            "kind_rows": len(kind_rows or []),
        },
        "market_counts": _count(fdr_records, "market"),
        "secu_group_counts": _count(fdr_records, "secu_group"),
        "reason_counts": dict(Counter(r.get("reason", "") for r in fdr_records).most_common(20)),
        "records": fdr_records,
    }
    if kind_rows is not None:
        report["kind_comparison"] = compare_with_kind(fdr_records, kind_rows)
    return report


def write_csv(records: List[Dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "symbol", "name", "market", "secu_group", "kind",
        "listing_date", "delisting_date", "reason", "to_symbol", "to_name",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(records)


def format_markdown_report(report: Dict[str, Any]) -> str:
    cfg = report["config"]
    totals = report["totals"]
    lines = [
        "# Delisting Universe Audit",
        "",
        f"- date: `{cfg['date_from']} ~ {cfg['date_to']}`",
        f"- markets: `{', '.join(cfg['markets'])}`",
        f"- secu_groups: `{', '.join(cfg['secu_groups'])}`",
        f"- exclude_spac: `{cfg['exclude_spac']}`",
        "",
        "## Summary",
        "",
        f"- FDR filtered rows: {totals['fdr_filtered']}",
        f"- KIND rows: {totals['kind_rows']}",
        "",
        "## Market Counts",
        "",
    ]
    for market, count in report["market_counts"].items():
        lines.append(f"- {market}: {count}")
    lines.extend(["", "## KIND Comparison", ""])
    comparison = report.get("kind_comparison")
    if comparison:
        lines.append(f"- matched: {comparison['matched_count']}")
        lines.append(f"- FDR only: {comparison['fdr_only_count']}")
        lines.append(f"- KIND only: {comparison['kind_only_count']}")
    else:
        lines.append("- KIND check not requested.")
    lines.extend(["", "## First Records", ""])
    for row in report["records"][:20]:
        lines.append(
            f"- `{row['symbol']}` {row['name']} {row['market']} "
            f"{row['delisting_date']} {row['reason']}"
        )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Audit FDR KRX-DELISTING rows and optionally cross-check KIND.",
    )
    parser.add_argument("--date-from", required=True, help="YYYYMMDD or YYYY-MM-DD")
    parser.add_argument("--date-to", required=True, help="YYYYMMDD or YYYY-MM-DD")
    parser.add_argument("--markets", default="KOSPI,KOSDAQ")
    parser.add_argument("--secu-groups", default="주권")
    parser.add_argument("--exclude-spac", action="store_true")
    parser.add_argument("--kind-check", action="store_true")
    parser.add_argument("--kind-market-type", default="")
    parser.add_argument("--output-json")
    parser.add_argument("--output-csv")
    parser.add_argument("--output-markdown")
    return parser


def _split_csv_arg(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in value.split(",") if part.strip())


def kind_market_type_arg(markets: Sequence[str], explicit: str = "") -> str:
    if explicit.strip():
        return explicit
    return ",".join(_KIND_MARKET_TYPES[m] for m in markets if m in _KIND_MARKET_TYPES)


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    markets = _split_csv_arg(args.markets)
    secu_groups = _split_csv_arg(args.secu_groups)

    fdr_df = fetch_fdr_delisting_listing()
    fdr_records = normalize_fdr_delistings(
        fdr_df,
        date_from=args.date_from,
        date_to=args.date_to,
        markets=markets,
        secu_groups=secu_groups,
        exclude_spac=args.exclude_spac,
    )
    kind_rows = (
        fetch_kind_delisting_rows(
            args.date_from,
            args.date_to,
            kind_market_type_arg(markets, args.kind_market_type),
        )
        if args.kind_check
        else None
    )
    report = build_report(
        fdr_records,
        date_from=args.date_from,
        date_to=args.date_to,
        kind_rows=kind_rows,
        markets=markets,
        secu_groups=secu_groups,
        exclude_spac=args.exclude_spac,
    )

    wrote = False
    if args.output_json:
        path = Path(args.output_json)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[INFO] JSON report: {path}")
        wrote = True
    if args.output_csv:
        path = Path(args.output_csv)
        write_csv(fdr_records, path)
        print(f"[INFO] CSV snapshot: {path}")
        wrote = True
    if args.output_markdown:
        path = Path(args.output_markdown)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(format_markdown_report(report), encoding="utf-8")
        print(f"[INFO] Markdown report: {path}")
        wrote = True
    if not wrote:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
