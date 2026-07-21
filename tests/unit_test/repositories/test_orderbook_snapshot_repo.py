import sqlite3

from repositories.orderbook_snapshot_repo import OrderbookSnapshotRepository


def test_record_tick_persists_sampled_top_of_book(tmp_path):
    repo = OrderbookSnapshotRepository(
        base_dir=str(tmp_path),
        sample_interval_sec=60.0,
    )
    try:
        accepted = repo.record_tick(
            "005930",
            {
                "주식체결시간": "101500",
                "영업일자": "20260721",
                "매도호가1": "71100",
                "매수호가1": "71000",
                "매도호가잔량": "1200",
                "매수호가잔량": "900",
                "총매도호가잔량": "15000",
                "총매수호가잔량": "18000",
            },
            now=1000.0,
        )
        duplicate = repo.record_tick(
            "005930",
            {
                "주식체결시간": "101530",
                "영업일자": "20260721",
                "매도호가1": "71200",
                "매수호가1": "71100",
                "매도호가잔량": "1000",
                "매수호가잔량": "800",
                "총매도호가잔량": "14000",
                "총매수호가잔량": "17000",
            },
            now=1030.0,
        )
        repo.flush()

        with sqlite3.connect(tmp_path / "orderbook_snapshots.db") as conn:
            rows = conn.execute(
                "SELECT code, trade_date, trade_time, ask_price, bid_price, "
                "ask_qty, bid_qty, total_ask_qty, total_bid_qty "
                "FROM top_of_book_history"
            ).fetchall()
    finally:
        repo.close()

    assert accepted is True
    assert duplicate is False
    assert rows == [
        ("005930", "20260721", "101500", 71100, 71000, 1200, 900, 15000, 18000)
    ]


def test_record_tick_rejects_missing_or_crossed_quotes(tmp_path):
    repo = OrderbookSnapshotRepository(base_dir=str(tmp_path), sample_interval_sec=0)
    try:
        assert repo.record_tick("005930", {"주식체결시간": "101500"}, now=1.0) is False
        assert repo.record_tick(
            "005930",
            {
                "주식체결시간": "101500",
                "매도호가1": "70000",
                "매수호가1": "71000",
            },
            now=2.0,
        ) is False
    finally:
        repo.close()
