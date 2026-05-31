import pytest
import sys
import os
import sqlite3
import pandas as pd
import json
import math
import concurrent.futures
import random
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock, AsyncMock
from repositories.virtual_trade_repository import (
    VirtualTradeRepository,
    SellResult,
    _build_strategy_return_history,
    _get_trading_dates,
)
from services.virtual_trade_service import VirtualTradeService

@pytest.fixture
def temp_db(tmp_path):
    """테스트용 임시 SQLite DB 경로 생성"""
    db_dir = tmp_path / "data" / "VirtualTradeRepository"
    db_dir.mkdir(parents=True)
    return str(db_dir / "virtual_trade.db")

@pytest.fixture
def mock_market_clock():
    """MarketClock Mock 생성"""
    tm = MagicMock()
    tm.get_current_kst_time.return_value = datetime(2025, 1, 1, 12, 0, 0)
    return tm

@pytest.fixture
def virutal_trade_repository(temp_db, mock_market_clock):
    """VirtualTradeRepository 인스턴스 생성"""
    return VirtualTradeRepository(db_path=temp_db, market_clock=mock_market_clock)

def test_init_creates_directory_and_file(temp_db):
    """초기화 시 DB 파일이 생성되고 필수 테이블이 존재하는지 확인"""
    VirtualTradeRepository(db_path=temp_db)
    assert os.path.exists(temp_db)
    conn = sqlite3.connect(temp_db)
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert {"trades", "snapshots", "price_cache"}.issubset(tables)

def test_log_buy_success(virutal_trade_repository):
    """매수 기록이 정상적으로 저장되는지 확인"""
    virutal_trade_repository.log_buy("TestStrategy", "005930", 70000)
    df = virutal_trade_repository._read()
    assert len(df) == 1
    assert df.iloc[0]['strategy'] == "TestStrategy"
    assert df.iloc[0]['code'] == "005930"
    assert df.iloc[0]['status'] == "HOLD"
    assert df.iloc[0]['qty'] == 1
    assert virutal_trade_repository.is_holding("TestStrategy", "005930") is True

def test_log_buy_duplicate_skips(virutal_trade_repository):
    """동일 전략/종목 중복 매수 방지 로직 확인"""
    virutal_trade_repository.log_buy("StrategyA", "005930", 70000)
    virutal_trade_repository.log_buy("StrategyA", "005930", 71000)
    df = virutal_trade_repository._read()
    assert len(df) == 1

def test_log_buy_with_qty(virutal_trade_repository):
    """매수 시 수량(qty)이 정상적으로 기록되는지 확인"""
    virutal_trade_repository.log_buy("TestStrategy", "005930", 70000, qty=10)
    df = virutal_trade_repository._read()
    assert len(df) == 1
    assert df.iloc[0]['qty'] == 10

def test_log_sell_success(virutal_trade_repository):
    """매도 기록 및 수익률 계산 확인"""
    virutal_trade_repository.log_buy("StrategyA", "005930", 10000)
    virutal_trade_repository.log_sell("005930", 11000)
    df = virutal_trade_repository._read()
    assert df.iloc[0]['status'] == "SOLD"
    assert df.iloc[0]['return_rate'] == 10.0
    assert df.iloc[0]['sell_price'] == 11000

def test_log_sell_by_strategy(virutal_trade_repository):
    """전략별 매도 기능 확인"""
    virutal_trade_repository.log_buy("S1", "005930", 10000)
    virutal_trade_repository.log_buy("S2", "005930", 10000)
    virutal_trade_repository.log_sell_by_strategy("S1", "005930", 12000)
    df = virutal_trade_repository._read()
    assert df[df['strategy'] == "S1"].iloc[0]['status'] == "SOLD"
    assert df[df['strategy'] == "S2"].iloc[0]['status'] == "HOLD"

def test_get_all_trades_json_compatibility(virutal_trade_repository):
    """NaN 값이 None으로 변환되어 JSON 직렬화가 가능한지 확인"""
    virutal_trade_repository.log_buy("S1", "005930", 70000)
    trades = virutal_trade_repository.get_all_trades()
    assert trades[0]['sell_price'] is None
    assert trades[0]['sell_date'] is None

def test_get_summary_calculation(virutal_trade_repository):
    """통계 요약 계산 로직 확인"""
    virutal_trade_repository.log_buy("S1", "001", 1000)
    virutal_trade_repository.log_sell("001", 1200) # +20%
    virutal_trade_repository.log_buy("S1", "002", 1000)
    virutal_trade_repository.log_sell("002", 900)  # -10%
    virutal_trade_repository.log_buy("S1", "003", 1000) # HOLD (수익률 계산 제외)

    summary = virutal_trade_repository.get_summary(apply_cost=False)
    assert summary['total_trades'] == 3
    assert summary['win_rate'] == 50.0
    assert summary['avg_return'] == 5.0 # (20 - 10) / 2


def test_get_summary_excludes_forced_close(virutal_trade_repository):
    """강제종결(reason="reconciled_force_close") 매도는 win_rate / avg_return 계산에서 제외한다.

    reconcile 로 sell_price=0 처리되는 강제종결이 통계를 왜곡하지 않도록 분리.
    force_closed_count 는 별도 노출되어 UI 가 표시 가능.
    """
    virutal_trade_repository.log_buy("S1", "001", 1000)
    virutal_trade_repository.log_sell("001", 1200)
    virutal_trade_repository.log_buy("S1", "002", 1000)
    virutal_trade_repository.log_sell("002", 900)

    virutal_trade_repository.log_buy("S1", "003", 1000)
    virutal_trade_repository.log_sell("003", 0, reason="reconciled_force_close")

    summary = virutal_trade_repository.get_summary(apply_cost=False)

    assert summary["total_trades"] == 3
    assert summary["win_rate"] == 50.0
    assert summary["avg_return"] == 5.0
    assert summary["force_closed_count"] == 1


def test_get_summary_all_forced_close_returns_zero(virutal_trade_repository):
    """정상 매도가 없고 강제종결뿐이면 win_rate/avg_return 은 0 으로 반환한다."""
    virutal_trade_repository.log_buy("S1", "001", 1000)
    virutal_trade_repository.log_sell("001", 0, reason="reconciled_force_close")

    summary = virutal_trade_repository.get_summary()

    assert summary["total_trades"] == 1
    assert summary["win_rate"] == 0
    assert summary["avg_return"] == 0
    assert summary["force_closed_count"] == 1


def test_fix_sell_price_logic(virutal_trade_repository):
    """매도가 0인 기록 보정 로직 확인"""
    virutal_trade_repository.log_buy("S1", "005930", 10000)
    df = virutal_trade_repository._read()
    df.loc[0, 'status'] = 'SOLD'
    df.loc[0, 'sell_price'] = 0
    virutal_trade_repository._write(df)

    virutal_trade_repository.fix_sell_price("005930", None, 15000)
    df = virutal_trade_repository._read()
    assert df.iloc[0]['sell_price'] == 15000
    assert df.iloc[0]['return_rate'] == 50.0

def test_save_daily_snapshot_stores_data(virutal_trade_repository):
    """일일 스냅샷 저장 확인"""
    # 1일차 (수)
    virutal_trade_repository.tm.get_current_kst_time.return_value = datetime(2025, 1, 1)
    virutal_trade_repository.save_daily_snapshot({"S1": 1.0, "ALL": 1.0})

    # 2일차 (목) - 개별 전략 값 변경
    virutal_trade_repository.tm.get_current_kst_time.return_value = datetime(2025, 1, 2)
    virutal_trade_repository.save_daily_snapshot({"S1": 2.5, "ALL": 2.5})

    data = virutal_trade_repository._load_data()
    assert data['daily']['2025-01-01']['ALL'] == 1.0
    assert data['daily']['2025-01-02']['ALL'] == 2.5

