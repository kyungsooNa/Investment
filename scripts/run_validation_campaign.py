"""품질 통과 캡처만 사용해 활성 전략 검증 캠페인을 실행한다."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.run_backtest import ACTIVE_BACKTEST_STRATEGIES
from services.validation_campaign_service import (
    SubprocessStrategyRunner,
    ValidationCampaignService,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="유효 캡처 기반 활성 전략 파일럿 캠페인")
    parser.add_argument("--replay-dir", default="data/backtest_microstructure")
    parser.add_argument("--strategies", default=",".join(ACTIVE_BACKTEST_STRATEGIES))
    parser.add_argument("--paper", action="store_true")
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--output-file")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    strategies = tuple(item.strip() for item in args.strategies.split(",") if item.strip())
    unknown = sorted(set(strategies) - set(ACTIVE_BACKTEST_STRATEGIES))
    if unknown:
        raise ValueError(f"지원하지 않는 전략: {', '.join(unknown)}")
    service = ValidationCampaignService(
        replay_dir=args.replay_dir,
        strategies=strategies,
        strategy_runner=SubprocessStrategyRunner(
            python_executable=sys.executable,
            paper=args.paper,
        ),
    )
    result = service.build_plan() if args.plan_only else service.run()
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output_file:
        output_path = Path(args.output_file)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered + "\n", encoding="utf-8")
        print(f"[INFO] 결과 저장: {output_path}")
    else:
        print(rendered)
    if not args.plan_only and result.get("status") == "blocked":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
