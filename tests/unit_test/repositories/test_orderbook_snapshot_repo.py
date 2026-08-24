import sqlite3
import time
from unittest.mock import MagicMock, patch

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


def _tick(**overrides):
    row = {
        "주식체결시간": "101500",
        "영업일자": "20260721",
        "매도호가1": "71100",
        "매수호가1": "71000",
        "매도호가잔량": "1200",
        "매수호가잔량": "900",
    }
    row.update(overrides)
    return row


def test_db_init_failure_disables_recording(tmp_path):
    logger = MagicMock()

    with patch(
        "repositories.orderbook_snapshot_repo.sqlite3.connect",
        side_effect=sqlite3.Error("DB 잠김"),
    ):
        repo = OrderbookSnapshotRepository(base_dir=str(tmp_path), logger=logger)

    assert repo._conn is None
    assert repo.record_tick("005930", _tick(), now=1.0) is False
    repo.flush()   # 연결이 없어도 안전해야 한다
    repo.close()
    logger.error.assert_called_once()


def test_record_tick_rejects_blank_code_and_non_dict_payloads(tmp_path):
    repo = OrderbookSnapshotRepository(base_dir=str(tmp_path), sample_interval_sec=0)
    try:
        assert repo.record_tick("", _tick(), now=1.0) is False
        assert repo.record_tick("005930", "틱 아님", now=1.0) is False
    finally:
        repo.close()


def test_record_tick_respects_the_sampling_interval(tmp_path):
    repo = OrderbookSnapshotRepository(base_dir=str(tmp_path), sample_interval_sec=60.0)
    try:
        assert repo.record_tick("005930", _tick(), now=1000.0) is True
        # 같은 종목의 후속 틱은 샘플링 간격 안이면 버린다.
        assert repo.record_tick("005930", _tick(), now=1030.0) is False
        assert repo.record_tick("005930", _tick(), now=1070.0) is True
    finally:
        repo.close()


def test_record_tick_rejects_a_malformed_trade_time(tmp_path):
    repo = OrderbookSnapshotRepository(base_dir=str(tmp_path), sample_interval_sec=0)
    try:
        assert repo.record_tick("005930", _tick(주식체결시간="10:15"), now=1.0) is False
    finally:
        repo.close()


def test_trade_date_falls_back_to_the_sampling_clock(tmp_path):
    repo = OrderbookSnapshotRepository(base_dir=str(tmp_path), sample_interval_sec=0)
    try:
        assert repo.record_tick("005930", _tick(영업일자=None), now=1_700_000_000.0) is True
        repo.flush()
        rows = repo._conn.execute("SELECT trade_date FROM top_of_book_history").fetchall()
    finally:
        repo.close()

    assert rows[0][0] == time.strftime("%Y%m%d", time.localtime(1_700_000_000.0))


def test_buffer_is_flushed_once_it_reaches_the_batch_size(tmp_path):
    repo = OrderbookSnapshotRepository(base_dir=str(tmp_path), sample_interval_sec=0)
    try:
        for i in range(OrderbookSnapshotRepository.FLUSH_BUFFER_SIZE):
            repo.record_tick(f"{i:06d}", _tick(), now=1000.0 + i)
        assert repo._buffer == []
        count = repo._conn.execute("SELECT COUNT(*) FROM top_of_book_history").fetchone()[0]
    finally:
        repo.close()

    assert count == OrderbookSnapshotRepository.FLUSH_BUFFER_SIZE


def test_flush_failure_is_logged_without_raising(tmp_path):
    logger = MagicMock()
    repo = OrderbookSnapshotRepository(
        base_dir=str(tmp_path), sample_interval_sec=0, logger=logger
    )
    try:
        repo.record_tick("005930", _tick(), now=1000.0)
        # 테이블을 지워 INSERT 가 sqlite3.Error 로 실패하게 만든다.
        repo._conn.execute("DROP TABLE top_of_book_history")
        repo.flush()
    finally:
        repo._conn = None  # close() 의 flush 가 다시 실패하지 않도록 끊는다

    logger.error.assert_called_once()


def test_close_is_idempotent(tmp_path):
    repo = OrderbookSnapshotRepository(base_dir=str(tmp_path))

    repo.close()
    repo.close()

    assert repo._conn is None


def test_integer_and_digit_parsers_reject_unusable_values():
    parse_int = OrderbookSnapshotRepository._parse_int
    digits = OrderbookSnapshotRepository._normalize_digits

    assert parse_int("100") == 100
    assert parse_int(None) is None
    assert parse_int("") is None
    assert parse_int("N/A") is None
    assert parse_int("숫자아님") is None

    assert digits("20260721", 8) == "20260721"
    assert digits("2026072", 8) is None
    assert digits("2026072X", 8) is None
    assert digits(None, 8) is None
