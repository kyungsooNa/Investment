"""CLI: active live-strategy period backtest runner.

Usage:
    python -m scripts.run_backtest --strategy oneil_pocket_pivot --dates 20260501,20260502
    python -m scripts.run_backtest --start-date 20260501 --end-date 20260510 --initial-cash 10000000
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import tempfile
from datetime import datetime, timedelta
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="기간 백테스트 — 활성 LiveStrategy contract를 replay 데이터로 실행",
    )
    parser.add_argument(
        "--strategy",
        default="oneil_pocket_pivot",
        choices=["oneil_pocket_pivot"],
        help="실행할 전략 (default: oneil_pocket_pivot)",
    )
    parser.add_argument("--dates", default=None, help="실행 날짜 목록 (YYYYMMDD, 쉼표 구분)")
    parser.add_argument("--start-date", default=None, dest="start_date", help="시작일 YYYYMMDD")
    parser.add_argument("--end-date", default=None, dest="end_date", help="종료일 YYYYMMDD")
    parser.add_argument("--initial-cash", type=float, default=10_000_000, dest="initial_cash")
    parser.add_argument("--max-positions", type=int, default=None, dest="max_positions")
    parser.add_argument("--output", default="console", choices=["console", "json"])
    parser.add_argument("--output-file", default=None, dest="output_file")
    parser.add_argument(
        "--paper",
        action="store_true",
        default=False,
        help="모의투자 모드로 서비스 그래프 초기화. 과거 분봉/프로그램매매 API는 실전 전용이라 기본은 실전 데이터 모드",
    )
    return parser.parse_args()


def _build_dates(args: argparse.Namespace) -> list[str]:
    if args.dates:
        return [date.strip() for date in str(args.dates).split(",") if date.strip()]
    if not args.start_date or not args.end_date:
        raise ValueError("--dates 또는 --start-date/--end-date를 지정해야 합니다.")

    start = datetime.strptime(args.start_date, "%Y%m%d").date()
    end = datetime.strptime(args.end_date, "%Y%m%d").date()
    if end < start:
        raise ValueError("--end-date는 --start-date보다 빠를 수 없습니다.")

    dates: list[str] = []
    current = start
    while current <= end:
        dates.append(current.strftime("%Y%m%d"))
        current += timedelta(days=1)
    return dates


def _get_program_provider(stock_query_service: Any) -> Any | None:
    market_data_service = getattr(stock_query_service, "market_data_service", None)
    return getattr(market_data_service, "_broker_api_wrapper", None)


def _format_console(result) -> str:
    buy_count = sum(1 for report in result.execution_reports if report.order.side.value == "BUY")
    sell_count = sum(1 for report in result.execution_reports if report.order.side.value == "SELL")
    rejected_count = len(result.journal_records)
    portfolio = result.portfolio or {}
    positions = portfolio.get("positions") or {}

    return "\n".join([
        "[BACKTEST RESULT]",
        f"전략: {result.strategy_name}",
        f"기간: {result.dates[0]} ~ {result.dates[-1]} ({len(result.dates)}일)",
        f"BUY 체결: {buy_count}",
        f"SELL 체결: {sell_count}",
        f"거부 기록: {rejected_count}",
        f"보유 종목: {len(positions)}",
        f"현금: {portfolio.get('cash', 0):,.0f}",
        f"가용현금: {portfolio.get('available_cash', 0):,.0f}",
        f"실현손익(순): {portfolio.get('realized_net_pnl', 0):,.0f}",
    ])


def _format_json(result) -> str:
    payload = {
        "strategy_name": result.strategy_name,
        "dates": result.dates,
        "execution_reports": [
            {
                "order_id": report.order.order_id,
                "code": report.order.code,
                "side": report.order.side.value,
                "qty": report.order.qty,
                "filled_qty": report.filled_qty,
                "fill_price": report.fill_price,
                "status": report.status.value,
                "reason": report.reason,
            }
            for report in result.execution_reports
        ],
        "journal_records": result.journal_records,
        "portfolio": result.portfolio,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


async def _run(args: argparse.Namespace) -> None:
    from scripts._bootstrap import bootstrap_pp_strategy, make_stdout_logger
    from services.backtest_execution_simulator import BacktestPortfolioLedger
    from services.backtest_period_runner import BacktestPeriodRunner, BacktestPeriodRunnerConfig
    from services.backtest_replay_adapter import (
        StockQueryBacktestReplayService,
        StockQueryIntradayReplayBarProvider,
    )
    from strategies.oneil_pocket_pivot_strategy import OneilPocketPivotStrategy

    dates = _build_dates(args)
    bootstrap_logger = make_stdout_logger("backtest_bootstrap", level=logging.WARNING)
    print(f"[INFO] 서비스 초기화 중... (모의투자={args.paper})")
    try:
        sqs, universe_service, market_clock = await bootstrap_pp_strategy(
            is_paper_trading=args.paper,
            logger=bootstrap_logger,
        )
    except RuntimeError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        sys.exit(1)

    replay_sqs = StockQueryBacktestReplayService(
        sqs,
        program_provider=_get_program_provider(sqs),
    )
    bar_provider = StockQueryIntradayReplayBarProvider(replay_sqs)
    ledger = BacktestPortfolioLedger(initial_cash=args.initial_cash)

    max_positions = None
    with tempfile.TemporaryDirectory(prefix="period_backtest_") as tmp_dir:
        strategy = OneilPocketPivotStrategy(
            stock_query_service=replay_sqs,
            universe_service=universe_service,
            market_clock=market_clock,
            logger=logging.getLogger("backtest.OneilPocketPivot"),
            state_file=os.path.join(tmp_dir, "pp_position_state.json"),
        )
        if args.max_positions is not None:
            max_positions = {strategy.name: args.max_positions}

        runner = BacktestPeriodRunner(
            strategy=strategy,
            bar_provider=bar_provider,
            ledger=ledger,
            config=BacktestPeriodRunnerConfig(max_positions_per_strategy=max_positions),
        )
        print("[INFO] 기간 백테스트 실행 중...\n")
        result = await runner.run(dates)

    rendered = _format_json(result) if args.output == "json" else _format_console(result)
    if args.output_file:
        with open(args.output_file, "w", encoding="utf-8") as fp:
            fp.write(rendered)
        print(f"[INFO] 결과 저장: {args.output_file}")
    else:
        print(rendered)


def main() -> None:
    args = _parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