def test_get_daily_change_calculation(virutal_trade_repository):
    """가장 최근 개장일 vs 직전 개장일 비교"""
    virutal_trade_repository.tm.get_current_kst_time.return_value = datetime(2025, 1, 3)  # 금요일
    data = {
        "daily": {
            "2025-01-01": {"S1": 0.5},  # 수요일
            "2025-01-02": {"S1": 1.0},  # 목요일
            "2025-01-03": {"S1": 1.8},  # 금요일 (오늘, 가장 최근)
        },
        "prev_values": {}
    }
    change, ref_date = virutal_trade_repository.get_daily_change("S1", 999, _data=data)
    assert change == 0.8   # 1.8(금) - 1.0(목)
    assert ref_date == "2025-01-02"  # 직전 개장일

def test_get_daily_change_skips_weekend_data(virutal_trade_repository):
    """토/일 스냅샷 무시, 휴장일에도 최근 2개 거래일 스냅샷 비교"""
    virutal_trade_repository.tm.get_current_kst_time.return_value = datetime(2025, 1, 6)  # 월요일 (스냅샷 없음)
    data = {
        "daily": {
            "2025-01-02": {"S1": 1.0},  # 목요일
            "2025-01-03": {"S1": 2.0},  # 금요일
            "2025-01-04": {"S1": 2.0},  # 토요일 (잔존) → 무시
            "2025-01-05": {"S1": 2.0},  # 일요일 (잔존) → 무시
        },
        "prev_values": {}
    }
    change, ref_date = virutal_trade_repository.get_daily_change("S1", 999, _data=data)
    assert change == 1.0   # 2.0(금) - 1.0(목)
    assert ref_date == "2025-01-02"

def test_get_daily_change_skips_holiday_data(virutal_trade_repository):
    """공휴일: 개별전략 동일 + ALL만 다른 경우도 무시"""
    virutal_trade_repository.tm.get_current_kst_time.return_value = datetime(2026, 3, 2)  # 월요일 (대체공휴일)
    data = {
        "daily": {
            "2026-02-26": {"S1": 1.0, "ALL": 1.0},
            "2026-02-27": {"S1": 2.0, "ALL": 3.64},  # 금요일
            "2026-03-02": {"S1": 2.0, "ALL": 2.12},  # 공휴일: S1 동일, ALL만 다름 → 무시
        },
        "prev_values": {}
    }
    change, ref_date = virutal_trade_repository.get_daily_change("S1", 999, _data=data)
    assert change == 1.0   # 2.0(금 2/27) - 1.0(목 2/26)
    assert ref_date == "2026-02-26"

def test_get_daily_change_today_is_trading_day(virutal_trade_repository):
    """오늘이 개장일이면 오늘 스냅샷을 가장 최근으로 사용"""
    virutal_trade_repository.tm.get_current_kst_time.return_value = datetime(2025, 1, 6)  # 월요일
    data = {
        "daily": {
            "2025-01-03": {"S1": 2.0},  # 금요일
            "2025-01-06": {"S1": 3.5},  # 월요일 (오늘)
        },
        "prev_values": {}
    }
    change, ref_date = virutal_trade_repository.get_daily_change("S1", 999, _data=data)
    assert change == 1.5   # 3.5(월) - 2.0(금)
    assert ref_date == "2025-01-03"

def test_save_daily_snapshot_skips_weekend(virutal_trade_repository):
    """토/일에는 스냅샷을 저장하지 않음"""
    virutal_trade_repository.tm.get_current_kst_time.return_value = datetime(2025, 1, 3)  # 금요일
    virutal_trade_repository.save_daily_snapshot({"ALL": 5.0})

    virutal_trade_repository.tm.get_current_kst_time.return_value = datetime(2025, 1, 4)  # 토요일
    virutal_trade_repository.save_daily_snapshot({"ALL": 5.0})

    virutal_trade_repository.tm.get_current_kst_time.return_value = datetime(2025, 1, 5)  # 일요일
    virutal_trade_repository.save_daily_snapshot({"ALL": 5.0})

    data = virutal_trade_repository._load_data()
    assert "2025-01-03" in data["daily"]
    assert "2025-01-04" not in data["daily"]
    assert "2025-01-05" not in data["daily"]

def test_save_daily_snapshot_skips_holiday(virutal_trade_repository):
    """공휴일: 개별전략 동일 + ALL만 다른 경우도 저장 안 함"""
    virutal_trade_repository.tm.get_current_kst_time.return_value = datetime(2026, 2, 27)  # 금요일
    virutal_trade_repository.save_daily_snapshot({"S1": 2.0, "ALL": 3.64})

    virutal_trade_repository.tm.get_current_kst_time.return_value = datetime(2026, 3, 2)  # 월요일 (대체공휴일)
    virutal_trade_repository.save_daily_snapshot({"S1": 2.0, "ALL": 2.12})  # S1 동일, ALL만 다름

    data = virutal_trade_repository._load_data()
    assert "2026-02-27" in data["daily"]
    assert "2026-03-02" not in data["daily"]

def test_get_weekly_change_logic(virutal_trade_repository):
    """7일 전 스냅샷 대비 변동폭 계산 확인"""
    virutal_trade_repository.tm.get_current_kst_time.return_value = datetime(2025, 1, 10)
    data = {
        "daily": {
            "2025-01-01": {"S1": 10.0}, # 9일 전
            "2025-01-03": {"S1": 20.0}, # 7일 전 (기준점)
            "2025-01-09": {"S1": 30.0}  # 1일 전
        }
    }
    change, ref_date = virutal_trade_repository.get_weekly_change("S1", 25.0, _data=data)
    assert change == 5.0  # 25.0 - 20.0
    assert ref_date == "2025-01-03"

def test_snapshot_migration_from_old_format(tmp_path):
    """레거시 JSON 포맷(portfolio_snapshots.json)을 SQLite로 1회 마이그레이션하는지 확인"""
    journal_dir = tmp_path / "data" / "VirtualTradeRepository"
    journal_dir.mkdir(parents=True)

    # 레거시 파일은 VirtualTradeManager 디렉토리에 위치
    legacy_dir = tmp_path / "data" / "VirtualTradeManager"
    legacy_dir.mkdir(parents=True)

    # 구버전 포맷: daily 키 없이 날짜가 최상위
    old_data = {"2025-01-01": {"ALL": 1.0}}
    snap_file = legacy_dir / "portfolio_snapshots.json"
    with open(snap_file, 'w', encoding='utf-8') as f:
        json.dump(old_data, f)

    db_path = str(journal_dir / "virtual_trade.db")
    repo = VirtualTradeRepository(db_path=db_path)
    data = repo._load_data()

    assert "daily" in data
    assert "2025-01-01" in data["daily"]
    assert data["daily"]["2025-01-01"]["ALL"] == 1.0
    assert data["prev_values"] == {}

