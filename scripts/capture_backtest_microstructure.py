"""CLI: capture microstructure overlay data for replay fixtures."""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from scripts._bootstrap import bootstrap_pp_strategy, make_stdout_logger
from services.backtest_microstructure_capture import BacktestMicrostructureCaptureService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Capture intraday, execution strength, and program overlays for replay fixtures.",
    )
    parser.add_argument("--date", required=True, help="대상 거래일 YYYYMMDD")
    parser.add_argument("--codes", required=True, help="Comma-separated stock codes")
    parser.add_argument("--start-hhmmss", default="090000")
    parser.add_argument("--end-hhmmss", default="153000")
    parser.add_argument("--session", choices=("REGULAR", "EXTENDED"), default="REGULAR")
    parser.add_argument(
        "--program-source",
        choices=("daily_rest", "program_db", "none"),
        default="daily_rest",
        help="프로그램매매 overlay 출처: daily_rest=과거 일별 REST, program_db=장중 WebSocket 저장 DB",
    )
    parser.add_argument(
        "--program-db-path",
        default="data/program_subscribe/program_trading.db",
    )
    parser.add_argument(
        "--execution-strength-source",
        choices=("rest_scalar", "es_db"),
        default="rest_scalar",
        help="체결강도 출처: rest_scalar=EOD REST 스칼라, es_db=장중 WS 샘플링 DB(미스 종목은 스칼라 폴백)",
    )
    parser.add_argument(
        "--execution-strength-db-path",
        default="data/execution_strength/execution_strength.db",
    )
    parser.add_argument("--output-dir", default="data/backtest_microstructure")
    parser.add_argument("--paper", action="store_true", default=False)
    parser.add_argument("--no-intraday", action="store_true", default=False)
    parser.add_argument("--no-execution-strength", action="store_true", default=False)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return asyncio.run(_run(args))


async def _run(args: argparse.Namespace) -> int:
    logger = make_stdout_logger("backtest_microstructure_capture", level=logging.INFO)
    sqs, _, _ = await bootstrap_pp_strategy(
        is_paper_trading=args.paper,
        logger=logger,
    )
    service = BacktestMicrostructureCaptureService(
        stock_query_service=sqs,
        program_provider=_get_program_provider(sqs),
        program_db_path=args.program_db_path,
        execution_strength_db_path=args.execution_strength_db_path,
    )
    payload = await service.capture(
        codes=_parse_codes(args.codes),
        date_ymd=args.date,
        start_hhmmss=args.start_hhmmss,
        end_hhmmss=args.end_hhmmss,
        session=args.session,
        include_intraday=not args.no_intraday,
        include_execution_strength=not args.no_execution_strength,
        program_source=args.program_source,
        execution_strength_source=args.execution_strength_source,
    )
    paths = _write_output_files(payload, Path(args.output_dir))
    for label, path in paths.items():
        print(f"[INFO] {label}: {path}")
    return 0


def _parse_codes(raw: str) -> list[str]:
    return [code.strip() for code in raw.split(",") if code.strip()]


def _get_program_provider(stock_query_service: Any) -> Any | None:
    market_data_service = getattr(stock_query_service, "market_data_service", None)
    return getattr(market_data_service, "_broker_api_wrapper", None)


def _write_output_files(payload: dict[str, Any], output_dir: Path) -> dict[str, Path]:
    return BacktestMicrostructureCaptureService.write_overlay_files(payload, output_dir)


if __name__ == "__main__":
    raise SystemExit(main())

