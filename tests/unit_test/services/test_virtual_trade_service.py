"""
VirtualTradeService 단위 테스트
"""
import pytest
import pandas as pd
from unittest.mock import MagicMock, AsyncMock
from datetime import datetime

from services.virtual_trade_service import VirtualTradeService

@pytest.fixture
def mock_repo():
    return MagicMock()

@pytest.fixture
def mock_clock():
    clock = MagicMock()
    # 기본적으로 2023-10-10 화요일로 모킹 (평일)
    clock.get_current_kst_time.return_value = datetime(2023, 10, 10, 12, 0, 0)
    return clock

@pytest.fixture
def virtual_trade_service(mock_repo, mock_clock):
    return VirtualTradeService(repository=mock_repo, market_clock=mock_clock)


def test_calculate_return(virtual_trade_service):
    """calculate_return 비용 적용 및 미적용 테스트"""
    res = virtual_trade_service.calculate_return(10000, 11000, qty=1, apply_cost=False)
    assert res == 10.0
    
    res_cost = virtual_trade_service.calculate_return(10000, 11000, qty=1, apply_cost=True)
    assert res_cost < 10.0


def test_get_trade_amount(virtual_trade_service):
    """get_trade_amount 매수/매도 및 비용 적용 테스트"""
    assert virtual_trade_service.get_trade_amount(10000, 2, is_sell=False, apply_cost=False) == 20000
    assert virtual_trade_service.get_trade_amount(10000, 2, is_sell=True, apply_cost=False) == 20000
    
    buy_amt = virtual_trade_service.get_trade_amount(10000, 2, is_sell=False, apply_cost=True)
    assert buy_amt > 20000
    sell_amt = virtual_trade_service.get_trade_amount(10000, 2, is_sell=True, apply_cost=True)
    assert sell_amt < 20000


def test_get_all_trades(virtual_trade_service, mock_repo):
    """get_all_trades 레코드 반환 및 비용 적용 수익률 계산 테스트"""
    df = pd.DataFrame([{"status": "SOLD", "buy_price": 1000, "sell_price": 1200, "qty": 1}])
    mock_repo._read.return_value = df
    mock_repo._to_json_records.return_value = [{"status": "SOLD", "buy_price": 1000, "sell_price": 1200, "qty": 1}]
    
    res = virtual_trade_service.get_all_trades(apply_cost=False)
    assert 'return_rate' not in res[0]
    
    res_cost = virtual_trade_service.get_all_trades(apply_cost=True)
    assert 'return_rate' in res_cost[0]
    assert res_cost[0]['return_rate'] < 20.0


def test_get_summary_empty(virtual_trade_service, mock_repo):
    """get_summary 거래 내역이 없을 때 빈 통계 반환 테스트"""
    mock_repo._read.return_value = pd.DataFrame(columns=['status'])
    res = virtual_trade_service.get_summary()
    assert res['total_trades'] == 0
    assert res['win_rate'] == 0
    assert res['avg_return'] == 0


def test_get_summary(virtual_trade_service, mock_repo):
    """get_summary 통계 계산 및 비용 적용 테스트"""
    df = pd.DataFrame([
        {'status': 'SOLD', 'buy_price': 1000, 'sell_price': 1200, 'qty': 1, 'return_rate': 20.0},
        {'status': 'HOLD', 'buy_price': 1000, 'sell_price': 0, 'qty': 1, 'return_rate': 0.0},
        {'status': 'SOLD', 'buy_price': 1000, 'sell_price': 800, 'qty': 1, 'return_rate': -20.0}
    ])
    mock_repo._read.return_value = df
    
    res = virtual_trade_service.get_summary(apply_cost=False)
    assert res['total_trades'] == 3
    assert res['win_rate'] == 50.0  # (1 승리 / 2 SOLD) * 100
    assert res['avg_return'] == 0.0  # (20 + (-20)) / 2

    res_cost = virtual_trade_service.get_summary(apply_cost=True)
    assert res_cost['avg_return'] < 0.0


