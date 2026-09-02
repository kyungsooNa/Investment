"""미국장 전용 USD 원장 테스트.

핵심 계약 2가지:
1. **부분 매도 분할** — PR #700 의 진범이 `log_sell` 이 qty 를 무시하고 lot 전체를
   SOLD 로 뒤집은 것이었다. 잔량이 원장에서 사라지면 다음 대사에서 고아가 된다.
2. **국내 비용 모델 미사용** — 증권거래세 0.2% 는 한국 전용이다. 미국은 온라인
   수수료 0.25%/side(왕복 0.5%) 기준으로 계산해야 한다.
"""
import pytest

from common.overseas_types import OverseasExchange
from repositories.overseas_trade_repository import OverseasTradeRepository


@pytest.fixture
def repo(tmp_path):
    return OverseasTradeRepository(db_path=str(tmp_path / "overseas_trade.db"))


def test_log_buy_creates_hold_lot(repo):
    repo.log_buy("AAPL", OverseasExchange.NASD, 190.0, 3, source="manual")

    holds = repo.get_holds()
    assert len(holds) == 1
    assert holds[0]["symbol"] == "AAPL"
    assert holds[0]["exchange"] == "NASD"
    assert holds[0]["currency"] == "USD"
    assert holds[0]["qty"] == 3
    assert holds[0]["status"] == "HOLD"


def test_repeated_buy_keeps_separate_lots(repo):
    """같은 심볼 재매수는 평단 병합이 아니라 별도 lot — 진입가별 청산 추적을 유지한다."""
    repo.log_buy("AAPL", OverseasExchange.NASD, 190.0, 3)
    repo.log_buy("AAPL", OverseasExchange.NASD, 200.0, 2)

    holds = repo.get_holds()
    assert [h["qty"] for h in holds] == [3, 2]
    assert [h["buy_price"] for h in holds] == [190.0, 200.0]


def test_partial_sell_keeps_remainder_as_hold(repo):
    """체결분만 SOLD 로 분리하고 잔량은 HOLD 로 남는다 (#700 회귀 잠금)."""
    repo.log_buy("AAPL", OverseasExchange.NASD, 190.0, 10)

    repo.log_sell("AAPL", 200.0, qty=4)

    holds = repo.get_holds()
    assert len(holds) == 1
    assert holds[0]["qty"] == 6

    solds = [t for t in repo.get_all_trades() if t["status"] == "SOLD"]
    assert len(solds) == 1
    assert solds[0]["qty"] == 4
    assert solds[0]["sell_price"] == 200.0
    assert solds[0]["buy_price"] == 190.0


def test_full_sell_closes_lot(repo):
    repo.log_buy("AAPL", OverseasExchange.NASD, 190.0, 5)

    repo.log_sell("AAPL", 200.0)

    assert repo.get_holds() == []
    solds = [t for t in repo.get_all_trades() if t["status"] == "SOLD"]
    assert len(solds) == 1
    assert solds[0]["qty"] == 5


def test_sell_spans_oldest_lots_first(repo):
    """여러 lot 보유 시 오래된 lot 부터 청산한다."""
    repo.log_buy("AAPL", OverseasExchange.NASD, 190.0, 3)
    repo.log_buy("AAPL", OverseasExchange.NASD, 200.0, 4)

    repo.log_sell("AAPL", 210.0, qty=5)

    holds = repo.get_holds()
    assert [(h["buy_price"], h["qty"]) for h in holds] == [(200.0, 2)]
    solds = sorted(
        (t for t in repo.get_all_trades() if t["status"] == "SOLD"),
        key=lambda t: t["buy_price"],
    )
    assert [(s["buy_price"], s["qty"]) for s in solds] == [(190.0, 3), (200.0, 2)]


def test_sell_more_than_held_closes_only_held(repo):
    repo.log_buy("AAPL", OverseasExchange.NASD, 190.0, 2)

    result = repo.log_sell("AAPL", 200.0, qty=10)

    assert result.sold_qty == 2
    assert repo.get_holds() == []


def test_sell_without_position_is_noop(repo):
    result = repo.log_sell("AAPL", 200.0, qty=1)

    assert result.sold_qty == 0
    assert repo.get_all_trades() == []


def test_summary_counts_only_sold(repo):
    repo.log_buy("AAPL", OverseasExchange.NASD, 100.0, 1)
    repo.log_buy("MSFT", OverseasExchange.NASD, 100.0, 1)
    repo.log_sell("AAPL", 110.0)

    summary = repo.get_summary()

    assert summary["total_trades"] == 2   # HOLD + SOLD
    assert summary["sold_trades"] == 1
    assert summary["win_rate"] == 100.0


