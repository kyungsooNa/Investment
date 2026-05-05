# repositories/virtual_trade_repository.py
import bisect
import sqlite3
import numpy as np
import pandas as pd
import asyncio
import threading
import os
import json
import math
import logging
from datetime import datetime, timedelta
from functools import lru_cache
from core.market_clock import MarketClock
from common.trade_journal_schema import normalize_virtual_trade
from utils.transaction_cost_utils import TransactionCostUtils

logger = logging.getLogger(__name__)

COLUMNS = ["strategy", "code", "buy_date", "buy_price", "qty", "sell_date", "sell_price", "return_rate", "status", "reason"]

# 강제종결 reason 마커 — reconcile 시 로컬 HOLD 인데 브로커 잔고 없음 판정 시 sell_price=0 으로 처리되며,
# 이를 통계에서 제외해 승률/평균수익률 왜곡을 방지한다. (services/strategy_log_report_service.py 와 동일 값)
_FORCE_CLOSE_REASON = "reconciled_force_close"

LIVE_STRATEGY_STATE_FILES = {
    "오닐PP/BGU": "pp_position_state.json",
    "오닐스퀴즈돌파": "osb_position_state.json",
    "하이타이트플래그": "htf_position_state.json",
    "첫눌림목": "fp_position_state.json",
}

