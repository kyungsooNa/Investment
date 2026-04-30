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

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="전략 디버깅 백테스트 — 종목별 필터 탈락 단계 리포트",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
예시:
  python -m scripts.run_strategy_debug --codes 005930,000660
  python -m scripts.run_strategy_debug --codes 005930 --output json
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
    return parser.parse_args()


async def _run(args: argparse.Namespace) -> None:
    from scripts._bootstrap import bootstrap_pp_strategy, make_stdout_logger
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

    strategy = OneilPocketPivotStrategy(
        stock_query_service=sqs,
        universe_service=universe_service,
        market_clock=market_clock,
        logger=debug_logger,
    )

    candidate_codes = None
    if args.codes:
        candidate_codes = [c.strip() for c in args.codes.split(",") if c.strip()]

    runner = StrategyDebugRunner(strategy, debug_logger)
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