def test_return_uses_us_cost_model_not_domestic_tax(repo):
    """국내 증권거래세(0.2%)가 아니라 미국 왕복 수수료 0.5% 를 반영한다."""
    gross = repo.calculate_return(100.0, 110.0, apply_cost=False)
    net = repo.calculate_return(100.0, 110.0, apply_cost=True)

    assert gross == 10.0
    # 매수 100*0.25% + 매도 110*0.25% = 0.525 → (110-100-0.525)/100 = 9.475%
    assert net == pytest.approx(9.48, abs=0.01)


def test_sold_row_stores_net_return(repo):
    repo.log_buy("AAPL", OverseasExchange.NASD, 100.0, 1)
    repo.log_sell("AAPL", 110.0)

    sold = [t for t in repo.get_all_trades() if t["status"] == "SOLD"][0]
    assert sold["return_rate"] == pytest.approx(9.48, abs=0.01)


# --- 체결 대사 (Phase 2) ---
#
# 기록 시점이 *주문 접수* 이라 미체결 지정가도 HOLD 로 잡힌다. 대사가 lot 단위로
# 성립하려면 주문번호가 있어야 한다 — 같은 심볼의 lot 이 여러 개면 수량 총합만으로는
# 어느 lot 이 미체결인지 구분할 수 없다.


def test_log_buy_stores_order_no(repo):
    repo.log_buy("AAPL", OverseasExchange.NASD, 190.0, 3, source="manual", order_no="0001234")

    assert repo.get_holds()[0]["order_no"] == "0001234"


def test_order_no_defaults_to_empty(repo):
    """주문번호 없이 기록된 lot 도 유효하다(대사 대상에서 빠질 뿐)."""
    repo.log_buy("AAPL", OverseasExchange.NASD, 190.0, 3)

    assert repo.get_holds()[0]["order_no"] == ""


def test_mark_canceled_removes_from_holds_without_deleting_row(repo):
    """미체결 lot 은 행을 지우지 않고 CANCELED 로 표시한다 — 기록 파괴 금지."""
    repo.log_buy("AAPL", OverseasExchange.NASD, 190.0, 3, order_no="0001234")
    trade_id = repo.get_holds()[0]["id"]

    repo.mark_canceled(trade_id, reason="fill_reconcile: 미체결")

    assert repo.get_holds() == []
    rows = repo.get_all_trades()
    assert len(rows) == 1
    assert rows[0]["status"] == "CANCELED"
    assert rows[0]["reason"] == "fill_reconcile: 미체결"


def test_mark_canceled_ignores_non_hold_row(repo):
    """이미 청산된 lot 을 취소로 뒤집으면 실현 성과가 사라진다."""
    repo.log_buy("AAPL", OverseasExchange.NASD, 100.0, 1)
    repo.log_sell("AAPL", 110.0)
    trade_id = repo.get_all_trades()[0]["id"]

    assert repo.mark_canceled(trade_id, reason="fill_reconcile: 미체결") is False
    assert repo.get_all_trades()[0]["status"] == "SOLD"


def test_adjust_qty_shrinks_partially_filled_lot(repo):
    repo.log_buy("AAPL", OverseasExchange.NASD, 190.0, 5, order_no="0001234")
    trade_id = repo.get_holds()[0]["id"]

    assert repo.adjust_qty(trade_id, 2, reason="fill_reconcile: 부분체결") is True

    holds = repo.get_holds()
    assert holds[0]["qty"] == 2
    assert holds[0]["reason"] == "fill_reconcile: 부분체결"


def test_adjust_qty_rejects_increase(repo):
    """대사는 과다 기록을 줄이는 방향만 허용한다 — 늘리면 없는 보유를 만든다."""
    repo.log_buy("AAPL", OverseasExchange.NASD, 190.0, 2, order_no="0001234")
    trade_id = repo.get_holds()[0]["id"]

    assert repo.adjust_qty(trade_id, 5, reason="fill_reconcile") is False
    assert repo.get_holds()[0]["qty"] == 2


def test_adjust_qty_to_zero_marks_canceled(repo):
    repo.log_buy("AAPL", OverseasExchange.NASD, 190.0, 2, order_no="0001234")
    trade_id = repo.get_holds()[0]["id"]

    assert repo.adjust_qty(trade_id, 0, reason="fill_reconcile: 미체결") is True

    assert repo.get_holds() == []
    assert repo.get_all_trades()[0]["status"] == "CANCELED"