def test_get_strategy_return_history_logic(virutal_trade_repository):
    """전략별 수익률 히스토리 조회 및 정렬 로직 테스트"""
    test_snapshots = {
        "daily": {
            "2025-01-01": {"S1": 1.1, "S2": 0.5},
            "2025-01-03": {"S1": 2.2, "S2": 1.5},
            "2025-01-02": {"S1": 1.5}  # 정렬 확인을 위해 순서 섞음
        },
        "prev_values": {}
    }
    virutal_trade_repository._save_data(test_snapshots)

    history = virutal_trade_repository.get_strategy_return_history("S1")

    assert len(history) == 3
    assert history[0] == {"date": "2025-01-01", "return_rate": 1.1}
    assert history[1] == {"date": "2025-01-02", "return_rate": 1.5}
    assert history[2] == {"date": "2025-01-03", "return_rate": 2.2}

    assert virutal_trade_repository.get_strategy_return_history("UNKNOWN") == []

def test_get_strategy_return_history_excludes_weekend(virutal_trade_repository):
    """차트 히스토리에서 토/일 데이터 제외 확인"""
    test_snapshots = {
        "daily": {
            "2025-01-03": {"S1": 2.0},  # 금요일
            "2025-01-04": {"S1": 2.0},  # 토요일 → 제외
            "2025-01-05": {"S1": 2.0},  # 일요일 → 제외
            "2025-01-06": {"S1": 3.0},  # 월요일
        },
        "prev_values": {}
    }
    virutal_trade_repository._save_data(test_snapshots)

    history = virutal_trade_repository.get_strategy_return_history("S1")
    dates = [h["date"] for h in history]
    assert "2025-01-04" not in dates
    assert "2025-01-05" not in dates
    assert dates == ["2025-01-03", "2025-01-06"]

def test_get_all_strategies_logic(virutal_trade_repository):
    """모든 전략 이름 추출 로직 테스트"""
    test_snapshots = {
        "daily": {
            "2025-01-01": {"S1": 1.0, "S2": 2.0},
            "2025-01-02": {"S1": 1.5, "S3": 0.5}
        },
        "prev_values": {}
    }
    virutal_trade_repository._save_data(test_snapshots)

    strategies = virutal_trade_repository.get_all_strategies()
    assert strategies == ["S1", "S2", "S3"]

def test_read_always_has_qty_column(virutal_trade_repository):
    """SQLite 스키마가 qty 컬럼 DEFAULT 1을 보장하는지 확인"""
    virutal_trade_repository.log_buy("S1", "005930", 1000)
    df = virutal_trade_repository._read()

    assert "qty" in df.columns
    assert df.iloc[0]['qty'] == 1

def test_log_buy_date_format(virutal_trade_repository):
    """매수 기록의 날짜 포맷이 YYYY-MM-DD HH:MM:SS 인지 확인 (호환성 유지)"""
    virutal_trade_repository.log_buy("TestStrategy", "005930", 70000)
    df = virutal_trade_repository._read()
    buy_date = df.iloc[0]['buy_date']
    assert buy_date[4] == '-' and buy_date[7] == '-'

@pytest.mark.asyncio
async def test_log_buy_async(virutal_trade_repository):
    """비동기 매수 기록 메서드 테스트"""
    await virutal_trade_repository.log_buy_async("AsyncStrategy", "005930", 70000, qty=5)
    df = virutal_trade_repository._read()
    assert len(df) == 1
    assert df.iloc[0]['strategy'] == "AsyncStrategy"
    assert df.iloc[0]['qty'] == 5

def test_log_buy_thread_safety(virutal_trade_repository):
    """여러 스레드에서 동시에 log_buy 호출 시 데이터 무결성 테스트 (Lock 작동 확인)."""
    def worker(idx):
        virutal_trade_repository.log_buy("ConcurrentStrat", f"0000{idx}", 1000 * idx)

    thread_count = 10
    with concurrent.futures.ThreadPoolExecutor(max_workers=thread_count) as executor:
        futures = [executor.submit(worker, i) for i in range(thread_count)]
        for future in concurrent.futures.as_completed(futures):
            future.result()

    df = virutal_trade_repository._read()
    assert len(df) == 10
    codes = set(df['code'])
    assert len(codes) == 10

