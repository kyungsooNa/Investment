import pytest
import os
import pandas as pd
import json
import math
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock
from managers.virtual_trade_manager import VirtualTradeManager

@pytest.fixture
def temp_journal(tmp_path):
    """테스트용 임시 저널 파일 경로 생성"""
    journal_dir = tmp_path / "data"
    journal_dir.mkdir()
    journal_file = journal_dir / "trade_journal.csv"
    return str(journal_file)

@pytest.fixture
def mock_time_manager():
    """TimeManager Mock 생성"""
    tm = MagicMock()
    tm.get_current_kst_time.return_value = datetime(2025, 1, 1, 12, 0, 0)
    return tm

@pytest.fixture
def manager(temp_journal, mock_time_manager):
    """VirtualTradeManager 인스턴스 생성"""
    return VirtualTradeManager(filename=temp_journal, time_manager=mock_time_manager)

def test_init_creates_directory_and_file(temp_journal):
    """초기화 시 디렉토리와 파일이 생성되는지 확인"""
    VirtualTradeManager(filename=temp_journal)
    assert os.path.exists(os.path.dirname(temp_journal))
    assert os.path.exists(temp_journal)
    df = pd.read_csv(temp_journal)
    expected_cols = ["strategy", "code", "buy_date", "buy_price", "qty", "sell_date", "sell_price", "return_rate", "status"]
    assert list(df.columns) == expected_cols

def test_log_buy_success(manager):
    """매수 기록이 정상적으로 저장되는지 확인"""
    manager.log_buy("TestStrategy", "005930", 70000)
    df = manager._read()
    assert len(df) == 1
    assert df.iloc[0]['strategy'] == "TestStrategy"
    assert df.iloc[0]['code'] == "005930"
    assert df.iloc[0]['status'] == "HOLD"
    assert df.iloc[0]['qty'] == 1
    assert manager.is_holding("TestStrategy", "005930") is True

def test_log_buy_duplicate_skips(manager):
    """동일 전략/종목 중복 매수 방지 로직 확인"""
    manager.log_buy("StrategyA", "005930", 70000)
    manager.log_buy("StrategyA", "005930", 71000)
    df = manager._read()
    assert len(df) == 1

def test_log_buy_with_qty(manager):
    """매수 시 수량(qty)이 정상적으로 기록되는지 확인"""
    manager.log_buy("TestStrategy", "005930", 70000, qty=10)
    df = manager._read()
    assert len(df) == 1
    assert df.iloc[0]['qty'] == 10

def test_log_sell_success(manager):
    """매도 기록 및 수익률 계산 확인"""
    manager.log_buy("StrategyA", "005930", 10000)
    manager.log_sell("005930", 11000)
    df = manager._read()
    assert df.iloc[0]['status'] == "SOLD"
    assert df.iloc[0]['return_rate'] == 10.0
    assert df.iloc[0]['sell_price'] == 11000

def test_log_sell_by_strategy(manager):
    """전략별 매도 기능 확인"""
    manager.log_buy("S1", "005930", 10000)
    manager.log_buy("S2", "005930", 10000)
    manager.log_sell_by_strategy("S1", "005930", 12000)
    df = manager._read()
    assert df[df['strategy'] == "S1"].iloc[0]['status'] == "SOLD"
    assert df[df['strategy'] == "S2"].iloc[0]['status'] == "HOLD"

def test_get_all_trades_json_compatibility(manager):
    """NaN 값이 None으로 변환되어 JSON 직렬화가 가능한지 확인"""
    manager.log_buy("S1", "005930", 70000)
    trades = manager.get_all_trades()
    assert trades[0]['sell_price'] is None
    assert trades[0]['sell_date'] is None

def test_get_summary_calculation(manager):
    """통계 요약 계산 로직 확인"""
    manager.log_buy("S1", "001", 1000)
    manager.log_sell("001", 1200) # +20%
    manager.log_buy("S1", "002", 1000)
    manager.log_sell("002", 900)  # -10%
    manager.log_buy("S1", "003", 1000) # HOLD (수익률 계산 제외)
    
    summary = manager.get_summary()
    assert summary['total_trades'] == 3
    assert summary['win_rate'] == 50.0
    assert summary['avg_return'] == 5.0 # (20 - 10) / 2

def test_fix_sell_price_logic(manager):
    """매도가 0인 기록 보정 로직 확인"""
    manager.log_buy("S1", "005930", 10000)
    df = manager._read()
    df.loc[0, 'status'] = 'SOLD'
    df.loc[0, 'sell_price'] = 0
    manager._write(df)
    
    manager.fix_sell_price("005930", None, 15000)
    df = manager._read()
    assert df.iloc[0]['sell_price'] == 15000
    assert df.iloc[0]['return_rate'] == 50.0

