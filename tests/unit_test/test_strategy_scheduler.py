# tests/unit_test/test_strategy_scheduler.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from scheduler.strategy_scheduler import StrategyScheduler, StrategySchedulerConfig
from common.types import TradeSignal, ErrorCode, ResCommonResponse

@pytest.fixture
def mock_components():
    vm = MagicMock()
    oes = MagicMock()
    # OES 내부 trading_service 및 메서드들 AsyncMock 처리
    oes.trading_service = MagicMock()
    oes.trading_service.get_current_stock_price = AsyncMock()
    oes.handle_place_buy_order = AsyncMock()
    oes.handle_place_sell_order = AsyncMock()
    
    tm = MagicMock()
    # 현재 시간 Mock
    tm.get_current_kst_time.return_value = MagicMock(strftime=lambda fmt: "2023-01-01 12:00:00")
    
    return vm, oes, tm

@pytest.fixture
def scheduler(mock_components):
    vm, oes, tm = mock_components
    # dry_run=False로 설정하여 실제 API 호출 로직을 타게 함
    sched = StrategyScheduler(vm, oes, tm, dry_run=False)
    return sched

@pytest.mark.asyncio
async def test_force_liquidate_strategy_execution(scheduler, mock_components):
    """
    강제 청산(_force_liquidate_strategy) 실행 시:
    1. 보유 종목을 가져온다.
    2. 시장가(0)로 매도 주문을 낸다.
    3. 로그 기록 시에는 현재가를 조회하여 기록한다.
    """
    vm, oes, tm = mock_components
    
    # 전략 설정
    mock_strategy = MagicMock()
    mock_strategy.name = "TestStrategy"
    config = StrategySchedulerConfig(strategy=mock_strategy, force_exit_on_close=True, order_qty=5)
    
    # Mock: 보유 종목 1개 존재
    vm.get_holds_by_strategy.return_value = [
        {"code": "005930", "name": "Samsung", "buy_price": 50000}
    ]
    
    # Mock: 현재가 조회 결과 (60000원)
    oes.trading_service.get_current_stock_price.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value,
        msg1="OK",
        data={"output": {"stck_prpr": "60000"}}
    )
    
    # Mock: 매도 주문 성공
    oes.handle_place_sell_order.return_value = ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="OK")
    
    # 실행
    await scheduler._force_liquidate_strategy(config)
    
    # 검증 1: 현재가 조회 호출됨
    oes.trading_service.get_current_stock_price.assert_called_with("005930")
    
    # 검증 2: API 매도 주문은 가격 0(시장가)으로 호출됨
    oes.handle_place_sell_order.assert_called_once_with("005930", 0, 5)
    
    # 검증 3: VM 로그 기록은 조회된 현재가(60000)로 기록됨
    vm.log_sell_by_strategy.assert_called_once_with("TestStrategy", "005930", 60000)

@pytest.mark.asyncio
async def test_stop_strategy_triggers_liquidation(scheduler, mock_components):
    """
    stop_strategy 호출 시 force_exit_on_close=True이고 enabled=True이면 강제 청산이 수행된다.
    """
    vm, oes, tm = mock_components
    
    # 전략 등록
    mock_strategy = MagicMock()
    mock_strategy.name = "AutoCloseStrategy"
    config = StrategySchedulerConfig(strategy=mock_strategy, force_exit_on_close=True, enabled=True)
    scheduler.register(config)
    
    # _force_liquidate_strategy를 스파이하거나 모킹해서 호출 여부 확인
    # 여기서는 내부 메서드 호출을 확인하기 위해 scheduler 객체의 메서드를 patch
    with patch.object(scheduler, '_force_liquidate_strategy', new_callable=AsyncMock) as mock_liquidate:
        await scheduler.stop_strategy("AutoCloseStrategy")
        
        # 강제 청산 메서드가 호출되었는지 확인
        mock_liquidate.assert_called_once_with(config)
        # 전략이 비활성화되었는지 확인
        assert config.enabled is False

@pytest.mark.asyncio
async def test_stop_strategy_no_liquidation_if_disabled(scheduler, mock_components):
    """
    이미 비활성화된 전략은 stop_strategy 호출 시 강제 청산을 수행하지 않는다.
    """
    mock_strategy = MagicMock()
    mock_strategy.name = "DisabledStrategy"
    config = StrategySchedulerConfig(strategy=mock_strategy, force_exit_on_close=True, enabled=False)
    scheduler.register(config)
    
    with patch.object(scheduler, '_force_liquidate_strategy', new_callable=AsyncMock) as mock_liquidate:
        await scheduler.stop_strategy("DisabledStrategy")
        
        mock_liquidate.assert_not_called()
        assert config.enabled is False

