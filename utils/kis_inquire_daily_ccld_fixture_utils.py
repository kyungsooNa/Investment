from __future__ import annotations

import argparse
import asyncio
import json
import logging
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from common.types import Exchange, OrderExecutionReport


DEFAULT_PAPER_TR_ID = "VTTC0081R"
DEFAULT_REAL_TR_ID = "TTTC0081R"
DEFAULT_DESCRIPTION = (
    "Sanitized KIS inquire-daily-ccld output1 rows for parser regression tests."
)
_ORDER_NO_FIELDS = {"odno", "orgn_odno", "ODNO", "ORGN_ODNO"}
_STOCK_CODE_FIELDS = {"pdno", "PDNO"}


def extract_inquire_daily_ccld_output1_rows(payload: Any) -> list[dict]:
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        if isinstance(payload.get("output1"), list):
            rows = payload["output1"]
        elif isinstance(payload.get("data"), list):
            rows = payload["data"]
        elif isinstance(payload.get("data"), dict) and isinstance(payload["data"].get("output1"), list):
            rows = payload["data"]["output1"]
        else:
            raise ValueError("Could not find output1 rows in inquire_daily_ccld payload.")
    else:
        raise ValueError("Unsupported payload type.")

    normalized_rows: list[dict] = []
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise ValueError(f"output1 row #{index} is not a dict.")
        normalized_rows.append(dict(row))
    if not normalized_rows:
        raise ValueError("output1 rows are empty.")
    return normalized_rows


def sanitize_inquire_daily_ccld_row(
    row: dict,
    *,
    row_index: int,
    mask_stock_code: bool = False,
) -> dict:
    sanitized = dict(row)
    for key in list(sanitized.keys()):
        if sanitized[key] in (None, ""):
            continue
        if key in _ORDER_NO_FIELDS:
            sanitized[key] = f"{row_index:010d}"
        elif key in _STOCK_CODE_FIELDS and mask_stock_code:
            sanitized[key] = f"{900000 + row_index:06d}"
    return sanitized


def build_inquire_daily_ccld_fixture_document(
    payload: Any,
    *,
    fixture_name: str,
    tr_id: str,
    description: str = DEFAULT_DESCRIPTION,
    sanitize: bool = True,
    mask_stock_code: bool = False,
) -> dict:
    rows = extract_inquire_daily_ccld_output1_rows(payload)
    cases: list[dict] = []
    case_counts: Counter[tuple[str, str]] = Counter()

    for row_index, row in enumerate(rows, start=1):
        fixture_row = (
            sanitize_inquire_daily_ccld_row(
                row,
                row_index=row_index,
                mask_stock_code=mask_stock_code,
            )
            if sanitize
            else dict(row)
        )
        report = OrderExecutionReport.from_order_query(fixture_row, tr_id=tr_id)
        side_name = report.side.value.lower() if report.side else "unknown"
        state_name = report.event_state.value.lower()
        case_key = (state_name, side_name)
        case_counts[case_key] += 1

        cases.append(
            {
                "case": f"{state_name}_{side_name}_{case_counts[case_key]}",
                "row": fixture_row,
                "expected": _build_expected_payload(report),
            }
        )

    return {
        "fixture_name": fixture_name,
        "description": description,
        "tr_id": tr_id,
        "rows": cases,
    }


