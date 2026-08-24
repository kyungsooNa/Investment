"""VirtualTradeRepository 의 마이그레이션·backfill·보조 경로 테스트.

기존 테스트가 매수/매도 원장과 통계 집계를 다루므로, 여기서는 레거시 파일
1회 마이그레이션, 스케줄러 signal_history 로부터 미청산 수량을 되짚는 계산,
backfill 조기 반환과 종가 매트릭스 결측 보정처럼 실데이터가 어긋났을 때만 타는
분기를 채운다.
"""
import json
import os
import sqlite3
from datetime import datetime
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from repositories.virtual_trade_repository import VirtualTradeRepository, _date_key


@pytest.fixture
def clock():
    tm = MagicMock()
    tm.get_current_kst_time.return_value = datetime(2026, 5, 10, 12, 0, 0)
    return tm


@pytest.fixture
def repo(tmp_path, clock):
    db_dir = tmp_path / "data" / "VirtualTradeRepository"
    db_dir.mkdir(parents=True)
    return VirtualTradeRepository(db_path=str(db_dir / "virtual_trade.db"),
                                  market_clock=clock)


def _legacy_dir(repo):
    base_dir = os.path.dirname(repo.db_path)
    path = os.path.join(os.path.dirname(base_dir), "VirtualTradeManager")
    os.makedirs(path, exist_ok=True)
    return path


# --- 날짜 키 ------------------------------------------------------------------

@pytest.mark.parametrize("raw", [None, "", "날짜아님"])
def test_unparsable_dates_yield_an_empty_key(raw):
    assert _date_key(raw) == ""


def test_intraday_round_trip_is_detected_by_the_shared_date_key(repo):
    repo.log_buy("전략", "005930", 70000, 10)

    result = repo.log_sell_with_result("005930", 71000, 10)

    assert result.is_intraday_trade is True


# --- 레거시 마이그레이션 ------------------------------------------------------

def test_migration_is_skipped_when_the_db_has_no_directory(clock):
    repo = VirtualTradeRepository(db_path=":memory:", market_clock=clock)

    repo._migrate_legacy_data()  # 예외 없이 조용히 지나가야 한다


def test_migration_is_skipped_once_the_flag_file_exists(repo):
    legacy = _legacy_dir(repo)
    pd.DataFrame([{"strategy": "전략", "code": "005930", "buy_date": "2026-05-01",
                   "buy_price": 70000, "sell_date": None, "sell_price": None,
                   "return_rate": None, "status": "HOLD"}]).to_csv(
        os.path.join(legacy, "trade_journal.csv"), index=False, encoding="utf-8")
    with open(os.path.join(os.path.dirname(repo.db_path), ".migrated"), "w") as f:
        f.write("1")

    repo._migrate_legacy_data()

    assert repo.get_all_trades() == []


def test_legacy_csv_without_qty_or_reason_columns_is_migrated_with_defaults(repo):
    legacy = _legacy_dir(repo)
    pd.DataFrame([{"strategy": "전략", "code": "005930", "buy_date": "2026-05-01",
                   "buy_price": 70000, "sell_date": None, "sell_price": None,
                   "return_rate": None, "status": "HOLD"}]).to_csv(
        os.path.join(legacy, "trade_journal.csv"), index=False, encoding="utf-8")

    repo._migrate_legacy_data()

    trades = repo.get_all_trades()
    assert len(trades) == 1
    assert trades[0]["qty"] == 1
    assert trades[0]["reason"] == ""
    assert os.path.exists(os.path.join(os.path.dirname(repo.db_path), ".migrated"))


def test_broken_legacy_csv_is_logged_and_does_not_abort_migration(repo, caplog):
    legacy = _legacy_dir(repo)
    with open(os.path.join(legacy, "trade_journal.csv"), "w", encoding="utf-8") as f:
        f.write("깨진,헤더\n1,2\n")

    repo._migrate_legacy_data()

    assert repo.get_all_trades() == []


def test_broken_legacy_price_cache_is_logged_and_does_not_abort_migration(repo):
    legacy = _legacy_dir(repo)
    with open(os.path.join(legacy, "close_price_cache.json"), "w", encoding="utf-8") as f:
        f.write("{깨진 JSON")

    repo._migrate_legacy_data()

    assert repo._load_price_cache() == {}


def test_legacy_price_cache_is_migrated_into_the_price_cache_table(repo):
    legacy = _legacy_dir(repo)
    with open(os.path.join(legacy, "close_price_cache.json"), "w", encoding="utf-8") as f:
        json.dump({"005930": {"2026-05-01": 70000}}, f)

    repo._migrate_legacy_data()

    assert repo._load_price_cache()["005930"]["2026-05-01"] == 70000