@pytest.mark.asyncio
async def test_stop_strategy_no_liquidation_if_option_off(scheduler, mock_components):
    """
    force_exit_on_close=False인 전략은 stop_strategy 호출 시 강제 청산을 수행하지 않는다.
    """
    mock_strategy = MagicMock()
    mock_strategy.name = "LongTermStrategy"
    config = StrategySchedulerConfig(strategy=mock_strategy, force_exit_on_close=False, enabled=True)
    scheduler.register(config)
    
    with patch.object(scheduler, '_force_liquidate_strategy', new_callable=AsyncMock) as mock_liquidate:
        await scheduler.stop_strategy("LongTermStrategy")
        
        mock_liquidate.assert_not_called()
        assert config.enabled is False

@pytest.mark.asyncio
async def test_scheduler_stop_calls_stop_strategy(scheduler, mock_components):
    """
    scheduler.stop() 호출 시 등록된 모든 전략에 대해 stop_strategy가 호출된다.
    """
    # 전략 2개 등록
    s1 = MagicMock(); s1.name = "S1"
    c1 = StrategySchedulerConfig(strategy=s1)
    
    s2 = MagicMock(); s2.name = "S2"
    c2 = StrategySchedulerConfig(strategy=s2)
    
    scheduler.register(c1)
    scheduler.register(c2)
    
    # stop_strategy 모킹
    with patch.object(scheduler, 'stop_strategy', new_callable=AsyncMock) as mock_stop_strat:
        await scheduler.stop()
        
        assert mock_stop_strat.call_count == 2
        # 호출 인자 확인 (순서는 보장되지 않을 수 있으므로 any_call 사용)
        # stop(save_state=False) 이므로 perform_force_exit=True
        mock_stop_strat.assert_any_call("S1", perform_force_exit=True)
        mock_stop_strat.assert_any_call("S2", perform_force_exit=True)

@pytest.mark.asyncio
async def test_stop_with_save_state_skips_liquidation(scheduler, mock_components):
    """
    scheduler.stop(save_state=True) 호출 시(재시작 상황), 강제 청산 옵션은 꺼진 채로 stop_strategy가 호출되어야 한다.
    """
    mock_strategy = MagicMock()
    mock_strategy.name = "RestartStrategy"
    config = StrategySchedulerConfig(strategy=mock_strategy, force_exit_on_close=True, enabled=True)
    scheduler.register(config)

    # stop_strategy를 스파이
    with patch.object(scheduler, 'stop_strategy', new_callable=AsyncMock) as mock_stop_strat:
        # 상태 저장 모드로 정지
        await scheduler.stop(save_state=True)
        
        # perform_force_exit=False로 호출되었는지 확인
        mock_stop_strat.assert_called_once_with("RestartStrategy", perform_force_exit=False)

@pytest.mark.asyncio
async def test_execute_signal_market_price_logging(scheduler, mock_components):
    """
    _execute_signal 메서드에서 signal.price가 0일 때 현재가를 조회하여 로그에 남기는지 테스트.
    """
    vm, oes, tm = mock_components
    
    # 시그널 생성 (가격 0 = 시장가)
    signal = TradeSignal(
        strategy_name="TestStrat",
        code="000660",
        name="Hynix",
        action="SELL",
        price=0,
        qty=10,
        reason="Market Sell"
    )
    
    # 현재가 조회 Mock (80000원)
    oes.trading_service.get_current_stock_price.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value,
        msg1="Success",
        data={"output": {"stck_prpr": "80000"}}
    )
    oes.handle_place_sell_order.return_value = ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="Success")
    
    await scheduler._execute_signal(signal)
    
    # 1. 현재가 조회 수행 확인
    oes.trading_service.get_current_stock_price.assert_called_with("000660")
    
    # 2. 주문은 0원으로 나갔는지 확인
    oes.handle_place_sell_order.assert_called_once_with("000660", 0, 10)
    
    # 3. 로그는 80000원으로 기록되었는지 확인
    vm.log_sell_by_strategy.assert_called_once_with("TestStrat", "000660", 80000)