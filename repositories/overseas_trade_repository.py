# repositories/overseas_trade_repository.py
"""미국장 전용 USD 거래 원장 (SQLite).

`VirtualTradeRepository` 와 **의도적으로 분리**한다. 그쪽은 원화·국내 6자리 코드
기준이고 국내 브로커 잔고 대사에 물려 있어서, USD 체결을 섞으면 전략별 성과 통계와
대사 고아가 오염된다. 통화가 다른 원장은 아예 다른 파일로 둔다.

**기록 시점**: 해외는 국내 `FillReconciliationService` 같은 체결 대사 경로가 아직
없어서, *주문 접수 성공* 시점에 기록한다. 따라서 체결되지 않은 지정가 주문도
HOLD 로 잡힐 수 있다 — `get_overseas_order_history`(체결/미체결 조회) 기반 대사는
후속 작업이다.

**부분 매도**: `log_sell(qty=...)` 은 체결분만 SOLD 로 분리하고 잔량을 HOLD 로
남긴다. 국내 원장에서 이 처리를 빠뜨려(PR #700) 잔량이 원장에서 사라지고 다음
대사에서 고아로 재등록되던 결함이 있었다 — 같은 실수를 반복하지 않는다.
"""
from __future__ import annotations

import asyncio
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from common.overseas_types import OverseasExchange

# 미국주식 온라인 수수료(편도, %). 국내 증권거래세 0.2% 는 한국 전용이라 재사용 금지.
# 환전 스프레드·SEC/TAF 등 매도 제비용은 미반영 — commission_only 가정.
US_COMMISSION_PCT_PER_SIDE = 0.25

_DDL = """
CREATE TABLE IF NOT EXISTS overseas_trades (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol      TEXT    NOT NULL,
    exchange    TEXT    NOT NULL,
    currency    TEXT    NOT NULL DEFAULT 'USD',
    buy_date    TEXT    NOT NULL,
    buy_price   REAL    NOT NULL,
    qty         INTEGER NOT NULL,
    sell_date   TEXT,
    sell_price  REAL,
    return_rate REAL    NOT NULL DEFAULT 0.0,
    status      TEXT    NOT NULL,
    reason      TEXT    NOT NULL DEFAULT '',
    source      TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_overseas_trades_symbol_status
    ON overseas_trades(symbol, status);
"""

_COLUMNS = (
    "id", "symbol", "exchange", "currency", "buy_date", "buy_price", "qty",
    "sell_date", "sell_price", "return_rate", "status", "reason", "source",
)


@dataclass(frozen=True)
class OverseasSellResult:
    """매도 기록 결과. 실제로 청산된 수량과 lot 별 수익률을 노출한다."""
    sold_qty: int
    return_rates: tuple[float, ...] = ()