# --- 스케줄러 signal_history 미청산 수량 ---------------------------------------

def _write_scheduler_db(repo, rows):
    path = os.path.join(repo._get_data_root_dir(), "StrategyScheduler")
    os.makedirs(path, exist_ok=True)
    db = os.path.join(path, "scheduler.db")
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE signal_history (id INTEGER PRIMARY KEY, strategy_name TEXT, "
            "code TEXT, action TEXT, price INTEGER, qty INTEGER, timestamp TEXT, "
            "api_success INTEGER)"
        )
        conn.executemany(
            "INSERT INTO signal_history (strategy_name, code, action, price, qty, "
            "timestamp, api_success) VALUES (?,?,?,?,?,?,1)", rows
        )
    return db


def test_open_signal_map_is_empty_without_target_pairs(repo):
    assert repo._load_scheduler_open_signal_map(set()) == {}


def test_open_signal_map_is_empty_without_a_scheduler_db(repo):
    assert repo._load_scheduler_open_signal_map({("전략", "005930")}) == {}


def test_open_signal_map_is_empty_when_the_scheduler_db_is_unreadable(repo):
    path = os.path.join(repo._get_data_root_dir(), "StrategyScheduler")
    os.makedirs(path, exist_ok=True)
    with open(os.path.join(path, "scheduler.db"), "w") as f:
        f.write("이건 sqlite 파일이 아니다")

    assert repo._load_scheduler_open_signal_map({("전략", "005930")}) == {}


def test_sells_cancel_out_the_most_recent_buys_first(repo):
    _write_scheduler_db(repo, [
        ("전략", "005930", "BUY", 70000, 10, "2026-05-01 09:00:00"),
        ("전략", "005930", "BUY", 71000, 5, "2026-05-02 09:00:00"),
        ("전략", "005930", "SELL", 72000, 5, "2026-05-03 09:00:00"),
    ])

    result = repo._load_scheduler_open_signal_map({("전략", "005930")})

    assert result[("전략", "005930")] == {
        "qty": 10, "buy_price": 70000, "buy_date": "2026-05-01 09:00:00"
    }


def test_partially_covered_buy_leaves_only_the_remaining_quantity(repo):
    _write_scheduler_db(repo, [
        ("전략", "005930", "BUY", 70000, 10, "2026-05-01 09:00:00"),
        ("전략", "005930", "SELL", 72000, 4, "2026-05-03 09:00:00"),
    ])

    result = repo._load_scheduler_open_signal_map({("전략", "005930")})

    assert result[("전략", "005930")]["qty"] == 6


def test_fully_sold_and_unrelated_pairs_are_dropped(repo):
    _write_scheduler_db(repo, [
        ("전략", "005930", "BUY", 70000, 10, "2026-05-01 09:00:00"),
        ("전략", "005930", "SELL", 72000, 10, "2026-05-03 09:00:00"),
        ("다른전략", "000660", "BUY", 200000, 3, "2026-05-01 09:00:00"),
    ])

    assert repo._load_scheduler_open_signal_map({("전략", "005930")}) == {}


def test_zero_quantity_and_non_trade_actions_are_ignored(repo):
    _write_scheduler_db(repo, [
        ("전략", "005930", "BUY", 70000, 0, "2026-05-01 09:00:00"),
        ("전략", "005930", "HOLD", 70000, 5, "2026-05-02 09:00:00"),
    ])

    assert repo._load_scheduler_open_signal_map({("전략", "005930")}) == {}


def test_live_strategy_sync_is_disabled_and_returns_nothing(repo):
    with patch.object(repo, "_load_live_strategy_state_positions", return_value=[]):
        assert repo.sync_live_strategy_positions() == []


# --- 매도 실패 경로 -----------------------------------------------------------

def test_selling_without_a_holding_returns_an_empty_result(repo):
    result = repo.log_sell_with_result("005930", 71000)

    assert result.return_rate is None
    assert result.pnl_filled_qty == 0


def test_selling_by_strategy_without_a_holding_returns_an_empty_result(repo):
    result = repo.log_sell_by_strategy_with_result("전략", "005930", 71000)

    assert result.return_rate is None
    assert result.pnl_filled_qty == 0


@pytest.mark.asyncio
async def test_async_sell_wrapper_delegates_to_the_sync_path(repo):
    repo.log_buy("전략", "005930", 70000, 10)

    result = await repo.log_sell_async_with_result("005930", 77000, 10)

    assert result.return_rate == 10.0


