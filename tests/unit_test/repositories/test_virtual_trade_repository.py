import pytest
import sys
import os
import pandas as pd
import json
import math
import concurrent.futures
import random
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock, AsyncMock
from repositories.virtual_trade_repository import VirtualTradeRepository
from services.virtual_trade_service import VirtualTradeService

@pytest.fixture
def temp_journal(tmp_path):
    """테스트용 임시 저널 파일 경로 생성"""
    journal_dir = tmp_path / "data" / "VirtualTradeRepository"
    journal_dir.mkdir(parents=True)
    journal_file = journal_dir / "trade_journal.csv"
    return str(journal_file)

@pytest.fixture
def mock_market_clock():
    """MarketClock Mock 생성"""
    tm = MagicMock()
    tm.get_current_kst_time.return_value = datetime(2025, 1, 1, 12, 0, 0)
    return tm

@pytest.fixture
def virutal_trade_repository(temp_journal, mock_market_clock):
    """VirtualTradeRepository 및 VirtualTradeService 래퍼 인스턴스 생성"""
    return VirtualTradeRepository(filename=temp_journal, market_clock=mock_market_clock)

def test_init_creates_directory_and_file(temp_journal):
    """초기화 시 디렉토리와 파일이 생성되는지 확인"""
    VirtualTradeRepository(filename=temp_journal)
    assert os.path.exists(os.path.dirname(temp_journal))
    assert os.path.exists(temp_journal)
    df = pd.read_csv(temp_journal)
    expected_cols = ["strategy", "code", "buy_date", "buy_price", "qty", "sell_date", "sell_price", "return_rate", "status"]
    assert list(df.columns) == expected_cols

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
    
    summary = virutal_trade_repository.get_summary()
    assert summary['total_trades'] == 3
    assert summary['win_rate'] == 50.0
    assert summary['avg_return'] == 5.0 # (20 - 10) / 2

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
    """이전 JSON 포맷 마이그레이션 확인"""
    journal_dir = tmp_path / "data" / "VirtualTradeRepository"
    journal_dir.mkdir(parents=True)
    snapshot_file = journal_dir / "portfolio_snapshots.json"
    old_data = {"2025-01-01": {"ALL": 1.0}}
    with open(snapshot_file, 'w', encoding='utf-8') as f:
        json.dump(old_data, f)
        
    # VirtualTradeRepository는 filename의 디렉토리를 기준으로 snapshot 파일을 찾음
    virutal_trade_repository = VirtualTradeRepository(filename=str(journal_dir / "trade_journal.csv"))
    data = virutal_trade_repository._load_data()
    
    assert "daily" in data
    assert "2025-01-01" in data["daily"]
    assert data["prev_values"] == {}

def test_get_strategy_return_history_logic(virutal_trade_repository):
    """전략별 수익률 히스토리 조회 및 정렬 로직 테스트"""
    # 1. 테스트용 스냅샷 데이터 저장
    test_snapshots = {
        "daily": {
            "2025-01-01": {"S1": 1.1, "S2": 0.5},
            "2025-01-03": {"S1": 2.2, "S2": 1.5},
            "2025-01-02": {"S1": 1.5}  # 정렬 확인을 위해 순서 섞음
        },
        "prev_values": {}
    }
    virutal_trade_repository._save_data(test_snapshots)

    # 2. S1 전략 히스토리 조회
    history = virutal_trade_repository.get_strategy_return_history("S1")

    # 3. 검증: 데이터 개수, 날짜순 정렬, 값 일치 여부
    assert len(history) == 3
    assert history[0] == {"date": "2025-01-01", "return_rate": 1.1}
    assert history[1] == {"date": "2025-01-02", "return_rate": 1.5}
    assert history[2] == {"date": "2025-01-03", "return_rate": 2.2}

    # 4. 존재하지 않는 전략 조회 시 빈 리스트 반환 확인
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
    # 중복 제거 및 알파벳 순 정렬 확인
    assert strategies == ["S1", "S2", "S3"]