def test_save_daily_snapshot_and_prev_values(manager):
    """일일 스냅샷 저장 및 기준값(prev_values) 갱신 확인"""
    # 1일차
    manager.tm.get_current_kst_time.return_value = datetime(2025, 1, 1)
    manager.save_daily_snapshot({"ALL": 1.0})
    
    # 2일차 - 변동 발생 (0.01 이상)
    manager.tm.get_current_kst_time.return_value = datetime(2025, 1, 2)
    manager.save_daily_snapshot({"ALL": 2.5})
    
    data = manager._load_data()
    assert data['prev_values']['ALL'] == 1.0
    assert data['daily']['2025-01-02']['ALL'] == 2.5

def test_get_daily_change_calculation(manager):
    """전일 대비 변동폭 계산 확인"""
    data = {
        "daily": {"2025-01-01": {"S1": 1.0}},
        "prev_values": {"S1": 0.5}
    }
    change = manager.get_daily_change("S1", 1.5, _data=data)
    assert change == 1.0 # 1.5 - 0.5

def test_get_weekly_change_logic(manager):
    """7일 전 스냅샷 대비 변동폭 계산 확인"""
    manager.tm.get_current_kst_time.return_value = datetime(2025, 1, 10)
    data = {
        "daily": {
            "2025-01-01": {"S1": 10.0}, # 9일 전
            "2025-01-03": {"S1": 20.0}, # 7일 전 (기준점)
            "2025-01-09": {"S1": 30.0}  # 1일 전
        }
    }
    change = manager.get_weekly_change("S1", 25.0, _data=data)
    assert change == 5.0 # 25.0 - 20.0

def test_snapshot_migration_from_old_format(tmp_path):
    """이전 JSON 포맷 마이그레이션 확인"""
    snapshot_file = tmp_path / "portfolio_snapshots.json"
    old_data = {"2025-01-01": {"ALL": 1.0}}
    with open(snapshot_file, 'w', encoding='utf-8') as f:
        json.dump(old_data, f)
        
    # VirtualTradeManager는 filename의 디렉토리를 기준으로 snapshot 파일을 찾음
    manager = VirtualTradeManager(filename=str(tmp_path / "journal.csv"))
    data = manager._load_data()
    
    assert "daily" in data
    assert "2025-01-01" in data["daily"]
    assert data["prev_values"] == {}

def test_get_strategy_return_history_logic(manager):
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
    manager._save_data(test_snapshots)

    # 2. S1 전략 히스토리 조회
    history = manager.get_strategy_return_history("S1")

    # 3. 검증: 데이터 개수, 날짜순 정렬, 값 일치 여부
    assert len(history) == 3
    assert history[0] == {"date": "2025-01-01", "return_rate": 1.1}
    assert history[1] == {"date": "2025-01-02", "return_rate": 1.5}
    assert history[2] == {"date": "2025-01-03", "return_rate": 2.2}

    # 4. 존재하지 않는 전략 조회 시 빈 리스트 반환 확인
    assert manager.get_strategy_return_history("UNKNOWN") == []

def test_get_all_strategies_logic(manager):
    """모든 전략 이름 추출 로직 테스트"""
    test_snapshots = {
        "daily": {
            "2025-01-01": {"S1": 1.0, "S2": 2.0},
            "2025-01-02": {"S1": 1.5, "S3": 0.5}
        },
        "prev_values": {}
    }
    manager._save_data(test_snapshots)
    
    strategies = manager.get_all_strategies()
    # 중복 제거 및 알파벳 순 정렬 확인
    assert strategies == ["S1", "S2", "S3"]

def test_read_adds_qty_column_if_missing(temp_journal):
    """기존 파일에 qty 컬럼이 없을 경우 읽기 시 기본값 1로 추가되는지 확인"""
    # qty 없는 구버전 파일 생성
    old_cols = ["strategy", "code", "buy_date", "buy_price", "sell_date", "sell_price", "return_rate", "status"]
    df = pd.DataFrame(columns=old_cols)
    df.loc[0] = ["S1", "005930", "2025-01-01", 1000, None, None, 0.0, "HOLD"]
    df.to_csv(temp_journal, index=False)
    
    manager = VirtualTradeManager(filename=temp_journal)
    df_read = manager._read()
    
    assert "qty" in df_read.columns
    assert df_read.iloc[0]['qty'] == 1

def test_log_buy_date_format(manager):
    """매수 기록의 날짜 포맷이 YYYY-MM-DD HH:MM:SS 인지 확인 (호환성 유지)"""
    manager.log_buy("TestStrategy", "005930", 70000)
    df = manager._read()
    buy_date = df.iloc[0]['buy_date']
    # 예: 2025-01-01 12:00:00 -> 하이픈(-)이 포함되어야 함
    assert buy_date[4] == '-' and buy_date[7] == '-'