def test_get_daily_change(virtual_trade_service, mock_repo, mock_clock):
    """get_daily_change 일일 수익률 변화 로직 테스트"""
    # 데이터 부족 시 None 반환
    mock_repo._load_data.return_value = {"daily": {}}
    change, ref_date = virtual_trade_service.get_daily_change("S1", 10.0)
    assert change is None and ref_date is None
    
    data = {
        "daily": {
            "2023-10-09": {"S1": 5.0, "ALL": 5.0},
            "2023-10-10": {"S1": 10.0, "ALL": 10.0}
        }
    }
    mock_clock.get_current_kst_time.return_value = datetime(2023, 10, 10, 12, 0)
    change, ref_date = virtual_trade_service.get_daily_change("S1", 10.0, _data=data)
    assert change == 5.0  # 10.0 - 5.0
    assert ref_date == "2023-10-09"


def test_get_weekly_change(virtual_trade_service, mock_repo, mock_clock):
    """get_weekly_change 주간 수익률 변화 로직 테스트"""
    data = {
        "daily": {
            "2023-10-02": {"S1": 1.0}, # 8일 전
            "2023-10-03": {"S1": 2.0}, # 7일 전 (기준일)
            "2023-10-09": {"S1": 5.0},
            "2023-10-10": {"S1": 10.0} # 오늘
        }
    }
    mock_clock.get_current_kst_time.return_value = datetime(2023, 10, 10, 12, 0)
    change, ref_date = virtual_trade_service.get_weekly_change("S1", 10.0, _data=data)
    assert change == 8.0 # 10.0 - 2.0
    assert ref_date == "2023-10-03"

    
def test_get_strategy_return_history(virtual_trade_service, mock_repo):
    """get_strategy_return_history 전략별 수익률 이력 조회 및 평일 필터링 확인"""
    data = {
        "daily": {
            "2023-10-06": {"S1": 1.0}, # 평일(금)
            "2023-10-09": {"S1": 2.0}, # 평일(월)
        }
    }
    mock_repo._load_data.return_value = data
    history = virtual_trade_service.get_strategy_return_history("S1")
    assert len(history) == 2
    assert history[0] == {"date": "2023-10-06", "return_rate": 1.0}
    assert history[1] == {"date": "2023-10-09", "return_rate": 2.0}


def test_get_all_strategies(virtual_trade_service, mock_repo):
    """get_all_strategies 최근 사용된 ALL을 제외한 전략 목록 반환 테스트"""
    data = {
        "daily": {
            "2023-10-09": {"S1": 5.0, "ALL": 5.0},
            "2023-10-10": {"S1": 10.0, "S2": 2.0, "ALL": 10.0}
        }
    }
    mock_repo._load_data.return_value = data
    strategies = virtual_trade_service.get_all_strategies()
    assert strategies == ["S1", "S2"]


@pytest.mark.asyncio
async def test_facade_delegation(virtual_trade_service, mock_repo):
    """Repository 위임(Facade) 메서드 정상 호출 테스트"""
    # Sync
    virtual_trade_service.log_buy("S1", "005930", 1000)
    mock_repo.log_buy.assert_called_with("S1", "005930", 1000)
    
    virtual_trade_service.log_sell("005930", 1200)
    mock_repo.log_sell.assert_called_with("005930", 1200)
    
    virtual_trade_service.get_holds()
    mock_repo.get_holds.assert_called_once()

    # Async
    mock_repo.log_buy_async = AsyncMock()
    await virtual_trade_service.log_buy_async("S1", "005930", 1000)
    mock_repo.log_buy_async.assert_awaited_with("S1", "005930", 1000)
    
    mock_repo.log_sell_async = AsyncMock()
    await virtual_trade_service.log_sell_async("005930", 1200)
    mock_repo.log_sell_async.assert_awaited_with("005930", 1200)