def test_summary_excludes_canceled_lots(repo):
    """취소 lot 이 total 에 남으면 성과 분모가 부풀려진다."""
    repo.log_buy("AAPL", OverseasExchange.NASD, 100.0, 1)
    repo.log_buy("MSFT", OverseasExchange.NASD, 100.0, 1, order_no="0009999")
    repo.mark_canceled(repo.get_holds()[1]["id"], reason="fill_reconcile: 미체결")

    summary = repo.get_summary()

    assert summary["total_trades"] == 1
    assert summary["canceled_trades"] == 1


def test_existing_db_without_order_no_is_migrated(tmp_path):
    """order_no 도입 이전 DB 도 재기동 시 그대로 열려야 한다."""
    import sqlite3

    db_path = str(tmp_path / "legacy.db")
    conn = sqlite3.connect(db_path)
    conn.executescript(
        "CREATE TABLE overseas_trades ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL, exchange TEXT NOT NULL,"
        "currency TEXT NOT NULL DEFAULT 'USD', buy_date TEXT NOT NULL, buy_price REAL NOT NULL,"
        "qty INTEGER NOT NULL, sell_date TEXT, sell_price REAL,"
        "return_rate REAL NOT NULL DEFAULT 0.0, status TEXT NOT NULL,"
        "reason TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT '');"
        "INSERT INTO overseas_trades (symbol, exchange, buy_date, buy_price, qty, status) "
        "VALUES ('AAPL', 'NASD', '2026-08-13 10:00:00', 190.0, 3, 'HOLD');"
    )
    conn.commit()
    conn.close()

    repo = OverseasTradeRepository(db_path=db_path)

    holds = repo.get_holds()
    assert len(holds) == 1
    assert holds[0]["order_no"] == ""


# ── source 필터 매도 (자동 전략 lot 과 수동 lot 분리) ─────────────────────────

def test_log_sell_source_filter_only_closes_matching_lots(tmp_path):
    """전략 청산이 같은 심볼의 수동 lot 을 대신 닫으면 안 된다.

    원장이 심볼만으로 오래된 lot 부터 닫으므로, 자동 전략 기록이 들어오기 시작하면
    수동 보유가 전략 청산에 휩쓸린다.
    """
    repo = OverseasTradeRepository(db_path=str(tmp_path / "t.db"))
    repo.log_buy("AAPL", OverseasExchange.NASD, 100.0, 2, source="manual")
    repo.log_buy("AAPL", OverseasExchange.NASD, 110.0, 3, source="OverseasIntradayVBO")

    result = repo.log_sell("AAPL", 120.0, reason="eod", source="OverseasIntradayVBO")

    assert result.sold_qty == 3
    holds = repo.get_holds()
    assert len(holds) == 1
    assert holds[0]["source"] == "manual"
    assert holds[0]["qty"] == 2


def test_log_sell_without_source_filter_keeps_existing_behavior(tmp_path):
    repo = OverseasTradeRepository(db_path=str(tmp_path / "t.db"))
    repo.log_buy("AAPL", OverseasExchange.NASD, 100.0, 2, source="manual")
    repo.log_buy("AAPL", OverseasExchange.NASD, 110.0, 3, source="OverseasIntradayVBO")

    result = repo.log_sell("AAPL", 120.0, reason="manual")

    assert result.sold_qty == 5
    assert repo.get_holds() == []


def test_log_sell_source_filter_with_no_matching_lot_is_noop(tmp_path):
    repo = OverseasTradeRepository(db_path=str(tmp_path / "t.db"))
    repo.log_buy("AAPL", OverseasExchange.NASD, 100.0, 2, source="manual")

    result = repo.log_sell("AAPL", 120.0, reason="eod", source="OverseasIntradayVBO")

    assert result.sold_qty == 0
    assert len(repo.get_holds()) == 1


@pytest.mark.asyncio
async def test_log_sell_async_forwards_source(tmp_path):
    repo = OverseasTradeRepository(db_path=str(tmp_path / "t.db"))
    repo.log_buy("AAPL", OverseasExchange.NASD, 100.0, 2, source="manual")
    repo.log_buy("AAPL", OverseasExchange.NASD, 110.0, 3, source="OverseasIntradayVBO")

    result = await repo.log_sell_async("AAPL", 120.0, reason="eod", source="OverseasIntradayVBO")

    assert result.sold_qty == 3
    assert repo.get_holds()[0]["source"] == "manual"