def save_fixture_document(document: dict, output_path: str | Path) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def load_fixture_document(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def discover_inquire_daily_ccld_fixture_documents(
    fixture_dir: str | Path,
) -> Iterable[tuple[Path, dict]]:
    base_dir = Path(fixture_dir)
    for path in sorted(base_dir.glob("inquire_daily_ccld_output1_*.json")):
        yield path, load_fixture_document(path)


def _build_expected_payload(report: OrderExecutionReport) -> dict:
    return {
        "broker_order_no": report.broker_order_no,
        "stock_code": report.stock_code,
        "side": report.side.value if report.side else None,
        "event_state": report.event_state.value,
        "order_qty": report.order_qty,
        "fill_qty": report.fill_qty,
        "cumulative_filled_qty": report.cumulative_filled_qty,
        "remaining_qty": report.remaining_qty,
        "fill_price": report.fill_price,
        "event_time": report.event_time,
    }


def _default_tr_id(mode: str) -> str:
    return DEFAULT_PAPER_TR_ID if mode == "paper" else DEFAULT_REAL_TR_ID


async def _fetch_live_payload(args: argparse.Namespace) -> dict:
    from brokers.korea_investment.korea_invest_client import KoreaInvestApiClient
    from brokers.korea_investment.korea_invest_env import KoreaInvestApiEnv
    from config.config_loader import load_configs

    logger = logging.getLogger("kis_inquire_daily_ccld_fixture")
    configs = load_configs()
    env = KoreaInvestApiEnv(configs, logger=logger)
    env.set_trading_mode(args.mode == "paper")
    client = KoreaInvestApiClient(env, logger=logger)
    try:
        response = await client.inquire_daily_ccld(
            start_date=args.start_date,
            end_date=args.end_date,
            side_code=args.side_code,
            stock_code=args.stock_code,
            ccld_dvsn=args.ccld_dvsn,
            order_no=args.order_no,
            exchange=Exchange(args.exchange),
        )
        return response.to_dict() if hasattr(response, "to_dict") else response
    finally:
        await _close_client_sessions(client)


async def _close_client_sessions(client: Any) -> None:
    closed_sessions: set[int] = set()
    for attr_name in ("_quotations", "_account", "_trading"):
        api = getattr(client, attr_name, None)
        session = getattr(api, "_async_session", None)
        if session is None or id(session) in closed_sessions:
            continue
        await session.aclose()
        closed_sessions.add(id(session))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Convert KIS inquire-daily-ccld responses into parser regression fixtures."
    )
    parser.add_argument("--input", help="Path to a raw JSON response file.")
    parser.add_argument("--output", required=True, help="Path to write the generated fixture JSON.")
    parser.add_argument("--fixture-name", help="Fixture name. Defaults to the output file stem.")
    parser.add_argument("--description", default=DEFAULT_DESCRIPTION, help="Fixture description.")
    parser.add_argument("--mode", choices=("paper", "real"), default="paper", help="Trading mode for live fetch.")
    parser.add_argument("--tr-id", help="TR ID used when computing the expected parser source.")
    parser.add_argument("--mask-stock-code", action="store_true", help="Mask stock codes too.")
    parser.add_argument("--keep-raw", action="store_true", help="Keep original rows without sanitizing order numbers.")
    parser.add_argument("--start-date", help="Live fetch start date (YYYYMMDD).")
    parser.add_argument("--end-date", help="Live fetch end date (YYYYMMDD).")
    parser.add_argument("--side-code", default="00", help="Side code.")
    parser.add_argument("--stock-code", default="", help="Stock code filter.")
    parser.add_argument("--ccld-dvsn", default="00", help="Conclusion division code.")
    parser.add_argument("--order-no", default="", help="Order number filter.")
    parser.add_argument("--exchange", choices=("KRX", "NXT"), default="KRX", help="Exchange code.")
    return parser


async def _async_main(args: argparse.Namespace) -> int:
    raw_payload = (
        load_fixture_document(args.input)
        if args.input
        else await _fetch_live_payload(args)
    )
    tr_id = args.tr_id or _default_tr_id(args.mode)
    fixture_name = args.fixture_name or Path(args.output).stem
    document = build_inquire_daily_ccld_fixture_document(
        raw_payload,
        fixture_name=fixture_name,
        tr_id=tr_id,
        description=args.description,
        sanitize=not args.keep_raw,
        mask_stock_code=args.mask_stock_code,
    )
    save_fixture_document(document, args.output)
    print(f"fixture saved: {args.output}")
    print(f"rows: {len(document['rows'])}, tr_id: {tr_id}")
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = _build_parser()
    args = parser.parse_args()
    return asyncio.run(_async_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
