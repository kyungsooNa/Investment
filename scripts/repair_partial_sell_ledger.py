"""부분 매도가 전량 청산으로 잘못 기록된 원장 행을 분할 복구한다.

배경: `log_sell` 계열이 qty 를 무시하고 lot 전체를 status='SOLD' 로 뒤집던 결함(PR #700)
때문에, 실제로는 절반만 체결된 주문이 전량 청산으로 기록됐다. 남은 잔량은 로컬 원장에서
사라져 다음 개장 대사에서 'broker_reconciled' 고아로 재등록되고, 그 시점부터 손절/청산을
관리하는 전략이 없어진다.

이 스크립트는 해당 행을 수정 후 코드가 애초에 만들었을 상태로 되돌린다:
  - 원본 행 → 잔량(qty - filled)만 남긴 HOLD 로 복원 (매도 필드 초기화, 전략 귀속 유지)
  - 신규 행   → 실제 체결분(filled)을 담은 SOLD 로 분리

실제 체결 수량은 로그의 execution_quality 이벤트(`filled_qty`)에서 확인해 넘긴다.

사용:
    # dry-run (기본) — 변경 없이 전/후만 출력
    python scripts/repair_partial_sell_ledger.py --trade-id 269 --filled-qty 4

    # 실제 반영 (DB 백업 후 수정)
    python scripts/repair_partial_sell_ledger.py --trade-id 269 --filled-qty 4 --apply

주의: 반영 전 브로커 잔고의 실제 보유 수량이 잔량과 일치하는지 확인할 것.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime

DEFAULT_DB = "data/VirtualTradeRepository/virtual_trade.db"
# 복원된 HOLD 행이 가져야 할 매도 관련 필드 값. 정상 HOLD 행과 동일한 형태로 맞춘다
# (return_rate/reason 은 NOT NULL 이라 NULL 이 아닌 기본값을 써야 한다).
_HOLD_RESET = {"sell_date": None, "sell_price": None, "return_rate": 0.0, "reason": ""}


def _fetch_row(conn: sqlite3.Connection, trade_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM trades WHERE id=?", (trade_id,)).fetchone()
    if row is None:
        sys.exit(f"[중단] trade id={trade_id} 행이 없습니다.")
    return row


def _validate(row: sqlite3.Row, filled_qty: int) -> int:
    if row["status"] != "SOLD":
        sys.exit(f"[중단] id={row['id']} status={row['status']} — SOLD 행만 복구 대상입니다.")
    held = int(row["qty"] or 0)
    if filled_qty <= 0:
        sys.exit(f"[중단] filled-qty 는 1 이상이어야 합니다 (입력 {filled_qty}).")
    if filled_qty >= held:
        sys.exit(
            f"[중단] filled-qty({filled_qty}) 가 기록 수량({held}) 이상입니다 — "
            f"부분 매도가 아니므로 복구할 것이 없습니다."
        )
    return held


def _print_plan(row: sqlite3.Row, filled_qty: int, held: int) -> None:
    remaining = held - filled_qty
    print("=" * 72)
    print(f"복구 대상: id={row['id']}  {row['strategy']}  {row['code']}")
    print(f"  매수    {row['buy_date']}  @{row['buy_price']:,.0f}  {held}주")
    print(f"  매도기록 {row['sell_date']}  @{row['sell_price']:,.0f}  수익률 {row['return_rate']}%")
    print("-" * 72)
    print("[현재] 1개 행")
    print(f"  id={row['id']}  SOLD  qty={held}   ← 실제로는 {filled_qty}주만 체결됨 (과대기록)")
    print()
    print("[복구 후] 2개 행")
    print(f"  id={row['id']}  HOLD  qty={remaining}   전략={row['strategy']} (매도필드 초기화, 잔량 보유 복원)")
    print(f"  id=신규    SOLD  qty={filled_qty}   @{row['sell_price']:,.0f}  수익률 {row['return_rate']}%")
    print("=" * 72)


def _backup(conn: sqlite3.Connection, db_path: str) -> str:
    """일관된 전체 스냅샷을 남긴다.

    이 DB 는 WAL 모드라 파일 복사(shutil.copy)는 -wal 에 있는 최신 커밋을 놓쳐
    조용히 불완전한 백업을 만든다. sqlite3 backup API 를 써야 한다.
    """
    backup_path = f"{db_path}.bak-{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    dest = sqlite3.connect(backup_path)
    try:
        conn.backup(dest)
    finally:
        dest.close()
    return backup_path


def _apply(conn: sqlite3.Connection, row: sqlite3.Row, filled_qty: int, held: int) -> int:
    columns = [r[1] for r in conn.execute("PRAGMA table_info(trades)").fetchall() if r[1] != "id"]
    overrides = {"qty": filled_qty}
    select_terms, params = [], []
    for col in columns:
        if col in overrides:
            select_terms.append("?")
            params.append(overrides[col])
        else:
            select_terms.append(col)
    params.append(row["id"])

    with conn:
        cursor = conn.execute(
            f"INSERT INTO trades ({', '.join(columns)}) "
            f"SELECT {', '.join(select_terms)} FROM trades WHERE id=?",
            params,
        )
        new_id = cursor.lastrowid
        reset_cols = ", ".join(f"{col}=?" for col in _HOLD_RESET)
        conn.execute(
            f"UPDATE trades SET qty=?, status='HOLD', {reset_cols} WHERE id=?",
            (held - filled_qty, *_HOLD_RESET.values(), row["id"]),
        )
    return new_id


def main() -> None:
    parser = argparse.ArgumentParser(description="부분 매도 오기록 원장 행 분할 복구")
    parser.add_argument("--trade-id", type=int, required=True, help="복구할 SOLD 행의 id")
    parser.add_argument("--filled-qty", type=int, required=True,
                        help="실제 체결 수량 (로그 execution_quality 의 filled_qty)")
    parser.add_argument("--db", default=DEFAULT_DB, help=f"DB 경로 (기본 {DEFAULT_DB})")
    parser.add_argument("--apply", action="store_true",
                        help="실제 반영. 생략 시 dry-run (변경 없음)")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    row = _fetch_row(conn, args.trade_id)
    held = _validate(row, args.filled_qty)
    _print_plan(row, args.filled_qty, held)

    if not args.apply:
        print("\n[DRY-RUN] 변경하지 않았습니다. 실제 반영하려면 --apply 를 붙이세요.")
        return

    backup = _backup(conn, args.db)
    print(f"\n[백업] {backup}")

    new_id = _apply(conn, row, args.filled_qty, held)
    print(f"[반영] id={args.trade_id} → HOLD {held - args.filled_qty}주 / 신규 id={new_id} SOLD {args.filled_qty}주")

    print("\n[검증]")
    for r in conn.execute(
        "SELECT id,strategy,code,status,qty,buy_price,sell_price FROM trades WHERE code=? ORDER BY id",
        (row["code"],),
    ):
        print("  ", dict(r))


if __name__ == "__main__":
    main()