def test_read_adds_qty_column_if_missing(temp_journal):
    """기존 파일에 qty 컬럼이 없을 경우 읽기 시 기본값 1로 추가되는지 확인"""
    # qty 없는 구버전 파일 생성
    old_cols = ["strategy", "code", "buy_date", "buy_price", "sell_date", "sell_price", "return_rate", "status"]
    df = pd.DataFrame(columns=old_cols)
    df.loc[0] = ["S1", "005930", "2025-01-01", 1000, None, None, 0.0, "HOLD"]
    df.to_csv(temp_journal, index=False)
    
    virtualTradeRepository = VirtualTradeRepository(filename=temp_journal)
    df_read = virtualTradeRepository._read()
    
    assert "qty" in df_read.columns
    assert df_read.iloc[0]['qty'] == 1

def test_log_buy_date_format(virutal_trade_repository):
    """매수 기록의 날짜 포맷이 YYYY-MM-DD HH:MM:SS 인지 확인 (호환성 유지)"""
    virutal_trade_repository.log_buy("TestStrategy", "005930", 70000)
    df = virutal_trade_repository._read()
    buy_date = df.iloc[0]['buy_date']
    # 예: 2025-01-01 12:00:00 -> 하이픈(-)이 포함되어야 함
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
        # 각 스레드가 서로 다른 종목을 매수한다고 가정
        virutal_trade_repository.log_buy("ConcurrentStrat", f"0000{idx}", 1000 * idx)

    thread_count = 10
    with concurrent.futures.ThreadPoolExecutor(max_workers=thread_count) as executor:
        futures = [executor.submit(worker, i) for i in range(thread_count)]
        for future in concurrent.futures.as_completed(futures):
            future.result() # 예외 발생 시 여기서 raise됨

    df = virutal_trade_repository._read()
    # 10개의 레코드가 있어야 함 (Lock이 없으면 race condition으로 일부가 덮어씌워져 누락될 수 있음)
    assert len(df) == 10
    # 중복 없이 모두 기록되었는지 확인
    codes = set(df['code'])
    assert len(codes) == 10

