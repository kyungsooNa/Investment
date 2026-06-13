"""DelistedOhlcvStore — 백필된 상폐 OHLCV CSV 서빙 (R-1 생존편향 연결층 2단계).

scripts/backfill_delisted_ohlcv.py 가 만든 종목별 CSV(date/open/high/low/close/volume)를
get_recent_daily_ohlcv 호환 행(date=YYYYMMDD 오름차순)으로 돌려준다.
synthetic CSV 로 검증한다(FDR 불필요).
"""
from __future__ import annotations

from pathlib import Path

from services.delisted_ohlcv_store import DelistedOhlcvStore

_CSV = (
    "date,symbol,open,high,low,close,volume,change\n"
    "2026-03-10,900100,1000,1100,950,1050,50000,50\n"
    "2026-03-11,900100,1050,1080,1000,1010,40000,-40\n"
    "2026-03-12,900100,1010,1020,800,820,120000,-190\n"
)


def _write_dir(tmp_path: Path) -> Path:
    d = tmp_path / "delisted_ohlcv"
    d.mkdir()
    (d / "900100.csv").write_text(_CSV, encoding="utf-8-sig")
    return d


def test_from_backfill_dir_loads_codes(tmp_path):
    store = DelistedOhlcvStore.from_backfill_dir(_write_dir(tmp_path))
    assert store.has("900100")
    assert store.codes() == {"900100"}
    assert not store.has("005930")


def test_rows_use_canonical_schema_ascending(tmp_path):
    store = DelistedOhlcvStore.from_backfill_dir(_write_dir(tmp_path))
    rows = store.get_daily_rows("900100")
    assert [r["date"] for r in rows] == ["20260310", "20260311", "20260312"]
    first = rows[0]
    assert first == {
        "date": "20260310",
        "open": 1000.0,
        "high": 1100.0,
        "low": 950.0,
        "close": 1050.0,
        "volume": 50000,
    }


def test_end_date_filters_future_rows(tmp_path):
    store = DelistedOhlcvStore.from_backfill_dir(_write_dir(tmp_path))
    rows = store.get_daily_rows("900100", end_date="20260311")
    assert [r["date"] for r in rows] == ["20260310", "20260311"]


def test_end_date_accepts_dashed_format(tmp_path):
    store = DelistedOhlcvStore.from_backfill_dir(_write_dir(tmp_path))
    rows = store.get_daily_rows("900100", end_date="2026-03-11")
    assert [r["date"] for r in rows] == ["20260310", "20260311"]


def test_limit_takes_most_recent_rows(tmp_path):
    store = DelistedOhlcvStore.from_backfill_dir(_write_dir(tmp_path))
    rows = store.get_daily_rows("900100", limit=2)
    assert [r["date"] for r in rows] == ["20260311", "20260312"]


def test_limit_and_end_date_combined(tmp_path):
    store = DelistedOhlcvStore.from_backfill_dir(_write_dir(tmp_path))
    rows = store.get_daily_rows("900100", limit=1, end_date="20260311")
    assert [r["date"] for r in rows] == ["20260311"]


def test_unknown_code_returns_empty(tmp_path):
    store = DelistedOhlcvStore.from_backfill_dir(_write_dir(tmp_path))
    assert store.get_daily_rows("000000") == []


def test_rows_without_date_are_skipped(tmp_path):
    d = tmp_path / "delisted_ohlcv"
    d.mkdir()
    (d / "900200.csv").write_text(
        "date,symbol,open,high,low,close,volume,change\n"
        ",900200,1,1,1,1,1,0\n"
        "2026-02-02,900200,10,11,9,10,100,0\n",
        encoding="utf-8-sig",
    )
    store = DelistedOhlcvStore.from_backfill_dir(d)
    rows = store.get_daily_rows("900200")
    assert [r["date"] for r in rows] == ["20260202"]


def test_empty_dir_yields_no_codes(tmp_path):
    d = tmp_path / "empty"
    d.mkdir()
    store = DelistedOhlcvStore.from_backfill_dir(d)
    assert store.codes() == set()
    assert store.get_daily_rows("900100") == []


def test_missing_dir_yields_no_codes(tmp_path):
    store = DelistedOhlcvStore.from_backfill_dir(tmp_path / "does_not_exist")
    assert store.codes() == set()


def test_direct_rows_constructor_normalizes(tmp_path):
    store = DelistedOhlcvStore({
        "900100": [
            {"date": "2026-03-12", "open": "1", "high": "2", "low": "1", "close": "2", "volume": "5"},
            {"date": "2026-03-10", "open": "1", "high": "2", "low": "1", "close": "1", "volume": "5"},
        ]
    })
    rows = store.get_daily_rows("900100")
    # 입력이 정렬돼 있지 않아도 date 오름차순으로 반환
    assert [r["date"] for r in rows] == ["20260310", "20260312"]
    assert rows[0]["close"] == 1.0
