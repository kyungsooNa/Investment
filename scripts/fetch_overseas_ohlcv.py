"""해외 일봉 수집기 — 파라미터 스윕/백테스트 입력(per-code JSONL) 생성.

`scripts/run_overseas_vbo_sweep.py` 와 `scripts/analyze_overseas_dryrun.py --ohlcv-dir`
가 읽는 포맷(`<out_dir>/<CODE>.jsonl`, 한 줄 = 일봉 dict)으로 저장한다.

**읽기 전용**: 조회 API(해외 일봉)만 호출하며 주문 경로를 조립하지 않는다.
심볼당 1회 호출이므로 top-N 50 이면 50콜이다.

**구간 주의**: KIS 해외 일봉 TR 은 `start_date` 를 반영하지 않고 `end_date` 기준
마지막 100봉만 반환한다(2026-08 실측). 더 긴 이력이 필요하면 `end_date` 를 과거로
옮겨 여러 번 실행해 이어붙여야 한다. 실행 결과에 실제 커버 구간을 출력한다.

사용:
    conda activate py310
    python scripts/fetch_overseas_ohlcv.py --start-date 20260101 --end-date 20260804
    python scripts/fetch_overseas_ohlcv.py --symbols AAPL,MSFT,NVDA \\
        --start-date 20260101 --end-date 20260804 --out-dir data/overseas_ohlcv
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from common.overseas_types import OverseasExchange  # noqa: E402
from common.types import ErrorCode  # noqa: E402


async def resolve_symbols(
    *,
    candidate_service,
    symbols_arg: Optional[str],
    exchange: OverseasExchange,
    top_n: int,
) -> List[str]:
    """명시 심볼이 있으면 그것을, 없으면 거래대금 상위 후보를 쓴다."""
    if symbols_arg:
        return [s.strip().upper() for s in symbols_arg.split(",") if s.strip()]
    candidates = await candidate_service.get_candidates(exchange, top_n=top_n)
    return [c["code"] for c in (candidates or []) if c.get("code")]


async def fetch_to_dir(
    *,
    stock_query_service,
    symbols: List[str],
    exchange: OverseasExchange,
    start_date: str,
    end_date: str,
    out_dir: Path | str,
    logger: logging.Logger,
) -> Dict[str, Any]:
    """심볼별 일봉을 받아 `<out_dir>/<CODE>.jsonl` 로 저장한다.

    한 심볼 실패가 전체를 중단시키지 않는다(수집은 재실행 가능한 배치).
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    saved = 0
    total_bars = 0
    failed: List[str] = []
    dates: List[str] = []
    for symbol in symbols:
        try:
            resp = await stock_query_service.get_ohlcv_range(
                symbol, "D", start_date, end_date, exchange=exchange,
            )
        except Exception as e:
            logger.warning("%s 일봉 조회 예외 — %s", symbol, e)
            failed.append(symbol)
            continue
        if not resp or resp.rt_cd != ErrorCode.SUCCESS.value or not resp.data:
            logger.warning("%s 일봉 조회 실패 — %s", symbol, getattr(resp, "msg1", ""))
            failed.append(symbol)
            continue
        bars = [b for b in resp.data if isinstance(b, dict)]
        path = out_dir / f"{symbol}.jsonl"
        with path.open("w", encoding="utf-8") as f:
            for bar in bars:
                f.write(json.dumps(bar, ensure_ascii=False))
                f.write("\n")
        saved += 1
        total_bars += len(bars)
        dates.extend(str(b.get("date") or "") for b in bars if b.get("date"))
    return {
        "symbols": saved,
        "bars": total_bars,
        "failed": failed,
        "out_dir": str(out_dir),
        # KIS 해외 일봉은 start_date 를 무시하고 end_date 기준 마지막 N봉만 준다.
        # 요청 구간이 아니라 실제로 받은 구간을 보고한다.
        "first_date": min(dates) if dates else None,
        "last_date": max(dates) if dates else None,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="해외 일봉 수집(스윕/백테스트 입력)")
    parser.add_argument("--start-date", required=True, help="YYYYMMDD")
    parser.add_argument("--end-date", required=True, help="YYYYMMDD")
    parser.add_argument("--out-dir", default="data/overseas_ohlcv")
    parser.add_argument("--exchange", default="NASD", help="NASD | NYSE | AMEX")
    parser.add_argument("--symbols", default=None,
                        help="쉼표 구분 심볼. 미지정 시 거래대금 상위 후보 top-N 사용.")
    parser.add_argument("--top-n", type=int, default=50)
    parser.add_argument("--paper", action="store_true", help="모의 환경 토큰 사용")
    return parser


async def main_async(argv: Optional[List[str]] = None) -> int:
    from scripts.run_overseas_dryrun import _make_stdout_logger, build_overseas_query_stack

    args = build_parser().parse_args(argv)
    logger = _make_stdout_logger()
    exchange = OverseasExchange(args.exchange.upper())

    stack = await build_overseas_query_stack(is_paper_trading=bool(args.paper), logger=logger)
    symbols = await resolve_symbols(
        candidate_service=stack["candidate_service"],
        symbols_arg=args.symbols,
        exchange=exchange,
        top_n=args.top_n,
    )
    if not symbols:
        print("[ERROR] 수집할 심볼이 없습니다.")
        return 1

    result = await fetch_to_dir(
        stock_query_service=stack["stock_query_service"],
        symbols=symbols,
        exchange=exchange,
        start_date=args.start_date,
        end_date=args.end_date,
        out_dir=args.out_dir,
        logger=logger,
    )
    print(f"[INFO] 저장 {result['symbols']}종목 / {result['bars']}봉 → {result['out_dir']}")
    print(f"[INFO] 실제 커버 구간: {result['first_date']} ~ {result['last_date']} "
          f"(요청: {args.start_date} ~ {args.end_date})")
    if result["failed"]:
        print(f"[WARN] 실패 {len(result['failed'])}종목: {', '.join(result['failed'][:10])}")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    return asyncio.run(main_async(argv))


if __name__ == "__main__":
    sys.exit(main())