@pytest.mark.asyncio
async def test_log_buy_async_thread_execution(virutal_trade_repository):
    """log_buy_async가 asyncio.to_thread를 사용하여 log_buy를 실행하는지 테스트."""
    with patch("asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
        await virutal_trade_repository.log_buy_async("S1", "005930", 1000, 10)
        mock_to_thread.assert_awaited_once_with(virutal_trade_repository.log_buy, "S1", "005930", 1000, 10, None, None, None, None, None)

@pytest.mark.asyncio
async def test_log_sell_async_thread_execution(virutal_trade_repository):
    """log_sell_async가 asyncio.to_thread를 사용하여 log_sell을 실행하는지 테스트."""
    with patch("asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
        await virutal_trade_repository.log_sell_async("005930", 1200, 5)
        mock_to_thread.assert_awaited_once_with(virutal_trade_repository.log_sell, "005930", 1200, 5, "")

@pytest.mark.asyncio
async def test_log_sell_by_strategy_async_thread_execution(virutal_trade_repository):
    """log_sell_by_strategy_async가 asyncio.to_thread를 사용하여 실행하는지 테스트."""
    with patch("asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
        await virutal_trade_repository.log_sell_by_strategy_async("S1", "005930", 1200, 5)
        mock_to_thread.assert_awaited_once_with(virutal_trade_repository.log_sell_by_strategy, "S1", "005930", 1200, 5, "")

def test_log_sell_failure_no_hold(virutal_trade_repository):
    """보유하지 않은 종목 매도 시도 시 처리 확인"""
    with patch("repositories.virtual_trade_repository.logger") as mock_logger:
        virutal_trade_repository.log_sell("999999", 10000)
        mock_logger.warning.assert_called()

    df = virutal_trade_repository._read()
    assert df.empty

def test_log_sell_by_strategy_failure_no_hold(virutal_trade_repository):
    """전략별 매도 시 보유하지 않은 경우 처리 확인"""
    with patch("repositories.virtual_trade_repository.logger") as mock_logger:
        virutal_trade_repository.log_sell_by_strategy("S1", "999999", 10000)
        mock_logger.warning.assert_called()

def test_get_solds_and_holds(virutal_trade_repository):
    """get_solds와 get_holds 메서드 확인"""
    virutal_trade_repository.log_buy("S1", "A", 1000)
    virutal_trade_repository.log_buy("S1", "B", 2000)
    virutal_trade_repository.log_sell("A", 1100)

    solds = virutal_trade_repository.get_solds()
    holds = virutal_trade_repository.get_holds()

    assert len(solds) == 1
    assert solds[0]['code'] == "A"
    assert len(holds) == 1
    assert holds[0]['code'] == "B"

def test_price_cache_operations(virutal_trade_repository):
    """가격 캐시 SQLite 저장 및 로드 확인"""
    cache_data = {"005930": {"2025-01-01": 70000}}
    virutal_trade_repository._save_price_cache(cache_data)

    loaded = virutal_trade_repository._load_price_cache()
    assert loaded == cache_data

def test_find_prev_close(virutal_trade_repository):
    """_find_prev_close 로직 확인"""
    cache = {
        "005930": {
            "2025-01-01": 70000,
            "2025-01-03": 72000
        }
    }
    # 1월 2일 데이터는 없음 -> 1월 1일 데이터 반환해야 함
    price = virutal_trade_repository._find_prev_close(cache, "005930", "2025-01-02")
    assert price == 70000

    # 이전 데이터가 아예 없는 경우
    price = virutal_trade_repository._find_prev_close(cache, "005930", "2024-12-31")
    assert price is None

def test_backfill_snapshots(virutal_trade_repository):
    """과거 스냅샷 backfill 로직 확인"""
    virutal_trade_repository.tm.get_current_kst_time.return_value = datetime(2025, 1, 1)
    virutal_trade_repository.log_buy("S1", "A", 1000)

    virutal_trade_repository.tm.get_current_kst_time.return_value = datetime(2025, 1, 2)
    virutal_trade_repository.log_buy("S2", "B", 2000)

    virutal_trade_repository.tm.get_current_kst_time.return_value = datetime(2025, 1, 3)
    virutal_trade_repository.log_sell("A", 1100) # 10% 수익

    price_cache = {
        "A": {"2025-01-01": 1000, "2025-01-02": 1050, "2025-01-03": 1100},
        "B": {"2025-01-02": 2000, "2025-01-03": 1900}
    }

    with patch.object(virutal_trade_repository, '_fetch_close_prices', return_value=price_cache):
        virutal_trade_repository.backfill_snapshots()

    data = virutal_trade_repository._load_data()
    daily = data["daily"]

    # 1/1: A 보유 (매수가 1000, 종가 1000 -> 0%)
    assert daily["2025-01-01"]["S1"] == 0.0

    # 1/2: A 보유 (매수가 1000, 종가 1050 -> 5%), B 보유 (매수가 2000, 종가 2000 -> 0%)
    assert daily["2025-01-02"]["S1"] == 5.0
    assert daily["2025-01-02"]["S2"] == 0.0

    # 1/3: A 매도 (확정 10%), B 보유 (매수가 2000, 종가 1900 -> -5%)
    assert daily["2025-01-03"]["S1"] == 10.0
    assert daily["2025-01-03"]["S2"] == -5.0

def test_load_data_returns_empty_for_new_repo(virutal_trade_repository):
    """신규 저장소에서 _load_data()가 빈 데이터를 반환하는지 확인"""
    # 캐시 초기화
    virutal_trade_repository._cached_data = None
    data = virutal_trade_repository._load_data()
    assert data == {"daily": {}, "prev_values": {}}

def test_backfill_snapshots_empty_df(virutal_trade_repository):
    """거래 내역이 없을 때 backfill 수행 안함"""
    with patch.object(virutal_trade_repository, '_fetch_close_prices') as mock_fetch:
        virutal_trade_repository.backfill_snapshots()
        mock_fetch.assert_not_called()

def test_load_price_cache_empty(virutal_trade_repository):
    """가격 캐시가 비어있을 때 빈 딕셔너리 반환 확인"""
    loaded = virutal_trade_repository._load_price_cache()
    assert loaded == {}

def test_fetch_close_prices_cache_hit(virutal_trade_repository):
    """캐시에 데이터가 이미 있으면 API 호출 스킵"""
    cache_data = {"005930": {"2025-01-01": 70000, "2025-01-02": 71000, "2025-01-03": 72000}}
    virutal_trade_repository._save_price_cache(cache_data)

    mock_pykrx_stock = MagicMock()
    mock_pykrx = MagicMock()
    mock_pykrx.stock = mock_pykrx_stock

    with patch.dict("sys.modules", {"pykrx": mock_pykrx, "pykrx.stock": mock_pykrx_stock}):
        result = virutal_trade_repository._fetch_close_prices(["005930"], "2025-01-01", "2025-01-03")
        assert result == cache_data
        mock_pykrx_stock.get_market_ohlcv_by_date.assert_not_called()

def test_fetch_close_prices_api_call(virutal_trade_repository):
    """캐시가 없으면 API 호출 후 저장"""
    mock_pykrx_stock = MagicMock()
    mock_df = pd.DataFrame({'종가': [70000, 71000]}, index=pd.to_datetime(['2025-01-01', '2025-01-02']))
    mock_pykrx_stock.get_market_ohlcv_by_date.return_value = mock_df

    mock_pykrx = MagicMock()
    mock_pykrx.stock = mock_pykrx_stock

    with patch.dict("sys.modules", {"pykrx": mock_pykrx, "pykrx.stock": mock_pykrx_stock}):
        result = virutal_trade_repository._fetch_close_prices(["005930"], "2025-01-01", "2025-01-02")

        assert "005930" in result
        assert result["005930"]["2025-01-01"] == 70000
        assert result["005930"]["2025-01-02"] == 71000
        mock_pykrx_stock.get_market_ohlcv_by_date.assert_called_once()

        loaded = virutal_trade_repository._load_price_cache()
        assert loaded["005930"]["2025-01-01"] == 70000

def test_fetch_close_prices_api_empty(virutal_trade_repository):
    """API 응답이 비어있을 때 처리"""
    mock_pykrx_stock = MagicMock()
    mock_pykrx_stock.get_market_ohlcv_by_date.return_value = pd.DataFrame()

    mock_pykrx = MagicMock()
    mock_pykrx.stock = mock_pykrx_stock

    with patch.dict("sys.modules", {"pykrx": mock_pykrx, "pykrx.stock": mock_pykrx_stock}):
        result = virutal_trade_repository._fetch_close_prices(["005930"], "2025-01-01", "2025-01-01")
        assert "005930" not in result
        mock_pykrx_stock.get_market_ohlcv_by_date.assert_called_once()

def test_fetch_close_prices_api_exception(virutal_trade_repository):
    """API 호출 중 예외 발생 시 처리"""
    mock_pykrx_stock = MagicMock()
    mock_pykrx_stock.get_market_ohlcv_by_date.side_effect = Exception("API Error")

    mock_pykrx = MagicMock()
    mock_pykrx.stock = mock_pykrx_stock

    with patch.dict("sys.modules", {"pykrx": mock_pykrx, "pykrx.stock": mock_pykrx_stock}):
        with patch("repositories.virtual_trade_repository.logger") as mock_logger:
            result = virutal_trade_repository._fetch_close_prices(["005930"], "2025-01-01", "2025-01-01")
            assert "005930" not in result
            mock_logger.warning.assert_called()

def test_backfill_snapshots_missing_price_logic(virutal_trade_repository):
    """backfill_snapshots: 종가 데이터 누락 시 직전 종가 사용 및 데이터 아예 없을 때 0.0 처리 검증"""
    virutal_trade_repository.tm.get_current_kst_time.return_value = datetime(2025, 1, 1)
    virutal_trade_repository.log_buy("S1", "A", 1000)
    virutal_trade_repository.log_buy("S2", "B", 1000)

    virutal_trade_repository.tm.get_current_kst_time.return_value = datetime(2025, 1, 3)
    virutal_trade_repository.log_sell("A", 1300)
    virutal_trade_repository.log_sell("B", 1300)

    # A: 1/1(1100), 1/2(데이터 없음 → ffill로 1100), 1/3(1200)
    # B: 데이터 아예 없음 → buy_price(1000) 사용
    price_cache = {
        "A": {"2025-01-01": 1100, "2025-01-03": 1200},
    }

    with patch.object(virutal_trade_repository, '_fetch_close_prices', return_value=price_cache):
        virutal_trade_repository.backfill_snapshots()

    data = virutal_trade_repository._load_data()
    daily = data["daily"]

    assert daily["2025-01-01"]["S1"] == 10.0
    assert daily["2025-01-01"]["S2"] == 0.0

    assert daily["2025-01-02"]["S1"] == 10.0
    assert daily["2025-01-02"]["S2"] == 0.0

    assert daily["2025-01-03"]["S1"] == 30.0
    assert daily["2025-01-03"]["S2"] == 30.0

def test_get_holds_by_strategy(virutal_trade_repository):
    """get_holds_by_strategy는 해당 전략의 HOLD 건만 반환하며 qty가 포함된다"""
    virutal_trade_repository.log_buy("StrategyA", "005930", 1000, qty=3)
    virutal_trade_repository.log_buy("StrategyB", "000660", 2000)

    holds = virutal_trade_repository.get_holds_by_strategy("StrategyA")

    assert len(holds) == 1
    assert holds[0]['code'] == "005930"
    assert holds[0]['qty'] == 3

def test_backfill_snapshots_asset_weighted_logic(virutal_trade_repository):
    """백필 시 자산 가중 평균으로 수익률이 계산되는지 확인"""
    virutal_trade_repository.tm.get_current_kst_time.return_value = datetime(2025, 1, 1)
    virutal_trade_repository.log_buy("StrategyA", "A", 1000, qty=1000) # 매수금 1,000,000
    virutal_trade_repository.log_buy("StrategyB", "B", 1000, qty=10)   # 매수금 10,000

    price_cache = {
        "A": {"2025-01-02": 1100},
        "B": {"2025-01-02": 500}
    }

    virutal_trade_repository.tm.get_current_kst_time.return_value = datetime(2025, 1, 3)

    with patch.object(virutal_trade_repository, '_fetch_close_prices', return_value=price_cache):
        virutal_trade_repository.backfill_snapshots()

    data = virutal_trade_repository._load_data()
    daily = data["daily"]

    assert "2025-01-02" in daily
    snapshot = daily["2025-01-02"]

    # 자산 가중 평균: 총매수 1,010,000 / 총평가 1,105,000 → 9.41%
    assert snapshot["ALL"] == 9.41
    assert snapshot["StrategyA"] == 10.0
    assert snapshot["StrategyB"] == -50.0

def test_get_daily_change_returns_none_if_insufficient_data(virutal_trade_repository):
    """데이터가 부족할 때 전일 대비가 None을 반환하는지 확인"""
    virutal_trade_repository.tm.get_current_kst_time.return_value = datetime(2025, 1, 2)
    data = {
        "daily": {
            "2025-01-02": {"ALL": 1.0}
        },
        "prev_values": {}
    }

    change, ref_date = virutal_trade_repository.get_daily_change("ALL", 1.0, _data=data)
    assert change is None
    assert ref_date is None

def test_backfill_snapshots_performance(virutal_trade_repository):
    """backfill_snapshots 대량 데이터 성능 테스트"""
    import time

    dates = pd.date_range(start="2024-01-01", end="2024-12-31", freq="B")
    trades = []
    codes = [f"A{i:04d}" for i in range(50)]

    for i, date in enumerate(dates):
        code = codes[i % 50]
        buy_date = date.strftime("%Y-%m-%d %H:%M:%S")
        status = "SOLD" if i % 2 == 0 else "HOLD"
        sell_date = (date + pd.Timedelta(days=5)).strftime("%Y-%m-%d %H:%M:%S") if status == "SOLD" else None
        sell_price = 11000 if status == "SOLD" else None

        trades.append({
            "strategy": "PerfTest",
            "code": code,
            "buy_date": buy_date,
            "buy_price": 10000,
            "qty": 10,
            "sell_date": sell_date,
            "sell_price": sell_price,
            "return_rate": 10.0 if status == "SOLD" else 0.0,
            "status": status,
            "reason": ""
        })

    df = pd.DataFrame(trades)
    virutal_trade_repository._write(df)

    price_cache = {}
    full_date_range = pd.date_range(start="2024-01-01", end="2024-12-31")
    for code in codes:
        price_cache[code] = {
            d.strftime("%Y-%m-%d"): random.randint(9000, 11000)
            for d in full_date_range
        }

    virutal_trade_repository.tm.get_current_kst_time.return_value = datetime(2025, 1, 1)

    start_time = time.time()
    with patch.object(virutal_trade_repository, '_fetch_close_prices', return_value=price_cache):
        virutal_trade_repository.backfill_snapshots()
    end_time = time.time()

    duration = end_time - start_time
    print(f"\n[Performance] backfill_snapshots processed {len(trades)} trades over {len(dates)} days in {duration:.4f} seconds")
    assert duration < 2.0

def test_calculate_return_delegation(virutal_trade_repository):
    """calculate_return 메서드가 비용 적용 옵션에 따라 다르게 동작하는지 확인"""
    ror_no_cost = virutal_trade_repository.calculate_return(10000, 11000, qty=1, apply_cost=False)
    assert ror_no_cost == 10.0

    ror_cost = virutal_trade_repository.calculate_return(10000, 11000, qty=1, apply_cost=True)
    assert ror_cost < 10.0

def test_get_trade_amount(virutal_trade_repository):
    """get_trade_amount 메서드가 비용을 가감하여 계산하는지 확인"""
    price = 10000
    qty = 10
    base_amount = price * qty

    assert virutal_trade_repository.get_trade_amount(price, qty, is_sell=False, apply_cost=False) == base_amount
    assert virutal_trade_repository.get_trade_amount(price, qty, is_sell=True, apply_cost=False) == base_amount

    buy_amt = virutal_trade_repository.get_trade_amount(price, qty, is_sell=False, apply_cost=True)
    assert buy_amt > base_amount

    sell_amt = virutal_trade_repository.get_trade_amount(price, qty, is_sell=True, apply_cost=True)
    assert sell_amt < base_amount

def test_get_summary_with_cost(virutal_trade_repository):
    """get_summary 메서드에서 apply_cost 옵션이 통계에 반영되는지 확인"""
    virutal_trade_repository.log_buy("StrategyA", "005930", 10000, qty=10)
    virutal_trade_repository.log_sell("005930", 11000, qty=10)

    summary_default = virutal_trade_repository.get_summary()
    assert summary_default['avg_return'] < 10.0

    summary_no_cost = virutal_trade_repository.get_summary(apply_cost=False)
    assert summary_no_cost['avg_return'] == 10.0

    summary_cost = virutal_trade_repository.get_summary(apply_cost=True)
    assert summary_cost['avg_return'] < 10.0
    assert summary_default == summary_cost

def test_get_all_trades_with_cost(virutal_trade_repository):
    """get_all_trades 메서드에서 apply_cost=True일 때 개별 거래 수익률이 재계산되는지 확인"""
    virutal_trade_repository.log_buy("StrategyA", "005930", 10000, qty=10)
    virutal_trade_repository.log_sell("005930", 11000, qty=10)

    trades_default = virutal_trade_repository.get_all_trades()
    assert len(trades_default) == 1
    assert trades_default[0]['return_rate'] < 10.0

    trades_no_cost = virutal_trade_repository.get_all_trades(apply_cost=False)
    assert len(trades_no_cost) == 1
    assert trades_no_cost[0]['return_rate'] == 10.0

    trades_cost = virutal_trade_repository.get_all_trades(apply_cost=True)
    assert len(trades_cost) == 1
    assert trades_cost[0]['return_rate'] < 10.0
    assert trades_default == trades_cost


def test_get_standard_journal_records_returns_backtest_live_comparable_schema(virutal_trade_repository):
    """표준 journal 조회는 기존 원장을 backtest/live 비교 가능한 공통 schema로 반환한다."""
    virutal_trade_repository.log_buy("StrategyA", "005930", 10000, qty=10)
    virutal_trade_repository.log_sell_by_strategy("StrategyA", "005930", 11000, qty=10, reason="target_hit")

    records = virutal_trade_repository.get_standard_journal_records()

    assert len(records) == 1
    record = records[0]
    assert record["source"] == "virtual_trade"
    assert record["strategy"] == "StrategyA"
    assert record["code"] == "005930"
    assert record["signal_time"] == "2025-01-01 12:00:00"
    assert record["decision_reason"] == "target_hit"
    assert record["rejected_reason"] == ""
    assert record["order_price"] == 10000.0
    assert record["fill_price"] == 11000.0
    assert record["qty"] == 10
    assert record["cost"] > 0
    assert record["net_pnl"] < record["gross_pnl"]
    assert record["net_return"] < record["gross_return"]


def test_log_order_failure_records_failed_trade(virutal_trade_repository):
    """주문 실패 기록은 FAILED 상태와 사유를 저장한다."""
    virutal_trade_repository.log_order_failure("매수", "005930", 70000, 3, "잔고부족", "전략A")

    df = virutal_trade_repository._read()
    assert len(df) == 1
    assert df.iloc[0]["strategy"] == "전략A"
    assert df.iloc[0]["status"] == "FAILED"
    assert df.iloc[0]["reason"] == "잔고부족"


def test_log_order_failure_uses_default_strategy_label(virutal_trade_repository):
    """전략명이 없으면 action 기반 기본 전략명이 저장된다."""
    virutal_trade_repository.log_order_failure("매도", "005930", 70000, 1, "주문거부")

    df = virutal_trade_repository._read()
    assert df.iloc[0]["strategy"] == "매도실패"


@pytest.mark.asyncio
async def test_log_order_failure_async_thread_execution(virutal_trade_repository):
    """log_order_failure_async가 asyncio.to_thread를 사용한다."""
    with patch("asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
        await virutal_trade_repository.log_order_failure_async("매수", "005930", 70000, 2, "실패", "전략A")
        mock_to_thread.assert_awaited_once_with(
            virutal_trade_repository.log_order_failure, "매수", "005930", 70000, 2, "실패", "전략A"
        )


def test_write_normalizes_nan_fields(virutal_trade_repository):
    """_write는 NaN/None 값을 SQLite 저장용 기본값으로 정규화한다."""
    df = pd.DataFrame([{
        "strategy": "S1",
        "code": "005930",
        "buy_date": "2025-01-01 12:00:00",
        "buy_price": 1000,
        "qty": float("nan"),
        "sell_date": float("nan"),
        "sell_price": float("nan"),
        "return_rate": float("nan"),
        "status": "HOLD",
        "reason": None,
    }])

    virutal_trade_repository._write(df)
    saved = virutal_trade_repository._read().iloc[0]

    assert saved["qty"] == 1
    assert saved["sell_price"] is None or pd.isna(saved["sell_price"])
    assert saved["return_rate"] == 0.0
    assert saved["reason"] == ""


def test_fix_sell_price_with_buy_date_filter(virutal_trade_repository):
    """buy_date 필터가 있으면 해당 건만 보정한다."""
    virutal_trade_repository.tm.get_current_kst_time.return_value = datetime(2025, 1, 1, 12, 0, 0)
    virutal_trade_repository.log_buy("S1", "005930", 10000)
    virutal_trade_repository.tm.get_current_kst_time.return_value = datetime(2025, 1, 1, 12, 0, 1)
    virutal_trade_repository.log_buy("S2", "005930", 20000)
    df = virutal_trade_repository._read()
    first_buy_date = df.iloc[0]["buy_date"]
    second_buy_date = df.iloc[1]["buy_date"]
    df["status"] = "SOLD"
    df["sell_price"] = 0
    virutal_trade_repository._write(df)

    virutal_trade_repository.fix_sell_price("005930", first_buy_date, 15000)
    fixed = virutal_trade_repository._read()

    first = fixed[fixed["buy_date"] == first_buy_date].iloc[0]
    second = fixed[fixed["buy_date"] == second_buy_date].iloc[0]
    assert first["sell_price"] == 15000
    assert second["sell_price"] == 0


def test_save_daily_snapshot_trims_old_data_and_skips_same_strategy_values(virutal_trade_repository):
    """30일 이전 데이터는 정리되고 개별 전략 값이 같으면 저장을 스킵한다."""
    old_day = "2024-11-20"
    keep_day = "2024-12-31"
    virutal_trade_repository._save_data({
        "daily": {
            old_day: {"S1": 1.0, "ALL": 1.0},
            keep_day: {"S1": 2.0, "ALL": 2.0},
        },
        "prev_values": {}
    })

    virutal_trade_repository.tm.get_current_kst_time.return_value = datetime(2025, 1, 2)
    virutal_trade_repository.save_daily_snapshot({"S1": 2.0, "ALL": 999.0})
    data = virutal_trade_repository._load_data()
    assert "2025-01-02" not in data["daily"]

    virutal_trade_repository.save_daily_snapshot({"S1": 3.0, "ALL": 3.0})
    refreshed = virutal_trade_repository._load_data()
    assert old_day not in refreshed["daily"]
    assert keep_day in refreshed["daily"]
    assert refreshed["daily"]["2025-01-02"]["S1"] == 3.0


def test_save_data_logs_error_on_failure(virutal_trade_repository):
    """_save_data는 DB 저장 실패를 로깅한다."""
    class FailingDB:
        def execute(self, *_args, **_kwargs):
            raise Exception("save fail")

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    original_db = virutal_trade_repository._db
    virutal_trade_repository._db = FailingDB()
    try:
        with patch("repositories.virtual_trade_repository.logger") as mock_logger:
            virutal_trade_repository._save_data({"daily": {"2025-01-01": {"ALL": 1.0}}, "prev_values": {}})
            mock_logger.error.assert_called_once()
    finally:
        virutal_trade_repository._db = original_db


def test_get_weekly_change_returns_none_when_missing_reference(virutal_trade_repository):
    """주간 기준일 후보가 없거나 기준 전략값이 없으면 None을 반환한다."""
    virutal_trade_repository.tm.get_current_kst_time.return_value = datetime(2025, 1, 10)
    no_candidate = {"daily": {"2025-01-10": {"S1": 5.0}}}
    assert virutal_trade_repository.get_weekly_change("S1", 5.0, _data=no_candidate) == (None, None)

    missing_ref = {"daily": {"2025-01-03": {"S2": 2.0}, "2025-01-10": {"S1": 5.0}}}
    assert virutal_trade_repository.get_weekly_change("S1", 5.0, _data=missing_ref) == (None, None)


def test_get_strategy_return_history_and_all_strategies_empty_paths(virutal_trade_repository):
    """스냅샷이 비어 있으면 빈 히스토리/전략 목록을 반환한다."""
    virutal_trade_repository._save_data({"daily": {}, "prev_values": {}})
    virutal_trade_repository._cached_data = None

    assert virutal_trade_repository.get_strategy_return_history("S1") == []
    assert virutal_trade_repository.get_all_strategies() == []


def test_get_strategy_return_history_skips_leading_empty_dates_in_repository(virutal_trade_repository):
    """repository history도 첫 실제 snapshot 이전 날짜를 포함하지 않는다."""
    virutal_trade_repository._save_data({
        "daily": {
            "2025-03-25": {"OldStrategy": -1.0},
            "2025-03-26": {"OldStrategy": -0.5},
            "2025-04-24": {"TodayStrategy": 0.0},
            "2025-04-25": {"TodayStrategy": 1.2},
        },
        "prev_values": {}
    })
    virutal_trade_repository._cached_data = None

    assert virutal_trade_repository.get_strategy_return_history("TodayStrategy") == [
        {"date": "2025-04-24", "return_rate": 0.0},
        {"date": "2025-04-25", "return_rate": 1.2},
    ]


def test_migrate_legacy_data_handles_failures(tmp_path):
    """레거시 마이그레이션 중 일부 파일 실패가 나도 나머지는 진행한다."""
    db_dir = tmp_path / "data" / "VirtualTradeRepository"
    db_dir.mkdir(parents=True)
    legacy_dir = tmp_path / "data" / "VirtualTradeManager"
    legacy_dir.mkdir(parents=True)

    (legacy_dir / "trade_journal.csv").write_text("bad,csv\n", encoding="utf-8")
    (legacy_dir / "portfolio_snapshots.json").write_text("{bad json", encoding="utf-8")
    with open(legacy_dir / "close_price_cache.json", "w", encoding="utf-8") as f:
        json.dump({"005930": {"2025-01-01": 70000}}, f)

    with patch("repositories.virtual_trade_repository.logger") as mock_logger:
        repo = VirtualTradeRepository(db_path=str(db_dir / "virtual_trade.db"))

    loaded_cache = repo._load_price_cache()
    assert loaded_cache["005930"]["2025-01-01"] == 70000
    assert mock_logger.warning.call_count >= 2


def test_sync_live_strategy_positions_does_not_backfill_from_strategy_state(virutal_trade_repository, tmp_path):
    """전략 state/scheduler 이력은 virtual_trade.db의 HOLD를 새로 만들 수 없다."""
    data_root = tmp_path / "data"
    scheduler_dir = data_root / "StrategyScheduler"
    scheduler_dir.mkdir(parents=True, exist_ok=True)

    state_payload = {
        "positions": {
            "489790": {"entry_price": 82000, "entry_date": "20260424"},
            "100840": {"entry_price": 57700, "entry_date": "20260424"},
        }
    }
    (data_root / "pp_position_state.json").write_text(json.dumps(state_payload), encoding="utf-8")

    scheduler_db = scheduler_dir / "scheduler.db"
    with sqlite3.connect(scheduler_db) as conn:
        conn.execute(
            """
            CREATE TABLE signal_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy_name TEXT NOT NULL,
                code TEXT NOT NULL,
                name TEXT NOT NULL,
                action TEXT NOT NULL,
                price INTEGER NOT NULL,
                qty INTEGER NOT NULL DEFAULT 1,
                return_rate REAL,
                reason TEXT,
                timestamp TEXT NOT NULL,
                api_success INTEGER NOT NULL DEFAULT 1
            )
            """
        )
        conn.executemany(
            """
            INSERT INTO signal_history
            (strategy_name, code, name, action, price, qty, return_rate, reason, timestamp, api_success)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ("오닐PP/BGU", "489790", "한화비전", "BUY", 82000, 6, None, "", "2026-04-24 13:14:18", 1),
                ("오닐PP/BGU", "100840", "SNT에너지", "BUY", 57700, 8, None, "", "2026-04-24 12:39:43", 1),
                ("오닐PP/BGU", "100840", "SNT에너지", "SELL", 60000, 8, None, "", "2026-04-24 14:00:00", 1),
            ],
        )

    with patch("repositories.virtual_trade_repository.logger") as mock_logger:
        inserted = virutal_trade_repository.sync_live_strategy_positions()
    holds = virutal_trade_repository.get_holds_by_strategy("오닐PP/BGU")

    assert inserted == []
    assert holds == []
    mock_logger.warning.assert_called()


def test_sync_live_strategy_positions_skips_state_only_holds(virutal_trade_repository, tmp_path):
    """scheduler상 열린 BUY 근거가 없으면 state 파일만으로 HOLD를 만들지 않는다."""
    data_root = tmp_path / "data"
    scheduler_dir = data_root / "StrategyScheduler"
    scheduler_dir.mkdir(parents=True, exist_ok=True)

    (data_root / "fp_position_state.json").write_text(
        json.dumps({
            "positions": {
                "819550": {"entry_price": 96000, "entry_date": "20260308"},
            }
        }),
        encoding="utf-8",
    )

    with sqlite3.connect(scheduler_dir / "scheduler.db") as conn:
        conn.execute(
            """
            CREATE TABLE signal_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy_name TEXT NOT NULL,
                code TEXT NOT NULL,
                name TEXT NOT NULL,
                action TEXT NOT NULL,
                price INTEGER NOT NULL,
                qty INTEGER NOT NULL DEFAULT 1,
                return_rate REAL,
                reason TEXT,
                timestamp TEXT NOT NULL,
                api_success INTEGER NOT NULL DEFAULT 1
            )
            """
        )

    with patch("repositories.virtual_trade_repository.logger") as mock_logger:
        inserted = virutal_trade_repository.sync_live_strategy_positions()

    assert inserted == []
    assert virutal_trade_repository.get_holds_by_strategy("첫눌림목") == []
    mock_logger.warning.assert_called()


def test_trading_dates_and_strategy_history_empty_edges():
    """스냅샷 헬퍼의 빈/무효 데이터 경계를 확인한다."""
    assert _get_trading_dates({"2025-01-04": {"S1": 1.0}}) == []
    assert _build_strategy_return_history({"2025-01-01": {"S1": None}}, "S1") == []


def test_normalize_entry_date_edges(virutal_trade_repository):
    """전략 state entry_date는 날짜 포맷별로 표준화된다."""
    assert virutal_trade_repository._normalize_entry_date("20260424") == "2026-04-24 00:00:00"
    assert virutal_trade_repository._normalize_entry_date("2026-04-24") == "2026-04-24 00:00:00"
    assert virutal_trade_repository._normalize_entry_date("2026/04/24 09:30") == "2026/04/24 09:30"


def test_load_live_strategy_positions_skips_bad_files_and_invalid_positions(virutal_trade_repository, tmp_path):
    """전략 state 파일 로드 실패와 잘못된 position row는 복구 대상에서 제외한다."""
    data_root = tmp_path / "data"
    data_root.mkdir(exist_ok=True)
    (data_root / "pp_position_state.json").write_text("{bad json", encoding="utf-8")
    (data_root / "htf_position_state.json").write_text(
        json.dumps({
            "positions": {
                "005930": {"entry_price": 70000, "entry_date": "20260424"},
                "000660": "broken",
                "035420": {"entry_price": 0, "entry_date": "20260424"},
            }
        }),
        encoding="utf-8",
    )

    with patch.object(virutal_trade_repository, "_get_data_root_dir", return_value=str(data_root)), \
         patch("repositories.virtual_trade_repository.logger") as mock_logger:
        positions = virutal_trade_repository._load_live_strategy_state_positions()

    assert positions == [{
        "strategy": "하이타이트플래그",
        "code": "005930",
        "buy_price": 70000.0,
        "buy_date": "2026-04-24 00:00:00",
    }]
    mock_logger.warning.assert_called_once()


def test_scheduler_open_signal_map_empty_missing_and_error_paths(virutal_trade_repository, tmp_path):
    """scheduler signal_history 로딩의 조기 반환/DB 오류 경로를 확인한다."""
    data_root = tmp_path / "data"
    scheduler_dir = data_root / "StrategyScheduler"
    scheduler_dir.mkdir(parents=True)

    with patch.object(virutal_trade_repository, "_get_data_root_dir", return_value=str(data_root)):
        assert virutal_trade_repository._load_scheduler_open_signal_map(set()) == {}
        assert virutal_trade_repository._load_scheduler_open_signal_map({("S1", "005930")}) == {}

    (scheduler_dir / "scheduler.db").write_text("not sqlite", encoding="utf-8")
    with patch.object(virutal_trade_repository, "_get_data_root_dir", return_value=str(data_root)), \
         patch("repositories.virtual_trade_repository.logger") as mock_logger:
        assert virutal_trade_repository._load_scheduler_open_signal_map({("S1", "005930")}) == {}
    mock_logger.warning.assert_called_once()


def test_get_summary_without_reason_column_and_fix_sell_price_no_rows(virutal_trade_repository):
    """구버전 DataFrame처럼 reason 컬럼이 없어도 요약 계산이 가능하다."""
    legacy_df = pd.DataFrame([{
        "strategy": "S1",
        "code": "005930",
        "buy_date": "2025-01-01 09:00:00",
        "buy_price": 1000,
        "qty": 1,
        "sell_date": "2025-01-01 10:00:00",
        "sell_price": 1100,
        "return_rate": 10.0,
        "status": "SOLD",
    }])
    with patch.object(virutal_trade_repository, "_read", return_value=legacy_df):
        summary = virutal_trade_repository.get_summary(apply_cost=False)

    assert summary["force_closed_count"] == 0
    assert summary["avg_return"] == 10.0
    virutal_trade_repository.fix_sell_price("NOPE", None, 1234)


def test_find_prev_close_and_change_empty_edges(virutal_trade_repository):
    """가격 캐시/스냅샷 부족 시 변화율 계열은 None을 반환한다."""
    assert VirtualTradeRepository._find_prev_close({}, "005930", "2025-01-02") is None
    assert VirtualTradeRepository._find_prev_close({"005930": {"2025-01-01": 70000}}, "005930", "2025-01-02") == 70000
    assert virutal_trade_repository.get_daily_change("S1", 1.0, _data={"daily": {}}) == (None, None)
    assert virutal_trade_repository.get_weekly_change("S1", 1.0, _data={"daily": {}}) == (None, None)


def test_fetch_close_prices_uses_cache_and_handles_pykrx_error(virutal_trade_repository):
    """종가 캐시가 충분하면 조회를 건너뛰고, 조회 실패 종목은 스킵한다."""
    cached = {"005930": {"2025-01-01": 70000, "2025-01-02": 71000, "2025-01-03": 72000}}
    with patch.object(virutal_trade_repository, "_load_price_cache", return_value=cached), \
         patch("pykrx.stock.get_market_ohlcv_by_date") as mock_pykrx:
        result = virutal_trade_repository._fetch_close_prices(["005930"], "2025-01-01", "2025-01-03")
    assert result is cached
    mock_pykrx.assert_not_called()

    with patch.object(virutal_trade_repository, "_load_price_cache", return_value={}), \
         patch("pykrx.stock.get_market_ohlcv_by_date", side_effect=RuntimeError("boom")), \
         patch("repositories.virtual_trade_repository.logger") as mock_logger:
        assert virutal_trade_repository._fetch_close_prices(["000660"], "2025-01-01", "2025-01-03") == {}
    mock_logger.warning.assert_called_once()


# ── SellResult / log_sell_with_result 테스트 ─────────────────────────────────

def test_log_sell_with_result_returns_sell_result(virutal_trade_repository):
    """log_sell_with_result는 SellResult(return_rate, net_pnl_won, pnl_filled_qty)를 반환한다."""
    virutal_trade_repository.log_buy("TestStrategy", "005930", 70000, 1)
    result = virutal_trade_repository.log_sell_with_result("005930", 77000)

    assert isinstance(result, SellResult)
    assert result.return_rate == pytest.approx(10.0, abs=0.1)
    assert result.net_pnl_won is not None
    assert 0 < result.net_pnl_won < 7000  # 수수료/세금 차감 후 < gross 7000
    assert result.pnl_filled_qty == 1


def test_log_sell_with_result_reconciled_force_close_returns_none_pnl(virutal_trade_repository):
    """reconciled_force_close reason → net_pnl_won=None (통계 왜곡 방지)."""
    virutal_trade_repository.log_buy("TestStrategy", "005930", 70000)
    result = virutal_trade_repository.log_sell_with_result("005930", 0, reason="reconciled_force_close")

    assert result.net_pnl_won is None


async def test_log_sell_async_returns_none_contract(virutal_trade_repository):
    """기존 log_sell_async 반환값 contract 유지: None 반환."""
    virutal_trade_repository.log_buy("TestStrategy", "005930", 70000)
    ret = await virutal_trade_repository.log_sell_async("005930", 77000)
    assert ret is None


def test_log_sell_by_strategy_with_result_returns_sell_result(virutal_trade_repository):
    """log_sell_by_strategy_with_result는 SellResult를 반환한다."""
    virutal_trade_repository.log_buy("MomentumStrategy", "005930", 70000)
    result = virutal_trade_repository.log_sell_by_strategy_with_result(
        "MomentumStrategy", "005930", 77000
    )

    assert isinstance(result, SellResult)
    assert result.return_rate == pytest.approx(10.0, abs=0.1)
    assert result.net_pnl_won is not None
    assert result.pnl_filled_qty == 1


def test_log_sell_with_result_marks_intraday_trade(virutal_trade_repository):
    """매수일과 매도일이 같으면 SellResult.is_intraday_trade=True."""
    virutal_trade_repository.tm.get_current_kst_time.return_value = datetime(2025, 1, 1, 10, 0, 0)
    virutal_trade_repository.log_buy("TestStrategy", "005930", 70000, 1)

    virutal_trade_repository.tm.get_current_kst_time.return_value = datetime(2025, 1, 1, 14, 0, 0)
    result = virutal_trade_repository.log_sell_with_result("005930", 69000)

    assert result.is_intraday_trade is True


def test_log_sell_with_result_marks_overnight_trade(virutal_trade_repository):
    """매수일과 매도일이 다르면 SellResult.is_intraday_trade=False."""
    virutal_trade_repository.tm.get_current_kst_time.return_value = datetime(2025, 1, 1, 10, 0, 0)
    virutal_trade_repository.log_buy("TestStrategy", "005930", 70000, 1)

    virutal_trade_repository.tm.get_current_kst_time.return_value = datetime(2025, 1, 2, 9, 30, 0)
    result = virutal_trade_repository.log_sell_with_result("005930", 69000)

    assert result.is_intraday_trade is False


def test_log_sell_by_strategy_with_result_marks_overnight_trade(virutal_trade_repository):
    """전략 매도 결과도 전일 보유분 여부를 반환한다."""
    virutal_trade_repository.tm.get_current_kst_time.return_value = datetime(2025, 1, 1, 10, 0, 0)
    virutal_trade_repository.log_buy("MomentumStrategy", "005930", 70000, 1)

    virutal_trade_repository.tm.get_current_kst_time.return_value = datetime(2025, 1, 2, 9, 30, 0)
    result = virutal_trade_repository.log_sell_by_strategy_with_result(
        "MomentumStrategy", "005930", 69000
    )

    assert result.is_intraday_trade is False


async def test_log_sell_by_strategy_async_contract_unchanged(virutal_trade_repository):
    """기존 log_sell_by_strategy_async 반환값 contract 유지: float|None (return_rate %)."""
    virutal_trade_repository.log_buy("MomentumStrategy", "005930", 70000)
    ret = await virutal_trade_repository.log_sell_by_strategy_async(
        "MomentumStrategy", "005930", 77000
    )
    assert isinstance(ret, float)
    assert ret == pytest.approx(10.0, abs=0.1)
