"""CLI: backfill point-in-time program trade series into existing replay captures.

#1068 이전 캡처는 `program_trades_intraday` 가 없어 품질 게이트에서 invalid 로
떨어진다. `pt_history` 원본이 아직 보존된 날짜(RETENTION_DAYS=7)에 한해 같은
조회 규칙으로 시계열을 채우고 품질 게이트를 다시 계산한다. 기본은 dry-run.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from services.backtest_microstructure_capture import BacktestMicrostructureCaptureService
from services.backtest_microstructure_quality import summarize_capture_quality

_GATE_SUMMARY_KEYS = (
    "intraday_coverage_pct",
    "program_overlay_coverage_pct",
    "program_db_coverage_pct",
    "program_intraday_db_coverage_pct",
    "execution_strength_db_coverage_pct",
    "orderbook_db_coverage_pct",
    "orderbook_sparse_codes",
    "orderbook_min_rows_per_code",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Backfill program_trades_intraday into replay captures from pt_history.",
    )
    parser.add_argument("--date", action="append", required=True, help="대상 거래일 YYYYMMDD (반복 가능)")
    parser.add_argument("--microstructure-dir", default="data/backtest_microstructure")
    parser.add_argument("--program-db", default="data/program_subscribe/program_trading.db")
    parser.add_argument("--apply", action="store_true", default=False, help="파일에 기록 (기본 dry-run)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = [
        backfill_date(
            date_ymd,
            microstructure_dir=Path(args.microstructure_dir),
            program_db_path=Path(args.program_db),
            apply=args.apply,
        )
        for date_ymd in args.date
    ]
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def backfill_date(
    date_ymd: str,
    *,
    microstructure_dir: Path,
    program_db_path: Path,
    apply: bool,
) -> dict[str, Any]:
    capture_path = Path(microstructure_dir) / f"replay_microstructure_{date_ymd}.json"
    if not capture_path.exists():
        return {"date": date_ymd, "status": "missing_capture"}
    payload = json.loads(capture_path.read_text(encoding="utf-8"))
    metadata = payload.get("metadata") or {}
    if metadata.get("program_source") != "program_db":
        return {"date": date_ymd, "status": "skipped_not_program_db"}
    existing = payload.get("program_trades_intraday") or {}
    if any(existing.values()):
        return {"date": date_ymd, "status": "skipped_existing"}

    service = BacktestMicrostructureCaptureService(
        stock_query_service=None, program_db_path=program_db_path
    )
    payload["program_trades_intraday"] = service._capture_program_trades_intraday(
        [str(code) for code in metadata.get("codes") or []],
        date_ymd=date_ymd,
        source="program_db",
        start_hhmmss=str(metadata.get("start_hhmmss") or "090000"),
        end_hhmmss=str(metadata.get("end_hhmmss") or "153000"),
    )
    metadata.setdefault("row_counts", {})["program_trades_intraday_rows"] = sum(
        len(rows) for rows in payload["program_trades_intraday"].values()
    )
    summary = summarize_capture_quality(payload)
    gate = metadata.setdefault("quality_gate", {})
    gate.update({
        "valid_for_backtest": summary["quality_gate_passed"],
        "passed": summary["quality_gate_passed"],
        "issues": list(summary["issues"]),
        "warnings": list(summary["warnings"]),
    })
    gate.update({key: summary[key] for key in _GATE_SUMMARY_KEYS})
    metadata["program_trades_intraday_backfill"] = {
        "source_db": str(program_db_path),
        "backfilled_at": datetime.now().isoformat(timespec="seconds"),
    }
    payload["metadata"] = metadata

    result = {
        "date": date_ymd,
        "status": "applied" if apply else "dry_run",
        "codes": summary["codes"],
        "program_intraday_db_available": summary["program_intraday_db_available"],
        "program_intraday_db_coverage_pct": summary["program_intraday_db_coverage_pct"],
        "quality_gate_passed": summary["quality_gate_passed"],
        "issues": list(summary["issues"]),
    }
    if apply:
        BacktestMicrostructureCaptureService.write_overlay_files(payload, Path(microstructure_dir))
    return result


if __name__ == "__main__":
    raise SystemExit(main())
