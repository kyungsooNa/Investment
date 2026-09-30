import json
import sqlite3
from datetime import datetime

import pytest

from common.overseas_types import OverseasExchange
from repositories.overseas_trade_repository import OverseasTradeRepository
from services.corporate_action_service import CorporateActionService


def _make_trade_db(path):
    with sqlite3.connect(path) as conn:
        conn.executescript(
            "CREATE TABLE overseas_trades ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL, "
            "buy_date TEXT NOT NULL, buy_price REAL NOT NULL, qty INTEGER NOT NULL, "
            "sell_date TEXT, status TEXT NOT NULL);"
        )


def _insert_trade(path, *, symbol="AAPL", buy_date="2026-08-01 10:00:00",
                  buy_price=200.0, qty=3, sell_date=None, status="HOLD"):
    with sqlite3.connect(path) as conn:
        conn.execute(
            "INSERT INTO overseas_trades "
            "(symbol, buy_date, buy_price, qty, sell_date, status) VALUES (?, ?, ?, ?, ?, ?)",
            (symbol, buy_date, buy_price, qty, sell_date, status),
        )


def test_apply_split_adjusts_only_eligible_holds_and_is_idempotent(tmp_path):
    db_path = tmp_path / "overseas.db"
    _make_trade_db(db_path)
    _insert_trade(db_path, qty=3, buy_price=200.0)
    _insert_trade(db_path, qty=2, buy_price=190.0, status="SOLD", sell_date="2026-08-10 10:00:00")
    _insert_trade(db_path, qty=1, buy_price=100.0, buy_date="2026-08-15 10:00:00")
    service = CorporateActionService(
        db_path=db_path,
        now_provider=lambda: datetime(2026, 8, 15, 12, 0, 0),
    )

    first = service.apply_split("aapl", "2026-08-15", numerator=4, denominator=1)
    second = service.apply_split("AAPL", "2026-08-15", numerator=4, denominator=1)

    assert first["applied"] is True
    assert first["affected_lots"] == 1
    assert second["applied"] is False
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT buy_price, qty, status FROM overseas_trades ORDER BY id"
        ).fetchall()
        actions = conn.execute("SELECT COUNT(*) FROM corporate_actions").fetchone()[0]
    assert rows == [(50.0, 12, "HOLD"), (190.0, 2, "SOLD"), (100.0, 1, "HOLD")]
    assert rows[0][0] * rows[0][1] == 600.0
    assert actions == 1


def test_reverse_split_rejects_fractional_shares_without_partial_update(tmp_path):
    db_path = tmp_path / "overseas.db"
    _make_trade_db(db_path)
    _insert_trade(db_path, qty=3, buy_price=20.0)
    service = CorporateActionService(db_path=db_path)

    with pytest.raises(ValueError, match="소수주"):
        service.apply_split("AAPL", "2026-08-15", numerator=1, denominator=2)

    with sqlite3.connect(db_path) as conn:
        trade = conn.execute("SELECT buy_price, qty FROM overseas_trades").fetchone()
        actions = conn.execute("SELECT COUNT(*) FROM corporate_actions").fetchone()[0]
    assert trade == (20.0, 3)
    assert actions == 0


def test_apply_split_updates_strategy_state_once(tmp_path):
    db_path = tmp_path / "overseas.db"
    _make_trade_db(db_path)
    _insert_trade(db_path, qty=2, buy_price=100.0)
    state_path = tmp_path / "overseas_intraday_vbo_state.json"
    state_path.write_text(json.dumps({
        "positions": {
            "AAPL": {
                "qty": 2,
                "entry_price": 100.0,
                "stop_price": 90.0,
                "last_price": 110.0,
            }
        }
    }), encoding="utf-8")
    service = CorporateActionService(db_path=db_path, state_paths=(state_path,))

    first = service.apply_split("AAPL", "2026-08-15", numerator=2, denominator=1)
    second = service.apply_split("AAPL", "2026-08-15", numerator=2, denominator=1)

    state = json.loads(state_path.read_text(encoding="utf-8"))
    held = state["positions"]["AAPL"]
    assert held == {
        "qty": 4,
        "entry_price": 50.0,
        "stop_price": 45.0,
        "last_price": 55.0,
    }
    assert len(state["applied_corporate_actions"]) == 1
    assert first["state_files_updated"] == 1
    assert second["state_files_updated"] == 0


def test_record_cash_dividend_uses_ex_date_eligible_quantity_and_is_idempotent(tmp_path):
    db_path = tmp_path / "overseas.db"
    _make_trade_db(db_path)
    _insert_trade(db_path, qty=3, buy_price=100.0)
    _insert_trade(
        db_path,
        qty=2,
        buy_price=100.0,
        status="SOLD",
        sell_date="2026-08-15 13:00:00",
    )
    _insert_trade(
        db_path,
        qty=5,
        buy_price=100.0,
        status="SOLD",
        sell_date="2026-08-14 13:00:00",
    )
    service = CorporateActionService(db_path=db_path)

    first = service.record_cash_dividend("AAPL", "2026-08-15", amount_per_share=0.25)
    second = service.record_cash_dividend("AAPL", "2026-08-15", amount_per_share=0.25)

    assert first["applied"] is True
    assert first["eligible_qty"] == 5
    assert first["cash_amount"] == 1.25
    assert second["applied"] is False
    assert service.list_actions()[0]["action_type"] == "CASH_DIVIDEND"


def test_external_id_cannot_be_reused_for_a_different_action(tmp_path):
    db_path = tmp_path / "overseas.db"
    _make_trade_db(db_path)
    service = CorporateActionService(db_path=db_path)
    service.apply_split(
        "AAPL",
        "2026-08-15",
        numerator=2,
        denominator=1,
        external_id="vendor-42",
    )

    with pytest.raises(ValueError, match="다른 기업행사"):
        service.apply_split(
            "MSFT",
            "2026-08-15",
            numerator=2,
            denominator=1,
            external_id="vendor-42",
        )


def test_split_is_visible_through_overseas_trade_repository(tmp_path):
    db_path = tmp_path / "overseas.db"
    repo = OverseasTradeRepository(
        db_path=str(db_path),
        now_provider=lambda: datetime(2026, 8, 1, 10, 0, 0),
    )
    repo.log_buy("AAPL", OverseasExchange.NASD, 200.0, 2, source="manual")
    service = CorporateActionService(db_path=db_path)

    service.apply_split("AAPL", "2026-08-15", numerator=4, denominator=1)

    hold = repo.get_holds()[0]
    assert hold["qty"] == 8
    assert hold["buy_price"] == 50.0
    repo._db.close()


def test_split_does_not_adjust_state_when_no_pre_effective_date_hold_exists(tmp_path):
    db_path = tmp_path / "overseas.db"
    _make_trade_db(db_path)
    _insert_trade(db_path, qty=2, buy_price=100.0, buy_date="2026-08-16 10:00:00")
    state_path = tmp_path / "overseas_intraday_vbo_state.json"
    original = {"positions": {"AAPL": {"qty": 2, "entry_price": 100.0}}}
    state_path.write_text(json.dumps(original), encoding="utf-8")
    service = CorporateActionService(db_path=db_path, state_paths=(state_path,))

    result = service.apply_split("AAPL", "2026-08-15", numerator=2, denominator=1)

    assert result["affected_lots"] == 0
    assert result["state_files_updated"] == 0
    assert json.loads(state_path.read_text(encoding="utf-8")) == original
