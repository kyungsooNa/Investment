"""Select candidate dates for real historical replay fixtures."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from services.backtest_replay_fixture_selector import BacktestReplayFixtureSelector


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Select good sample dates for historical replay fixture expansion.",
    )
    parser.add_argument("--db-path", default="data/stocks.db")
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--min-trading-value", type=int, default=10_000_000_000)
    parser.add_argument("--min-ohlcv-days", type=int, default=60)
    parser.add_argument("--sample-codes", type=int, default=5)
    parser.add_argument("--output", choices=("console", "json"), default="console")
    parser.add_argument("--output-file")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    selector = BacktestReplayFixtureSelector(
        args.db_path,
        min_trading_value=args.min_trading_value,
        min_ohlcv_days=args.min_ohlcv_days,
        sample_code_count=args.sample_codes,
    )
    candidates = selector.select_sample_dates(
        start_date=args.start_date,
        end_date=args.end_date,
        limit=args.limit,
    )
    payload = [candidate.to_dict() for candidate in candidates]

    if args.output == "json":
        text = json.dumps(payload, ensure_ascii=False, indent=2)
    else:
        text = _format_console(payload)

    if args.output_file:
        Path(args.output_file).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


def _format_console(rows: list[dict]) -> str:
    if not rows:
        return "No replay fixture sample candidates found."
    lines = ["[REPLAY FIXTURE SAMPLE CANDIDATES]"]
    for row in rows:
        lines.append(
            "{date} | daily={daily} liquid={liquid} replay_ready={ready} "
            "ready_ratio={ready_ratio:.1%} rs={rs} sample_codes={codes}".format(
                date=row["trade_date"],
                daily=row["daily_rows"],
                liquid=row["liquid_rows"],
                ready=row["replay_ready_rows"],
                ready_ratio=row["replay_ready_ratio"],
                rs=row["rs_rows"],
                codes=",".join(row["sample_codes"]),
            )
        )
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
