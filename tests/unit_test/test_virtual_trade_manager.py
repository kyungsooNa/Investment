import pytest
import sys
import os
import pandas as pd
import json
import math
import concurrent.futures
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock, AsyncMock
from managers.virtual_trade_manager import VirtualTradeManager

@pytest.fixture
def temp_journal(tmp_path):
    """테스트용 임시 저널 파일 경로 생성"""
    journal_dir = tmp_path / "data" / "VirtualTradeManager"
    journal_dir.mkdir(parents=True)
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
    journal_dir = tmp_path / "data" / "VirtualTradeManager"
    journal_dir.mkdir(parents=True)
    snapshot_file = journal_dir / "portfolio_snapshots.json"
    old_data = {"2025-01-01": {"ALL": 1.0}}
    with open(snapshot_file, 'w', encoding='utf-8') as f:
        json.dump(old_data, f)
        
    # VirtualTradeManager는 filename의 디렉토리를 기준으로 snapshot 파일을 찾음
    manager = VirtualTradeManager(filename=str(journal_dir / "trade_journal.csv"))
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

@pytest.mark.asyncio
async def test_log_buy_async(manager):
    """비동기 매수 기록 메서드 테스트"""
    await manager.log_buy_async("AsyncStrategy", "005930", 70000, qty=5)
    df = manager._read()
    assert len(df) == 1
    assert df.iloc[0]['strategy'] == "AsyncStrategy"
    assert df.iloc[0]['qty'] == 5

def test_log_buy_thread_safety(manager):
    """여러 스레드에서 동시에 log_buy 호출 시 데이터 무결성 테스트 (Lock 작동 확인)."""
    def worker(idx):
        # 각 스레드가 서로 다른 종목을 매수한다고 가정
        manager.log_buy("ConcurrentStrat", f"0000{idx}", 1000 * idx)

    thread_count = 10
    with concurrent.futures.ThreadPoolExecutor(max_workers=thread_count) as executor:
        futures = [executor.submit(worker, i) for i in range(thread_count)]
        for future in concurrent.futures.as_completed(futures):
            future.result() # 예외 발생 시 여기서 raise됨

    df = manager._read()
    # 10개의 레코드가 있어야 함 (Lock이 없으면 race condition으로 일부가 덮어씌워져 누락될 수 있음)
    assert len(df) == 10
    # 중복 없이 모두 기록되었는지 확인
    codes = set(df['code'])
    assert len(codes) == 10

