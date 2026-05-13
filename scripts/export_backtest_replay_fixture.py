"""Export a historical replay fixture JSON file from the local stocks DB."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from services.backtest_replay_fixture_exporter import BacktestReplayFixtureExporter


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export a deterministic historical replay fixture JSON.",
    )
    parser.add_argument("--db-path", default="data/stocks.db")
    parser.add_argument("--date", required=True)
    parser.add_argument("--codes", help="Comma-separated stock codes. Defaults to selector sample codes.")
    parser.add_argument("--sample-codes", type=int, default=5)
    parser.add_argument("--min-trading-value", type=int, default=10_000_000_000)
    parser.add_argument("--min-ohlcv-days", type=int, default=60)
    parser.add_argument("--ohlcv-lookback-days", type=int, default=60)
    parser.add_argument("--output-file", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    codes = _parse_codes(args.codes)
    exporter = BacktestReplayFixtureExporter(
        args.db_path,
        min_trading_value=args.min_trading_value,
        min_ohlcv_days=args.min_ohlcv_days,
        sample_code_count=args.sample_codes,
    )
    payload = exporter.export_fixture(
        trade_date=args.date,
        codes=codes,
        ohlcv_lookback_days=args.ohlcv_lookback_days,
    )

    output_path = Path(args.output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Exported replay fixture: {output_path}")
    return 0


def _parse_codes(raw: str | None) -> list[str] | None:
    if raw is None:
        return None
    return [code.strip() for code in raw.split(",") if code.strip()]


if __name__ == "__main__":
    raise SystemExit(main())
