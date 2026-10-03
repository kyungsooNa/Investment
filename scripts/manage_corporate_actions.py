"""확정된 해외주식 기업행사를 원장에 기록·반영하는 CLI."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.corporate_action_service import CorporateActionService
from services.corporate_action_workflow_service import CorporateActionWorkflowService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="해외주식 분할·병합·현금배당 원장 관리")
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--db-path")
    subparsers = parser.add_subparsers(dest="command", required=True)

    split = subparsers.add_parser("split", help="분할 또는 병합 반영")
    split.add_argument("symbol")
    split.add_argument("effective_date", help="YYYY-MM-DD")
    split.add_argument("--numerator", type=int, required=True, help="신주 수")
    split.add_argument("--denominator", type=int, default=1, help="구주 수")
    split.add_argument("--external-id", default="")
    split.add_argument("--no-state-sync", action="store_true")
    split.add_argument("--apply", action="store_true")
    split.add_argument("--approval-token", default="")

    dividend = subparsers.add_parser("dividend", help="현금배당 권리수량 기록")
    dividend.add_argument("symbol")
    dividend.add_argument("effective_date", help="배당락일 YYYY-MM-DD")
    dividend.add_argument("--amount-per-share", type=float, required=True)
    dividend.add_argument("--external-id", default="")
    dividend.add_argument("--apply", action="store_true")
    dividend.add_argument("--approval-token", default="")

    listing = subparsers.add_parser("list", help="기업행사 감사 원장 조회")
    listing.add_argument("--symbol")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    data_dir = Path(args.data_dir)
    db_path = Path(args.db_path) if args.db_path else data_dir / "OverseasTradeRepository" / "overseas_trade.db"
    state_paths = ()
    if args.command == "split" and not args.no_state_sync:
        state_paths = tuple(sorted(data_dir.glob("overseas_intraday_*_state.json")))
    service = CorporateActionService(db_path=db_path, state_paths=state_paths)
    workflow = CorporateActionWorkflowService(
        service=service,
        db_path=db_path,
        state_paths=state_paths,
        backup_root=data_dir / "corporate_action_backups",
    )

    if args.command == "split":
        kwargs = dict(
            symbol=args.symbol,
            effective_date=args.effective_date,
            numerator=args.numerator,
            denominator=args.denominator,
            external_id=args.external_id,
        )
        result = (
            workflow.apply_split(approval_token=args.approval_token, **kwargs)
            if args.apply else workflow.preview_split(**kwargs)
        )
    elif args.command == "dividend":
        kwargs = dict(
            symbol=args.symbol,
            effective_date=args.effective_date,
            amount_per_share=args.amount_per_share,
            external_id=args.external_id,
        )
        result = (
            workflow.apply_dividend(approval_token=args.approval_token, **kwargs)
            if args.apply else workflow.preview_dividend(**kwargs)
        )
    else:
        result = service.list_actions(symbol=args.symbol)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
