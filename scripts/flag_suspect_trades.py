"""오염이 의심되는 원장 행에 의심 표식을 남긴다(수치는 재작성하지 않는다).

배경: PR #700 이전 `log_sell` 이 qty 를 무시하고 lot 전체를 SOLD 로 뒤집던 결함 때문에,
절반만 체결된 주문이 전량 청산으로 기록됐다. 나머지 절반이 미청산이라 **당시 수익률을
소급 재구성할 수 없다** — 그래서 값을 고치지 않고 `data_quality_flag` 로 표식만 남긴다.
표식이 붙은 행은 웹 매도 기록 표에 ⚠ 로 표시되고, 성과 집계 수치 자체는 바뀌지 않는다.

알려진 대상(06-28 tiered 도입 이후 SOLD 15건 중 5건, 전부 VBO):
    trade id 251 · 261 · 263 · 265 · 269

사용:
    # dry-run (기본) — 변경 없이 대상 행만 출력
    python scripts/flag_suspect_trades.py --trade-id 251 --trade-id 261 \
        --note "부분매도 전량기록 의심 (PR #700 이전)"

    # 실제 반영 (DB 백업 후)
    python scripts/flag_suspect_trades.py --trade-id 251 --note "..." --apply

주의: 이 DB 는 WAL 모드다 — 백업은 반드시 `sqlite3` backup API 로. 파일 복사는
`-wal` 의 최신 커밋을 놓쳐 조용히 불완전한 스냅샷을 만든다.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys

DEFAULT_DB = "data/VirtualTradeRepository/virtual_trade.db"


def _ensure_flag_column(conn: sqlite3.Connection) -> None:
    existing = {row[1] for row in conn.execute("PRAGMA table_info(trades)").fetchall()}
    if "data_quality_flag" not in existing:
        conn.execute("ALTER TABLE trades ADD COLUMN data_quality_flag TEXT")


def _fetch_rows(conn: sqlite3.Connection, trade_ids: list[int]) -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    rows = []
    for trade_id in trade_ids:
        row = conn.execute("SELECT * FROM trades WHERE id=?", (trade_id,)).fetchone()
        if row is None:
            sys.exit(f"[중단] trade id={trade_id} 행이 없습니다.")
        rows.append(row)
    return rows


def _print_plan(rows: list[sqlite3.Row], note: str, apply: bool) -> None:
    print("=" * 72)
    print(f"의심 표식 대상 {len(rows)}건  note={note!r}")
    print("=" * 72)
    for row in rows:
        # dry-run 은 컬럼 추가 전이라 아직 없을 수 있다.
        before = row["data_quality_flag"] if "data_quality_flag" in row.keys() else None
        print(
            f"  id={row['id']}  {row['strategy']}  {row['code']}  "
            f"status={row['status']}  return_rate={row['return_rate']}"
        )
        print(f"      flag: {before!r} -> {note!r}")
    print("-" * 72)
    print("수익률/상태/수량은 변경하지 않는다 — 표식만 남긴다.")
    if not apply:
        print("dry-run 입니다. 실제로 반영하려면 --apply 를 붙이세요.")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="오염 의심 거래에 data_quality_flag 표식을 남긴다.")
    parser.add_argument("--trade-id", type=int, action="append", required=True,
                        help="대상 trade id (여러 번 지정 가능)")
    parser.add_argument("--note", required=True, help="표식 문구 (의심 사유)")
    parser.add_argument("--db", default=DEFAULT_DB, help=f"DB 경로 (기본: {DEFAULT_DB})")
    parser.add_argument("--apply", action="store_true", help="실제 DB 에 반영 (기본은 dry-run)")
    args = parser.parse_args(argv)

    conn = sqlite3.connect(args.db)
    try:
        rows = _fetch_rows(conn, args.trade_id)
        _print_plan(rows, args.note, args.apply)
        if not args.apply:
            return
        with conn:
            _ensure_flag_column(conn)
            conn.executemany(
                "UPDATE trades SET data_quality_flag=? WHERE id=?",
                [(args.note, row["id"]) for row in rows],
            )
        print(f"반영 완료 — {len(rows)}건에 표식을 남겼습니다.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