class OverseasTradeRepository:
    DEFAULT_DB_PATH = "data/OverseasTradeRepository/overseas_trade.db"

    def __init__(self, db_path: str | None = None, now_provider=None) -> None:
        self.db_path = db_path or self.DEFAULT_DB_PATH
        self._now = now_provider or datetime.now
        dir_path = os.path.dirname(self.db_path)
        if dir_path:
            os.makedirs(dir_path, exist_ok=True)
        self._db = sqlite3.connect(self.db_path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(_DDL)

    # ---- 기록 ----

    def log_buy(self, symbol: str, exchange, price, qty: int, source: str = "") -> None:
        """매수 lot 을 추가한다. 같은 심볼을 다시 사도 평단 병합 없이 별도 lot 으로 둔다
        (진입가별 청산 추적을 잃지 않기 위해)."""
        with self._db:
            self._db.execute(
                "INSERT INTO overseas_trades "
                "(symbol, exchange, currency, buy_date, buy_price, qty, status, source) "
                "VALUES (?, ?, 'USD', ?, ?, ?, 'HOLD', ?)",
                (
                    str(symbol).upper(),
                    self._exchange_value(exchange),
                    self._now().strftime("%Y-%m-%d %H:%M:%S"),
                    float(price),
                    int(qty),
                    source,
                ),
            )

    def log_sell(self, symbol: str, price, qty: int | None = None, reason: str = "") -> OverseasSellResult:
        """보유 lot 을 오래된 것부터 청산한다.

        `qty` 미지정이면 전량. 지정 수량이 lot 수량보다 작으면 **체결분만 SOLD 로
        분리하고 잔량은 HOLD 로 남긴다** — 잔량이 사라지면 관리 주체가 없어진다.
        """
        symbol = str(symbol).upper()
        sell_price = float(price)
        sold_date = self._now().strftime("%Y-%m-%d %H:%M:%S")

        holds = self._db.execute(
            "SELECT * FROM overseas_trades WHERE symbol=? AND status='HOLD' ORDER BY id",
            (symbol,),
        ).fetchall()
        if not holds:
            return OverseasSellResult(sold_qty=0)

        remaining = sum(int(row["qty"]) for row in holds) if qty is None else int(qty)
        if remaining <= 0:
            return OverseasSellResult(sold_qty=0)

        sold_qty = 0
        rates: list[float] = []
        with self._db:
            for row in holds:
                if remaining <= 0:
                    break
                held = int(row["qty"])
                take = min(held, remaining)
                rate = self.calculate_return(row["buy_price"], sell_price, apply_cost=True)

                if take == held:
                    self._db.execute(
                        "UPDATE overseas_trades SET status='SOLD', sell_date=?, sell_price=?, "
                        "return_rate=?, reason=? WHERE id=?",
                        (sold_date, sell_price, rate, reason, row["id"]),
                    )
                else:
                    # 부분 청산: 잔량 lot 을 줄이고 체결분을 별도 SOLD 행으로 분리한다.
                    self._db.execute(
                        "UPDATE overseas_trades SET qty=? WHERE id=?",
                        (held - take, row["id"]),
                    )
                    self._db.execute(
                        "INSERT INTO overseas_trades "
                        "(symbol, exchange, currency, buy_date, buy_price, qty, sell_date, "
                        "sell_price, return_rate, status, reason, source) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'SOLD', ?, ?)",
                        (
                            row["symbol"], row["exchange"], row["currency"], row["buy_date"],
                            row["buy_price"], take, sold_date, sell_price, rate, reason,
                            row["source"],
                        ),
                    )
                sold_qty += take
                remaining -= take
                rates.append(rate)

        return OverseasSellResult(sold_qty=sold_qty, return_rates=tuple(rates))

    async def log_buy_async(self, symbol: str, exchange, price, qty: int, source: str = "") -> None:
        await asyncio.to_thread(self.log_buy, symbol, exchange, price, qty, source)

    async def log_sell_async(self, symbol: str, price, qty: int | None = None,
                             reason: str = "") -> OverseasSellResult:
        return await asyncio.to_thread(self.log_sell, symbol, price, qty, reason)

    # ---- 조회 ----

    def get_all_trades(self) -> list[dict]:
        rows = self._db.execute("SELECT * FROM overseas_trades ORDER BY id").fetchall()
        return [self._to_dict(row) for row in rows]

    def get_holds(self) -> list[dict]:
        rows = self._db.execute(
            "SELECT * FROM overseas_trades WHERE status='HOLD' ORDER BY id"
        ).fetchall()
        return [self._to_dict(row) for row in rows]

    def get_summary(self) -> dict:
        """성과 요약. 승률/평균수익률은 청산된 거래만으로 계산한다."""
        rows = self._db.execute(
            "SELECT return_rate FROM overseas_trades WHERE status='SOLD'"
        ).fetchall()
        total = self._db.execute("SELECT COUNT(*) FROM overseas_trades").fetchone()[0]
        if not rows:
            return {"total_trades": total, "sold_trades": 0, "win_rate": 0.0, "avg_return": 0.0}

        returns = [float(row["return_rate"]) for row in rows]
        wins = sum(1 for r in returns if r > 0)
        return {
            "total_trades": total,
            "sold_trades": len(returns),
            "win_rate": round(wins / len(returns) * 100, 2),
            "avg_return": round(sum(returns) / len(returns), 2),
        }

    # ---- 계산 ----

    @staticmethod
    def calculate_return(buy_price, sell_price, apply_cost: bool = True) -> float:
        """수익률(%). apply_cost 면 왕복 수수료(편도 0.25%)를 차감한다."""
        buy = float(buy_price or 0)
        sell = float(sell_price or 0)
        if buy <= 0:
            return 0.0
        profit = sell - buy
        if apply_cost:
            rate = US_COMMISSION_PCT_PER_SIDE / 100
            profit -= (buy * rate) + (sell * rate)
        return round(profit / buy * 100, 2)

    # ---- 내부 ----

    @staticmethod
    def _exchange_value(exchange) -> str:
        if isinstance(exchange, OverseasExchange):
            return exchange.value
        return str(exchange).upper()

    @staticmethod
    def _to_dict(row: sqlite3.Row) -> dict:
        return {key: row[key] for key in _COLUMNS}
