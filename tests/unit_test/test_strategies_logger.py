import pytest
import importlib
from unittest.mock import MagicMock, patch

# 테스트할 전략 클래스와 예상되는 로거 이름 매핑
STRATEGIES = [
    ("strategies.oneil_pocket_pivot_strategy", "OneilPocketPivotStrategy", "OneilPocketPivot"),
    ("strategies.oneil_squeeze_breakout_strategy", "OneilSqueezeBreakoutStrategy", "OneilSqueezeBreakout"),
    ("strategies.traditional_volume_breakout_strategy", "TraditionalVolumeBreakoutStrategy", "TraditionalVolumeBreakout"),
    ("strategies.program_buy_follow_strategy", "ProgramBuyFollowStrategy", "ProgramBuyFollow"),
    ("strategies.volume_breakout_live_strategy", "VolumeBreakoutLiveStrategy", "VolumeBreakoutLive"),
    ("strategies.GapUpPullback_strategy", "GapUpPullbackStrategy", "GapUpPullback"),
]

@pytest.mark.parametrize("module_path, class_name, expected_logger_name", STRATEGIES)
def test_strategies_init_logger(module_path, class_name, expected_logger_name):
    """
    모든 전략이 초기화 시 get_strategy_logger를 사용하여 
    올바른 이름의 전용 로거를 생성하는지 검증합니다.
    """
    # 1. 모듈 및 클래스 동적 로드
    module = importlib.import_module(module_path)
    StrategyClass = getattr(module, class_name)
    
    # 2. 의존성 Mock 생성
    ts = MagicMock()
    universe = MagicMock()
    tm = MagicMock()
    sqs = MagicMock()
    mapper = MagicMock()
    broker = MagicMock()
    
    # 3. 전략별 생성자 인자 준비
    kwargs = {}
    if class_name == "OneilSqueezeBreakoutStrategy":
        kwargs = {'trading_service': ts, 'universe_service': universe, 'time_manager': tm}
    elif class_name == "OneilPocketPivotStrategy":
        kwargs = {'stock_query_service': sqs, 'universe_service': universe, 'time_manager': tm}
    elif "Traditional" in class_name:
        kwargs = {'trading_service': ts, 'stock_query_service': sqs, 'stock_code_mapper': mapper, 'time_manager': tm}
    elif "Program" in class_name or "VolumeBreakoutLive" in class_name:
        kwargs = {'trading_service': ts, 'stock_query_service': sqs, 'time_manager': tm}
    elif "GapUp" in class_name:
        kwargs = {'broker': broker}
        
    # 4. get_strategy_logger 패치 및 검증
    with patch(f"{module_path}.get_strategy_logger") as mock_get_logger:
        # 전략 인스턴스화 (logger 인자를 주지 않음 -> 내부에서 get_strategy_logger 호출 유도)
        strategy = StrategyClass(**kwargs)
        
        # get_strategy_logger가 예상된 이름으로 호출되었는지 확인
        mock_get_logger.assert_called_once()
        args, _ = mock_get_logger.call_args
        assert args[0] == expected_logger_name
        
        # 전략 인스턴스에 로거가 할당되었는지 확인
        logger_attr = getattr(strategy, '_logger', getattr(strategy, 'logger', None))
        assert logger_attr == mock_get_logger.return_value