@pytest.mark.asyncio
async def test_log_buy_async_thread_execution(manager):
    """log_buy_async가 asyncio.to_thread를 사용하여 log_buy를 실행하는지 테스트."""
    with patch("asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
        await manager.log_buy_async("S1", "005930", 1000, 10)
        mock_to_thread.assert_awaited_once_with(manager.log_buy, "S1", "005930", 1000, 10)

@pytest.mark.asyncio
async def test_log_sell_async_thread_execution(manager):
    """log_sell_async가 asyncio.to_thread를 사용하여 log_sell을 실행하는지 테스트."""
    with patch("asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
        await manager.log_sell_async("005930", 1200, 5)
        mock_to_thread.assert_awaited_once_with(manager.log_sell, "005930", 1200, 5)

@pytest.mark.asyncio
async def test_log_sell_by_strategy_async_thread_execution(manager):
    """log_sell_by_strategy_async가 asyncio.to_thread를 사용하여 실행하는지 테스트."""
    with patch("asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
        await manager.log_sell_by_strategy_async("S1", "005930", 1200, 5)
        mock_to_thread.assert_awaited_once_with(manager.log_sell_by_strategy, "S1", "005930", 1200, 5)

def test_log_sell_failure_no_hold(manager):
    """보유하지 않은 종목 매도 시도 시 처리 확인"""
    with patch("managers.virtual_trade_manager.logger") as mock_logger:
        manager.log_sell("999999", 10000)
        mock_logger.warning.assert_called()
        
    df = manager._read()
    assert df.empty

def test_log_sell_by_strategy_failure_no_hold(manager):
    """전략별 매도 시 보유하지 않은 경우 처리 확인"""
    with patch("managers.virtual_trade_manager.logger") as mock_logger:
        manager.log_sell_by_strategy("S1", "999999", 10000)
        mock_logger.warning.assert_called()

def test_get_solds_and_holds(manager):
    """get_solds와 get_holds 메서드 확인"""
    manager.log_buy("S1", "A", 1000)
    manager.log_buy("S1", "B", 2000)
    manager.log_sell("A", 1100)
    
    solds = manager.get_solds()
    holds = manager.get_holds()
    
    assert len(solds) == 1
    assert solds[0]['code'] == "A"
    assert len(holds) == 1
    assert holds[0]['code'] == "B"

def test_price_cache_operations(manager):
    """가격 캐시 저장 및 로드 확인"""
    cache_data = {"005930": {"2025-01-01": 70000}}
    manager._save_price_cache(cache_data)
    
    loaded = manager._load_price_cache()
    assert loaded == cache_data
    
    # 파일 경로 확인
    assert manager._price_cache_path().endswith("close_price_cache.json")

def test_find_prev_close(manager):
    """_find_prev_close 로직 확인"""
    cache = {
        "005930": {
            "2025-01-01": 70000,
            "2025-01-03": 72000
        }
    }
    # 1월 2일 데이터는 없음 -> 1월 1일 데이터 반환해야 함
    price = manager._find_prev_close(cache, "005930", "2025-01-02")
    assert price == 70000
    
    # 이전 데이터가 아예 없는 경우
    price = manager._find_prev_close(cache, "005930", "2024-12-31")
    assert price is None

def test_backfill_snapshots(manager):
    """과거 스냅샷 backfill 로직 확인"""
    # 1. 거래 기록 생성
    # A: 1/1 매수 -> 1/3 매도
    # B: 1/2 매수 -> 보유
    manager.tm.get_current_kst_time.return_value = datetime(2025, 1, 1)
    manager.log_buy("S1", "A", 1000)
    
    manager.tm.get_current_kst_time.return_value = datetime(2025, 1, 2)
    manager.log_buy("S2", "B", 2000)
    
    manager.tm.get_current_kst_time.return_value = datetime(2025, 1, 3)
    manager.log_sell("A", 1100) # 10% 수익
    
    # 2. 가격 캐시 Mocking
    # A: 1/1(1000), 1/2(1050), 1/3(1100)
    # B: 1/2(2000), 1/3(1900)
    price_cache = {
        "A": {"2025-01-01": 1000, "2025-01-02": 1050, "2025-01-03": 1100},
        "B": {"2025-01-02": 2000, "2025-01-03": 1900}
    }
    
    # _fetch_close_prices를 Mock하여 위 캐시를 반환하도록 함
    with patch.object(manager, '_fetch_close_prices', return_value=price_cache):
        manager.backfill_snapshots()
        
    data = manager._load_data()
    daily = data["daily"]
    
    # 1/1: A 보유 (매수가 1000, 종가 1000 -> 0%)
    assert daily["2025-01-01"]["S1"] == 0.0
    
    # 1/2: A 보유 (매수가 1000, 종가 1050 -> 5%), B 보유 (매수가 2000, 종가 2000 -> 0%)
    assert daily["2025-01-02"]["S1"] == 5.0
    assert daily["2025-01-02"]["S2"] == 0.0
    
    # 1/3: A 매도 (확정 10%), B 보유 (매수가 2000, 종가 1900 -> -5%)
    assert daily["2025-01-03"]["S1"] == 10.0
    assert daily["2025-01-03"]["S2"] == -5.0

def test_load_data_corrupted(manager, tmp_path):
    """손상된 스냅샷 파일 로드 시 빈 데이터 반환 확인"""
    snapshot_file = manager._snapshot_path()
    with open(snapshot_file, 'w') as f:
        f.write("{invalid json")
        
    data = manager._load_data()
    assert data == {"daily": {}, "prev_values": {}}

def test_backfill_snapshots_empty_df(manager):
    """거래 내역이 없을 때 backfill 수행 안함"""
    with patch.object(manager, '_fetch_close_prices') as mock_fetch:
        manager.backfill_snapshots()
        mock_fetch.assert_not_called()

def test_load_price_cache_corrupted(manager):
    """가격 캐시 파일이 손상되었을 때 빈 딕셔너리 반환 확인"""
    cache_path = manager._price_cache_path()
    # Ensure dir exists
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    with open(cache_path, 'w') as f:
        f.write("{invalid json")
    
    loaded = manager._load_price_cache()
    assert loaded == {}

def test_fetch_close_prices_cache_hit(manager):
    """캐시에 데이터가 이미 있으면 API 호출 스킵"""
    cache_data = {"005930": {"2025-01-01": 70000, "2025-01-02": 71000, "2025-01-03": 72000}}
    manager._save_price_cache(cache_data)
    
    mock_pykrx_stock = MagicMock()
    mock_pykrx = MagicMock()
    mock_pykrx.stock = mock_pykrx_stock
    
    with patch.dict("sys.modules", {"pykrx": mock_pykrx, "pykrx.stock": mock_pykrx_stock}):
        result = manager._fetch_close_prices(["005930"], "2025-01-01", "2025-01-03")
        assert result == cache_data
        mock_pykrx_stock.get_market_ohlcv_by_date.assert_not_called()

def test_fetch_close_prices_api_call(manager):
    """캐시가 없으면 API 호출 후 저장"""
    if os.path.exists(manager._price_cache_path()):
        os.remove(manager._price_cache_path())
        
    mock_pykrx_stock = MagicMock()
    mock_df = pd.DataFrame({'종가': [70000, 71000]}, index=pd.to_datetime(['2025-01-01', '2025-01-02']))
    mock_pykrx_stock.get_market_ohlcv_by_date.return_value = mock_df
    
    mock_pykrx = MagicMock()
    mock_pykrx.stock = mock_pykrx_stock
    
    with patch.dict("sys.modules", {"pykrx": mock_pykrx, "pykrx.stock": mock_pykrx_stock}):
        result = manager._fetch_close_prices(["005930"], "2025-01-01", "2025-01-02")
        
        assert "005930" in result
        assert result["005930"]["2025-01-01"] == 70000
        assert result["005930"]["2025-01-02"] == 71000
        mock_pykrx_stock.get_market_ohlcv_by_date.assert_called_once()
        
        loaded = manager._load_price_cache()
        assert loaded["005930"]["2025-01-01"] == 70000

def test_fetch_close_prices_api_empty(manager):
    """API 응답이 비어있을 때 처리"""
    mock_pykrx_stock = MagicMock()
    mock_pykrx_stock.get_market_ohlcv_by_date.return_value = pd.DataFrame()
    
    mock_pykrx = MagicMock()
    mock_pykrx.stock = mock_pykrx_stock
    
    with patch.dict("sys.modules", {"pykrx": mock_pykrx, "pykrx.stock": mock_pykrx_stock}):
        result = manager._fetch_close_prices(["005930"], "2025-01-01", "2025-01-01")
        assert "005930" not in result
        mock_pykrx_stock.get_market_ohlcv_by_date.assert_called_once()

def test_fetch_close_prices_api_exception(manager):
    """API 호출 중 예외 발생 시 처리"""
    mock_pykrx_stock = MagicMock()
    mock_pykrx_stock.get_market_ohlcv_by_date.side_effect = Exception("API Error")
    
    mock_pykrx = MagicMock()
    mock_pykrx.stock = mock_pykrx_stock
    
    with patch.dict("sys.modules", {"pykrx": mock_pykrx, "pykrx.stock": mock_pykrx_stock}):
        with patch("managers.virtual_trade_manager.logger") as mock_logger:
            result = manager._fetch_close_prices(["005930"], "2025-01-01", "2025-01-01")
            assert "005930" not in result
            mock_logger.warning.assert_called()

def test_backfill_snapshots_missing_price_logic(manager):
    """backfill_snapshots: 종가 데이터 누락 시 직전 종가 사용 및 데이터 아예 없을 때 0.0 처리 검증"""
    # 1. 거래 기록 생성
    # A: 1/1 매수 (1000원) -> 1/3 매도 (1300원)
    # B: 1/1 매수 (1000원) -> 1/3 매도 (1300원)
    # 이렇게 하면 backfill 범위가 1/1 ~ 1/3이 됨
    manager.tm.get_current_kst_time.return_value = datetime(2025, 1, 1)
    manager.log_buy("S1", "A", 1000)
    manager.log_buy("S2", "B", 1000)
    
    manager.tm.get_current_kst_time.return_value = datetime(2025, 1, 3)
    manager.log_sell("A", 1300)
    manager.log_sell("B", 1300)
    
    # 2. 가격 캐시 Mocking
    # A: 1/1(1100), 1/2(데이터 없음 -> 1/1의 1100 사용 예상), 1/3(1200)
    # B: 데이터 아예 없음 (1/1, 1/2 모두 없음)
    price_cache = {
        "A": {"2025-01-01": 1100, "2025-01-03": 1200},
        # B는 키조차 없거나 비어있음
    }
    
    with patch.object(manager, '_fetch_close_prices', return_value=price_cache):
        manager.backfill_snapshots()
        
    data = manager._load_data()
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

    manager = VirtualTradeManager(filename=temp_journal)
    holds = manager.get_holds_by_strategy("StrategyA")

    assert len(holds) == 1
    assert holds[0]['code'] == "005930"
    assert holds[0]['qty'] == 1