@pytest.mark.asyncio
async def test_log_buy_async_thread_execution(virutal_trade_repository):
    """log_buy_async가 asyncio.to_thread를 사용하여 log_buy를 실행하는지 테스트."""
    with patch("asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
        await virutal_trade_repository.log_buy_async("S1", "005930", 1000, 10)
        mock_to_thread.assert_awaited_once_with(virutal_trade_repository.log_buy, "S1", "005930", 1000, 10)

@pytest.mark.asyncio
async def test_log_sell_async_thread_execution(virutal_trade_repository):
    """log_sell_async가 asyncio.to_thread를 사용하여 log_sell을 실행하는지 테스트."""
    with patch("asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
        await virutal_trade_repository.log_sell_async("005930", 1200, 5)
        mock_to_thread.assert_awaited_once_with(virutal_trade_repository.log_sell, "005930", 1200, 5)

@pytest.mark.asyncio
async def test_log_sell_by_strategy_async_thread_execution(virutal_trade_repository):
    """log_sell_by_strategy_async가 asyncio.to_thread를 사용하여 실행하는지 테스트."""
    with patch("asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
        await virutal_trade_repository.log_sell_by_strategy_async("S1", "005930", 1200, 5)
        mock_to_thread.assert_awaited_once_with(virutal_trade_repository.log_sell_by_strategy, "S1", "005930", 1200, 5)

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
    """가격 캐시 저장 및 로드 확인"""
    cache_data = {"005930": {"2025-01-01": 70000}}
    virutal_trade_repository._save_price_cache(cache_data)
    
    loaded = virutal_trade_repository._load_price_cache()
    assert loaded == cache_data
    
    # 파일 경로 확인
    assert virutal_trade_repository._price_cache_path().endswith("close_price_cache.json")

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
    # 1. 거래 기록 생성
    # A: 1/1 매수 -> 1/3 매도
    # B: 1/2 매수 -> 보유
    virutal_trade_repository.tm.get_current_kst_time.return_value = datetime(2025, 1, 1)
    virutal_trade_repository.log_buy("S1", "A", 1000)
    
    virutal_trade_repository.tm.get_current_kst_time.return_value = datetime(2025, 1, 2)
    virutal_trade_repository.log_buy("S2", "B", 2000)
    
    virutal_trade_repository.tm.get_current_kst_time.return_value = datetime(2025, 1, 3)
    virutal_trade_repository.log_sell("A", 1100) # 10% 수익
    
    # 2. 가격 캐시 Mocking
    # A: 1/1(1000), 1/2(1050), 1/3(1100)
    # B: 1/2(2000), 1/3(1900)
    price_cache = {
        "A": {"2025-01-01": 1000, "2025-01-02": 1050, "2025-01-03": 1100},
        "B": {"2025-01-02": 2000, "2025-01-03": 1900}
    }
    
    # _fetch_close_prices를 Mock하여 위 캐시를 반환하도록 함
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

def test_load_data_corrupted(virutal_trade_repository, tmp_path):
    """손상된 스냅샷 파일 로드 시 빈 데이터 반환 확인"""
    snapshot_file = virutal_trade_repository._snapshot_path()
    with open(snapshot_file, 'w') as f:
        f.write("{invalid json")
        
    data = virutal_trade_repository._load_data()
    assert data == {"daily": {}, "prev_values": {}}

def test_backfill_snapshots_empty_df(virutal_trade_repository):
    """거래 내역이 없을 때 backfill 수행 안함"""
    with patch.object(virutal_trade_repository, '_fetch_close_prices') as mock_fetch:
        virutal_trade_repository.backfill_snapshots()
        mock_fetch.assert_not_called()

def test_load_price_cache_corrupted(virutal_trade_repository):
    """가격 캐시 파일이 손상되었을 때 빈 딕셔너리 반환 확인"""
    cache_path = virutal_trade_repository._price_cache_path()
    # Ensure dir exists
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    with open(cache_path, 'w') as f:
        f.write("{invalid json")
    
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
    if os.path.exists(virutal_trade_repository._price_cache_path()):
        os.remove(virutal_trade_repository._price_cache_path())
        
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
    # 1. 거래 기록 생성
    # A: 1/1 매수 (1000원) -> 1/3 매도 (1300원)
    # B: 1/1 매수 (1000원) -> 1/3 매도 (1300원)
    # 이렇게 하면 backfill 범위가 1/1 ~ 1/3이 됨
    virutal_trade_repository.tm.get_current_kst_time.return_value = datetime(2025, 1, 1)
    virutal_trade_repository.log_buy("S1", "A", 1000)
    virutal_trade_repository.log_buy("S2", "B", 1000)
    
    virutal_trade_repository.tm.get_current_kst_time.return_value = datetime(2025, 1, 3)
    virutal_trade_repository.log_sell("A", 1300)
    virutal_trade_repository.log_sell("B", 1300)
    
    # 2. 가격 캐시 Mocking
    # A: 1/1(1100), 1/2(데이터 없음 -> 1/1의 1100 사용 예상), 1/3(1200)
    # B: 데이터 아예 없음 (1/1, 1/2 모두 없음)
    price_cache = {
        "A": {"2025-01-01": 1100, "2025-01-03": 1200},
        # B는 키조차 없거나 비어있음
    }
    
    with patch.object(virutal_trade_repository, '_fetch_close_prices', return_value=price_cache):
        virutal_trade_repository.backfill_snapshots()
        
    data = virutal_trade_repository._load_data()
    daily = data["daily"]
    
    # 1/1: A(1100) -> 10%, B(없음) -> 0%
    assert daily["2025-01-01"]["S1"] == 10.0
    assert daily["2025-01-01"]["S2"] == 0.0
    
    # 1/2: A(데이터 없음 -> 직전 1/1의 1100) -> 10%, B(없음) -> 0%
    # 여기가 핵심 검증 포인트 (lines 318-324)
    assert daily["2025-01-02"]["S1"] == 10.0
    assert daily["2025-01-02"]["S2"] == 0.0
    
    # 1/3: 매도 완료된 상태 (확정 수익률 사용)
    # A: (1300-1000)/1000 = 30%
    # B: (1300-1000)/1000 = 30%
    assert daily["2025-01-03"]["S1"] == 30.0
    assert daily["2025-01-03"]["S2"] == 30.0
    

def test_get_holds_by_strategy_missing_qty_field(temp_journal):
    """get_holds_by_strategy 호출 시 파일에 qty 필드가 없어도 기본값 1로 반환되는지 확인"""
    # qty 없는 구버전 파일 생성
    old_cols = ["strategy", "code", "buy_date", "buy_price", "sell_date", "sell_price", "return_rate", "status"]
    df = pd.DataFrame(columns=old_cols)
    df.loc[0] = ["StrategyA", "005930", "2025-01-01", 1000, None, None, 0.0, "HOLD"]
    df.to_csv(temp_journal, index=False)

    virutal_trade_repository = VirtualTradeRepository(filename=temp_journal)
    holds = virutal_trade_repository.get_holds_by_strategy("StrategyA")

    assert len(holds) == 1
    assert holds[0]['code'] == "005930"
    assert holds[0]['qty'] == 1

def test_backfill_snapshots_asset_weighted_logic(virutal_trade_repository):
    """백필 시 자산 가중 평균으로 수익률이 계산되는지 확인"""
    # 1. 거래 기록 생성
    # 전략 A: 큰 금액(100만원), 작은 수익률(+10%)
    virutal_trade_repository.tm.get_current_kst_time.return_value = datetime(2025, 1, 1)
    virutal_trade_repository.log_buy("StrategyA", "A", 1000, qty=1000) # 매수금 1,000,000
    
    # 전략 B: 작은 금액(1만원), 큰 손실률(-50%)
    virutal_trade_repository.tm.get_current_kst_time.return_value = datetime(2025, 1, 1)
    virutal_trade_repository.log_buy("StrategyB", "B", 1000, qty=10)   # 매수금 10,000
    
    # 2. 가격 캐시 Mocking (1월 2일 기준 평가)
    # A: 1100원 (+10%) -> 평가금 1,100,000
    # B: 500원 (-50%) -> 평가금 5,000
    price_cache = {
        "A": {"2025-01-02": 1100},
        "B": {"2025-01-02": 500}
    }
    
    # 3. Backfill 수행 (현재 시간을 1/3로 설정하여 1/2일자 스냅샷 생성 유도)
    virutal_trade_repository.tm.get_current_kst_time.return_value = datetime(2025, 1, 3)
    
    with patch.object(virutal_trade_repository, '_fetch_close_prices', return_value=price_cache):
        virutal_trade_repository.backfill_snapshots()
        
    data = virutal_trade_repository._load_data()
    daily = data["daily"]
    
    # 1월 2일자 스냅샷 확인
    assert "2025-01-02" in daily
    snapshot = daily["2025-01-02"]
    
    # 자산 가중 평균 계산 검증
    # 총 매수: 1,010,000
    # 총 평가: 1,105,000
    # 수익률: (1,105,000 - 1,010,000) / 1,010,000 * 100 = 9.4059... -> 9.41%
    # 단순 평균이었다면: (10 - 50) / 2 = -20%
    
    assert snapshot["ALL"] == 9.41
    assert snapshot["StrategyA"] == 10.0
    assert snapshot["StrategyB"] == -50.0

def test_get_daily_change_returns_none_if_insufficient_data(virutal_trade_repository):
    """데이터가 부족할 때 전일 대비가 None을 반환하는지 확인"""
    virutal_trade_repository.tm.get_current_kst_time.return_value = datetime(2025, 1, 2)
    data = {
        "daily": {
            "2025-01-02": {"ALL": 1.0} # 데이터가 하루치만 있음
        },
        "prev_values": {}
    }
    
    change, ref_date = virutal_trade_repository.get_daily_change("ALL", 1.0, _data=data)
    assert change is None
    assert ref_date is None

def test_backfill_snapshots_performance(virutal_trade_repository):
    """backfill_snapshots 대량 데이터 성능 테스트"""
    import time
    
    # 1. 대량의 거래 기록 생성 (약 250건, 1년치 영업일)
    dates = pd.date_range(start="2024-01-01", end="2024-12-31", freq="B")
    trades = []
    codes = [f"A{i:04d}" for i in range(50)] # 50개 종목
    
    for i, date in enumerate(dates):
        # 매일 거래가 발생한다고 가정
        code = codes[i % 50]
        buy_date = date.strftime("%Y-%m-%d %H:%M:%S")
        status = "SOLD" if i % 2 == 0 else "HOLD" # 반반
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
            "status": status
        })
            
    df = pd.DataFrame(trades)
    virutal_trade_repository._write(df)
    
    # 2. 대량의 종가 데이터 Mocking
    price_cache = {}
    full_date_range = pd.date_range(start="2024-01-01", end="2024-12-31")
    for code in codes:
        price_cache[code] = {
            d.strftime("%Y-%m-%d"): random.randint(9000, 11000)
            for d in full_date_range
        }
        
    # 3. backfill 실행 및 시간 측정
    virutal_trade_repository.tm.get_current_kst_time.return_value = datetime(2025, 1, 1)
    
    start_time = time.time()
    with patch.object(virutal_trade_repository, '_fetch_close_prices', return_value=price_cache):
        virutal_trade_repository.backfill_snapshots()
    end_time = time.time()
    
    duration = end_time - start_time
    print(f"\n[Performance] backfill_snapshots processed {len(trades)} trades over {len(dates)} days in {duration:.4f} seconds")
    
    # 최적화된 로직이라면 1년치 데이터도 매우 빠르게 처리되어야 함 (보수적으로 2초 설정)
    assert duration < 2.0

def test_calculate_return_delegation(virutal_trade_repository):
    """calculate_return 메서드가 비용 적용 옵션에 따라 다르게 동작하는지 확인"""
    # 비용 미적용: 10%
    ror_no_cost = virutal_trade_repository.calculate_return(10000, 11000, qty=1, apply_cost=False)
    assert ror_no_cost == 10.0
    
    # 비용 적용: 10% 미만 (수수료/세금 차감)
    ror_cost = virutal_trade_repository.calculate_return(10000, 11000, qty=1, apply_cost=True)
    assert ror_cost < 10.0

def test_get_trade_amount(virutal_trade_repository):
    """get_trade_amount 메서드가 비용을 가감하여 계산하는지 확인"""
    price = 10000
    qty = 10
    base_amount = price * qty
    
    # 비용 미적용
    assert virutal_trade_repository.get_trade_amount(price, qty, is_sell=False, apply_cost=False) == base_amount
    assert virutal_trade_repository.get_trade_amount(price, qty, is_sell=True, apply_cost=False) == base_amount
    
    # 비용 적용 (매수: 금액 + 비용)
    buy_amt = virutal_trade_repository.get_trade_amount(price, qty, is_sell=False, apply_cost=True)
    assert buy_amt > base_amount
    
    # 비용 적용 (매도: 금액 - 비용)
    sell_amt = virutal_trade_repository.get_trade_amount(price, qty, is_sell=True, apply_cost=True)
    assert sell_amt < base_amount

def test_get_summary_with_cost(virutal_trade_repository):
    """get_summary 메서드에서 apply_cost 옵션이 통계에 반영되는지 확인"""
    # 데이터 준비: 1건의 매매 (10% 수익)
    virutal_trade_repository.log_buy("StrategyA", "005930", 10000, qty=10)
    virutal_trade_repository.log_sell("005930", 11000, qty=10)
    
    # 비용 미적용 요약
    summary_no_cost = virutal_trade_repository.get_summary(apply_cost=False)
    assert summary_no_cost['avg_return'] == 10.0
    
    # 비용 적용 요약
    summary_cost = virutal_trade_repository.get_summary(apply_cost=True)
    assert summary_cost['avg_return'] < 10.0
    
def test_get_all_trades_with_cost(virutal_trade_repository):
    """get_all_trades 메서드에서 apply_cost=True일 때 개별 거래 수익률이 재계산되는지 확인"""
    # 데이터 준비
    virutal_trade_repository.log_buy("StrategyA", "005930", 10000, qty=10)
    virutal_trade_repository.log_sell("005930", 11000, qty=10)
    
    # 비용 미적용 조회 (CSV에 저장된 값 그대로: 10.0)
    trades_no_cost = virutal_trade_repository.get_all_trades(apply_cost=False)
    assert len(trades_no_cost) == 1
    assert trades_no_cost[0]['return_rate'] == 10.0
    
    # 비용 적용 조회 (재계산된 값)
    trades_cost = virutal_trade_repository.get_all_trades(apply_cost=True)
    assert len(trades_cost) == 1
    assert trades_cost[0]['return_rate'] < 10.0