_SELECT_TRADES = (
    "SELECT strategy, code, buy_date, buy_price, qty, sell_date, sell_price, return_rate, status, reason "
    "FROM trades ORDER BY id"
)
_INSERT_TRADE = (
    "INSERT INTO trades "
    "(strategy, code, buy_date, buy_price, qty, sell_date, sell_price, return_rate, status, reason) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)
_DDL = """
CREATE TABLE IF NOT EXISTS trades (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy    TEXT    NOT NULL,
    code        TEXT    NOT NULL,
    buy_date    TEXT    NOT NULL,
    buy_price   REAL    NOT NULL,
    qty         INTEGER NOT NULL DEFAULT 1,
    sell_date   TEXT,
    sell_price  REAL,
    return_rate REAL    NOT NULL DEFAULT 0.0,
    status      TEXT    NOT NULL,
    reason      TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_trades_strategy_code_status ON trades(strategy, code, status);
CREATE TABLE IF NOT EXISTS snapshots (
    date        TEXT NOT NULL,
    strategy    TEXT NOT NULL,
    return_rate REAL NOT NULL,
    PRIMARY KEY (date, strategy)
);
CREATE INDEX IF NOT EXISTS idx_snapshots_date ON snapshots(date);
CREATE TABLE IF NOT EXISTS price_cache (
    code    TEXT    NOT NULL,
    date    TEXT    NOT NULL,
    close   INTEGER NOT NULL,
    PRIMARY KEY (code, date)
);
"""


@lru_cache(maxsize=1024)
def _is_weekday(date_str: str) -> bool:
    # 이미 처리한 날짜는 다시 계산하지 않음
    return datetime.strptime(date_str, "%Y-%m-%d").weekday() < 5


def _strategy_values(snapshot: dict) -> dict:
    """스냅샷에서 개별 전략 값만 추출 (ALL 제외). ALL은 파생 값이라 비교에서 제외."""
    return {k: v for k, v in snapshot.items() if k != "ALL"}


def _get_trading_dates(daily: dict) -> list[str]:
    """스냅샷 dict에서 실제 거래일만 추출 (평일 + 개별 전략 값이 변한 날짜). 오름차순 반환."""
    weekday_dates = sorted(d for d in daily if _is_weekday(d))
    if not weekday_dates:
        return []
    trading = [weekday_dates[0]]  # 첫 날은 항상 포함
    for d in weekday_dates[1:]:
        if _strategy_values(daily[d]) != _strategy_values(daily[trading[-1]]):
            trading.append(d)
    return trading


def _build_strategy_return_history(daily: dict, strategy_name: str) -> list[dict]:
    if not daily:
        return []

    df = pd.DataFrame.from_dict(daily, orient='index')
    if strategy_name not in df.columns:
        return []

    series = df[strategy_name].sort_index()
    first_valid = series.first_valid_index()
    if first_valid is None:
        return []

    series = series.loc[first_valid:].ffill()
    return [
        {"date": date, "return_rate": float(val)}
        for date, val in series.items()
        if _is_weekday(date) and pd.notna(val)
    ]


class VirtualTradeRepository:
    def __init__(self, db_path: str = "data/VirtualTradeRepository/virtual_trade.db", market_clock: MarketClock = None):
        self._cached_data = None
        self.db_path = db_path
        self.tm = market_clock if market_clock else MarketClock()
        self._lock = threading.Lock()
        dir_path = os.path.dirname(self.db_path)
        if dir_path:
            os.makedirs(dir_path, exist_ok=True)
        self._db = sqlite3.connect(self.db_path, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(_DDL)
        self._migrate_legacy_data()

    # ---- 레거시 데이터 마이그레이션 (CSV/JSON → SQLite, 최초 1회) ----

    def _migrate_legacy_data(self):
        """기존 CSV/JSON 파일이 있으면 SQLite로 1회 마이그레이션한다."""
        base_dir = os.path.dirname(self.db_path)
        if not base_dir:
            return  # :memory: 등 디렉토리가 없는 경우 스킵
        flag_path = os.path.join(base_dir, ".migrated")
        if os.path.exists(flag_path):
            return

        # 레거시 파일은 VirtualTradeManager 디렉토리에 위치
        legacy_dir = os.path.join(os.path.dirname(base_dir), "VirtualTradeManager")

        migrated = False

        csv_path = os.path.join(legacy_dir, "trade_journal.csv")
        if os.path.exists(csv_path):
            try:
                df = pd.read_csv(csv_path, dtype={'code': str, 'sell_date': object}, encoding='utf-8')
                df['return_rate'] = df['return_rate'].fillna(0.0)
                if 'qty' not in df.columns:
                    df['qty'] = 1
                if 'reason' not in df.columns:
                    df['reason'] = ''
                self._write(df)
                logger.info(f"[마이그레이션] {csv_path} → trades 테이블 완료")
                migrated = True
            except Exception as e:
                logger.warning(f"[마이그레이션] CSV 마이그레이션 실패: {e}")

        snap_path = os.path.join(legacy_dir, "portfolio_snapshots.json")
        if os.path.exists(snap_path):
            try:
                with open(snap_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if "daily" not in data:
                    data = {"daily": data, "prev_values": {}}
                self._save_data(data)
                logger.info(f"[마이그레이션] {snap_path} → snapshots 테이블 완료")
                migrated = True
            except Exception as e:
                logger.warning(f"[마이그레이션] 스냅샷 마이그레이션 실패: {e}")

        price_path = os.path.join(legacy_dir, "close_price_cache.json")
        if os.path.exists(price_path):
            try:
                with open(price_path, 'r', encoding='utf-8') as f:
                    cache = json.load(f)
                self._save_price_cache(cache)
                logger.info(f"[마이그레이션] {price_path} → price_cache 테이블 완료")
                migrated = True
            except Exception as e:
                logger.warning(f"[마이그레이션] 가격 캐시 마이그레이션 실패: {e}")

        if migrated:
            with open(flag_path, 'w') as f:
                f.write("1")
            logger.info("[마이그레이션] 레거시 데이터 마이그레이션 완료.")

    def _read(self) -> pd.DataFrame:
        df = pd.read_sql_query(_SELECT_TRADES, self._db, dtype={'code': str, 'sell_date': object})
        df['return_rate'] = df['return_rate'].fillna(0.0)
        return df

    def _write(self, df: pd.DataFrame):
        """DataFrame으로 trades 테이블 전체 교체 (기존 코드 및 테스트 호환성 유지)."""
        rows = []
        for row in df.itertuples(index=False):
            sell_date_raw = getattr(row, 'sell_date', None)
            sell_date = None if (sell_date_raw is None or (isinstance(sell_date_raw, float) and math.isnan(sell_date_raw))) else str(sell_date_raw)
            sell_price_raw = getattr(row, 'sell_price', None)
            sell_price = None if (sell_price_raw is None or (isinstance(sell_price_raw, float) and math.isnan(sell_price_raw))) else float(sell_price_raw)
            qty_raw = getattr(row, 'qty', 1)
            qty = int(qty_raw) if (qty_raw is not None and not (isinstance(qty_raw, float) and math.isnan(qty_raw))) else 1
            rr_raw = getattr(row, 'return_rate', 0.0)
            return_rate = float(rr_raw) if (rr_raw is not None and not (isinstance(rr_raw, float) and math.isnan(rr_raw))) else 0.0
            rows.append((
                row.strategy, row.code, str(row.buy_date), float(row.buy_price), qty,
                sell_date, sell_price, return_rate, row.status,
                getattr(row, 'reason', '') or ''
            ))
        with self._db:
            self._db.execute("DELETE FROM trades")
            self._db.executemany(_INSERT_TRADE, rows)

    def _get_data_root_dir(self) -> str:
        base_dir = os.path.dirname(os.path.dirname(self.db_path))
        return base_dir if base_dir else "data"

    @staticmethod
    def _normalize_entry_date(entry_date: str) -> str:
        raw = str(entry_date or "").strip()
        if len(raw) == 8 and raw.isdigit():
            return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]} 00:00:00"
        if len(raw) == 10 and raw.count("-") == 2:
            return f"{raw} 00:00:00"
        return raw

    def _load_live_strategy_state_positions(self) -> list[dict]:
        data_root = self._get_data_root_dir()
        positions: list[dict] = []

        for strategy_name, file_name in LIVE_STRATEGY_STATE_FILES.items():
            path = os.path.join(data_root, file_name)
            if not os.path.exists(path):
                continue

            try:
                with open(path, "r", encoding="utf-8") as f:
                    payload = json.load(f)
            except Exception as e:
                logger.warning(f"[가상매매] 전략 상태파일 로드 실패 {path}: {e}")
                continue

            raw_positions = payload.get("positions", {}) if isinstance(payload, dict) else {}
            for code, state in raw_positions.items():
                if not isinstance(state, dict):
                    continue

                positions.append({
                    "strategy": strategy_name,
                    "code": str(code).strip(),
                    "buy_price": float(state.get("entry_price", 0) or 0),
                    "buy_date": self._normalize_entry_date(state.get("entry_date", "")),
                })

        return [p for p in positions if p["code"] and p["buy_price"] > 0 and p["buy_date"]]

    def _load_scheduler_open_signal_map(self, target_pairs: set[tuple[str, str]]) -> dict[tuple[str, str], dict]:
        if not target_pairs:
            return {}

        scheduler_db_path = os.path.join(self._get_data_root_dir(), "StrategyScheduler", "scheduler.db")
        if not os.path.exists(scheduler_db_path):
            return {}

        try:
            with sqlite3.connect(scheduler_db_path) as conn:
                rows = conn.execute(
                    """
                    SELECT id, strategy_name, code, action, price, qty, timestamp
                    FROM signal_history
                    WHERE api_success=1
                    ORDER BY id
                    """
                ).fetchall()
        except Exception as e:
            logger.warning(f"[가상매매] scheduler signal_history 로드 실패: {e}")
            return {}

        signals_by_pair: dict[tuple[str, str], list[dict]] = {}
        for row_id, strategy_name, code, action, price, qty, timestamp in rows:
            key = (str(strategy_name).strip(), str(code).strip())
            if key not in target_pairs:
                continue
            signals_by_pair.setdefault(key, []).append({
                "id": row_id,
                "action": str(action).strip().upper(),
                "price": int(price or 0),
                "qty": int(qty or 0),
                "timestamp": str(timestamp or "").strip(),
            })

        open_signal_map: dict[tuple[str, str], dict] = {}
        for key, signal_rows in signals_by_pair.items():
            remaining_sell_qty = 0
            open_lots: list[dict] = []

            for row in reversed(signal_rows):
                qty = int(row["qty"] or 0)
                if qty <= 0:
                    continue

                if row["action"] == "SELL":
                    remaining_sell_qty += qty
                    continue
                if row["action"] != "BUY":
                    continue

                if remaining_sell_qty >= qty:
                    remaining_sell_qty -= qty
                    continue

                open_qty = qty - remaining_sell_qty
                remaining_sell_qty = 0
                open_lots.append({
                    "qty": open_qty,
                    "price": row["price"],
                    "timestamp": row["timestamp"],
                })

            if not open_lots:
                continue

            latest_lot = open_lots[0]
            open_signal_map[key] = {
                "qty": sum(int(lot["qty"]) for lot in open_lots),
                "buy_price": latest_lot["price"],
                "buy_date": latest_lot["timestamp"],
            }

        return open_signal_map

    def sync_live_strategy_positions(self) -> list[dict]:
        positions = self._load_live_strategy_state_positions()
        if not positions:
            return []

        target_pairs = {(p["strategy"], p["code"]) for p in positions}
        open_signal_map = self._load_scheduler_open_signal_map(target_pairs)
        inserted: list[dict] = []

        with self._lock:
            for position in positions:
                strategy_name = position["strategy"]
                code = position["code"]
                row = self._db.execute(
                    "SELECT 1 FROM trades WHERE strategy=? AND code=? AND status='HOLD' LIMIT 1",
                    (strategy_name, code)
                ).fetchone()
                if row is not None:
                    continue

                signal_meta = open_signal_map.get((strategy_name, code), {})
                buy_price = float(signal_meta.get("buy_price") or position["buy_price"])
                buy_date = str(signal_meta.get("buy_date") or position["buy_date"])
                qty = int(signal_meta.get("qty") or 1)
                source = "scheduler_signal" if signal_meta else "state_file"

                # 행 단위 추적 로그 — 향후 의심 데이터(자정 buy_date 등) 발생 시 즉시 추적 가능.
                # 자정(00:00:00)은 strategy state 파일이 YYYYMMDD 만 기록해 _normalize_entry_date 가
                # 자정으로 정규화한 흔적이며, 정상 매매 흐름에서는 나오지 않는 패턴이다.
                is_midnight = buy_date.endswith("00:00:00")
                log_fn = logger.warning if is_midnight else logger.info
                log_fn(
                    f"[가상매매] sync INSERT strategy={strategy_name} code={code} "
                    f"buy_date={buy_date} buy_price={buy_price} qty={qty} source={source}"
                    + (" [의심: 자정 buy_date — state 파일 entry_date 가 시간 없는 형식]" if is_midnight else "")
                )

                with self._db:
                    self._db.execute(
                        _INSERT_TRADE,
                        (strategy_name, code, buy_date, buy_price, qty, None, None, 0.0, "HOLD", "")
                    )

                inserted.append({
                    "strategy": strategy_name,
                    "code": code,
                    "buy_date": buy_date,
                    "buy_price": buy_price,
                    "qty": qty,
                    "source": source,
                })

        if inserted:
            logger.info(f"[가상매매] live 전략 포지션 동기화: {inserted}")

        return inserted

    # ---- 매수/매도 ----

    def log_buy(self, strategy_name: str, code: str, current_price, qty: int = 1):
        """가상 매수 기록. 동일 전략+종목 중복 매수 방지."""
        with self._lock:
            if self.is_holding(strategy_name, code):
                logger.info(f"[가상매매] {strategy_name}/{code} 이미 보유 중 — 매수 스킵")
                return
            buy_date = self.tm.get_current_kst_time().strftime("%Y-%m-%d %H:%M:%S")
            with self._db:
                self._db.execute(_INSERT_TRADE,
                    (strategy_name, code, buy_date, current_price, qty, None, None, 0.0, "HOLD", ""))
            logger.info(f"[가상매매] {strategy_name}/{code} 매수 기록 (가격: {current_price}, 수량: {qty})")

    async def log_buy_async(self, strategy_name: str, code: str, current_price, qty: int = 1):
        """log_buy의 비동기 래퍼 (스레드 실행)."""
        await asyncio.to_thread(self.log_buy, strategy_name, code, current_price, qty)

    def log_sell(self, code: str, current_price, qty: int = 1, reason: str = ""):
        """가상 매도 — 해당 종목 가장 최근 HOLD 건."""
        with self._lock:
            row = self._db.execute(
                "SELECT id, buy_price FROM trades WHERE code=? AND status='HOLD' ORDER BY id DESC LIMIT 1",
                (code,)
            ).fetchone()
            if row is None:
                logger.warning(f"[가상매매] {code} 매도 실패: 보유 내역 없음")
                return
            trade_id, buy_price = row
            return_rate = ((current_price - buy_price) / buy_price) * 100 if buy_price else 0
            sell_date = self.tm.get_current_kst_time().strftime("%Y-%m-%d %H:%M:%S")
            with self._db:
                self._db.execute(
                    "UPDATE trades SET sell_date=?, sell_price=?, return_rate=?, status='SOLD', reason=? WHERE id=?",
                    (sell_date, current_price, round(return_rate, 2), reason, trade_id)
                )
            logger.info(f"[가상매매] {code} 매도 기록 (수익률: {return_rate:.2f}%{', 사유: '+reason if reason else ''})")

    async def log_sell_async(self, code: str, current_price, qty: int = 1, reason: str = ""):
        """log_sell의 비동기 래퍼 (스레드 실행)."""
        await asyncio.to_thread(self.log_sell, code, current_price, qty, reason)

    def log_sell_by_strategy(self, strategy_name: str, code: str, current_price, qty: int = 1, reason: str = "") -> float | None:
        """전략+종목 매칭 매도. 성공 시 수익률 반환, 실패 시 None 반환."""
        with self._lock:
            row = self._db.execute(
                "SELECT id, buy_price FROM trades WHERE strategy=? AND code=? AND status='HOLD' ORDER BY id DESC LIMIT 1",
                (strategy_name, code)
            ).fetchone()
            if row is None:
                logger.warning(f"[가상매매] {strategy_name}/{code} 매도 실패: 보유 내역 없음")
                return None
            trade_id, buy_price = row
            return_rate = ((current_price - buy_price) / buy_price) * 100 if buy_price else 0
            sell_date = self.tm.get_current_kst_time().strftime("%Y-%m-%d %H:%M:%S")
            with self._db:
                self._db.execute(
                    "UPDATE trades SET sell_date=?, sell_price=?, return_rate=?, status='SOLD', reason=? WHERE id=?",
                    (sell_date, current_price, round(return_rate, 2), reason, trade_id)
                )
            logger.info(f"[가상매매] {strategy_name}/{code} 매도 기록 (수익률: {round(return_rate, 2):.2f}%{', 사유: '+reason if reason else ''})")
            return round(return_rate, 2)

    async def log_sell_by_strategy_async(self, strategy_name: str, code: str, current_price, qty: int = 1, reason: str = "") -> float | None:
        """log_sell_by_strategy의 비동기 래퍼 (스레드 실행). 성공 시 수익률 반환."""
        return await asyncio.to_thread(self.log_sell_by_strategy, strategy_name, code, current_price, qty, reason)

    def log_order_failure(self, action: str, code: str, price, qty: int, reason: str, strategy_name: str = ""):
        """주문 최종 실패 시 FAILED 상태로 기록."""
        with self._lock:
            fail_date = self.tm.get_current_kst_time().strftime("%Y-%m-%d %H:%M:%S")
            strategy_label = strategy_name if strategy_name else f"{action}실패"
            with self._db:
                self._db.execute(_INSERT_TRADE,
                    (strategy_label, code, fail_date, price, qty, None, None, 0.0, "FAILED", reason))
            logger.warning(f"[가상매매] {action} 주문 실패 기록: {code} @ {price}원 x {qty}주 — {reason}")

    async def log_order_failure_async(self, action: str, code: str, price, qty: int, reason: str, strategy_name: str = ""):
        """log_order_failure의 비동기 래퍼 (스레드 실행)."""
        await asyncio.to_thread(self.log_order_failure, action, code, price, qty, reason, strategy_name)

    # ---- 조회 ----

    def _to_json_records(self, df: pd.DataFrame) -> list:
        """DataFrame을 JSON 직렬화 가능한 dict 리스트로 변환 (NaN -> None)."""
        records = df.to_dict(orient='records')
        for record in records:
            for key, value in record.items():
                if isinstance(value, float) and math.isnan(value):
                    record[key] = None
        return records

    def calculate_return(self, buy_price, sell_price, qty=1, apply_cost=False) -> float:
        """수익률 계산 헬퍼 (TransactionCostManager 위임)"""
        return round(TransactionCostUtils.get_return_rate(buy_price, sell_price, qty, apply_cost), 2)

    def get_trade_amount(self, price, qty=1, is_sell=False, apply_cost=False) -> float:
        """거래 금액 계산 (비용 포함 매수금액 또는 비용 차감 매도금액)"""
        base_amount = price * qty
        if not apply_cost:
            return base_amount
        cost = TransactionCostUtils.calculate_cost(price, qty, is_sell)
        return base_amount - cost if is_sell else base_amount + cost

    def get_all_trades(self, apply_cost: bool = False) -> list:
        """전체 거래 기록 반환 (웹 API용). apply_cost=True 시 수익률 재계산."""
        df = self._read()
        records = self._to_json_records(df)
        if apply_cost:
            for r in records:
                if r.get('status') == 'SOLD' and r.get('buy_price') and r.get('sell_price'):
                    r['return_rate'] = self.calculate_return(r['buy_price'], r['sell_price'], r.get('qty', 1), True)
        return records

    def get_standard_journal_records(self) -> list[dict]:
        """백테스트/실거래 비교용 표준 journal schema로 전체 거래를 반환한다."""
        return [normalize_virtual_trade(record) for record in self.get_all_trades()]

    def get_solds(self) -> list:
        """전체 SOLD 포지션 반환."""
        df = pd.read_sql_query(
            "SELECT strategy,code,buy_date,buy_price,qty,sell_date,sell_price,return_rate,status,reason "
            "FROM trades WHERE status='SOLD' ORDER BY id",
            self._db, dtype={'code': str, 'sell_date': object}
        )
        return self._to_json_records(df)

    def get_holds(self) -> list:
        """전체 HOLD 포지션 반환."""
        df = pd.read_sql_query(
            "SELECT strategy,code,buy_date,buy_price,qty,sell_date,sell_price,return_rate,status,reason "
            "FROM trades WHERE status='HOLD' ORDER BY id",
            self._db, dtype={'code': str, 'sell_date': object}
        )
        return self._to_json_records(df)

    def get_holds_by_strategy(self, strategy_name: str) -> list:
        """전략별 HOLD 포지션 반환."""
        df = pd.read_sql_query(
            "SELECT strategy,code,buy_date,buy_price,qty,sell_date,sell_price,return_rate,status,reason "
            "FROM trades WHERE strategy=? AND status='HOLD' ORDER BY id",
            self._db, params=(strategy_name,), dtype={'code': str, 'sell_date': object}
        )
        return self._to_json_records(df)

    def is_holding(self, strategy_name: str, code: str) -> bool:
        """해당 전략에서 종목 보유 중인지 확인."""
        row = self._db.execute(
            "SELECT 1 FROM trades WHERE strategy=? AND code=? AND status='HOLD' LIMIT 1",
            (strategy_name, code)
        ).fetchone()
        return row is not None

    def fix_sell_price(self, code: str, buy_date: str, correct_price):
        """sell_price가 0인 SOLD 기록의 매도가/수익률을 보정합니다."""
        with self._lock:
            query = "SELECT id, buy_price FROM trades WHERE code=? AND status='SOLD' AND sell_price=0"
            params: list = [code]
            if buy_date:
                query += " AND buy_date=?"
                params.append(buy_date)
            rows = self._db.execute(query, params).fetchall()
            if not rows:
                return
            with self._db:
                for trade_id, buy_price in rows:
                    return_rate = round(((correct_price - buy_price) / buy_price) * 100, 2) if buy_price else 0
                    self._db.execute(
                        "UPDATE trades SET sell_price=?, return_rate=? WHERE id=?",
                        (correct_price, return_rate, trade_id)
                    )
            logger.info(f"[가상매매] {code} sell_price 보정 완료 → {correct_price}")

    def get_summary(self, apply_cost: bool = False) -> dict:
        """전체 매매 요약 통계 (HOLD + SOLD 모두 포함).

        win_rate / avg_return 은 강제종결(reason="reconciled_force_close") 매매를 제외한
        정상 매도만으로 계산한다 — 브로커 잔고 미일치로 sell_price=0 처리되는 강제종결이
        승률·평균수익률 통계를 왜곡하지 않도록 분리.
        force_closed_count 는 별도 노출하여 UI 가 필요 시 표시할 수 있게 한다.
        """
        df = self._read()
        total_trades = len(df)
        sold_df = df[df['status'] == 'SOLD']

        if 'reason' in sold_df.columns:
            reason_series = sold_df['reason'].fillna('').astype(str)
            force_closed_count = int((reason_series == _FORCE_CLOSE_REASON).sum())
            natural_sold_df = sold_df[reason_series != _FORCE_CLOSE_REASON]
        else:
            force_closed_count = 0
            natural_sold_df = sold_df

        if natural_sold_df.empty:
            return {
                "total_trades": total_trades,
                "win_rate": 0,
                "avg_return": 0,
                "force_closed_count": force_closed_count,
            }

        # 수익률 시리즈 추출 (비용 적용 시 재계산)
        if apply_cost:
            returns = natural_sold_df.apply(
                lambda row: self.calculate_return(row['buy_price'], row['sell_price'], row['qty'], True),
                axis=1,
            )
        else:
            returns = natural_sold_df['return_rate']

        win_trades = len(returns[returns > 0])
        win_rate = (win_trades / len(natural_sold_df) * 100)
        avg_return = returns.mean()

        return {
            "total_trades": total_trades,
            "win_rate": round(win_rate, 1),
            "avg_return": round(avg_return, 2),
            "force_closed_count": force_closed_count,
        }

    # ---- 종가 캐시 (backfill용) ----

    def _load_price_cache(self) -> dict:
        """SQLite price_cache 테이블 로드. 구조: { "005930": {"2026-02-13": 56000, ...}, ... }"""
        cache: dict = {}
        for code, date, close in self._db.execute("SELECT code, date, close FROM price_cache"):
            if code not in cache:
                cache[code] = {}
            cache[code][date] = close
        return cache

    def _save_price_cache(self, cache: dict):
        rows = [
            (code, date, int(close))
            for code, dates in cache.items()
            for date, close in dates.items()
        ]
        if rows:
            with self._db:
                self._db.executemany(
                    "INSERT OR REPLACE INTO price_cache (code, date, close) VALUES (?, ?, ?)",
                    rows
                )

    def _fetch_close_prices(self, codes: list[str], start_date: str, end_date: str) -> dict:
        """pykrx로 종가 조회 후 캐시에 병합. 캐시에 이미 있으면 API 스킵.
        Returns: { code: { "YYYY-MM-DD": close_price, ... }, ... }
        """
        from pykrx import stock as pykrx_stock

        cache = self._load_price_cache()
        start_fmt = start_date.replace('-', '')
        end_fmt = end_date.replace('-', '')
        fetched = 0

        for code in codes:
            # 캐시에 해당 종목+기간 데이터가 이미 있는지 확인
            if code in cache:
                cached_dates = set(cache[code].keys())
                # start~end 범위의 영업일 중 누락된 날짜가 없으면 스킵
                needed_dates = set(
                    d.strftime('%Y-%m-%d')
                    for d in pd.date_range(start_date, end_date, freq='B')
                )
                if needed_dates.issubset(cached_dates):
                    continue

            try:
                df = pykrx_stock.get_market_ohlcv_by_date(start_fmt, end_fmt, code)
                if df.empty:
                    continue

                if code not in cache:
                    cache[code] = {}

                for date_idx, row in df.iterrows():
                    day_str = date_idx.strftime('%Y-%m-%d')
                    cache[code][day_str] = int(row['종가'])

                fetched += 1
            except Exception as e:
                logger.warning(f"[가상매매] pykrx 종가 조회 실패 {code}: {e}")
                continue

        if fetched > 0:
            self._save_price_cache(cache)
            logger.info(f"[가상매매] 종가 캐시 업데이트: {fetched}개 종목 조회")

        return cache

    # ---- backfill ----

    def backfill_snapshots(self):
        """거래 기록을 기반으로 과거 일별 스냅샷을 역산하여 채웁니다.
        이미 스냅샷이 존재하는 날짜는 덮어쓰지 않습니다.

        계산 방식 (web_api.py의 save_daily_snapshot과 동일):
        - 해당 날짜 기준 '활성 거래' = 매수일 <= day인 모든 거래
          - SOLD: sell_day <= day → 확정 return_rate 사용
          - HOLD(당시 기준): 당일 종가 기준 수익률 (pykrx 조회, SQLite 캐시)
        - 전략별 평균 return_rate 저장
        """
        df = self._read()
        if df.empty:
            return

        data = self._load_data()
        daily = data["daily"]

        # 1. 날짜 전처리
        df['buy_day_str'] = pd.to_datetime(df['buy_date'], errors='coerce').dt.strftime('%Y-%m-%d')
        sell_mask = df['sell_date'].notna() & (df['sell_date'] != '')
        df['sell_day_str'] = None
        sell_dt = pd.to_datetime(df.loc[sell_mask, 'sell_date'], errors='coerce')
        valid_sell = sell_mask & sell_dt.notna().reindex(df.index, fill_value=False)
        df.loc[valid_sell, 'sell_day_str'] = sell_dt.dropna().dt.strftime('%Y-%m-%d')

        all_days = set(df['buy_day_str'].dropna().tolist())
        all_days |= set(df.loc[sell_mask, 'sell_day_str'].dropna().tolist())

        if not all_days:
            return

        min_day = min(all_days)
        max_day = max(all_days)

        # 현재 시점(어제)까지 backfill 범위 확장 (보유 중인 경우 등 고려)
        yesterday = (self.tm.get_current_kst_time() - timedelta(days=1)).strftime('%Y-%m-%d')
        if yesterday > max_day:
            max_day = yesterday

        # backfill이 필요한 날짜 확인
        date_range = pd.date_range(min_day, max_day, freq='D')
        date_strs = [d.strftime('%Y-%m-%d') for d in date_range]
        missing_days = [d for d in date_strs if d not in daily]

        if not missing_days:
            return  # backfill 불필요

        # 2. 종가 캐시 조회 (HOLD 포지션 수익률 계산용)
        all_codes = df['code'].unique().tolist()
        price_cache = self._fetch_close_prices(all_codes, min_day, max_day)

        # 종가 데이터를 DataFrame으로 변환하고 전처리 (ffill)
        price_df = pd.DataFrame()
        if price_cache:
            try:
                price_df = pd.DataFrame(price_cache)
                price_df.index = pd.to_datetime(price_df.index)
                price_df = price_df.sort_index()
                full_idx = pd.date_range(start=min_day, end=max_day)
                price_df = price_df.reindex(full_idx).ffill()
            except Exception as e:
                logger.warning(f"[가상매매] 종가 데이터프레임 변환 실패: {e}")
                price_df = pd.DataFrame()

        # 3. 날짜별 스냅샷 생성 (Numpy Optimization)
        added = 0
        missing_days.sort()
        n_days = len(missing_days)

        strategies = sorted(df['strategy'].unique().tolist())
        strat_to_idx = {s: i for i, s in enumerate(strategies)}
        n_strats = len(strategies)

        buy_sums = np.zeros((n_days, n_strats), dtype=np.float64)
        eval_sums = np.zeros((n_days, n_strats), dtype=np.float64)

        price_matrix = None
        code_to_idx = {}

        if not price_df.empty:
            md_dt = pd.to_datetime(missing_days)
            price_df_aligned = price_df.reindex(md_dt)
            codes = price_df_aligned.columns.tolist()
            code_to_idx = {c: i for i, c in enumerate(codes)}
            price_matrix = price_df_aligned.to_numpy(dtype=np.float64)

        for row in df.itertuples():
            strat = row.strategy
            s_idx = strat_to_idx.get(strat)
            if s_idx is None:
                continue

            code = row.code
            try:
                qty = float(row.qty) if hasattr(row, 'qty') and pd.notna(row.qty) else 1.0
            except (ValueError, TypeError):
                qty = 1.0
            bp = float(row.buy_price) if pd.notna(row.buy_price) else 0.0
            if bp == 0:
                continue

            buy_date = row.buy_day_str
            sell_date = row.sell_day_str if row.status == 'SOLD' else None

            start_idx = bisect.bisect_left(missing_days, buy_date)
            if start_idx >= n_days:
                continue

            if sell_date:
                sell_idx = bisect.bisect_left(missing_days, sell_date)

                # Period 2: SOLD (from sell_idx onwards)
                if sell_idx < n_days:
                    sp = float(row.sell_price) if pd.notna(row.sell_price) else 0.0
                    val = sp if sp > 0 else bp
                    s_start = max(start_idx, sell_idx)
                    if s_start < n_days:
                        buy_sums[s_start:, s_idx] += (bp * qty)
                        eval_sums[s_start:, s_idx] += (val * qty)

                # Period 1: HOLD (from start_idx to sell_idx)
                h_end = min(sell_idx, n_days)
                if start_idx < h_end:
                    buy_sums[start_idx:h_end, s_idx] += (bp * qty)
                    c_idx = code_to_idx.get(code)
                    if c_idx is not None and price_matrix is not None:
                        prices = price_matrix[start_idx:h_end, c_idx]
                        if np.isnan(prices).any():
                            prices = prices.copy()
                            prices[np.isnan(prices)] = bp
                        eval_sums[start_idx:h_end, s_idx] += (prices * qty)
                    else:
                        eval_sums[start_idx:h_end, s_idx] += (bp * qty)
            else:
                # HOLD until end
                buy_sums[start_idx:, s_idx] += (bp * qty)
                c_idx = code_to_idx.get(code)
                if c_idx is not None and price_matrix is not None:
                    prices = price_matrix[start_idx:, c_idx]
                    if np.isnan(prices).any():
                        prices = prices.copy()
                        prices[np.isnan(prices)] = bp
                    eval_sums[start_idx:, s_idx] += (prices * qty)
                else:
                    eval_sums[start_idx:, s_idx] += (bp * qty)

        # Calculate Returns
        with np.errstate(divide='ignore', invalid='ignore'):
            returns = ((eval_sums - buy_sums) / buy_sums) * 100
        returns[~np.isfinite(returns)] = 0.0
        returns = np.round(returns, 2)

        # Calculate ALL
        total_buy = buy_sums.sum(axis=1)
        total_eval = eval_sums.sum(axis=1)
        with np.errstate(divide='ignore', invalid='ignore'):
            all_returns = ((total_eval - total_buy) / total_buy) * 100
        all_returns[~np.isfinite(all_returns)] = 0.0
        all_returns = np.round(all_returns, 2)

        # Build result dict
        for i, day_str in enumerate(missing_days):
            if total_buy[i] > 0:
                day_stats = {}
                for j, strat in enumerate(strategies):
                    if buy_sums[i, j] > 0:
                        day_stats[strat] = returns[i, j]

                day_stats['ALL'] = all_returns[i]
                daily[day_str] = day_stats
                added += 1

        if added > 0:
            cutoff = (self.tm.get_current_kst_time() - timedelta(days=30)).strftime("%Y-%m-%d")
            data["daily"] = {d: v for d, v in sorted(daily.items()) if d >= cutoff}
            self._save_data(data)
            logger.info(f"[가상매매] 스냅샷 backfill 완료: {added}일 추가")

    @staticmethod
    def _find_prev_close(price_cache: dict, code: str, day: str):
        """해당 날짜 이전 가장 가까운 종가를 찾습니다 (휴장일 대응)."""
        code_prices = price_cache.get(code)
        if not code_prices:
            return None
        prev_dates = sorted([d for d in code_prices if d < day], reverse=True)
        return code_prices[prev_dates[0]] if prev_dates else None

    # ---- 포트폴리오 스냅샷 (전일/전주대비 계산용) ----
    #
    # SQLite snapshots 테이블 구조:
    # (date, strategy, return_rate)  ← PRIMARY KEY (date, strategy)
    # _load_data() / _save_data() 는 하위 호환을 위해 dict 구조를 유지:
    # { "daily": {"2026-02-13": {"ALL": 2.5, "수동매매": 2.5}, ...}, "prev_values": {} }

    def _load_data(self) -> dict:
        """메모리 캐시 우선, 없으면 SQLite에서 로드."""
        if self._cached_data is not None:
            return self._cached_data

        rows = self._db.execute(
            "SELECT date, strategy, return_rate FROM snapshots ORDER BY date"
        ).fetchall()
        daily: dict = {}
        for date, strategy, return_rate in rows:
            if date not in daily:
                daily[date] = {}
            daily[date][strategy] = return_rate

        self._cached_data = {"daily": daily, "prev_values": {}}
        return self._cached_data

    def _save_data(self, data: dict):
        daily = data.get("daily", {})
        try:
            existing_dates = {row[0] for row in self._db.execute("SELECT DISTINCT date FROM snapshots")}
            with self._db:
                for date in existing_dates - set(daily.keys()):
                    self._db.execute("DELETE FROM snapshots WHERE date=?", (date,))
                for date, strategies in daily.items():
                    self._db.execute("DELETE FROM snapshots WHERE date=?", (date,))
                    self._db.executemany(
                        "INSERT INTO snapshots (date, strategy, return_rate) VALUES (?, ?, ?)",
                        [(date, strategy, rr) for strategy, rr in strategies.items()]
                    )
            self._cached_data = data
        except Exception as e:
            logger.error(f"Failed to save snapshot: {e}")

    def save_daily_snapshot(self, strategy_returns: dict):
        """오늘 스냅샷 저장. 성능 최적화 버전."""
        now = self.tm.get_current_kst_time()
        if now.weekday() >= 5:  # 주말 제외
            return

        today = now.strftime("%Y-%m-%d")

        data = self._load_data()
        daily = data.get("daily", {})

        if daily:
            prev_dates = [d for d in daily if d < today]
            if prev_dates:
                last_date = max(prev_dates)
                if _is_weekday(last_date):
                    last_snapshot = daily[last_date]
                    if _strategy_values(last_snapshot) == _strategy_values(strategy_returns):
                        return

        daily[today] = strategy_returns

        cutoff_dt = now - timedelta(days=30)
        cutoff_str = cutoff_dt.strftime("%Y-%m-%d")
        new_daily = {d: v for d, v in daily.items() if d >= cutoff_str}
        data["daily"] = new_daily

        self._save_data(data)

    def get_daily_change(self, strategy: str, current_return: float, *, _data: dict | None = None) -> tuple[float | None, str | None]:
        data = _data or self._load_data()
        daily = data.get("daily", {})
        if not daily:
            return None, None

        today = self.tm.get_current_kst_time().strftime("%Y-%m-%d")

        all_trading = _get_trading_dates(daily)
        trading = [d for d in all_trading if d <= today]
        if len(trading) < 2:
            return None, None

        latest_date = trading[-1]
        prev_date = trading[-2]

        latest_val = daily[latest_date].get(strategy)
        prev_val = daily[prev_date].get(strategy)

        if latest_val is None or prev_val is None:
            return None, None
        return round(latest_val - prev_val, 2), prev_date

    def get_weekly_change(self, strategy: str, current_return: float, *, _data: dict | None = None) -> tuple[float | None, str | None]:
        """7일 전 거래일 스냅샷 대비 변화. (변동값, 기준날짜) 튜플 반환."""
        data = _data or self._load_data()
        daily = data.get("daily", {})
        if not daily:
            return None, None

        today = self.tm.get_current_kst_time().strftime("%Y-%m-%d")
        target = (self.tm.get_current_kst_time() - timedelta(days=7)).strftime("%Y-%m-%d")

        sorted_dates = sorted(daily.keys())
        candidates = [d for d in sorted_dates if d <= target and d != today]

        if not candidates:
            return None, None

        ref_date = candidates[-1]
        ref_val = daily[ref_date].get(strategy)
        if ref_val is None:
            return None, None
        return round(current_return - ref_val, 2), ref_date

    def get_strategy_return_history(self, strategy_name: str) -> list[dict]:
        data = self._load_data()
        daily = data.get("daily", {})
        return _build_strategy_return_history(daily, strategy_name)

    def get_all_strategies(self) -> list[str]:
        data = self._load_data()
        daily = data.get("daily", {})

        strategies = set()
        if daily:
            recent_dates = sorted(daily.keys(), reverse=True)[:5]  # 최근 5거래일만 확인
            for date in recent_dates:
                strategies.update(daily[date].keys())

        trade_rows = self._db.execute(
            "SELECT DISTINCT strategy FROM trades WHERE strategy != '' AND strategy != 'ALL' AND status != 'FAILED'"
        ).fetchall()
        strategies.update(row[0] for row in trade_rows if row and row[0])

        if "ALL" in strategies:
            strategies.remove("ALL")
        return sorted(list(strategies))