@pytest.mark.parametrize("qty", [0, -5])
def test_hold_quantity_update_rejects_non_positive_quantities(repo, qty):
    repo.log_buy("전략", "005930", 70000, 10)

    assert repo.update_hold_qty("전략", "005930", qty) == 0
    assert repo.get_holds()[0]["qty"] == 10


# --- JSON 직렬화 --------------------------------------------------------------

def test_unparsable_market_regime_text_becomes_none(repo):
    df = pd.DataFrame([{"code": "005930", "market_regime": "{깨진 JSON",
                        "return_rate": float("nan")}])

    records = repo._to_json_records(df)

    assert records[0]["market_regime"] is None
    assert records[0]["return_rate"] is None


# --- backfill 조기 반환 / 결측 보정 ---------------------------------------------

def test_backfill_returns_early_without_any_trades(repo):
    repo.backfill_snapshots()

    assert repo._load_data()["daily"] == {}


def test_backfill_returns_early_when_no_day_can_be_derived(repo):
    repo.log_buy("전략", "005930", 70000, 10)
    with repo._db:
        repo._db.execute("UPDATE trades SET buy_date='날짜아님'")

    repo.backfill_snapshots()

    assert repo._load_data()["daily"] == {}


def test_backfill_skips_when_every_day_already_has_a_snapshot(repo, clock):
    clock.get_current_kst_time.return_value = datetime(2026, 5, 2, 12, 0, 0)
    repo.log_buy("전략", "005930", 70000, 10)
    data = repo._load_data()
    data["daily"] = {"2026-05-02": {"전략": 1.0}}
    repo._save_data(data)

    with patch.object(repo, "_fetch_close_prices") as fetch:
        repo.backfill_snapshots()

    fetch.assert_not_called()


def test_backfill_falls_back_to_the_buy_price_when_close_prices_are_missing(repo, clock):
    clock.get_current_kst_time.return_value = datetime(2026, 5, 4, 12, 0, 0)
    repo.log_buy("전략", "005930", 70000, 10)

    with patch.object(repo, "_fetch_close_prices", return_value={}):
        repo.backfill_snapshots()

    daily = repo._load_data()["daily"]
    assert daily
    assert all(values.get("전략") == 0.0 for values in daily.values())


def test_backfill_ignores_rows_whose_buy_price_is_zero(repo, clock):
    clock.get_current_kst_time.return_value = datetime(2026, 5, 4, 12, 0, 0)
    repo.log_buy("전략", "005930", 70000, 10)
    repo.log_buy("전략", "000660", 0, 10)

    with patch.object(repo, "_fetch_close_prices", return_value={}):
        repo.backfill_snapshots()

    assert repo._load_data()["daily"]


def test_backfill_recovers_when_the_price_cache_cannot_be_framed(repo, clock):
    clock.get_current_kst_time.return_value = datetime(2026, 5, 4, 12, 0, 0)
    repo.log_buy("전략", "005930", 70000, 10)

    with patch.object(repo, "_fetch_close_prices",
                      return_value={"005930": "프레임으로 못 만드는 값"}):
        repo.backfill_snapshots()

    assert repo._load_data()["daily"]


# --- 변화량 조회 --------------------------------------------------------------

def test_daily_change_is_unknown_without_snapshots(repo):
    assert repo.get_daily_change("전략", 1.0) == (None, None)


def test_daily_change_is_unknown_with_a_single_trading_day(repo):
    assert repo.get_daily_change(
        "전략", 1.0, _data={"daily": {"2026-05-01": {"전략": 1.0}}}
    ) == (None, None)


def test_daily_change_is_unknown_when_the_strategy_is_absent_on_either_day(repo):
    data = {"daily": {"2026-05-01": {"다른전략": 1.0}, "2026-05-04": {"전략": 3.0}}}

    assert repo.get_daily_change("전략", 3.0, _data=data) == (None, None)


def test_daily_change_reports_the_delta_and_the_reference_day(repo):
    data = {"daily": {"2026-05-01": {"전략": 1.0}, "2026-05-04": {"전략": 3.5}}}

    assert repo.get_daily_change("전략", 3.5, _data=data) == (2.5, "2026-05-01")


def test_all_strategies_merges_snapshot_and_trade_names_and_drops_the_all_bucket(repo):
    repo.log_buy("전략A", "005930", 70000, 10)
    data = repo._load_data()
    data["daily"] = {"2026-05-01": {"전략B": 1.0, "ALL": 2.0}}
    repo._save_data(data)

    assert repo.get_all_strategies() == ["전략A", "전략B"]
