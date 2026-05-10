"""CLI: 전략 디버깅 백테스트 ("왜 안 샀을까?") 실행 도구.

Usage:
    python -m scripts.run_strategy_debug --codes 005930,000660,035720
    python -m scripts.run_strategy_debug --codes 005930 --output json --output-file out.json

지원 전략: oneil_pocket_pivot (현재)
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="전략 디버깅 백테스트 — 종목별 필터 탈락 단계 리포트",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
예시:
  python -m scripts.run_strategy_debug --codes 005930,000660
  python -m scripts.run_strategy_debug --codes 005930 --output json
  python -m scripts.run_strategy_debug --codes 005930,000660 --portfolio-cash 10000000 --max-positions 3
  python -m scripts.run_strategy_debug                          # universe 전체 스캔
""",
    )
    parser.add_argument(
        "--strategy",
        default="oneil_pocket_pivot",
        choices=["oneil_pocket_pivot"],
        help="분석할 전략 (default: oneil_pocket_pivot)",
    )
    parser.add_argument(
        "--codes",
        default=None,
        help="분석할 종목 코드 목록 (쉼표 구분). 미입력 시 universe 전체",
    )
    parser.add_argument(
        "--output",
        default="console",
        choices=["console", "json"],
        help="출력 형식 (default: console)",
    )
    parser.add_argument(
        "--output-file",
        default=None,
        dest="output_file",
        help="결과를 저장할 파일 경로 (미입력 시 stdout)",
    )
    parser.add_argument(
        "--paper",
        action="store_true",
        default=True,
        help="모의투자 모드 사용 (default: True)",
    )
    parser.add_argument(
        "--portfolio-cash",
        type=float,
        default=None,
        dest="portfolio_cash",
        help="백테스트 포트폴리오 dry-run 초기 현금. 지정 시 신호를 현금/예약 장부로 검증",
    )
    parser.add_argument(
        "--max-positions",
        type=int,
        default=None,
        dest="max_positions",
        help="전략별 최대 보유 종목 수 dry-run 제한. --portfolio-cash와 함께 사용",
    )
    return parser.parse_args()


async def _run(args: argparse.Namespace) -> None:
    from scripts._bootstrap import bootstrap_pp_strategy, make_stdout_logger
    from repositories.backtest_journal_repository import BacktestJournalRepository
    from services.backtest_execution_simulator import BacktestPortfolioLedger
    from strategies.oneil_pocket_pivot_strategy import OneilPocketPivotStrategy
    from strategies.debug.strategy_debug_runner import StrategyDebugRunner
    from strategies.debug.rejection_report import format_console, format_json

    # 부트스트랩 로그는 WARNING 이상만 — 초기화 노이즈 억제
    bootstrap_logger = make_stdout_logger("debug_bootstrap", level=logging.WARNING)

    # 전략 전용 디버그 로거: file handler 없이 생성 → 실거래 로그 파일 영향 없음
    debug_logger = logging.getLogger("strategy_debug.OneilPocketPivot")
    debug_logger.setLevel(logging.DEBUG)
    debug_logger.propagate = False

    print(f"[INFO] 서비스 초기화 중... (모의투자={args.paper})")
    try:
        sqs, universe_service, market_clock = await bootstrap_pp_strategy(
            is_paper_trading=args.paper,
            logger=bootstrap_logger,
        )
    except RuntimeError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        sys.exit(1)

    candidate_codes = None
    if args.codes:
        candidate_codes = [c.strip() for c in args.codes.split(",") if c.strip()]

    with tempfile.TemporaryDirectory(prefix="strategy_debug_") as tmp_dir:
        strategy = OneilPocketPivotStrategy(
            stock_query_service=sqs,
            universe_service=universe_service,
            market_clock=market_clock,
            logger=debug_logger,
            state_file=os.path.join(tmp_dir, "pp_position_state.json"),
        )

        target_date = market_clock.get_current_kst_time().strftime("%Y%m%d")
        portfolio_ledger = (
            BacktestPortfolioLedger(initial_cash=args.portfolio_cash)
            if args.portfolio_cash is not None
            else None
        )
        max_positions = (
            {strategy.name: args.max_positions}
            if args.max_positions is not None
            else None
        )
        runner = StrategyDebugRunner(
            strategy,
            debug_logger,
            backtest_journal_repository=BacktestJournalRepository(),
            target_date=target_date,
            backtest_portfolio_ledger=portfolio_ledger,
            max_positions_per_strategy=max_positions,
        )
        print("[INFO] 전략 스캔 실행 중...\n")
        report = await runner.run(candidate_codes)

    result = format_json(report) if args.output == "json" else format_console(report)

    if args.output_file:
        with open(args.output_file, "w", encoding="utf-8") as f:
            f.write(result)
        print(f"[INFO] 결과 저장: {args.output_file}")
    else:
        print(result)


def main() -> None:
    args = _parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
